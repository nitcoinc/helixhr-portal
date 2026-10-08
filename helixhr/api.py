import json
import math
import os
import re
from urllib.parse import quote

import frappe
from frappe import _
from frappe.permissions import AUTOMATIC_ROLES
from frappe.utils import (
	add_days,
	add_to_date,
	cint,
	date_diff,
	flt,
	get_datetime,
	get_datetime_in_timezone,
	get_first_day,
	get_last_day,
	get_system_timezone,
	get_url,
	get_url_to_form,
	get_url_to_list,
	get_url_to_report,
	get_url_to_report_with_filters,
	getdate,
	now_datetime,
	time_diff_in_seconds,
)
from frappe.utils.print_format import download_pdf
from hrms.api import (
	get_attendance_calendar_events,
	get_current_employee,
	get_current_employee_info,
	get_leave_balance_map,
)
from hrms.hr.doctype.leave_application.leave_application import get_employee_leave_approver
from hrms.utils.holiday_list import get_holiday_list_for_employee

from helixhr.events import (
	APPROVER_FIELDS,
	DECISION_REASON_FIELD,
	HR_REPLY_SUBJECT_PREFIX,
	HR_REQUEST_DONE,
	HR_REQUEST_IN_PROGRESS,
	HR_REQUEST_OPEN,
	HR_REQUEST_REJECTED,
	HR_REQUEST_WAITING_ON_EMPLOYEE,
	LEAVE_RULES_DOCTYPE,
	LEAVE_STAGE_HR,
	PENDING_SINCE_FIELD,
	PENDING_STATE,
	REQUEST_APPROVED,
	REQUEST_DRAFT,
	REQUEST_PENDING_HR,
	REQUEST_PENDING_MANAGER,
	REQUEST_REJECTED,
	REQUEST_SENT_BACK,
	REQUEST_WITHDRAWABLE,
	TIMESHEET_PENDING_HR,
	TIMESHEET_SENT_BACK,
	_approver_user,
	_enabled_users_with_role,
	_is_hr,
	approver_drift,
	approver_problem,
	backdated_grace_days,
	backdated_leave_earliest,
	backdated_leave_reason,
	leave_overdraw,
	rederive_approvers,
)
from helixhr.helixhr.doctype.helixhr_timesheet_change.helixhr_timesheet_change import (
	comment_problem,
)

# The routed roles that are *not* already unscoped HR access (P5-U5's own
# permission scope), reused here rather than re-listed so this gate and
# `hr_request.get_permission_query_conditions` cannot drift apart.
from helixhr.helixhr.doctype.hr_request.hr_request import _WORKER_ROLES as _ROUTED_WORKER_ROLES
from helixhr.utils import (
	EMAIL_THEME,
	HOLIDAY_LIST_EDITABLE_FIELDS,
	LEAVE_TYPE_EDITABLE_FIELDS,
	MANAGED_PORTAL_ROLES,
	NOTIFICATION_EVENTS,
	PERSON_EDITABLE_FIELDS,
	PORTAL_ADMIN_ROLE,
	PROFILE_CORRECTABLE_FIELDS,
	PROFILE_CORRECTION_CATEGORY,
	PROFILE_EDITABLE_FIELDS,
	PROFILE_LABELS,
	PROFILE_MASKED_FIELDS,
	PROFILE_SECTION_FIELDS,
	PROFILE_SECTION_TABLES,
	PROFILE_USER_LINK_FIELDS,
	SHIFT_TYPE_EDITABLE_FIELDS,
	THEME_PLACEHOLDERS,
	UPLOAD_MAX_BYTES,
	TemplateRejected,
	admin_scope_employee_filters,
	as_administrator,
	can_admin_portal,
	email_theme,
	employee_in_admin_scope,
	event_variables,
	get_manager_user,
	get_week_bounds,
	has_custom_wording,
	is_photo_content,
	mask_identifier,
	message_brand,
	photo_file_filters,
	portal_home_page,
	prepare_profile_photo,
	project_in_scope,
	project_scope_filters,
	rate_limit_per_user,
	render_message,
	resolve_admin_scope,
	resolve_portal_admin_scope,
	resolve_project_scope,
	sample_context,
	send_notification,
	session_company,
	validate_email_theme,
	validate_logo_upload,
	validate_message_template,
	validate_portal_upload,
)


@frappe.whitelist()
def get_dashboard(**kwargs):
	"""One-screen summary for the logged-in employee (R6). The employee is
	always resolved from the session, never from an argument -- any extra
	query args (an `employee` a caller might try to pass) are accepted and
	ignored via **kwargs, they never change whose data comes back (KTD5).

	Each section is independent: a section-specific failure returns null for
	that section only, never an error for the whole page -- and names itself
	in `failed_sections` so the page can label the one region that broke
	instead of rendering its null as "nothing recorded yet" (P2-R2, P2-R25).

	One endpoint, one round trip (P2-R21). Sections that need the same read
	share it through `once` rather than asking Frappe the same question two
	and three times: the attendance calendar, this week's Timesheet row, the
	employee's open leave and the caller's pending decisions were each
	fetched by more than one section.
	"""
	rate_limit_per_user("get_dashboard")
	employee = get_current_employee()
	failed = []
	cache = {}

	def once(key, fn):
		if key not in cache:
			cache[key] = fn()
		return cache[key]

	# One error policy for the whole app: `_safe` owns it, and a section
	# only adds its name to `failed_sections`. The sentinel is what keeps a
	# section that legitimately answers `None` apart from one that threw.
	missing = object()

	def section(name, fn):
		value = _safe(fn, title=f"HelixHR dashboard section failed: {name}", default=missing)
		if value is missing:
			failed.append(name)
			return None
		return value

	return {
		"employee": section("employee", lambda: _get_employee_header(employee)),
		"leave_balances": section("leave_balances", get_leave_balance_map),
		"attendance_this_month": section(
			"attendance_this_month", lambda: _get_attendance_summary(once)
		),
		# The two sections the week-spine dashboard is built on. Kept inside
		# get_dashboard rather than split into their own endpoints so the home
		# screen stays one request.
		"week": section("week", lambda: _get_week_spine(employee, once)),
		"needs_you": section("needs_you", lambda: _get_needs_you(employee, once)),
		# P4-U9. The first few policy links, so the rail reaches them without a
		# trip to /documents. Bounded and disclosing the remainder the same way
		# the queue is: HR manages this catalogue in Desk, so it has no ceiling,
		# and the rail sits in a grid row whose height the other column shares.
		"documents": section("documents", lambda: _get_documents_card(employee)),
		# P4-R14: a projection, never the underlying dates (P4-KTD14).
		"celebrations": section(
			"celebrations", lambda: _get_celebrations(employee, getdate(user_today()))
		),
		"failed_sections": failed,
	}


@frappe.whitelist(methods=["POST"])
def update_my_profile(**fields):
	"""Save the caller's own contact fields on their Employee record (R9).

	`fields` is dropped to the allow-list before it ever reaches the
	document -- a caller passing `department` or any other key gets it
	silently ignored here, on top of (not instead of) the permlevel lock
	the U5 fixtures put on the field itself (KTD6: the fixture is the real
	lock; this allow-list stops the write attempt one step earlier so a
	rejected value never even reaches `validate()`). The employee is
	resolved from the session, never from an argument, so nobody can name
	another employee's record here (KTD5).
	"""
	rate_limit_per_user("update_my_profile")
	employee = get_current_employee()
	updates = {field: value for field, value in fields.items() if field in PROFILE_EDITABLE_FIELDS}

	doc = frappe.get_doc("Employee", employee)
	for field, value in updates.items():
		doc.set(field, value)
	doc.save()

	return {field: doc.get(field) for field in PROFILE_EDITABLE_FIELDS}


@frappe.whitelist()
def get_my_profile(**kwargs):
	"""Everything HR holds about the caller, by Profile tab (plan
	2026-09-29-001, U3) -- so an employee can check their own record without
	Desk, and ask HR to fix what is wrong.

	The employee is resolved from the session; any argument is ignored
	(KTD5). This reads HR-only (permlevel 2) bank and ID fields on the
	owner's behalf, deliberately: the named allow-list in
	`utils.PROFILE_SECTION_FIELDS` / `PROFILE_SECTION_TABLES` and server-side
	masking (`PROFILE_MASKED_FIELDS`) are the boundary, so a full account,
	PAN or passport number never leaves this function. Each tab is an
	independent `_safe` section named in `failed_sections` when it breaks.
	"""
	rate_limit_per_user("get_my_profile")
	info = get_current_employee_info() or {}
	employee = info.get("name")
	if not employee:
		# HRMS's own `get_current_employee()` means to raise this but hits
		# AttributeError (a 500) on its own None first.
		frappe.throw(_("Employee not found"), frappe.PermissionError)

	meta = frappe.get_meta("Employee")
	missing = object()
	failed = []

	def section(name):
		value = _safe(
			lambda: _profile_section(employee, name, meta),
			title=f"HelixHR profile section failed: {name}",
			default=missing,
		)
		if value is missing:
			failed.append(name)
			return None
		return value

	category_active = frappe.db.get_value("HelixHR Request Category", PROFILE_CORRECTION_CATEGORY, "is_active")
	return {
		"employee": employee,
		"employee_name": info.get("employee_name"),
		"photo_url": _employee_photo_url(employee),
		"sections": {name: section(name) for name in PROFILE_SECTION_FIELDS},
		"failed_sections": failed,
		"correction_category": PROFILE_CORRECTION_CATEGORY if cint(category_active) else None,
		# HR and administrators only (KTD6): every employee is a System
		# User, and the portal host 404s Desk for them.
		"desk_url": get_url_to_form("Employee", employee) if _portal_desk_url(frappe.session.user) else None,
	}


def _profile_section(employee, name, meta):
	"""One Profile tab: its present fields in allow-list order, plus its
	child tables. A field this site does not have is left out; one HR never
	filled comes back with a None value, for the page's "Not recorded"."""
	fieldnames = [field for field in PROFILE_SECTION_FIELDS[name] if meta.has_field(field)]
	values = frappe.db.get_value("Employee", employee, fieldnames, as_dict=True) if fieldnames else {}
	fields = [
		{
			"fieldname": field,
			"label": PROFILE_LABELS.get(field) or _sentence_case(_(meta.get_label(field))),
			"value": _profile_value(field, values.get(field)),
			"masked": field in PROFILE_MASKED_FIELDS,
			"editable": field in PROFILE_EDITABLE_FIELDS,
		}
		for field in fieldnames
	]
	tables = [
		_profile_table(employee, table, columns, meta)
		for table, columns in PROFILE_SECTION_TABLES.get(name, {}).items()
		if meta.has_field(table)
	]
	return {"fields": fields, "tables": tables}


def _sentence_case(label):
	"""The portal's sentence case for Frappe's title-case labels: "Date Of
	Retirement" -> "Date of retirement", acronyms ("IBAN", "PAN") kept."""
	words = (label or "").split()
	return " ".join(
		word if (index == 0 or (len(word) > 1 and word.isupper())) else word.lower()
		for index, word in enumerate(words)
	)


def _profile_value(field, value):
	"""The displayable form: masked identifiers, and people by name --
	`reports_to` holds an Employee id and the approver fields a login, and
	neither is something the page should print."""
	if field in PROFILE_MASKED_FIELDS:
		return mask_identifier(value)
	if not value:
		return value
	if field == "reports_to":
		return frappe.db.get_value("Employee", value, "employee_name")
	if field in PROFILE_USER_LINK_FIELDS:
		return _employee_for_user(value)[1] or frappe.db.get_value("User", value, "full_name")
	return value


def _profile_table(employee, table, columns, meta):
	child = frappe.get_meta(meta.get_field(table).options)
	present = [column for column in columns if child.has_field(column)]
	rows = frappe.get_all(
		child.name,
		filters={"parent": employee, "parenttype": "Employee", "parentfield": table},
		fields=present,
		order_by="idx asc",
	)
	return {
		"fieldname": table,
		"label": _sentence_case(_(meta.get_label(table))),
		"columns": [
			{"fieldname": column, "label": PROFILE_LABELS.get(column) or _sentence_case(_(child.get_label(column)))}
			for column in present
		],
		"rows": rows,
	}


# Profile photo (plan 2026-09-30-001, U2-U4, R1-R6)
#
# The photo is a private File attached to (Employee, <id>, "image"). Nobody
# but its owner can read that Employee under strict user permissions, so
# Frappe's own `/private/files` check refuses colleagues -- on purpose, and
# left that way. Colleagues get the bytes through `get_employee_photo`, which
# applies the Directory's rule itself (KTD1).

_PHOTO_FIELD = "image"
# `private`: never a shared cache. The URL carries a version token that
# changes on every replace, so a replaced photo is never stale; the short
# max-age bounds how long a *removed* one can still show from cache (R5).
_PHOTO_CACHE_CONTROL = "private, max-age=300"
_PHOTO_UNAVAILABLE = "That photo isn't available."


def _my_employee():
	"""The caller's Active Employee id, or the portal's standard refusal."""
	employee = (get_current_employee_info() or {}).get("name")
	if not employee:
		frappe.throw(_("Employee not found"), frappe.PermissionError)
	return employee


def _photo_file_names(employee):
	return frappe.get_all(
		"File",
		filters=photo_file_filters(attached_to_name=employee),
		pluck="name",
	)


def _set_photo_field(employee, file_url):
	"""KTD3: `db_set` of this one field, never `doc.save()`. A save runs
	ERPNext's `Employee.update_user`, which copies `image` into
	`User.user_image` and attaches a second File row to the User -- a read
	path to a private photo for anyone with User read (R6). Later full saves
	are covered by `events.employee_before_save`."""
	frappe.db.set_value("Employee", employee, _PHOTO_FIELD, file_url)


def _photo_url(employee, version):
	"""The URL an `<img>` loads: the serving method, never the file path."""
	return (
		"/api/method/helixhr.api.get_employee_photo"
		f"?employee={quote(employee)}&v={quote(str(version or '')[:12])}"
	)


def _photo_urls(employees):
	"""`{employee: photo_url}` for each of `employees` that has a photo, in
	one query however long the list is (U4). A File counts only while it is
	still the one `Employee.image` names."""
	ids = list({employee for employee in employees if employee})
	if not ids:
		return {}
	file = frappe.qb.DocType("File")
	person = frappe.qb.DocType("Employee")
	rows = (
		frappe.qb.from_(file)
		.join(person)
		.on((person.name == file.attached_to_name) & (person.image == file.file_url))
		.select(file.attached_to_name, file.content_hash, file.modified)
		.where(
			(file.attached_to_doctype == "Employee")
			& (file.attached_to_field == _PHOTO_FIELD)
			& (file.attached_to_name.isin(ids))
		)
		.run(as_dict=True)
	)
	return {
		row.attached_to_name: _photo_url(row.attached_to_name, row.content_hash or str(row.modified))
		for row in rows
	}


def _with_photo_urls(rows, key="employee"):
	"""Add `photo_url` beside `initials` on every row dict, batched."""
	urls = _photo_urls(row.get(key) for row in rows)
	for row in rows:
		row["photo_url"] = urls.get(row.get(key))
	return rows


def _employee_photo_url(employee):
	return _photo_urls([employee]).get(employee) if employee else None


@frappe.whitelist(methods=["POST"])
def upload_my_photo(**kwargs):
	"""Set or replace the caller's own photo (R1, R2).

	The employee comes from the session; any argument is ignored, so there
	is no way to name someone else's record (KTD5). The upload is validated
	and re-encoded (`utils.prepare_profile_photo`) before anything is
	stored, then written private, and the previous photo File is deleted so
	exactly one remains.
	"""
	rate_limit_per_user("upload_my_photo")
	employee = _my_employee()

	upload = (getattr(frappe.request, "files", None) or {}).get("file")
	if upload is None:
		frappe.throw(_("No file came through. Pick the file again."))
	content, extension, _content_type = prepare_profile_photo(
		os.path.basename(upload.filename or "").strip(), upload.stream.read()
	)

	previous = _photo_file_names(employee)
	doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"{employee}-photo{extension}",
			"content": content,
			"attached_to_doctype": "Employee",
			"attached_to_name": employee,
			"attached_to_field": _PHOTO_FIELD,
			"is_private": 1,
		}
	)
	doc.insert(ignore_permissions=True)
	for name in previous:
		# File.on_trash keeps the bytes on disk while another row shares them.
		frappe.delete_doc("File", name, ignore_permissions=True, force=True)
	_set_photo_field(employee, doc.file_url)
	return {"photo_url": _photo_url(employee, doc.content_hash)}


@frappe.whitelist(methods=["POST"])
def remove_my_photo(**kwargs):
	"""Remove the caller's own photo. Removing when there is none is a
	no-op, not an error."""
	rate_limit_per_user("remove_my_photo")
	employee = _my_employee()
	for name in _photo_file_names(employee):
		frappe.delete_doc("File", name, ignore_permissions=True, force=True)
	if frappe.db.get_value("Employee", employee, _PHOTO_FIELD):
		_set_photo_field(employee, None)
	return {"photo_url": None}


def _may_see_photo(employee):
	"""R3: the owner; HR within admin scope (any status); an Active
	colleague in the caller's own company (the Directory rule)."""
	target = frappe.db.get_value("Employee", employee, ["company", "status", "user_id"], as_dict=True)
	if not target:
		return False
	user = frappe.session.user
	if target.user_id and target.user_id == user:
		return True
	if employee_in_admin_scope(employee, resolve_admin_scope(user)):
		return True
	if target.status != "Active":
		return False
	company = session_company(user)
	return bool(company) and company == target.company


@frappe.whitelist(methods=["GET"])
def get_employee_photo(employee, v=None):
	"""Stream one employee's photo inline to a viewer allowed to see it
	(KTD1, KTD2, R3, R5).

	Not rate-limited on purpose: a Directory page loads one of these per
	avatar. `v` is only a cache-busting token and is not read. The File is
	found by its attachment and must still be the one `Employee.image`
	names -- never by a path the client sends. A refusal and "no photo" are
	the same not-found answer with no bytes, so this cannot be used to learn
	who exists. A photo set in Desk before the portal handled photos may be
	public and was never re-encoded; it is served only when its bytes really
	are PNG or JPEG, so an SVG or HTML file can never render inline here.
	"""
	if not isinstance(employee, str) or not employee or not _may_see_photo(employee):
		frappe.throw(_(_PHOTO_UNAVAILABLE), frappe.DoesNotExistError)

	image = frappe.db.get_value("Employee", employee, _PHOTO_FIELD)
	name = image and frappe.db.get_value(
		"File",
		photo_file_filters(attached_to_name=employee, file_url=image),
		"name",
	)
	content = None
	if name:
		try:
			# `encodings=[]`: raw bytes, never a latin-1 decoded string.
			content = frappe.get_doc("File", name).get_content(encodings=[])
		except (OSError, frappe.DoesNotExistError):
			content = None
	if not isinstance(content, bytes) or not is_photo_content(content):
		frappe.throw(_(_PHOTO_UNAVAILABLE), frappe.DoesNotExistError)

	is_png = content.startswith(b"\x89PNG")
	frappe.response.type = "download"
	frappe.response.display_content_as = "inline"
	frappe.response.content_type = "image/png" if is_png else "image/jpeg"
	frappe.response.filename = f"{employee}-photo{'.png' if is_png else '.jpg'}"
	frappe.response.filecontent = content
	frappe.local.response_headers["Cache-Control"] = _PHOTO_CACHE_CONTROL
	frappe.local.response_headers["X-Content-Type-Options"] = "nosniff"


# Portal bootstrap and the user's own calendar (P2-U2, P2-R5, P2-R20, P2-R21)


def get_user_time_zone(user=None):
	"""The IANA timezone the portal treats as authoritative for this user.

	P2-R5: "today" and the Monday..Sunday week are the *user's* calendar,
	not the browser's -- a laptop with a wrong clock zone, or an employee
	travelling, must not move their week. Frappe already stores a per-user
	`User.time_zone`; the site's System Settings timezone is the documented
	fallback when a user has none, which is the normal case.
	"""
	user = user or frappe.session.user
	return frappe.db.get_value("User", user, "time_zone") or get_system_timezone()


def user_today(user=None):
	"""Today's date, as a `YYYY-MM-DD` calendar value, in the user's own
	timezone. This is the server-side half of P2-AE3: every date this API
	derives from "now" has to agree with what the portal renders."""
	return get_datetime_in_timezone(get_user_time_zone(user)).strftime("%Y-%m-%d")


@frappe.whitelist()
def get_portal_bootstrap():
	"""Everything the shell needs before it can render anything, in one
	request (P2-R20, P2-R21, KTD7).

	Replaces the per-navigation `hrms.api.get_current_employee_info` call
	plus the shell's separate `frappe.client.get_count` for direct reports:
	the router guard fetched identity again on every route change, which is
	both a wasted round trip per navigation and a second place for the two
	answers to disagree.

	**This is not an authorization decision.** `can_approve` only decides
	whether a nav item is drawn; every domain method still resolves the
	session user and is still refused by Frappe permissions on its own
	(see docs/architecture.md, "Security model"). A caller who lies to
	themselves about this response gains nothing.
	"""
	employee = get_current_employee_info() or None
	time_zone = get_user_time_zone()
	today = user_today()
	monday, sunday = get_week_bounds(today)

	boot = {
		"user": frappe.session.user,
		"employee": employee,
		# The calendar contract. `system_time_zone` is the frame Frappe's
		# naive timestamps are wall-clock readings in; without it the
		# browser cannot convert one into the user's zone at all.
		"time_zone": time_zone,
		"system_time_zone": get_system_timezone(),
		"today": today,
		"week_start": str(monday),
		"week_end": str(sunday),
		"can_approve": False,
		# P3-KTD11: Team is gated on direct reports, not on `can_approve` --
		# a leave approver with no reports would otherwise open an empty
		# Team page. Same rule as `can_approve`: a nav decision, not a grant.
		"has_reports": False,
		# P5-U11: a routed-role holder (IT Team today) has no reports and is
		# never `_is_hr()`, so `can_approve` alone would hide the Approvals nav
		# item until their queue happened to have something in it -- the same
		# gap P4-R13 already closed for HR Manager. Read unconditionally on
		# role, the same way HR's `can_approve` does not wait for a pending row.
		"can_work_requests": _holds_routed_role(),
		# Plan 2026-10-04-002 U4 / KTD6: the Requests page's "To work on" tab
		# needs a wider gate than the Approvals nav item -- HR Manager also
		# holds a routed role. A bootstrap boolean, never a role name.
		"can_handle_requests": _is_hr() or _holds_routed_role(),
		# P5-U14: the same predicate `get_portal_config` itself enforces, so
		# the nav item and the server's own gate can never disagree. Read
		# unconditionally on role, like `can_work_requests` above -- HR
		# Manager need not be anybody's employee for Settings to make sense.
		"can_configure": _is_hr(frappe.session.user),
		# P5-U15: the same predicate `get_organisation_view` itself enforces,
		# so the nav item and the server's own gate can never disagree --
		# same shape as `can_configure` just above.
		"can_see_organisation": _is_hr(frappe.session.user),
		# The Email templates page (theme, message templates, celebrations &
		# holidays): Portal Admin and System Manager only -- the predicate
		# every endpoint behind the page enforces. A boolean, never a role list.
		"can_edit_email_templates": _can_edit_email_templates(frappe.session.user),
		# P6-U4: same shape again -- `search_people` and `get_person` are
		# gated by `resolve_admin_scope`, which grants a scope to exactly
		# the roles `_is_hr` names, so the nav item and the server's gate
		# agree by construction.
		"can_see_people": resolve_admin_scope(frappe.session.user)["kind"] != "none",
		# P7-U5: same shape as `can_see_people` just above -- `search_projects`
		# and `get_project` are gated by `resolve_project_scope`, which grants a
		# scope to exactly the callers this flag names, so the nav item and the
		# server's gate agree by construction.
		"can_see_projects": resolve_project_scope(frappe.session.user)["kind"] != "none",
		# Plan 2026-10-04-001 U4: the Reports nav item. True when
		# `get_report_catalog` would list at least one entry -- the same
		# `resolve_report_access` answer `run_report` enforces.
		"can_run_reports": _can_run_reports(frappe.session.user),
		# Portal Admin and System Manager: the access matrix, the export log
		# and the portal-role section -- `can_admin_portal` is the predicate
		# each of those endpoints enforces. HR Manager is refused since plan
		# 2026-10-06-001 U3 (R5/R6).
		"can_admin_portal": can_admin_portal(frappe.session.user),
		# Documents: the upload/edit/delete controls -- `save_document_link`'s
		# own gate, asked without a company.
		"can_manage_documents": _can_manage_documents(frappe.session.user),
		# P6-KTD4: resolved on the caller's own ability to reach Desk (a
		# System User holding a `desk_access` role), never on "is HR" --
		# the two are correlated today but the flag must not assume they
		# stay that way.
		"can_open_desk": _can_open_desk(frappe.session.user),
		# The shell's "Open Desk" button, and -- when there is no Employee
		# below -- what lets an HR or System Manager use the portal's admin
		# pages instead of being told their account is not set up. A nav
		# decision like every flag above; each admin method gates itself.
		"desk_url": _portal_desk_url(frappe.session.user),
		"unread_notifications": 0,
	}

	if not employee or not employee.get("name"):
		# A signed-in user with no active Employee record. Everything below
		# is scoped to an Employee, so there is nothing more to say -- and
		# saying it plainly is what lets the browser tell this apart from a
		# service failure (P2-U2 scenario 3).
		return boot

	# The shell's own avatar (plan 2026-09-30-001 U4). One indexed read.
	employee["photo_url"] = _employee_photo_url(employee["name"])

	# A leave approver need not be anybody's manager, and a manager's only
	# pending work may be a timesheet -- gating the Approvals nav item on
	# direct reports alone hid the entry from both (P2-R11). The count is
	# still tried first because it is one indexed count and short-circuits
	# the two list reads for the common case. P3-KTD11 also surfaces it as
	# `has_reports`, which is what the Team nav item reads.
	title = "HelixHR portal bootstrap failed"
	report_count = _safe(lambda: _count_direct_reports(employee["name"]), title=title) or 0
	boot["has_reports"] = report_count > 0
	# P4-R13: the Approvals nav item is present for every HR Manager, empty
	# queue or not -- HR's work arrives without warning and a rail item that
	# comes and goes is a rail item nobody trusts. Still a nav decision, not
	# a grant: `_assert_may_act_on` re-checks every read and every action.
	boot["can_approve"] = (
		report_count > 0
		or _is_hr(frappe.session.user)
		or bool(_safe(lambda: _pending_approvals(employee["name"]), title=title))
	)
	boot["unread_notifications"] = _safe(_get_unread_notification_count, title=title) or 0
	return boot


@frappe.whitelist(allow_guest=True)
def login_via_office365(code: str, state: str):
	"""Frappe's Microsoft Entra ID callback, then the portal's landing rule.

	Registered over `frappe.integrations.oauth2_logins.login_via_office365`
	in hooks.py, so the Azure redirect URI does not change. The token
	exchange, user match and session are all Frappe's own; only the final
	`Location` is corrected.

	Frappe's password login asks `get_home_page()` -- and therefore
	`portal_home_page` -- where to land. Its OAuth path does not: with no
	`redirect-to` it sends a System User to `get_default_path()` (the User's
	Default App, `/desk/people` for HRMS, or `/apps`), and the portal host
	404s both (docs/deployment.md).

	The `redirect-to` this login attempt carried is read *before* Frappe
	consumes it (the single-use `state` cache entry), so a link somebody
	followed into the login -- a Desk record from an email, a portal deep
	link -- is still honoured. Only Frappe's own default is replaced.
	"""
	from frappe.integrations.oauth2_logins import login_via_office365 as frappe_login_via_office365
	from frappe.utils.oauth import OAUTH_LOGIN_FLOW_CACHE_PREFIX

	requested = frappe.cache.get_value(f"{OAUTH_LOGIN_FLOW_CACHE_PREFIX}:{state}") if state else None
	frappe_login_via_office365(code, state)
	_redirect_to_portal_home(requested)


def _redirect_to_portal_home(requested):
	"""Replace Frappe's default post-login landing with the portal, unless
	the login was asked to go somewhere (`requested`). Error pages --
	Frappe's refused-login responses are web pages, not redirects -- are
	left alone."""
	response = frappe.local.response
	if response.get("type") != "redirect" or frappe.session.user == "Guest" or requested:
		return
	home = portal_home_page(frappe.session.user)
	if home:
		response["location"] = get_url(f"/{home}")


def _direct_report_filters(manager):
	"""The one definition of "direct reports": Active Employees whose
	`reports_to` is `manager`. Home, Team week and Roster all read it, so
	the screens cannot drift apart."""
	return {"reports_to": manager, "status": "Active"}


def _count_direct_reports(employee):
	return frappe.db.count("Employee", _direct_report_filters(employee))


def _safe(fn, title="HelixHR portal section failed", default=None):
	"""Run `fn`, and turn a failure into `default` plus one named log line.

	`title` is the caller's own label: a bootstrap failure used to log
	itself as "HelixHR dashboard section failed", so the one place the
	shell can break was the hardest one to find in the error log.
	"""
	try:
		return fn()
	except Exception:
		frappe.log_error(title=title)
		return default


def _get_employee_header(employee):
	fields = ["name", "employee_name", "designation", "department", "branch", "reports_to"]
	data = frappe.db.get_value("Employee", employee, fields, as_dict=True)
	# Employee has no dedicated "location" field; branch (India/USA offices)
	# is what this company actually uses for that, so the dashboard's
	# "location" is Employee.branch under a plainer label (design system
	# copy rule: no Frappe words).
	data["manager_name"] = (
		frappe.db.get_value("Employee", data.reports_to, "employee_name") if data.reports_to else None
	)
	return data


def _get_attendance_summary(once):
	"""This month's attendance, counted by status.

	Reads the shared calendar (`_attendance_events`) rather than calling
	HRMS again: the spine needs the same data for a different range, and
	this section used to make its own second call for the overlap.
	"""
	start, end = str(get_first_day(user_today())), str(get_last_day(user_today()))
	summary = {}
	for date, status in once("attendance_events", _attendance_events).items():
		if start <= date <= end:
			summary[status] = summary.get(status, 0) + 1
	return summary


def _attendance_events():
	"""One calendar read covering both ranges that need it: the month the
	summary counts, and the Monday..Sunday week the spine draws. They
	overlap but are not the same range, which is why this is their union
	rather than either one of them.

	str(), not the date objects get_first_day/get_last_day return:
	hrms.api.get_attendance_calendar_events is annotated `from_date: str`
	and Frappe's typing validation raises FrappeTypeError on a date. The
	section wrapper swallowed it, so this card returned null and rendered
	"Nothing recorded yet" for every employee regardless of their real
	attendance.
	"""
	monday, sunday = get_week_bounds(user_today())
	start = min(get_first_day(user_today()), monday)
	end = max(get_last_day(user_today()), sunday)
	return get_attendance_calendar_events(str(start), str(end)) or {}


# Celebrations (P4-U5 / P4-R14, P4-KTD14)
#
# `Employee.date_of_birth` and `date_of_joining` sit at permlevel 1 by
# property setter -- role Employee cannot read either one, on purpose. So
# this is a *projection*, the same posture `get_directory` takes: the read
# runs server-side, scoped to the caller's own company, bounded by that
# company's active headcount, and what comes back is only what the card
# prints. The year of birth never leaves the server, and neither does an
# age: a day and a month are what a colleague needs to say happy birthday,
# and the rest is nobody's business (P4-R14).
#
# Eligibility is HRMS's own rule, so Home and the reminder email can never
# disagree about who is celebrating: Active, the day and month match, and
# the *year* is strictly before this year (`get_employees_having_an_event_today`
# filters `Extract(year, ...) < today.year`). That one condition covers both
# "no year recorded" (a null date is skipped outright) and "joined or was
# born this year" -- a person's first year is not an anniversary, and a
# newborn is not a colleague.
_CELEBRATION_FIELDS = ("name", "employee_name", "date_of_birth", "date_of_joining")


def _celebration_projection(row, event_date, today):
	"""One card row: who, which day, and whether that day is today.

	Deliberately no `date`, no `year`, no `age`. `day` and `month` are
	integers the client formats in the reader's own locale (`formatDayMonth`
	in lib/dates.js), which also means there is no full date on the wire for
	a caller to reconstruct a birth year from.
	"""
	return {
		"employee": row.name,
		"employee_name": row.employee_name,
		# The monogram the directory and the Approvals queue already draw,
		# from the server so all three agree (P3-U9).
		"initials": _initials(row.employee_name),
		"day": event_date.day,
		"month": event_date.month,
		"is_today": event_date.day == today.day,
	}


def _get_celebrations(employee, today):
	"""This month's birthdays and work anniversaries in the caller's own
	company, today's first and then by day.

	An employee whose record carries no company gets empty lists rather
	than an error, exactly as `get_directory` does -- "we cannot tell which
	company you are in" is a thing for HR to fix, and the card simply does
	not appear.
	"""
	company = frappe.db.get_value("Employee", employee, "company")
	if not company:
		return {"birthdays": [], "anniversaries": []}

	rows = frappe.get_all(
		"Employee",
		filters={"status": "Active", "company": company},
		fields=list(_CELEBRATION_FIELDS),
		order_by="employee_name asc",
		ignore_permissions=True,
	)

	birthdays, anniversaries = [], []
	for row in rows:
		born = getdate(row.date_of_birth) if row.date_of_birth else None
		if born and born.month == today.month and born.year < today.year:
			birthdays.append(_celebration_projection(row, born, today))

		joined = getdate(row.date_of_joining) if row.date_of_joining else None
		if joined and joined.month == today.month and joined.year < today.year:
			anniversary = _celebration_projection(row, joined, today)
			# The one number the card does print: years completed, which is
			# a fact about the job and not about the person.
			anniversary["years"] = today.year - joined.year
			anniversaries.append(anniversary)

	_with_photo_urls(birthdays + anniversaries)
	return {
		"birthdays": _ordered_celebrations(birthdays),
		"anniversaries": _ordered_celebrations(anniversaries),
	}


def _ordered_celebrations(entries):
	"""Today first, then up the month. Someone reading the card today wants
	today's names at the top; the rest of the month is a reminder."""
	return sorted(entries, key=lambda entry: (not entry["is_today"], entry["day"]))


def _open_leave(employee):
	"""This employee's leave that is still with their manager -- the rows
	the queue's "Waiting on others" list is drawn from.

	Bounded by _QUEUE_FETCH like every other queue source.
	"""
	return frappe.get_all(
		"Leave Application",
		filters={"employee": employee, "status": "Open"},
		fields=["name", "leave_type", "from_date", "to_date"],
		order_by="from_date asc",
		limit=_QUEUE_FETCH,
	)


def _get_week_spine(employee, once):
	"""Monday..Sunday for the current week, one entry per day: the
	attendance status Frappe recorded, the hours booked on that day's
	timesheet rows, and whether approved leave covers it.

	Same Monday-anchored week as the Timesheet screen (get_week_bounds,
	KTD10), so "Thu" means the same day on both screens.
	"""
	from frappe.utils import add_days, getdate

	monday, sunday = get_week_bounds(user_today())
	attendance = once("attendance_events", _attendance_events)
	timesheet = once("week_timesheet", lambda: _week_timesheet(employee, monday))
	hours = _hours_by_day(timesheet)
	leave_days = _leave_days(employee, monday, sunday)
	current = getdate(user_today())

	days = []
	for offset in range(7):
		date = add_days(monday, offset)
		iso = str(date)
		days.append(
			{
				"date": iso,
				"weekday": date.strftime("%a"),
				"day_of_month": date.day,
				"is_today": date == current,
				"is_future": date > current,
				"attendance": attendance.get(iso),
				"hours": hours.get(iso, 0),
				"on_leave": iso in leave_days,
			}
		)

	return {
		"week_start": str(monday),
		"week_end": str(sunday),
		"days": days,
		"total_hours": sum(hours.values()),
		"timesheet_state": timesheet.get("workflow_state") if timesheet else None,
	}


def _week_timesheet(employee, monday, fields=("name", "workflow_state"), sunday=None):
	"""The one Timesheet a week's hours, status and edits all come from --
	the newest non-cancelled one whose `start_date` falls inside the week
	(KTD10). Every query site goes through here so the rule lives once.

	The week is a **range**, never `start_date == monday`. ERPNext's own
	`Timesheet.set_dates` rewrites `start_date` to the earliest `from_time`
	in the child table, so a week booked Tuesday-Friday -- leave, a
	holiday, or simply starting mid-week -- persists with the Tuesday.
	Matched by equality it then read back as an empty week, and the next
	save hit ERPNext's OverlapError against the row nobody could see.
	"""
	sunday = sunday or get_week_bounds(monday)[1]
	return frappe.db.get_value(
		"Timesheet",
		{
			"employee": employee,
			"start_date": ["between", [str(monday), str(sunday)]],
			"docstatus": ["!=", 2],
		},
		list(fields),
		as_dict=True,
		order_by="creation desc",
	)


def _hours_by_day(timesheet):
	"""Booked hours per day from this week's Timesheet. Timesheet Detail
	carries `from_time`, not a date column, so the day is derived the same
	way get_my_week does it (_row_date)."""
	if not timesheet:
		return {}

	totals = {}
	for row in frappe.get_all(
		"Timesheet Detail", filters={"parent": timesheet["name"]}, fields=["from_time", "hours"]
	):
		if not row.from_time:
			continue
		day = str(get_datetime(row.from_time).date())
		totals[day] = flt(totals.get(day, 0)) + flt(row.hours)
	return totals


def _leave_days(employee, monday, sunday):
	"""Set of ISO dates inside the week covered by an approved leave. Leave
	Applications store a range, so each one is expanded across the days it
	overlaps with this week.

	`docstatus` 1, not status alone (P2-R10): an application whose status
	says Approved but which was never submitted consumed no balance and
	created no ledger entry, so it is a legacy defect row for HR to
	resolve, not a day off. preflight.check_unsubmitted_approved_leave
	counts them and patches/v1_0/report_unsubmitted_approved_leave lists
	them."""
	from frappe.utils import add_days, getdate

	covered = set()
	applications = frappe.get_all(
		"Leave Application",
		filters={
			"employee": employee,
			"status": "Approved",
			"docstatus": 1,
			"from_date": ["<=", str(sunday)],
			"to_date": [">=", str(monday)],
		},
		fields=["from_date", "to_date"],
	)
	for leave in applications:
		date = max(getdate(leave.from_date), monday)
		last = min(getdate(leave.to_date), sunday)
		while date <= last:
			covered.add(str(date))
			date = add_days(date, 1)
	return covered


# The action queue (P2-U4, P2-R11, P2-R12).
#
# Order is the screen's whole argument, so it lives here on the server rather
# than in the component. Urgency leads -- blocked work (something sent back)
# outranks an HR reply nobody has read, which outranks a decision this person
# owes as an approver -- and inside a tier the oldest comes first. That last
# part is the direction's named risk: a three-week-old rejection is more
# overdue than this week's, so it must not sort underneath it.
#
# Two lists come back, not one. "Needs you" is work this person can actually
# move; leave that is only waiting on a manager is *theirs* but not *for*
# them, so it goes to a quieter "Waiting on others" section instead of
# padding the queue with rows whose only honest action is "wait" (P2-R8).
#
# Every item carries a stable record identity (`id`, the Vue list key), the
# record it is about, an exact route destination, its urgency and whose move
# it is. `day` ties a row to the spine above; `age_days` is what the row
# shows when it falls outside the week, so an old item reads as old.
def _get_needs_you(employee, once):
	monday, sunday = get_week_bounds(user_today())
	current = _as_date(user_today())
	items = []
	waiting = []

	def day_for(date):
		return str(date) if date and monday <= _as_date(date) <= sunday else None

	def age_days(date):
		"""How overdue this is, in days. None for rows with no date."""
		return (current - _as_date(date)).days if date else None

	rejected = frappe.get_all(
		"Timesheet",
		filters={"employee": employee, "workflow_state": TIMESHEET_SENT_BACK, "docstatus": ["!=", 2]},
		fields=["name", "start_date", "end_date"],
		order_by="start_date asc",
		limit=_QUEUE_FETCH,
	)
	reasons = _rejection_comments("Timesheet", [row.name for row in rejected], employee)
	for row in rejected:
		items.append(
			_queue_item(
				kind="timesheet_rejected",
				name=row.name,
				title="Your timesheet was sent back",
				detail=reasons.get(row.name),
				date=str(row.start_date),
				day=day_for(row.start_date),
				age_days=age_days(row.start_date),
				action="Edit and resubmit",
				owner="you",
				urgency="blocked",
				# The exact week, by its Monday -- not "/timesheet", which
				# opened the *current* week whichever week was sent back
				# (P2-AE5). Normalised through get_week_bounds because
				# ERPNext rewrites `start_date` to the earliest booked day,
				# so a week that starts on the Tuesday would otherwise put
				# a Tuesday in the route parameter (KTD10).
				to={
					"name": "TimesheetWeek",
					"params": {"weekStart": str(get_week_bounds(row.start_date)[0])},
				},
			)
		)

	# Leave the manager sent back, with the reason quoted on the row (P2-R14,
	# P2-U5 scenario 1). A rejection stays at docstatus 0 by design (P2-U1),
	# so this is blocked work the employee can actually move: edit the dates
	# and resend, or withdraw it.
	rejected_leave = frappe.get_all(
		"Leave Application",
		filters={"employee": employee, "status": "Rejected", "docstatus": 0},
		fields=["name", "leave_type", "from_date", "owner"],
		order_by="from_date asc",
		limit=_QUEUE_FETCH,
	)
	leave_reasons = _leave_reason(
		[row.name for row in rejected_leave],
		{row.name: row.owner for row in rejected_leave},
	)
	for row in rejected_leave:
		items.append(
			_queue_item(
				kind="leave_rejected",
				name=row.name,
				title=f"Your {row.leave_type} was sent back",
				detail=leave_reasons.get(row.name),
				date=str(row.from_date),
				day=day_for(row.from_date),
				age_days=age_days(row.from_date),
				action="Edit and resend",
				owner="you",
				urgency="blocked",
				to={"name": "LeaveDetail", "params": {"name": row.name}},
			)
		)

	# A sent-back attendance request, with the reason quoted on the row
	# (P3-R17, P3-AE10). It stays at docstatus 0, and the Edit transition
	# takes it back to Draft, so this is blocked work the employee can move.
	rejected_requests = frappe.get_all(
		"Attendance Request",
		filters={"employee": employee, "workflow_state": REQUEST_SENT_BACK, "docstatus": 0},
		fields=["name", "from_date", "to_date", "reason"],
		order_by="from_date asc",
		limit=_QUEUE_FETCH,
	)
	request_reasons = _rejection_comments(
		"Attendance Request", [row.name for row in rejected_requests], employee
	)
	for row in rejected_requests:
		items.append(
			_queue_item(
				kind="attendance_request_rejected",
				name=row.name,
				title="Your attendance request was sent back",
				detail=request_reasons.get(row.name),
				date=str(row.from_date),
				day=day_for(row.from_date),
				age_days=age_days(row.from_date),
				action="Edit and resend",
				owner="you",
				urgency="blocked",
				to={"name": "AttendanceRequestDetail", "params": {"name": row.name}},
			)
		)

	# P4-R4. A terminal rejection is nobody's move. The employee has to *read*
	# it -- the reason is the whole content of the row -- but there is nothing
	# to edit, resend or chase, so it goes to the quieter "Waiting on others"
	# list rather than into a queue of work. Once each: the two collectors
	# above are scoped to the send-back states, so a rejected record reaches
	# exactly one of these lists and never both.
	final_leave = frappe.get_all(
		"Leave Application",
		filters={"employee": employee, "status": "Rejected", "docstatus": 1},
		fields=["name", "leave_type", "from_date", "owner"],
		order_by="from_date desc",
		limit=_QUEUE_FETCH,
	)
	final_leave_reasons = _leave_reason(
		[row.name for row in final_leave],
		{row.name: row.owner for row in final_leave},
	)
	for row in final_leave:
		waiting.append(
			_queue_item(
				kind="leave_final_rejected",
				name=row.name,
				title=f"Your {row.leave_type} was rejected",
				detail=final_leave_reasons.get(row.name),
				date=str(row.from_date),
				day=day_for(row.from_date),
				age_days=age_days(row.from_date),
				action="View",
				owner="nobody",
				urgency="waiting",
				to={"name": "LeaveDetail", "params": {"name": row.name}},
			)
		)

	final_requests = frappe.get_all(
		"Attendance Request",
		filters={"employee": employee, "workflow_state": REQUEST_REJECTED, "docstatus": 0},
		fields=["name", "from_date", "reason"],
		order_by="from_date desc",
		limit=_QUEUE_FETCH,
	)
	final_request_reasons = _rejection_comments(
		"Attendance Request", [row.name for row in final_requests], employee
	)
	for row in final_requests:
		waiting.append(
			_queue_item(
				kind="attendance_request_final_rejected",
				name=row.name,
				title=f"Your {row.reason} request was rejected",
				detail=final_request_reasons.get(row.name),
				date=str(row.from_date),
				day=day_for(row.from_date),
				age_days=age_days(row.from_date),
				action="View",
				owner="nobody",
				urgency="waiting",
				to={"name": "AttendanceRequestDetail", "params": {"name": row.name}},
			)
		)

	# An HR reply is an obligation for exactly as long as its notification is
	# unread (KTD6). Deriving it from Notification Log rather than from the
	# request's own status is what lets reading it clear the queue without a
	# second seen-state model -- and what makes a *revised* reply a new
	# obligation without reopening the one already read.
	for log in frappe.get_all(
		"Notification Log",
		filters={
			"for_user": frappe.session.user,
			"read": 0,
			"document_type": "HR Request",
			"subject": ["like", f"{HR_REPLY_SUBJECT_PREFIX}%"],
		},
		fields=["name", "document_name", "subject", "description", "creation"],
		order_by="creation asc",
		limit=_QUEUE_FETCH,
	):
		items.append(
			_queue_item(
				kind="request_answered",
				name=log.document_name,
				title=log.subject,
				detail=_notification_text(log.description),
				date=str(log.creation),
				day=None,
				age_days=age_days(log.creation),
				action="Read",
				owner="you",
				urgency="unread",
				to={"name": "RequestDetail", "params": {"name": log.document_name}},
				notification=log.name,
			)
		)

	# One row per decision, not a count: "3 requests waiting" is a link to a
	# list, and P2-R12 asks for the exact decision.
	for decision in once("approvals", lambda: _pending_approvals(employee)):
		items.append(
			_queue_item(
				kind=decision["kind"],
				name=decision["reference_name"],
				title=decision["title"],
				detail=None,
				date=decision["date"],
				day=day_for(decision["date"]),
				age_days=age_days(decision["date"]),
				action="Review",
				owner="you",
				urgency="decision",
				to={
					"name": "ApprovalDetail",
					"params": {"kind": decision["route_kind"], "name": decision["reference_name"]},
				},
			)
		)

	for row in once("open_leave", lambda: _open_leave(employee)):
		waiting.append(
			_queue_item(
				kind="leave_waiting",
				name=row.name,
				title=f"{row.leave_type} waiting for your manager",
				detail=None,
				date=str(row.from_date),
				day=day_for(row.from_date),
				age_days=age_days(row.from_date),
				action="View",
				owner="manager",
				urgency="waiting",
				to={"name": "LeaveDetail", "params": {"name": row.name}},
			)
		)

	# Both pending steps, with the step's owner named -- the employee's own
	# request is waiting on somebody else either way (P3-R17).
	for row in frappe.get_all(
		"Attendance Request",
		filters={
			"employee": employee,
			"workflow_state": ["in", [REQUEST_PENDING_MANAGER, REQUEST_PENDING_HR]],
			"docstatus": 0,
		},
		fields=["name", "from_date", "to_date", "reason", "workflow_state"],
		order_by="from_date asc",
		limit=_QUEUE_FETCH,
	):
		with_hr = row.workflow_state == REQUEST_PENDING_HR
		waiting.append(
			_queue_item(
				kind="attendance_request_waiting",
				name=row.name,
				title=f"{row.reason} request waiting for {'HR' if with_hr else 'your manager'}",
				detail=None,
				date=str(row.from_date),
				day=day_for(row.from_date),
				age_days=age_days(row.from_date),
				action="View",
				owner="hr" if with_hr else "manager",
				urgency="waiting",
				to={"name": "AttendanceRequestDetail", "params": {"name": row.name}},
			)
		)

	items.sort(key=lambda item: (_URGENCY_RANK[item["urgency"]], -(item["age_days"] or 0)))
	# P4-R4, second order key: "nobody" rows are terminal receipts -- there is
	# nothing to chase -- so they sort *below* everything still live, and only
	# then by age. A recency window was the alternative and is worse: it would
	# drop a rejection the employee never read, and the reason is the whole
	# content of the row.
	waiting.sort(key=lambda item: (item["owner"] == "nobody", -(item["age_days"] or 0)))
	shown = items[:_QUEUE_LIMIT]
	return {
		"items": shown,
		"more": max(0, len(items) - len(shown)),
		"waiting": waiting[:_QUEUE_LIMIT],
		# The "View all (N)" count for the waiting list, as `more` is for the queue.
		"waiting_more": max(0, len(waiting) - _QUEUE_LIMIT),
	}


def _queue_item(
	*, kind, name, title, detail, date, day, age_days, action, owner, urgency, to, notification=None
):
	return {
		# Stable record identity, and the Vue list key. Never an index: a
		# re-ordered queue reused the wrong row's DOM state under one.
		"id": f"{kind}:{notification or name}",
		"kind": kind,
		"notification": notification,
		"title": title,
		"detail": detail,
		"date": date,
		"day": day,
		"age_days": age_days,
		"action": action,
		# Whose move it is. "you" rows are the queue; "manager", "hr" and
		# "nobody" rows are the waiting list -- "nobody" is a decision that
		# has already been made and only needs reading (P4-R4).
		"owner": owner,
		"urgency": urgency,
		"tone": _URGENCY_TONE[urgency],
		"to": to,
	}


# Shown on the screen, versus fetched per source. Fetching limit+1 would only
# ever prove "at least one more exists"; a bounded window instead makes the
# "and N more" count exact without a second COUNT query per source, and these
# tables hold a handful of rows per employee. Twenty, not eight (R12): Home
# shows five and scrolls the rest, so an HR backlog stays reachable without
# flooding the page; "View all (N)" carries the full count.
_QUEUE_LIMIT = 20

# The rail card's ceiling (P4-U9). Five rows is what fits the rail beside the
# queue without becoming the taller column; the page behind it is the full
# searchable catalogue.
_LINKS_LIMIT = 5
_QUEUE_FETCH = 50
# Plan 2026-10-04-003 KTD8: the timesheet and leave kinds serve a team of up
# to 50 reports, so their per-kind reads rise to 150 and the 25-row page is
# gone for them (R19); a hard cap of 200 stays, reported through
# `total_is_capped`.
_TIMESHEET_QUEUE_FETCH = 150
_LEAVE_QUEUE_FETCH = 150
_QUEUE_HARD_CAP = 200
# The bound on the one comment read that spans many records: a handful of
# comments per sent-back record, over a page of records (P3-R25).
_COMMENT_FETCH = 200
# P4-R5's hand-over note, marked. It is written for HR ("check this against the
# policy") and lives in the same Comment stream as the reason the *employee*
# reads, so an unmarked note was surfaced as the send-back reason by any later
# reason-less decision. Written in one place (`act_on_approval`) and matched in
# one place (`_hr_handover_note` / `_decision_reason_text`), like
# HR_REPLY_SUBJECT_PREFIX. Visible in the Desk timeline on purpose -- it says
# what the note was for.
HR_HANDOVER_NOTE_PREFIX = "Sent to HR:"
# blocked work, then an answer waiting to be read, then a decision this
# person owes somebody else. "waiting" never enters the queue; it is the
# urgency of the separate Waiting-on-others list.
_URGENCY_RANK = {"blocked": 0, "unread": 1, "decision": 2, "waiting": 3}
_URGENCY_TONE = {"blocked": "danger", "unread": "info", "decision": "action", "waiting": "muted"}


def _as_date(value):
	from frappe.utils import getdate

	return getdate(value)


def _notification_text(description):
	"""A Notification Log body as the one plain line the queue quotes.
	`description` is a rich-text field and hr_request_on_update escapes the
	note into it, so both directions have to be undone to get the sentence
	HR actually typed back."""
	if not description:
		return None

	from html import unescape

	return unescape(frappe.utils.strip_html(description)).strip() or None


def _decision_reason_text(content):
	"""One comment as a reason the employee may be shown, or None.

	None for a hand-over note: it was written about routing, not about a
	decision taken against them (P4-R5, P4-KTD7a)."""
	text = frappe.utils.strip_html(content).strip() if content else None
	if not text or text.startswith(HR_HANDOVER_NOTE_PREFIX):
		return None
	return text


def _hr_handover_note(content):
	"""The other half of the same marker: the note itself, without its
	prefix, or None when this comment is not a hand-over note."""
	text = frappe.utils.strip_html(content).strip() if content else None
	if not text or not text.startswith(HR_HANDOVER_NOTE_PREFIX):
		return None
	return text[len(HR_HANDOVER_NOTE_PREFIX) :].strip() or None


def _rejection_comments(doctype, names, employee=None):
	"""The approver's reason for every sent-back record of `doctype`, in one
	query (P2-R22: server queries avoid per-record comment lookups). This was
	one Comment read per queue row.

	Generalised by doctype in P3-U5: a sent-back Attendance Request carries
	its reason the same way a sent-back Timesheet does (P3-KTD9).

	Scoped to comments somebody *other than* the employee wrote, the way
	`_leave_reason` already is: the newest comment on a sent-back record is
	otherwise whatever the employee themselves last typed on it, which is not
	a reason it came back.

	Newest first and bounded (P3-R25: every read has a limit). Descending
	with "first one seen wins" is what makes the bound safe -- a truncated
	page can only cost an older record its reason, never replace a reason
	with a staler one.
	"""
	if not names:
		return {}

	employee_user = frappe.db.get_value("Employee", employee, "user_id") if employee else None
	latest = {}
	for row in frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": doctype,
			"reference_name": ["in", names],
			"comment_type": "Comment",
		},
		fields=["reference_name", "content", "owner"],
		order_by="creation desc",
		limit=_COMMENT_FETCH,
	):
		if row.reference_name in latest or (employee_user and row.owner == employee_user):
			continue
		text = _decision_reason_text(row.content)
		if text:
			latest[row.reference_name] = text
	return latest


def _last_rejection_comment(timesheet):
	"""The manager's reason, shown inline so the employee doesn't have to
	open the timesheet to find out what to change."""
	for row in frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": "Timesheet",
			"reference_name": timesheet,
			"comment_type": "Comment",
		},
		fields=["content"],
		order_by="creation desc",
		limit=_COMMENT_FETCH,
	):
		# Newest first, skipping the hand-over notes: the newest comment on a
		# sent-back week can be a note written for HR, which is not a reason
		# this employee was sent back (P4-R5).
		text = _decision_reason_text(row.content)
		if text:
			return text
	return None


def _summary_row(
	kind, doctype, name, employee, employee_name, from_date, to_date, sent_on, status, today, **extra
):
	"""One queue row, in the one shape every kind answers in (P3-U6 step 0).

	The keys are fixed, so the screen reads the same fields whichever kind a
	row is, and a kind that has nothing to say about `total_hours` says None
	rather than leaving the key out.
	"""
	row = {
		# Stable identity, and the Vue list key.
		"id": f"{kind}:{name}",
		"kind": kind,
		"doctype": doctype,
		"name": name,
		"employee": employee,
		"employee_name": employee_name,
		"initials": _initials(employee_name),
		"leave_type": None,
		"from_date": str(from_date) if from_date else None,
		"to_date": str(to_date) if to_date else None,
		"total_days": None,
		"total_hours": None,
		"status": status,
		"sent_on": str(sent_on) if sent_on else None,
		"age_days": _age_in_days(sent_on, today),
		# P4-R11. One queue, tagged: an HR Manager's list mixes their own
		# reports' work with everything a manager handed to HR, and only the
		# tag tells the two apart. Every row answers all three keys, so the
		# screen never has to ask whether a kind knows about them (P4-KTD7).
		"for_hr": False,
		"sent_to_hr_by": None,
		"hr_note": None,
		# U4 / R10: why HR sees a manager-stage leave -- "approver_away" or
		# "overdue" -- and None for everything else.
		"hr_reason": None,
		# Plan 2026-10-04-003 KTD7: the "Needs a look" flags, computed on the
		# server and shipped on the row, so the display and the batch
		# endpoint's re-check can never disagree. None for the kinds that
		# carry no flags (attendance, request, change).
		"flags": None,
		"needs_look": None,
	}
	row.update(extra)
	return row


# Plan 2026-10-04-003 KTD7 / R16. The flags live here and nowhere else: the
# queue computes them once per row, the batch endpoint re-runs the same
# helpers per item before approving (U6), and the screen only ever reads
# what arrived. A flag is a named boolean, never a colour.
#
# A timesheet needs a look when its hours differ from expected by more than
# 10%, an expected working day has no hours, it is a resubmission after a
# send-back, it arrives from an amend, or it has waited past the overdue
# threshold. A leave needs a look when approval would take the balance
# negative, it overlaps another report's leave, it is in the HR stage, or it
# starts within two days.
_TIMESHIFT_THRESHOLD = 0.10


def _working_days_index(employees, start, end):
	"""The raw parts of the expected-hours arithmetic over a date span:
	`standard` hours a day, each employee's holiday list, the holidays each
	list marks in the span, and the approved-leave days each employee has in
	it. One query per part for the whole span, so a queue page spanning
	several weeks costs the same as one spanning one (KTD8)."""
	standard = flt(frappe.db.get_single_value("HR Settings", "standard_working_hours")) or None
	rows = frappe.get_all(
		"Employee",
		filters={"name": ["in", list(employees)]},
		fields=["name", "holiday_list", "company"],
		ignore_permissions=True,
	)
	defaults = {
		row.name: row.default_holiday_list
		for row in frappe.get_all("Company", fields=["name", "default_holiday_list"])
	}
	lists = {row.name: row.holiday_list or defaults.get(row.company) for row in rows}

	holidays = {}
	if any(lists.values()):
		for row in frappe.get_all(
			"Holiday",
			filters={
				"parent": ["in", [name for name in lists.values() if name]],
				"parenttype": "Holiday List",
				"holiday_date": ["between", [str(start), str(end)]],
			},
			fields=["parent", "holiday_date"],
			ignore_permissions=True,
		):
			holidays.setdefault(row.parent, set()).add(str(row.holiday_date))

	leave_days = {}
	leaves = frappe.get_all(
		"Leave Application",
		filters={
			"employee": ["in", list(employees)],
			"docstatus": 1,
			"status": "Approved",
			"from_date": ["<=", str(end)],
			"to_date": [">=", str(start)],
		},
		fields=["employee", "from_date", "to_date"],
		ignore_permissions=True,
	)
	for leave in leaves:
		days = leave_days.setdefault(leave.employee, set())
		date = max(getdate(leave.from_date), getdate(start))
		last = min(getdate(leave.to_date), getdate(end))
		while date <= last:
			days.add(str(date))
			date = add_days(date, 1)

	return {"standard": standard, "lists": lists, "holidays": holidays, "leave_days": leave_days}


def _employee_working_days(index, employee, monday, sunday):
	"""The days in one week `employee` is expected at work, from the index:
	not on their holiday list, not covered by approved leave."""
	off_days = index["holidays"].get(index["lists"].get(employee), set())
	leave_days = index["leave_days"].get(employee, set())
	return {
		str(add_days(monday, offset))
		for offset in range(7)
		if str(add_days(monday, offset)) not in off_days and str(add_days(monday, offset)) not in leave_days
	}


def _working_days_by_employee(employees, monday, sunday):
	"""`{employee: set(date)}` for one week -- the index, folded to a week."""
	index = _working_days_index(employees, monday, sunday)
	return {employee: _employee_working_days(index, employee, monday, sunday) for employee in employees}, index[
		"standard"
	]


def _timesheet_flags_for(row, working_days, expected_hours, day_hours, today, threshold_days):
	"""The flags for one pending week (R16). `row` is a dict of the week's
	stored columns; `working_days` is the set of dates the employee was
	expected to work; `day_hours` the hours actually logged per date."""
	flags = {}
	if expected_hours is not None and flt(expected_hours) > 0:
		total = flt(row.get("total_hours"))
		if abs(total - flt(expected_hours)) / flt(expected_hours) > _TIMESHIFT_THRESHOLD:
			flags["hours_off"] = True
	if any(flt(day_hours.get(date, 0)) == 0 for date in working_days):
		flags["missing_day"] = True
	# A week still carrying its manager's send-back reason is a resubmission
	# -- one week is one row, so the reason is the only record of the round
	# trip (P4-KTD7a).
	if (row.get("helixhr_decision_reason") or "").strip():
		flags["resubmitted"] = True
	if row.get("amended_from"):
		flags["amended"] = True
	if is_overdue(row.get(PENDING_SINCE_FIELD), today, threshold_days):
		flags["overdue"] = True
	return flags


def _leave_flags_for(row, balance_after, overlap_count, today):
	"""The flags for one pending leave (R16)."""
	flags = {}
	if balance_after is not None and flt(balance_after) < 0:
		flags["negative_balance"] = True
	if cint(overlap_count) > 0:
		flags["overlap"] = True
	if row.get("for_hr"):
		flags["with_hr"] = True
	if row.get("from_date") and getdate(row["from_date"]) <= add_days(today, 2):
		flags["short_notice"] = True
	return flags


def _leave_names_in_hr_stage(names):
	"""Which of these applications are waiting for HR (P4-R5, P4-R7). One
	bounded read, because the caller already holds the names."""
	if not names:
		return set()
	return set(
		frappe.get_all(
			"Leave Application",
			filters={"name": ["in", names], "helixhr_stage": LEAVE_STAGE_HR},
			pluck="name",
			limit=len(names),
		)
	)


def _line_manager_filter(employee):
	"""The `employee` filter for the two workflow collectors, and None when
	this caller has no line-manager queue at all: their direct, Active
	reports, by `reports_to`, for every caller.

	It used to be "anybody but me" for an ordinary manager, trusting native
	read to narrow it. Native read is wider than the decision: Employee is a
	nested set, so a skip-level manager reads every descendant's Timesheet
	and Attendance Request, while `_may_act_on_timesheet` /
	`_may_act_on_attendance_request` accept only the direct manager. Those
	rows were listed and then refused with "That request isn't here." The
	leave detail's overlap count reads with `ignore_permissions`, so there
	"anybody but me" counted the whole company. An HR Manager reads the
	whole company natively; their HR half is a separate collector (P4-KTD7,
	P4-R11).
	"""
	# No limit, deliberately: this is a *filter*, not a page of rows. The
	# per-collector `limit=_QUEUE_FETCH` on the reads below is the real bound,
	# and capping the reports list here instead made the requests of anybody
	# past the 50th report disappear from the queue altogether.
	reports = frappe.get_all(
		"Employee",
		filters={"reports_to": employee, "status": "Active"},
		pluck="name",
	)
	return ["in", reports] if reports else None


def _leave_summaries(employee, today):
	"""HRMS filters leave by `leave_approver`, so this read is already
	scoped to decisions this session may make.

	P4-R7 subtracts one thing from it: a request in the HR stage is nobody's
	line-manager work any more, and `leave_approver` deliberately stays the
	manager so HRMS's own validation and DocShare keep working (P4-KTD4).
	HRMS returns its own field list and does not know about the stage, so the
	stage is asked for once, on the names it answered with, rather than by
	re-reading the applications.

	Plan 2026-10-04-003 KTD7/KTD8: each row gains the balance after approval
	(cached per employee and type), how many of the caller's other reports
	are off on overlapping days, and the R16 flags.
	"""
	from hrms.api import get_leave_applications

	applications = get_leave_applications(
		employee, approver_id=frappe.session.user, for_approval=True, limit=_LEAVE_QUEUE_FETCH
	)
	with_hr = _leave_names_in_hr_stage([row["name"] for row in applications])

	# The overlap count (R15): how many of this caller's *other* reports are
	# off on overlapping days. One read over the whole scope answers the
	# page; a caller with no reports has nobody to overlap with.
	scope = _line_manager_filter(employee)
	overlap_index = {}
	if scope and applications:
		first = min(str(row["from_date"]) for row in applications)
		last = max(str(row["to_date"]) for row in applications)
		for row in frappe.get_all(
			"Leave Application",
			filters={
				"employee": scope,
				"docstatus": ["<", 2],
				"status": ["in", ["Open", "Approved"]],
				"from_date": ["<=", last],
				"to_date": [">=", first],
			},
			fields=["employee", "from_date", "to_date"],
			ignore_permissions=True,
		):
			overlap_index.setdefault(row.employee, []).append((str(row.from_date), str(row.to_date)))

	def overlap_count(row):
		if not scope:
			return 0
		start, end = str(row["from_date"]), str(row["to_date"])
		others = 0
		for other, ranges in overlap_index.items():
			if other == row["employee"]:
				continue
			for range_start, range_end in ranges:
				if range_start <= end and start <= range_end:
					others += 1
					break
		return others

	balances = {}

	# The concurrency token the batch endpoint re-checks (U6): HRMS's own
	# projection carries `creation` but not `modified`, so the page's tokens
	# arrive in one query rather than one per row.
	modified_by_name = {
		row.name: str(row.modified)
		for row in frappe.get_all(
			"Leave Application",
			filters={"name": ["in", [row["name"] for row in applications] or [""]]},
			fields=["name", "modified"],
		)
	}

	def balance_after(row):
		"""The balance once this request is approved (R15). One HRMS ledger
		read per employee and type, cached for the page; None where the
		balance is not the kind of number that can go negative (LWP,
		negative-allowed, no allocation)."""
		key = (row["employee"], row["leave_type"])
		if key not in balances:
			try:
				result = leave_overdraw(row["employee"], row["leave_type"], row["from_date"], row["to_date"], 0)
			except Exception:
				result = None
			balances[key] = result["balance"] if result else None
		balance = balances[key]
		if balance is None:
			return None
		return flt(balance) - flt(row.get("total_leave_days") or 0)

	rows = []
	for row in applications:
		if row["name"] in with_hr:
			continue
		sent_on = row.get("creation") or row.get("posting_date")
		balance = balance_after(row)
		count = overlap_count(row)
		flags = _leave_flags_for(
			{**row, "for_hr": False}, balance, count, today
		)
		rows.append(
			_summary_row(
				"leave",
				"Leave Application",
				row["name"],
				row.get("employee"),
				row.get("employee_name"),
				row.get("from_date"),
				row.get("to_date"),
				sent_on,
				row.get("status"),
				today,
				leave_type=row.get("leave_type"),
				total_days=flt(row.get("total_leave_days")),
				balance_after=balance,
				overlap_count=count,
				flags=flags or {},
				needs_look=bool(flags),
				token_modified=modified_by_name.get(row["name"]),
			)
		)
	return rows


def _timesheet_summaries(employee, today):
	"""A timesheet reaches its approver through the Pending-Approval DocShare
	`timesheet_on_update` grants plus the nested-set User Permission a manager
	holds over their reports.

	This read used `frappe.get_all` until P2-U7. `get_all` is `get_list` with
	`ignore_permissions=True`, so it answered with *every* pending timesheet
	on the site regardless of who was asking -- the employee name and week of
	every person in the company, to anyone with a session.

	P4-KTD7: for an HR Manager, whose native read on Timesheet is that wide
	answer all over again, `_line_manager_filter` narrows this half of the
	queue to their own direct reports.

	Plan 2026-10-04-003 KTD7/KTD8: the evidence the flags need -- per-day
	hours, project split, expected hours, the send-back reason that marks a
	resubmission, `amended_from` and the pending-since stamp -- is batched
	for the page, and each row carries `flags` and `needs_look` (R15, R16).
	"""
	scope = _line_manager_filter(employee)
	if scope is None:
		return []
	rows = frappe.get_list(
		"Timesheet",
		filters={
			"workflow_state": PENDING_STATE,
			"docstatus": 0,
			"employee": scope,
		},
		fields=[
			"name",
			"employee",
			"employee_name",
			"start_date",
			"end_date",
			"total_hours",
			"modified",
			"amended_from",
			"helixhr_decision_reason",
			PENDING_SINCE_FIELD,
		],
		order_by="start_date asc",
		limit=_TIMESHEET_QUEUE_FETCH,
	)
	if not rows:
		return []

	# `modified` is when the week last moved, which for a Pending
	# Approval timesheet is when it was sent. Timesheet has no
	# submitted-on field of its own and the workflow transition is a
	# plain field update, so this is the closest honest answer.
	day_hours, project_split = _team_time_logs([row.name for row in rows])
	week_bounds = [get_week_bounds(row.start_date) for row in rows]
	index = _working_days_index(
		{row.employee for row in rows},
		min(week[0] for week in week_bounds),
		max(week[1] for week in week_bounds),
	)
	threshold = approval_overdue_days()
	# The send-back reason sits at permlevel 1, which `get_list` strips for a
	# caller without the level -- it is the flag's input here, read in one
	# batched pass and never shipped on the row (the flag travels, the words
	# stay with the decision detail).
	reasons = {
		row.name: row.helixhr_decision_reason
		for row in frappe.get_all(
			"Timesheet",
			filters={"name": ["in", [row.name for row in rows]]},
			fields=["name", "helixhr_decision_reason"],
			ignore_permissions=True,
		)
	}

	result = []
	for row, (monday, sunday) in zip(rows, week_bounds, strict=True):
		working = _employee_working_days(index, row.employee, monday, sunday)
		expected = flt(index["standard"] * len(working)) if index["standard"] and working else None
		flags = _timesheet_flags_for(
			{**row, "helixhr_decision_reason": reasons.get(row.name)},
			working,
			expected,
			day_hours.get(row.name, {}),
			today,
			threshold,
		)
		result.append(
			_summary_row(
				"timesheet",
				"Timesheet",
				row.name,
				row.employee,
				row.employee_name,
				row.start_date,
				row.end_date,
				row.modified,
				"Pending Approval",
				today,
				total_hours=flt(row.total_hours),
				expected_hours=expected,
				hours=day_hours.get(row.name, {}),
				project_split=project_split.get(row.name, []),
				flags=flags or {},
				needs_look=bool(flags),
				# The concurrency token the batch endpoint re-checks (U6).
				token_modified=str(row.modified),
			)
		)
	return result


def _attendance_request_summaries(employee, today):
	"""Pending Manager rows only (P3-KTD7, P3-R16).

	A request that has reached Pending HR is not this half of the queue's
	work: the manager's step is done. It reaches an HR Manager through
	`_hr_attendance_request_summaries` instead, tagged, so a manager who is
	also HR is never offered two versions of the same row (P4-KTD7).

	`frappe.get_list` as the session user: the manager reaches a report's
	request through the `write` DocShare `events.attendance_request_on_update`
	grants for exactly this state, and through nothing else.

	P4-KTD7: an HR Manager reaches every one of them through the role
	instead, so `_line_manager_filter` narrows this half of their queue to
	their own direct reports; what a manager handed to HR arrives through the
	HR collector, tagged.
	"""
	scope = _line_manager_filter(employee)
	if scope is None:
		return []
	rows = frappe.get_list(
		"Attendance Request",
		filters={
			"workflow_state": REQUEST_PENDING_MANAGER,
			"docstatus": 0,
			"employee": scope,
		},
		fields=[
			"name",
			"employee",
			"employee_name",
			"from_date",
			"to_date",
			"half_day",
			"half_day_date",
			"reason",
			"explanation",
			"modified",
		],
		order_by="modified asc",
		limit=_QUEUE_FETCH,
	)
	# The Submit transition is a plain field update, so `modified` is when the
	# employee sent it -- the same reasoning as the timesheet's.
	shown = _requested_day_status(rows)
	return [
		_summary_row(
			"attendance",
			"Attendance Request",
			row.name,
			row.employee,
			row.employee_name,
			row.from_date,
			row.to_date,
			row.modified,
			REQUEST_PENDING_MANAGER,
			today,
			total_days=date_diff(row.to_date, row.from_date) + 1,
			reason=row.reason,
			explanation=(row.explanation or "").strip() or None,
			half_day=bool(cint(row.half_day)),
			half_day_date=str(row.half_day_date) if row.half_day_date else None,
			# What the calendar already shows for those days -- the manager's
			# evidence, and the reason a "fix Tuesday" request over a Present
			# Tuesday is visibly not a correction.
			calendar=shown.get(row.name, []),
		)
		for row in rows
	]


def _attendance_status_by_date(employees, start, end):
	"""What attendance already exists for these employees across the range, as
	`{(employee, "YYYY-MM-DD"): status}` -- one query for the whole set rather
	than one per person or per request (P2-R22, P3-U9).

	The one lookup behind all three readers: the Approvals queue, one decision's
	detail and the attendance-request preview. Regardless of shift, deliberately
	-- HRMS's own lookup is shift-scoped, so an open-ended Shift Assignment
	(which leaves `shift` empty on the request) would hide exactly the row an
	approved request goes on to rewrite (P3-KTD14).

	`get_all` on purpose: the queue's rows came back from a permission-checked
	`get_list`, one detail is gated by `_assert_may_act_on`, and the preview
	only ever asks about the session's own employee -- and the days a request
	names are exactly the evidence P3-R16 says the manager must see before
	deciding.
	"""
	if not employees:
		return {}
	return {
		(row.employee, str(row.attendance_date)): row.status
		for row in frappe.get_all(
			"Attendance",
			filters={
				"employee": ["in", list(employees)],
				"attendance_date": ["between", [str(start), str(end)]],
				"docstatus": ["<", 2],
			},
			fields=["employee", "attendance_date", "status"],
		)
	}


def _attendance_days(employee, start, end, statuses):
	"""One `{date, status}` per day of the range, read out of the map
	`_attendance_status_by_date` returned (P3-U9)."""
	days = []
	date, last = _as_date(start), _as_date(end)
	while date <= last:
		iso = str(date)
		days.append({"date": iso, "status": statuses.get((employee, iso))})
		date = add_days(date, 1)
	return days


def _requested_day_status(rows):
	"""What the calendar already shows for every day the given requests cover,
	by request name -- one query for the whole queue (P2-R22, P3-R16)."""
	if not rows:
		return {}

	statuses = _attendance_status_by_date(
		{row.employee for row in rows},
		min(_as_date(row.from_date) for row in rows),
		max(_as_date(row.to_date) for row in rows),
	)
	return {
		row.name: _attendance_days(row.employee, row.from_date, row.to_date, statuses)
		for row in rows
	}


# ---------------------------------------------------------------------------
# The HR queue (P4-R11, P4-KTD7)
#
# One list, not a second page: an HR Manager who is also a line manager would
# otherwise have two backlogs to poll. The three collectors below answer with
# everything waiting for HR across the three kinds; the line-manager
# collectors above answer with their own reports' work, narrowed by
# `_line_manager_filter`. The two halves cannot overlap, because the states
# they filter on are disjoint.
#
# Each of them runs as the session user. HR Manager holds native read on all
# three doctypes, which is exactly why the *other* half needed narrowing --
# here the wide read is the point.
# ---------------------------------------------------------------------------


def _hr_senders(doctype, rows):
	"""Who put each of these records in the HR queue, and the note they left,
	as `{name: {"by": full name or None, "note": text or None}}`.

	This answers the question P4 left to implementation. The sender is the
	record's own `modified_by`; the note is the newest Comment that user left
	on it.

	`Version` was the alternative and cannot answer for leave at all: the
	stage is permlevel 1 and is therefore written with `db_set` (P4-KTD4),
	which runs no `save` and so writes no Version row -- for the one kind
	whose escalation is not a workflow transition, the table would be empty.
	Version is also gated on `track_changes`, a Desk-editable flag, and would
	need its JSON diff parsed per row. `modified_by` is already a column of
	the collector's own read, and the note is the same bounded, newest-first
	Comment query `_rejection_comments` already is.

	A record whose last writer is the employee themselves has no sender: that
	is a leave of an HR-approves type, which nobody handed over -- it started
	in the HR queue (P4-R7).
	"""
	if not rows:
		return {}

	writers = {row["name"]: row.get("modified_by") for row in rows}
	employee_users = {
		row.name: row.user_id
		for row in frappe.get_all(
			"Employee",
			filters={"name": ["in", list({row["employee"] for row in rows if row.get("employee")})]},
			fields=["name", "user_id"],
		)
	}

	notes = {}
	for comment in frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": doctype,
			"reference_name": ["in", list(writers)],
			"comment_type": "Comment",
		},
		fields=["reference_name", "content", "owner"],
		# Newest first with "first one seen wins", so a truncated page can
		# only cost an older record its note, never show a staler one.
		order_by="creation desc",
		limit=_COMMENT_FETCH,
	):
		if comment.owner != writers.get(comment.reference_name) or comment.reference_name in notes:
			continue
		# Only a marked hand-over note. The newest comment by the same writer
		# was the old answer and could be the reason they sent the record
		# *back* on an earlier round, which is not what HR was told (P4-R5).
		text = _hr_handover_note(comment.content)
		if text:
			notes[comment.reference_name] = text

	full_names = {
		row.name: row.full_name
		for row in frappe.get_all(
			"User",
			filters={"name": ["in", list({writer for writer in writers.values() if writer})]},
			fields=["name", "full_name"],
		)
	}

	senders = {}
	for row in rows:
		writer = writers.get(row["name"])
		if not writer or writer == employee_users.get(row.get("employee")):
			senders[row["name"]] = {"by": None, "note": None}
		else:
			senders[row["name"]] = {
				"by": full_names.get(writer) or writer,
				"note": notes.get(row["name"]),
			}
	return senders


def _hr_queue_employee_filter(employee):
	"""The `employee` filter for HR's own queue collectors: everyone but the
	caller, inside the company scope every administrative read uses
	(`resolve_admin_scope`, P6-R6). `None` means the caller may see nobody.

	The HRMS doctypes these collectors read (Leave Application, Timesheet,
	Attendance Request) carry no company scoping of this app's own, so a
	company-anchored HR Manager used to see every company's queue here while
	`hr_request.py`'s hook scoped the fourth kind -- the two halves of the
	same queue disagreed. HR Request is deliberately NOT routed through this:
	its collector also serves routed workers (`IT Team`), who hold no admin
	scope at all, and its own permission hook already narrows both personas.
	"""
	scope = resolve_admin_scope(frappe.session.user)
	if scope["kind"] == "none":
		return None
	if scope["kind"] == "unscoped":
		return ["!=", employee]
	names = [
		name
		for name in frappe.get_all(
			"Employee", filters=admin_scope_employee_filters(scope), pluck="name"
		)
		if name != employee
	]
	return ["in", names] if names else None


def _hr_leave_summaries(employee, today):
	"""Leave waiting for HR: status Open at docstatus 0 in stage HR, whether a
	manager sent it over or the Leave Type routed it there (P4-R5, P4-R7)."""
	employee_filter = _hr_queue_employee_filter(employee)
	if employee_filter is None:
		return []
	rows = frappe.get_list(
		"Leave Application",
		filters={
			"status": "Open",
			"docstatus": 0,
			"helixhr_stage": LEAVE_STAGE_HR,
			"employee": employee_filter,
		},
		fields=[
			"name",
			"employee",
			"employee_name",
			"leave_type",
			"from_date",
			"to_date",
			"total_leave_days",
			"status",
			"creation",
			"modified_by",
		],
		order_by="creation asc",
		limit=_QUEUE_FETCH,
	)
	senders = _hr_senders("Leave Application", rows)

	def flags_for(row):
		# R16: a send-to-HR item needs a look wherever it waits. HR's own
		# view carries the same balance and short-notice flags the manager's
		# half computes; the overlap count stays the line manager's notion
		# and is not re-derived here.
		try:
			result = leave_overdraw(row.employee, row.leave_type, row.from_date, row.to_date, 0)
		except Exception:
			result = None
		balance = flt(result["balance"]) - flt(row.total_leave_days) if result else None
		return _leave_flags_for(
			{"for_hr": True, "from_date": row.from_date}, balance, 0, today
		), balance

	flagged = [flags_for(row) for row in rows]
	return [
		_summary_row(
			"leave",
			"Leave Application",
			row.name,
			row.employee,
			row.employee_name,
			row.from_date,
			row.to_date,
			row.creation,
			row.status,
			today,
			leave_type=row.leave_type,
			total_days=flt(row.total_leave_days),
			balance_after=flagged[index][1],
			flags=flagged[index][0],
			needs_look=bool(flagged[index][0]),
			for_hr=True,
			sent_to_hr_by=senders[row.name]["by"],
			hr_note=senders[row.name]["note"],
		)
		for index, row in enumerate(rows)
	] + _hr_stalled_leave_summaries(employee, today, employee_filter)


# Plan 2026-10-02-001 R25 / KTD13. Calendar days, the default the plan chose;
# working days is the recorded upgrade path.
APPROVAL_OVERDUE_DAYS_DEFAULT = 2


def approval_overdue_days():
	"""R25's threshold for leave, timesheets and attendance requests: site
	config `helixhr_approval_overdue_days`, default 2 calendar days."""
	return max(0, cint(frappe.conf.get("helixhr_approval_overdue_days", APPROVAL_OVERDUE_DAYS_DEFAULT)))


def is_overdue(pending_since, today, threshold):
	"""The one overdue predicate (U4; U11 and U12 reuse it): pending for
	longer than the threshold, counted from `helixhr_pending_since`."""
	if not pending_since:
		return False
	return _age_in_days(pending_since, today) > threshold


def _approvers_away(users, today):
	"""Which of these approver users are on approved, submitted leave today
	(half days included) -- KTD4's "away"."""
	if not users:
		return set()
	employees = {
		row.name: row.user_id
		for row in frappe.get_all(
			"Employee", filters={"user_id": ["in", list(users)]}, fields=["name", "user_id"]
		)
	}
	if not employees:
		return set()
	on_leave = frappe.get_all(
		"Leave Application",
		filters={
			"employee": ["in", list(employees)],
			"docstatus": 1,
			"status": "Approved",
			"from_date": ["<=", today],
			"to_date": [">=", today],
		},
		pluck="employee",
	)
	return {employees[name] for name in on_leave}


def _hr_stalled_leave_summaries(employee, today, employee_filter):
	"""Manager-stage leave HR may decide because the approver is away or the
	request is overdue (R10, KTD4). Admin-scoped by `employee_filter`, never
	narrowed by `_line_manager_filter`: these are other managers' reports.
	The caller's own approvals are already in their line-manager half."""
	rows = frappe.get_all(
		"Leave Application",
		filters={
			"status": "Open",
			"docstatus": 0,
			"helixhr_stage": ["in", ["", None, _LEAVE_STAGE_MANAGER]],
			"employee": employee_filter,
			"leave_approver": ["not in", ["", frappe.session.user]],
		},
		fields=[
			"name",
			"employee",
			"employee_name",
			"leave_type",
			"from_date",
			"to_date",
			"total_leave_days",
			"status",
			"creation",
			"leave_approver",
			PENDING_SINCE_FIELD,
		],
		order_by="creation asc",
		limit=_QUEUE_FETCH,
	)
	away = _approvers_away({row.leave_approver for row in rows}, today)
	threshold = approval_overdue_days()
	stalled = []
	for row in rows:
		if row.leave_approver in away:
			reason = "approver_away"
		elif is_overdue(row.get(PENDING_SINCE_FIELD), today, threshold):
			reason = "overdue"
		else:
			continue
		stalled.append(
			_summary_row(
				"leave",
				"Leave Application",
				row.name,
				row.employee,
				row.employee_name,
				row.from_date,
				row.to_date,
				row.creation,
				row.status,
				today,
				leave_type=row.leave_type,
				total_days=flt(row.total_leave_days),
				for_hr=True,
				hr_reason=reason,
			)
		)
	return stalled


def _hr_timesheet_summaries(employee, today):
	"""Weeks a manager handed over. Pending HR carries no DocShare on
	purpose (P4-U1) -- HR reaches these through the role."""
	employee_filter = _hr_queue_employee_filter(employee)
	if employee_filter is None:
		return []
	rows = frappe.get_list(
		"Timesheet",
		filters={
			"workflow_state": TIMESHEET_PENDING_HR,
			"docstatus": 0,
			"employee": employee_filter,
		},
		fields=[
			"name",
			"employee",
			"employee_name",
			"start_date",
			"end_date",
			"total_hours",
			"modified",
			"modified_by",
		],
		order_by="start_date asc",
		limit=_QUEUE_FETCH,
	)
	senders = _hr_senders("Timesheet", rows)
	return [
		_summary_row(
			"timesheet",
			"Timesheet",
			row.name,
			row.employee,
			row.employee_name,
			row.start_date,
			row.end_date,
			row.modified,
			TIMESHEET_PENDING_HR,
			today,
			total_hours=flt(row.total_hours),
			for_hr=True,
			sent_to_hr_by=senders[row.name]["by"],
			hr_note=senders[row.name]["note"],
		)
		for row in rows
	]


def _hr_attendance_request_summaries(employee, today):
	"""Attendance requests a manager handed over, with the same evidence the
	manager read before doing so -- including what the calendar shows for
	each day, which is the whole reason an overwrite is HR's decision and not
	theirs (P4-KTD5)."""
	employee_filter = _hr_queue_employee_filter(employee)
	if employee_filter is None:
		return []
	rows = frappe.get_list(
		"Attendance Request",
		filters={
			"workflow_state": REQUEST_PENDING_HR,
			"docstatus": 0,
			"employee": employee_filter,
		},
		fields=[
			"name",
			"employee",
			"employee_name",
			"from_date",
			"to_date",
			"half_day",
			"half_day_date",
			"reason",
			"explanation",
			"modified",
			"modified_by",
		],
		order_by="modified asc",
		limit=_QUEUE_FETCH,
	)
	senders = _hr_senders("Attendance Request", rows)
	shown = _requested_day_status(rows)
	return [
		_summary_row(
			"attendance",
			"Attendance Request",
			row.name,
			row.employee,
			row.employee_name,
			row.from_date,
			row.to_date,
			row.modified,
			REQUEST_PENDING_HR,
			today,
			total_days=date_diff(row.to_date, row.from_date) + 1,
			reason=row.reason,
			explanation=(row.explanation or "").strip() or None,
			half_day=bool(cint(row.half_day)),
			half_day_date=str(row.half_day_date) if row.half_day_date else None,
			calendar=shown.get(row.name, []),
			for_hr=True,
			sent_to_hr_by=senders[row.name]["by"],
			hr_note=senders[row.name]["note"],
		)
		for row in rows
	]


def _hr_request_summaries(employee, today, states=None, picked_up_by=None, routed_roles=None, limit=None, start=None):
	"""Every routed request the session user may work right now (P5-R11,
	P5-R12).

	There is no line-manager half for this kind -- a routed role's whole
	queue arrives through this collector, reached from `_approval_summaries`'s
	`hr_queue` loop rather than from `_APPROVAL_SUMMARY_COLLECTORS`. Scope is
	not repeated here: `frappe.get_list` runs as the session user, and
	`hr_request.get_permission_query_conditions` (P5-U5) already narrows the
	rows to an HR Manager's company or a routed-role holder's own route
	before this function ever sees them.

	`Waiting on Employee` is excluded on purpose -- it is the employee's
	backlog while they hold the ball, not the worker's (P5-R6), and counting
	it here would show a manager or IT holder a request there is currently
	nothing for them to do about.

	`for_hr` is true only when the **stored** route is HR Manager: an IT
	Team holder's rows must never carry Home's "waiting for HR" caption
	(P4-KTD7's tag, applied to a fourth kind for the first time).

	Plan 2026-10-04-002 U3 / KTD5: the same query behind the Requests page's
	"To work on" tab, so counts can never disagree between the two views.
	The optional arguments widen or narrow what Approvals asks by default:

	* `states` replaces the default Open / In Progress pair -- the work
	  feed's Waiting and Closed chips ask for the other statuses explicitly;
	  nothing else does, so the queue keeps excluding "Waiting on Employee"
	  and "Done / Rejected".
	* `picked_up_by` narrows to the rows that caller picked up (the Mine
	  chip). The queue never passes it.
	* `routed_roles` narrows to the roles the caller holds -- an HR Manager
	  cannot act on an IT-routed row, so the work feed refuses to show one;
	  the queue keeps its wider behaviour untouched.
	* `limit` / `start` page the feed; the queue stays bounded at
	  `_QUEUE_FETCH` and reports a floor instead (P3-R25).

	The filter dict itself is built by `_request_summaries_filters`, so the
	feed's page and its count can never ask two different questions.

	Rows always carry `picked_up_by_name` (resolved server-side, KTD4) and
	`sla_overdue` (the category's SLA against age, `is_overdue`'s predicate)
	-- keys the queue's consumers ignore and the feed renders.
	"""
	filters = _request_summaries_filters(
		employee, states or (HR_REQUEST_OPEN, HR_REQUEST_IN_PROGRESS), picked_up_by, routed_roles
	)
	rows = frappe.get_list(
		"HR Request",
		filters=filters,
		fields=[
			"name",
			"employee",
			"category",
			"subject",
			"status",
			"routed_to_role",
			"picked_up_by",
			"creation",
			"modified",
			"hr_note",
			# Plan 2026-10-02-001 U14 / R28: the masked copy only -- the
			# Password fields are never read into a queue.
			"correction_field",
			"correction_proposed_masked",
		],
		order_by="creation asc",
		limit=_QUEUE_FETCH if limit is None else min(max(cint(limit), 1), _REQUEST_MAX_PAGE),
		**({"limit_start": max(cint(start), 0)} if start is not None else {}),
	)
	if not rows:
		return []

	employee_names = {
		row.name: row.employee_name
		for row in frappe.get_all(
			"Employee",
			filters={"name": ["in", list({row.employee for row in rows})]},
			fields=["name", "employee_name"],
		)
	}
	picker_names = {
		user: _picker_display_name(user)
		for user in {row.picked_up_by for row in rows if row.picked_up_by}
	}
	# One read per page, not per row (P2-R22): SLA days ride beside the
	# routed role the categories already answer.
	sla_days = {
		name: cint(sla)
		for name, sla in frappe.get_all("HelixHR Request Category", fields=["name", "sla_days"], as_list=True)
	}
	return [
		_summary_row(
			"request",
			"HR Request",
			row.name,
			row.employee,
			employee_names.get(row.employee) or row.employee,
			None,
			None,
			row.creation,
			row.status,
			today,
			category=row.category,
			subject=row.subject,
			routed_to_role=row.routed_to_role,
			picked_up_by=row.picked_up_by,
			picked_up_by_name=picker_names.get(row.picked_up_by),
			sla_overdue=is_overdue(row.creation, today, sla_days.get(row.category, 0))
			and row.status in (HR_REQUEST_OPEN, HR_REQUEST_IN_PROGRESS),
			for_hr=(row.routed_to_role == "HR Manager"),
			hr_note=row.hr_note,
			correction_field=row.correction_field,
			correction_proposed_masked=row.correction_proposed_masked,
		)
		for row in rows
	]


def _request_summaries_filters(employee, states, picked_up_by=None, routed_roles=None):
	"""The one place the request summaries' filter dict is built: the queue,
	the work feed's page and the feed's count all ask through it, so the
	"both views read one function" claim (plan 2026-10-04-002 KTD5) cannot
	drift into two hand-built copies of the same rule."""
	filters = {
		"status": ["in", states],
		"employee": ["!=", employee],
	}
	if picked_up_by:
		filters["picked_up_by"] = picked_up_by
	if routed_roles:
		filters["routed_to_role"] = ["in", routed_roles]
	return filters


# Plan 2026-10-04-002 U3: the "To work on" chips. Each chip names the
# statuses it can mean; "open" is the working set, "waiting" is the ball in
# the employee's hands, "closed" is off by default.
_REQUEST_WORK_STATES = {
	"open": (HR_REQUEST_OPEN, HR_REQUEST_IN_PROGRESS),
	"mine": (HR_REQUEST_OPEN, HR_REQUEST_IN_PROGRESS),
	"waiting": (HR_REQUEST_WAITING_ON_EMPLOYEE,),
	"closed": (HR_REQUEST_DONE, HR_REQUEST_REJECTED),
}


@frappe.whitelist()
def get_request_work(state=None, limit=None, start=0):
	"""The "To work on" feed behind /requests' second tab (plan
	2026-10-04-002 U3, R7-R8).

	The same rows Approvals' request half shows, read from the same query
	(`_hr_request_summaries`) so the two views can never disagree, with the
	state chips the tab offers: `open` (the default), `mine` (picked up by
	the caller), `waiting` (on the employee) and `closed`. A caller with no
	routed role and no HR role is refused -- the same
	`_is_hr() or _holds_routed_role()` gate the queue itself uses, never a
	route name -- and the rows the query answers are further narrowed by
	Frappe's own permission conditions (company for a company-anchored HR
	Manager, own route for a routed-role holder) and by the roles the
	caller actually holds.
	"""
	rate_limit_per_user("get_request_work")
	if not (_is_hr() or _holds_routed_role()):
		frappe.throw(_("You don't have permission to do that."), frappe.PermissionError)

	state = (state or "").strip().lower() or "open"
	if state not in _REQUEST_WORK_STATES:
		frappe.throw(_("There is no work filter called {0}.").format(state))

	roles = set(frappe.get_roles())
	routed_roles = [role for role in ("HR Manager", *_ROUTED_WORKER_ROLES) if role in roles]
	if not routed_roles:
		# An HR-adjacent session (System Manager) without either role that
		# routes requests: nothing here is theirs to work.
		return {"work": [], "total": 0, "limit": _REQUEST_PAGE, "today": user_today()}

	employee = get_current_employee()
	today = _as_date(user_today())
	page = min(max(cint(limit) or _REQUEST_PAGE, 1), _REQUEST_MAX_PAGE)
	filters = {
		"states": _REQUEST_WORK_STATES[state],
		"routed_roles": routed_roles,
		"limit": page,
		"start": max(cint(start), 0),
	}
	if state == "mine":
		filters["picked_up_by"] = frappe.session.user

	rows = _hr_request_summaries(employee, today, **filters)
	# One count for the same filter, so Load More has an honest end (P2-R22).
	# Read through `get_list`, not a bare COUNT: the caller's permission
	# conditions are the scope, and the count must answer exactly what the
	# page above was scoped to.
	total = len(
		frappe.get_list(
			"HR Request",
			filters=_request_summaries_filters(
				employee,
				_REQUEST_WORK_STATES[state],
				frappe.session.user if state == "mine" else None,
				routed_roles,
			),
			pluck="name",
			limit_page_length=0,
		)
	)
	return {
		"work": rows,
		"total": total,
		"limit": page,
		"today": user_today(),
	}


# Per-kind, never "not leave means timesheet" (P3-U6 step 0). A third kind
# landed in P3-U5, and every one of these helpers used to branch on one
# doctype and treat everything else as the other. Each carries its own read
# bound (KTD8): the two kinds that serve a 50-report team read deeper. The
# change kind joins the tuple where it is defined, further down.
_APPROVAL_SUMMARY_COLLECTORS = (
	(_leave_summaries, _LEAVE_QUEUE_FETCH),
	(_timesheet_summaries, _TIMESHEET_QUEUE_FETCH),
	(_attendance_request_summaries, _QUEUE_FETCH),
)


def _approval_summaries(employee):
	"""Every decision the session user may make right now, as one bounded,
	typed, oldest-first list -- leave, timesheet *and* attendance request
	(P2-R11, P2-U7 step 1, P3-R16).

	This is the single source of the three things that used to be answered
	separately and could therefore disagree: what the Approvals queue shows,
	what Home counts as a decision the manager owes, and whether the
	Approvals nav item is drawn at all. It is *not* an authorization
	decision -- `_assert_may_act_on` re-checks who may act, on the server,
	on every read of a detail and on every action.

	Each kind's read runs as the session user, so Frappe's own permissions
	decide what comes back; the per-kind collector says how.
	"""
	today = _as_date(user_today())
	rows = []
	# Whether any collector came back full, and the total is therefore a floor
	# rather than a count. Each read is bounded at `_QUEUE_FETCH` per kind
	# (P3-R25), so a backlog of 200 used to be reported as 50 with nothing
	# saying so.
	capped = False
	# A routed-role holder who is not also an Employee (P5-U2 ships IT Team
	# without that role, deliberately -- P5-KTD10) has no possible
	# line-manager backlog at all. Leave Application, Timesheet and
	# Attendance Request each grant their baseline read to role Employee; a
	# caller holding neither that role nor an HR one has no DocPerm row on
	# any of the three at all, which `frappe.get_list` answers with a hard
	# PermissionError, not an empty list -- discovered by this gate's own
	# widening in P5-U6, since nothing reached this function as that kind of
	# caller before.
	if "Employee" in frappe.get_roles():
		for collect, bound in _APPROVAL_SUMMARY_COLLECTORS:
			collected = collect(employee, today)
			capped = capped or len(collected) >= bound
			rows.extend(collected)
	# P4-R11: one queue. An HR Manager's own reports' work arrived above,
	# narrowed by `_line_manager_filter`; everything waiting for HR is added
	# here and tagged, so the two halves are one oldest-first backlog rather
	# than two lists to poll.
	#
	if _is_hr():
		for kind in _APPROVAL_KINDS.values():
			collected = kind["hr_queue"](employee, today)
			capped = capped or len(collected) >= _QUEUE_FETCH
			rows.extend(collected)
	elif _holds_routed_role():
		# P5-R11: a routed-role holder (IT Team today) has no line-manager
		# half and no standing on Leave Application, Timesheet or Attendance
		# Request at all -- calling their `hr_queue` collectors the way the
		# `_is_hr()` branch above does would be the same hard PermissionError
		# the line-manager loop hit above, not an empty answer. Their whole
		# queue is `_hr_request_summaries`, and only that one is reached.
		collected = _hr_request_summaries(employee, today)
		capped = capped or len(collected) >= _QUEUE_FETCH
		rows.extend(collected)

	# Oldest first: the queue is a backlog, and the person who has waited
	# longest is the one the manager is holding up (P2-U7 step 7).
	rows.sort(key=lambda entry: (entry["sent_on"] or "", entry["name"]))
	return _with_photo_urls(rows), capped


def _holds_routed_role(user=None):
	"""Whether the caller holds a role a category may route work to, other
	than HR Manager -- `_is_hr` already covers HR Manager, and this is the
	other half of P5-R11's "is HR or holds a routed role" gate."""
	user = user or frappe.session.user
	return bool(set(frappe.get_roles(user)) & _ROUTED_WORKER_ROLES)


def _can_edit_email_templates(user=None):
	"""Whether the caller owns every portal email -- the shared theme, the
	message templates and the celebration / holiday mail: HelixHR Portal
	Admin or System Manager. HR roles and the Notification Manager are
	refused (the page is portal configuration, not HR data).

	Plan 2026-10-06-001 U3 narrowed `can_admin_portal` to exactly this role
	set, so the two predicates are now one -- delegated rather than
	duplicated, so a future role-set change is made once."""
	return can_admin_portal(user)


def _initials(full_name):
	"""Two letters for the row's avatar. The name is always printed beside
	it, so this is a second reading of it and never the only one."""
	parts = [part for part in (full_name or "").split() if part]
	if not parts:
		return "?"
	return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()


def _age_in_days(value, today):
	if not value:
		return None
	return max(0, (today - _as_date(value)).days)


def _pending_approvals(employee):
	"""The same decisions, shaped for Home's action queue (P2-R11, P2-R12).

	One source, two shapes: the queue row and the Approvals list are the
	same server answer, so a decision can never be on one and missing from
	the other.
	"""
	decisions = []
	rows, _capped = _approval_summaries(employee)
	for row in rows:
		title = _QUEUE_TITLE[row["kind"]](row)
		if row["for_hr"]:
			# P4-R11: Home's queue says which hat this decision is under.
			# Without it an HR Manager who is also a line manager reads five
			# identical lines and cannot tell which are theirs as a manager.
			title = f"{title} · waiting for HR"
		decisions.append(
			{
				"kind": f"approval_{row['kind']}",
				"reference_doctype": row["doctype"],
				"reference_name": row["name"],
				"route_kind": row["kind"],
				"title": title,
				"for_hr": row["for_hr"],
				"date": row["from_date"],
			}
		)
	return decisions


# One sentence per kind, named explicitly (P3-U6 step 0). "Not leave" used to
# mean "sent a week for your approval", which an attendance request is not.
_QUEUE_TITLE = {
	"leave": lambda row: f"{row['employee_name']} asked for {row['leave_type']}",
	"timesheet": lambda row: f"{row['employee_name']} sent a week for your approval",
	"attendance": lambda row: f"{row['employee_name']} asked for {row['reason']}",
	"request": lambda row: f"{row['employee_name']} filed a {row['category']} request",
	"change": lambda row: f"{row['employee_name']} asked to change an approved week",
}


# ---------------------------------------------------------------------------
# Leave (P2-U5, P2-R10, P2-R14, P2-R22, P2-R27)
#
# Every leave read and write an employee performs goes through this section
# rather than through `frappe.client.*` from the browser. Three reasons, in
# order of how much they matter:
#
#   1. The browser used to call `frappe.client.delete` to withdraw. That is a
#      caller-controlled generic write: the only thing standing between it and
#      somebody else's leave was Frappe's own permission check, and nothing at
#      all stood between it and an *approved* one but the UI's decision not to
#      draw the button (P2-R27).
#   2. Half-day and approver correctness are properties of the record, not of
#      the form. `apply_for_leave` sets `half_day_date` from `from_date` and
#      refuses outright when no approver exists, so neither can be wrong
#      because a watcher in a Vue component did not fire (P2-U5 scenarios 3
#      and 4).
#   3. The day count, the non-working days and the approver's *name* are
#      server facts. Recomputing any of them in the browser would be a second
#      implementation of HRMS's eligibility rules, which is exactly the thing
#      this unit is told not to build.
# ---------------------------------------------------------------------------

# The bounded first page (P2-R22). A long-serving employee has hundreds of
# rows and the screen shows two groups of a handful; the rest arrives through
# an explicit "Show N more" rather than by fetching the lot on every visit.
_LEAVE_PAGE = 20
_LEAVE_MAX_PAGE = 200

# How far ahead of a leave request the day-count endpoint will look. A leave
# application cannot cross two allocation records anyway, so anything past a
# year is a typo or a probe, and refusing it cheaply keeps a caller from
# asking the holiday resolver for a decade of dates (P2-R22).
_LEAVE_MAX_SPAN_DAYS = 366

# P4-KTD4. Leave has no Workflow (P2-KTD17) -- HRMS's own lifecycle is the
# right one -- so the queue a request is waiting in is one Custom Field beside
# it, at permlevel 1 so only HR can move it through a generic write. These are
# its two options; nothing else is ever written to the field. "HR" is
# `events.LEAVE_STAGE_HR`, imported above, because the submit hook enforces
# the same value on the raw route.
_LEAVE_STAGE_MANAGER = "Manager"


def _leave_state(status, docstatus):
	"""The lifecycle state the *portal* reasons about, which is not the same
	thing as `status` on its own (P2-R10).

	  open            docstatus 0, Open       -- with the approver, withdrawable
	  sent_back       docstatus 0, Rejected   -- the manager's reason applies
	  waiting_for_hr  docstatus 0, Approved   -- the legacy defect state P2-U1
	                                             step 4 reconciles. It never
	                                             consumed balance, so it must
	                                             never read as "Approved", and
	                                             the employee has no action.
	  approved        docstatus 1, Approved   -- submitted; balance consumed
	  rejected        docstatus 1, Rejected   -- the final no of P4-R4. HRMS's
	                                             own on_submit accepts this
	                                             status, and a submitted
	                                             application consumes no
	                                             balance and cannot be edited
	                                             or resent.
	  decided         docstatus 1, anything else
	  cancelled       docstatus 2, or status Cancelled
	"""
	docstatus = cint(docstatus)
	if docstatus == 2 or status == "Cancelled":
		return "cancelled"
	if docstatus == 1:
		if status == "Approved":
			return "approved"
		# P4-KTD4: docstatus 0 + Rejected is the send-back; docstatus 1 +
		# Rejected is terminal. Same status, two different answers, which is
		# exactly why the portal reasons about a state and not a status.
		return "rejected" if status == "Rejected" else "decided"
	if status == "Rejected":
		return "sent_back"
	if status == "Approved":
		return "waiting_for_hr"
	return "open"


def _may_withdraw(state):
	"""Withdrawal is a property of the lifecycle, not of the screen. Only a
	still-unsubmitted request of this employee's own can be removed; an
	approved one is a submitted document with a ledger entry behind it and
	the honest path is "Ask HR to cancel".

	The lifecycle is half the answer. The other half is `owner`: the only
	delete grant the Employee role has is the `if_owner` Custom DocPerm
	from patches/v1_0/apply_permission_deltas, and `if_owner` matches
	`Document.owner`, not `employee`. A leave HR filed in Desk *for* this
	employee is theirs to see and not theirs to delete, so callers pair
	this with the owner check rather than promising a button that throws.
	"""
	return state in ("open", "sent_back")


def _leave_reason(names, owners):
	"""The approver's reason for each sent-back leave, in one query.

	Scoped twice on purpose (P2-U5 scenario 1). Only a *rejected* record is
	asked about at all, and only comments written by somebody other than the
	employee who raised it are returned -- `act_on_approval` is the only path
	that can leave one, and it authorizes the approver or HR first, so what
	survives both filters is the manager's reason and nothing else.
	"""
	if not names:
		return {}

	latest = {}
	for row in frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": "Leave Application",
			"reference_name": ["in", names],
			"comment_type": "Comment",
		},
		fields=["reference_name", "content", "owner"],
		# Ascending, so the newest comment is written last and wins.
		order_by="creation asc",
	):
		if row.owner == owners.get(row.reference_name):
			continue
		text = _decision_reason_text(row.content)
		if text:
			latest[row.reference_name] = text
	return latest


def _leave_projection(row, approver_names, reasons):
	state = _leave_state(row.get("status"), row.get("docstatus"))
	approver = row.get("leave_approver")
	return {
		"name": row.get("name"),
		"leave_type": row.get("leave_type"),
		"from_date": str(row.get("from_date")) if row.get("from_date") else None,
		"to_date": str(row.get("to_date")) if row.get("to_date") else None,
		"total_leave_days": flt(row.get("total_leave_days")),
		"half_day": bool(cint(row.get("half_day"))),
		"half_day_date": str(row.get("half_day_date")) if row.get("half_day_date") else None,
		"description": row.get("description"),
		"status": row.get("status"),
		"docstatus": cint(row.get("docstatus")),
		"state": state,
		# P4-R5/R7: which queue this request is waiting in. "HR" means the
		# employee is told "Waiting for HR" and the manager has no action.
		"stage": row.get("helixhr_stage") or _LEAVE_STAGE_MANAGER,
		"can_withdraw": _may_withdraw(state) and row.get("owner") == frappe.session.user,
		"approver": approver,
		# `get_leave_applications` hands back a user id; the row needs the
		# person. The portal used to resolve these with its own
		# `frappe.client.get_list` against User -- a generic read of an
		# unrelated DocType, issued once per page load, to render one word.
		"approver_name": approver_names.get(approver),
		# Only ever populated for a sent-back record; see _leave_reason.
		"reason": reasons.get(row.get("name")),
		"posting_date": str(row.get("posting_date")) if row.get("posting_date") else None,
		"creation": str(row.get("creation")) if row.get("creation") else None,
		"modified": str(row.get("modified")) if row.get("modified") else None,
	}


_LEAVE_FIELDS = [
	"name",
	"leave_type",
	"from_date",
	"to_date",
	"total_leave_days",
	"half_day",
	"half_day_date",
	"description",
	"status",
	"docstatus",
	"leave_approver",
	"helixhr_stage",
	"posting_date",
	"owner",
	"creation",
	"modified",
]


def _approver_names(rows):
	ids = {row.get("leave_approver") for row in rows if row.get("leave_approver")}
	if not ids:
		return {}
	return {
		row.name: row.full_name
		for row in frappe.get_all(
			"User", filters={"name": ["in", list(ids)]}, fields=["name", "full_name"]
		)
	}


def _leave_balance_map(employee):
	"""`hrms.api.get_leave_balance_map`'s own shape, resolved for `employee`
	directly rather than through that call's built-in session scoping
	(P6-KTD5) -- so U3's person view reads the same number the employee's
	own screen would, for whichever employee is asked about, without a
	second derivation."""
	from hrms.hr.doctype.leave_application.leave_application import get_leave_details

	allocation = get_leave_details(employee, getdate())["leave_allocation"]
	return {
		leave_type: {
			"allocated_leaves": details.get("total_leaves"),
			"balance_leaves": details.get("remaining_leaves"),
		}
		for leave_type, details in allocation.items()
	}


def _leave_balances(employee):
	"""Allocated / used / left per leave type, in the shape the field block
	draws."""
	balances = []
	for leave_type, details in (_leave_balance_map(employee) or {}).items():
		allocated = flt(details.get("allocated_leaves"))
		left = flt(details.get("balance_leaves"))
		balances.append(
			{
				"leave_type": leave_type,
				"allocated": allocated,
				"left": left,
				"used": max(0.0, allocated - left),
			}
		)
	balances.sort(key=lambda entry: entry["leave_type"])
	return balances


@frappe.whitelist()
def get_my_leave(limit=None):
	"""Balances and a bounded page of this employee's own leave, with the
	approver's display name and the manager's reason already resolved
	(P2-R14, P2-R22).

	One request where the page used to make three -- balances, applications,
	and a generic User list to turn approver ids into names.
	"""
	employee = get_current_employee()
	limit = min(max(cint(limit) or _LEAVE_PAGE, 1), _LEAVE_MAX_PAGE)

	rows = frappe.get_all(
		"Leave Application",
		filters={"employee": employee},
		fields=_LEAVE_FIELDS,
		order_by="from_date desc",
		limit=limit,
	)
	total = frappe.db.count("Leave Application", {"employee": employee})

	rejected = [row.name for row in rows if row.status == "Rejected"]
	owners = {row.name: row.owner for row in rows}
	reasons = _leave_reason(rejected, owners)
	approver_names = _approver_names(rows)

	return {
		"balances": _leave_balances(employee),
		"applications": [_leave_projection(row, approver_names, reasons) for row in rows],
		"total": total,
		"limit": limit,
		"today": user_today(),
	}


@frappe.whitelist()
def get_my_leave_detail(name):
	"""One leave record, by name (P2-R12, KTD5).

	The list is bounded, so `/leave/<name>` has to be answerable on its own:
	an old record reached from a notification or a bookmark is not
	necessarily on the page the list returned.
	"""
	rate_limit_per_user("get_my_leave_detail")
	employee = get_current_employee()
	row = frappe.db.get_value(
		"Leave Application", name, [*_LEAVE_FIELDS, "employee"], as_dict=True
	)
	if not row:
		frappe.throw(_("That leave request no longer exists."), frappe.DoesNotExistError)
	if row.employee != employee:
		# Not "not found": the caller is authenticated and this is a refusal,
		# which the portal renders as its own state with no Retry (P2-R2).
		frappe.throw(_("That leave request isn't yours."), frappe.PermissionError)

	reasons = _leave_reason([name] if row.status == "Rejected" else [], {name: row.owner})
	return _leave_projection(row, _approver_names([row]), reasons)


@frappe.whitelist()
def get_leave_form_context():
	"""Everything the ask sheet needs before the employee types anything:
	the leave types they may take with the balance on each, and who the
	request will go to.

	Replaces `hrms.api.get_leave_types` + `hrms.api.get_leave_approval_details`
	as two separate browser calls, and -- more importantly -- means the
	browser never has to be told its own Employee id to ask the question.

	Neither HRMS call is used underneath any more. `get_leave_approval_details`
	checks Department *read* before falling back to the department's
	approver, which the Employee role does not have, so an employee whose
	approver is set only on their Department got a PermissionError and an
	empty sheet. `get_leave_types` lists every leave-without-pay type to
	everybody and nothing the policy granted 0 days; the list here is the
	allocated types plus the employee's own policy (`_policy_leave_types`).
	"""
	employee = get_current_employee()
	today = user_today()
	approver = get_employee_leave_approver(employee)
	balances = {entry["leave_type"]: entry for entry in _leave_balances(employee)}

	names = [*balances, *(t for t in _policy_leave_types(employee, today) if t not in balances)]
	# P4-R7: a type HR approves never reaches the manager, so the sheet says
	# so before the employee sends it. One read for the whole list.
	hr_approved = set(
		frappe.get_all(
			"Leave Type",
			filters={"name": ["in", names], "helixhr_hr_approves": 1},
			pluck="name",
		)
		if names
		else []
	)

	types = []
	for leave_type in names:
		entry = balances.get(leave_type)
		types.append(
			{
				"leave_type": leave_type,
				"hr_approves": leave_type in hr_approved,
				# None, not 0, for a type with no allocation (leave without
				# pay): "0 left" and "no balance to show" are different
				# sentences and the chip prints them differently.
				"left": entry["left"] if entry else None,
				"allocated": entry["allocated"] if entry else None,
			}
		)

	return {
		"today": today,
		"types": types,
		"approver": approver,
		"approver_name": frappe.db.get_value("User", approver, "full_name", cache=True)
		if approver
		else None,
		# The approver is a User; the photo belongs to their Employee.
		"approver_photo_url": _employee_photo_url(_employee_for_user(approver)[0]),
	}


def _policy_leave_types(employee, on_date):
	"""Every leave type in `employee`'s Leave Policy Assignment covering
	`on_date`, in policy order.

	HRMS writes no Leave Allocation for a policy row worth 0 days, nor ever
	for leave without pay, so the allocations alone miss a comp off not yet
	earned and a negative-allowed type such as WFH. Whether a request for one
	is accepted stays HRMS's decision on insert (`allow_negative`, balance).
	"""
	policies = frappe.get_all(
		"Leave Policy Assignment",
		filters={
			"employee": employee,
			"docstatus": 1,
			"effective_from": ["<=", on_date],
			"effective_to": [">=", on_date],
		},
		pluck="leave_policy",
	)
	if not policies:
		return []
	rows = frappe.get_all(
		"Leave Policy Detail",
		filters={"parenttype": "Leave Policy", "parent": ["in", policies]},
		pluck="leave_type",
		order_by="idx asc",
	)
	return list(dict.fromkeys(rows))


@frappe.whitelist()
def get_leave_day_count(leave_type, from_date, to_date, half_day=0, half_day_date=None):
	"""The day count HRMS itself will store, plus the non-working days it
	skipped and what the balance looks like afterwards (P2-U5 scenario 2).

	This calls HRMS's own `get_number_of_leave_days`, deliberately: a
	browser-side count would be a second implementation of the
	`include_holiday` rule, and the first time the two disagreed the
	employee would see one number and get another.

	It is an *advisory gate*: HRMS and the validate rule
	(`events.leave_application_validate`) decide. `blocked_reason` is the
	one sentence the browser shows beside a disabled Send when the request
	would start earlier than the backdated grace rule allows (R6), overdraw
	once pending requests are counted (R1) or exceed the type's
	consecutive-days limit; `earliest_start` is that grace date (None when
	the caller is exempt). A refusal on insert still wins over
	anything shown here.
	"""
	from hrms.hr.doctype.leave_application.leave_application import (
		get_leave_balance_on,
		get_number_of_leave_days,
	)

	rate_limit_per_user("get_leave_day_count")

	employee = get_current_employee()
	start, end = _as_date(from_date), _as_date(to_date)
	if end < start:
		frappe.throw(_("The end date must be on or after the start date."))
	if date_diff(end, start) + 1 > _LEAVE_MAX_SPAN_DAYS:
		frappe.throw(_("A leave request can't be longer than a year."))

	half_day = cint(half_day)
	if half_day:
		# The half-day date is the selected From date, here as well as in
		# apply_for_leave, so the preview cannot describe a different
		# request from the one that gets sent.
		half_day_date = str(start)

	days = flt(
		get_number_of_leave_days(employee, leave_type, start, end, half_day, half_day_date)
	)

	skipped = []
	if not frappe.db.get_value("Leave Type", leave_type, "include_holiday"):
		holidays = _holiday_dates(employee, start, end) or set()
		skipped = sorted(holidays)

	balance = flt(get_leave_balance_on(employee, leave_type, end))
	overdraw = leave_overdraw(employee, leave_type, start, end, days)
	pending = overdraw["pending"] if overdraw else 0.0
	max_continuous = cint(frappe.db.get_value("Leave Type", leave_type, "max_continuous_days_allowed"))
	earliest = backdated_leave_earliest(employee)
	blocked_reason = backdated_leave_reason(employee, start) or (overdraw["reason"] if overdraw else None)
	if not blocked_reason and max_continuous and days > max_continuous:
		blocked_reason = _("{0} allows at most {1} days in one request.").format(leave_type, max_continuous)
	return {
		"total_leave_days": days,
		"skipped": skipped,
		"skipped_label": _skipped_label(skipped),
		"balance": balance,
		"balance_after": balance - days,
		"pending": pending,
		"max_continuous": max_continuous or None,
		"earliest_start": str(earliest) if earliest else None,
		"blocked_reason": blocked_reason,
	}


def _skipped_label(skipped):
	"""One plain sentence about the non-working days inside the range, built
	here rather than in the browser: naming a weekday needs a locale-aware
	formatter and `lib/dates.js` deliberately has none (it renders dates, not
	day names)."""
	if not skipped:
		return None
	if len(skipped) <= 2:
		names = ", ".join(_as_date(day).strftime("%a") for day in skipped)
		return _("{0} skipped").format(names)
	return _("{0} non-working days skipped").format(len(skipped))


@frappe.whitelist(methods=["POST"])
def apply_for_leave(leave_type, from_date, to_date, half_day=0, description=None):
	"""Create this employee's leave request (P2-R27).

	Field-allow-listed and session-scoped: `employee` comes from the session,
	`leave_approver` from HRMS's own resolution, and `half_day_date` from
	`from_date` -- none of the three is a caller input, so none of them can be
	wrong or forged. `frappe.client.insert` with a browser-built document,
	which this replaces, offered all three as parameters.

	No approver means no document at all (P2-U5 scenario 4): HR Settings'
	`leave_approver_mandatory_in_leave_application` would refuse it anyway,
	but refusing here means the employee gets a sentence naming the next
	step instead of a validation error, and no draft is left behind.
	"""
	rate_limit_per_user("apply_for_leave")
	employee = get_current_employee()
	# Employee's own approver, else the Department's first -- without the
	# Department read check `get_leave_approval_details` makes first.
	approver = get_employee_leave_approver(employee)
	if not approver:
		frappe.throw(
			_("You don't have a leave approver yet, so this can't be sent. Ask HR to set one.")
		)

	half_day = cint(half_day)
	start = _as_date(from_date)
	# A half day is one day by definition; accepting the form's To date here
	# is what let a stale watcher submit "half day, 14th to 16th".
	end = start if half_day else _as_date(to_date)

	doc = frappe.get_doc(
		{
			"doctype": "Leave Application",
			"employee": employee,
			"leave_type": leave_type,
			"from_date": str(start),
			"to_date": str(end),
			"half_day": half_day,
			"half_day_date": str(start) if half_day else None,
			"description": description,
			"leave_approver": approver,
			"status": "Open",
		}
	)
	doc.insert()

	# P4-R7 / P4-KTD4. `helixhr_stage` is permlevel 1, so setting it on the
	# document above would be silently reset -- `reset_values_if_no_permlevel
	# _access` puts a new document's high-permlevel fields back to their
	# default for any session without the level, which every employee is. The
	# stage is written straight to the row instead, *after* the insert has
	# authorized and validated the application, and `db_set` still runs
	# `on_change`, so the "Waiting for HR" Notification fires (P4-KTD9).
	stage = _LEAVE_STAGE_MANAGER
	if frappe.db.get_value("Leave Type", leave_type, "helixhr_hr_approves"):
		stage = LEAVE_STAGE_HR
		doc.db_set("helixhr_stage", stage)
	return {
		"name": doc.name,
		"status": doc.status,
		"stage": stage,
		"total_leave_days": flt(doc.total_leave_days),
	}


@frappe.whitelist(methods=["POST"])
def withdraw_my_leave(name):
	"""Withdraw one still-unsubmitted leave request of the caller's own
	(P2-U5 step 4, P2-R27).

	The row is locked first, then the three things that make withdrawal
	legal are checked in order -- it is yours, it is unsubmitted, and it has
	not already been decided. An approved application is a *submitted*
	document since P2-U1: it has a Leave Ledger Entry behind it, deleting it
	would strand that entry, and the portal is not an HR administration
	tool. The path for that is an HR Request, which the screen offers by
	name.
	"""
	rate_limit_per_user("withdraw_my_leave")
	employee = get_current_employee()
	current = frappe.db.get_value(
		"Leave Application",
		name,
		["employee", "docstatus", "status", "owner"],
		as_dict=True,
		for_update=True,
	)
	if not current:
		frappe.throw(_("That leave request no longer exists."), frappe.DoesNotExistError)
	if current.employee != employee:
		frappe.throw(_("That leave request isn't yours."), frappe.PermissionError)

	state = _leave_state(current.status, current.docstatus)
	if state == "waiting_for_hr":
		# The P2-U1 legacy row. It is unsubmitted, so a delete would
		# technically succeed -- and would quietly destroy the record HR has
		# been asked to resolve in Desk.
		frappe.throw(_("This one is with HR. Ask HR to sort it out before withdrawing it."))
	if not _may_withdraw(state):
		frappe.throw(
			_("This leave has already been decided, so it can't be withdrawn. Ask HR to cancel it.")
		)
	if current.owner != frappe.session.user:
		# HR filed this one in Desk, so the `if_owner` delete grant does not
		# cover it and the delete below would throw a bare PermissionError.
		# Same sentence the screen shows, from the same rule (_may_withdraw).
		frappe.throw(_("HR raised this one for you. Ask HR to cancel it."))

	frappe.delete_doc("Leave Application", name)
	return {"name": name, "withdrawn": True}


# Attendance (U7, R16)

# One year, the widest span the screen can ask for (P2-U5 step 6).
_ATTENDANCE_MAX_DAYS = 366
# A day's worth of punches. Nobody badges fifty times; the cap is there so a
# malformed date can never turn the day sheet into an unbounded read.
_CHECKIN_LIMIT = 50


def _attendance_month_summary(employee, from_date, to_date):
	"""One range of attendance for `employee`: a status per day, the
	late/early flags Frappe records, and the four exceptions R16 asks for
	(absent, half day, late, missing).

	"Missing" is the careful one. No check-in device is configured yet, so a
	naive "working day with no Attendance record" would mark *every* past day
	as missing and drown the page in red -- worse than showing nothing. A day
	only counts as missing when it falls on or after the first Attendance
	record this employee has ever had: before that date the company simply
	was not recording, so nothing can be absent from it. That makes the whole
	feature dormant until real data arrives, and correct the moment it does,
	with no further change here.

	Deliberately carries no check-in *affordance* -- "may this employee punch
	right now" (`_checkin_state`, added by `get_my_attendance` below) is a
	self-service prompt about the session's own owner, not a fact about a
	person HR is reading (P6-KTD5).
	"""
	start, end = _as_date(from_date), _as_date(to_date)
	# Bounded at the API, not at the caller (P2-R22). The screen only ever
	# asks for one month, so anything else is a typo or a probe -- and both
	# are answered before a single row is read, rather than after the holiday
	# resolver has been asked for a decade of dates.
	if end < start:
		frappe.throw(_("Those dates are the wrong way round."))
	if date_diff(end, start) + 1 > _ATTENDANCE_MAX_DAYS:
		frappe.throw(_("Ask for a shorter date range -- a year at most."))

	records = frappe.get_all(
		"Attendance",
		filters={
			"employee": employee,
			"attendance_date": ["between", [str(start), str(end)]],
			"docstatus": 1,
		},
		fields=["attendance_date", "status", "late_entry", "early_exit", "attendance_request"],
	)

	days = {
		str(row.attendance_date): {
			"status": row.status,
			"late": bool(row.late_entry),
			"early": bool(row.early_exit),
			# P3-R19. A day an approved attendance request marked is a day
			# the employee already had corrected: it shows on the calendar
			# with its status, and it is never an exception again.
			"by_request": bool(row.attendance_request),
		}
		for row in records
	}

	tracking_since = frappe.db.get_value(
		"Attendance",
		{"employee": employee, "docstatus": 1},
		"attendance_date",
		order_by="attendance_date asc",
	)
	# Once per request (P2-U5 step 6). This used to be resolved twice --
	# once for `working_days_known` and again inside the missing-day walk --
	# and `get_holiday_dates_for_employee` is a holiday-list lookup plus a
	# date range read, not a cached value.
	holidays = _holiday_dates(employee, start, end)
	missing = _missing_attendance_days(employee, start, end, days, tracking_since, holidays)

	summary = {}
	for entry in days.values():
		summary[entry["status"]] = summary.get(entry["status"], 0) + 1

	return {
		"tracked": bool(tracking_since),
		"tracking_since": str(tracking_since) if tracking_since else None,
		# False when the employee has no resolvable holiday list, in which case
		# working days are unknowable and `missing` is deliberately empty
		# rather than guessed.
		"working_days_known": holidays is not None,
		"days": days,
		"missing": missing,
		"summary": summary,
		# P3-R19. Counted over the days a request did *not* mark: a
		# half-day Work From Home request writes a Half Day row, and
		# flagging it would send the employee back to HR about a day they
		# have already had fixed. `missing` cannot contain such a day
		# anyway -- a request-marked day has an Attendance row.
		"exceptions": {
			"absent": sum(
				1
				for entry in days.values()
				if entry["status"] == "Absent" and not entry["by_request"]
			),
			"half_day": sum(
				1
				for entry in days.values()
				if entry["status"] == "Half Day" and not entry["by_request"]
			),
			"late": sum(1 for entry in days.values() if entry["late"] and not entry["by_request"]),
			"missing": len(missing),
		},
	}


@frappe.whitelist()
def get_my_attendance(from_date, to_date):
	"""`_attendance_month_summary` for the logged-in employee, plus the Today
	strip's own affordance -- may this employee punch right now, and what the
	next punch is (P3-U4 step 1 / P3-R5, P3-R8)."""
	employee = get_current_employee()
	return {
		**_attendance_month_summary(employee, from_date, to_date),
		"checkin": _safe(
			lambda: _checkin_state(employee),
			"HelixHR check-in state failed",
			_checkin_unavailable(),
		),
	}


def _holiday_dates(employee, start, end):
	"""Holiday dates as a set of ISO strings, or None when the employee has no
	resolvable holiday list -- the caller must treat None as "cannot tell",
	never as "no holidays"."""
	try:
		from hrms.hr.utils import get_holiday_dates_for_employee

		return {str(d) for d in get_holiday_dates_for_employee(employee, str(start), str(end))}
	except Exception:
		return None


def _missing_attendance_days(employee, start, end, days, tracking_since, holidays):
	from frappe.utils import add_days

	if not tracking_since:
		return []
	if holidays is None:
		return []

	on_leave = _leave_days(employee, start, end)
	first = _as_date(tracking_since)
	today_date = _as_date(user_today())

	missing = []
	date = start
	while date <= end:
		iso = str(date)
		if (
			date >= first
			and date < today_date  # today is not late yet
			and iso not in days
			and iso not in holidays
			and iso not in on_leave
		):
			missing.append(iso)
		date = add_days(date, 1)
	return missing


# Check-in (P3-U4, P3-R5 to P3-R9)
#
# The punch is a server decision (P3-KTD3). The browser contributes exactly
# two things -- a location and which button the employee thinks they are
# pressing -- and the server decides the type, the time and whether a punch
# happens at all. HRMS owns the rest: it resolves the shift, refuses a punch
# outside a geofence and refuses a coordinate-less punch when HR Settings'
# geolocation tracking is on.

# Every portal punch is stamped with this, so a device punch and a portal
# punch are still distinguishable in Desk (P3-R7).
_PORTAL_DEVICE_ID = "HelixHR Portal"
# A second tap inside a minute is the same punch, not a check-out: a slow
# network, a double tap or a retry must not book two rows (P3-R7).
_PUNCH_DEBOUNCE_SECONDS = 60
# "Not set up" copy, used for the HR flag being off and for an employee with
# no shift at all -- from their side those are the same situation, and both
# are HR's to fix (P3-R8).
_CHECKIN_NOT_SET_UP = "Check-in isn't set up for you yet. Ask HR if you think it should be."


def _checkin_unavailable(reason=None, window=None, last=None):
	return {"enabled": False, "reason": reason or _(_CHECKIN_NOT_SET_UP), "window": window, "last": last}


def _mobile_checkin_allowed():
	return bool(cint(frappe.db.get_single_value("HR Settings", "allow_employee_checkin_from_mobile_app")))


def _shift_windows(employee, at):
	"""HRMS's own shift resolution for one instant: `(window_now, upcoming)`.

	`window_now` is the window `at` falls inside, grace periods included --
	the same call `EmployeeCheckin.fetch_shift` makes, so the portal offers a
	punch exactly when HRMS would attach one to a shift (P3-KTD5). A punch
	outside it is stored `offshift` and never becomes Attendance, which
	later reads as a missing day.

	`upcoming` is the next window that has not closed yet, which is what
	lets the strip say when check-in opens instead of claiming it is not set
	up. HRMS has no call for that: every one of its resolvers answers "which
	shift is this instant inside", and `get_employee_shift(..., "forward")`
	looks for an assignment starting *after* today, so an open-ended
	assignment that started last month answers nothing at all. So the
	upcoming window is built from the assignments HRMS itself lists for the
	day, using HRMS's own timings for each -- today first, then tomorrow,
	which is where the next window lives once today's has closed.
	"""
	from hrms.hr.doctype.shift_assignment.shift_assignment import (
		get_actual_start_end_datetime_of_shift,
		get_shift_details,
		get_shifts_for_date,
	)

	exact = get_actual_start_end_datetime_of_shift(employee, at, True) or None
	if exact:
		return exact, exact

	for moment in (at, add_to_date(at, days=1)):
		upcoming = None
		for assignment in get_shifts_for_date(employee, moment):
			details = get_shift_details(assignment.shift_type, moment)
			if not details or not details.get("actual_start") or not details.get("actual_end"):
				continue
			# The assignment has to cover the day the window falls on. This
			# is a looser reading than HRMS's own midnight-shift arithmetic,
			# which is right for a sentence about when check-in opens: the
			# punch itself is still gated by HRMS's resolution above.
			opens_on = getdate(details.actual_start)
			if opens_on < getdate(assignment.start_date):
				continue
			if assignment.end_date and opens_on > getdate(assignment.end_date):
				continue
			if details.actual_end < at:
				continue
			if upcoming is None or details.actual_start < upcoming.actual_start:
				upcoming = details
		if upcoming:
			return exact, upcoming
	return exact, None


def _window_bounds(shift):
	"""The `{start, end}` of a resolved HRMS shift, or None."""
	if not shift:
		return None
	return {"start": shift.actual_start, "end": shift.actual_end}


def _shift_window(employee, at):
	"""The window `at` falls inside, or None. The punch's own gate."""
	exact, _upcoming = _shift_windows(employee, at)
	return _window_bounds(exact)


def _last_punch_in_window(employee, start, end):
	"""The employee's newest punch inside one shift window.

	The window, not the calendar day: a night shift spans two dates and a
	traveller's local day is a third answer again (P3-AE4). Whatever HRMS
	would attach this punch to is what decides which punches count as "the
	shift so far".
	"""
	rows = frappe.get_all(
		"Employee Checkin",
		filters={"employee": employee, "time": ["between", [str(start), str(end)]]},
		fields=["name", "time", "log_type", "latitude", "longitude"],
		order_by="time desc, creation desc",
		limit=1,
	)
	return rows[0] if rows else None


def _next_log_type(last):
	"""No punch yet is a check-in; anything else alternates (P3-R7)."""
	if last and last.log_type == "IN":
		return "OUT"
	return "IN"


def _has_location(row):
	"""P3-R9. Whether this punch still carries coordinates at all.

	The sentinel is the *pair* (0, 0) -- what `tasks.ERASED` writes when the
	retention period expires, and the one reading `_punch_coordinates`
	refuses as input -- never either value on its own. Testing each
	coordinate for truth reported "no location" for a real punch on the
	equator or the prime meridian.
	"""
	return not (flt(row.get("latitude")) == 0 and flt(row.get("longitude")) == 0)


def _punch_projection(row, existing=False):
	return {
		"name": row.get("name"),
		"log_type": row.get("log_type"),
		"time": str(row.get("time")),
		"has_location": _has_location(row),
		"existing": existing,
	}


def _checkin_state(employee):
	"""P3-R5, P3-R8. What the Today strip renders: the next action if there
	is one, and one plain sentence if there is not."""
	if not _mobile_checkin_allowed():
		return _checkin_unavailable()

	now = now_datetime()
	exact, upcoming = _shift_windows(employee, now)
	window = _window_bounds(exact) or _window_bounds(upcoming)
	if not exact:
		if not upcoming:
			return _checkin_unavailable()
		return _checkin_unavailable(
			_("Check-in opens at {0}.").format(_when(upcoming.actual_start)), window=window
		)

	last = _last_punch_in_window(employee, window["start"], window["end"])
	return {
		"enabled": True,
		"reason": None,
		"window": {"start": str(window["start"]), "end": str(window["end"])},
		"last": _punch_last(last),
		"next_log_type": _next_log_type(last),
	}


def _punch_last(last):
	if not last:
		return None
	return {
		"log_type": last.log_type,
		"time": str(last.time),
		"has_location": _has_location(last),
	}


def _when(moment):
	""""08:45" for a window that opens today, "08:45 on 12 Sep" when it does
	not -- the sentence has to be true for a night shift and for a Monday
	read on a Saturday."""
	moment = get_datetime(moment)
	clock = moment.strftime("%H:%M")
	if getdate(moment) == getdate(user_today()):
		return clock
	return _("{0} on {1}").format(clock, frappe.utils.formatdate(str(getdate(moment)), "d MMM"))


def _punch_coordinates(latitude, longitude):
	"""P3-R7a / P3-AE5. Two floats that name a place on Earth, or a plain
	refusal. A browser that fails to fix a position sends nothing at all;
	`NaN`, an infinity and a 95th parallel come from a caller that is not
	the portal, and 0,0 is the null-island reading a broken sensor gives."""
	missing = _("Your location didn't come through, so nothing was recorded. Try again.")
	try:
		lat, lon = float(latitude), float(longitude)
	except (TypeError, ValueError):
		frappe.throw(missing)
	if not (math.isfinite(lat) and math.isfinite(lon)):
		frappe.throw(missing)
	if abs(lat) > 90 or abs(lon) > 180:
		frappe.throw(_("That location isn't a real place on the map, so nothing was recorded."))
	if lat == 0 and lon == 0:
		frappe.throw(missing)
	return lat, lon


@frappe.whitelist(methods=["POST"])
def punch_my_checkin(latitude, longitude, expected_log_type):
	"""One check-in or check-out for the logged-in employee, at server time,
	with the location the browser captured at the tap (P3-KTD3, P3-R6, P3-R7).

	`expected_log_type` is what the screen was offering, not an instruction:
	the server derives the type from the last punch in the window and refuses
	a mismatch, so a strip left open on a phone since this morning cannot
	book a second check-in. HRMS's own validation (geofence, strict log type,
	inactive employee) runs on insert and its messages are surfaced unchanged
	-- they are already plain sentences about a place and a distance.
	"""
	rate_limit_per_user("punch_my_checkin")
	employee = get_current_employee()

	expected = str(expected_log_type or "").strip().upper()
	if expected not in ("IN", "OUT"):
		frappe.throw(_("That's neither a check-in nor a check-out. Reload and try again."))
	latitude, longitude = _punch_coordinates(latitude, longitude)

	if not _mobile_checkin_allowed():
		frappe.throw(_(_CHECKIN_NOT_SET_UP))

	# Serialise the punches of one employee against each other, so two taps
	# that arrive together cannot both read "no punch yet" and both insert
	# (the same rule `submit_my_week` follows for a week's Timesheet).
	_lock_employee(employee)

	now = now_datetime()
	window = _shift_window(employee, now)
	if not window:
		frappe.throw(
			_("Check-in isn't open right now. Reload to see when it opens, or use Fix a day.")
		)

	last = _last_punch_in_window(employee, window["start"], window["end"])
	if (
		last
		and last.log_type == expected
		# The type has to match too: a double tap always asks for what it
		# already got, while a genuine check-out half a minute after the
		# check-in asks for the other one -- and returning the check-in for
		# it reported a punch that never happened.
		and abs(time_diff_in_seconds(now, last.time)) < _PUNCH_DEBOUNCE_SECONDS
	):
		# The same punch, tapped twice. Returning it (rather than refusing)
		# is what makes a retry after a timeout safe.
		return _punch_projection(last, existing=True)

	derived = _next_log_type(last)
	if derived != expected:
		frappe.throw(
			_("Your check-in has moved on since this screen loaded. Reload and try again.")
		)

	doc = frappe.get_doc(
		{
			"doctype": "Employee Checkin",
			"employee": employee,
			# Server time, never the caller's: the method takes no timestamp
			# at all, which is the whole reason it exists rather than
			# `add_log_based_on_employee_field` (P3-KTD3).
			"time": now,
			"log_type": derived,
			"device_id": _PORTAL_DEVICE_ID,
			"latitude": latitude,
			"longitude": longitude,
		}
	)
	# Role Employee has no `create` on Employee Checkin after P3-KTD13's
	# delta: this method is the create rule, exactly as `create_my_request`
	# is for HR Request.
	doc.insert(ignore_permissions=True)
	return _punch_projection(doc.as_dict())


@frappe.whitelist()
def get_my_checkins(date):
	"""The caller's own check-ins for one day, for the attendance day sheet.

	The page used to ask `frappe.client.get_list` for Employee Checkin with
	no employee filter at all and rely entirely on Frappe's permission layer
	to narrow it. That works today, and it is still the wrong shape: the
	scope is not stated anywhere the reader of the page can see it, and the
	bound is `limit_page_length: 0` (P2-R22, P2-R27).
	"""
	employee = get_current_employee()
	day = _as_date(date)
	rows = frappe.get_all(
		"Employee Checkin",
		filters={
			"employee": employee,
			"time": ["between", [f"{day} 00:00:00", f"{day} 23:59:59"]],
		},
		fields=["name", "time", "log_type", "latitude", "longitude"],
		order_by="time asc",
		limit=_CHECKIN_LIMIT,
	)
	# P3-R9: the day sheet draws a pin, so it needs to know *whether* the
	# punch has a location, not where it was. The coordinates stay on the
	# server (P3-KTD15 erases them there on a schedule); a payload that
	# carried them would put a location history in every browser cache.
	return [
		{
			"name": row.name,
			"time": row.time,
			"log_type": row.log_type,
			"has_location": _has_location(row),
		}
		for row in rows
	]


# Attendance requests -- "Fix a day" (P3-U5, P3-R12 to P3-R18)
#
# Every method here is the employee's own half of the two-step approval: the
# manager's half is `act_on_approval` (P3-U6) and HR's is Desk. The workflow
# fixture decides who may move the state; `helixhr.events` carries the rules
# Frappe does not enforce; these six carry the bounds, the allow-lists and the
# plain-words refusals.

# HRMS offers exactly two reasons (P3-KTD10); anything else is an HR Request.
_REQUEST_REASONS = ("Work From Home", "On Duty")
# A correction is a handful of days, and the preview walks every one of them
# through a holiday, leave and attendance lookup (P3-R25).
_REQUEST_MAX_SPAN_DAYS = 31
_REQUEST_EXPLANATION_MAX = 1000
_REQUEST_PAGE_SIZE = 20
_REQUEST_MAX_PAGE_SIZE = 100

# An existing Attendance row an approved request would rewrite in place, and
# never revert (P3-KTD14) -- so a request over one of these days is refused
# rather than previewed away. An `Absent` row is the opposite case: replacing
# the Absent that auto attendance wrote overnight is the whole feature.
_REQUEST_OVERWRITE_STATUSES = ("Present", "Half Day", "Work From Home", "On Leave")

_ATTENDANCE_REQUEST_FIELDS = [
	"name",
	"from_date",
	"to_date",
	"half_day",
	"half_day_date",
	"reason",
	"explanation",
	"shift",
	"workflow_state",
	"docstatus",
	"modified",
	"creation",
]


# The two outcomes an approver can reach that leave the employee something to
# read: one recoverable, one final (P4-KTD1). Both carry a reason.
_REQUEST_DECIDED_AGAINST = (REQUEST_SENT_BACK, REQUEST_REJECTED)


def _request_projection(row, reasons):
	state = row.get("workflow_state") or REQUEST_DRAFT
	return {
		"name": row.get("name"),
		"from_date": str(row.get("from_date")) if row.get("from_date") else None,
		"to_date": str(row.get("to_date")) if row.get("to_date") else None,
		"half_day": bool(cint(row.get("half_day"))),
		"half_day_date": str(row.get("half_day_date")) if row.get("half_day_date") else None,
		"reason": row.get("reason"),
		"explanation": row.get("explanation"),
		"shift": row.get("shift"),
		"workflow_state": state,
		"docstatus": cint(row.get("docstatus")),
		"can_withdraw": state in REQUEST_WITHDRAWABLE and cint(row.get("docstatus")) == 0,
		# Only ever populated for a sent-back request.
		"reason_sent_back": reasons.get(row.get("name")),
		"modified": str(row.get("modified")) if row.get("modified") else None,
		"creation": str(row.get("creation")) if row.get("creation") else None,
	}


@frappe.whitelist()
def get_my_attendance_requests(limit=None, start=0):
	"""A bounded page of this employee's own attendance requests, newest
	first, with the sent-back reason and the manager's name already resolved
	(P3-R17).

	Cancelled requests are excluded: an approved request that HR later
	cancelled is HR's record, not an item the employee can act on.
	"""
	employee = get_current_employee()
	limit = min(max(cint(limit) or _REQUEST_PAGE_SIZE, 1), _REQUEST_MAX_PAGE_SIZE)
	start = max(cint(start), 0)
	scope = {"employee": employee, "docstatus": ["<", 2]}

	rows = frappe.get_all(
		"Attendance Request",
		filters=scope,
		fields=_ATTENDANCE_REQUEST_FIELDS,
		order_by="from_date desc, creation desc",
		limit_start=start,
		limit_page_length=limit,
	)
	reasons = _rejection_comments(
		"Attendance Request",
		[row.name for row in rows if row.workflow_state in _REQUEST_DECIDED_AGAINST],
		employee,
	)

	return {
		"requests": [_request_projection(row, reasons) for row in rows],
		"total": frappe.db.count("Attendance Request", scope),
		"limit": limit,
		"start": start,
		"approver_name": _approver_name(employee),
		"reasons": list(_REQUEST_REASONS),
		"today": user_today(),
	}


@frappe.whitelist()
def get_my_attendance_request(name):
	"""One request, by name -- the list is bounded, so a request reached from
	a notification or a bookmark has to be answerable on its own."""
	rate_limit_per_user("get_my_attendance_request")
	employee = get_current_employee()
	row = frappe.db.get_value(
		"Attendance Request",
		name,
		[*_ATTENDANCE_REQUEST_FIELDS, "employee"],
		as_dict=True,
	)
	if not row:
		frappe.throw(_("That attendance request no longer exists."), frappe.DoesNotExistError)
	if row.employee != employee:
		frappe.throw(_("That attendance request isn't yours."), frappe.PermissionError)

	reasons = _rejection_comments(
		"Attendance Request",
		[name] if row.workflow_state in _REQUEST_DECIDED_AGAINST else [],
		employee,
	)
	detail = _request_projection(row, reasons)
	detail["approver_name"] = _approver_name(employee)
	return detail


def _request_range(from_date, to_date):
	start, end = _as_date(from_date), _as_date(to_date)
	if end < start:
		frappe.throw(_("Those dates are the wrong way round."))
	if date_diff(end, start) + 1 > _REQUEST_MAX_SPAN_DAYS:
		frappe.throw(_("Ask for a shorter date range -- a month at most."))
	return start, end


def _holiday_kinds(employee, start, end, cache=None):
	"""Which dates in the range are holidays, and which of those are the
	employee's weekly off -- two different sentences on the screen, and the
	same row in HRMS's answer (P3-KTD14).

	Resolved through the Holidays section's `_holiday_list_spans` (P3-U3), so a
	range that straddles a Holiday List Assignment change reads each half from
	the list actually in force over it. Resolving the list once, as of today --
	which is what this did before P3-U9 -- returned the wrong list for exactly
	that range, and `_holiday_list_spans` is the function that exists to handle
	it.

	The name returned is the list in force at `start`, which is what the
	preview's footnote says; the map covers the whole range whichever list each
	day came from. `(None, None)` means no list resolves at all -- the "cannot
	tell yet, ask HR" state, never a range with no holidays in it (P3-R11).

	`cache` is optional and is passed straight to `_holiday_dates_by_span`.
	"""
	spans = _holiday_list_spans(employee, _as_date(start), _as_date(end))
	if not spans:
		return None, None
	return spans[0]["holiday_list"], _holiday_dates_by_span(spans, cache)


def _holiday_dates_by_span(spans, cache=None):
	"""Every Holiday row across these spans, as `{date: "holiday" |
	"weekly_off"}`.

	`cache` (when given) is keyed by the resolved span, so a team on one holiday
	list costs one Holiday query however many people are in it.

	The rows are read with `ignore_permissions`, deliberately, for the reason
	the section note above `get_my_holidays` gives: role Employee has no read on
	Holiday List at all (P2-R26), and the list names here are server-derived
	from the employee, so there is nothing a caller can steer (P3-KTD1).
	"""
	kinds = {}
	for span in spans:
		key = (span["holiday_list"], str(span["from_date"]), str(span["to_date"]))
		found = cache.get(key) if cache is not None else None
		if found is None:
			found = {
				str(getdate(row.holiday_date)): ("weekly_off" if cint(row.weekly_off) else "holiday")
				for row in frappe.get_all(
					"Holiday",
					filters={
						"parent": span["holiday_list"],
						"parenttype": "Holiday List",
						"holiday_date": ["between", [str(span["from_date"]), str(span["to_date"])]],
					},
					fields=["holiday_date", "weekly_off"],
					ignore_permissions=True,
				)
			}
			if cache is not None:
				cache[key] = found
		kinds.update(found)
	return kinds


@frappe.whitelist()
def get_attendance_request_preview(
	from_date, to_date, half_day=0, half_day_date=None, reason="Work From Home"
):
	"""What sending this request would actually do, day by day (P3-R13).

	Three buckets, per P3-KTD14: `mark` (no attendance yet, or an Absent row
	the request replaces), `skipped` (a holiday, a weekly off, or a day the
	employee is already on approved leave) and `overwrite` (a day that
	already carries real attendance). Any overwrite day refuses the send,
	because HRMS rewrites such a row in place at HR's submit and cancels it
	outright on cancel -- so a request over one can erase attendance rather
	than revert it. The HR Request is the pointer for that case.

	`known: false` is the Attendance page's "cannot tell yet, ask HR" shape:
	with no resolvable holiday list, HRMS's own preview raises instead of
	answering, and the screen has to disable Send rather than guess.
	"""
	rate_limit_per_user("get_attendance_request_preview")
	employee = get_current_employee()
	start, end = _request_range(from_date, to_date)
	reason = _assert_request_reason(reason)
	return _attendance_request_preview(
		employee, start, end, half_day=half_day, half_day_date=half_day_date, reason=reason
	)


def _attendance_request_preview(
	employee, start, end, half_day=0, half_day_date=None, reason="Work From Home"
):
	"""The preview itself, on an already-resolved employee and an
	already-validated range (P3-U9).

	Separate from the whitelisted method so that `send_my_attendance_request`,
	which re-derives the preview against the stored record, does not spend the
	caller's preview rate-limit budget on the send.
	"""
	list_name, holidays = _holiday_kinds(employee, start, end)
	total_days = date_diff(end, start) + 1
	if holidays is None:
		return {
			"known": False,
			"holiday_list": None,
			"total_days": total_days,
			"mark": 0,
			"replaces_absent": 0,
			"overwrite": 0,
			"skipped": {"holiday": 0, "weekly_off": 0, "on_leave": 0},
			"days": [],
			"can_send": False,
		}

	warnings = _request_warnings(
		employee, start, end, half_day=half_day, half_day_date=half_day_date, reason=reason
	)
	existing = _attendance_status_by_date([employee], start, end)

	days = []
	counts = {"mark": 0, "replaces_absent": 0, "overwrite": 0}
	skipped = {"holiday": 0, "weekly_off": 0, "on_leave": 0}
	date = start
	while date <= end:
		iso = str(date)
		warning = warnings.get(iso) or {}
		if iso in holidays:
			kind = holidays[iso]
			skipped[kind] += 1
			days.append({"date": iso, "bucket": "skipped", "reason": kind})
		elif warning.get("reason") == "On Leave":
			skipped["on_leave"] += 1
			days.append({"date": iso, "bucket": "skipped", "reason": "on_leave"})
		else:
			status = existing.get((employee, iso))
			if status in _REQUEST_OVERWRITE_STATUSES:
				counts["overwrite"] += 1
				days.append({"date": iso, "bucket": "overwrite", "reason": status})
			else:
				counts["mark"] += 1
				if status:
					counts["replaces_absent"] += 1
				days.append({"date": iso, "bucket": "mark", "reason": status})
		date = add_days(date, 1)

	return {
		"known": True,
		"holiday_list": list_name,
		"total_days": total_days,
		**counts,
		"skipped": skipped,
		"days": days,
		"can_send": counts["mark"] >= 1 and counts["overwrite"] == 0,
	}


def _request_warnings(employee, start, end, half_day=0, half_day_date=None, reason="Work From Home"):
	"""HRMS's own day-by-day warnings, asked of an unsaved request.

	Reusing `get_attendance_warnings` rather than reimplementing it keeps the
	approved-leave rule (including its half-day nuance) in one place -- HRMS's.
	Only its Holiday and On Leave answers are used; the existing-row question
	is answered by `_attendance_status_by_date`, because HRMS's is shift-scoped
	(P3-KTD14).
	"""
	doc = frappe.new_doc("Attendance Request")
	doc.employee = employee
	doc.company = frappe.db.get_value("Employee", employee, "company")
	doc.from_date = str(start)
	doc.to_date = str(end)
	doc.half_day = cint(half_day)
	doc.half_day_date = str(half_day_date) if half_day_date else None
	doc.reason = reason
	return {str(warning["date"]): warning for warning in doc.get_attendance_warnings()}


def _assert_request_reason(reason):
	if reason not in _REQUEST_REASONS:
		frappe.throw(
			_("Pick Work From Home or On Duty. Anything else is a request to HR instead.")
		)
	return reason


def _request_shift(employee, from_date):
	"""The shift an approved request writes onto the Attendance rows.

	HRMS fills `shift` from a Shift Assignment that fully covers the range;
	an open-ended assignment leaves it empty, so this falls back to the shift
	HRMS resolves for the first day, default shift included (P3-KTD14).
	"""
	from hrms.hr.doctype.shift_assignment.shift_assignment import get_employee_shift

	details = get_employee_shift(employee, get_datetime(f"{from_date} 12:00:00"), True) or {}
	shift_type = details.get("shift_type")
	return shift_type.name if shift_type else None


@frappe.whitelist(methods=["POST"])
def create_my_attendance_request(
	from_date, to_date, reason, explanation=None, half_day=0, half_day_date=None
):
	"""Create this employee's own attendance request as a Draft (P3-R12).

	Field-allow-listed and session-scoped: `employee` and `company` come from
	the session and `half_day_date` from `from_date` for a one-day range, so
	none of the three is a caller input. Nothing is sent here -- the employee
	sees the preview first, and `send_my_attendance_request` is the step that
	moves it to the manager.
	"""
	rate_limit_per_user("create_my_attendance_request")
	employee = get_current_employee()
	start, end = _request_range(from_date, to_date)
	reason = _assert_request_reason(reason)

	explanation = (explanation or "").strip() or None
	if explanation and len(explanation) > _REQUEST_EXPLANATION_MAX:
		frappe.throw(
			_("Keep the explanation under {0} characters.").format(_REQUEST_EXPLANATION_MAX)
		)

	half_day = cint(half_day)
	if half_day:
		if start == end:
			# A half day on a one-day range is that day, always -- accepting
			# the form's value here is how "half day, 14th, on the 20th"
			# would get in.
			half_day_date = str(start)
		elif not half_day_date or not (start <= _as_date(half_day_date) <= end):
			frappe.throw(_("Pick which day of the range is the half day."))
		else:
			half_day_date = str(_as_date(half_day_date))
	else:
		half_day_date = None

	doc = frappe.get_doc(
		{
			"doctype": "Attendance Request",
			"employee": employee,
			"company": frappe.db.get_value("Employee", employee, "company"),
			"from_date": str(start),
			"to_date": str(end),
			"half_day": half_day,
			"half_day_date": half_day_date,
			"reason": reason,
			"explanation": explanation,
			"shift": _request_shift(employee, start),
		}
	)
	doc.insert()
	return _request_projection(doc.as_dict(), {})


@frappe.whitelist(methods=["POST"])
def send_my_attendance_request(name, expected_modified=None):
	"""Send one Draft request to the manager (P3-R13, P3-R14, P3-R15).

	The preview is re-derived here rather than trusted from the screen: a
	holiday list, an approved leave or an attendance row can all have landed
	between rendering the sheet and tapping Send, and the refusals are the
	same ones the sheet showed.
	"""
	from frappe.model.workflow import apply_workflow

	rate_limit_per_user("send_my_attendance_request")
	employee = get_current_employee()
	current = frappe.db.get_value(
		"Attendance Request",
		name,
		["employee", "docstatus", "workflow_state", "modified", "from_date", "to_date", "half_day", "half_day_date", "reason"],
		as_dict=True,
		for_update=True,
	)
	if not current:
		frappe.throw(_("That attendance request no longer exists."), frappe.DoesNotExistError)
	if current.employee != employee:
		frappe.throw(_("That attendance request isn't yours."), frappe.PermissionError)
	if (current.workflow_state or REQUEST_DRAFT) != REQUEST_DRAFT:
		frappe.throw(_("This one has already been sent. Reload to see where it is."))
	_assert_expected_state(expected_modified, current.modified)

	# The plain preview, not the whitelisted method: a Send is not a preview
	# and must not consume the caller's preview budget (P3-U9). The stored
	# range and reason are still validated, exactly as the method validates a
	# caller's.
	preview = _attendance_request_preview(
		employee,
		*_request_range(current.from_date, current.to_date),
		half_day=current.half_day,
		half_day_date=current.half_day_date,
		reason=_assert_request_reason(current.reason),
	)
	if not preview["known"]:
		frappe.throw(
			_("We can't tell which of those days are working days yet. Ask HR about your holiday list.")
		)
	if preview["overwrite"]:
		frappe.throw(
			_(
				"Some of those days already have attendance, so this can't fix them. "
				"Raise a request to HR for those days instead."
			)
		)
	if not preview["mark"]:
		frappe.throw(_("None of those days would change. Pick different dates."))

	doc = frappe.get_doc("Attendance Request", name)
	apply_workflow(doc, "Submit")
	doc.reload()
	return {
		"name": doc.name,
		"workflow_state": doc.workflow_state,
		"modified": str(doc.modified),
	}


@frappe.whitelist(methods=["POST"])
def withdraw_my_attendance_request(name):
	"""Remove one of the caller's own requests while it is still theirs to
	remove -- Draft, with the manager, sent back, or rejected (P3-R17,
	P4-KTD3).

	Rejected is on that list because a terminal row at docstatus 0 would
	block the same dates for ever (HRMS refuses an overlap below docstatus
	2, and a Workflow cannot reach 2 from 0). The portal words it "Remove",
	not "Withdraw": there is nothing left to withdraw from, and the
	approver's reason survives on the Deleted Document snapshot.

	Once it has reached HR the honest path is HR, and `events
	.attendance_request_on_trash` refuses the same states through every
	other route.
	"""
	rate_limit_per_user("withdraw_my_attendance_request")
	employee = get_current_employee()
	current = frappe.db.get_value(
		"Attendance Request",
		name,
		["employee", "docstatus", "workflow_state"],
		as_dict=True,
		for_update=True,
	)
	if not current:
		frappe.throw(_("That attendance request no longer exists."), frappe.DoesNotExistError)
	if current.employee != employee:
		frappe.throw(_("That attendance request isn't yours."), frappe.PermissionError)

	state = current.workflow_state or REQUEST_DRAFT
	if cint(current.docstatus) != 0 or state not in REQUEST_WITHDRAWABLE:
		if state == REQUEST_APPROVED:
			frappe.throw(_("This one already counts. Ask HR to cancel it."))
		frappe.throw(_("This one is with HR now. Ask HR to sort it out."))

	frappe.delete_doc("Attendance Request", name)
	return {"name": name, "withdrawn": True}


# Payslips (P3-U2, P3-R1 to P3-R4, P3-KTD1, P3-KTD2)

# Bounded like the leave list (P3-R25): payroll is monthly, so a long-serving
# employee has a few hundred rows and the screen shows one year at a time.
_PAYSLIP_PAGE = 20
_PAYSLIP_MAX_PAGE = 200

# The explicit allow-list KTD1 asks for. Role Employee has `read` and `print`
# on Salary Slip and nothing else -- no `report` -- so the generic list and
# report views stay refused and this projection is the only way in (P3-R3).
_PAYSLIP_FIELDS = [
	"name",
	"start_date",
	"end_date",
	"posting_date",
	"gross_pay",
	"total_deduction",
	"net_pay",
	"rounded_total",
	"currency",
	"status",
	"amended_from",
	"payroll_frequency",
]

# One sentence for a slip that is missing and for a slip that belongs to
# somebody else, deliberately unlike `get_my_leave_detail`, which answers
# "isn't yours" for a foreign record. A Salary Slip's name embeds the
# employee id (`Sal Slip/<employee>/#####`, SalarySlip.default_series), so
# telling the two apart is an existence oracle *about another employee*:
# the caller already knows whose id they put in the name, and a distinct
# refusal would confirm the slip exists. Leave names are numeric and carry
# no such fact, which is why the two methods differ (P3-R3).
_PAYSLIP_NOT_FOUND = "That payslip isn't here."

# What Frappe falls back to when a site has not chosen a default print
# format for Salary Slip. HRMS ships this one and preflight has no business
# forcing a choice on a site (P3-KTD2).
_PAYSLIP_PRINT_FORMAT = "Salary Slip Standard"


def _payslip_projection(row):
	"""One row, in the words the page renders.

	Money stays a number per row with its own `currency` beside it: the
	amounts of two slips in two currencies are never comparable, so nothing
	here or on the page ever adds them (P3-R1, P3-U2 step 5).
	"""
	status = row.get("status")
	return {
		"name": row.get("name"),
		"start_date": str(row.get("start_date")) if row.get("start_date") else None,
		"end_date": str(row.get("end_date")) if row.get("end_date") else None,
		"posting_date": str(row.get("posting_date")) if row.get("posting_date") else None,
		"gross_pay": flt(row.get("gross_pay")),
		"total_deduction": flt(row.get("total_deduction")),
		# `rounded_total` is what payroll actually pays out, unless the site
		# turned rounding off, in which case HRMS leaves it at zero.
		"net_pay": flt(row.get("rounded_total")) or flt(row.get("net_pay")),
		"currency": row.get("currency"),
		"status": status,
		# P3-R4. Withheld is listed and named, never silently hidden, and it
		# has no PDF -- the figures on a withheld slip are not what anybody
		# was paid.
		"withheld": status == "Withheld",
		"can_download": status != "Withheld",
		# An amended slip is the correction of an earlier one. The employee
		# needs the word, not the superseded slip's id.
		"revised": bool(row.get("amended_from")),
		"payroll_frequency": row.get("payroll_frequency"),
	}


def _payslip_years(employee):
	"""Every year this employee has a submitted slip in, newest first.

	Derived from the first and last slip rather than by reading every row's
	date: payroll is monthly and continuous, so the span is the answer, and
	a year inside it with no slip filters to a list the page renders as its
	own empty state instead of an error (P3-R25 keeps this bounded at two
	cheap reads).
	"""
	scope = {"employee": employee, "docstatus": 1}
	newest = frappe.db.get_value("Salary Slip", scope, "end_date", order_by="end_date desc")
	oldest = frappe.db.get_value("Salary Slip", scope, "end_date", order_by="end_date asc")
	if not newest:
		return []
	return list(range(getdate(newest).year, getdate(oldest).year - 1, -1))


@frappe.whitelist()
def get_my_payslips(year=None, start=0, limit=None):
	"""A bounded page of this employee's own submitted payslips, newest
	period first, with the years they can filter by (P3-R1).

	Only `docstatus == 1`: a draft slip is payroll's work in progress and a
	cancelled one is a slip that was withdrawn, so neither is a payslip the
	employee has (P3-R4).
	"""
	employee = get_current_employee()
	limit = min(max(cint(limit) or _PAYSLIP_PAGE, 1), _PAYSLIP_MAX_PAGE)
	start = max(cint(start), 0)

	scope = {"employee": employee, "docstatus": 1}
	if year:
		year = cint(year)
		scope["end_date"] = ["between", [f"{year}-01-01", f"{year}-12-31"]]

	rows = frappe.get_all(
		"Salary Slip",
		filters=scope,
		fields=_PAYSLIP_FIELDS,
		order_by="end_date desc",
		limit_start=start,
		limit_page_length=limit,
	)

	return {
		"payslips": [_payslip_projection(row) for row in rows],
		"total": frappe.db.count("Salary Slip", scope),
		"limit": limit,
		"start": start,
		"year": year or None,
		"years": _payslip_years(employee),
	}


def _my_payslip(name, employee):
	"""The row behind `name` once it is established that it is this
	employee's own submitted slip, or one uniform refusal (see
	`_PAYSLIP_NOT_FOUND`)."""
	row = frappe.db.get_value(
		"Salary Slip", name, [*_PAYSLIP_FIELDS, "employee", "docstatus"], as_dict=True
	)
	if not row or row.employee != employee or cint(row.docstatus) != 1:
		frappe.throw(_(_PAYSLIP_NOT_FOUND), frappe.DoesNotExistError)
	return row


@frappe.whitelist()
def get_my_payslip(name):
	"""One payslip with its breakdown (P3-R2).

	The list is bounded, so `/payslips/<name>` has to be answerable on its
	own -- a slip reached from a bookmark is not necessarily on the page the
	list returned.
	"""
	employee = get_current_employee()
	row = _my_payslip(name, employee)

	# The child rows, from the document: `salary_component` and `amount`
	# only. Everything else on an earning row (the formula, the account it
	# posts to, the year-to-date columns) is payroll's working, not the
	# employee's payslip.
	doc = frappe.get_doc("Salary Slip", name)
	detail = _payslip_projection(row)
	detail.update(
		{
			"payment_days": flt(doc.payment_days),
			"total_working_days": flt(doc.total_working_days),
			"leave_without_pay": flt(doc.leave_without_pay),
			"earnings": [
				{"salary_component": entry.salary_component, "amount": flt(entry.amount)}
				for entry in doc.earnings
			],
			"deductions": [
				{"salary_component": entry.salary_component, "amount": flt(entry.amount)}
				for entry in doc.deductions
			],
		}
	)
	return detail


@frappe.whitelist(methods=["GET"])
# PDF rendering spawns wkhtmltopdf, which is CPU-bound: a handful of
# concurrent downloads can take a web worker each and stall every other
# request on the site. Frappe v16 ships the primitive for exactly this
# (`frappe.concurrent_limit`, a Redis semaphore across workers, 503 when the
# wait runs out) and its default limit is derived from the site's own worker
# count, so no number is invented here (P3-U2 step 3).
@frappe.concurrent_limit()
def download_my_payslip(name):
	"""This employee's own submitted payslip as a PDF attachment, in the
	print format the site chose for Salary Slip (P3-R2, P3-KTD2).

	A GET, and a real navigation from the page rather than a fetch: that is
	what lets a phone browser save or open the file itself.
	"""
	rate_limit_per_user("download_my_payslip")
	employee = get_current_employee()
	row = _my_payslip(name, employee)
	if row.status == "Withheld":
		# Ownership is already established, so this one says what is wrong.
		frappe.throw(_("This payslip is on hold. Ask HR about it."))

	download_pdf(
		"Salary Slip",
		name,
		format=frappe.get_meta("Salary Slip").default_print_format or _PAYSLIP_PRINT_FORMAT,
	)

	# `frappe.utils.response.as_pdf` writes `Content-Disposition: inline`,
	# which renders the payslip in the tab instead of saving it, and no
	# cache directive at all. `frappe.local.response_headers` is merged over
	# the response's own headers in `frappe.app.application`, so this is the
	# supported way to correct both after the fact. The filename is the
	# period, because "Sal Slip-HR-EMP-00001-00003.pdf" is not a name anybody
	# wants in their downloads folder.
	period = str(row.end_date or row.start_date or "")[:7]
	frappe.local.response_headers["Content-Disposition"] = (
		f"attachment; filename*=UTF-8''{quote(f'Payslip {period}.pdf')}"
	)
	frappe.local.response_headers["Cache-Control"] = "no-store"


# Timesheets (U8, KTD7, KTD10, KTD11)

# A week is written whole: every save below replaces the week's rows
# outright. The cap is not a policy about how much anybody may work, it is
# a bound on what one request may ask the database to write (P2-R22) --
# seven days times a plausible project list does not come near it, and a
# malformed or hostile payload stops at it instead of at the row limit of
# a child table.
_MAX_WEEK_ROWS = 100

# What a full working week looks like, for the "30 of 40 hours" reading on
# the week spine and the bar on Past weeks. Not a rule the server enforces
# -- nothing in HRMS carries a contracted weekly figure for an employee --
# so it is context, never validation.
FULL_WEEK_HOURS = 40


@frappe.whitelist()
def get_my_week(week_start=None):
	"""The one Timesheet for the Monday..Sunday week containing
	`week_start` (any date in that week; defaults to today), or None if
	the employee hasn't started one yet (KTD10: one week is one
	Timesheet -- the newest non-cancelled one for that Monday)."""
	employee = get_current_employee()
	monday, sunday = get_week_bounds(week_start or user_today())

	current = _week_timesheet(employee, monday, ("name",), sunday)

	timesheet = None
	if current:
		doc = frappe.get_doc("Timesheet", current.name)
		timesheet = {
			"name": doc.name,
			"workflow_state": doc.workflow_state,
			"total_hours": doc.total_hours,
			"docstatus": doc.docstatus,
			# The concurrency token submit_my_week checks (P2-R25, P2-R27).
			# The screen sends back the value it was rendered from; a week
			# that moved on in another tab, or a second tap of Submit, no
			# longer matches it.
			"modified": str(doc.modified),
			# Resolved here rather than by the page, which used to read it with
			# `frappe.client.get_list` on Comment -- a doctype the Employee Self
			# Service role cannot read, so that call 403'd and the employee was
			# told their week was sent back without ever being told why. The
			# e2e only asserted the "Sent back" label, so it never caught it.
			"rejection_comment": _last_rejection_comment(doc.name)
			if doc.workflow_state == TIMESHEET_SENT_BACK
			else None,
			"rows": [
				{
					"project": row.project,
					"task": row.task,
					"hours": row.hours,
					"note": row.description,
					"date": _row_date(row),
				}
				for row in doc.time_logs
			],
		}

	# Plan 2026-10-04-003 U3 (R8, R9): the week's open change request, and
	# -- on an approved week -- whether it can be changed at all, with the
	# reason it cannot. The screen shows the why *before* the comment box,
	# so an employee never writes a request the server would refuse.
	# R12: with no open request, a declined one stays visible with its
	# reason -- the employee reads the no where the week lives, not only in
	# the email.
	change = _open_week_change(current.name) if current else None
	declined_change = None
	if current and not change:
		declined_change = frappe.db.get_value(
			"HelixHR Timesheet Change",
			{"timesheet": current.name, "status": "Declined"},
			["name", "comment", "decision_note"],
			as_dict=True,
			order_by="creation desc",
		)
	changeable = None
	if current and timesheet and timesheet["workflow_state"] == "Approved":
		problem = week_change_problem(current.name)
		changeable = {"ok": problem is None, "reason": problem}

	return {
		"week_start": str(monday),
		"week_end": str(sunday),
		# Who this week goes to when it is sent. The desktop grid names them
		# beside Submit, and an employee with nobody named is told before
		# they fill a week in rather than by a refusal afterwards.
		"approver_name": _approver_name(employee),
		"full_week_hours": FULL_WEEK_HOURS,
		"timesheet": timesheet,
		"change": {
			"name": change.name,
			"comment": change.comment,
			"status": change.status,
			"approver_user": change.approver_user,
		}
		if change
		else None,
		"declined_change": {
			"name": declined_change.name,
			"comment": declined_change.comment,
			"decision_note": declined_change.decision_note,
		}
		if declined_change
		else None,
		"changeable": changeable,
	}


def _row_date(row):
	return str(get_datetime(row.from_time).date()) if row.from_time else None


def _approver_name(employee):
	"""The manager's display name -- two hops, because `reports_to` is an
	Employee id."""
	reports_to = frappe.db.get_value("Employee", employee, "reports_to")
	return frappe.db.get_value("Employee", reports_to, "employee_name") if reports_to else None


@frappe.whitelist()
def get_my_timesheet_history(limit=12, start=0):
	"""Past weeks, newest first, one bounded page at a time (P2-R22).

	The page used to ask `frappe.client.get_list` for `limit_page_length:
	0` -- every week the employee had ever filed, to render a dozen. The
	manager's reason for each sent-back week comes back with the page in
	one Comment query rather than one per row, and each row carries the
	**Monday** of its week: ERPNext recomputes `start_date` from the
	earliest time log, so a week whose Monday is empty starts on a
	Tuesday, and the route parameter is always the Monday (KTD10).
	"""
	employee = get_current_employee()
	limit = min(max(cint(limit) or 12, 1), 52)
	start = max(cint(start), 0)
	scope = {"employee": employee, "docstatus": ["!=", 2]}

	rows = frappe.get_all(
		"Timesheet",
		filters=scope,
		fields=["name", "start_date", "end_date", "total_hours", "workflow_state"],
		order_by="start_date desc",
		limit_start=start,
		limit_page_length=limit,
	)
	reasons = _rejection_comments(
		"Timesheet",
		[row.name for row in rows if row.workflow_state == TIMESHEET_SENT_BACK],
		employee,
	)
	# Plan 2026-10-04-003 U3 (R8): the open change request rides the same
	# page -- one query for the page, so the history can show it and offer
	# the withdraw without a second call per row.
	changes = {}
	# Plan 2026-10-05-001 U4: keep the rows, not their names -- the loop
	# reads `.timesheet` and `.comment`.
	open_changes = frappe.get_all(
		"HelixHR Timesheet Change",
		filters={"employee": employee, "status": "Open", "timesheet": ["in", [r.name for r in rows] or [""]]},
		fields=["name", "timesheet", "comment"],
	)
	for change in open_changes:
		changes[change.timesheet] = {"name": change.name, "comment": change.comment}

	weeks = []
	for row in rows:
		monday, sunday = get_week_bounds(row.start_date)
		weeks.append(
			{
				"name": row.name,
				"week_start": str(monday),
				"week_end": str(sunday),
				"total_hours": row.total_hours,
				"workflow_state": row.workflow_state,
				"rejection_comment": reasons.get(row.name),
				"open_change": changes.get(row.name),
			}
		)

	return {
		"weeks": weeks,
		"total": frappe.db.count("Timesheet", scope),
		"full_week_hours": FULL_WEEK_HOURS,
	}


_MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_MONTH_UNSUBMITTED = (None, "Draft", TIMESHEET_SENT_BACK)


@frappe.whitelist()
def get_my_month(month=None):
	"""Every Monday..Sunday week that overlaps `month` (YYYY-MM; defaults to
	this month), for the Timesheet month overview (plan 2026-10-05-001 U5).

	A week with no Timesheet still comes back, as `state` None ("Not
	started"). The week's Timesheet is `_week_timesheet`'s rule -- newest
	non-cancelled one whose `start_date` falls inside the week -- applied
	to one query for the whole span. Expected hours come from the same
	working-days index the Team tab reads (KTD4), clamped to the
	employee's joining..relieving span: None when HR Settings has no
	standard hours ("not measured"), 0 when no working day remains.

	`missing`: the week is over, is not yet sent (Not started, Draft, Sent
	back), and had at least one working day -- so it holds whether or not
	expected hours are measured.
	"""
	if month in (None, ""):
		month = str(getdate(user_today()))[:7]
	if not isinstance(month, str) or not _MONTH_PATTERN.match(month):
		frappe.throw(_("Month must look like YYYY-MM."), frappe.ValidationError)

	employee = get_current_employee()
	first = getdate(f"{month}-01")
	last = get_last_day(first)
	mondays = []
	monday = get_week_bounds(first)[0]
	while monday <= last:
		mondays.append(monday)
		monday = add_days(monday, 7)
	span_start, span_end = mondays[0], add_days(mondays[-1], 6)

	joined, relieved = frappe.db.get_value(
		"Employee", employee, ["date_of_joining", "relieving_date"]
	)
	joined = getdate(joined) if joined else None
	relieved = getdate(relieved) if relieved else None

	sheets = {}
	for row in frappe.get_all(
		"Timesheet",
		filters={
			"employee": employee,
			"start_date": ["between", [str(span_start), str(span_end)]],
			"docstatus": ["!=", 2],
		},
		fields=["name", "start_date", "workflow_state", "total_hours"],
		order_by="creation desc",
	):
		sheets.setdefault(get_week_bounds(row.start_date)[0], row)

	open_changes = set(
		frappe.get_all(
			"HelixHR Timesheet Change",
			filters={
				"employee": employee,
				"status": "Open",
				"timesheet": ["in", [row.name for row in sheets.values()] or [""]],
			},
			pluck="timesheet",
		)
	)

	index = _working_days_index([employee], span_start, span_end)
	standard = index["standard"]
	today = getdate(user_today())

	weeks = []
	for monday in mondays:
		sunday = add_days(monday, 6)
		days = {
			day
			for day in _employee_working_days(index, employee, monday, sunday)
			if (not joined or getdate(day) >= joined) and (not relieved or getdate(day) <= relieved)
		}
		sheet = sheets.get(monday)
		state = sheet.workflow_state if sheet else None
		weeks.append(
			{
				"week_start": str(monday),
				"week_end": str(sunday),
				"state": state,
				"total_hours": flt(sheet.total_hours) if sheet else 0.0,
				"expected_hours": flt(standard * len(days)) if standard else None,
				"missing": sunday < today and state in _MONTH_UNSUBMITTED and bool(days),
				"change_open": bool(sheet and sheet.name in open_changes),
			}
		)

	return {"month": month, "weeks": weeks, "full_week_hours": FULL_WEEK_HOURS}


@frappe.whitelist()
def get_timesheet_week_start(name):
	"""The Monday of the week a Timesheet belongs to.

	A Notification Log carries the record id, not the week (P2-U4 recorded
	that as a deviation: a timesheet notification opened Past weeks rather
	than the week it was about). One indexed read resolves it, scoped to
	the session employee -- somebody else's timesheet id answers nothing.
	"""
	employee = get_current_employee()
	start = frappe.db.get_value("Timesheet", {"name": name, "employee": employee}, "start_date")
	if not start:
		frappe.throw(_("That week is not yours to open."), frappe.PermissionError)
	return str(get_week_bounds(start)[0])


def _my_project_names(user):
	"""Projects `user` belongs to: a Project Users row or a User Permission
	on Project. The one membership rule `get_my_projects` and
	`get_my_project_overview` share."""
	return set(frappe.get_all("Project User", filters={"user": user}, pluck="parent")) | set(
		frappe.get_all("User Permission", filters={"user": user, "allow": "Project"}, pluck="for_value")
	)


@frappe.whitelist()
def get_my_projects():
	"""Open Projects the session user may book time on -- Project Users
	or a User Permission on Project, each with its own open Tasks
	(KTD11: no "bookable projects" API exists upstream).

	Tasks come back in **one** query for the whole allowed project set
	(P2-R22). It used to be one Task query per project, so an employee on
	a dozen projects paid a dozen round trips to fill a dropdown.
	"""
	project_names = _my_project_names(frappe.session.user)
	if not project_names:
		return []

	projects = frappe.get_all(
		"Project",
		filters={"name": ["in", list(project_names)], "status": "Open"},
		fields=["name", "project_name", "helixhr_is_billable as billable"],
		order_by="project_name",
	)
	for project in projects:
		project["billable"] = bool(project["billable"])
	if not projects:
		return []

	tasks_by_project = {}
	for task in frappe.get_all(
		"Task",
		filters={
			"project": ["in", [project.name for project in projects]],
			"status": ["not in", ["Cancelled", "Completed"]],
		},
		fields=["name", "subject", "project"],
		order_by="subject",
	):
		tasks_by_project.setdefault(task.project, []).append(
			{"name": task.name, "subject": task.subject}
		)

	for project in projects:
		project["tasks"] = tasks_by_project.get(project.name, [])
	return projects


@frappe.whitelist()
def get_my_project_overview():
	"""The My projects page (plan 2026-10-05-001 U6): each Open project the
	caller belongs to (same rule as `get_my_projects`), with its customer,
	the caller's own open Tasks (assigned to them) and the caller's own
	hours this calendar month. A sibling read so the timesheet dropdown's
	payload stays as it is. No cost or billing fields, by design.

	Ordered by my hours this month, highest first, then by name."""
	user = frappe.session.user
	employee = _my_employee()
	project_names = _my_project_names(user)
	if not project_names:
		return []

	projects = frappe.get_all(
		"Project",
		filters={"name": ["in", list(project_names)], "status": "Open"},
		fields=["name", "project_name", "customer", "status"],
	)
	if not projects:
		return []
	names = [project.name for project in projects]

	tasks_by_project = {}
	for task in frappe.get_all(
		"Task",
		filters={
			"project": ["in", names],
			"status": ["not in", ["Cancelled", "Completed", "Template"]],
			# `_assign` is Frappe's JSON list of assignees; the quotes keep
			# one user id from matching inside another.
			"_assign": ["like", f'%"{user}"%'],
		},
		fields=["name", "subject", "project", "status", "exp_end_date"],
		order_by="subject",
	):
		tasks_by_project.setdefault(task.project, []).append(
			{"name": task.name, "subject": task.subject, "status": task.status, "due": task.exp_end_date}
		)

	month_start = get_first_day(user_today())
	month_end = get_last_day(user_today())
	hours_by_project = {}
	timesheets = frappe.get_all(
		"Timesheet",
		filters={
			"employee": employee,
			"docstatus": ["<", 2],
			"start_date": ["<=", month_end],
			"end_date": [">=", month_start],
		},
		pluck="name",
	)
	if timesheets:
		for row in frappe.get_all(
			"Timesheet Detail",
			filters={
				"parent": ["in", timesheets],
				"project": ["in", names],
				"from_time": ["between", [month_start, month_end]],
			},
			fields=["project", "hours"],
		):
			hours_by_project[row.project] = flt(hours_by_project.get(row.project)) + flt(row.hours)

	result = [
		{
			"name": project.name,
			"project_name": project.project_name,
			"customer": project.customer,
			"status": project.status,
			"tasks": tasks_by_project.get(project.name, []),
			"hours_this_month": flt(hours_by_project.get(project.name), 2),
		}
		for project in projects
	]
	result.sort(key=lambda row: (-row["hours_this_month"], (row["project_name"] or "").lower()))
	return result

def _bookable_tasks_by_project():
	"""`{project: {task ids}}` for the session user -- the allow-list both
	writes below validate against. The browser's dropdown is a convenience;
	this is the check (P2-R27)."""
	return {project["name"]: {task["name"] for task in project["tasks"]} for project in get_my_projects()}


def _project_billable_flags(project_names):
	"""`{project: bool}` read straight from `helixhr_is_billable` (P7-KTD2).

	The row's `is_billable` is derived from this, never from the request --
	a payload cannot mark its own hours billable independent of the
	project it names. Read directly rather than through `get_my_projects`
	so a project's flag is authoritative even if the caller's bookable set
	changed underneath a row already on the week."""
	if not project_names:
		return {}
	rows = frappe.get_all(
		"Project",
		filters={"name": ["in", list(project_names)]},
		fields=["name", "helixhr_is_billable"],
	)
	return {row.name: bool(row.helixhr_is_billable) for row in rows}


@frappe.whitelist(methods=["POST"])
def save_my_week(week_start, rows):
	"""Insert or update the one draft Timesheet for this week (KTD10).
	Refuses to touch anything but a Draft or Rejected timesheet -- once a
	week is Pending Approval or Approved it isn't this method's to edit
	(the workflow's own `allow_edit` per state backs this up too, this
	is just a clearer error than a generic permission failure).
	"""
	rate_limit_per_user("save_my_week")
	employee = get_current_employee()
	monday, sunday = get_week_bounds(week_start)
	return _write_my_week(employee, monday, sunday, rows).name


@frappe.whitelist(methods=["POST"])
def submit_my_week(week_start, rows, expected_modified=None):
	"""Save this week's rows and send them to the manager, in one request
	(P2-U6, P2-AE4, P2-R27).

	The browser used to do this in two calls -- `save_my_week`, then
	`frappe.model.workflow.apply_workflow` -- and `saveDraft()` caught its
	own error, so `submitWeek()` awaited a *failed* save and submitted
	anyway. An invalid edit was silently dropped and the previously saved
	rows went to the manager as though they were what the employee saw
	(P2-AE4). One method removes the seam: validation that refuses never
	reaches the transition, and anything that throws rolls back the write
	with it.

	`expected_modified` is the concurrency token `get_my_week` returned
	for this week, or nothing at all when the week has no Timesheet yet.
	A second tap of Submit, or a week edited in another tab, arrives
	carrying a value that no longer matches and is refused rather than
	transitioning twice. The employee row is locked first, so two
	concurrent requests serialize and the loser reads the winner's result
	instead of racing it -- the first submit of a week has no Timesheet
	row to lock yet, which is precisely the case where a double tap would
	otherwise insert two.
	"""
	from frappe.model.workflow import apply_workflow

	rate_limit_per_user("save_my_week")
	employee = get_current_employee()
	monday, sunday = get_week_bounds(week_start)

	_lock_employee(employee)

	current = _week_timesheet(employee, monday, ("name", "workflow_state", "modified"), sunday)
	_assert_week_is_still_sendable(current, expected_modified)

	# The row this week already has, handed to the writer rather than looked
	# up again: it is the same query, and it was being run twice per submit.
	doc = _write_my_week(employee, monday, sunday, rows, existing_name=current.name if current else None)
	apply_workflow(doc, "Submit")
	doc.reload()
	return {
		"name": doc.name,
		"workflow_state": doc.workflow_state,
		"modified": str(doc.modified),
	}


_STALE_WEEK = "This week changed while you were working on it. Reload and try again."


@frappe.whitelist(methods=["POST"])
def recall_my_week(week_start, expected_modified=None):
	"""Take back a week that is still waiting for its manager's decision
	(plan 2026-10-04-003 U2, R5).

	The workflow's Recall transition does the state move; this method is the
	authorization and the concurrency token around it, laid out like
	`submit_my_week`: the employee row is locked first, the stored week is
	re-read under the lock, and only the caller's own week still in Pending
	Approval moves. Once the manager has decided -- Approved, Sent Back,
	Pending HR -- the week is not the employee's to recall (R6).
	"""
	from frappe.model.workflow import apply_workflow

	rate_limit_per_user("recall_my_week")
	employee = get_current_employee()
	monday, sunday = get_week_bounds(week_start)

	_lock_employee(employee)

	current = _week_timesheet(employee, monday, ("name", "workflow_state", "modified"), sunday)
	if not current:
		frappe.throw(_("There is no timesheet for this week."), frappe.DoesNotExistError)
	if current.workflow_state == "Approved":
		frappe.throw(_("This week is already approved. Ask HR to change it."))
	if current.workflow_state == TIMESHEET_PENDING_HR:
		frappe.throw(_("This week is with HR now. Ask HR to sort it out."))
	if current.workflow_state != PENDING_STATE:
		# The portal's own words, never the raw workflow state (design
		# system copy rules).
		if current.workflow_state == TIMESHEET_SENT_BACK:
			frappe.throw(_("This week was sent back and is yours to edit, not recall."))
		if not current.workflow_state or current.workflow_state == "Draft":
			frappe.throw(_("This week is still a draft -- there is nothing to recall."))
		frappe.throw(_("This week can't be recalled right now. Reload to see its state."))
	if expected_modified and get_datetime(expected_modified) != get_datetime(current.modified):
		frappe.throw(_(_STALE_WEEK))

	# The arrival bell is this week's "waiting for you" row; the recall is
	# its answer, so the stale one goes before the new notice is written.
	manager_user = _approver_user(employee)
	if manager_user:
		frappe.db.delete(
			"Notification Log",
			{
				"for_user": manager_user,
				"document_type": "Timesheet",
				"document_name": current.name,
				"subject": ["like", "%submitted a timesheet%"],
			},
		)

	doc = frappe.get_doc("Timesheet", current.name)
	apply_workflow(doc, "Recall")
	doc.reload()
	return {
		"name": doc.name,
		"workflow_state": doc.workflow_state,
		"modified": str(doc.modified),
	}


# ---------------------------------------------------------------------------
# Change requests on approved weeks (plan 2026-10-04-003 U3, R7-R13)
#
# KTD3: a change request is its own DocType, `HelixHR Timesheet Change` --
# an approved week is docstatus 1 and immutable, so state on it would need
# allow-on-submit fields and could not keep the history of several requests.
# The DocType carries no Employee-role DocPerm at all: every read and write
# here goes through a projection that authorizes first (`get_all` with its
# own scope, never `get_list`'s DocPerm check), which is this app's
# "projections, not permissions" shape. `_assert_may_act_on` still gates
# every decision before a single side effect runs.
# ---------------------------------------------------------------------------

_WEEK_NOT_CHANGEABLE = "This week can't be changed."


def _open_week_change(timesheet_name, fields=("name", "status", "comment", "creation")):
	"""The one open change request on a week, or None (R8: at most one)."""
	return frappe.db.get_value(
		"HelixHR Timesheet Change",
		{"timesheet": timesheet_name, "status": "Open"},
		list(fields),
		as_dict=True,
		order_by="creation desc",
	)


def week_change_problem(timesheet_name):
	"""KTD5: why an approved week is locked, or None when it can be changed.

	R9 wants the *why* shown to the employee before they write a comment, so
	every branch answers with the sentence the screen shows. HR has no
	override here (Scope Boundaries): a week inside payroll or billing is
	not the portal's to reopen, whatever anybody asks for.
	"""
	if frappe.db.exists("Sales Invoice Timesheet", {"time_sheet": timesheet_name}):
		invoice = frappe.db.get_value(
			"Sales Invoice Timesheet", {"time_sheet": timesheet_name}, "parent"
		)
		if frappe.db.get_value("Sales Invoice", invoice, "docstatus") == 1:
			return _("This week is part of a submitted invoice, so it can't be changed.")
	salary_slip = frappe.db.get_value("Timesheet", timesheet_name, "salary_slip")
	if salary_slip:
		return _("This week is already in a payslip, so it can't be changed.")
	row = frappe.db.get_value(
		"Timesheet", timesheet_name, ["employee", "start_date", "end_date"], as_dict=True
	)
	if row and frappe.db.exists(
		"Salary Slip",
		{
			"employee": row.employee,
			"docstatus": 1,
			"start_date": ["<=", str(row.end_date)],
			"end_date": [">=", str(row.start_date)],
		},
	):
		return _("This week is covered by a payslip, so it can't be changed.")
	return None


@frappe.whitelist(methods=["POST"])
def raise_timesheet_change(week_start, comment, expected_modified=None):
	"""Ask to change an approved week (R7, R8, R9).

	The comment is the request: required, at least ten characters. Raised
	only on the caller's own Approved week, only while nothing about it is
	locked, and only when the week has no open request already. The current
	`reports_to` manager is stamped as the approver; with no manager the
	request routes to HR (R10).
	"""
	rate_limit_per_user("raise_timesheet_change")
	employee = get_current_employee()
	monday, _sunday = get_week_bounds(week_start)

	# The employee row lock serialises two concurrent raises, so the
	# one-open-request check below and the insert cannot both pass (R8).
	_lock_employee(employee)

	current = _week_timesheet(employee, monday, ("name", "workflow_state", "modified"))
	if not current:
		frappe.throw(_("There is no timesheet for this week."), frappe.DoesNotExistError)
	if current.workflow_state != "Approved":
		frappe.throw(_("Only an approved week can be changed."))
	problem = week_change_problem(current.name)
	if problem:
		frappe.throw(problem)
	if _open_week_change(current.name):
		frappe.throw(_("There is already an open change request for this week."))
	text = (comment or "").strip()
	problem = comment_problem(text)
	if problem:
		frappe.throw(problem)
	if expected_modified and get_datetime(expected_modified) != get_datetime(current.modified):
		frappe.throw(_(_STALE_WEEK))

	doc = frappe.get_doc(
		{
			"doctype": "HelixHR Timesheet Change",
			"naming_series": "HTC-.YYYY.-",
			"employee": employee,
			"company": frappe.db.get_value("Employee", employee, "company"),
			"timesheet": current.name,
			"week_start": str(monday),
			"comment": text,
			"status": "Open",
			"approver_user": _approver_user(employee),
		}
	)
	doc.insert(ignore_permissions=True)
	return {"name": doc.name, "status": doc.status, "approver_user": doc.approver_user}


@frappe.whitelist(methods=["POST"])
def withdraw_timesheet_change(name):
	"""Take back the caller's own open change request (R8)."""
	rate_limit_per_user("withdraw_timesheet_change")
	employee = get_current_employee()
	current = frappe.db.get_value(
		"HelixHR Timesheet Change",
		name,
		["employee", "status"],
		as_dict=True,
		for_update=True,
	)
	if not current:
		frappe.throw(_("That change request no longer exists."), frappe.DoesNotExistError)
	if current.employee != employee:
		frappe.throw(_("That change request isn't yours."), frappe.PermissionError)
	if current.status != "Open":
		frappe.throw(_("This change request has already been decided."))
	frappe.db.set_value("HelixHR Timesheet Change", name, "status", "Withdrawn")
	return {"name": name, "status": "Withdrawn"}


def _may_act_on_timesheet_change(doc, user):
	"""The stamped approver decides. `employee_on_update` keeps
	`approver_user` current across a `reports_to` change (KTD10), so the
	stamp is the single answer for who owns an open request; HR reaches the
	kind through `_assert_may_act_on`'s scope branch (R10)."""
	if user != doc.approver_user:
		frappe.throw(_(_APPROVAL_NOT_FOUND), frappe.PermissionError)


def _change_allowed_actions(doc, user):
	"""R13: exactly two decisions, and the employee's own request never
	offers them to the employee (`_allowed_actions` refuses the requester
	before this runs). The stamped approver gets them; HR gets them in the
	manager's place, through the same admin-scope reach
	`_assert_may_act_on` already applies (R10)."""
	if user == doc.approver_user:
		return ["Accept", "Decline"]
	if _is_hr(user) and employee_in_admin_scope(doc.employee, resolve_admin_scope(user)):
		return ["Accept", "Decline"]
	return []


def _change_request_decision_detail(doc):
	"""R13: the request's own words and the approved week's hours -- the
	evidence deciding it takes, without re-reading the whole grid."""
	detail = _decision_head(doc, doc.employee_name)
	detail.update(
		{
			"kind": "change",
			"state": doc.status,
			"status": doc.status,
			"timesheet": doc.timesheet,
			"week_start": str(doc.week_start),
			"week_end": str(add_days(doc.week_start, 6)),
			"comment": doc.comment,
			"total_hours": flt(frappe.db.get_value("Timesheet", doc.timesheet, "total_hours")),
			"decision_note": doc.decision_note,
			"amended_timesheet": doc.amended_timesheet,
			"sent_on": str(doc.creation) if doc.creation else None,
		}
	)
	return detail


def _change_request_hours(rows):
	"""The approved week's hours for a page of change rows, one query for
	the page (P2-R22)."""
	names = {row.timesheet for row in rows if row.timesheet}
	if not names:
		return {}
	return {
		row.name: flt(row.total_hours)
		for row in frappe.get_all(
			"Timesheet",
			filters={"name": ["in", list(names)]},
			fields=["name", "total_hours"],
		)
	}


def _change_request_summaries(employee, today):
	"""Open change requests addressed to this caller (R13).

	`frappe.get_all` with `ignore_permissions` is deliberate: role Employee
	has no DocPerm on this doctype (KTD3), and `frappe.get_list` answers a
	PermissionError rather than an empty list for exactly that caller. The
	scope is the authorization: `approver_user` is stamped at raise and
	kept current by `employee_on_update` (KTD10).

	This half never stands down for HR (plan 2026-10-05-001 KTD1): a
	request addressed to an approver who also holds HR Manager or System
	Manager arrives here, and `_hr_change_request_summaries` excludes rows
	addressed to the caller, so each request still shows once.
	"""
	rows = frappe.get_all(
		"HelixHR Timesheet Change",
		filters={"status": "Open", "approver_user": frappe.session.user},
		fields=["name", "employee", "employee_name", "timesheet", "week_start", "comment", "creation"],
		order_by="creation asc",
		limit=_QUEUE_FETCH,
	)
	hours = _change_request_hours(rows)
	return [
		_summary_row(
			"change",
			"HelixHR Timesheet Change",
			row.name,
			row.employee,
			row.employee_name,
			row.week_start,
			add_days(row.week_start, 6),
			row.creation,
			"Open",
			today,
			total_hours=hours.get(row.timesheet),
			comment=row.comment,
		)
		for row in rows
	]


# The queue tuple above is built before this section's functions exist, so
# the change kind joins it here rather than in the literal -- with its own
# read bound, like every other entry (KTD8).
_APPROVAL_SUMMARY_COLLECTORS = (
	*_APPROVAL_SUMMARY_COLLECTORS,
	(_change_request_summaries, _QUEUE_FETCH),
)


def _hr_change_request_summaries(employee, today):
	"""HR's view of open change requests: everything in their admin scope
	that is not already in their manager half, tagged. A routed request (no
	manager) is HR's alone; one with a manager shows here because HR
	decides in the manager's place (R10). A request addressed to the HR
	caller themselves is excluded here because `_change_request_summaries`
	always returns it, so it shows once."""
	employee_filter = _hr_queue_employee_filter(employee)
	if employee_filter is None:
		return []
	rows = frappe.get_all(
		"HelixHR Timesheet Change",
		filters={
			"status": "Open",
			"employee": employee_filter,
			"approver_user": ["!=", frappe.session.user],
		},
		fields=["name", "employee", "employee_name", "timesheet", "week_start", "comment", "approver_user", "creation"],
		order_by="creation asc",
		limit=_QUEUE_FETCH,
	)
	hours = _change_request_hours(rows)
	return [
		_summary_row(
			"change",
			"HelixHR Timesheet Change",
			row.name,
			row.employee,
			row.employee_name,
			row.week_start,
			add_days(row.week_start, 6),
			row.creation,
			"Open",
			today,
			total_hours=hours.get(row.timesheet),
			comment=row.comment,
			for_hr=not row.approver_user,
		)
		for row in rows
	]


def _act_on_timesheet_change(doc, action):
	"""Accept or Decline (R11, R12).

	The whole move runs as Administrator inside `as_administrator()`: the
	HelixHR gate (`_assert_may_act_on`) has already decided who may act, and
	neither role Employee (the decider is an ordinary line manager) nor the
	stamped approver has a DocPerm on this doctype to carry the status save.
	Capturing the decider first is load-bearing: inside the block the
	session user is Administrator, and "decided by Administrator" would be a
	lie on the record and in the employee's email.
	"""
	from helixhr.utils import as_administrator

	decider = frappe.session.user
	with as_administrator():
		current = frappe.db.get_value(
			"HelixHR Timesheet Change",
			doc.name,
			["status", "employee", "timesheet"],
			as_dict=True,
			for_update=True,
		)
		if not current or current.status != "Open":
			frappe.throw(_("This change request has already been decided. Reload to see the result."))
		if action == "Accept":
			_accept_timesheet_change(doc, decider)
		else:
			_decline_timesheet_change(doc, decider)

	# The employee hears the outcome from outside the elevated block, so the
	# send never runs as Administrator and the template's own checks see the
	# real actor.
	from helixhr import events

	employee_user = frappe.db.get_value("Employee", doc.employee, "user_id")
	events._mail(
		doc,
		"timesheet_change_decided",
		[employee_user] if employee_user else [],
		lambda: events._change_context(
			doc,
			state="accepted" if action == "Accept" else "declined",
			approver_name=frappe.utils.get_fullname(decider),
			decision_note=(doc.decision_note or "").strip() if action == "Decline" else "",
			# The week page reads only the `:weekStart` route param (plan
			# 2026-10-05-001 U4); the amended Draft keeps the same Monday.
			action_url=events._portal_url(f"timesheet/{doc.week_start}"),
		),
	)


def _decline_timesheet_change(doc, decider):
	doc.db_set("status", "Declined")
	doc.db_set("decided_by", decider)
	doc.db_set("decided_on", now_datetime())
	doc.db_set("decision_note", (doc.flags.helixhr_decision_note or "").strip())


def _accept_timesheet_change(doc, decider):
	"""KTD4, one transaction: lock, recheck, cancel, amend.

	Any failure throws and the whole accept rolls back -- the request stays
	Open and the week stays Approved, which is the only state that cannot
	confuse anybody.
	"""
	timesheet_name = doc.timesheet
	# 1. The change row is locked by the caller; the week is locked here so
	#    an accept racing a Desk cancel serialises on the row.
	frappe.db.get_value("Timesheet", timesheet_name, "name", for_update=True)
	state = frappe.db.get_value(
		"Timesheet", timesheet_name, ["workflow_state", "docstatus"], as_dict=True
	)
	# 2. R9 rechecked under the lock: an invoice or payslip that landed
	#    between raise and accept closes the door here.
	if not state or state.workflow_state != "Approved" or cint(state.docstatus) != 1:
		frappe.throw(_("The week is no longer approved. Reload and decide again."))
	problem = week_change_problem(timesheet_name)
	if problem:
		frappe.throw(problem)

	# 3. Cancel the approved week. The Cancelled workflow state is set
	#    before `cancel()` so the stored row reads Cancelled at docstatus 2
	#    (KTD2), and ERPNext's own `on_cancel` unwinds the Task/Project
	#    hours it added at submit.
	timesheet = frappe.get_doc("Timesheet", timesheet_name)
	timesheet.workflow_state = "Cancelled"
	timesheet.cancel()

	# 4. The editable copy starts at Draft, owned by the employee -- an
	#    amend run as the manager would hand them a week they could never
	#    resend (the self-approval check keys on `doc.owner`).
	employee_user = frappe.db.get_value("Employee", doc.employee, "user_id")
	amended = frappe.copy_doc(timesheet)
	amended.amended_from = timesheet_name
	amended.docstatus = 0
	amended.workflow_state = "Draft"
	# 5. The old week's decision reason is the old week's; the copy starts
	#    clean so the resubmit's queue row is not pre-flagged with it.
	amended.set(DECISION_REASON_FIELD, None)
	amended.insert(ignore_permissions=True)
	frappe.db.set_value("Timesheet", amended.name, "owner", employee_user, update_modified=False)

	doc.db_set("status", "Accepted")
	doc.db_set("decided_by", decider)
	doc.db_set("decided_on", now_datetime())
	doc.db_set("amended_timesheet", amended.name)
	return amended


def _assert_week_is_still_sendable(current, expected_modified):
	"""One send per week, per state. Everything here runs *after* the
	employee row lock, so the second of two concurrent submits sees the
	first one's result."""
	if current and current.workflow_state not in ("Draft", TIMESHEET_SENT_BACK, None):
		frappe.throw(
			_("This week is {0} and can't be sent again.").format(current.workflow_state)
		)

	if not expected_modified:
		# The screen was rendered before this week had a Timesheet. If one
		# exists now, something else created it -- another tab, or the
		# first half of a double tap.
		if current:
			frappe.throw(_(_STALE_WEEK))
		return

	if not current or get_datetime(expected_modified) != get_datetime(current.modified):
		frappe.throw(_(_STALE_WEEK))


def _lock_employee(employee):
	"""`SELECT ... FOR UPDATE` on the one Employee row every writer of a
	week shares, so two concurrent writes serialise instead of both
	inserting a Timesheet for the same week.

	Taken by every writer that has to serialise against another, because a
	lock only excludes statements that also take it: `submit_my_week` held
	it while `save_my_week` took nothing at all, so a save walked straight
	past a concurrent submit and the double-insert the lock exists to stop
	was still reachable. The first write of a week has no Timesheet row to
	lock yet, which is exactly the case that matters. `punch_my_checkin`
	takes the same lock for the same reason on the same row (P3-R7).
	"""
	return frappe.db.get_value("Employee", employee, "name", for_update=True)


def _write_my_week(employee, monday, sunday, rows, existing_name=None):
	"""Replace this week's rows. Shared by `save_my_week` and
	`submit_my_week` so a week is validated and written exactly one way.

	`existing_name` is the week's Timesheet when the caller already looked
	it up (`submit_my_week` does, to check sendability); otherwise it is
	resolved here.
	"""
	from frappe.model.workflow import apply_workflow

	if isinstance(rows, str):
		rows = json.loads(rows)
	if not isinstance(rows, list):
		frappe.throw(_("Those rows aren't in a shape we can save."))

	_validate_rows(rows, _bookable_tasks_by_project(), monday, sunday)

	_lock_employee(employee)
	if not existing_name:
		current = _week_timesheet(employee, monday, ("name",), sunday)
		existing_name = current.name if current else None

	if existing_name:
		doc = frappe.get_doc("Timesheet", existing_name)
		if doc.workflow_state == TIMESHEET_SENT_BACK:
			# Sent Back is not an editable state for an Employee (the
			# workflow gives `allow_edit` to HR Manager), so a sent-back
			# week has to travel Sent Back -> Draft before it can be
			# written. The portal used to make the employee do that
			# themselves with a button called "Edit and resubmit" that
			# only performed the reopen -- it left them on a Draft with
			# their fix unsaved and unsent (P2-U6 step 7). It is plumbing,
			# not a decision, so it happens here.
			apply_workflow(doc, "Edit")
			doc.reload()
		if doc.workflow_state not in ("Draft", None):
			frappe.throw(
				_("This week is {0} and can't be edited here.").format(doc.workflow_state)
			)
	else:
		doc = frappe.new_doc("Timesheet")
		doc.employee = employee
		doc.company = frappe.db.get_value("Employee", employee, "company")

	doc.user = frappe.session.user
	doc.start_date = str(monday)
	doc.end_date = str(sunday)
	doc.set("time_logs", [])
	# ERPNext's Timesheet refuses two time logs whose from/to windows overlap,
	# so a day's rows are laid end to end from midnight rather than all
	# starting at the same hour. The portal books *durations*, not clock
	# times -- nothing in it displays from_time -- but the child table stores
	# a window, and two projects on one day is the ordinary case the grid is
	# built for. Midnight rather than 09:00 as the anchor: a day validated up
	# to 24 hours has to fit inside its own day.
	#
	# `is_billable` (P7-R6, R7, KTD1, KTD2) is derived here from the
	# project's own flag, never taken from `row` -- the request is never
	# consulted for it, so a row that names a billable-ish key of its own
	# is silently ignored. ERPNext's `update_billing_hours` then derives
	# `billing_hours` from `hours` on validate; nothing here reads or
	# writes `billing_rate`, `billing_amount`, `costing_rate`, or
	# `costing_amount` (R8).
	billable_by_project = _project_billable_flags({row["project"] for row in rows})
	day_offset = {}
	for row in rows:
		date = str(getdate(row["date"]))
		hours = flt(row["hours"])
		start = frappe.utils.add_to_date(
			get_datetime(f"{date} 00:00:00"), hours=day_offset.get(date, 0)
		)
		day_offset[date] = day_offset.get(date, 0) + hours
		doc.append(
			"time_logs",
			{
				"project": row["project"],
				"task": row.get("task"),
				"hours": hours,
				"description": row.get("note"),
				"activity_type": "General",
				"from_time": start,
				"to_time": frappe.utils.add_to_date(start, hours=hours),
				"is_billable": cint(billable_by_project.get(row["project"], False)),
			},
		)
	doc.save()
	return doc


def _validate_rows(rows, tasks_by_project, monday, sunday):
	"""Everything the browser also checks, checked again here because the
	browser is not where the rule lives (P2-R27, P2-U6 scenario 4)."""
	if not rows:
		frappe.throw(_("Add at least one row before saving."))
	if len(rows) > _MAX_WEEK_ROWS:
		frappe.throw(_("That's more rows than one week can hold."))

	day_totals = {}
	for row in rows:
		if not isinstance(row, dict):
			frappe.throw(_("Those rows aren't in a shape we can save."))

		project = row.get("project")
		if not project:
			frappe.throw(_("Every row needs a project."))
		if project not in tasks_by_project:
			frappe.throw(_("You can't book time on {0}.").format(project))

		task = row.get("task")
		if task and task not in tasks_by_project[project]:
			frappe.throw(_("That task isn't on {0}.").format(project))

		if not row.get("date"):
			frappe.throw(_("Every row needs a date."))
		date = getdate(row["date"])
		if date < monday or date > sunday:
			frappe.throw(_("{0} isn't in this week.").format(date))

		hours = flt(row.get("hours"))
		if hours < 0.25 or hours > 24:
			frappe.throw(_("Hours must be between 0.25 and 24."))

		day_totals[str(date)] = day_totals.get(str(date), 0) + hours
		if day_totals[str(date)] > 24:
			frappe.throw(_("{0} has more than 24 hours booked.").format(date))


def _get_unread_notification_count():
	return frappe.db.count("Notification Log", {"for_user": frappe.session.user, "read": 0})


# ---------------------------------------------------------------------------
# Approvals (U12, R25, R26, KTD7; P2-U7, P2-R17, P2-R25, P2-R27)
#
# One decision queue, and one rule about it: the summary a manager sees, the
# evidence they read before deciding, and the authorization on the decision
# itself all come from the same two server functions. `_approval_summaries`
# decides what is in the queue; `_assert_may_act_on` decides who may open or
# act on any single item. Nothing on the screen widens either.
#
# There is no second approval model here. Timesheet keeps its Workflow and
# its Pending-Approval-only DocShare; Leave Application keeps the native
# HRMS submit lifecycle. Bulk approval was refused in P2-U7 because "a
# decision is made against evidence, and a button that decides eight
# records at once cannot have been". Plan 2026-10-04-003 reverses that in a
# guarded form (KTD6): `approve_clean_items` approves only rows the server
# itself still classifies as clean -- it recomputes the R16 flags per item
# and refuses anything flagged, anything whose concurrency token moved, and
# anything the per-item authorization would refuse. The evidence is the
# flag row the manager already saw; the confirm names the count and total.
# ---------------------------------------------------------------------------

# How much of the queue is shown at once. A manager with more than this many
# people waiting has a staffing problem, not a paging problem -- the count is
# still exact so the screen can say so.
_APPROVAL_PAGE = 25
# "Decided this week" is a receipt, not a history: the last few outcomes, so
# a manager can see that the thing they just did actually happened.
_DECIDED_DAYS = 7
_DECIDED_LIMIT = 5
# What a handed-over leave reads as in the receipt list. Leave has no workflow
# state of its own (P2-KTD17), so this is the one place the word lives -- the
# same word `TIMESHEET_PENDING_HR` and `REQUEST_PENDING_HR` already carry, so
# the three kinds report one state under one name.
_LEAVE_PENDING_HR = "Pending HR"


# U6 / R13. The kinds a queue row can be, in the order the chips render.
_APPROVAL_FILTER_KINDS = ("leave", "timesheet", "attendance", "request", "change")


def _valid_request_category(category):
	"""The category filter, refused unless it names a category record --
	active or not, because an inactive category still has past requests a
	chip has to reach (R13). Returns None when no filter was asked for."""
	if not category:
		return None
	if not isinstance(category, str) or not frappe.db.exists("HelixHR Request Category", category):
		frappe.throw(_("There is no request category called {0}.").format(category))
	return category


def _count_by(values):
	"""[{name, count}] for each distinct non-empty value, by name."""
	counts = {}
	for value in values:
		if value:
			counts[value] = counts.get(value, 0) + 1
	return [{"name": name, "count": counts[name]} for name in sorted(counts)]


@frappe.whitelist()
def get_my_approvals(kind=None, category=None):
	"""The manager's queue: everything waiting on them, oldest first, plus
	the handful of decisions they made this week (P2-U7 step 1).

	Summary only. The evidence -- timesheet rows and day totals, a leave's
	reason -- costs a document read per item, so it is loaded by
	`get_approval_detail` for the one item actually selected (P2-R22).

	U6 / R13: `kind` and `category` narrow the page; `counts` is always the
	caller's whole queue, so every chip says what choosing it would show.
	A category implies kind "request" -- only requests have one.
	"""
	rate_limit_per_user("get_my_approvals")
	if kind and kind not in _APPROVAL_FILTER_KINDS:
		frappe.throw(_("There is no approval kind called {0}.").format(kind))
	category = _valid_request_category(category)
	if category:
		kind = "request"
	employee = get_current_employee()
	pending, capped = _approval_summaries(employee)
	counts = {
		"kinds": [
			{"name": name, "count": sum(1 for row in pending if row["kind"] == name)}
			for name in _APPROVAL_FILTER_KINDS
		],
		"categories": _count_by(row.get("category") for row in pending if row["kind"] == "request"),
	}
	if kind:
		pending = [row for row in pending if row["kind"] == kind]
	if category:
		pending = [row for row in pending if row.get("category") == category]
	# Plan 2026-10-04-003 KTD8 / R19: the 25-row page is gone for the kinds
	# the redesign groups per person (timesheet, leave, and the mixed view
	# those live in); the routed and attendance kinds keep theirs. The hard
	# cap stands behind all of it.
	shown = pending[:_APPROVAL_PAGE] if kind in ("attendance", "request") else pending[:_QUEUE_HARD_CAP]

	# R14: the queue is grouped per person on the server, oldest waiting
	# first -- the rows are already oldest-first, so the first time each
	# employee appears is their oldest item, and the groups come out in the
	# same order without a second sort.
	people = []
	by_employee = {}
	for row in pending:
		entry = by_employee.get(row["employee"])
		if not entry:
			entry = {
				"employee": row["employee"],
				"employee_name": row["employee_name"],
				"initials": row["initials"],
				"photo_url": row.get("photo_url"),
				"count": 0,
				"oldest_sent_on": row["sent_on"],
			}
			by_employee[row["employee"]] = entry
			people.append(entry)
		entry["count"] += 1
	return {
		"today": user_today(),
		"pending": shown,
		"total": len(pending),
		"counts": counts,
		"people": people,
		# `total` is what came back, and every kind's read is bounded, so on a
		# very large backlog it is a floor and not a count. The flag is what
		# lets the screen say "50+" rather than lie about 50; a real COUNT per
		# kind on every poll is the thing being avoided (P2-R22, P3-R25).
		"total_is_capped": capped or len(pending) > len(shown),
		"decided": _recently_decided(employee),
	}


def _decided_row(
	kind, name, employee_name, label, from_date, to_date, status, decided_on, docstatus=0
):
	return {
		"id": f"{kind}:{name}",
		"kind": kind,
		"name": name,
		"employee_name": employee_name,
		"initials": _initials(employee_name),
		"label": label,
		"from_date": str(from_date) if from_date else None,
		"to_date": str(to_date) if to_date else None,
		"status": status,
		# P4-U4: the receipt's badge needs it. Leave's Rejected is a send-back
		# at docstatus 0 and a terminal rejection at 1 (P4-KTD4), so a receipt
		# without the docstatus would word a manager's final no as "Sent back".
		"docstatus": cint(docstatus),
		"decided_on": str(decided_on) if decided_on else None,
	}


def _decided_leave(employee, since):
	"""What this approver decided, plus what they handed to HR.

	The handover row is the same receipt a timesheet and an attendance
	request have had since P3-KTD7: a manager who escalates has finished
	with the record and needs to see that their step happened. It was the one
	kind of the three that reported nothing at all.
	"""
	handed_over = []
	# Empty for an HR caller: for them this row is the top of their own
	# pending queue, not a receipt (see `_decided_hr_handover_states`).
	if _decided_hr_handover_states(LEAVE_STAGE_HR):
		handed_over = [
			_decided_row(
				"leave",
				row.name,
				row.employee_name,
				row.leave_type,
				row.from_date,
				row.to_date,
				# The same word the other two kinds use for the same state.
				_LEAVE_PENDING_HR,
				row.modified,
				row.docstatus,
			)
			for row in frappe.get_list(
				"Leave Application",
				filters={
					"leave_approver": frappe.session.user,
					"employee": ["!=", employee],
					"status": "Open",
					"docstatus": 0,
					"helixhr_stage": LEAVE_STAGE_HR,
					"modified": [">=", str(since)],
				},
				fields=[
					"name",
					"employee_name",
					"leave_type",
					"from_date",
					"to_date",
					"docstatus",
					"modified",
				],
				order_by="modified desc",
				limit=_DECIDED_LIMIT,
			)
		]

	return handed_over + [
		_decided_row(
			"leave",
			row.name,
			row.employee_name,
			row.leave_type,
			row.from_date,
			row.to_date,
			row.status,
			row.modified,
			row.docstatus,
		)
		for row in frappe.get_list(
			"Leave Application",
			filters={
				"leave_approver": frappe.session.user,
				"employee": ["!=", employee],
				"status": ["in", ["Approved", "Rejected"]],
				"modified": [">=", str(since)],
			},
			fields=[
				"name",
				"employee_name",
				"leave_type",
				"from_date",
				"to_date",
				"status",
				"docstatus",
				"modified",
			],
			order_by="modified desc",
			limit=_DECIDED_LIMIT,
		)
	]


def _decided_hr_handover_states(*states):
	"""The Pending-HR states a "Decided this week" receipt should include, for
	*this* caller (P4-U4).

	A manager who hands a week or a request to HR has finished with it, and
	the receipt is how they see that their step happened -- attendance
	requests have said "Waiting for HR" here since P3-KTD7, and after P4-R5 a
	timesheet can be handed over too, so it says the same thing.

	For an HR Manager the same row is not a receipt at all: it is the top of
	their own pending queue (P4-R11), and listing it under "Decided this
	week" would tell them they had already dealt with the thing they are
	being asked to deal with. So the Pending-HR states drop out for an HR
	caller, and the receipt they keep is what they themselves approved, sent
	back or rejected.
	"""
	return [] if _is_hr() else list(states)


def _decided_by_me():
	"""The extra filter that keeps an HR caller's receipt list their own.

	"Decided this week" is a receipt for work *this user* did, and the two
	workflow collectors narrow it with `employee != caller` alone -- which is
	enough for a line manager (their reads are bounded by the DocShare and by
	their own nested-set User Permission) and not remotely enough for an HR
	Manager, whose native read spans the company: HR's receipt list was every
	manager's decisions. The same permission-scoping trap `_line_manager_filter`
	closes for the *pending* queue (P4-KTD7, P4-R11).

	`modified_by` rather than `_line_manager_filter`: HR decides records that
	are nobody's reports, and those are exactly the decisions their receipt
	has to show. A decided record's last writer is the decider.
	"""
	return {"modified_by": frappe.session.user} if _is_hr() else {}


def _decided_timesheets(employee, since):
	"""Plan 2026-10-04-003 KTD2: a cancelled week reads "Cancelled" here.

	The Cancelled workflow state (docstatus 2) joins the receipt list, and
	every docstatus-2 row reads Cancelled regardless of its stored state --
	a week cancelled in Desk before the state existed keeps
	`workflow_state = "Approved"`, and an approver must never see that
	row as an approval."""
	return [
		_decided_row(
			"timesheet",
			row.name,
			row.employee_name,
			"Timesheet",
			row.start_date,
			row.end_date,
			"Cancelled" if cint(row.docstatus) == 2 else row.workflow_state,
			row.modified,
			row.docstatus,
		)
		for row in frappe.get_list(
			"Timesheet",
			filters={
				"employee": ["!=", employee],
				"workflow_state": [
					"in",
					[
						"Approved",
						"Cancelled",
						TIMESHEET_SENT_BACK,
						*_decided_hr_handover_states(TIMESHEET_PENDING_HR),
					],
				],
				"modified": [">=", str(since)],
				**_decided_by_me(),
			},
			fields=[
				"name",
				"employee_name",
				"start_date",
				"end_date",
				"workflow_state",
				"docstatus",
				"modified",
			],
			order_by="modified desc",
			limit=_DECIDED_LIMIT,
		)
	]


def _decided_attendance_requests(employee, since):
	"""A request the manager sent on reads "Waiting for HR" here, which is
	the honest receipt: their step is done and HR's has not happened yet
	(P3-KTD7). Approved, Sent Back and Rejected are the later outcomes of the
	same row. See `_decided_hr_handover_states` for why HR's own queue does
	not appear in HR's receipt.
	"""
	return [
		_decided_row(
			"attendance",
			row.name,
			row.employee_name,
			"Attendance request",
			row.from_date,
			row.to_date,
			row.workflow_state,
			row.modified,
			row.docstatus,
		)
		for row in frappe.get_list(
			"Attendance Request",
			filters={
				"employee": ["!=", employee],
				"workflow_state": [
					"in",
					[
						REQUEST_APPROVED,
						REQUEST_SENT_BACK,
						REQUEST_REJECTED,
						*_decided_hr_handover_states(REQUEST_PENDING_HR),
					],
				],
				"modified": [">=", str(since)],
				**_decided_by_me(),
			},
			fields=[
				"name",
				"employee_name",
				"from_date",
				"to_date",
				"workflow_state",
				"docstatus",
				"modified",
			],
			order_by="modified desc",
			limit=_DECIDED_LIMIT,
		)
	]


_DECIDED_COLLECTORS = (_decided_leave, _decided_timesheets, _decided_attendance_requests)


def _recently_decided(employee):
	"""What this approver decided in the last week, newest first.

	Best effort by design. A Timesheet's DocShare is removed the moment it
	is decided (P2-U7 scenario 8), and an Attendance Request's the moment it
	leaves Pending Manager, so a decided record is only still visible to a
	manager who can read it some other way -- the nested-set User Permission
	over their own reports. An approver who is nobody's manager sees their
	leave decisions here and nothing else, which is correct: the group is a
	receipt for work this user did, not a record they own.
	"""
	since = add_days(user_today(), -_DECIDED_DAYS)
	today = _as_date(user_today())
	decided = []
	# P5-U11: the same gate `_approval_summaries` needed for its line-manager
	# loop. A routed-role holder with no `Employee` role (IT Team, P5-KTD10)
	# has no DocPerm row on Leave Application, Timesheet or Attendance
	# Request at all, so `frappe.get_list` inside these collectors answers a
	# hard PermissionError rather than an empty list -- first reachable here
	# once `get_my_approvals` served a caller of that shape (P5-U11 is that
	# caller; HR Request has no decided-receipts collector of its own, see
	# `_DECIDED_COLLECTORS`, so a routed-role holder's receipt list is empty
	# rather than missing a kind).
	if "Employee" in frappe.get_roles():
		for collect in _DECIDED_COLLECTORS:
			decided.extend(collect(employee, since))

	decided.sort(key=lambda entry: entry["decided_on"] or "", reverse=True)
	for entry in decided:
		entry["age_days"] = _age_in_days(entry["decided_on"], today)
	return _with_photo_urls(decided[:_DECIDED_LIMIT])


@frappe.whitelist()
def get_overdue_approvals():
	"""U12 / R26. HR's Overdue tab: who is sitting on what, how long, and
	against which threshold, within the caller's admin scope (P6-R6).

	Same collector and grouping as the daily HR summary (U11), so the tab
	and the email never disagree. Owner groups come oldest item first; rows
	inside a group are oldest first."""
	rate_limit_per_user("get_overdue_approvals")
	if not _is_hr():
		frappe.throw(_("Only HR can see overdue approvals."), frappe.PermissionError)
	from helixhr.reminders import _summary_owners, collect_overdue

	def row(item):
		return {
			"kind": item["kind"],
			"route_kind": item["route_kind"],
			"name": item["name"],
			"title": item["title"],
			"employee_name": item["employee_name"],
			"age_days": item["age_days"],
			"threshold_days": item["threshold_days"],
		}

	today = getdate(user_today())
	groups = _summary_owners(collect_overdue(today), frappe.session.user, project=row)
	return {"groups": groups, "count": sum(len(group["items"]) for group in groups)}


@frappe.whitelist()
def get_approval_detail(kind, name):
	"""The evidence for one decision, loaded only when it is selected
	(P2-U7 step 2, P2-R17, P2-AE6).

	Authorized by exactly the rule that authorizes the decision itself --
	`_assert_may_act_on`, the same function `act_on_approval` calls. A
	manager who could not approve this record cannot read it here either,
	and the answer is a PermissionError rather than an empty projection, so
	the screen can say "you don't have access to this" instead of "nothing
	here".

	`frappe.get_doc` performs no read check of its own, which is why the
	assert is not optional.
	"""
	rate_limit_per_user("get_approval_detail")
	doctype = _APPROVAL_DOCTYPES.get(kind)
	if not doctype:
		frappe.throw(_("Not a valid request."))

	if not frappe.db.exists(doctype, name):
		frappe.throw(_(_APPROVAL_NOT_FOUND), frappe.PermissionError)

	doc = frappe.get_doc(doctype, name)
	_assert_may_act_on(doc)
	detail = _APPROVAL_KINDS[doctype]["detail"](doc)
	# P4-R1 / P4-KTD6: the outcomes that are legal for *this* record and
	# *this* approver, from the one rule table `act_on_approval` validates
	# against. The screen draws exactly this list, so it can never offer a
	# button the server would refuse.
	detail["actions"] = _allowed_actions(doc, frappe.session.user)
	return detail


# The three kinds, and the doctype each one names -- the alias the whitelisted
# `kind` parameters are validated against. Everything else that is per-kind
# lives in `_APPROVAL_KINDS`, keyed by the doctype, rather than in a map of its
# own per question (P3-U6 step 0, P3-U9).
_APPROVAL_DOCTYPES = {
	"leave": "Leave Application",
	"timesheet": "Timesheet",
	"attendance": "Attendance Request",
	"request": "HR Request",
	# Plan 2026-10-04-003 U3: the change request is a queue kind of its own.
	"change": "HelixHR Timesheet Change",
}

# One refusal for "missing", "not yours to decide" and "outside your company"
# alike, the way `_PAYSLIP_NOT_FOUND` already works: record names are
# sequential, so distinct messages would let any signed-in employee walk the
# id space and learn which records exist and whose they are.
_APPROVAL_NOT_FOUND = "That request isn't here."


# The nine outcomes, in the order the screen draws them: the decision, the
# recoverable no, the final no, the hand-over (P4-R1..R5), and the routed
# request's own three (Pick up, Need info, Done -- P5-U6). Timesheets reach
# three of the first four and never Reject (P4-KTD2); HR Request never
# reaches Approve or Send to HR at all -- both are facts of each workflow's
# own transitions rather than a rule written here. Accept and Decline are
# the change request's two (plan 2026-10-04-003 R13), and are never
# batchable (U6).
_APPROVAL_ACTIONS = (
	"Approve",
	"Send Back",
	"Reject",
	"Send to HR",
	"Pick up",
	"Need info",
	"Done",
	"Accept",
	"Decline",
)

# The four that are meaningless without one: the employee is told what to
# change, why the answer is final, or what is missing before it can be
# finished (P4-R3, P4-R4, P5-U6); a decline is told why (R12).
_REASON_REQUIRED = ("Send Back", "Reject", "Need info", "Decline")

# Where an approver's reason is stored, per kind. Leave keeps its Comment --
# its rows are never deleted -- while a rejected Attendance Request is
# removable by its employee and a Comment dies with the document, so those
# two carry the reason as a field of the record (P4-KTD7a, P4-KTD3). HR
# Request joins them for the same reason as Attendance Request: its own
# employee can read its Comments (P5-R10's conversation), so a decision
# reason has to live somewhere a rejection-comment reader would not
# mistake it for part of that conversation (P5-KTD14).
_DECISION_REASON_KINDS = ("Timesheet", "Attendance Request", "HR Request")

# Leave has no Workflow (P2-KTD17), so its half of the rule table is written
# here. HR decides, sends back or rejects in either stage; only a line
# manager hands over, and only from their own stage (P4-R5).
_LEAVE_DECISIONS = ("Approve", "Send Back", "Reject")


def _requester_user(doc):
	return frappe.db.get_value("Employee", doc.employee, "user_id")


def _allowed_actions(doc, user):
	"""The outcomes this user may take on this record, right now (P4-KTD6).

	One function, two entry points: `get_approval_detail` returns it as
	`actions` and `act_on_approval` refuses anything absent from it. There is
	no second copy of the rules on the screen or in the act.

	For Timesheet and Attendance Request the list is *derived* from the
	Workflow -- `frappe.model.workflow.get_transitions` applies the same
	roles and the same conditions `apply_workflow` will apply, filtered to
	the four outcome names. Editing a transition in Desk therefore changes
	the button row and the server's answer together, which is the whole point
	of deriving it. Only Leave uses the explicit table above.

	Nobody decides their own request, on any kind, in any state (P4-R8): the
	list is empty for the requester, and `_assert_may_act_on` refuses them
	before this is ever reached from `act_on_approval`. Administrator is
	exempt, as it is in the submit hooks -- it is the migration and backfill
	account, not a person with requests of their own.
	"""
	if not _APPROVAL_KINDS[doc.doctype]["is_open"](doc):
		return []
	if user != "Administrator" and _requester_user(doc) == user:
		return []
	if doc.doctype == "Leave Application":
		actions = _leave_allowed_actions(doc, user)
	elif doc.doctype == "HelixHR Timesheet Change":
		actions = _change_allowed_actions(doc, user)
	else:
		actions = _workflow_allowed_actions(doc, user)

	# P4-R5, and the dead end it would otherwise create: the only route out of
	# Pending HR is an HR Manager who is not the requester, so on a site with
	# no enabled HR Manager -- or where the only one is the person who raised
	# this record -- a hand-over leaves it with no legal move for anybody.
	# Withheld rather than offered.
	if "Send to HR" in actions and not _an_hr_manager_can_take_this(doc):
		actions = [action for action in actions if action != "Send to HR"]
	return actions


def _an_hr_manager_can_take_this(doc):
	"""Is there anybody who could actually decide this record once it is with
	HR: an enabled login holding HR Manager who is not this record's
	requester.

	The same notion of "enabled holder of HR Manager" `preflight._hr_manager_users`
	checks (P4-R11). The two-query shape is repeated here rather than imported
	because preflight is an operator command and this is a request path; both
	exclude Administrator and Guest -- Administrator is the migration and
	backfill account, not a person HR work can be handed to.

	`ignore_permissions` for the reason the holiday and directory reads give:
	an ordinary employee has no read on Has Role, this method owns the whole
	scope, and what leaves the function is a boolean.
	"""
	holders = set(
		frappe.get_all(
			"Has Role",
			filters={"role": "HR Manager", "parenttype": "User"},
			pluck="parent",
			ignore_permissions=True,
		)
	) - {"Administrator", "Guest", _requester_user(doc)}
	if not holders:
		return False
	return bool(
		frappe.get_all(
			"User",
			filters={"enabled": 1, "name": ["in", list(holders)]},
			pluck="name",
			limit=1,
			ignore_permissions=True,
		)
	)


def _workflow_allowed_actions(doc, user):
	"""The two workflow kinds' list, derived from the fixture's own
	transitions.

	Filtered through `has_approval_access` as well as by action name, because
	`apply_workflow` applies it too and it keys on `doc.owner`: a manager who
	*filed* a request for one of their reports would otherwise be offered the
	Employee-role outcomes (`allow_self_approval = 0`) and then refused with
	"Self approval is not allowed" on the click. P4-KTD6's promise is that the
	offered list and the enforced list cannot disagree.
	"""
	from frappe.model.workflow import get_transitions, has_approval_access

	reachable = {
		transition.get("action")
		for transition in get_transitions(doc)
		if transition.get("action") in _APPROVAL_ACTIONS
		and has_approval_access(user, doc, transition)
	}
	return [action for action in _APPROVAL_ACTIONS if action in reachable]


def _leave_allowed_actions(doc, user):
	"""The HTD table, as code. A user who is both HR and this employee's
	line manager gets the union -- minus Send to HR once the request is
	already with HR, because there is nowhere left to send it."""
	is_hr = _is_hr(user)
	is_manager = user == doc.leave_approver

	if (doc.get("helixhr_stage") or _LEAVE_STAGE_MANAGER) == LEAVE_STAGE_HR:
		return list(_LEAVE_DECISIONS) if is_hr else []
	if not (is_hr or is_manager):
		return []
	return [*_LEAVE_DECISIONS, "Send to HR"] if is_manager else list(_LEAVE_DECISIONS)


def _decision_head(doc, employee_name):
	for_hr = _APPROVAL_KINDS[doc.doctype]["hr_state"](doc)
	sender = (
		_hr_senders(
			doc.doctype,
			[{"name": doc.name, "employee": doc.employee, "modified_by": doc.modified_by}],
		)[doc.name]
		if for_hr
		else {"by": None, "note": None}
	)
	return {
		"name": doc.name,
		"doctype": doc.doctype,
		"employee": doc.employee,
		"employee_name": employee_name,
		"initials": _initials(employee_name),
		"photo_url": _employee_photo_url(doc.employee),
		# The concurrency token. The screen sends back the value it was
		# rendered from, and `act_on_approval` refuses anything else
		# (P2-R25, P2-U7 step 3).
		"modified": str(doc.modified),
		# The same three keys the queue row carries (P4-R11), so the detail
		# head can say "HR · sent by Priya · 'needs a policy check'" without
		# a second request.
		"for_hr": for_hr,
		"sent_to_hr_by": sender["by"],
		"hr_note": sender["note"],
	}


def _leave_decision_detail(doc):
	"""Reason, dates, day count and current status -- everything the
	approver needs before the balance is consumed (P2-U7 scenario 2)."""
	detail = _decision_head(doc, doc.employee_name)
	detail.update(
		{
			"kind": "leave",
			"state": doc.status,
			"status": doc.status,
			"docstatus": cint(doc.docstatus),
			"leave_type": doc.leave_type,
			"from_date": str(doc.from_date) if doc.from_date else None,
			"to_date": str(doc.to_date) if doc.to_date else None,
			"total_days": flt(doc.total_leave_days),
			"half_day": bool(cint(doc.half_day)),
			"half_day_date": str(doc.half_day_date) if doc.half_day_date else None,
			# The employee's own words for why they need the days.
			"reason": frappe.utils.strip_html(doc.description or "").strip() or None,
			"leave_balance": flt(doc.leave_balance),
			"sent_on": str(doc.creation) if doc.creation else None,
			"age_days": _age_in_days(doc.creation, _as_date(user_today())),
		}
	)
	return detail


def _timesheet_decision_detail(doc):
	"""Every row and every total on the week, which is what makes the
	decision a decision rather than a rubber stamp (P2-AE6).

	The rows are aggregated into one line per project/task with a cell per
	day, because that is the shape both the desktop grid and the phone
	strip read from -- one data model, two layouts.
	"""
	monday, sunday = get_week_bounds(doc.start_date)
	dates = [str(add_days(monday, offset)) for offset in range(7)]

	lines = {}
	day_totals = dict.fromkeys(dates, 0.0)
	notes = []
	for row in doc.time_logs:
		date = _row_date(row)
		hours = flt(row.hours)
		key = (row.project, row.task)
		line = lines.setdefault(
			key,
			{"project": row.project, "task": row.task, "hours_by_date": {}, "total": 0.0},
		)
		if date:
			line["hours_by_date"][date] = flt(line["hours_by_date"].get(date, 0)) + hours
			if date in day_totals:
				day_totals[date] += hours
		line["total"] += hours
		note = (row.description or "").strip()
		if note and note not in notes:
			notes.append(note)

	names = _project_and_task_names(lines)
	for (project, task), line in lines.items():
		line["project_name"] = names["projects"].get(project) or project
		line["task_subject"] = names["tasks"].get(task) or task

	detail = _decision_head(doc, doc.employee_name)
	detail.update(
		{
			"kind": "timesheet",
			"state": doc.workflow_state,
			"status": doc.workflow_state,
			"docstatus": cint(doc.docstatus),
			"week_start": str(monday),
			"week_end": str(sunday),
			"dates": dates,
			"lines": sorted(
				lines.values(), key=lambda line: (line["project_name"] or "", line["task_subject"] or "")
			),
			"day_totals": [{"date": date, "hours": flt(day_totals[date])} for date in dates],
			"total_hours": flt(doc.total_hours),
			"full_week_hours": FULL_WEEK_HOURS,
			# The employee's note, which is usually the explanation for
			# whatever looks odd in the grid.
			"note": " ".join(notes) or None,
			"sent_on": str(doc.modified) if doc.modified else None,
			"age_days": _age_in_days(doc.modified, _as_date(user_today())),
		}
	)
	return detail


def _attendance_decision_detail(doc):
	"""The days a request covers, what the calendar already shows for each of
	them, and the employee's own explanation -- everything P3-R16 says the
	manager reads before sending it to HR.

	`working_days_known` is false when no holiday list resolves for the
	employee, in which case the holiday column is unknowable rather than
	empty, and the screen says so instead of implying every day is a working
	day (the Attendance page's own "cannot tell yet" shape).
	"""
	detail = _decision_head(doc, doc.employee_name)
	start, end = _as_date(doc.from_date), _as_date(doc.to_date)
	_, holidays = _holiday_kinds(doc.employee, start, end)
	statuses = _attendance_status_by_date([doc.employee], start, end)
	detail.update(
		{
			"kind": "attendance",
			"state": doc.workflow_state,
			"status": doc.workflow_state,
			"docstatus": cint(doc.docstatus),
			"reason": doc.reason,
			"from_date": str(start),
			"to_date": str(end),
			"total_days": date_diff(end, start) + 1,
			"half_day": bool(cint(doc.half_day)),
			"half_day_date": str(doc.half_day_date) if doc.half_day_date else None,
			# The employee's own words. Kept separate from `reason`, which for
			# this kind is the HRMS reason code (Work From Home / On Duty).
			"explanation": (doc.explanation or "").strip() or None,
			"working_days_known": holidays is not None,
			"days": [
				{
					"date": day["date"],
					"status": day["status"],
					"holiday": (holidays or {}).get(day["date"]),
				}
				for day in _attendance_days(doc.employee, start, end, statuses)
			],
			"sent_on": str(doc.modified) if doc.modified else None,
			"age_days": _age_in_days(doc.modified, _as_date(user_today())),
		}
	)
	return detail


def _project_and_task_names(lines):
	"""Two queries for the whole week, not two per row (P2-R22)."""
	projects = {project for project, _ in lines if project}
	tasks = {task for _, task in lines if task}
	return {
		"projects": {
			row.name: row.project_name
			for row in frappe.get_all(
				"Project", filters={"name": ["in", list(projects)]}, fields=["name", "project_name"]
			)
		}
		if projects
		else {},
		"tasks": {
			row.name: row.subject
			for row in frappe.get_all(
				"Task", filters={"name": ["in", list(tasks)]}, fields=["name", "subject"]
			)
		}
		if tasks
		else {},
	}




@frappe.whitelist(methods=["POST"])
def act_on_approval(
	doctype, name, action, comment=None, expected_modified=None, expected_state=None
):
	"""Take one of the four outcomes on a report's pending request: Approve,
	Send Back, Reject or Send to HR (P4-R1..R5).

	`comment` is the approver's reason. It is **required** for Send Back and
	for Reject -- the employee is told what to change, or why the answer is
	final, not merely that it happened -- and optional for Send to HR, where
	it is the note HR reads. Approve takes none.

	Who may actually act is checked here on the server, not assumed from
	what the portal chose to show (R26). Timesheet goes through the same
	workflow transition the portal's own Submit/Edit actions use (its
	condition and before_submit guard are the real check); Leave
	Application has no Workflow (KTD17), so the equivalent check is
	explicit here.

	Order matters, and it is the P2-U1 fix. The sequence is: lock the
	native row, authorize, check the state the caller was looking at, and
	only then create any side effect. Before P2-U1 the comment was added
	first, so an unauthorized caller left a real Comment on somebody else's
	leave before the approver check refused them (P2-R10, P2-U1 step 9).

	`expected_modified` is **required** as of P2-U7: it is the `modified`
	value `get_approval_detail` handed the screen, so a decision is always
	made against evidence the caller actually saw. Optional was not enough
	-- a caller who simply omitted the token got the old unguarded write
	back, which is the entire failure mode P2-R25 exists to close.
	`expected_state` is the workflow state / status that came with it, and
	is compared too: it is the difference between "somebody edited this"
	and "somebody already decided this", and the manager is told which.
	"""
	rate_limit_per_user("act_on_approval")
	if doctype not in _APPROVAL_DOCTYPES.values():
		frappe.throw(_("Not a valid request."))
	if action not in _APPROVAL_ACTIONS:
		frappe.throw(_("Not a valid action."))
	reason = (comment or "").strip() or None
	if action == "Send Back" and not reason:
		frappe.throw(_("Say what should change before sending it back."))
	if action == "Reject" and not reason:
		frappe.throw(_("Say why before rejecting this."))
	if action == "Need info" and not reason:
		frappe.throw(_("Say what you need from them before asking."))
	# Plan 2026-10-04-003 R12: a decline without a reason is a dead end for
	# the employee, who sees exactly that reason afterwards.
	if action == "Decline" and not reason:
		frappe.throw(_("Say why before declining this."))
	if not expected_modified:
		frappe.throw(_("Open this request before deciding it, then try again."))

	return _decide_one(doctype, name, action, reason, expected_modified, expected_state)


def _decide_one(doctype, name, action, reason, expected_modified, expected_state):
	"""Everything `act_on_approval` does after its rate limit, for one
	record -- the sequence the batch endpoint re-runs per item (KTD6), so
	there is one authorization path and never a second copy of it.

	Order matters, and it is the P2-U1 fix. The sequence is: lock the
	native row, authorize, check the state the caller was looking at, and
	only then create any side effect. Before P2-U1 the comment was added
	first, so an unauthorized caller left a real Comment on somebody else's
	leave before the approver check refused them (P2-R10, P2-U1 step 9).
	"""
	if doctype not in _APPROVAL_DOCTYPES.values():
		frappe.throw(_("Not a valid request."))
	if action not in _APPROVAL_ACTIONS:
		frappe.throw(_("Not a valid action."))

	# SELECT ... FOR UPDATE on the one row: two concurrent decisions
	# serialize here, so the second one reads the first one's result and is
	# refused by the state check below rather than racing it (P2-U1 step 1).
	current_modified = frappe.db.get_value(doctype, name, "modified", for_update=True)
	if current_modified is None:
		frappe.throw(_(_APPROVAL_NOT_FOUND), frappe.PermissionError)

	doc = frappe.get_doc(doctype, name)
	_assert_may_act_on(doc)
	_assert_still_open(doc)
	# P4-R1 / P4-KTD6: the same list the screen was drawn from. For the two
	# workflow kinds this is the fixture's own roles and conditions, so an
	# action the workflow would technically allow from some other role is
	# still refused here, and vice versa.
	if action not in _allowed_actions(doc, frappe.session.user):
		frappe.throw(_("That isn't something you can do to this request."))
	_assert_expected_state(expected_modified, current_modified)
	_assert_expected_workflow_state(doc, expected_state)

	# Before the transition, so the employee's notification carries the
	# reason the transition writes (P4-R9, P3-KTD9).
	if reason:
		if action in _REASON_REQUIRED and doctype in _DECISION_REASON_KINDS:
			# `db_set` because the field is permlevel 1 (P4-KTD7a): a save by
			# this approver, who is an ordinary employee with a DocShare,
			# would have the value silently reset on the way in. The
			# authorization above is what makes this safe.
			doc.db_set(DECISION_REASON_FIELD, reason)
		# P4-R5: a hand-over note is marked, so no employee-facing reason
		# reader can ever return it (see HR_HANDOVER_NOTE_PREFIX).
		doc.add_comment(
			"Comment",
			f"{HR_HANDOVER_NOTE_PREFIX} {reason}" if action == "Send to HR" else reason,
		)

	# U9: leave keeps its reason as a Comment, so the decision email reads it
	# from here (`events.leave_application_on_submit` / `_on_update`).
	doc.flags.helixhr_decision_note = reason
	_APPROVAL_KINDS[doctype]["act"](doc, action)
	_record_hr_acting_for_approver(doc, action)
	return {
		"name": doc.name,
		"action": action,
		"state": doc.get(_APPROVAL_KINDS[doctype]["state_field"]),
	}


_BULK_MAX_ITEMS = 60


@frappe.whitelist(methods=["POST"])
def approve_clean_items(items):
	"""Approve several clean items in one call (plan 2026-10-04-003 U6,
	R17, R18).

	`items` is `[{doctype, name, expected_modified, expected_state}]`, at
	most 60, and only Timesheet and Leave Application -- the two kinds the
	queue marks "Looks normal". Everything else about the batch is the
	per-item path: each item is re-authorized by `_decide_one`, its
	concurrency token is checked, and the R16 flags are **recomputed on the
	server** first -- a flagged item is refused, so the browser can never
	slip a "Needs a look" row through by sending it anyway (KTD6).

	R18: one stale or refused item never stops the rest. Each item commits
	on its own, so a failure later in the list cannot undo an earlier
	approval, and the answer is a per-item `{name, ok, message}` the screen
	reports verbatim.
	"""
	rate_limit_per_user("approve_clean_items")
	if isinstance(items, str):
		items = json.loads(items)
	if not isinstance(items, list) or not items:
		frappe.throw(_("Pick at least one item to approve."))
	if len(items) > _BULK_MAX_ITEMS:
		frappe.throw(_("Approve up to {0} items at once.").format(_BULK_MAX_ITEMS))

	results = []
	for item in items:
		name = (item or {}).get("name")
		doctype = (item or {}).get("doctype")
		try:
			if doctype not in ("Timesheet", "Leave Application"):
				frappe.throw(_("Only timesheets and leave can be approved together."))
			if _item_flags(doctype, name):
				frappe.throw(_("This one needs a look, so it can't be approved with the rest."))
			result = _decide_one(
				doctype,
				name,
				"Approve",
				None,
				item.get("expected_modified"),
				item.get("expected_state"),
			)
			# Commit per item (R18): a later failure must not undo this one.
			frappe.db.commit()
			results.append({"name": name, "ok": True, "state": result["state"]})
		except Exception as exc:
			frappe.db.rollback()
			message = frappe.utils.strip_html(str(exc)).strip() or _("This one could not be approved.")
			results.append({"name": name, "ok": False, "message": message})
	return results


def _item_flags(doctype, name):
	"""The R16 flags for one item, recomputed on the server (KTD6).

	The batch endpoint never trusts the client's "clean" claim: the same
	helpers the queue shipped the flags with run again here, per item. An
	empty dict is "looks normal"; anything else is "needs a look"."""
	today = getdate(user_today())
	if doctype == "Timesheet":
		row = frappe.db.get_value(
			"Timesheet",
			name,
			["employee", "start_date", "total_hours", "amended_from", "helixhr_decision_reason", PENDING_SINCE_FIELD],
			as_dict=True,
		)
		if not row:
			frappe.throw(_(_APPROVAL_NOT_FOUND), frappe.PermissionError)
		monday, sunday = get_week_bounds(row.start_date)
		index = _working_days_index([row.employee], monday, sunday)
		working = _employee_working_days(index, row.employee, monday, sunday)
		expected = flt(index["standard"] * len(working)) if index["standard"] and working else None
		day_hours, _split = _team_time_logs([name])
		return _timesheet_flags_for(
			row, working, expected, day_hours.get(name, {}), today, approval_overdue_days()
		)
	if doctype == "Leave Application":
		row = frappe.db.get_value(
			"Leave Application",
			name,
			["employee", "leave_type", "from_date", "to_date", "total_leave_days", "helixhr_stage"],
			as_dict=True,
		)
		if not row:
			frappe.throw(_(_APPROVAL_NOT_FOUND), frappe.PermissionError)
		try:
			result = leave_overdraw(row.employee, row.leave_type, row.from_date, row.to_date, 0)
		except Exception:
			result = None
		balance = flt(result["balance"]) - flt(row.total_leave_days) if result else None

		# The overlap count against this caller's other reports, the same
		# notion the queue's leave collector uses (R15).
		employee = get_current_employee()
		scope = _line_manager_filter(employee)
		overlap = 0
		if scope:
			if scope[0] == "in":
				others = [name for name in scope[1] if name != row.employee]
				employee_filter = ["in", others] if others else None
			else:
				employee_filter = ["!=", row.employee]
			if employee_filter:
				overlap = len(
					frappe.get_all(
						"Leave Application",
						filters={
							"employee": employee_filter,
							"docstatus": ["<", 2],
							"status": ["in", ["Open", "Approved"]],
							"from_date": ["<=", str(row.to_date)],
							"to_date": [">=", str(row.from_date)],
						},
						pluck="name",
						ignore_permissions=True,
					)
				)
		return _leave_flags_for(
			{"for_hr": (row.helixhr_stage or _LEAVE_STAGE_MANAGER) == LEAVE_STAGE_HR, "from_date": row.from_date},
			balance,
			overlap,
			today,
		)
	return {}


def _record_hr_acting_for_approver(doc, action):
	"""U4 / R11: HR decided a manager-stage leave in the approver's place.
	The timeline says so, and the approver gets a bell row. Runs after the
	decision, so a refused action leaves neither behind."""
	if doc.doctype != "Leave Application" or action == "Send to HR":
		return
	user = frappe.session.user
	approver = doc.leave_approver
	if not approver or approver == user or not _is_hr(user):
		return
	if (frappe.db.get_value("Leave Application", doc.name, "helixhr_stage") or _LEAVE_STAGE_MANAGER) != (
		_LEAVE_STAGE_MANAGER
	):
		return
	approver_name = frappe.utils.get_fullname(approver)
	doc.add_comment("Info", _("Decided by HR for {0}.").format(approver_name))
	frappe.get_doc(
		{
			"doctype": "Notification Log",
			"for_user": approver,
			"from_user": user,
			"type": "Alert",
			"document_type": doc.doctype,
			"document_name": doc.name,
			"subject": _("HR decided {0}'s leave request for you: {1}.").format(doc.employee_name, action),
		}
	).insert(ignore_permissions=True)


def _act_through_workflow(doc, action):
	"""Timesheet and Attendance Request both move through their own Workflow,
	whose transition condition and role are the real check; this runs the
	same transition the Desk actions run."""
	from frappe.model.workflow import apply_workflow

	apply_workflow(doc, action)


def _may_act_on_leave(doc, user):
	"""Who may decide one leave request (P4-R5, P4-R7).

	Only reached for a non-HR session -- `_assert_may_act_on` returns early
	for `_is_hr` -- so the stage check is the whole of "the manager loses the
	request once it is with HR". The raw route (the `submit=1` DocShare HRMS
	grants the approver on every save) is closed by
	`events.leave_application_before_submit`, not here.
	"""
	if doc.get("helixhr_stage") == LEAVE_STAGE_HR:
		frappe.throw(
			_("This leave request is with HR now, so only HR can decide it."),
			frappe.PermissionError,
		)
	if user != doc.leave_approver:
		frappe.throw(
			_(_APPROVAL_NOT_FOUND),
			frappe.PermissionError,
		)


def _may_act_on_timesheet(doc, user):
	# The same rule events.timesheet_before_submit enforces on submit,
	# applied here so a Reject (which never submits) and the comment that
	# goes with it are covered by it too.
	if user != get_manager_user(doc.employee):
		frappe.throw(
			_(_APPROVAL_NOT_FOUND),
			frappe.PermissionError,
		)


def _may_act_on_attendance_request(doc, user):
	"""`events._approver_user` is the single source for who the manager is
	(P3-KTD7): stricter than `get_manager_user` because it also requires the
	manager's own Employee record to be Active, which is what the DocShare
	and the workflow condition already assume."""
	if user != _approver_user(doc.employee):
		frappe.throw(
			_(_APPROVAL_NOT_FOUND),
			frappe.PermissionError,
		)


def _assert_may_act_on(doc):
	"""Refuse anyone but this record's own approver (or HR) before a single
	side effect runs (P2-U1 step 9).

	`events._is_hr` is the one definition of "HR" in this app (Administrator, HR
	Manager or System Manager); it is not repeated here. The doctype is indexed
	directly: both entry points -- `get_approval_detail` and `act_on_approval` --
	have already refused anything that is not one of `_APPROVAL_DOCTYPES`, so a
	missing key here would be a programming error, not a caller's input.

	P4-R8: the "not your own request" refusal runs *before* the HR
	short-circuit. It used to sit inside the per-kind checks, which HR never
	reaches, so an HR Manager could send back, reject or escalate their own
	leave through the portal. Administrator is exempt, as it is in the three
	`before_submit` hooks that cover the raw routes.
	"""
	user = frappe.session.user
	if user != "Administrator" and _requester_user(doc) == user:
		frappe.throw(
			_("You can't decide your own request. Ask your manager or HR."),
			frappe.PermissionError,
		)
	if _is_hr(user):
		# HR's reach is the same one every administrative read uses
		# (`resolve_admin_scope`, P6-R6): a company-anchored HR Manager
		# decides -- and reads the evidence for -- their own company's
		# records and no other's. Before this, "is HR" alone was the whole
		# check, and the list routes were scoped while the record routes were
		# not. The refusal is the same words as for a missing record, so this
		# endpoint cannot be used to learn whether a record exists.
		if not employee_in_admin_scope(doc.employee, resolve_admin_scope(user)):
			frappe.throw(_APPROVAL_NOT_FOUND, frappe.PermissionError)
		return

	_APPROVAL_KINDS[doc.doctype]["may_act"](doc, user)


def _assert_still_open(doc):
	"""One decision per record. A second decision -- the losing half of a
	concurrent approve/approve or approve/reject -- is refused here, before
	it can add a contradicting comment or a second ledger effect."""
	kind = _APPROVAL_KINDS[doc.doctype]
	if not kind["is_open"](doc):
		# Plan 2026-10-04-003 R6: a week sitting at Draft while the approver
		# was looking at Pending Approval is a recall, not a decision -- say
		# which, because "already decided" sends them hunting for a decision
		# that never happened.
		if doc.doctype == "Timesheet" and doc.get("workflow_state") == "Draft":
			frappe.throw(_("The employee recalled this week. Reload to see it."))
		frappe.throw(_(kind["open_message"]))


def _assert_expected_state(expected_modified, current_modified):
	if not expected_modified:
		return
	if get_datetime(expected_modified) != get_datetime(current_modified):
		frappe.throw(_("Somebody changed this while you were looking at it. Reload and try again."))


def _assert_expected_workflow_state(doc, expected_state):
	"""The second half of the token (P2-U7 step 3). `modified` says the row
	moved; this says what it moved *to*, which is the difference between a
	harmless edit and a decision somebody else already made."""
	if not expected_state:
		return
	current = doc.get(_APPROVAL_KINDS[doc.doctype]["state_field"])
	if expected_state != current:
		frappe.throw(_("This has already been decided. Reload to see the result."))


def _act_on_leave_application(doc, action):
	"""Run the native HRMS lifecycle (P2-R10, P2-AE1).

	An approval *submits* the application, which is what makes HRMS write
	the Leave Ledger Entry, consume balance and update attendance. Before
	P2-U1 this only set `status = "Approved"` and saved, so the portal
	said "Approved" while the leave was never taken from the balance.

	A rejection deliberately stays at docstatus 0: an unsubmitted
	application consumes nothing, and HRMS's own on_submit refuses any
	status but Approved/Rejected anyway.

	`ignore_permissions` on the write, because the native grant is not
	enough. HRMS's `status` is permlevel 1, writable only by Leave Approver,
	HR User and HR Manager, and HRMS grants Leave Approver only to a user
	named on `Employee.leave_approver`. A Department approver -- the
	fallback `get_employee_leave_approver` resolves -- holds just the
	`submit=1` DocShare `hrms.hr.utils.share_doc_with_approver` creates, so
	Frappe silently reset `status` to Open on save: Approve and Reject died
	in HRMS's on_submit and Send Back needed a write the share never gave.
	The bypass is safe here only because `_decide_one` has already locked,
	authorized and state-checked this exact record; the helixhr
	`before_submit` / `validate` guards and every HRMS validation still run.
	See test_an_approver_without_the_leave_approver_role_can_still_decide.

	P4-U2 gives the same function the other three outcomes. Send back is
	unchanged; Reject is the *submitted* twin of it -- HRMS's own on_submit
	accepts Approved and Rejected, a submitted application writes no Leave
	Ledger Entry unless it is Approved, and docstatus 1 is what makes the row
	unresendable (P4-R4). Send to HR touches no HRMS field at all: it moves
	`helixhr_stage`, which is permlevel 1, so it goes through `db_set` after
	the authorization `act_on_approval` has already done (P4-KTD4).
	"""
	doc.flags.ignore_permissions = True
	if action == "Approve":
		doc.status = "Approved"
		doc.submit()
	elif action == "Reject":
		doc.status = "Rejected"
		doc.submit()
	elif action == "Send to HR":
		doc.db_set("helixhr_stage", LEAVE_STAGE_HR)
	elif action == "Send Back":
		doc.status = "Rejected"
		doc.save()
	else:
		frappe.throw(_("Not a valid action."))


def _act_on_attendance_request(doc, action):
	"""The workflow transition, with the overwrite gate in front of Approve
	(P4-KTD5).

	Single-step approval makes the manager's Approve a real docstatus 0 -> 1
	submit, and HRMS's `on_submit` rewrites existing Attendance rows in
	place. P3-U6's preview refuses an overwrite at *send* time, but
	auto-attendance can mark a day Present between the send and the
	decision, and the person now submitting is a line manager who cannot see
	Attendance at all. So the preview is re-run against the stored range and
	the manager is pointed at the one person who can weigh an overwrite.

	HR is deliberately not gated: HR can read Attendance, and leaving the
	overwrite decision with them is what the sentence below asks for.

	This is the portal's copy of the refusal, kept for its sentence alone.
	The *enforcement* is `events.attendance_request_before_submit`, which
	every submit route passes through -- P4-KTD5's `submit=1` DocShare means
	a line manager can reach `apply_workflow` and `frappe.client.submit` from
	Desk, neither of which comes through here.
	"""
	if action == "Approve" and not _is_hr():
		assert_no_attendance_overwrite(
			doc.employee,
			doc.from_date,
			doc.to_date,
			half_day=doc.half_day,
			half_day_date=doc.half_day_date,
			reason=doc.reason,
		)

	_act_through_workflow(doc, action)


def assert_no_attendance_overwrite(
	employee, from_date, to_date, half_day=0, half_day_date=None, reason="Work From Home"
):
	"""Refuse a non-HR approval of days that already carry attendance, or
	that cannot be judged at all (P4-KTD5).

	Called from two places on purpose: the portal's `_act_on_attendance_request`
	(for the message a manager reads) and `events.attendance_request_before_submit`
	(the choke point every submit route -- portal, `apply_workflow`,
	`frappe.client.submit` -- actually passes through). One function so the two
	cannot drift.

	An unanswerable preview (`known` false: no holiday list resolves) is
	treated as an overwrite rather than as "no overwrite". HRMS's own
	`is_holiday(..., raise_exception=True)` throws a few lines later anyway,
	so the choice is between our sentence and a raw Frappe error -- and the
	honest answer is the same one `can_send` gives at send time: this needs
	HR.
	"""
	preview = _attendance_request_preview(
		employee,
		_as_date(from_date),
		_as_date(to_date),
		half_day=half_day,
		half_day_date=half_day_date,
		reason=reason,
	)
	if not preview["known"]:
		frappe.throw(
			_(
				"We can't tell which of those days are working days, so this one is HR's "
				"call. Send it to HR instead."
			)
		)
	if preview["overwrite"]:
		frappe.throw(_("Some of those days now have attendance; send this to HR instead."))


def _may_act_on_hr_request(doc, user):
	"""Only a holder of this request's **stored** `routed_to_role` may act on
	it (P5-R5, P5-R9). Reached only for a non-HR session -- `_assert_may_act_on`
	returns early for `_is_hr()` -- so a request stamped to HR Manager is only
	ever decided by HR here, never by an IT Team holder, whatever roles they
	both happen to hold."""
	if doc.routed_to_role not in frappe.get_roles(user):
		frappe.throw(
			_(_APPROVAL_NOT_FOUND),
			frappe.PermissionError,
		)


def _act_on_hr_request(doc, action):
	"""The fixture's own transitions are the rule (P5-KTD4); this only runs
	the one the caller was already authorised and validated for."""
	_act_through_workflow(doc, action)


def _request_thread(doc):
	"""The request and every reply after it, oldest first, as one
	conversation (P5-R10).

	The employee's opening message is a field of the record; everything
	after it is a Comment -- the routed role's `Need info`/`Reject` reasons
	(written by `act_on_approval`'s existing, doctype-generic reason code)
	and the employee's own replies (`reply_to_my_request`) land there the
	same way, told apart only by who wrote them. Filtered the way
	`_rejection_comments` filters a hand-over note: a `Send to HR` prefix can
	never appear on this doctype's own transitions, but the guard costs
	nothing and keeps the rule in one place rather than assuming it.
	"""
	employee_user = frappe.db.get_value("Employee", doc.employee, "user_id")
	entries = []
	opening = frappe.utils.strip_html(doc.details or "").strip()
	if opening:
		entries.append({"by": "employee", "message": opening, "on": str(doc.creation)})

	for row in frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": "HR Request",
			"reference_name": doc.name,
			"comment_type": "Comment",
		},
		fields=["content", "owner", "creation"],
		order_by="creation asc",
		limit=_COMMENT_FETCH,
	):
		text = frappe.utils.strip_html(row.content or "").strip()
		if not text or text.startswith(HR_HANDOVER_NOTE_PREFIX):
			continue
		entries.append(
			{
				"by": "employee" if employee_user and row.owner == employee_user else "worker",
				"message": text,
				"on": str(row.creation),
			}
		)
	return entries


def _request_decision_detail(doc):
	"""The evidence a worker needs to decide a routed request: what the
	employee wrote, the category it came in under, and the conversation so
	far (P5-R10, P5-R11)."""
	employee_name = frappe.db.get_value("Employee", doc.employee, "employee_name") or doc.employee
	return {
		"name": doc.name,
		"doctype": doc.doctype,
		"employee": doc.employee,
		"employee_name": employee_name,
		"initials": _initials(employee_name),
		"photo_url": _employee_photo_url(doc.employee),
		"modified": str(doc.modified),
		# P4-KTD7's tag, carried by a fourth kind for the first time: true
		# only when the stored route is HR Manager, never for IT Team.
		"for_hr": doc.routed_to_role == "HR Manager",
		"sent_to_hr_by": None,
		"hr_note": doc.hr_note,
		"kind": "request",
		"state": doc.status,
		"status": doc.status,
		"docstatus": cint(doc.docstatus),
		"category": doc.category,
		"subject": doc.subject,
		"routed_to_role": doc.routed_to_role,
		"picked_up_by": doc.picked_up_by,
		"decision_reason": doc.get(DECISION_REASON_FIELD),
		"sent_on": str(doc.creation) if doc.creation else None,
		"age_days": _age_in_days(doc.creation, _as_date(user_today())),
		"thread": _request_thread(doc),
		"correction": _correction_summary(doc),
		# U14: the files on the request -- for a correction, the proof HR
		# checks before Done. Private; File's own read check gates download.
		"attachments": [
			_attachment(row)
			for row in frappe.get_all(
				"File",
				filters={"attached_to_doctype": "HR Request", "attached_to_name": doc.name},
				fields=["name", "file_name", "file_url", "file_size", "is_private"],
				order_by="creation asc",
			)
		],
	}


def _correction_summary(doc):
	"""What a correction proposes, masked only (plan 2026-10-02-001 U14, R28).
	None for an ordinary request. The full value is `reveal_correction_value`'s
	alone, so neither Password field is read here."""
	if not doc.get("correction_field"):
		return None
	return {
		"field": doc.correction_field,
		"label": PROFILE_CORRECTABLE_FIELDS.get(doc.correction_field, doc.correction_field),
		"current_masked": doc.correction_current_masked,
		"proposed_masked": doc.correction_proposed_masked,
	}


# Everything that is per-kind about a decision, in one doctype-keyed table
# (P3-U6 step 0, P3-U9). It replaced five parallel maps over the same three
# doctypes -- five places a fourth kind could be half-registered, which is the
# `if timesheet else leave` failure mode in a different shape. One table, one
# completeness test.
#
#   state_field    where the kind's lifecycle lives, which is what the
#                  stale-decision token compares (P2-U7 step 3)
#   detail         the evidence `get_approval_detail` returns
#   may_act        who may open or decide this record, checked on the server
#                  on every read of a detail and every action (P2-R10, R26)
#   is_open        the states in which the portal still has a decision to
#                  offer -- the manager's *and* HR's, since P4-U3 gives HR a
#                  queue inside the portal -- and `open_message` the sentence
#                  for a caller who arrives after both have moved. Which of
#                  the two an individual caller may act in is
#                  `_allowed_actions`'s answer, not this one (P4-KTD6).
#   hr_state       whether this record is waiting for HR right now, which is
#                  what tags a queue row and a detail head (P4-R11)
#   hr_queue       the HR half of the queue for this kind, oldest first
#   act            the lifecycle a decision actually runs
_APPROVAL_KINDS = {
	"Leave Application": {
		"state_field": "status",
		"detail": _leave_decision_detail,
		"may_act": _may_act_on_leave,
		"is_open": lambda doc: cint(doc.docstatus) == 0 and doc.status == "Open",
		"open_message": "This leave request has already been decided. Reload to see the result.",
		"hr_state": lambda doc: (doc.get("helixhr_stage") or _LEAVE_STAGE_MANAGER)
		== LEAVE_STAGE_HR,
		"hr_queue": _hr_leave_summaries,
		"act": _act_on_leave_application,
	},
	"Timesheet": {
		"state_field": "workflow_state",
		"detail": _timesheet_decision_detail,
		"may_act": _may_act_on_timesheet,
		"is_open": lambda doc: doc.workflow_state in (PENDING_STATE, TIMESHEET_PENDING_HR),
		"open_message": "This timesheet has already been decided. Reload to see the result.",
		"hr_state": lambda doc: doc.workflow_state == TIMESHEET_PENDING_HR,
		"hr_queue": _hr_timesheet_summaries,
		"act": _act_through_workflow,
	},
	"Attendance Request": {
		"state_field": "workflow_state",
		"detail": _attendance_decision_detail,
		"may_act": _may_act_on_attendance_request,
		"is_open": lambda doc: cint(doc.docstatus) == 0
		and doc.workflow_state in (REQUEST_PENDING_MANAGER, REQUEST_PENDING_HR),
		"open_message": "This attendance request has already been decided. Reload to see the result.",
		"hr_state": lambda doc: doc.workflow_state == REQUEST_PENDING_HR,
		"hr_queue": _hr_attendance_request_summaries,
		"act": _act_on_attendance_request,
	},
	"HR Request": {
		"state_field": "status",
		"detail": _request_decision_detail,
		"may_act": _may_act_on_hr_request,
		"is_open": lambda doc: doc.status in (
			HR_REQUEST_OPEN,
			HR_REQUEST_IN_PROGRESS,
			HR_REQUEST_WAITING_ON_EMPLOYEE,
		),
		"open_message": "This request has already been finished. Reload to see the result.",
		"hr_state": lambda doc: doc.routed_to_role == "HR Manager",
		"hr_queue": _hr_request_summaries,
		"act": _act_on_hr_request,
	},
	"HelixHR Timesheet Change": {
		"state_field": "status",
		"detail": _change_request_decision_detail,
		"may_act": _may_act_on_timesheet_change,
		"is_open": lambda doc: doc.status == "Open",
		"open_message": "This change request has already been decided. Reload to see the result.",
		"hr_state": lambda doc: not doc.approver_user,
		"hr_queue": _hr_change_request_summaries,
		"act": _act_on_timesheet_change,
	},
}


# Documents (R19, P2-R19)


_DOCUMENT_DOCTYPE = "HelixHR Document Link"
_DOCUMENT_CATEGORIES = ("Important", "General")
_DOCUMENT_TITLE_MAX = 140
_DOCUMENT_DESCRIPTION_MAX = 1000


def _visible_document_links(employee):
	"""The documents one employee may see: global ones plus their own
	company's (P2-R19), newest first.

	One query, two callers -- `get_my_documents` for the full searchable page
	and the dashboard's bounded card -- so the scope has one definition.
	`published_on` falls back to the creation date for a row nobody dated.
	`file` is a private File URL; Frappe serves it only to a reader of the
	link (its `has_permission` hook), so handing it out widens nothing.
	"""
	company = frappe.db.get_value("Employee", employee, "company")
	rows = frappe.get_all(
		_DOCUMENT_DOCTYPE,
		or_filters=[["company", "is", "not set"], ["company", "=", company]],
		fields=[
			"name",
			"title",
			"url",
			"file",
			"company",
			"description",
			"category",
			"published_on",
			"creation",
		],
		order_by="published_on desc, creation desc",
	)
	for row in rows:
		row.published_on = row.published_on or getdate(row.creation)
		row.category = row.category or "General"
		del row["creation"]
	return rows


def _get_documents_card(employee):
	"""The rail card: the first `_LINKS_LIMIT` documents, Important ones
	first, newest first within each, and how many were not shown."""
	links = sorted(_visible_document_links(employee), key=lambda row: row.category != "Important")
	shown = links[:_LINKS_LIMIT]
	return {"items": shown, "more": max(0, len(links) - len(shown))}


@frappe.whitelist()
def get_my_documents():
	"""The documents this employee may see: global ones plus their own
	company's (P2-R19), newest first.

	The scope is not this method's only enforcement -- HelixHR Document
	Link registers `permission_query_conditions` and `has_permission`
	(hooks.py), so a caller reaching for frappe.client.get_list,
	/api/resource, report view, print or export gets the same answer. This
	method exists so the portal asks a session-scoped question instead of
	sending the filter itself (KTD5, R27). Whether the caller may publish is
	the bootstrap's `can_manage_documents`.
	"""
	return _visible_document_links(get_current_employee())


def _can_manage_documents(user=None):
	"""Whether `save_document_link` would accept *some* company from this
	caller: HR, and either System Manager or holding an admin scope -- the
	same rule as `_assert_can_manage_document`, asked without a company."""
	user = user or frappe.session.user
	if not _is_hr(user):
		return False
	if "System Manager" in frappe.get_roles(user):
		return True
	return resolve_admin_scope(user)["kind"] != "none"


def _assert_can_manage_document(company):
	"""The publish gate per company: HR only; an anchored HR Manager for
	their own company (so never a global, blank company row), a System
	Manager for any."""
	if not _is_hr():
		frappe.throw(_("You don't have permission to do that."), frappe.PermissionError)
	if "System Manager" not in frappe.get_roles():
		_assert_company_in_admin_scope(company or None)


@frappe.whitelist()
def get_document_admin_options():
	"""The upload dialog's company choices for this caller, and whether a
	document "for everyone" (no company) is theirs to publish."""
	if not _can_manage_documents():
		frappe.throw(_("You don't have permission to do that."), frappe.PermissionError)
	sees_all = (
		"System Manager" in frappe.get_roles()
		or resolve_admin_scope(frappe.session.user)["kind"] == "unscoped"
	)
	companies = (
		frappe.get_all("Company", pluck="name", order_by="company_name asc")
		if sees_all
		else _companies_in_admin_scope()
	)
	return {"companies": companies, "allow_global": sees_all}


def _document_upload(file_url=None):
	"""(file name, bytes) of the document this save carries, or (None, None).
	A multipart `file` (the portal) wins over `file_url` (an agent reusing a
	File it can already read); both pass the same document policy."""
	upload = (getattr(frappe.request, "files", None) or {}).get("file")
	if upload is not None:
		file_name = os.path.basename(upload.filename or "").strip()
		if not file_name:
			frappe.throw(_("That file has no name. Pick another one."))
		return file_name, upload.stream.read()

	file_url = (file_url or "").strip()
	if not file_url:
		return None, None
	source = frappe.db.get_value("File", {"file_url": file_url, "is_folder": 0}, "name")
	if not source:
		frappe.throw(_("That file does not exist."))
	source = frappe.get_doc("File", source)
	if not source.has_permission("read"):
		frappe.throw(_("You don't have permission to do that."), frappe.PermissionError)
	# `encodings=[]`: raw bytes. The default tries text encodings first, and
	# a zip that happens to decode would come back as a different byte string.
	return source.file_name, source.get_content(encodings=[])


def _remove_document_files(name, keep=None):
	"""Delete the File(s) a document link's `file` field once pointed at."""
	for file_name in frappe.get_all(
		"File",
		filters={"attached_to_doctype": _DOCUMENT_DOCTYPE, "attached_to_name": name, "attached_to_field": "file"},
		pluck="name",
	):
		if file_name != keep:
			# The publish gate is this path's authorisation, as for the logo.
			frappe.delete_doc("File", file_name, ignore_permissions=True)


@frappe.whitelist(methods=["POST"])
def save_document_link(
	name=None,
	title=None,
	description=None,
	category=None,
	company=None,
	url=None,
	published_on=None,
	file_url=None,
):
	"""Create or edit one document on the Documents page (HR).

	The document is an uploaded file -- multipart `file`, or `file_url` of
	an existing File the caller can read -- or a web `url`, never both: a new
	file clears the link, a link clears the file, and neither keeps whatever
	the row already had. The file is checked by signature against
	`DOCUMENT_POLICY` (20 MB), stored **private** and attached to this row,
	so Frappe serves it exactly to the people who may read the row.

	Gate: `_assert_can_manage_document` for the target company and, on an
	edit, for the row's current company too -- an HR Manager can neither
	reach into another company's row nor move one into theirs.
	"""
	from helixhr.utils import validate_document_upload

	rate_limit_per_user("save_document_link")
	company = (company or "").strip() or None
	_assert_can_manage_document(company)
	if company and not frappe.db.exists("Company", company):
		frappe.throw(_("That company does not exist."))

	title = (title or "").strip()
	if not title:
		frappe.throw(_("Give the document a title."))
	if len(title) > _DOCUMENT_TITLE_MAX:
		frappe.throw(_("Keep the title under {0} characters.").format(_DOCUMENT_TITLE_MAX))
	description = (description or "").strip()
	if len(description) > _DOCUMENT_DESCRIPTION_MAX:
		frappe.throw(_("Keep the description under {0} characters.").format(_DOCUMENT_DESCRIPTION_MAX))
	if category not in _DOCUMENT_CATEGORIES:
		frappe.throw(_("Choose Important or General."))
	try:
		published_on = getdate(published_on) if published_on else None
	except Exception:
		frappe.throw(_("That date isn't valid."))

	if name:
		if not frappe.db.exists(_DOCUMENT_DOCTYPE, name):
			frappe.throw(_("That document no longer exists."), frappe.DoesNotExistError)
		doc = frappe.get_doc(_DOCUMENT_DOCTYPE, name)
		_assert_can_manage_document(doc.company)
	else:
		doc = frappe.new_doc(_DOCUMENT_DOCTYPE)

	file_name, content = _document_upload(file_url)
	if content is not None:
		validate_document_upload(file_name, content)
	url = (url or "").strip()

	doc.update(
		{
			"title": title,
			"description": description,
			"category": category,
			"company": company,
			"published_on": published_on or doc.published_on or frappe.utils.today(),
		}
	)
	if content is not None:
		doc.url = None
		doc.file = None
		doc.flags.file_pending = True
	elif url:
		doc.url = url
		doc.file = None
	doc.save()

	if content is not None:
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": file_name,
				"content": content,
				"attached_to_doctype": _DOCUMENT_DOCTYPE,
				"attached_to_name": doc.name,
				"attached_to_field": "file",
				# Never a parameter; `events.file_before_insert` refuses a
				# public one anyway.
				"is_private": 1,
			}
		)
		file_doc.insert(ignore_permissions=True)
		doc.db_set("file", file_doc.file_url)
		_remove_document_files(doc.name, keep=file_doc.name)
	elif url:
		_remove_document_files(doc.name)

	return frappe.db.get_value(
		_DOCUMENT_DOCTYPE,
		doc.name,
		["name", "title", "url", "file", "company", "description", "category", "published_on"],
		as_dict=True,
	)


@frappe.whitelist(methods=["POST"])
def delete_document_link(name):
	"""Remove one document and its uploaded file (HR, same gate as the save)."""
	rate_limit_per_user("delete_document_link")
	company = frappe.db.get_value(_DOCUMENT_DOCTYPE, name, "company")
	if company is None and not frappe.db.exists(_DOCUMENT_DOCTYPE, name):
		frappe.throw(_("That document no longer exists."), frappe.DoesNotExistError)
	_assert_can_manage_document(company)
	_remove_document_files(name)
	frappe.delete_doc(_DOCUMENT_DOCTYPE, name)
	return {"name": name, "deleted": True}


# ---------------------------------------------------------------------------
# HR Requests (P2-U8, P2-R12, P2-R13, P2-R18, P2-R22, P2-R25, P2-R27)
#
# The employee's conversation with HR. Three rules hold this section together:
#
#   * Role Employee has no `create` and no `write` on HR Request any more
#     (the DocType's own permissions). Everything an employee writes here goes
#     through `create_my_request` or `attach_to_my_request`, both of which
#     resolve the employee from the session and accept a fixed, bounded set of
#     fields -- so there is no generic Frappe route left that widens what an
#     employee may say about their own request, or whose request it is.
#   * Creating the request and attaching the file are **two** observable
#     steps, and they are told apart on purpose (P2-R18). A request whose
#     upload failed is a real request that is missing a file, not a failure --
#     saying "couldn't send" about a committed record is the defect this
#     replaces.
#   * The first of those two steps carries a client operation key, so a
#     response lost between the commit and the browser costs a retry rather
#     than a duplicate request (P2-AE7).
# ---------------------------------------------------------------------------

# One bounded first page, and the ceiling Load More may climb to (P2-R22).
_REQUEST_PAGE = 20
_REQUEST_MAX_PAGE = 100
_REQUEST_CATEGORY_LIMIT = 100

_REQUEST_FIELDS = (
	"name",
	"category",
	"subject",
	"status",
	"hr_note",
	"creation",
	"modified",
	"picked_up_on",
	"replied_on",
	"closed_on",
)

# What the new-request sheet states up front, enforced by
# `helixhr.utils.validate_portal_upload` so the sentence on the screen is a
# rule and not a hope. The site's own `max_file_size` still applies underneath
# (File.check_max_file_size); this is the portal's own, lower, stated cap.
_ATTACHMENT_MAX_BYTES = UPLOAD_MAX_BYTES

# The shape `crypto.randomUUID()` produces, plus enough slack for a fallback
# generator, and nothing else. Bounded input at the boundary: this value goes
# into an indexed unique column.
_OPERATION_KEY_PATTERN = re.compile(r"^[A-Za-z0-9-]{16,64}$")

# Data(140) on the DocType. Truncating silently would change what the employee
# said, so an over-long subject is refused rather than trimmed.
_SUBJECT_MAX = 140
_DETAILS_MAX = 5000


def _handled_by_team(routed_to_role):
	"""The display label for whoever handles a request (plan 2026-10-04-002
	R5): "IT" for the IT Team, "HR" for HR Manager, the role name itself
	otherwise."""
	if routed_to_role == "IT Team":
		return "IT"
	if routed_to_role == "HR Manager":
		return "HR"
	return routed_to_role or "HR"


def _category_teams():
	"""{category -> display team label}, one query over a bounded table."""
	return {
		name: _handled_by_team(role)
		for name, role in frappe.get_all(
			"HelixHR Request Category", fields=["name", "route_to_role"], as_list=True
		)
	}


def _picker_display_name(picked_up_by):
	"""The picker's full name, or None when the honest answer is "the team
	handled it" (plan 2026-10-04-002 KTD4): no picker recorded, the
	Administrator account, or a disabled user. The raw user id never
	returns -- an employee has no read on User, so the client couldn't
	resolve it anyway."""
	if not picked_up_by or picked_up_by == "Administrator":
		return None
	row = frappe.db.get_value("User", picked_up_by, ["full_name", "enabled"], as_dict=True)
	if not row or not row.enabled:
		return None
	return row.full_name


def _requests_summary(employee, limit=None, category=None):
	"""A bounded page of `employee`'s requests, newest first, optionally of
	one category (U6 / R13; `counts` is per category across all of them).

	Carries what the list actually renders and nothing else: the lifecycle
	dates, HR's reply, how many files are on it, and whether there is an
	unread notification about it -- which is what puts a row under "Needs
	you" rather than a status word (P2-R13).
	"""
	limit = min(max(cint(limit) or _REQUEST_PAGE, 1), _REQUEST_MAX_PAGE)
	category = _valid_request_category(category)
	filters = {"employee": employee, **({"category": category} if category else {})}

	rows = frappe.get_all(
		"HR Request",
		filters=filters,
		fields=[*list(_REQUEST_FIELDS), "picked_up_by"],
		order_by="creation desc",
		limit=limit,
	)
	names = [row.name for row in rows]
	unread = _unread_request_notifications(names)
	counts = _attachment_counts(names)
	# Team labels come from the category, one query for the whole page.
	teams = _category_teams()
	for row in rows:
		row["picked_up_by_name"] = _picker_display_name(row.pop("picked_up_by"))
		row["handled_by_team"] = teams.get(row.category) or "HR"

	return {
		"requests": [
			{
				**row,
				"unread": row.name in unread,
				"attachments": counts.get(row.name, 0),
			}
			for row in rows
		],
		"total": frappe.db.count("HR Request", filters),
		# One column over one employee's requests -- small, and the same
		# flat-read reasoning as `_attachment_counts`.
		"counts": {
			"categories": _count_by(
				frappe.get_all("HR Request", filters={"employee": employee}, pluck="category")
			)
		},
		"limit": limit,
		"today": user_today(),
	}


@frappe.whitelist()
def get_my_requests(limit=None, category=None):
	return _requests_summary(get_current_employee(), limit, category)


@frappe.whitelist()
def get_my_request(name):
	"""One request, in full: what the employee wrote, when it moved, HR's
	reply, and every file on it (P2-R12, P2-R18).

	Its own read rather than a lookup into the list, because the list is
	bounded -- an old request reached from a notification or a bookmark is
	not necessarily on the page the list returned.
	"""
	rate_limit_per_user("get_my_request")
	employee = get_current_employee()
	return _request_detail(name, employee)


def _request_detail(name, employee):
	row = frappe.db.get_value(
		"HR Request",
		name,
		[
			*_REQUEST_FIELDS,
			"details",
			"employee",
			"routed_to_role",
			"picked_up_by",
			"correction_field",
			"correction_proposed_masked",
		],
		as_dict=True,
	)
	if not row:
		frappe.throw(_("That request no longer exists."), frappe.DoesNotExistError)
	if row.employee != employee:
		# Not "not found": the caller is authenticated and this is a refusal,
		# which the portal renders as its own state with no Retry (P2-R2).
		frappe.throw(_("That request isn't yours."), frappe.PermissionError)
	row.pop("employee")
	# Plan 2026-10-04-002 R4/R5: the surface names who picked the request up
	# and which team handles it -- resolved here, because the employee has
	# no read on User; the raw ids are popped, never returned.
	row["picked_up_by_name"] = _picker_display_name(row.pop("picked_up_by"))
	row["handled_by_team"] = _handled_by_team(row.pop("routed_to_role"))

	files = frappe.get_all(
		"File",
		filters={"attached_to_doctype": "HR Request", "attached_to_name": name},
		fields=["name", "file_name", "file_url", "file_size", "is_private", "owner", "creation"],
		order_by="creation asc",
	)
	mine = frappe.session.user
	return {
		**row,
		"unread_notifications": _unread_request_notifications([name]).get(name, []),
		# Split by who put it there, because the screen says two different
		# things about them: yours sit under "You wrote", HR's under "HR
		# replied". Every one of them is private and reachable only through
		# File's own download check against this request (P2-U8 scenario 4).
		"attachments": [_attachment(row) for row in files if row.owner == mine],
		"hr_attachments": [_attachment(row) for row in files if row.owner != mine],
		# P5-R10: the same conversation `get_approval_detail` projects to the
		# worker, so a reply the employee sends and a reason the worker writes
		# read as one thread on both sides rather than two different views of
		# the same record.
		"thread": _request_thread(frappe.get_doc("HR Request", name)),
		"can_reply": row.status == HR_REQUEST_WAITING_ON_EMPLOYEE,
	}


def _attachment(row):
	return {
		"name": row.name,
		"file_name": row.file_name,
		"file_url": row.file_url,
		"file_size": cint(row.file_size),
		"is_private": cint(row.is_private),
	}


def _attachment_counts(names):
	"""How many files each of these requests carries, in one query rather
	than one per row (P2-R22)."""
	if not names:
		return {}
	counts = {}
	# Counted here rather than with a GROUP BY: Frappe v16's query builder
	# refuses a SQL function written as a string in `fields`, and the page is
	# bounded to 100 requests with a handful of files each, so one flat read
	# is both cheaper to reason about and still a single query.
	for row in frappe.get_all(
		"File",
		filters={"attached_to_doctype": "HR Request", "attached_to_name": ["in", names]},
		pluck="attached_to_name",
	):
		counts[row] = counts.get(row, 0) + 1
	return counts


def _unread_request_notifications(names):
	"""{request name -> the caller's unread Notification Log rows about it}.

	The read state is Frappe's own (P2-KTD6); there is no second seen-model
	here. `for_user` is the session user in the filter as well as in the
	doctype's permission query -- this runs through `frappe.get_all`, which
	does not apply that query, so the filter is the boundary.
	"""
	if not names:
		return {}
	found = {}
	for row in frappe.get_all(
		"Notification Log",
		filters={
			"for_user": frappe.session.user,
			"document_type": "HR Request",
			"document_name": ["in", names],
			"read": 0,
		},
		fields=["name", "document_name"],
	):
		found.setdefault(row.document_name, []).append(row.name)
	return found


@frappe.whitelist(methods=["POST"])
def mark_my_request_read(name):
	"""Clear the read obligation on a request the employee has just opened
	(P2-R13, P2-U8 step 5).

	Every unread Notification Log this user holds about this request, not
	only the HR-reply one: the employee has now seen the record all of them
	point at, and leaving a status-change row unread would leave the shell
	badge claiming there is something else to look at.

	Returns the new total so the badge moves in the same interaction rather
	than at the next poll.
	"""
	rate_limit_per_user("mark_notifications_read")
	employee = get_current_employee()
	if frappe.db.get_value("HR Request", name, "employee") != employee:
		frappe.throw(_("That request isn't yours."), frappe.PermissionError)

	cleared = _unread_request_notifications([name]).get(name, [])
	for log in cleared:
		# Scoped to `for_user` by the query above, so this can only ever mark
		# the caller's own row.
		frappe.db.set_value("Notification Log", log, "read", 1, update_modified=False)

	return {"cleared": len(cleared), "unread": _get_unread_notification_count()}


@frappe.whitelist(methods=["POST"])
def create_my_request(
	category,
	subject,
	details=None,
	operation_key=None,
	correction_field=None,
	correction_value=None,
	correction_confirm=None,
):
	"""Create this employee's HR Request, once, whatever the network does
	(P2-R18, P2-R25, P2-AE7).

	`operation_key` is generated by the browser with `crypto.randomUUID()`
	**once per user attempt** and stored on the record in a unique column.
	The contract it buys:

	  * A first call commits the request and returns it.
	  * A retry with the same key -- the case where the first response was
	    lost -- returns *that* request rather than making a second one.
	  * A key that already belongs to somebody else's request is refused
	    with `DuplicateEntryError` and nothing else: no name, no subject, no
	    hint that a record exists. The caller's contract is to rotate the key
	    and send again.
	  * A deliberate second submission carries a *new* key, so it is a
	    second request and is meant to be.

	Employee, status and the naming series are all record facts rather than
	caller inputs, and category is checked against the DocType's own options
	-- `frappe.client.insert` with a browser-built document, which this
	replaces, offered every one of them as a parameter.

	Plan 2026-10-02-001 U13: with `correction_field`, this files a profile
	correction. The value is typed twice (`correction_value`,
	`correction_confirm`) and the proof is the multipart `file` of this same
	call, so the request and its proof commit together. Every rule lives in
	`events.hr_request_validate`; this only hands the inputs over. The
	response never echoes the value.
	"""
	rate_limit_per_user("create_my_request")
	employee = get_current_employee()
	key = (operation_key or "").strip()
	if not _OPERATION_KEY_PATTERN.match(key):
		frappe.throw(_("That request couldn't be started. Try sending it again."))

	existing = _request_for_key(key, employee)
	if existing:
		return {**_request_detail(existing, employee), "created": False}

	category = (category or "").strip()
	if category not in _request_categories():
		frappe.throw(_("Pick what your request is about."))
	subject = (subject or "").strip()
	if not subject:
		frappe.throw(_("Give your request a subject, so HR knows what it's about."))
	if len(subject) > _SUBJECT_MAX:
		frappe.throw(_("That subject is too long. Keep it under {0} characters.").format(_SUBJECT_MAX))
	details = (details or "").strip()
	if len(details) > _DETAILS_MAX:
		frappe.throw(_("Those details are too long. Keep them under {0} characters.").format(_DETAILS_MAX))

	doc = frappe.get_doc(
		{
			"doctype": "HR Request",
			"employee": employee,
			"category": category,
			"subject": subject,
			"details": details or None,
			"client_operation_key": key,
		}
	)
	if correction_field:
		doc.correction_field = correction_field
		doc.correction_proposed = correction_value
		doc.flags.correction_confirm = correction_confirm
		upload = (getattr(frappe.request, "files", None) or {}).get("file")
		if upload is not None:
			doc.flags.correction_proof = (
				os.path.basename(upload.filename or "").strip(),
				upload.stream.read(),
			)
	try:
		# Role Employee has no `create` on this DocType by design: the
		# allow-list above *is* the create rule, and it is stricter than a
		# DocPerm can be.
		doc.insert(ignore_permissions=True)
	except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
		# Two calls with the same key raced. Whichever lost re-reads and
		# returns the winner, which is the same answer a later retry gets.
		frappe.db.rollback()
		won = _request_for_key(key, employee)
		if not won:
			raise
		return {**_request_detail(won, employee), "created": False}

	return {**_request_detail(doc.name, employee), "created": True}


@frappe.whitelist(methods=["POST"])
def reveal_correction_value(name):
	"""The full proposed value of an open correction, for the HR user handling
	it (plan 2026-10-02-001 R28, KTD15).

	The same authorization a decision on the record gets (`_assert_may_act_on`:
	not the requester, in HR's admin scope), narrowed to HR and, once picked
	up, to the user who picked it up. Closed requests have nothing left to
	reveal. Every reveal leaves an Info comment naming who looked.
	"""
	from frappe.utils.password import get_decrypted_password

	rate_limit_per_user("reveal_correction_value")
	if not frappe.db.exists("HR Request", name):
		frappe.throw(_(_APPROVAL_NOT_FOUND), frappe.PermissionError)
	doc = frappe.get_doc("HR Request", name)
	if not _is_hr() or doc.routed_to_role != "HR Manager":
		frappe.throw(_(_APPROVAL_NOT_FOUND), frappe.PermissionError)
	_assert_may_act_on(doc)
	if doc.picked_up_by and doc.picked_up_by != frappe.session.user:
		frappe.throw(_("Only the person handling this request can see the full value."), frappe.PermissionError)
	if not doc.correction_field or doc.status in ("Done", "Rejected"):
		frappe.throw(_("There is no value to show on this request."))
	value = get_decrypted_password("HR Request", name, "correction_proposed", raise_exception=False)
	if not value:
		frappe.throw(_("There is no value to show on this request."))
	doc.add_comment(
		"Info",
		_("Proposed {0} revealed by {1}").format(
			PROFILE_CORRECTABLE_FIELDS[doc.correction_field],
			frappe.utils.get_fullname(frappe.session.user),
		),
	)
	return {"name": name, "field": doc.correction_field, "value": value}


def _request_for_key(key, employee):
	"""The request this key already made, if any. Refuses rather than
	answers when the key belongs to another employee."""
	row = frappe.db.get_value(
		"HR Request", {"client_operation_key": key}, ["name", "employee"], as_dict=True
	)
	if not row:
		return None
	if row.employee != employee:
		frappe.throw(
			_("That request couldn't be started. Try sending it again."),
			frappe.DuplicateEntryError,
		)
	return row.name


def _request_categories():
	return frappe.get_all(
		"HelixHR Request Category",
		filters={"is_active": 1},
		pluck="name",
		limit=_REQUEST_CATEGORY_LIMIT,
		order_by="category_name asc",
	)


@frappe.whitelist()
def get_request_categories():
	"""The active request categories the employee may file (P5-R1).

	`sends_to` names who receives the request, for the form's send button:
	"IT" for a category routed to the IT Team role, "HR" for everything
	else. The role name itself stays server-side."""
	rate_limit_per_user("get_request_categories")
	rows = frappe.get_all(
		"HelixHR Request Category",
		filters={"is_active": 1},
		fields=["name", "category_name", "hint", "route_to_role"],
		limit=_REQUEST_CATEGORY_LIMIT,
		order_by="category_name asc",
	)
	for row in rows:
		row["sends_to"] = "IT" if row.pop("route_to_role") == "IT Team" else "HR"
	return rows


# --- Configuration (P5-U13, P5-KTD3, P5-KTD11, P5-KTD12, P5-KTD15) ---------

# Data fieldtype's storage limit, restated here so a caller gets a plain
# refusal instead of a DB truncation or a `CharacterLengthExceededError` with
# no context (P5-R19). `HelixHR Message Template.subject` carries the same
# limit in its own JSON; this is the number the API enforces before it ever
# reaches the document.
_TEMPLATE_SUBJECT_MAX = 140

# The category's own fields (not one of P5-KTD12's borrowed-doctype sets --
# `HelixHR Request Category` is app-owned, so its whole shape beyond the
# autoname key is already short).
_CATEGORY_EDITABLE_FIELDS = ("hint", "route_to_role", "name_prefix", "sla_days", "is_active")


def _assert_config_write(doc):
	"""Explicit permission check before a configuration write (P5-R18,
	P5-KTD15): `doc.save()` on a new or existing document still runs the
	doctype's own `has_permission`, so this is not the only gate, but the
	plan asks for a `PermissionError` a caller can act on before anything
	else about the write is attempted."""
	ptype = "create" if doc.is_new() else "write"
	if not doc.has_permission(ptype):
		frappe.throw(_("You don't have permission to do that."), frappe.PermissionError)


def _apply_allowed_fields(doc, fields, allowed, skip_on_update=()):
	"""Update only the named, allow-listed fields (P5-KTD12) -- anything
	else in `fields` is silently ignored, never reaches `doc.set`, and can
	never widen what a caller can change just by adding another keyword.
	`skip_on_update` names an identifying field (`leave_type_name`,
	`holiday_list_name`) that only applies at creation: these doctypes'
	`field:` autoname does not re-rename an existing document when the
	field changes later, so accepting it on an update would silently
	desynchronise `doc.name` from the field HR just edited."""
	for field in allowed:
		if field in skip_on_update and not doc.is_new():
			continue
		if field in fields:
			doc.set(field, fields[field])


# P8-U6: one Desk list-view link per Settings section, named once here so
# the section->doctype mapping cannot drift from what the tabs actually are
# (`Settings.vue`'s own `SECTIONS` list). "Categories" is the one section
# with no key here -- it maps to `HelixHR Request Category` the same way
# every other section maps to its own doctype, added in the same order the
# tabs render.
_SETTINGS_DESK_DOCTYPES = {
	"categories": "HelixHR Request Category",
	"leave_types": "Leave Type",
	"holiday_lists": "Holiday List",
	"shift_types": "Shift Type",
	# Plan 2026-10-04-004 U5: the celebrations section left Settings for the
	# Email templates page's own group, so it names no Desk doctype here.
	# Plan 2026-10-04-001 U6: the report access matrix.
	"report_access": "HelixHR Report Access",
}


def _settings_desk_urls():
	"""A Desk list-view URL per Settings section (P8-R4), or `None` for a
	caller who cannot reach Desk at all -- the same `_can_open_desk` gate
	`get_person`'s own `desk_url` already uses (P6-KTD4): the server
	decides whether the link is ever handed out, never merely hides it on
	a caller who could still follow the URL directly."""
	if not _can_open_desk(frappe.session.user):
		return None
	return {section: get_url_to_list(doctype) for section, doctype in _SETTINGS_DESK_DOCTYPES.items()}


@frappe.whitelist()
def get_portal_config():
	"""Everything the Settings screen needs, in one call (P5-R13, P5-R14,
	P5-R16). HR only: an IT Team holder works requests but does not
	configure the site."""
	rate_limit_per_user("get_portal_config")
	if not _is_hr():
		frappe.throw(_("You don't have permission to do that."), frappe.PermissionError)

	return {
		"desk_urls": _settings_desk_urls(),
		"categories": frappe.get_all(
			"HelixHR Request Category",
			fields=["name", "category_name", "hint", "route_to_role", "name_prefix", "sla_days", "is_active"],
			order_by="category_name asc",
		),
		"leave_types": frappe.get_all(
			"Leave Type", fields=["name", *LEAVE_TYPE_EDITABLE_FIELDS], order_by="leave_type_name asc"
		),
		# `holidays` is a child table -- not a plain column `frappe.get_all`
		# can select for a list summary. The scalar fields are enough for
		# the settings list; a specific list's rows are read when editing
		# it, via `frappe.get_doc`.
		"holiday_lists": frappe.get_all(
			"Holiday List",
			fields=["name", *(field for field in HOLIDAY_LIST_EDITABLE_FIELDS if field != "holidays")],
			order_by="holiday_list_name asc",
		),
		"shift_types": frappe.get_all(
			"Shift Type", fields=["name", *SHIFT_TYPE_EDITABLE_FIELDS], order_by="name asc"
		),
	}


@frappe.whitelist(methods=["POST"])
def save_request_category(name, **fields):
	"""Create or update one request category (P5-R13). `route_to_role` is
	validated against the reviewed set of workable roles by the doctype's
	own `validate()` -- not repeated here, so the rule cannot drift between
	the portal and Desk."""
	rate_limit_per_user("save_request_category")
	name = (name or "").strip()
	if not name:
		frappe.throw(_("Give the category a name."))

	if frappe.db.exists("HelixHR Request Category", name):
		doc = frappe.get_doc("HelixHR Request Category", name)
	else:
		doc = frappe.new_doc("HelixHR Request Category")
		doc.category_name = name

	_assert_config_write(doc)
	_apply_allowed_fields(doc, fields, _CATEGORY_EDITABLE_FIELDS)
	doc.save()
	return {field: doc.get(field) for field in ("name", "category_name", *_CATEGORY_EDITABLE_FIELDS)}


# --- Email templates page (plan 2026-10-02-001 U10, R15, R18) --------------


def _assert_can_edit_email_templates():
	"""The one gate every Email templates method calls first: Portal Admin
	or System Manager (`_can_edit_email_templates`). HR Manager is refused."""
	if not _can_edit_email_templates():
		frappe.throw(_("You don't have permission to do that."), frappe.PermissionError)


def _message_event(template_key):
	if template_key not in NOTIFICATION_EVENTS:
		frappe.throw(_("Not a valid message."))
	return NOTIFICATION_EVENTS[template_key]


@frappe.whitelist()
def get_notification_setup():
	"""Every portal email event with its state (Off / Default / Custom, KTD7),
	wording, defaults and variable reference -- the whole page in one call."""
	_assert_can_edit_email_templates()
	rate_limit_per_user("get_notification_setup")
	saved = {
		row.template_key: row
		for row in frappe.get_all(
			"HelixHR Message Template",
			fields=["template_key", "subject", "body", "is_enabled", "hide_logo", "modified"],
		)
	}
	events = []
	for key, event in NOTIFICATION_EVENTS.items():
		locked = bool(event.get("locked"))
		row = saved.get(key)
		custom_wording = bool(row and has_custom_wording(row))
		if row and not row.is_enabled and not locked:
			state = "Off"
		else:
			state = "Custom" if custom_wording else "Default"
		events.append(
			{
				"key": key,
				"label": event["label"],
				"audience": event["audience"],
				"locked": locked,
				"state": state,
				"subject": (row.subject if row else None) or event["subject"],
				"body": (row.body if row else None) or event["body"],
				"default_subject": event["subject"],
				"default_body": event["body"],
				"custom_wording": custom_wording,
				# The per-template logo opt-out; the logo itself is the theme's.
				"hide_logo": bool(row and row.hide_logo),
				"last_fallback": _last_template_fallback(key, row),
				"variables": [
					{"name": name, "description": description, "sample": sample}
					for name, (description, sample) in event_variables(key).items()
				],
			}
		)
	return {"events": events, "subject_max": _TEMPLATE_SUBJECT_MAX}


def _last_template_fallback(event_key, row):
	"""Plan 2026-10-05-001 U13: the most recent time the saved template for
	`event_key` failed on real data and the default went out instead --
	`render_message` already writes that Error Log row, so no new doctype.
	Only a failure *after* the row was last saved counts, so the warning
	clears once HR saves a fix. The event key, the time and a truncated
	exception line only; never the traceback."""
	if not row:
		return None
	log = frappe.db.get_value(
		"Error Log",
		{"method": f"HelixHR message template {event_key} failed", "creation": [">", row.modified]},
		["creation", "error"],
		order_by="creation desc",
		as_dict=True,
	)
	if not log:
		return None
	lines = [line.strip() for line in (log.error or "").strip().splitlines() if line.strip()]
	message = lines[-1] if lines else ""
	return {
		"event_key": event_key,
		"at": frappe.utils.format_datetime(log.creation, "yyyy-MM-dd HH:mm"),
		"message": message[:200],
	}


@frappe.whitelist(methods=["POST"])
def save_message_template(template_key, subject=None, body=None, is_enabled=None, hide_logo=None):
	"""Edit the wording of one message the portal sends (P5-R14). The
	doctype's own `validate()` holds the template to the HelixHR sandbox's
	rules (plan 2026-10-02-001 U8, R17): unknown variables, disallowed
	constructs and templates that fail on sample data are refused there.

	`hide_logo` alone (no subject/body) on a default-wording message keeps a
	wording-less row that carries just the opt-out."""
	_assert_can_edit_email_templates()
	rate_limit_per_user("save_message_template")
	_message_event(template_key)

	if frappe.db.exists("HelixHR Message Template", template_key):
		doc = frappe.get_doc("HelixHR Message Template", template_key)
	else:
		doc = frappe.new_doc("HelixHR Message Template")
		doc.template_key = template_key

	if subject is not None:
		subject = subject.strip()
		if len(subject) > _TEMPLATE_SUBJECT_MAX:
			frappe.throw(
				_("That subject is too long. Keep it under {0} characters.").format(_TEMPLATE_SUBJECT_MAX)
			)
		doc.subject = subject
	if body is not None:
		doc.body = body
	if is_enabled is not None:
		doc.is_enabled = cint(is_enabled)
	if hide_logo is not None:
		doc.hide_logo = cint(hide_logo)
	# The gate above is the authorisation: Portal Admin holds no DocPerm
	# (preflight enforces), so the doctype's own `validate()` still runs but
	# its permission check is skipped.
	doc.save(ignore_permissions=True)
	return {
		"template_key": doc.template_key,
		"subject": doc.subject,
		"body": doc.body,
		"is_enabled": cint(doc.is_enabled),
		"hide_logo": cint(doc.hide_logo),
	}


@frappe.whitelist(methods=["POST"])
def reset_message_template(template_key):
	"""Back to the default wording: no row is Default (KTD7). The Info
	comment names the actor (R18a); it is written after the delete because
	`delete_doc` removes the row's own comments, so its link is not checked
	-- the next save recreates the row under the same name."""
	_assert_can_edit_email_templates()
	rate_limit_per_user("reset_message_template")
	_message_event(template_key)
	if frappe.db.exists("HelixHR Message Template", template_key):
		if frappe.db.get_value("HelixHR Message Template", template_key, "hide_logo"):
			# The logo opt-out is not wording: keep it on a wording-less row.
			frappe.db.set_value(
				"HelixHR Message Template",
				template_key,
				{"subject": None, "body": None, "is_enabled": 1},
			)
		else:
			# Portal Admin has no DocPerm on purpose -- Desk delete stays
			# System Manager's. The guard above is this path's gate.
			frappe.delete_doc("HelixHR Message Template", template_key, ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Info",
				"reference_doctype": "HelixHR Message Template",
				"reference_name": template_key,
				"content": _("{0} reset this email template to the default").format(
					frappe.utils.get_fullname(frappe.session.user)
				),
			}
		).insert(ignore_permissions=True, ignore_links=True)
	return {"template_key": template_key, "state": "Default"}


def _render_draft(template_key, subject, body, hide_logo=0, embed_logo=True):
	"""Validate then render an unsaved draft with the event's sample data.
	A refusal is the same sentence a save would give (R17)."""
	event = _message_event(template_key)
	if event.get("locked"):
		subject = event["subject"]
	try:
		validate_message_template(template_key, subject, body)
	except TemplateRejected as exc:
		frappe.throw(str(exc), title=_("Template not valid"))
	# The real brand, resolved as `send_notification` does for this caller
	# (the test send's recipient): their company and its logo, else the
	# default company's -- never the sample's placeholder logo address.
	context = {**sample_context(template_key), **message_brand(frappe.session.user)}
	return render_message(
		template_key,
		context,
		source={"subject": subject, "body": body, "hide_logo": cint(hide_logo)},
		embed_logo=embed_logo,
	)


@frappe.whitelist(methods=["POST"])
def preview_message_template(template_key, subject=None, body=None, hide_logo=0):
	"""The draft as the email would look, with sample data. The client shows
	`html` only in a sandboxed iframe (KTD11). `hide_logo` is the editor's
	unsaved "Include company logo" checkbox, inverted."""
	_assert_can_edit_email_templates()
	rate_limit_per_user("preview_message_template")
	message = _render_draft(template_key, subject or "", body or "", hide_logo, embed_logo=False)
	return {"subject": message["subject"], "html": message["html"]}


@frappe.whitelist(methods=["POST"])
def send_test_message(template_key, subject=None, body=None, hide_logo=0):
	"""Send the draft, with sample data, to the caller's own address only --
	never a recipient the caller names."""
	_assert_can_edit_email_templates()
	rate_limit_per_user("send_test_message")
	message = _render_draft(template_key, subject or "", body or "", hide_logo)
	email = frappe.db.get_value("User", frappe.session.user, "email")
	if not email:
		frappe.throw(_("Your account has no email address to send the test to."))
	frappe.sendmail(
		recipients=[email],
		subject=_("[Test] {0}").format(message["subject"]),
		message=message["html"],
	)
	return {"sent_to": email}


# --- Shared email theme (`HelixHR Email Theme`) ------------------------------
#
# One look for every email the portal sends. Same gate as the templates;
# Portal Admin holds no DocPerm, so writes go through `ignore_permissions`
# behind that gate while the doctype's own `validate()` still runs.

_THEME_SAMPLE_EVENT = "leave_approved"


def _theme_draft(brand_color=None, footer_text=None, use_custom_code=None, theme_code=None):
	"""The saved theme with the editor's unsaved values over it, validated
	-- a refusal is the sentence a save would give."""
	theme = email_theme(
		{
			"brand_color": brand_color,
			"footer_text": footer_text,
			"use_custom_code": use_custom_code,
			"theme_code": theme_code,
		}
	)
	try:
		validate_email_theme(theme)
	except TemplateRejected as exc:
		frappe.throw(str(exc), title=_("Theme not valid"))
	return theme


def _theme_sample_message(theme, embed_logo):
	"""A real message (Leave approved, sample data) inside `theme`."""
	context = {**sample_context(_THEME_SAMPLE_EVENT), **message_brand(frappe.session.user)}
	return render_message(
		_THEME_SAMPLE_EVENT,
		context,
		source={"subject": None, "body": None, "hide_logo": 0},
		embed_logo=embed_logo,
		theme=theme,
	)


def _theme_projection():
	theme = email_theme()
	return {
		"logo": theme.logo,
		"brand_color": (theme.brand_color or "").upper(),
		"footer_text": theme.footer_text,
		"use_custom_code": theme.use_custom_code,
		"theme_code": theme.theme_code,
		"placeholders": list(THEME_PLACEHOLDERS),
		"preview_html": _theme_sample_message(theme, embed_logo=False)["html"],
	}


@frappe.whitelist()
def get_email_theme():
	"""The Theme tab in one call: the saved fields and a preview."""
	_assert_can_edit_email_templates()
	rate_limit_per_user("get_email_theme")
	return _theme_projection()


@frappe.whitelist(methods=["POST"])
def preview_email_theme(brand_color=None, footer_text=None, use_custom_code=None, theme_code=None):
	"""The unsaved theme around a sample message. The client shows `html`
	only in a sandboxed iframe (KTD11); the logo is linked, not embedded."""
	_assert_can_edit_email_templates()
	rate_limit_per_user("preview_email_theme")
	theme = _theme_draft(brand_color, footer_text, use_custom_code, theme_code)
	return {"html": _theme_sample_message(theme, embed_logo=False)["html"]}


@frappe.whitelist(methods=["POST"])
def save_email_theme(brand_color=None, footer_text=None, use_custom_code=None, theme_code=None):
	"""Save the theme's colour, footer and custom code (the logo has its own
	upload). The doctype's `validate()` refuses a bad colour, missing
	`{{ content }}` or anything the sandbox does not allow."""
	_assert_can_edit_email_templates()
	rate_limit_per_user("save_email_theme")
	doc = frappe.get_doc(EMAIL_THEME)
	doc.brand_color = (brand_color or "").strip()
	doc.footer_text = footer_text or ""
	doc.use_custom_code = cint(use_custom_code)
	doc.theme_code = theme_code or ""
	doc.save(ignore_permissions=True)
	return _theme_projection()


@frappe.whitelist(methods=["POST"])
def reset_email_theme():
	"""Back to the default look: colour, footer and custom code cleared. The
	logo stays (it has its own Remove)."""
	_assert_can_edit_email_templates()
	rate_limit_per_user("reset_email_theme")
	doc = frappe.get_doc(EMAIL_THEME)
	doc.brand_color = ""
	doc.footer_text = ""
	doc.use_custom_code = 0
	doc.theme_code = ""
	doc.save(ignore_permissions=True)
	return _theme_projection()


@frappe.whitelist(methods=["POST"])
def upload_email_theme_logo(remove=0):
	"""Upload, replace or remove the theme logo. The upload is
	`frappe.request.files["file"]`: PNG, JPEG or WebP by signature, at most
	2 MB (`validate_logo_upload`), stored public -- the preview loads it by
	URL; sent mail carries it as an inline attachment."""
	_assert_can_edit_email_templates()
	rate_limit_per_user("upload_email_theme_logo")
	doc = frappe.get_doc(EMAIL_THEME)
	if cint(remove):
		doc.logo = ""
		doc.save(ignore_permissions=True)
		return _theme_projection()

	upload = (getattr(frappe.request, "files", None) or {}).get("file")
	if upload is None:
		frappe.throw(_("No file came through. Pick the file again."))
	file_name = os.path.basename(upload.filename or "").strip()
	content = upload.stream.read()
	extension = validate_logo_upload(file_name, content)
	# The logo must be public (mail clients and the preview load it by URL),
	# and preflight requires System Settings' "only System Managers upload
	# public files". That rule checks the session user even under
	# `ignore_permissions`, so a Portal Admin was refused with a bare
	# PermissionError. The gate above and `validate_logo_upload` have already
	# decided this upload, so only this one insert runs as Administrator --
	# every other public upload on the site stays locked.
	with as_administrator():
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"email-theme-logo{extension}",
				"content": content,
				"attached_to_doctype": EMAIL_THEME,
				"attached_to_name": EMAIL_THEME,
				"attached_to_field": "logo",
				"is_private": 0,
			}
		).insert(ignore_permissions=True)
	doc.logo = file_doc.file_url
	doc.save(ignore_permissions=True)
	return _theme_projection()


@frappe.whitelist(methods=["POST"])
def send_email_theme_test(brand_color=None, footer_text=None, use_custom_code=None, theme_code=None):
	"""Send the sample message in the unsaved theme to the caller's own
	address only, with the logo embedded as a real send would."""
	_assert_can_edit_email_templates()
	rate_limit_per_user("send_email_theme_test")
	theme = _theme_draft(brand_color, footer_text, use_custom_code, theme_code)
	message = _theme_sample_message(theme, embed_logo=True)
	email = frappe.db.get_value("User", frappe.session.user, "email")
	if not email:
		frappe.throw(_("Your account has no email address to send the test to."))
	frappe.sendmail(
		recipients=[email],
		subject=_("[Test] {0}").format(message["subject"]),
		message=message["html"],
	)
	return {"sent_to": email}


@frappe.whitelist(methods=["POST"])
def save_leave_type(name, **fields):
	"""Create or update a leave type through the field set P5-KTD12
	names (widened by U2) -- HRMS's own `validate()` still runs on `doc.save()` (P5-KTD15),
	so a value HRMS itself would reject is rejected here too."""
	rate_limit_per_user("save_leave_type")
	name = (name or "").strip()
	if not name:
		frappe.throw(_("Give the leave type a name."))

	if frappe.db.exists("Leave Type", name):
		doc = frappe.get_doc("Leave Type", name)
	else:
		doc = frappe.new_doc("Leave Type")
		doc.leave_type_name = fields.get("leave_type_name") or name

	_assert_config_write(doc)
	_apply_allowed_fields(doc, fields, LEAVE_TYPE_EDITABLE_FIELDS, skip_on_update=("leave_type_name",))
	doc.save()
	return {"name": doc.name, **{field: doc.get(field) for field in LEAVE_TYPE_EDITABLE_FIELDS}}


# --- Leave rules (plan 2026-10-06-001 U2, R1, R2) --------------------------
#
# The backdated leave rule lives in the HelixHR Leave Rules Single (U1); HR
# edits it here instead of through site config. Gated on `_is_hr()`, the same
# predicate `can_configure` and `get_portal_config` use and the audience Leave
# types already has, then written with `ignore_permissions` -- the Single
# grants HR Manager no DocPerm on purpose (it is edited from the portal, not
# from Desk; the shape KTD3 gives the admin-only endpoints, applied to HR).


def _leave_rules_projection():
	doc = frappe.get_single(LEAVE_RULES_DOCTYPE)
	return {
		# The effective value, not the raw field: an unset Single must show the
		# rule's real default (1), not the 0 `get_single_value` casts an unset
		# Int to.
		"backdated_grace_days": backdated_grace_days(),
		"backdated_exempt_role": doc.backdated_exempt_role or "",
		# Enabled roles for the tab's picker. `AUTOMATIC_ROLES` (All, Guest,
		# Desk User, Administrator) is excluded -- exempting one would exempt
		# every user -- and the doctype's validate() refuses them too.
		"roles": frappe.get_all(
			"Role",
			filters={"disabled": 0, "name": ["not in", list(AUTOMATIC_ROLES)]},
			pluck="name",
			order_by="name asc",
		),
	}


@frappe.whitelist()
def get_leave_rules():
	"""The backdated leave rule, for the Settings Leave rules tab."""
	rate_limit_per_user("get_leave_rules")
	if not _is_hr(frappe.session.user):
		frappe.throw(_("You don't have permission to do that."), frappe.PermissionError)
	return _leave_rules_projection()


@frappe.whitelist(methods=["POST"])
def save_leave_rules(backdated_grace_days=None, backdated_exempt_role=None):
	"""Save the backdated leave rule. The 0-365 bound and the exempt role's
	existence are validated by the doctype's own `validate()`, so a Desk save
	is held to the same rules as this one."""
	rate_limit_per_user("save_leave_rules")
	if not _is_hr(frappe.session.user):
		frappe.throw(_("You don't have permission to do that."), frappe.PermissionError)
	doc = frappe.get_single(LEAVE_RULES_DOCTYPE)
	if backdated_grace_days is not None:
		doc.backdated_grace_days = cint(backdated_grace_days)
	if backdated_exempt_role is not None:
		doc.backdated_exempt_role = (backdated_exempt_role or "").strip()
	doc.flags.ignore_permissions = True
	doc.save()
	return _leave_rules_projection()


@frappe.whitelist(methods=["POST"])
def save_holiday_list(name, holidays=None, **fields):
	"""Create or update a holiday list through the named field set
	(P5-KTD12). `holidays` is the child table, posted as a list of
	`{"holiday_date": ..., "description": ...}` rows -- `doc.set` replaces
	the table wholesale, matching how the Desk form itself saves it."""
	rate_limit_per_user("save_holiday_list")
	name = (name or "").strip()
	if not name:
		frappe.throw(_("Give the holiday list a name."))

	if frappe.db.exists("Holiday List", name):
		doc = frappe.get_doc("Holiday List", name)
	else:
		doc = frappe.new_doc("Holiday List")
		doc.holiday_list_name = fields.get("holiday_list_name") or name

	_assert_config_write(doc)
	_apply_allowed_fields(doc, fields, HOLIDAY_LIST_EDITABLE_FIELDS, skip_on_update=("holiday_list_name",))
	if holidays is not None:
		doc.set("holidays", holidays)
	doc.save()
	return {
		"name": doc.name,
		"from_date": str(doc.from_date) if doc.from_date else None,
		"to_date": str(doc.to_date) if doc.to_date else None,
		"weekly_off": doc.weekly_off,
		"holidays": [
			{"holiday_date": str(row.holiday_date), "description": row.description} for row in doc.holidays
		],
	}


@frappe.whitelist(methods=["POST"])
def save_shift_type(name, **fields):
	"""Create or update a shift type through the named field set
	(P5-KTD12). Shift Type is prompt-autonamed: `name` is the identifier a
	create call supplies and is never itself rewritten on an update."""
	rate_limit_per_user("save_shift_type")
	name = (name or "").strip()
	if not name:
		frappe.throw(_("Give the shift a name."))

	if frappe.db.exists("Shift Type", name):
		doc = frappe.get_doc("Shift Type", name)
	else:
		doc = frappe.new_doc("Shift Type")
		doc.name = name

	_assert_config_write(doc)
	_apply_allowed_fields(doc, fields, SHIFT_TYPE_EDITABLE_FIELDS)
	doc.save()
	return {"name": doc.name, **{field: doc.get(field) for field in SHIFT_TYPE_EDITABLE_FIELDS}}


# ---------------------------------------------------------------------------
# P8-U12: authoring the birthday and work-anniversary email from the portal.
#
# `helixhr.reminders.EVENTS` names the two events; the Email Template each
# `HelixHR Celebration Reminder` row links to is HR's own body, seeded once
# by `seed_celebration_templates` under the same two names this module reuses
# rather than creating a second template per event on first save.
# ---------------------------------------------------------------------------

# Plan 2026-10-05-001 U11 (KTD12): each (event, company) sends from its own
# Email Template, `reminders.celebration_template_name` -- the shared seeded
# names are no longer written by a save, so one company's edit never
# reaches another's mail.


def _celebration_reminder_projection(event, company):
	from helixhr.reminders import EVENTS

	spec = EVENTS[event]
	# Settings are per company since plan 2026-10-04-004 U1 -- one row per
	# (event, company), absent meaning disabled.
	reminder = frappe.db.get_value(
		"HelixHR Celebration Reminder",
		{"event": event, "company": company},
		["name", "is_enabled", "email_template", "recipient_mode", "frequency", "hide_logo"],
		as_dict=True,
	)

	from helixhr.reminders import CELEBRATION_DEFAULTS

	# No row or no template yet: the editor opens on the shipped default,
	# never blank (U11).
	subject = CELEBRATION_DEFAULTS[event]["subject"]
	body = CELEBRATION_DEFAULTS[event]["body"]
	use_html = True
	template_name = reminder.email_template if reminder else None
	if template_name and frappe.db.exists("Email Template", template_name):
		template = frappe.get_doc("Email Template", template_name)
		subject = template.subject
		use_html = bool(template.use_html)
		body = template.response_html if template.use_html else template.response

	recipients = []
	if reminder:
		recipients = frappe.get_all(
			"HelixHR Celebration Recipient",
			filters={"parent": reminder.name, "parenttype": "HelixHR Celebration Reminder"},
			fields=["employee", "employee_name"],
			order_by="idx asc",
		)

	return {
		"event": event,
		"label": spec["label"],
		"is_enabled": bool(reminder and reminder.is_enabled),
		"recipient_mode": reminder.recipient_mode if reminder else "All employees",
		"frequency": reminder.frequency if reminder else ("Weekly" if event == "holiday" else None),
		"subject": subject,
		"body": body,
		"use_html": use_html,
		"recipients": recipients,
		"hide_logo": bool(reminder and reminder.hide_logo),
	}


def _celebration_company(user=None):
	"""The company the celebration settings are read and written for when
	the caller does not name one: their own active Employee's company.
	Interim (plan 2026-10-04-004 U1/U4): the settings page's section reads
	the caller's company until the Email Templates group replaces it."""
	from helixhr.utils import session_company

	company = session_company(user or frappe.session.user)
	if not company:
		companies = frappe.get_all("Company", pluck="name")
		if len(companies) == 1:
			return companies[0]
		frappe.throw(_("Pick the company to configure."))
	return company


# The documented context every celebration template renders against
# (`helixhr.reminders._context`) -- surfaced to the portal so the section
# can list it the same way TemplatesSection.vue lists `template_tokens`.
CELEBRATION_TEMPLATE_TOKENS = (
	"persons",
	"names",
	"count",
	"company",
	"logo_url",
	"date",
	"portal_url",
)


@frappe.whitelist(methods=["POST"])
def save_celebration_reminder(event, subject, body, is_enabled=0, recipient_mode="All employees", recipients=None, company=None, frequency=None, hide_logo=0):
	"""HR writes the birthday/work-anniversary email and picks its audience
	from the portal (P8-U12 / P8-R5, P8-R6). Per company since plan
	2026-10-04-004 U1: `company` names whose setting this is -- the
	caller's own company when omitted, as the settings page's section
	does until the Email Templates group replaces it (U4).

	`subject`/`body` are rendered against the event's sample context in
	HelixHR's sandbox before anything is written (P8-U12's own test
	scenario: a bad template is refused at save time, not at 8am the next
	morning) -- the same sandbox `reminders._render_restricted` sends with
	(plan 2026-10-05-001 U11).
	"""
	from helixhr.reminders import EVENTS

	_assert_can_edit_email_templates()
	rate_limit_per_user("save_celebration_reminder")
	if event not in EVENTS:
		frappe.throw(_("That reminder is not offered here."))

	if isinstance(recipients, str):
		recipients = frappe.parse_json(recipients)
	recipients = recipients or []

	company = company or _celebration_company()
	_assert_template_company(company)
	# The cadence is the row's own for `holiday` (U1); the controller
	# refuses a holiday row without one and ignores the field elsewhere.
	# A wrong value is refused, mirroring the controller -- a silent
	# rewrite to Weekly would mail on a cadence HR never picked.
	if event == "holiday" and frequency not in (None, "Weekly", "Monthly"):
		frappe.throw(_("Pick how often the holiday reminder goes out: Weekly or Monthly."))
	if event == "holiday" and frequency is None:
		frequency = "Weekly"

	subject = (subject or "").strip()
	body = body or ""
	_render_celebration_or_throw(event, company, subject, body, frequency)

	reminder = frappe.db.get_value(
		"HelixHR Celebration Reminder", {"event": event, "company": company}, "name"
	)
	if reminder:
		reminder = frappe.get_doc("HelixHR Celebration Reminder", reminder)
	else:
		reminder = frappe.new_doc("HelixHR Celebration Reminder")
		reminder.event = event
		reminder.company = company

	template = _company_celebration_template(event, company)

	# `ignore_permissions=True`, not `_assert_config_write` (KTD8's own
	# framing, reused): Email Template is a shared core doctype used across
	# the whole site, not one this app owns, and the Portal Admin holds no
	# DocPerm at all (preflight enforces). `_assert_can_edit_email_templates`
	# above is the real authorisation boundary here, and the template name
	# is never caller input -- it is always
	# `reminders.celebration_template_name(event, company)` (U11: never the
	# shared seeded name, so a company created after the clone patch still
	# gets its own copy).
	template.subject = subject
	if template.use_html:
		template.response_html = body
	else:
		template.response = body
	template.save(ignore_permissions=True)

	reminder.email_template = template.name
	reminder.is_enabled = cint(is_enabled)
	reminder.recipient_mode = recipient_mode
	reminder.frequency = frequency
	reminder.hide_logo = cint(hide_logo)
	reminder.set("recipients", [{"employee": row} for row in recipients])
	# Same boundary as the template above; the controller's validate runs.
	reminder.save(ignore_permissions=True)
	# The portal owns this event: HRMS's own checkbox is read-only in Desk
	# (P8-KTD8), so HR has no way to untick it. Leaving it on means HRMS's
	# daily job keeps sending its stock email even after HR disables the
	# reminder here. `set_single_value`, not a HR Settings save, so
	# `events.hr_settings_validate` is not re-run over unrelated fields.
	frappe.db.set_single_value("HR Settings", EVENTS[event]["hrms_field"], 0)

	return _celebration_reminder_projection(event, company)


def _company_celebration_template(event, company):
	"""The (event, company) Email Template, or a new unsaved one under its
	per-company name (U11)."""
	from helixhr.reminders import celebration_template_name

	name = celebration_template_name(event, company)
	if frappe.db.exists("Email Template", name):
		return frappe.get_doc("Email Template", name)
	template = frappe.new_doc("Email Template")
	template.name = name
	template.use_html = 1
	return template


@frappe.whitelist(methods=["POST"])
def reset_celebration_template(event, company=None):
	"""Back to the shipped default wording for the caller's company only
	(U11): the default is written into that company's own Email Template and
	the row, if any, is pointed at it. Portal Admin or System Manager
	(`_celebration_gate`). The Info comment names the actor, like
	`reset_message_template`."""
	from helixhr.reminders import CELEBRATION_DEFAULTS

	company = _celebration_gate(event, "reset_celebration_template", company)
	default = CELEBRATION_DEFAULTS[event]
	template = _company_celebration_template(event, company)
	template.subject = default["subject"]
	template.use_html = 1
	template.response_html = default["body"]
	# Same reasoning as `save_celebration_reminder`: the gate above is the
	# boundary, and the name is derived, never caller input.
	template.save(ignore_permissions=True)

	reminder = frappe.db.get_value(
		"HelixHR Celebration Reminder", {"event": event, "company": company}, "name"
	)
	if reminder:
		frappe.db.set_value("HelixHR Celebration Reminder", reminder, "email_template", template.name)
	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Info",
			"reference_doctype": "Email Template",
			"reference_name": template.name,
			"content": _("{0} reset this email template to the default").format(
				frappe.utils.get_fullname(frappe.session.user)
			),
		}
	).insert(ignore_permissions=True)
	return _celebration_reminder_projection(event, company)


@frappe.whitelist()
def get_celebration_setup(company=None):
	"""Plan 2026-10-04-004 U4 (R9, R10): the Email Templates page's
	"Celebrations & holidays" group in one call -- every event's setting
	for `company`, the template-token reference, and the companies this
	caller may configure. Portal Admin and System Manager only, for any
	company: email is portal configuration, not company HR data."""
	from helixhr.reminders import EVENTS

	_assert_can_edit_email_templates()
	rate_limit_per_user("get_celebration_setup")
	try:
		company = company or _celebration_company()
	except frappe.ValidationError:
		# An editor with no Employee on a multi-company site has no single
		# company to default to (KTD3): hand the choice to the caller
		# instead of refusing -- the editor renders its company selector
		# from `companies`.
		company = None
	if company:
		_assert_template_company(company)

	return {
		"company": company,
		"companies": frappe.get_all("Company", pluck="name", order_by="company_name asc"),
		"events": {event: _celebration_reminder_projection(event, company) for event in EVENTS},
		"template_tokens": CELEBRATION_TEMPLATE_TOKENS,
	}


def _assert_template_company(company):
	"""The celebration group's company argument: any existing company (the
	gate above is role-only, not company-scoped)."""
	if not company or not frappe.db.exists("Company", company):
		frappe.throw(_("That company does not exist."))


def _assert_company_in_admin_scope(company):
	"""KTD3: the celebration group's server gate, per company. An HR
	Manager / System Manager anchored to an Employee is scoped to their own
	company (P6-R6); a Desk-only one may configure any company; anyone else
	is refused before anything is read."""
	from helixhr.utils import resolve_admin_scope

	scope = resolve_admin_scope(frappe.session.user)
	if scope["kind"] == "unscoped":
		return
	if scope["kind"] == "company" and company == scope["company"]:
		return
	frappe.throw(_("You don't have permission to do that."), frappe.PermissionError)


def _companies_in_admin_scope():
	"""The company selector's options: everything for an unscoped caller,
	the one company for an anchored one."""
	from helixhr.utils import resolve_admin_scope

	scope = resolve_admin_scope(frappe.session.user)
	if scope["kind"] == "company":
		return [scope["company"]]
	return frappe.get_all("Company", pluck="name", order_by="company_name asc")


def _celebration_sample_context(event, company, frequency=None):
	"""The preview and test-send render against the same context the real
	send builds (`reminders._context` for the two celebration events, the
	holiday sender's own shape for `holiday`), with one sample person."""
	from frappe.utils import add_days, format_date

	from helixhr.reminders import _context, _date_format, _logo_url

	if event == "holiday":
		today = getdate()
		return {
			"employee_name": "Ada Lovelace",
			"holidays": [
				{
					"date": format_date(add_days(today, 3), _date_format()),
					"description": "Company Holiday",
				}
			],
			"company": company,
			"logo_url": _logo_url(company),
			"portal_url": get_url("/helixhr"),
			"date": format_date(today, _date_format()),
			"frequency": frequency or "Weekly",
		}
	return _context(
		[{"name": "Ada Lovelace", "image": None, "date_of_joining": "2020-01-01"}],
		company,
		event,
	)


def _celebration_draft(event, company, subject, body, frequency=None, hide_logo=0, embed_logo=True):
	"""Compile then render an unsaved draft with the event's sample context
	-- the same refusal a save would give (P8-U12's compile check), the
	same restriction the real render runs under (P8-KTD7). A holiday draft
	with no cadence given renders against the saved row's own cadence, so
	the preview shows what this row will actually mail (R11)."""
	if not frequency:
		frequency = frappe.db.get_value(
			"HelixHR Celebration Reminder", {"event": event, "company": company}, "frequency"
		)
	rendered = _render_celebration_or_throw(
		event, company, subject, body, frequency, include_logo=not cint(hide_logo), embed_logo=embed_logo
	)
	return {"subject": rendered["subject"], "html": rendered["message"]}


def _render_celebration_or_throw(
	event, company, subject, body, frequency=None, include_logo=True, embed_logo=True
):
	"""Render a draft against the event's sample context in the HelixHR
	sandbox (U11); any failure is a refusal naming why, not a stack trace."""
	from helixhr.utils import render_celebration_email

	context = _celebration_sample_context(event, company, frequency)
	try:
		return render_celebration_email(
			subject or "", body or "", context, include_logo=include_logo, embed_logo=embed_logo
		)
	except Exception as exc:
		frappe.throw(_("This template cannot be used: {0}").format(exc), title=_("Template not valid"))


def _celebration_gate(event, endpoint, company=None):
	"""The draft endpoints' shared gate: Portal Admin or System Manager,
	rate-limited, a known event and an existing company. Returns the
	resolved company."""
	from helixhr.reminders import EVENTS

	_assert_can_edit_email_templates()
	rate_limit_per_user(endpoint)
	if event not in EVENTS:
		frappe.throw(_("That reminder is not offered here."))
	company = company or _celebration_company()
	_assert_template_company(company)
	return company


@frappe.whitelist(methods=["POST"])
def preview_celebration(event, subject, body, company=None, hide_logo=0):
	"""R11: the draft as the email would look, rendered with the selected
	company's own name and logo. The client shows `html` only in a
	sandboxed iframe (KTD11)."""
	company = _celebration_gate(event, "preview_celebration", company)
	rendered = _celebration_draft(event, company, subject, body, hide_logo=hide_logo, embed_logo=False)
	return {"subject": rendered["subject"], "html": rendered["html"]}


@frappe.whitelist(methods=["POST"])
def send_test_celebration(event, subject, body, company=None, hide_logo=0):
	"""R11: send the draft to the caller's own address only -- never a
	recipient the caller names, the same rule `send_test_message` holds."""
	company = _celebration_gate(event, "send_test_celebration", company)
	rendered = _celebration_draft(event, company, subject, body, hide_logo=hide_logo)
	email = frappe.db.get_value("User", frappe.session.user, "email")
	if not email:
		frappe.throw(_("Your account has no email address to send the test to."))
	frappe.sendmail(
		recipients=[email],
		subject=_("[Test] {0}").format(rendered["subject"]),
		message=rendered["html"],
	)


@frappe.whitelist()
def search_celebration_recipients(company, query=""):
	"""R12: the selected-people picker searches employees of the selected
	company only, within the editor's scope -- active, name matching."""
	_assert_can_edit_email_templates()
	rate_limit_per_user("search_celebration_recipients")
	_assert_template_company(company)

	filters = {"status": "Active", "company": company}
	if (query or "").strip():
		filters["employee_name"] = ["like", f"%{query.strip()}%"]
	return frappe.get_all(
		"Employee",
		filters=filters,
		fields=["name", "employee_name", "image"],
		order_by="employee_name asc",
		limit=20,
	)


@frappe.whitelist(methods=["POST"])
def attach_to_my_request(name):
	"""Attach one private file to a request of the caller's own (P2-R27).

	Frappe's `upload_file` cannot serve this any more: it gates on `write`
	permission for the target document, and role Employee deliberately has
	no write on HR Request. This is the same job with the ownership rule
	stated directly, plus the type and size policy the new-request sheet
	promises the employee up front.

	Idempotent by (request, file name, uploader), so "Retry upload" after a
	failed or ambiguous attempt attaches the file once rather than twice
	(P2-AE7). The multipart body is read from `frappe.request.files`, which
	is where Frappe puts an uploaded stream for any whitelisted method.
	"""
	rate_limit_per_user("attach_to_my_request")
	employee = get_current_employee()
	if frappe.db.get_value("HR Request", name, "employee") != employee:
		frappe.throw(_("That request isn't yours."), frappe.PermissionError)

	upload = (getattr(frappe.request, "files", None) or {}).get("file")
	if upload is None:
		frappe.throw(_("No file came through. Pick the file again."))

	file_name = os.path.basename(upload.filename or "").strip()
	if not file_name:
		frappe.throw(_("That file has no name. Pick another one."))

	content = upload.stream.read()
	# P2-U9 step 5. Size, extension, leading signature and -- for the two
	# OOXML types -- the container itself, all in one place that
	# `helixhr.events.file_before_insert` shares, so a File inserted by any
	# other path gets the same answer.
	validate_portal_upload(file_name, content)

	existing = frappe.db.get_value(
		"File",
		{
			"attached_to_doctype": "HR Request",
			"attached_to_name": name,
			"file_name": file_name,
			"owner": frappe.session.user,
		},
		["name", "file_name", "file_url", "file_size", "is_private"],
		as_dict=True,
	)
	if existing:
		return {**_attachment(existing), "created": False}

	doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"content": content,
			"attached_to_doctype": "HR Request",
			"attached_to_name": name,
			# Never a parameter. `helixhr.events.file_before_insert` refuses a
			# non-private file on an HR Request anyway; this is why it never
			# has to.
			"is_private": 1,
		}
	)
	doc.insert(ignore_permissions=True)
	return {**_attachment(doc), "created": True}


@frappe.whitelist(methods=["POST"])
def reply_to_my_request(name, message, expected_modified=None):
	"""The employee's half of a routed conversation (P5-R10, P5-U6).

	This is deliberately **not** `act_on_approval` (P5-KTD5): role Employee
	has no write on HR Request at all, so there is no workflow transition for
	this caller to apply. The state check is against the **stored** status --
	a reply against anything but `Waiting on Employee` is refused, the same
	staleness contract `act_on_approval` gives the worker (P5-R8) -- and the
	move back to the routed role's queue is `db_set`, after this function's
	own ownership and state checks have authorised it, exactly the way
	`act_on_approval` already writes `helixhr_decision_reason` on a
	permlevel-1 field it does not otherwise have write on.

	The reply is recorded as a Comment through `add_comment`, which always
	inserts with `ignore_permissions=True` itself -- the fourth documented
	exception the P2-U8 allow-list comment above promises: role Employee can
	read nothing on this doctype directly, including its own Comments, so
	this is the only route by which the employee's own words join the
	thread `_request_thread` projects back to them and to the routed role.
	"""
	rate_limit_per_user("reply_to_my_request")
	employee = get_current_employee()
	row = frappe.db.get_value(
		"HR Request",
		name,
		["employee", "status", "modified", "routed_to_role", "category", "subject"],
		as_dict=True,
	)
	if not row or row.employee != employee:
		frappe.throw(_("That request isn't yours."), frappe.PermissionError)
	if not expected_modified:
		frappe.throw(_("Open this request before replying, then try again."))
	if get_datetime(expected_modified) != get_datetime(row.modified):
		frappe.throw(_("Somebody changed this while you were looking at it. Reload and try again."))
	if row.status != HR_REQUEST_WAITING_ON_EMPLOYEE:
		frappe.throw(
			_("This request isn't waiting on you right now ({0}). Reload to see its status.").format(
				row.status
			)
		)

	message = (message or "").strip()
	if not message:
		frappe.throw(_("Say something before sending your reply."))
	if len(message) > _DETAILS_MAX:
		frappe.throw(_("That reply is too long. Keep it under {0} characters.").format(_DETAILS_MAX))

	doc = frappe.get_doc("HR Request", name)
	doc.add_comment("Comment", message)
	doc.db_set("status", HR_REQUEST_IN_PROGRESS)

	# U9: templated through the HelixHR sandbox; `send_notification` never
	# raises, so a mail failure cannot undo the reply.
	send_notification(
		"request_reply",
		_enabled_users_with_role(row.routed_to_role),
		{
			"employee_name": frappe.db.get_value("Employee", employee, "employee_name"),
			"category": row.category,
			"subject": row.subject,
			"reply_excerpt": message[:300],
			"action_url": frappe.utils.get_url(f"/helixhr/approvals/request/{name}"),
		},
		reference_doctype="HR Request",
		reference_name=name,
	)

	return {"name": name, "status": doc.status}


@frappe.whitelist(methods=["POST"])
def attach_to_request_reply(name):
	"""The routed role's side of the conversation's attachments (P5-R10a) --
	what makes an `HR Letter` request completable without opening Desk.

	`attach_to_my_request`'s ownership check ("is this the caller's own
	request") is inverted here: a worker must **not** be the request's own
	employee, and must otherwise be authorised to act on it right now, which
	is exactly what `_assert_may_act_on` already checks for a decision on
	the same record (P5-R9). Reusing it means a caller this method refuses
	and a caller `act_on_approval` would refuse can never disagree.
	"""
	rate_limit_per_user("attach_to_request_reply")
	if not frappe.db.exists("HR Request", name):
		frappe.throw(_(_APPROVAL_NOT_FOUND), frappe.PermissionError)
	doc = frappe.get_doc("HR Request", name)
	_assert_may_act_on(doc)
	# A closed request takes no more files, the same rule a decision on it
	# already obeys -- otherwise a worker could keep attaching to a Done or
	# Rejected request indefinitely, and the employee would keep seeing new
	# "HR attachments" on something already settled.
	_assert_still_open(doc)

	upload = (getattr(frappe.request, "files", None) or {}).get("file")
	if upload is None:
		frappe.throw(_("No file came through. Pick the file again."))

	file_name = os.path.basename(upload.filename or "").strip()
	if not file_name:
		frappe.throw(_("That file has no name. Pick another one."))

	content = upload.stream.read()
	# The same policy `attach_to_my_request` applies, so the type and size
	# rule is one rule for both sides of the conversation.
	validate_portal_upload(file_name, content)

	existing = frappe.db.get_value(
		"File",
		{
			"attached_to_doctype": "HR Request",
			"attached_to_name": name,
			"file_name": file_name,
			"owner": frappe.session.user,
		},
		["name", "file_name", "file_url", "file_size", "is_private"],
		as_dict=True,
	)
	if existing:
		return {**_attachment(existing), "created": False}

	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"content": content,
			"attached_to_doctype": "HR Request",
			"attached_to_name": name,
			"is_private": 1,
		}
	)
	file_doc.insert(ignore_permissions=True)
	return {**_attachment(file_doc), "created": True}


# ---------------------------------------------------------------------------
# Holidays (P3-U3 / P3-R10, P3-R11)
#
# HRMS v16 resolves a holiday list through Holiday List Assignment, per date:
# the employee's own assignment wins, the company's is the fallback, and an
# assignment that starts mid-year splits the year between two lists. So the
# year is resolved as *ranges* -- the same shape
# `hrms.utils.holiday_list.get_holiday_dates_between_range` uses -- and not
# as one list name resolved once, which would silently show the wrong list
# for half the year.
#
# The Holiday rows are read with `ignore_permissions`, deliberately: role
# Employee has no read on Holiday List at all (P2-R26 strict permissions),
# and the list names here are server-derived from the session's employee, so
# there is nothing a caller can steer (P3-KTD1).
# ---------------------------------------------------------------------------

# A typo or a probe, answered before any read. Payroll-era dates and a
# couple of years of planning ahead are the whole legitimate range.
_HOLIDAY_MIN_YEAR = 2000
_HOLIDAY_MAX_YEAR = 2100


@frappe.whitelist()
def get_my_holidays(year=None):
	"""The holidays HRMS resolves for the logged-in employee in one calendar
	year (P3-R10), with the next one and how many days away it is.

	Weekly offs are excluded: a list with a weekly off configured carries one
	Holiday row per Saturday and Sunday, and 104 of those would bury the
	eight days this page exists to show. The footnote says so.

	`known` is false when no list resolves for either the employee or their
	company -- the same "cannot tell" contract `get_my_attendance` uses for
	`working_days_known` (P3-R11). It is never an empty holiday year.
	"""
	employee = get_current_employee()
	today = user_today()
	current_year = getdate(today).year
	year = cint(year) or current_year
	if year < _HOLIDAY_MIN_YEAR or year > _HOLIDAY_MAX_YEAR:
		frappe.throw(_("Pick a year between {0} and {1}.").format(_HOLIDAY_MIN_YEAR, _HOLIDAY_MAX_YEAR))

	start, end = getdate(f"{year}-01-01"), getdate(f"{year}-12-31")
	spans = _holiday_list_spans(employee, start, end)
	years = _holiday_years(employee, current_year, year)

	if not spans:
		return {
			"known": False,
			"holiday_list": None,
			"year": year,
			"years": years,
			"holidays": [],
			"next": None,
		}

	holidays = {}
	for span in spans:
		rows = frappe.get_all(
			"Holiday",
			filters={
				"parent": span["holiday_list"],
				"parenttype": "Holiday List",
				"holiday_date": ["between", [str(span["from_date"]), str(span["to_date"])]],
				"weekly_off": 0,
			},
			fields=["holiday_date", "description", "is_half_day"],
			ignore_permissions=True,
		)
		for row in rows:
			date = getdate(row.holiday_date)
			holidays[str(date)] = {
				"date": str(date),
				# HR pastes rich text into these often enough that the raw
				# value reaches the screen as markup.
				"description": (frappe.utils.strip_html(row.description or "").strip() or None),
				"is_half_day": bool(row.is_half_day),
				"weekday": date.strftime("%A"),
			}

	ordered = [holidays[key] for key in sorted(holidays)]
	upcoming = next((row for row in ordered if row["date"] >= str(today)), None)

	return {
		"known": True,
		# The list covering the reference day, which is what the footnote
		# names. With a mid-year reassignment the year has two, and naming the
		# one in force is more use than naming both.
		"holiday_list": _holiday_list_at(spans, min(max(getdate(today), start), end)),
		"year": year,
		"years": years,
		"holidays": ordered,
		"next": (
			{
				"date": upcoming["date"],
				"description": upcoming["description"],
				"days_until": date_diff(upcoming["date"], today),
			}
			if upcoming
			else None
		),
	}


def _holiday_list_spans(employee, start, end):
	"""The holiday lists in force across `start`..`end`, as
	`{holiday_list, from_date, to_date}` spans in date order.

	At most two spans, because it mirrors
	`hrms.utils.holiday_list.get_holiday_dates_between_range` exactly: HRMS
	resolves the list at each end of the range and splits at the later
	assignment's start date, so a third assignment starting inside the range
	is not seen -- by HRMS either, which is the resolver every other page
	agrees with. `raise_exception=False` because "no list" is a state this
	page renders (P3-R11), not an error.
	"""
	from_list = (
		get_holiday_list_for_employee(employee, raise_exception=False, as_on=start, as_dict=True) or {}
	)
	to_list = get_holiday_list_for_employee(employee, raise_exception=False, as_on=end, as_dict=True) or {}

	if (
		from_list.get("holiday_list")
		and to_list.get("holiday_list")
		and from_list.get("holiday_list") != to_list.get("holiday_list")
	):
		split = getdate(to_list.get("from_date"))
		return [
			{"holiday_list": from_list["holiday_list"], "from_date": start, "to_date": add_days(split, -1)},
			{"holiday_list": to_list["holiday_list"], "from_date": split, "to_date": end},
		]

	resolved = from_list.get("holiday_list") or to_list.get("holiday_list")
	if resolved:
		return [{"holiday_list": resolved, "from_date": start, "to_date": end}]
	return []


def _holiday_list_at(spans, on):
	for span in spans:
		if getdate(span["from_date"]) <= getdate(on) <= getdate(span["to_date"]):
			return span["holiday_list"]
	return spans[0]["holiday_list"]


def _holiday_years(employee, current_year, requested):
	"""The years the year chip can offer: every calendar year an assigned
	holiday list covers, plus the current and requested ones so the chip
	always contains what is on screen."""
	company = frappe.db.get_value("Employee", employee, "company")
	assigned = frappe.get_all(
		"Holiday List Assignment",
		filters={"assigned_to": ["in", [employee, company]], "docstatus": 1},
		pluck="holiday_list",
		ignore_permissions=True,
	)
	years = {current_year, requested}
	if assigned:
		for row in frappe.get_all(
			"Holiday List",
			filters={"name": ["in", list(set(assigned))]},
			fields=["from_date", "to_date"],
			ignore_permissions=True,
		):
			for candidate in range(getdate(row.from_date).year, getdate(row.to_date).year + 1):
				if _HOLIDAY_MIN_YEAR <= candidate <= _HOLIDAY_MAX_YEAR:
					years.add(candidate)
	return sorted(years)


# ---------------------------------------------------------------------------
# Directory (P3-U8, P3-R22, P3-R23)
#
# Everyone's colleagues, as a server projection rather than as a list route.
#
# Role Employee cannot read another Employee at all: the User Permission each
# fixture and every real site creates scopes the doctype to the signed-in
# person's own record, and P2-R26 keeps strict user permissions on. So this
# method reads with `ignore_permissions=True` and owns the scope itself --
# Active only, this employee's own company only, and a fixed field allow-list
# (P3-KTD1). The generic Employee list stays exactly as denied as it was;
# `test_fixtures.TestStrictPermissionParity` pins that.
#
# The allow-list is the privacy boundary, not a convenience: `user_id` is a
# login identifier and never leaves the server, so the work email comes from
# `company_email` alone and the key is absent when HR has not filled it in.
# No photo and no phone number, for the same reason -- neither is needed to
# find a colleague's role and reach them (P3-R22).
# ---------------------------------------------------------------------------

# One bounded page, and the ceiling Load More may climb to (P3-R25).
_DIRECTORY_PAGE = 50
_DIRECTORY_MAX_PAGE = 200

# A search is a name, a role or a department -- 60 characters is longer than
# any of the three, and below two the needle matches most of the company, so
# it is ignored rather than run.
_DIRECTORY_QUERY_MAX = 60
_DIRECTORY_QUERY_MIN = 2

_DIRECTORY_FIELDS = (
	"name",
	"employee_name",
	"designation",
	"department",
	"reports_to",
	"company_email",
)


def _directory_manager_names(rows):
	"""Every manager named by `rows`, in one query rather than one per row."""
	ids = {row.reports_to for row in rows if row.reports_to}
	if not ids:
		return {}
	return {
		row.name: row.employee_name
		for row in frappe.get_all(
			"Employee",
			filters={"name": ["in", list(ids)]},
			fields=["name", "employee_name"],
			ignore_permissions=True,
		)
	}


def _directory_projection(row, manager_names):
	person = {
		"name": row.name,
		"employee_name": row.employee_name,
		# The monogram, from the server, so the directory's avatar and the
		# Approvals queue's are the same two letters (P3-U9).
		"initials": _initials(row.employee_name),
		"designation": row.designation or None,
		"department": row.department or None,
		"manager": row.reports_to or None,
		"manager_name": manager_names.get(row.reports_to) if row.reports_to else None,
	}
	# Absent, not empty: a key with "" in it reads on the page as an address
	# that failed to load rather than as one HR has not published.
	if row.company_email:
		person["email"] = row.company_email
	return person


def _aggregate_count(row):
	"""The count out of an aggregated `frappe.get_all` row.

	Frappe v16 refuses a `"count(name) as total"` string in `fields` and
	returns the aggregate under SQL's own name instead (`COUNT(*)`), so the
	one value in the row is read for what it is rather than by a label this
	app is free to choose.
	"""
	return cint(next(value for key, value in row.items() if key.upper().startswith("COUNT")))


def _directory_departments(company):
	"""The departments this company's active people are in, with a headcount
	each -- the desktop chips. Counted over the whole company rather than over
	the current page or search, so a chip does not move while it is being
	used."""
	rows = frappe.get_all(
		"Employee",
		filters={"status": "Active", "company": company},
		fields=["department", {"COUNT": "name"}],
		group_by="department",
		order_by="department asc",
		ignore_permissions=True,
	)
	return [
		{"name": row.department, "count": _aggregate_count(row)} for row in rows if row.department
	]


@frappe.whitelist()
def get_directory(query=None, department=None, start=0, limit=None):
	"""A bounded page of active colleagues in this employee's own company,
	by name, with the role, department, manager and work email each one has
	published (P3-R22, P3-R23).

	An employee whose record carries no company gets an empty page rather
	than an error: "we cannot tell which company you are in" is a thing for
	HR to fix, and the page says so in its own words.
	"""
	rate_limit_per_user("get_directory")
	limit = min(max(cint(limit) or _DIRECTORY_PAGE, 1), _DIRECTORY_MAX_PAGE)
	start = max(cint(start), 0)

	company = _caller_company()
	if not company:
		return {"people": [], "total": 0, "limit": limit, "start": start, "departments": []}

	filters = {"status": "Active", "company": company}
	if department:
		filters["department"] = department

	needle = (query or "").strip()[:_DIRECTORY_QUERY_MAX]
	or_filters = None
	if len(needle) >= _DIRECTORY_QUERY_MIN:
		# P8-U3: `name` (the employee id) and `company_email` widen this to
		# match what the project member picker's own placeholder already
		# promises ("Name, employee number or work email") -- this reader
		# is the one it reuses (Projects.vue's own comment explains why:
		# no admin permission required, every employee's own company).
		# Purely additive over the existing three fields, so Directory.vue's
		# own search only ever matches more, never less, and stays scoped
		# to the caller's own company exactly as before.
		or_filters = [
			["name", "like", f"%{needle}%"],
			["employee_name", "like", f"%{needle}%"],
			["designation", "like", f"%{needle}%"],
			["department", "like", f"%{needle}%"],
			["company_email", "like", f"%{needle}%"],
		]

	scope = {"filters": filters, "or_filters": or_filters, "ignore_permissions": True}
	rows = frappe.get_all(
		"Employee",
		fields=list(_DIRECTORY_FIELDS),
		order_by="employee_name asc",
		limit_start=start,
		limit_page_length=limit,
		**scope,
	)
	# `frappe.db.count` takes no or_filters, so the total comes from the same
	# scope aggregated -- one row, whether or not a search is running.
	total = _aggregate_count(frappe.get_all("Employee", fields=[{"COUNT": "*"}], **scope)[0])

	manager_names = _directory_manager_names(rows)
	return {
		"people": _with_photo_urls([_directory_projection(row, manager_names) for row in rows], key="name"),
		"total": total,
		"limit": limit,
		"start": start,
		"departments": _directory_departments(company),
	}


# ---------------------------------------------------------------------------
# Finding a person, for HR (P6-U2 / P6-R1, P6-R8, P6-R13)
#
# The administrative sibling of `get_directory` just above -- same bounded,
# paged, server-side-filtered shape -- but scoped by `resolve_admin_scope`
# (every company an admin persona may see) rather than to the caller's own
# company, and refused entirely for anyone the scope helper does not grant.
# The employee-facing directory is untouched.

_PEOPLE_SEARCH_PAGE = 50
_PEOPLE_SEARCH_MAX_PAGE = 200
_PEOPLE_SEARCH_QUERY_MIN = 2
_PEOPLE_SEARCH_QUERY_MAX = 60

_PEOPLE_SEARCH_FIELDS = (
	"name",
	"employee_name",
	"employee_number",
	"designation",
	"department",
	"company",
	"company_email",
)


def _people_search_projection(row):
	return {
		"name": row.name,
		"employee_name": row.employee_name,
		"employee_number": row.employee_number,
		"initials": _initials(row.employee_name),
		"designation": row.designation or None,
		"department": row.department or None,
		"company": row.company,
	}


@frappe.whitelist()
def search_people(query=None, start=0, limit=None):
	"""A bounded page of active employees this caller may administer,
	matched by name, employee number or work email (P6-R1, P6-R8).

	Refused server-side, before any row is read, for anyone
	`resolve_admin_scope` does not grant a scope to (P6-R6, P6-R13) --
	`PermissionError`, the same as every other admin-only read in this file.
	"""
	rate_limit_per_user("search_people")
	scope = resolve_admin_scope(frappe.session.user)
	if scope["kind"] == "none":
		frappe.throw(_("You are not authorised to look up other people."), frappe.PermissionError)

	limit = min(max(cint(limit) or _PEOPLE_SEARCH_PAGE, 1), _PEOPLE_SEARCH_MAX_PAGE)
	start = max(cint(start), 0)

	filters = admin_scope_employee_filters(scope)
	filters = {**(filters or {}), "status": "Active"}

	# Left and Inactive employees are excluded by default -- looking somebody
	# up means a current colleague unless HR says otherwise (P6 Open
	# Questions: findability of Left employees is deferred until asked).
	needle = (query or "").strip()[:_PEOPLE_SEARCH_QUERY_MAX]
	or_filters = None
	if len(needle) >= _PEOPLE_SEARCH_QUERY_MIN:
		or_filters = [
			["employee_name", "like", f"%{needle}%"],
			["employee_number", "like", f"%{needle}%"],
			["company_email", "like", f"%{needle}%"],
		]

	scope_query = {"filters": filters, "or_filters": or_filters, "ignore_permissions": True}
	rows = frappe.get_all(
		"Employee",
		fields=list(_PEOPLE_SEARCH_FIELDS),
		order_by="employee_name asc",
		limit_start=start,
		limit_page_length=limit,
		**scope_query,
	)
	total = _aggregate_count(frappe.get_all("Employee", fields=[{"COUNT": "*"}], **scope_query)[0])

	return {
		"people": _with_photo_urls([_people_search_projection(row) for row in rows], key="name"),
		"total": total,
		"limit": limit,
		"start": start,
	}


# ---------------------------------------------------------------------------
# The person view, for HR (P6-U3 / P6-R2, P6-R3, P6-R4, P6-R5, P6-R8)
#
# An explicit projection, assembled from readers the portal already has
# (P6-KTD5) -- never a second derivation of what the employee's own screens
# already compute. Every section fails independently, `get_dashboard`'s own
# shape: an absent section is named in `failed_sections`, never a broken
# screen.


def _employee_for_user(user):
	"""The Employee id and display name behind a Link-to-User value (an
	approver), or `(None, None)` -- the inverse of `get_manager_user`
	(P8-U9). Both are needed: the id is what the edit form's picker
	pre-selects (the picker's own values are employee ids, never logins,
	same as every other picker in this app), the name is what the
	read-only card renders."""
	if not user:
		return None, None
	row = frappe.db.get_value("Employee", {"user_id": user}, ["name", "employee_name"], as_dict=True)
	return (row.name, row.employee_name) if row else (None, None)


def _person_profile(employee):
	"""Identity, manager, employment status and joining date, plus the
	overview/joining/manager/shift fields P8-U8/U9 make editable and the
	approvers that follow the manager -- the
	part of the person view that is not one of the portal's other existing
	readers. Every field here is permlevel 0 (KTD3)."""
	fields = [
		"name",
		"employee_name",
		"designation",
		"department",
		"branch",
		"company_email",
		"reports_to",
		"status",
		"date_of_joining",
		"employment_type",
		"grade",
		"scheduled_confirmation_date",
		"final_confirmation_date",
		"leave_approver",
		"expense_approver",
		"shift_request_approver",
		"default_shift",
		"holiday_list",
	]
	data = frappe.db.get_value("Employee", employee, fields, as_dict=True)
	data["manager_name"] = (
		frappe.db.get_value("Employee", data.reports_to, "employee_name") if data.reports_to else None
	)
	for field in APPROVER_FIELDS:
		data[f"{field}_name"] = _employee_for_user(data[field])[1]
	# Plan 2026-10-07-001 R7: the approvers follow `reports_to`, so when
	# they are empty the card says which part of the reporting line to fix.
	data["approver_problem"] = approver_problem(data.reports_to)
	return data


def _can_open_desk(user):
	"""Whether `user` can actually reach Desk -- a System User holding a
	role with `desk_access` -- rather than "holds an HR role" (P6-KTD4). The
	two are correlated today (an `IT Team` holder is a Website User and
	cannot) but nothing here assumes that stays true."""
	if user == "Administrator":
		return True
	# The user type is the whole answer, by Frappe's own definition: every
	# System User is automatically given the `Desk User` role (desk_access=1,
	# `frappe.permissions.AUTOMATIC_ROLES`), so a "holds a desk_access role"
	# check on top of this is true for every System User and false for every
	# Website User -- i.e. the same test, done twice. A Website User who has
	# been handed `HR Manager` is still refused here: the role does not make
	# Desk load for them, and this flag must not say otherwise.
	return frappe.db.get_value("User", user, "user_type") == "System User"


def _caller_company():
	"""The company a company-wide read (Directory, Organisation) is about.

	The caller's own Active Employee's company, as it always was. An admin
	with no Employee record at all -- `resolve_admin_scope`'s "unscoped"
	persona, the desk-only portal's user -- has no company of their own, so
	they get the site's default company instead of an empty page. Anyone
	else with no Active Employee is refused -- in particular an HR Manager
	whose Employee is Left or Inactive resolves to scope "none" and never to
	the fallback. Refused explicitly: HRMS's `get_current_employee()` means
	to raise `PermissionError` here but calls `.get` on its own `None` first,
	which surfaced as a 500.
	"""
	info = get_current_employee_info()
	if info:
		return info.get("company")
	if resolve_admin_scope(frappe.session.user)["kind"] == "unscoped":
		return frappe.db.get_single_value("Global Defaults", "default_company")
	frappe.throw(_("Employee not found"), frappe.PermissionError)


# Who gets the portal's "Open Desk" button, and the portal without an
# Employee record of their own: the HR and administration roles. Not every
# System User -- an ordinary employee is one too, and must still see "not
# set up" when HR has not linked them.
_PORTAL_DESK_ROLES = frozenset({"HR Manager", "HR User", "System Manager"})


def _portal_desk_url(user):
	"""The Desk URL for the shell's button, or None when `user` should not
	be shown one. Built with `get_url`, so a site with `host_name` set sends
	HR to the Desk host rather than the portal host that 404s /desk."""
	if not _can_open_desk(user):
		return None
	if user != "Administrator" and not set(frappe.get_roles(user)) & _PORTAL_DESK_ROLES:
		return None
	return get_url("/desk")


def _report_filter_query(filters):
	"""A simple-value filter dict as the `key=value&...` query string
	`get_url_to_report_with_filters` expects -- built the same way Frappe's
	own `get_link_to_report` does for a non-Report-Builder report."""
	from urllib.parse import quote

	return "&".join(f"{key}={quote(str(value))}" for key, value in filters.items())


def get_report_url(report, filters=None):
	"""A curated report's Desk URL, pre-filtered when `filters` is given
	(P6-R10), built by Frappe's own `get_url_to_report*` helpers (P6-KTD3)
	-- never a hand-concatenated Desk path."""
	if filters:
		return get_url_to_report_with_filters(report, _report_filter_query(filters))
	return get_url_to_report(report)


@frappe.whitelist()
def get_report_link(report, employee=None):
	"""A curated report's Desk URL, pre-filtered to `employee` when given
	(P6-R9, P6-R10) -- the one method in this plan that hands out a Desk URL
	outside `get_person`, checked here server-side rather than left to the
	frontend to merely hide (P6-R8's standard, applied to reports too):
	`report` must be on the curated list, the caller must hold the same
	admin scope every other read in this plan requires, and -- P6-KTD4's own
	extra condition -- must be able to reach Desk at all (P6-R12).
	"""
	rate_limit_per_user("get_report_link")
	scope = resolve_admin_scope(frappe.session.user)
	if scope["kind"] == "none":
		frappe.throw(_("You are not authorised to open reports here."), frappe.PermissionError)
	from helixhr.reports import wrapped_report_names

	if report not in wrapped_report_names():
		frappe.throw(_("That report is not offered here."), frappe.PermissionError)
	if not _can_open_desk(frappe.session.user):
		frappe.throw(_("You do not have access to Frappe's Desk."), frappe.PermissionError)

	filters = None
	if employee:
		if not employee_in_admin_scope(employee, scope):
			frappe.throw(_("You are not authorised to view this person."), frappe.PermissionError)
		filters = {"employee": employee}

	return get_report_url(report, filters)


# ---------------------------------------------------------------------------
# Plan 2026-10-04-001 U2: reports inside the portal.
#
# `run_report` replaces P7-U8's `run_portal_report` and `get_billable_hours`
# (KTD13). The catalog, engines and shaper live in `helixhr/reports.py`; this
# endpoint is the gate. Wrapped HRMS reports never go through
# `frappe.desk.query_report.run` as the portal user, and no portal role holds
# `report` on Timesheet -- `helixhr.preflight.check_no_timesheet_report_permission`
# is the standing guard.
# ---------------------------------------------------------------------------

_REPORT_NOT_OFFERED = "That report is not offered here."


@frappe.whitelist()
def run_report(report_key, filters=None, group_by=None, sort=None, **kwargs):
	"""Run one catalog report for the screen (R8, R9, R22).

	Access comes from `resolve_report_access` alone: an unknown key, a
	deny-listed report and a report this caller may not run all get the same
	refusal. `filters` is allowlisted against the entry's filter specs,
	company is forced to the caller's scope, and entity filters must be plain
	in-scope strings (see `reports.resolve_filters`). Anything else in the
	request -- including a key naming a user to run as -- lands in
	``**kwargs`` and is never read.

	Returns ``{columns, rows, total_rows, totals, groups_applied, truncated,
	filters_removed, filters, can_export, extra}``. ``rows`` is shaped (``_kind`` row /
	subtotal / total) and capped at `reports.SCREEN_ROW_CAP` data rows at a
	group boundary; ``totals`` and the trailing total row cover every row.
	"""
	from helixhr import reports
	from helixhr.utils import resolve_report_access

	rate_limit_per_user("run_report")
	access = resolve_report_access(frappe.session.user, report_key)
	if not access["can_run"]:
		frappe.throw(_(_REPORT_NOT_OFFERED), frappe.PermissionError)

	result = reports.run(report_key, access["scope"], filters, group_by, sort)
	rows, truncated = reports.cap_rows(result["shaped"]["rows"])
	return {
		"columns": result["columns"],
		"rows": rows,
		"total_rows": result["shaped"]["total_rows"],
		"totals": result["shaped"]["totals"],
		"groups_applied": result["groups_applied"],
		"truncated": truncated,
		"filters_removed": result["filters_removed"],
		"filters": result["filters"],
		"can_export": access["can_export"],
		# U7: the flagship's task x day grid and pending-hours figure.
		"extra": result["extra"],
	}


def _runnable_reports(user):
	"""``(entry, access)`` for every catalog entry ``user`` may run, in
	catalog order. The single source for the catalog and the nav flag."""
	from helixhr import reports
	from helixhr.utils import resolve_report_access

	for entry in reports.CATALOG:
		access = resolve_report_access(user, entry["key"])
		if access["can_run"]:
			yield entry, access


def _can_run_reports(user):
	return any(True for _pair in _runnable_reports(user))


@frappe.whitelist()
def get_report_catalog():
	"""U4: the catalog entries this caller may run, with filter specs and
	``can_export`` per entry -- the Reports page never decides access.
	``can_open_in_desk`` marks the `frappe`-engine entries `get_report_link`
	would actually hand a Desk URL out for."""
	from helixhr import reports

	rate_limit_per_user("get_report_catalog")
	user = frappe.session.user
	desk = resolve_admin_scope(user)["kind"] != "none" and _can_open_desk(user)
	return [
		{**reports.client_entry(entry, access), "can_open_in_desk": desk and entry["engine"] == "frappe"}
		for entry, access in _runnable_reports(user)
	]


@frappe.whitelist()
def search_report_options(report_key, filter, query=None, value=None, context=None, **kwargs):
	"""U3: typeahead options for one report filter, scoped exactly like the
	report itself. Access is resolved for ``report_key`` first (the uniform
	refusal otherwise); only filter types the entry declares are served; at
	most `reports.OPTIONS_LIMIT` results; a query under two characters
	returns nothing. ``value`` resolves one chosen value's label (URL load).
	``context`` carries a dependent picker's parent, e.g. ``{"project"}``
	for tasks."""
	from helixhr import reports
	from helixhr.utils import resolve_report_access

	rate_limit_per_user("search_report_options")
	access = resolve_report_access(frappe.session.user, report_key)
	if not access["can_run"]:
		frappe.throw(_(_REPORT_NOT_OFFERED), frappe.PermissionError)
	return reports.search_options(
		reports.get_entry(report_key), filter, access["scope"], query=query, value=value, context=context
	)


# --- Report export (plan 2026-10-04-001 U5, resolved decision 3) ----------
#
# POST `request_export` checks access with ``export_scope``, runs the report
# through the same runner and shaper as the screen, writes the audit row and
# hands back a one-time token; GET `download_export` streams the bytes. The
# split keeps the expensive work on a POST (CSRF-checked, commits the audit
# row) and the download a plain navigation a phone browser can save.

_EXPORT_TOKEN_PREFIX = "helixhr-report-export|"
_EXPORT_TOKEN_SECONDS = 300
_EXPORT_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
_EXPORT_TOO_LARGE = "This export is too large to download here. Narrow the filters and try again."
_EXPORT_GONE = "That export has expired. Export it again."
_EXPORT_LOG_PAGE_MAX = 100


def _export_fingerprint(report_key, fmt, filters, group_by, sort, hidden):
	"""Stable hash of one export request (U13 dedups queued exports on it)."""
	import hashlib

	hidden = sorted(hidden) if hidden is not None else None
	payload = json.dumps([report_key, fmt, filters, group_by, sort, hidden], sort_keys=True, default=str)
	return hashlib.sha256(payload.encode()).hexdigest()


@frappe.whitelist(methods=["POST"])
# wkhtmltopdf and a 10,000-row workbook are CPU-bound, so the same Redis
# semaphore `download_my_payslip` uses bounds concurrent exports per site.
@frappe.concurrent_limit()
def request_export(report_key, format, filters=None, group_by=None, sort=None, hidden=None, **kwargs):
	"""Export one catalog report as ``csv``, ``xlsx`` or ``pdf`` (R11-R14).

	Requires ``can_export`` and runs over ``export_scope`` (never the run
	scope, which can be wider). ``hidden`` names on-screen hidden columns,
	which the file leaves out. Up to `reports.INLINE_EXPORT_CAP` rows the file
	is built now and ``{token, export, filename, row_count}`` returned; the
	token is good for one `download_export` by this user within five minutes.
	Up to `reports.BACKGROUND_EXPORT_CAP` it is queued (U13) and
	``{export, status}`` returned; above that, refused with a "narrow the
	filters" sentence.
	"""
	from helixhr import reports
	from helixhr.utils import resolve_report_access

	rate_limit_per_user("request_export")
	access = resolve_report_access(frappe.session.user, report_key)
	if not access["can_export"]:
		frappe.throw(_(_REPORT_NOT_OFFERED), frappe.PermissionError)
	if format not in reports.EXPORT_FORMATS:
		frappe.throw(_("Choose CSV, Excel or PDF."))

	entry = reports.get_entry(report_key)
	scope = access["export_scope"]
	# None = the screen never chose: the default-hidden ID columns stay out.
	hidden = reports._parse(hidden, None)
	if not isinstance(hidden, list) or not all(isinstance(field, str) for field in hidden):
		hidden = None
	result = reports.run(report_key, scope, filters, group_by, sort)
	total_rows = result["shaped"]["total_rows"]

	raw = reports._parse(filters, {})
	sort = reports._parse(sort, None)
	fingerprint = _export_fingerprint(
		report_key, format, result["filters"], result["groups_applied"], sort, hidden
	)
	row = {
		"doctype": "HelixHR Report Export",
		"report_key": report_key,
		"report_label": entry["label"],
		"format": format,
		"company": scope.get("company")
		or (reports._scope_company(scope, raw) if scope["kind"] == "unscoped" else None),
		"row_count": total_rows,
		"filters": json.dumps(result["filters"], sort_keys=True, default=str),
		"group_by": ", ".join(result["groups_applied"]),
		"filters_hash": fingerprint,
	}

	mode = reports.export_mode(format, total_rows)
	if mode == "refused":
		frappe.throw(_(_EXPORT_TOO_LARGE))
	if mode == "background":
		return _queue_export(row, result, sort, hidden)

	content, filename, content_type = reports.build_export(entry, result, format, scope, raw, hidden)
	log = frappe.get_doc({**row, "mode": "Inline", "status": "Ready", "file_name": filename}).insert(
		ignore_permissions=True
	)

	token = frappe.generate_hash(length=32)
	frappe.cache.set_value(
		_EXPORT_TOKEN_PREFIX + token,
		{"user": frappe.session.user, "content": content, "filename": filename, "content_type": content_type},
		expires_in_sec=_EXPORT_TOKEN_SECONDS,
	)
	return {"token": token, "export": log.name, "filename": filename, "row_count": total_rows}


@frappe.whitelist(methods=["GET"])
def download_export(token):
	"""Stream one inline export prepared by `request_export`. The token is
	single-use, short-lived and bound to the user who asked for it; anyone
	else -- or a second use -- gets the same "expired" refusal."""
	rate_limit_per_user("download_export")
	key = _EXPORT_TOKEN_PREFIX + token if isinstance(token, str) and _EXPORT_TOKEN_RE.match(token) else None
	payload = frappe.cache.get_value(key) if key else None
	if not payload or payload.get("user") != frappe.session.user:
		frappe.throw(_(_EXPORT_GONE), frappe.PermissionError)
	frappe.cache.delete_value(key)

	frappe.local.response.filename = payload["filename"]
	frappe.local.response.filecontent = payload["content"]
	frappe.local.response.content_type = payload["content_type"]
	frappe.local.response.type = "download"
	# Same correction `download_my_payslip` makes: RFC 5987 filename, and
	# never cached by a browser or proxy.
	frappe.local.response_headers["Content-Disposition"] = (
		f"attachment; filename*=UTF-8''{quote(payload['filename'])}"
	)
	frappe.local.response_headers["Cache-Control"] = "no-store"


@frappe.whitelist()
def get_export_log(start=0, page_length=50):
	"""System Manager / Portal Admin: who exported what, newest first --
	metadata only, never a file. A company-scoped caller sees its company's
	rows; a caller whose scope resolves to none sees no rows. HR Manager is
	refused since plan 2026-10-06-001 U3 (R6)."""
	rate_limit_per_user("get_export_log")
	if not can_admin_portal(frappe.session.user):
		frappe.throw(_("You don't have permission to do that."), frappe.PermissionError)
	start = max(cint(start), 0)
	page_length = min(max(cint(page_length), 1), _EXPORT_LOG_PAGE_MAX)

	scope = resolve_portal_admin_scope(frappe.session.user)
	if scope["kind"] == "none":
		return {"rows": [], "has_more": False}
	filters = {"company": scope["company"]} if scope["kind"] == "company" else {}
	rows = frappe.get_all(
		"HelixHR Report Export",
		filters=filters,
		fields=[
			"name",
			"owner",
			"creation",
			"report_key",
			"report_label",
			"format",
			"mode",
			"status",
			"row_count",
			"company",
			"filters",
			"group_by",
		],
		order_by="creation desc",
		start=start,
		limit=page_length + 1,
		ignore_permissions=True,
	)
	for row in rows:
		row["user_name"] = frappe.utils.get_fullname(row.owner)
		row["filters"] = frappe.parse_json(row.filters) if row.filters else {}
	return {"rows": rows[:page_length], "has_more": len(rows) > page_length}


# --- Background exports (plan 2026-10-04-001 U13, resolved decision 5) -----

_EXPORT_ACTIVE = ("Queued", "Running")
_EXPORT_USER_ACTIVE_CAP = 2
_MY_EXPORTS_LIMIT = 20


def _queue_export(row, result, sort, hidden):
	"""Insert a Queued background export and enqueue its job -- or hand back
	the caller's identical Queued/Running one. The job re-resolves access."""
	user = frappe.session.user
	existing = frappe.get_all(
		"HelixHR Report Export",
		filters={"owner": user, "filters_hash": row["filters_hash"], "status": ["in", _EXPORT_ACTIVE]},
		fields=["name", "status"],
		limit=1,
	)
	if existing:
		return {"export": existing[0].name, "status": existing[0].status, "row_count": row["row_count"]}
	if (
		frappe.db.count("HelixHR Report Export", {"owner": user, "status": ["in", _EXPORT_ACTIVE]})
		>= _EXPORT_USER_ACTIVE_CAP
	):
		frappe.throw(_("You already have two exports being prepared. Try again when one is ready."))

	log = frappe.get_doc({**row, "mode": "Background", "status": "Queued"}).insert(ignore_permissions=True)
	frappe.enqueue(
		"helixhr.reports.run_background_export",
		queue="long",
		job_id=f"report-export:{user}:{row['filters_hash']}",
		deduplicate=True,
		# The job reads the row, so it must not start before this commits.
		enqueue_after_commit=True,
		export=log.name,
		filters=result["filters"],
		group_by=result["groups_applied"],
		sort=sort,
		hidden=hidden,
	)
	return {"export": log.name, "status": "Queued", "row_count": row["row_count"]}


@frappe.whitelist()
def list_my_exports():
	"""The caller's own background exports, newest first (the "My exports"
	panel). Metadata only; the file goes through `download_report_export`."""
	from helixhr.helixhr.doctype.helixhr_report_export.helixhr_report_export import FILE_RETENTION_DAYS

	rate_limit_per_user("list_my_exports")
	rows = frappe.get_all(
		"HelixHR Report Export",
		filters={"owner": frappe.session.user, "mode": "Background"},
		fields=["name", "creation", "report_key", "report_label", "format", "status", "row_count", "file_name"],
		order_by="creation desc",
		limit=_MY_EXPORTS_LIMIT,
		ignore_permissions=True,
	)
	for row in rows:
		row["expires_on"] = (
			frappe.utils.add_days(row.creation, FILE_RETENTION_DAYS) if row.status == "Ready" else None
		)
	return rows


@frappe.whitelist(methods=["GET"])
def download_report_export(export):
	"""Stream a Ready background export to its requester -- nobody else, HR
	Manager included (the row and its private File are owner-only)."""
	rate_limit_per_user("download_export")
	row = (
		frappe.db.get_value(
			"HelixHR Report Export", export, ["owner", "status", "file", "file_name", "format"], as_dict=True
		)
		if isinstance(export, str)
		else None
	)
	if not row or row.owner != frappe.session.user or row.status != "Ready" or not row.file:
		frappe.throw(_(_EXPORT_GONE), frappe.PermissionError)
	file_name = frappe.db.get_value(
		"File", {"file_url": row.file, "attached_to_doctype": "HelixHR Report Export", "attached_to_name": export}
	)
	if not file_name:
		frappe.throw(_(_EXPORT_GONE), frappe.PermissionError)
	from helixhr import reports

	frappe.local.response.filename = row.file_name
	# Raw bytes: File.get_content decodes text and drops the CSV's BOM.
	with open(frappe.get_doc("File", file_name).get_full_path(), "rb") as handle:
		frappe.local.response.filecontent = handle.read()
	frappe.local.response.content_type = reports._CONTENT_TYPES[row.format]
	frappe.local.response.type = "download"
	frappe.local.response_headers["Content-Disposition"] = (
		f"attachment; filename*=UTF-8''{quote(row.file_name)}"
	)
	frappe.local.response_headers["Cache-Control"] = "no-store"


# --- Saved report views (plan 2026-10-04-001 U12, resolved decision 10) -----
#
# A view stores the URL state of `frontend/src/lib/reportQuery.js`
# (``{<filter>: value, group, sort, hide}``). Applying one is a plain
# `run_report` as the viewer, so an out-of-scope entity value comes back as
# ``filters_removed`` with no rows -- never widened (resolved decision 8).

_VIEW_FIELD_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_VIEW_VALUE_MAX = 140
_VIEW_QUERY_MAX = 4000
_VIEW_LABEL_MAX = 80


def _viewer_company(user, scope):
	"""The company a view is shared within: the scope's company, else the
	caller's Active Employee's (project-scoped tiers). None when unscoped."""
	if scope["kind"] == "unscoped":
		return None
	return scope.get("company") or frappe.db.get_value(
		"Employee", {"user_id": user, "status": "Active"}, "company"
	)


def _clean_view_query(entry, query):
	"""Validate a saved query against the entry's own filter/group specs."""
	query = frappe.parse_json(query) if isinstance(query, str) else query
	if not isinstance(query, dict):
		frappe.throw(_("Invalid view."))
	filter_names = {spec["name"] for spec in entry["filters"]}
	groupable = set(entry["group_by"] or ())
	clean = {}
	for key, value in query.items():
		if isinstance(value, bool) or not isinstance(value, (str, int)):
			frappe.throw(_("Invalid view."))
		value = str(value)
		if not value:
			continue
		if len(value) > _VIEW_VALUE_MAX:
			frappe.throw(_("Invalid view."))
		if key in filter_names:
			clean[key] = value
		elif key in ("group", "hide"):
			fields = value.split(",")
			if not all(_VIEW_FIELD_RE.match(field) for field in fields):
				frappe.throw(_("Invalid view."))
			if key == "group" and (len(fields) > 2 or not set(fields) <= groupable):
				frappe.throw(_("This report cannot be grouped that way."))
			clean[key] = value
		elif key == "sort":
			if not _VIEW_FIELD_RE.match(value.removeprefix("-")):
				frappe.throw(_("Invalid view."))
			clean[key] = value
		else:
			frappe.throw(_("Invalid view."))
	if len(json.dumps(clean)) > _VIEW_QUERY_MAX:
		frappe.throw(_("Invalid view."))
	return clean


def _view_access(report_key):
	from helixhr.utils import resolve_report_access

	access = resolve_report_access(frappe.session.user, report_key)
	if not access["can_run"]:
		frappe.throw(_(_REPORT_NOT_OFFERED), frappe.PermissionError)
	return access


def _can_delete_view(view, user):
	if view.owner == user:
		return True
	if view.visibility != "Shared" or not _is_hr(user):
		return False
	scope = resolve_admin_scope(user)
	return scope["kind"] == "unscoped" or (scope["kind"] == "company" and scope["company"] == view.company)


@frappe.whitelist()
def list_report_views(report_key):
	"""The caller's own views of ``report_key`` plus views shared within
	their company. A report the caller can no longer run lists nothing (the
	views are kept, not deleted)."""
	rate_limit_per_user("list_report_views")
	access = _view_access(report_key)
	user = frappe.session.user
	company = _viewer_company(user, access["scope"])
	shared = {"visibility": "Shared", "report_key": report_key}
	if access["scope"]["kind"] != "unscoped":
		if not company:
			shared = None
		else:
			shared["company"] = company
	fields = ["name", "owner", "label", "visibility", "company", "query", "creation"]
	rows = frappe.get_all(
		"HelixHR Report View", filters={"owner": user, "report_key": report_key}, fields=fields
	)
	if shared:
		rows += frappe.get_all(
			"HelixHR Report View", filters={**shared, "owner": ["!=", user]}, fields=fields
		)
	rows.sort(key=lambda row: row.label.lower())
	return [
		{
			"name": row.name,
			"label": row.label,
			"visibility": row.visibility,
			"query": frappe.parse_json(row.query) if row.query else {},
			"is_owner": row.owner == user,
			"owner_name": frappe.utils.get_fullname(row.owner),
			"can_delete": _can_delete_view(row, user),
		}
		for row in rows
	]


@frappe.whitelist(methods=["POST"])
def save_report_view(report_key, label, query=None, visibility="Private", name=None):
	"""Create a view, or (``name``) update the caller's own. Labels are
	unique per owner and report; ``query`` is validated against the entry."""
	from helixhr import reports

	rate_limit_per_user("save_report_view")
	access = _view_access(report_key)
	user = frappe.session.user
	label = (label or "").strip() if isinstance(label, str) else ""
	if not label or len(label) > _VIEW_LABEL_MAX:
		frappe.throw(_("Give the view a name of up to {0} characters.").format(_VIEW_LABEL_MAX))
	if visibility not in ("Private", "Shared"):
		frappe.throw(_("Invalid view."))
	clean = _clean_view_query(reports.get_entry(report_key), query or {})

	if name:
		doc = frappe.get_doc("HelixHR Report View", name) if isinstance(name, str) else None
		if not doc or doc.owner != user or doc.report_key != report_key:
			frappe.throw(_("You don't have permission to do that."), frappe.PermissionError)
	else:
		doc = frappe.new_doc("HelixHR Report View")
		doc.report_key = report_key
	if frappe.db.exists(
		"HelixHR Report View",
		{"owner": user, "report_key": report_key, "label": label, "name": ["!=", doc.name or ""]},
	):
		frappe.throw(_("You already have a view called {0} for this report.").format(label))

	doc.update(
		{
			"label": label,
			"visibility": visibility,
			"query": json.dumps(clean, sort_keys=True),
			"company": _viewer_company(user, access["scope"]),
		}
	)
	doc.save(ignore_permissions=True)
	return {"name": doc.name, "label": doc.label, "visibility": doc.visibility, "query": clean}


@frappe.whitelist(methods=["POST"])
def delete_report_view(name):
	"""The owner deletes their view; an HR Manager in the view's company
	(or unscoped) may also delete a shared one."""
	rate_limit_per_user("delete_report_view")
	view = (
		frappe.db.get_value("HelixHR Report View", name, ["name", "owner", "visibility", "company"], as_dict=True)
		if isinstance(name, str)
		else None
	)
	if not view or not _can_delete_view(view, frappe.session.user):
		frappe.throw(_("You don't have permission to do that."), frappe.PermissionError)
	frappe.delete_doc("HelixHR Report View", view.name, ignore_permissions=True)


# --- Report access matrix (plan 2026-10-04-001 U6, R20, R21) ---------------

_REPORT_ACCESS_FLAGS = ("hr_user_run", "hr_user_export", "dm_run", "dm_export")


def _assert_report_access_admin():
	if not can_admin_portal(frappe.session.user):
		frappe.throw(_("You don't have permission to do that."), frappe.PermissionError)


@frappe.whitelist()
def get_report_access():
	"""Every catalog entry with its HR User / Delivery Manager run/export
	flags. ``dm_allowed`` is false where the catalog forbids Delivery Manager
	(no project scope); HR Manager and Report Manager rights are fixed."""
	from helixhr import reports

	rate_limit_per_user("get_report_access")
	_assert_report_access_admin()
	saved = {
		row.name: row
		for row in frappe.get_all("HelixHR Report Access", fields=["name", *_REPORT_ACCESS_FLAGS])
	}
	return [
		{
			"key": entry["key"],
			"label": entry["label"],
			"family": entry["family"],
			"dm_allowed": "project" in entry["scopes"],
			**{
				flag: cint(saved[entry["key"]].get(flag)) if entry["key"] in saved else 0
				for flag in _REPORT_ACCESS_FLAGS
			},
		}
		for entry in reports.CATALOG
	]


@frappe.whitelist(methods=["POST"])
def save_report_access(rows):
	"""Save a batch of matrix rows ``[{key, hr_user_run, ...}]``, all or
	nothing: every row is checked first (catalog key, Delivery Manager only
	on project-scoped entries, export implies run), and one bad row rejects
	the batch with a sentence naming that report. Each row then goes through
	``doc.save()`` so the doctype's own validation runs too."""
	from helixhr import reports

	rate_limit_per_user("save_report_access")
	_assert_report_access_admin()
	rows = frappe.parse_json(rows) if isinstance(rows, str) else rows
	if not isinstance(rows, list) or not rows or len(rows) > len(reports.CATALOG):
		frappe.throw(_("Nothing to save."))

	clean, seen = [], set()
	for row in rows:
		entry = reports.get_entry(row.get("key")) if isinstance(row, dict) else None
		if not entry or entry["key"] in seen:
			frappe.throw(_("One of these reports is not in the catalog. Reload and try again."))
		seen.add(entry["key"])
		flags = {flag: 1 if row.get(flag) in (1, True, "1") else 0 for flag in _REPORT_ACCESS_FLAGS}
		label = _(entry["label"])
		if (flags["dm_run"] or flags["dm_export"]) and "project" not in entry["scopes"]:
			frappe.throw(_("{0}: Delivery Manager can't be given this report.").format(label))
		if (flags["hr_user_export"] and not flags["hr_user_run"]) or (
			flags["dm_export"] and not flags["dm_run"]
		):
			frappe.throw(_("{0}: export needs run as well.").format(label))
		clean.append((entry["key"], flags))

	frappe.db.savepoint("save_report_access")
	try:
		for key, flags in clean:
			if frappe.db.exists("HelixHR Report Access", key):
				doc = frappe.get_doc("HelixHR Report Access", key)
			else:
				doc = frappe.new_doc("HelixHR Report Access")
				doc.report_key = key
			if _is_hr(frappe.session.user):
				_assert_config_write(doc)
			else:
				# Portal Admin holds no DocPerm on purpose (preflight FAILs on a
				# write/create grant); `_assert_report_access_admin` is its gate.
				doc.flags.ignore_permissions = True
			_apply_allowed_fields(doc, flags, _REPORT_ACCESS_FLAGS)
			doc.save()
	except Exception:
		frappe.db.rollback(save_point="save_report_access")
		raise
	return get_report_access()


# --- Portal roles (HelixHR Portal Admin) -------------------------------------

_PORTAL_ROLE_RESULTS = 20


def _assert_portal_admin():
	"""Portal Admin / System Manager with a non-empty scope (HR Manager and
	HR User are refused since plan 2026-10-06-001 U3)."""
	user = frappe.session.user
	scope = resolve_portal_admin_scope(user) if can_admin_portal(user) else {"kind": "none"}
	if scope["kind"] == "none":
		frappe.throw(_("You don't have permission to do that."), frappe.PermissionError)
	return scope


def _role_holder_rows(employees):
	users = [row.user_id for row in employees]
	held = {}
	for row in frappe.get_all(
		"Has Role",
		filters={"parenttype": "User", "parent": ["in", users], "role": ["in", MANAGED_PORTAL_ROLES]},
		fields=["parent", "role"],
	):
		held.setdefault(row.parent, set()).add(row.role)
	return [
		{
			"employee": row.name,
			"employee_name": row.employee_name,
			"user": row.user_id,
			"roles": {role: role in held.get(row.user_id, ()) for role in MANAGED_PORTAL_ROLES},
		}
		for row in employees
	]


@frappe.whitelist()
def get_portal_role_holders(query=None):
	"""Active employees in the caller's scope with a User, and which of the
	four portal-only roles each holds. With no ``query``: everyone holding at
	least one of them. With a ``query`` (2+ characters): a name search.
	Employee name, id and user only -- no HR data."""
	rate_limit_per_user("get_portal_role_holders")
	scope = _assert_portal_admin()
	filters = {"status": "Active", "user_id": ["is", "set"]}
	if scope["kind"] == "company":
		filters["company"] = scope["company"]
	query = (query or "").strip() if isinstance(query, str) else ""
	or_filters = None
	if query:
		if len(query) < 2:
			return {"roles": list(MANAGED_PORTAL_ROLES), "rows": []}
		like = f"%{query[:80]}%"
		or_filters = {"employee_name": ["like", like], "name": ["like", like]}
	else:
		holders = frappe.get_all(
			"Has Role",
			filters={"parenttype": "User", "role": ["in", MANAGED_PORTAL_ROLES]},
			pluck="parent",
			distinct=True,
		)
		if not holders:
			return {"roles": list(MANAGED_PORTAL_ROLES), "rows": []}
		filters["user_id"] = ["in", holders]
	employees = frappe.get_all(
		"Employee",
		filters=filters,
		or_filters=or_filters,
		fields=["name", "employee_name", "user_id"],
		order_by="employee_name asc",
		limit=_PORTAL_ROLE_RESULTS if query else 0,
	)
	return {"roles": list(MANAGED_PORTAL_ROLES), "rows": _role_holder_rows(employees)}


@frappe.whitelist(methods=["POST"])
def set_portal_role(employee, role, enabled):
	"""Grant or remove one of the four portal-only roles on ``employee``'s
	User. Refused: any other role, an employee outside the caller's scope
	(same refusal whether or not it exists), and the caller's own User. The
	User is saved through ``doc.save()`` so its own validation runs, and an
	Info comment on it records who changed what."""
	rate_limit_per_user("set_portal_role")
	scope = _assert_portal_admin()
	if not isinstance(role, str) or role not in MANAGED_PORTAL_ROLES:
		frappe.throw(_("That role can't be managed here."), frappe.PermissionError)
	target = None
	if isinstance(employee, str) and employee:
		target = frappe.db.get_value(
			"Employee", employee, ["name", "employee_name", "user_id", "status", "company"], as_dict=True
		)
	if (
		not target
		or target.status != "Active"
		or not target.user_id
		or (scope["kind"] == "company" and target.company != scope["company"])
	):
		frappe.throw(_("You are not authorised to change this person's roles."), frappe.PermissionError)
	if target.user_id in (frappe.session.user, "Administrator", "Guest"):
		frappe.throw(_("You can't change your own roles here."), frappe.PermissionError)

	enabled = enabled in (1, True, "1", "true")
	user = frappe.get_doc("User", target.user_id)
	held = role in [row.role for row in user.roles]
	if held != enabled:
		if enabled:
			user.append_roles(role)
		else:
			user.set("roles", [row for row in user.roles if row.role != role])
		# The caller holds no write on User (Portal Admin has no DocPerm at
		# all); the gate above is the permission. Validation still runs.
		user.flags.ignore_permissions = True
		user.save()
		if (role in [row.role for row in user.roles]) != enabled:
			# A Role Profile on the User re-derives its roles on save.
			frappe.throw(_("This person's roles come from a role profile. Change it in Desk."))
		user.add_comment(
			"Info",
			_("{0} {1} {2} in the HelixHR portal").format(
				frappe.utils.get_fullname(frappe.session.user),
				_("granted") if enabled else _("removed"),
				role,
			),
		)
		frappe.clear_cache(user=target.user_id)
	return _role_holder_rows(
		[frappe._dict(name=target.name, employee_name=target.employee_name, user_id=target.user_id)]
	)[0]


# --- Approvers cleanup (plan 2026-10-07-001 U4) ------------------------------

_APPROVER_CLEANUP_MAX = 200


@frappe.whitelist()
def get_approver_cleanup():
	"""The Approvers cleanup preview, for a Portal Admin's scope: who
	`events.approver_drift` finds out of line, in two groups.

	`will_change` -- a stored approver or a pending request names somebody
	other than the Reports to manager; applying fixes it (and clears the
	approver when there is no usable manager, which the row's `problem`
	says). `needs_attention` -- already in line, but with no usable manager,
	so only a change to the reporting line helps; not selectable."""
	rate_limit_per_user("get_approver_cleanup")
	scope = _assert_portal_admin()
	rows = approver_drift(admin_scope_employee_filters(scope) or {})
	return {
		"will_change": [row for row in rows if row["will_change"]],
		"needs_attention": [row for row in rows if not row["will_change"]],
	}


@frappe.whitelist(methods=["POST"])
def apply_approver_cleanup(employees):
	"""Bring each named employee's approvers and pending requests in line
	with Reports to (`events.rederive_approvers`), one at a time.

	Each id is re-checked against the caller's scope -- the same refusal
	whether or not it exists -- and each one commits on its own, so a
	failure never undoes the rest (`approve_clean_items`' R18 shape). The
	writes skip DocPerm: Portal Admin holds none on Employee, and the gate
	plus the scope check above are the permission, as in `set_portal_role`.
	Idempotent, so a stale preview can only ever re-apply the rule."""
	rate_limit_per_user("apply_approver_cleanup")
	scope = _assert_portal_admin()
	if isinstance(employees, str):
		employees = json.loads(employees)
	if not isinstance(employees, list) or not employees:
		frappe.throw(_("Pick at least one person."))
	if len(employees) > _APPROVER_CLEANUP_MAX:
		frappe.throw(_("Fix up to {0} people at once.").format(_APPROVER_CLEANUP_MAX))

	results = []
	for employee in employees:
		try:
			if not isinstance(employee, str) or not employee_in_admin_scope(employee, scope):
				frappe.throw(_("You are not authorised to change this person."), frappe.PermissionError)
			rederive_approvers(employee)
			frappe.db.commit()
			results.append({"employee": employee, "ok": True})
		except Exception as exc:
			frappe.db.rollback()
			# Our own refusals are worded for the screen; anything else is
			# logged, not echoed (it can carry internals or other logins).
			if isinstance(exc, frappe.ValidationError | frappe.PermissionError):
				message = frappe.utils.strip_html(str(exc)).strip()
			else:
				frappe.log_error(title="Approvers cleanup failed", reference_doctype="Employee", reference_name=employee if isinstance(employee, str) else None)
				message = ""
			results.append({"employee": employee, "ok": False, "message": message or _("This one could not be fixed.")})
	return results


@frappe.whitelist()
def get_person(employee):
	"""Everything HR asks about a person, on one screen (P6-R2): leave
	balance by type, this month's attendance, open and recent requests, the
	assigned shift and holiday list, the reporting manager, the joining date
	and employment status.

	Resolved through `resolve_admin_scope` before anything else is read
	(P6-R6): a caller who may not administer `employee` is refused before
	any record is touched, and the refusal is the same `PermissionError`
	whether or not the employee exists (P6-R8) -- it never discloses which.

	Read-only (P6-R3): no field above Employee permlevel 0, no leave
	*reason*, no check-in *coordinates* -- a faster route to what HR already
	reaches in Desk through the roles it holds, never a wider one (P6-R5).
	"""
	rate_limit_per_user("get_person")
	scope = resolve_admin_scope(frappe.session.user)
	if scope["kind"] == "none" or not employee_in_admin_scope(employee, scope):
		frappe.throw(_("You are not authorised to view this person."), frappe.PermissionError)

	missing = object()
	failed = []

	def section(name, fn):
		value = _safe(fn, title=f"HelixHR person view section failed: {name}", default=missing)
		if value is missing:
			failed.append(name)
			return None
		return value

	today = user_today()
	month_start, month_end = str(get_first_day(today)), str(get_last_day(today))

	return {
		"employee": section("employee", lambda: _person_profile(employee)),
		"leave_balances": section("leave_balances", lambda: _leave_balances(employee)),
		"attendance": section(
			"attendance", lambda: _attendance_month_summary(employee, month_start, month_end)
		),
		"requests": section("requests", lambda: _requests_summary(employee)),
		"shift": section("shift", lambda: _request_shift(employee, today)),
		"holiday_list": section(
			"holiday_list", lambda: get_holiday_list_for_employee(employee, raise_exception=False)
		),
		# None for a caller who cannot reach Desk at all (P6-R12) -- the
		# frontend never has to be trusted to hide this on its own, since
		# no other method in this plan hands out this employee's Desk URL.
		"desk_url": get_url_to_form("Employee", employee) if _can_open_desk(frappe.session.user) else None,
		"failed_sections": failed,
	}


# ---------------------------------------------------------------------------
# P8-U7/U8/U9: editing a person's overview, joining details, approvers and
# default shift from the portal -- reversing `get_person`'s read-only
# posture (P6-R3) deliberately, at exactly the width `PERSON_EDITABLE_FIELDS`
# names (KTD3), scoped by the same `resolve_admin_scope` /
# `employee_in_admin_scope` pair `get_person` already uses.
# ---------------------------------------------------------------------------

_PERSON_ALL_EDITABLE_FIELDS = tuple(
	field for group in PERSON_EDITABLE_FIELDS.values() for field in group
)


def _validate_reports_to(employee_id, scope):
	"""`reports_to` is Link-to-Employee -- the chosen manager's own id is
	exactly the value stored, once it is confirmed to exist and to fall
	inside the caller's own admin scope (the same "picker's options are the
	caller's own reach" rule KTD5 applies to project members). The three
	approver fields follow it in `events.employee_validate` (plan
	2026-10-07-001), so they are not editable here."""
	if not employee_id:
		return None
	if not employee_in_admin_scope(employee_id, scope):
		frappe.throw(_("You are not authorised to view this person."), frappe.PermissionError)
	if not frappe.db.exists("Employee", employee_id):
		frappe.throw(_("{0} isn't an employee here.").format(employee_id))
	return employee_id


@frappe.whitelist()
def get_person_form_options(employee):
	"""The option lists the person-view edit cards need, in one call
	(P8-U7): Designation, Department, Branch, Employment Type, Employee
	Grade, Shift Type and Holiday List, plus a company-scoped employee
	list for the reporting-manager picker.

	Scoped exactly like `get_person` -- a caller outside their admin scope,
	or a target outside it, is refused before anything is read. Department
	narrows to the target's own company; the employee list narrows there
	too, so the picker this call feeds never offers someone outside the
	target's own company as a manager.
	"""
	rate_limit_per_user("get_person_form_options")
	scope = resolve_admin_scope(frappe.session.user)
	if scope["kind"] == "none" or not employee_in_admin_scope(employee, scope):
		frappe.throw(_("You are not authorised to view this person."), frappe.PermissionError)

	company = frappe.db.get_value("Employee", employee, "company")
	people_filters = {"status": "Active"}
	if company:
		people_filters["company"] = company
	people = frappe.get_all(
		"Employee",
		filters=people_filters,
		fields=["name", "employee_name"],
		order_by="employee_name asc",
		ignore_permissions=True,
	)

	return {
		"designations": frappe.get_all("Designation", pluck="name", order_by="name asc"),
		"departments": frappe.get_all(
			"Department",
			filters={"company": company} if company else {},
			pluck="name",
			order_by="name asc",
		),
		"branches": frappe.get_all("Branch", pluck="name", order_by="name asc"),
		"employment_types": frappe.get_all("Employment Type", pluck="name", order_by="name asc"),
		"grades": frappe.get_all("Employee Grade", pluck="name", order_by="name asc"),
		"shift_types": frappe.get_all("Shift Type", pluck="name", order_by="name asc"),
		"holiday_lists": frappe.get_all("Holiday List", pluck="name", order_by="name asc"),
		"people": [{"name": row.name, "employee_name": row.employee_name} for row in people],
	}


@frappe.whitelist(methods=["POST"])
def save_person(employee, **fields):
	"""Edit a person's overview, joining details, reporting manager and
	default shift from the portal (P8-R3) -- the write surface `PERSON_EDITABLE_FIELDS`
	names, no wider (KTD3): every field is permlevel 0, and anything else
	in `fields` is silently ignored, the same rule `_apply_allowed_fields`
	already applies to every other config write in this module.

	`doc.save()`, not an `ignore_permissions=True` insert-style write:
	Employee's own `validate()` still runs -- the `reports_to` cycle
	check, the joining/relieving-date rules -- and Frappe's own Employee
	permissions remain a second gate under the scope helper, exactly the
	posture `_assert_config_write`'s callers already take.
	"""
	rate_limit_per_user("save_person")
	scope = resolve_admin_scope(frappe.session.user)
	if scope["kind"] == "none" or not employee_in_admin_scope(employee, scope):
		frappe.throw(_("You are not authorised to view this person."), frappe.PermissionError)

	doc = frappe.get_doc("Employee", employee)

	resolved = dict(fields)
	if "reports_to" in fields:
		resolved["reports_to"] = _validate_reports_to(fields["reports_to"], scope)

	_apply_allowed_fields(doc, resolved, _PERSON_ALL_EDITABLE_FIELDS)
	doc.save()

	return _person_profile(employee)


# ---------------------------------------------------------------------------
# Reading projects, tasks and members (P7-U3 / R1-R4 read half)
#
# The Delivery Manager / HR Manager / System Manager sibling of
# `search_people` and `get_person` just above -- same shape, scoped by
# `resolve_project_scope` (U2) instead of `resolve_admin_scope`, and refused
# entirely for anyone that scope does not grant.
#
# One refusal message covers "does not exist", "not yours" and "outside your
# scope" in `get_project` (KTD9's uniform-refusal ordering, `get_person`'s
# own pattern): project ids are sequential, so a distinct message per case
# would let a caller learn which ids exist and whose they are just by
# reading the wording back.

_PROJECT_NOT_FOUND = "That project isn't here."

_PROJECT_SEARCH_FIELDS = ("name", "project_name", "status", "company")

_PROJECT_FIELDS = (
	"name",
	"project_name",
	"status",
	"expected_start_date",
	"expected_end_date",
	"helixhr_is_billable",
	"priority",
	"project_type",
)


def _project_priority_options():
	"""`Project.priority`'s own Select options, read from the doctype meta
	rather than duplicated here as a literal list (P8-U2) -- a Desk-side
	customisation of the field's options is then the only place this ever
	needs editing, and the create form can never drift from what
	`doc.insert()`'s own validation actually accepts."""
	options = frappe.get_meta("Project").get_field("priority").options or ""
	return [option for option in options.split("\n") if option]

# Task.status has no single "closed" value -- Completed and Cancelled both
# are -- so "open" is everything else, not one literal status string.
_CLOSED_TASK_STATUSES = ("Completed", "Cancelled")


def _project_search_projection(row):
	return {
		"name": row.name,
		"project_name": row.project_name,
		"status": row.status,
		"company": row.company,
	}


@frappe.whitelist()
def search_projects():
	"""Every project this caller administers, per `resolve_project_scope`
	(P7-R1-R4): unscoped for System Manager, the caller's own company for an
	HR Manager, exactly the projects a HelixHR Delivery Manager is a member
	of, refused for anyone else.

	No `query`/paging parameters -- unlike `search_people`'s employee search,
	a project list is small enough per caller (a company, or one person's
	memberships) that a page control would be UI the plan never asked for."""
	rate_limit_per_user("search_projects")
	scope = resolve_project_scope(frappe.session.user)
	if scope["kind"] == "none":
		frappe.throw(_("You are not authorised to view projects here."), frappe.PermissionError)

	filters = project_scope_filters(scope)
	projects = []
	if filters is not None:
		rows = frappe.get_all(
			"Project",
			filters=filters,
			fields=list(_PROJECT_SEARCH_FIELDS),
			order_by="project_name asc",
			ignore_permissions=True,
		)
		projects = [_project_search_projection(row) for row in rows]

	# P8-U2: the create form's own option lists, read in the same call so
	# the page that hosts it needs no second request. `company` mirrors
	# `create_project`'s own derivation exactly -- `None` for the
	# Desk-only, no-Employee-record persona `resolve_project_scope`
	# deliberately admits as "unscoped" (see `create_project`'s docstring),
	# so the create form can say so instead of offering a company it does
	# not have.
	employee_info = get_current_employee_info()
	return {
		"projects": projects,
		"project_types": frappe.get_all("Project Type", pluck="name", order_by="name asc"),
		"priority_options": _project_priority_options(),
		"company": employee_info.get("company") if employee_info else None,
	}


def _project_open_tasks(project):
	return frappe.get_all(
		"Task",
		filters={"project": project, "status": ["not in", _CLOSED_TASK_STATUSES]},
		fields=["name", "subject", "status", "priority", "exp_start_date", "exp_end_date"],
		order_by="exp_start_date asc, name asc",
		ignore_permissions=True,
	)


def _project_members(project):
	"""Every `Project User` row on `project`, resolved to an employee name
	(KTD5) -- never the raw Frappe User login that `Project User.user`
	actually stores. An Employee link is preferred; a member with none is
	still returned, named from the User's own full name, so a missing
	Employee record is a renderable row rather than a broken one."""
	rows = frappe.get_all(
		"Project User", filters={"parent": project}, fields=["user"], order_by="idx asc", ignore_permissions=True
	)
	logins = [row.user for row in rows]
	if not logins:
		return []

	employees = {
		row.user_id: row
		for row in frappe.get_all(
			"Employee",
			filters={"user_id": ["in", logins]},
			fields=["name", "user_id", "employee_name"],
			ignore_permissions=True,
		)
	}
	full_names = {
		row.name: row.full_name
		for row in frappe.get_all(
			"User", filters={"name": ["in", logins]}, fields=["name", "full_name"], ignore_permissions=True
		)
	}

	members = []
	for login in logins:
		employee = employees.get(login)
		employee_name = (employee.employee_name if employee else None) or full_names.get(login) or _(
			"Unknown member"
		)
		members.append(
			{
				"employee": employee.name if employee else None,
				"employee_name": employee_name,
				"initials": _initials(employee_name),
			}
		)
	return _with_photo_urls(members)


@frappe.whitelist()
def get_project(project):
	"""One project, as a named field list (KTD9) -- never the whole
	document. ERPNext's `Project` carries a costing tab (estimated cost,
	total costing/billable/billed/sales amount, gross margin) and links to
	Customer and Sales Order; none of it belongs in this response, and a
	whole-document read would carry all of it regardless of what this
	function goes on to return.

	Resolved through `resolve_project_scope` before anything else is read:
	a caller outside their scope is refused with `_PROJECT_NOT_FOUND`, the
	same message a nonexistent project id gets, so this can never become an
	oracle for which project ids exist.
	"""
	rate_limit_per_user("get_project")
	scope = resolve_project_scope(frappe.session.user)
	if scope["kind"] == "none" or not project_in_scope(project, scope):
		frappe.throw(_(_PROJECT_NOT_FOUND), frappe.PermissionError)

	data = frappe.db.get_value("Project", project, list(_PROJECT_FIELDS), as_dict=True)

	return {
		"name": data.name,
		"project_name": data.project_name,
		"status": data.status,
		"billable": bool(data.helixhr_is_billable),
		"priority": data.priority,
		"project_type": data.project_type,
		"expected_start_date": data.expected_start_date,
		"expected_end_date": data.expected_end_date,
		"tasks": _project_open_tasks(project),
		"members": _project_members(project),
	}


# ---------------------------------------------------------------------------
# P7-U4: creating projects and tasks, and assigning people.
#
# Three POST-only, rate-limited writes, each gated by the same
# `resolve_project_scope` / `project_in_scope` pair that gates the reads
# above -- no separate authorisation logic (the plan's own instruction for
# this unit).
# ---------------------------------------------------------------------------


def _write_project_users(doc, *, insert):
	"""Insert or save a Project whose `users` child table changed, without
	needing the caller's own session to hold Frappe's `share` doc-perm on
	Project, and without leaving behind the standing document-level access
	ERPNext's own auto-share grants.

	ERPNext's own `Project.after_insert` / `validate` auto-shares the
	document with everyone newly added to `users`
	(`control_access_for_project_users`), and that share step -- unlike the
	surrounding `insert`/`save` -- checks the *session user's* `share`
	permission regardless of `ignore_permissions`, so the write runs as
	Administrator rather than widening every member's standing grant just
	to satisfy an internal Frappe side effect.

	Code review found that the resulting `DocShare` row is not merely a
	permission-check formality: Frappe's own `has_permission` falls back to
	"is this document shared with the user?" whenever role-based permission
	says no, *before* consulting any custom `has_permission` hook's answer.
	So the auto-share alone -- independent of any DocPerm this app grants or
	refuses, and independent of `helixhr.project_permissions`'s hooks --
	would let any member read the whole Project document, costing tab
	included, through Frappe's generic REST route (R8, KTD9). This app
	never relies on that share for anything -- every HelixHR method reads
	and writes Project via `ignore_permissions=True`/`frappe.db.get_value`,
	never through Frappe's permission or sharing system -- so the shares
	this call creates are removed immediately after, for every member named
	in `doc.users` at the time of this write. Desk-created projects and
	their own shares (created by a Projects Manager working directly in
	Desk, not through this endpoint) are untouched.

	Attribution is restored immediately after: the technical actor that
	satisfied Frappe's check is not who actually asked for this write.

	The escalation runs through `as_administrator()`, not
	`frappe.set_user()` (P8-U1 / KTD1): `set_user` mutates the caller's live
	session in place -- wiping `session.data` and clobbering `session.sid`
	-- and that gutted payload is what got written back to the session
	cache, signing the caller out on their very next request. See
	`as_administrator`'s own docstring for the full mechanism.
	"""
	caller = frappe.session.user
	members = [row.user for row in doc.users]
	with as_administrator():
		if insert:
			doc.insert(ignore_permissions=True)
		else:
			doc.save(ignore_permissions=True)
		for member in members:
			frappe.share.remove(doc.doctype, doc.name, member)
	frappe.db.set_value(
		doc.doctype, doc.name, {"owner": caller, "modified_by": caller}, update_modified=False
	)


@frappe.whitelist(methods=["POST"])
def create_project(project_name, is_billable=0, priority=None, project_type=None, **kwargs):
	"""Create a project the caller administers (P7-R1, P7-R6).

	`company` is never read from the request -- it comes from the caller's
	own Employee record, the same one `resolve_project_scope`'s "company"
	and "assigned" branches key on, so a company named in the request body
	(accepted here only via `**kwargs`, then ignored, the same pattern
	`get_dashboard` uses to swallow extra caller input) can never steer
	which company the project lands in.

	`priority` and `project_type` (P8-U2) are the two fields Desk asks for
	that the portal's create form did not -- both optional, both left to
	`doc.insert()`'s own validation (`priority` against `Project`'s own
	Select options, `project_type` as a plain Link) rather than re-checked
	here, so the two can never drift from what the doctype itself accepts.

	The creator is added as a `Project User` in the same operation: without
	it, a HelixHR Delivery Manager who just created the project would fall
	straight back out of their own "assigned" scope and lose it the instant
	they made it (the plan's own named risk for this unit).
	"""
	rate_limit_per_user("create_project")
	scope = resolve_project_scope(frappe.session.user)
	if scope["kind"] == "none":
		frappe.throw(_("You are not authorised to create projects here."), frappe.PermissionError)

	project_name = (project_name or "").strip()
	if not project_name:
		frappe.throw(_("Give the project a name."))

	# `resolve_project_scope`'s "unscoped" branch deliberately admits a
	# System Manager or an HR-role holder with no Employee record at all
	# (the Desk-only persona `ensure_hr_manager_user` builds) -- there is no
	# "own record" to take a company from for that caller, and `Project.company`
	# is mandatory, so there is no safe company to guess on their behalf
	# (Frappe's global default company is a site-wide setting, not this
	# caller's own, and silently attaching their project to it would be a
	# guess dressed up as a decision). `get_current_employee` has no defined
	# behaviour for this persona either -- it calls `.get("name")` on
	# whatever `get_current_employee_info` returned, which is `None` (not a
	# dict) here, so it raises an unhandled `AttributeError` rather than the
	# `PermissionError` its own body appears to promise. Refuse clearly
	# instead: this persona already has Desk for project creation.
	employee_info = get_current_employee_info()
	if not employee_info:
		frappe.throw(
			_("Your account has no linked employee record, so a project can't be created from here."),
			frappe.ValidationError,
		)
	company = employee_info.get("company")

	doc = frappe.get_doc(
		{
			"doctype": "Project",
			"project_name": project_name,
			"company": company,
			"helixhr_is_billable": cint(is_billable),
			"priority": priority or None,
			"project_type": project_type or None,
			"users": [{"user": frappe.session.user}],
		}
	)
	_write_project_users(doc, insert=True)

	return {
		"name": doc.name,
		"project_name": doc.project_name,
		"status": doc.status,
		"billable": bool(doc.helixhr_is_billable),
		"priority": doc.priority,
		"project_type": doc.project_type,
		"expected_start_date": doc.expected_start_date,
		"expected_end_date": doc.expected_end_date,
		"tasks": [],
		"members": _project_members(doc.name),
	}


def _task_projection(doc):
	return {
		"name": doc.name,
		"subject": doc.subject,
		"status": doc.status,
		"priority": doc.priority,
		"exp_start_date": doc.exp_start_date,
		"exp_end_date": doc.exp_end_date,
	}


@frappe.whitelist(methods=["POST"])
def save_task(project, task=None, subject=None, status=None):
	"""Add, rename, or close a task on `project` (P7-R2).

	`task` absent means add a new one; present means rename and/or close an
	existing one. Closing sets ERPNext's own `status` (a `Task.status` of
	`Completed` or `Cancelled`, validated by ERPNext's own Select options on
	save) rather than deleting the record, so time already booked against
	the task keeps a task to be read back against.

	Scoped by `resolve_project_scope` / `project_in_scope` exactly like
	`get_project` -- refused, with the same not-found wording, for a caller
	outside their scope or for a `task` that does not actually belong to
	`project`, so a caller cannot reach a task by naming a project they do
	administer alongside a task id from one they do not.
	"""
	rate_limit_per_user("save_task")
	scope = resolve_project_scope(frappe.session.user)
	if scope["kind"] == "none" or not project_in_scope(project, scope):
		frappe.throw(_(_PROJECT_NOT_FOUND), frappe.PermissionError)

	if task:
		current_project = frappe.db.get_value("Task", task, "project")
		if not current_project or current_project != project:
			frappe.throw(_(_PROJECT_NOT_FOUND), frappe.PermissionError)
		doc = frappe.get_doc("Task", task)
		if subject is not None:
			subject = subject.strip()
			if not subject:
				frappe.throw(_("Give the task a subject."))
			doc.subject = subject
	else:
		subject = (subject or "").strip()
		if not subject:
			frappe.throw(_("Give the task a subject."))
		# ERPNext's `Task.status` carries no doctype-level default -- Desk's
		# new-task form fills "Open" client-side, which this write path has
		# no client side to borrow, so it is named explicitly here.
		doc = frappe.get_doc(
			{"doctype": "Task", "project": project, "subject": subject, "status": "Open"}
		)

	if status is not None:
		doc.status = status

	if task:
		doc.save(ignore_permissions=True)
	else:
		doc.insert(ignore_permissions=True)

	return _task_projection(doc)


@frappe.whitelist(methods=["POST"])
def set_project_members(project, employees):
	"""Replace `project`'s full `Project User` membership with `employees`
	(P7-R3, KTD5).

	The portal's surface is people, not logins, so each Employee is resolved
	to its linked `user_id` -- the field ERPNext's own `Project User` child
	table keys on. If *any* employee named has no linked user the whole call
	is refused, naming which one: that is the caller's own directory data
	(they administer this project), not a disclosure.

	Replaces the set rather than patching it, so calling this twice with the
	same employees is a no-op, and calling it with a shorter list removes
	whoever is missing -- without touching any time they already recorded,
	since `Project User` carries no reference to `Timesheet Detail`.
	"""
	rate_limit_per_user("set_project_members")
	scope = resolve_project_scope(frappe.session.user)
	if scope["kind"] == "none" or not project_in_scope(project, scope):
		frappe.throw(_(_PROJECT_NOT_FOUND), frappe.PermissionError)

	if isinstance(employees, str):
		employees = frappe.parse_json(employees)
	# De-duplicated, order preserved: a caller sending the same employee
	# twice should not raise "duplicate row" from ERPNext's own child-table
	# validation.
	employees = list(dict.fromkeys(employees or []))

	rows = (
		frappe.get_all(
			"Employee",
			filters={"name": ["in", employees]},
			fields=["name", "employee_name", "user_id"],
		)
		if employees
		else []
	)
	by_name = {row.name: row for row in rows}

	users = []
	for employee in employees:
		row = by_name.get(employee)
		if not row:
			frappe.throw(_("{0} isn't an employee here.").format(employee))
		if not row.user_id:
			frappe.throw(
				_("{0} has no linked user, so they can't be assigned to a project.").format(
					row.employee_name or employee
				)
			)
		users.append(row.user_id)

	doc = frappe.get_doc("Project", project)
	doc.set("users", [{"user": user} for user in users])
	_write_project_users(doc, insert=False)

	return {"members": _project_members(project)}


# ---------------------------------------------------------------------------
# Team leave calendar (P3-U7 / P3-R20, P3-R21, P3-R23)
#
# One week, one row per active direct report, and nothing else. Three rules
# hold this section together:
#
#   * The report set is derived on the server from `Employee.reports_to`
#     plus `status == "Active"` -- the same filter `_count_direct_reports`
#     gates the nav item on (P3-KTD11) -- and the caller cannot steer it.
#     There is no `employee` parameter, so there is no team but your own.
#   * Leave rows are read as a *projection* with an explicit field list
#     (P3-KTD1, P3-R23): role Employee has no read on another person's
#     Leave Application at all, and the nested-set User Permission that
#     would grant a manager one is not something this endpoint depends on.
#   * `description` is never selected and never returned (P3-R21). A leave
#     reason is between the employee and their approver; "who is out on
#     Thursday" is a scheduling fact and is all this page is for.
#
# Holiday shading is resolved **per report**, not once for the manager. The
# plan named the manager's own holiday dates, but HRMS resolves a holiday
# list per employee (Holiday List Assignment, per date -- see the Holidays
# section above), so on a company with more than one list the manager's
# calendar is the wrong calendar for half the team. Each report is already
# being visited, and the Holiday rows are cached per resolved list, so the
# cost is one query per *distinct* list rather than one per person. The
# manager's own dates still shade the column headers, because a column is
# one date across everybody and has to be labelled from somebody's list.
# ---------------------------------------------------------------------------

# One screen, one week. A manager with more direct reports than this has an
# org chart problem rather than a paging problem -- `total_reports` stays
# exact so the page can say how many rows are not drawn (P3-R25).
_TEAM_REPORT_LIMIT = 50
# Seven days times the report cap, with room to spare for the long leaves
# that overlap the window from outside it. A bound, not a page.
_TEAM_LEAVE_LIMIT = 500


@frappe.whitelist()
def get_my_team_timesheets(week_start=None):
	"""Every current direct report's week, in any state (plan 2026-10-04-003
	U4, R1-R4).

	Approved weeks are no longer shared with the manager -- the DocShare
	leaves with the decision -- so a permission-scoped read cannot work and
	this projection reads with an explicit field allow-list under
	`ignore_permissions` (KTD9), exactly the trade Team week's leave half
	already makes. Scope stays the caller's own active direct reports, which
	is what the share model ever granted.

	Batched: one query for the reports, one for the weeks, one for the time
	logs (which answers both the per-day hours and the project split), one
	for the holiday lists, one for the holiday rows, one for approved leave,
	one for the open change requests. A 50-report week is seven queries, not
	one per row.
	"""
	rate_limit_per_user("get_my_team_timesheets")
	manager = get_current_employee()
	monday, sunday = get_week_bounds(week_start or user_today())

	total_reports = _count_direct_reports(manager)
	if not total_reports:
		frappe.throw(
			_("Only a manager with people reporting to them has a team week to show."),
			frappe.PermissionError,
		)

	reports = frappe.get_all(
		"Employee",
		filters=_direct_report_filters(manager),
		fields=["name", "employee_name"],
		order_by="employee_name asc",
		limit=_TEAM_REPORT_LIMIT,
		ignore_permissions=True,
	)
	employees = [report.name for report in reports]

	# The same "newest non-cancelled row inside the week" rule
	# `_week_timesheet` owns for one employee, run for the set at once.
	weeks = frappe.get_all(
		"Timesheet",
		filters={
			"employee": ["in", employees],
			"start_date": ["between", [str(monday), str(sunday)]],
			"docstatus": ["!=", 2],
		},
		fields=[
			"name",
			"employee",
			"employee_name",
			"workflow_state",
			"total_hours",
			"helixhr_decision_reason",
			"modified",
		],
		order_by="creation asc",
		limit=_TEAM_REPORT_LIMIT,
		ignore_permissions=True,
	)
	by_employee = {row.employee: row for row in weeks}
	timesheet_names = [row.name for row in by_employee.values()]

	day_hours, project_split = _team_time_logs(timesheet_names)
	expected = _team_expected_hours(employees, monday, sunday)
	changes = _team_open_changes(employees)

	rows = [
		{
			"employee": report.name,
			"employee_name": report.employee_name,
			"initials": _initials(report.employee_name),
			# "Not started" is the honest state for a week with no Timesheet
			# row at all -- a manager chases it the same way they chase a
			# draft (R4).
			"state": by_employee[report.name].workflow_state if report.name in by_employee else None,
			"timesheet": by_employee[report.name].name if report.name in by_employee else None,
			"total_hours": flt(by_employee[report.name].total_hours) if report.name in by_employee else 0.0,
			"expected_hours": expected.get(report.name),
			"hours": day_hours.get(by_employee[report.name].name, {}) if report.name in by_employee else {},
			"projects": project_split.get(by_employee[report.name].name, []) if report.name in by_employee else [],
			"decision_reason": (by_employee[report.name].helixhr_decision_reason or "").strip() or None
			if report.name in by_employee
			else None,
			"open_change": changes.get(report.name),
		}
		for report in reports
	]
	_with_photo_urls(rows)

	return {
		"week_start": str(monday),
		"week_end": str(sunday),
		"reports": rows,
		"total_reports": total_reports,
	}


def _team_time_logs(timesheet_names):
	"""`({timesheet: {date: hours}}, {timesheet: [{project, hours}]})` from
	one query over the page's time logs. Project, task, hours and the day
	they were logged on only -- never rates, costing or billing amounts
	(R3)."""
	if not timesheet_names:
		return {}, {}
	day_hours = {}
	split = {}
	for row in frappe.get_all(
		"Timesheet Detail",
		filters={"parent": ["in", timesheet_names]},
		fields=["parent", "from_time", "project", "task", "hours"],
		order_by="parent asc, idx asc",
	):
		day = str(get_datetime(row.from_time).date()) if row.from_time else None
		if day:
			day_hours.setdefault(row.parent, {})
			day_hours[row.parent][day] = flt(day_hours[row.parent].get(day, 0)) + flt(row.hours)
		if row.project:
			entries = split.setdefault(row.parent, [])
			entry = next((e for e in entries if e["project"] == row.project), None)
			if entry:
				entry["hours"] = flt(entry["hours"]) + flt(row.hours)
			else:
				entries.append({"project": row.project, "hours": flt(row.hours)})
	return day_hours, split


def _team_expected_hours(employees, monday, sunday):
	"""`{employee: hours or None}` -- KTD7's expected hours, batched.

	`standard_working_hours` a day times the employee's working days in the
	week (see `_working_days_by_employee`). No standard hours configured (or
	no holiday list to define a working week) answers None, which the flags
	treat as "no hours flag" and the screen as "not measured".
	"""
	working, standard = _working_days_by_employee(employees, monday, sunday)
	if not standard:
		return {employee: None for employee in employees}
	return {
		employee: flt(standard * len(days)) if days else None
		for employee, days in working.items()
	}


def _team_open_changes(employees):
	"""`{employee: {name, comment}}` -- the one open change request each
	report has, if any (R2's "Change requested" state rides it)."""
	if not employees:
		return {}
	return {
		row.employee: {"name": row.name, "comment": row.comment}
		for row in frappe.get_all(
			"HelixHR Timesheet Change",
			filters={"employee": ["in", list(employees)], "status": "Open"},
			fields=["name", "employee", "comment"],
			ignore_permissions=True,
		)
	}


@frappe.whitelist()
def get_team_member_week(employee, week_start):
	"""One report's week read-only: tasks by day, hours, the decision trail
	and any open change request (plan 2026-10-04-003 U4, R3).

	Authorized here, not by Frappe: an approved week carries no DocShare
	any more, so `frappe.get_doc`'s read (which checks nothing) is exactly
	why the allow-list below is explicit and the scope check above runs
	first. Cost, billing and rate fields never leave the server.
	"""
	rate_limit_per_user("get_team_member_week")
	manager = get_current_employee()
	if not frappe.db.exists("Employee", {"name": employee, **_direct_report_filters(manager)}):
		frappe.throw(_("That person is not on your team."), frappe.PermissionError)

	monday, sunday = get_week_bounds(week_start)
	current = _week_timesheet(
		employee,
		monday,
		("name", "workflow_state", "total_hours", "helixhr_decision_reason"),
		sunday,
	)

	response = {
		"employee": employee,
		"week_start": str(monday),
		"week_end": str(sunday),
		"timesheet": None,
	}
	if not current:
		return response

	doc = frappe.get_doc("Timesheet", current.name)
	day_hours, project_split = _team_time_logs([current.name])
	change = _open_week_change(current.name, fields=("name", "status", "comment", "creation"))

	response["timesheet"] = {
		"name": doc.name,
		"state": doc.workflow_state,
		"total_hours": flt(doc.total_hours),
		"expected_hours": _team_expected_hours([employee], monday, sunday).get(employee),
		"hours": day_hours.get(current.name, {}),
		"projects": project_split.get(current.name, []),
		"decision_reason": (doc.helixhr_decision_reason or "").strip() or None,
		"rows": [
			{
				"project": row.project,
				"task": row.task,
				"hours": flt(row.hours),
				"note": row.description,
				"date": _row_date(row),
			}
			for row in doc.time_logs
		],
		# The decision trail: the workflow's own state comments plus any
		# reason the approver wrote, named by full name so no User record is
		# ever needed to read it.
		"trail": [
			{
				"on": str(comment.creation),
				"kind": comment.comment_type,
				"by": frappe.utils.get_fullname(comment.owner),
				"text": comment.content,
			}
			for comment in frappe.get_all(
				"Comment",
				filters={
					"reference_doctype": "Timesheet",
					"reference_name": current.name,
					"comment_type": ["in", ["Workflow", "Comment"]],
				},
				fields=["creation", "comment_type", "content", "owner"],
				order_by="creation asc",
			)
		],
		"open_change": {"name": change.name, "comment": change.comment} if change else None,
	}
	return response


@frappe.whitelist()
def get_my_team_week(week_start=None):
	"""The week's approved and waiting leave for the logged-in manager's
	active direct reports (P3-R20, P3-R21, P3-R23).

	Refused with a permission error when the caller has nobody reporting to
	them: the page is gated on `has_reports` in the bootstrap (P3-KTD11) and
	the server holds the same line, so a leave approver who manages nobody
	gets a refusal rather than an empty grid that looks like a broken page.

	The week is Monday..Sunday through `helixhr.utils.get_week_bounds`, the
	same normalisation Timesheet uses, so "this week" means one thing across
	the portal whatever the site's week-start setting says.
	"""
	# P3-R25: bounded like the directory, and for the same reason -- this is
	# the one portal read that fans out across other people's rows, so a
	# script walking weeks is worth a ceiling even though any single
	# week's payload is small.
	rate_limit_per_user("get_my_team_week")
	manager = get_current_employee()
	today = user_today()
	monday, sunday = get_week_bounds(week_start or today)

	total_reports = _count_direct_reports(manager)
	if not total_reports:
		frappe.throw(
			_("Only a manager with people reporting to them has a team week to show."),
			frappe.PermissionError,
		)

	reports = frappe.get_all(
		"Employee",
		filters=_direct_report_filters(manager),
		fields=["name", "employee_name"],
		order_by="employee_name asc",
		limit=_TEAM_REPORT_LIMIT,
		ignore_permissions=True,
	)

	holiday_cache = {}
	column_holidays = _team_holiday_dates(manager, monday, sunday, holiday_cache)
	days = [
		{
			"date": str(date),
			"weekday": date.strftime("%A"),
			"is_weekend": date.weekday() >= 5,
			"is_holiday": str(date) in column_holidays,
		}
		for date in (add_days(monday, offset) for offset in range(7))
	]

	by_employee, waiting_count = _team_leaves([report.name for report in reports], monday, sunday)

	rows = [
		{
			"employee": report.name,
			"employee_name": report.employee_name,
			"initials": _initials(report.employee_name),
			"leaves": by_employee.get(report.name, []),
			# This person's own non-working days, which are not necessarily
			# the column's (see the section note above).
			"holidays": sorted(_team_holiday_dates(report.name, monday, sunday, holiday_cache)),
		}
		for report in reports
	]
	_with_photo_urls(rows)

	# "Who is out today" is about today, and the rows in hand only cover the
	# week on screen -- so paging to another week says so rather than
	# claiming an empty office (the page reads `is_current_week`).
	is_current_week = str(monday) <= today <= str(sunday)
	out_today = (
		[
			{
				"employee": row["employee"],
				"employee_name": row["employee_name"],
				"initials": row["initials"],
				"leave_type": leave["leave_type"],
				"half_day": leave["half_day"] and leave["half_day_date"] == today,
				"waiting": leave["waiting"],
			}
			for row in rows
			for leave in row["leaves"]
			if leave["from_date"] <= today <= leave["to_date"]
		]
		if is_current_week
		else []
	)

	return {
		"week_start": str(monday),
		"week_end": str(sunday),
		"today": today,
		"is_current_week": is_current_week,
		"days": days,
		"reports": rows,
		"out_today": out_today,
		"total_reports": total_reports,
		"waiting_count": waiting_count,
	}


def _team_leaves(employees, monday, sunday):
	"""`({employee: [leave, ...]}, waiting_count)` for `employees` over
	the week -- the Team week's leave projection, shared with the Roster so
	the two screens can never disagree about who is out (plan 2026-09-30-001
	U7). Never `description` (P3-R21)."""
	by_employee = {}
	waiting_count = 0
	if not employees:
		return by_employee, waiting_count
	# Explicitly *not* `description`, and explicitly not `*` (P3-R21).
	# `docstatus` and `status` are read to decide `waiting` and are
	# translated into that one flag rather than passed through -- the
	# screen has no use for either word (design system copy rules).
	for row in frappe.get_all(
		"Leave Application",
		filters={
			"employee": ["in", list(employees)],
			"docstatus": ["<", 2],
			"status": ["in", ["Open", "Approved"]],
			"from_date": ["<=", str(sunday)],
			"to_date": [">=", str(monday)],
		},
		fields=[
			"name",
			"employee",
			"leave_type",
			"from_date",
			"to_date",
			"half_day",
			"half_day_date",
			"status",
			"docstatus",
		],
		order_by="from_date asc, name asc",
		limit=_TEAM_LEAVE_LIMIT,
		ignore_permissions=True,
	):
		waiting = not (cint(row.docstatus) == 1 and row.status == "Approved")
		waiting_count += 1 if waiting else 0
		by_employee.setdefault(row.employee, []).append(
			{
				"name": row.name,
				"leave_type": row.leave_type,
				# The true range, because the phone list says it in
				# words and a bar that lies about its dates on the week
				# it starts in is worse than no bar.
				"from_date": str(getdate(row.from_date)),
				"to_date": str(getdate(row.to_date)),
				# ...and the range clipped to this week, which is the
				# bar's geometry. Clipping on the server keeps the rule
				# in one place and makes "a two-week leave shows in both
				# weeks" assertable without a browser.
				"start": str(max(getdate(row.from_date), monday)),
				"end": str(min(getdate(row.to_date), sunday)),
				"half_day": bool(row.half_day),
				"half_day_date": str(getdate(row.half_day_date)) if row.half_day_date else None,
				"waiting": waiting,
			}
		)
	return by_employee, waiting_count


def _team_holiday_dates(employee, start, end, cache):
	"""The holiday dates HRMS resolves for `employee` across `start`..`end`,
	as a set of `YYYY-MM-DD` strings.

	The one resolver every screen shares (`_holiday_kinds`, P3-U3, P3-U9) with
	the weekly offs dropped, as on the Holidays page: Saturday and Sunday are
	already dimmed as weekends, and a list that carries one Holiday row per
	weekend day would otherwise report every weekend as a named holiday. `cache`
	is the per-span one, so a team on one holiday list costs one Holiday query
	however many people are in it.
	"""
	_, kinds = _holiday_kinds(employee, start, end, cache)
	return {date for date, kind in (kinds or {}).items() if kind != "weekly_off"}


# ---------------------------------------------------------------------------
# Shift roster (plan 2026-09-30-001 U7, U8 / R7-R11, KTD7-KTD9)
#
# A week of who works which shift, read from HRMS Shift Assignments. HelixHR's
# own methods over HRMS doctypes, never `hrms.api.roster` (KTD7): its dotted
# paths are HRMS-internal and its writes assume Desk DocPerms.
#
#   * The row set is the server's, per mode (KTD8): `mine` is the caller's
#     own Employee, `team` is the caller plus their Active direct reports
#     (Team's rule -- not the nested tree), `hr` is every Active employee in
#     `resolve_admin_scope`. A mode the caller does not hold is refused with
#     a PermissionError, never silently downgraded, so the page's `forbidden`
#     state is the one answer to "not yours to see".
#   * Cells are read by docstatus 1 plus date overlap, *not* by `status`:
#     HRMS's nightly `mark_expired_shift_assignments_as_inactive` flips an
#     ended assignment to Inactive, and a past week must still show who
#     worked it. Cancelled (docstatus 2) never shows. Where an Active and an
#     Inactive assignment both cover a day (HRMS skips its overlap check for
#     Inactive rows), the Active one wins.
#   * Writes are HR-only, inside admin scope, through `insert`/`submit`/
#     `save`/`cancel` so HRMS's own validation runs, and each one runs in a
#     savepoint so a refused step leaves nothing half-written (KTD9).
# ---------------------------------------------------------------------------

# One screen, one week, the Team page's cap. `total` stays exact, and `hr`
# mode pages through the rest with `start` and narrows with `search`.
_ROSTER_ROW_LIMIT = 50
# The row cap times a handful of assignments a week each. A bound, not a page.
# Worst case, not a typical week: the week is read by date overlap, so each
# row can show at most seven distinct assignments (one a day) plus the
# overlapping Inactive ones HRMS leaves behind -- 14 a row covers both.
_ROSTER_ASSIGNMENT_LIMIT = _ROSTER_ROW_LIMIT * 14
# HRMS v16 Shift Type has no enabled/disabled field, so every type counts;
# `shift_types_truncated` tells the sheet when HR has more than this.
_ROSTER_SHIFT_TYPE_LIMIT = 200
_ROSTER_SEARCH_MAX = 140
_ROSTER_MODES = ("mine", "team", "hr")

_ROSTER_REFUSED = "You don't have permission to do that."
_ROSTER_NO_TEAM = "Only a manager with people reporting to them has a team roster to show."
_ROSTER_NOT_HR = "Only HR can see everyone's shifts."
_ROSTER_NOT_FOUND = "That shift assignment could not be found."
_ROSTER_OVERLAP = "This person already has a shift on some of those dates. End or change that one first."
_ROSTER_CANCEL_BLOCKED = (
	"This shift can't be cancelled because check-ins or attendance are already recorded against it. "
	"End it on a date instead."
)
_ROSTER_END_BEFORE_START = "The end date can't be before the shift starts."
_ROSTER_CHANGE_ON_START = "That is the day this shift starts. Cancel it and assign the new shift instead."
_ROSTER_CHANGE_AFTER_END = "That date is after this shift ends."
_ROSTER_INACTIVE_EMPLOYEE = "Shifts can only be assigned to someone who is currently employed."
_ROSTER_NO_SHIFT_TYPE = "Choose a shift."
_ROSTER_BAD_DATE = "Give a valid date."
_ROSTER_SAVE_FAILED = "The shift couldn't be saved."


def _hh_mm(value):
	"""`"09:00"` from a Shift Type time (a timedelta out of MariaDB)."""
	if value is None:
		return None
	seconds = int(value.total_seconds())
	return f"{seconds // 3600 % 24:02d}:{seconds // 60 % 60:02d}"


def _roster_date(value):
	"""A date from the browser, or None when blank. Not `_as_date`: that
	turns a blank into today and lets `getdate`'s HTML message through."""
	if not value:
		return None
	try:
		return getdate(value)
	except frappe.ValidationError:
		frappe.clear_last_message()  # getdate's "<b>x</b> is not a valid date string."
	except (TypeError, ValueError, OverflowError):
		pass
	frappe.throw(_(_ROSTER_BAD_DATE))


def _roster_employee_scope(mode):
	"""`(filters, or_filters)` for the Employee rows `mode` may see, or a
	PermissionError when the caller does not hold that mode."""
	if mode == "hr":
		filters = admin_scope_employee_filters(resolve_admin_scope(frappe.session.user))
		if filters is None:
			frappe.throw(_(_ROSTER_NOT_HR), frappe.PermissionError)
		return {**filters, "status": "Active"}
	# The portal's standard not-linked refusal, like every session-employee
	# read. Desk-only HR has no Employee and uses `hr` mode, handled above.
	me = _my_employee()
	if mode == "mine":
		return {"name": me}
	reports = frappe.get_all(
		"Employee", filters=_direct_report_filters(me), pluck="name", ignore_permissions=True
	)
	if not reports:
		frappe.throw(_(_ROSTER_NO_TEAM), frappe.PermissionError)
	return {"name": ["in", [me, *reports]]}


def _roster_cells(rows, monday, sunday, can_edit):
	"""`{employee: [cell x 7]}` from one Shift Assignment read and one Shift
	Type read for the whole page, whatever the row count."""
	ids = [row.name for row in rows]
	if not ids:
		return {}, {}
	assignments = frappe.get_all(
		"Shift Assignment",
		filters={"employee": ["in", ids], "docstatus": 1, "start_date": ["<=", str(sunday)]},
		or_filters=[["end_date", "is", "not set"], ["end_date", ">=", str(monday)]],
		fields=["name", "employee", "shift_type", "start_date", "end_date", "status"],
		order_by="start_date asc, name asc",
		limit=_ROSTER_ASSIGNMENT_LIMIT,
		ignore_permissions=True,
	)
	shift_names = {row.shift_type for row in assignments}
	shift_names |= {row.default_shift for row in rows if row.default_shift}
	times = {}
	if shift_names:
		times = {
			shift.name: shift
			for shift in frappe.get_all(
				"Shift Type",
				filters={"name": ["in", list(shift_names)]},
				fields=["name", "start_time", "end_time"],
				ignore_permissions=True,
			)
		}

	by_employee = {}
	for row in assignments:
		by_employee.setdefault(row.employee, []).append(row)

	cells = {}
	for employee in ids:
		employee_assignments = by_employee.get(employee, [])
		week = []
		for offset in range(7):
			day = add_days(monday, offset)
			covering = [
				a
				for a in employee_assignments
				if getdate(a.start_date) <= day and (not a.end_date or getdate(a.end_date) >= day)
			]
			covering.sort(key=lambda a: a.status != "Active")
			found = covering[0] if covering else None
			shift = times.get(found.shift_type) if found else None
			cell = {
				"date": str(day),
				"shift_type": found.shift_type if found else None,
				"start_time": _hh_mm(shift.start_time) if shift else None,
				"end_time": _hh_mm(shift.end_time) if shift else None,
			}
			if can_edit:
				# Only HR acts on a cell, so only HR's payload names the record.
				cell["assignment"] = found.name if found else None
				cell["assignment_start"] = str(getdate(found.start_date)) if found else None
				cell["assignment_end"] = str(getdate(found.end_date)) if found and found.end_date else None
			week.append(cell)
		cells[employee] = week
	return cells, times


@frappe.whitelist()
def get_roster_week(week_start=None, mode="mine", search=None, start=0):
	"""One Monday-first week of shift cells for the caller's scope (U7,
	R7, R8). `mode` is `mine` (default), `team` or `hr`; the browser picks
	it and the server refuses one the caller does not hold. `search`
	(employee name or id) and `start` page the rows, capped at 50 with an
	exact `total`."""
	rate_limit_per_user("get_roster_week")
	mode = (mode or "mine").strip().lower()
	if mode not in _ROSTER_MODES:
		frappe.throw(_("Choose whose shifts to show."))
	today = user_today()
	monday, sunday = get_week_bounds(week_start or today)
	start = max(cint(start), 0)

	filters = _roster_employee_scope(mode)
	needle = (search or "").strip()[:_ROSTER_SEARCH_MAX]
	or_filters = (
		[["name", "like", f"%{needle}%"], ["employee_name", "like", f"%{needle}%"]] if needle else None
	)
	scope = {"filters": filters, "or_filters": or_filters, "ignore_permissions": True}
	employees = frappe.get_all(
		"Employee",
		fields=["name", "employee_name", "default_shift"],
		order_by="employee_name asc, name asc",
		offset=start,
		limit=_ROSTER_ROW_LIMIT,
		**scope,
	)
	total = _aggregate_count(frappe.get_all("Employee", fields=[{"COUNT": "*"}], **scope)[0])

	can_edit = mode == "hr" and bool(frappe.has_permission("Shift Assignment", "create"))
	cells, times = _roster_cells(employees, monday, sunday, can_edit)
	leaves = _team_leaves([row.name for row in employees], monday, sunday)[0]
	holiday_cache = {}
	rows = []
	for row in employees:
		default = times.get(row.default_shift) if row.default_shift else None
		rows.append(
			{
				"employee": row.name,
				"employee_name": row.employee_name,
				"initials": _initials(row.employee_name),
				# The hint an empty cell may show: HRMS falls back to it
				# for check-in when no assignment covers the day.
				"default_shift": (
					{
						"shift_type": row.default_shift,
						"start_time": _hh_mm(default.start_time) if default else None,
						"end_time": _hh_mm(default.end_time) if default else None,
					}
					if row.default_shift
					else None
				),
				"cells": cells[row.name],
				"leaves": leaves.get(row.name, []),
				"holidays": sorted(_team_holiday_dates(row.name, monday, sunday, holiday_cache)),
			}
		)
	_with_photo_urls(rows)

	shift_types = []
	if can_edit:
		# One past the cap, so a full list is told apart from a cut one.
		shift_types = [
			{"name": shift.name, "start_time": _hh_mm(shift.start_time), "end_time": _hh_mm(shift.end_time)}
			for shift in frappe.get_all(
				"Shift Type",
				fields=["name", "start_time", "end_time"],
				order_by="name asc",
				limit=_ROSTER_SHIFT_TYPE_LIMIT + 1,
			)
		]
	shift_types_truncated = len(shift_types) > _ROSTER_SHIFT_TYPE_LIMIT
	shift_types = shift_types[:_ROSTER_SHIFT_TYPE_LIMIT]

	return {
		"week_start": str(monday),
		"week_end": str(sunday),
		"today": today,
		"is_current_week": str(monday) <= today <= str(sunday),
		"mode": mode,
		"days": [
			{"date": str(day), "weekday": day.strftime("%A"), "is_weekend": day.weekday() >= 5}
			for day in (add_days(monday, offset) for offset in range(7))
		],
		"rows": rows,
		"total": total,
		"start": start,
		"limit": _ROSTER_ROW_LIMIT,
		"can_edit": can_edit,
		"shift_types": shift_types,
		"shift_types_truncated": shift_types_truncated,
	}


def _assert_roster_employee(employee):
	"""HR-only, inside admin scope, before anything is read (KTD8). Plain
	employees and managers hold scope "none", so they stop here too."""
	scope = resolve_admin_scope(frappe.session.user)
	if not employee or not employee_in_admin_scope(employee, scope):
		frappe.throw(_(_ROSTER_REFUSED), frappe.PermissionError)


def _roster_assignment(name):
	"""The submitted Shift Assignment `name`, once the caller is known to
	administer its employee. A draft or a cancelled one is "not found": the
	roster never shows either, so neither is anything to act on."""
	if resolve_admin_scope(frappe.session.user)["kind"] == "none":
		frappe.throw(_(_ROSTER_REFUSED), frappe.PermissionError)
	row = (
		frappe.db.get_value("Shift Assignment", name, ["employee", "docstatus"], as_dict=True)
		if name
		else None
	)
	if not row or cint(row.docstatus) != 1:
		frappe.throw(_(_ROSTER_NOT_FOUND), frappe.DoesNotExistError)
	_assert_roster_employee(row.employee)
	return frappe.get_doc("Shift Assignment", name)


def _roster_assignment_projection(doc):
	return {
		"name": doc.name,
		"employee": doc.employee,
		"shift_type": doc.shift_type,
		"start_date": str(getdate(doc.start_date)),
		"end_date": str(getdate(doc.end_date)) if doc.end_date else None,
		"status": doc.status,
	}


def _new_roster_assignment(employee, shift_type, start_date, end_date):
	"""Insert and submit one assignment through HRMS's controller. Company
	comes from the Employee, never from the caller (U8)."""
	if not shift_type or not frappe.db.exists("Shift Type", shift_type):
		frappe.throw(_(_ROSTER_NO_SHIFT_TYPE))
	person = frappe.db.get_value("Employee", employee, ["status", "company"], as_dict=True)
	if person.status != "Active":
		frappe.throw(_(_ROSTER_INACTIVE_EMPLOYEE))
	if end_date and end_date < start_date:
		frappe.throw(_(_ROSTER_END_BEFORE_START))
	doc = frappe.new_doc("Shift Assignment")
	doc.update(
		{
			"employee": employee,
			"shift_type": shift_type,
			"company": person.company,
			"start_date": start_date,
			"end_date": end_date,
			"status": "Active",
		}
	)
	_assert_config_write(doc)
	doc.insert()
	doc.submit()
	return doc


def _run_roster_write(write, refused=None):
	"""Run `write` inside a savepoint. A refusal rolls back to it -- so a
	change whose new half fails leaves the old `end_date` as it was
	(KTD9) -- and comes back as one plain sentence: HRMS's own messages
	carry HTML links and record names, and its msgprint is cleared so the
	raw version never reaches the client beside ours. `refused` replaces
	whatever HRMS said (the cancel path, whose reasons all mean the same
	thing to HR)."""
	from frappe.utils.messages import clear_messages
	from hrms.hr.doctype.shift_assignment.shift_assignment import MultipleShiftError, OverlappingShiftError

	savepoint = "helixhr_roster_write"
	frappe.db.savepoint(savepoint)
	try:
		result = write()
	except frappe.PermissionError:
		frappe.db.rollback(save_point=savepoint)
		clear_messages()
		frappe.throw(_(_ROSTER_REFUSED), frappe.PermissionError)
	except frappe.ValidationError as error:
		frappe.db.rollback(save_point=savepoint)
		clear_messages()
		if isinstance(error, OverlappingShiftError | MultipleShiftError):
			message = _(_ROSTER_OVERLAP)
		else:
			message = refused or frappe.utils.strip_html(str(error)).strip() or _(_ROSTER_SAVE_FAILED)
		frappe.throw(message)
	frappe.db.release_savepoint(savepoint)
	return result


@frappe.whitelist(methods=["POST"])
def assign_shift(employee, shift_type, start_date, end_date=None, **kwargs):
	"""HR assigns `shift_type` to `employee` from `start_date`, open-ended
	or to `end_date` (U8, R9). HRMS refuses an overlap; anything else in
	the payload, `company` included, is ignored."""
	rate_limit_per_user("assign_shift")
	_assert_roster_employee(employee)
	start = _roster_date(start_date)
	if not start:
		frappe.throw(_(_ROSTER_BAD_DATE))
	end = _roster_date(end_date)
	doc = _run_roster_write(lambda: _new_roster_assignment(employee, shift_type, start, end))
	return _roster_assignment_projection(doc)


@frappe.whitelist(methods=["POST"])
def end_shift_assignment(assignment, end_date, **kwargs):
	"""HR ends a submitted assignment on `end_date`: `end_date` is
	`allow_on_submit`, so the doc stays submitted (R10)."""
	rate_limit_per_user("end_shift_assignment")
	doc = _roster_assignment(assignment)
	end = _roster_date(end_date)
	if not end:
		frappe.throw(_(_ROSTER_BAD_DATE))
	if end < getdate(doc.start_date):
		frappe.throw(_(_ROSTER_END_BEFORE_START))

	def write():
		_assert_config_write(doc)
		doc.end_date = end
		doc.save()
		return doc

	return _roster_assignment_projection(_run_roster_write(write))


@frappe.whitelist(methods=["POST"])
def change_shift_assignment(assignment, from_date, shift_type, **kwargs):
	"""HR changes the shift from `from_date`: the current assignment ends
	the day before and a new one starts on it, keeping the old end date, in
	one savepoint (KTD9). `shift_type` is not `allow_on_submit`, so this is
	never an edit in place. A change on the assignment's own first day is
	refused -- that is a cancel plus an assign, and saying so is clearer
	than a zero-day assignment HRMS would refuse anyway."""
	rate_limit_per_user("change_shift_assignment")
	doc = _roster_assignment(assignment)
	start = _roster_date(from_date)
	if not start:
		frappe.throw(_(_ROSTER_BAD_DATE))
	if start <= getdate(doc.start_date):
		frappe.throw(_(_ROSTER_CHANGE_ON_START))
	old_end = getdate(doc.end_date) if doc.end_date else None
	if old_end and start > old_end:
		frappe.throw(_(_ROSTER_CHANGE_AFTER_END))

	def write():
		_assert_config_write(doc)
		doc.end_date = add_days(start, -1)
		doc.save()
		return doc, _new_roster_assignment(doc.employee, shift_type, start, old_end)

	ended, assigned = _run_roster_write(write)
	return {
		"ended": _roster_assignment_projection(ended),
		"assigned": _roster_assignment_projection(assigned),
	}


@frappe.whitelist(methods=["POST"])
def cancel_shift_assignment(assignment, **kwargs):
	"""HR cancels an assignment, only where HRMS allows it -- no check-ins
	or attendance against it (R10). Stock HR User holds no cancel on Shift
	Assignment and gets the plain refusal; end-dating covers that need."""
	rate_limit_per_user("cancel_shift_assignment")
	doc = _roster_assignment(assignment)
	if not doc.has_permission("cancel"):
		frappe.throw(_(_ROSTER_REFUSED), frappe.PermissionError)
	_run_roster_write(doc.cancel, refused=_(_ROSTER_CANCEL_BLOCKED))
	return {"name": doc.name, "cancelled": True}


# Organisation view (P5-U15 / P5-R20, P5-R23)
#
# Management -- HR Manager and System Manager, the roles that already have
# company-wide read -- sees the state of their own company here and can act
# on nothing: this method has no write counterpart at all, not a smaller
# permission on a shared one.
#
# "Absence is aggregate, never per person" (P5-U15's own words). Every other
# screen that shows who is out (`get_my_team_week`) is scoped to a manager's
# direct reports and withholds the leave reason; an org-wide read of *names*
# would be a wider disclosure than any screen in the app makes today, so this
# one goes one step further and withholds the person too. What comes back is
# a count, and each queue's backlog age in days -- a fact about the queue,
# never about whoever is at the front of it -- plus the same celebrations
# projection the home page already makes public (`_get_celebrations`).
_ORGANISATION_QUEUES = (
	{
		"key": "leave",
		"doctype": "Leave Application",
		"state_field": "status",
		"open_states": ("Open",),
		"docstatus_zero": True,
	},
	{
		"key": "timesheet",
		"doctype": "Timesheet",
		"state_field": "workflow_state",
		"open_states": (PENDING_STATE, TIMESHEET_PENDING_HR),
		"docstatus_zero": False,
	},
	{
		"key": "attendance",
		"doctype": "Attendance Request",
		"state_field": "workflow_state",
		"open_states": (REQUEST_PENDING_MANAGER, REQUEST_PENDING_HR),
		"docstatus_zero": True,
	},
	{
		"key": "request",
		"doctype": "HR Request",
		"state_field": "status",
		"open_states": (HR_REQUEST_OPEN, HR_REQUEST_IN_PROGRESS, HR_REQUEST_WAITING_ON_EMPLOYEE),
		"docstatus_zero": False,
	},
)


def _queue_aggregate(doctype, state_field, open_states, docstatus_zero, company, today):
	"""One kind's backlog, as two numbers: how many rows are open in this
	company, and how many days old the oldest one is.

	One aggregate query -- `count(*)` and `min(creation)` together -- rather
	than a fetch-then-measure, so this stays flat as the organisation grows
	instead of costing one round trip per pending row (P5-R23). `state_field`
	and `doctype` are drawn only from `_ORGANISATION_QUEUES` above, never from
	a caller, so building the query with an f-string carries no injection
	surface; every value that *is* a caller-adjacent input (`company`,
	`open_states`) is bound as a SQL parameter.
	"""
	if not company:
		return {"pending": 0, "oldest_pending_days": None}
	table = f"`tab{doctype}`"
	state_placeholders = ", ".join(["%s"] * len(open_states))
	docstatus_clause = "and t.docstatus = 0" if docstatus_zero else ""
	row = frappe.db.sql(
		f"""
		select count(*) as pending, min(t.creation) as oldest
		from {table} t
		inner join `tabEmployee` e on e.name = t.employee
		where e.company = %s
		and t.{state_field} in ({state_placeholders})
		{docstatus_clause}
		""",
		(company, *open_states),
		as_dict=True,
	)[0]
	oldest_days = date_diff(today, getdate(row.oldest)) if row.oldest else None
	return {"pending": cint(row.pending), "oldest_pending_days": oldest_days}


def _on_leave_today(company, today):
	"""How many people in `company` are on approved leave today -- a count,
	never a name (see the module note above)."""
	if not company:
		return 0
	return cint(
		frappe.db.sql(
			"""
			select count(*)
			from `tabLeave Application` t
			inner join `tabEmployee` e on e.name = t.employee
			where e.company = %s and t.docstatus = 1 and t.status = 'Approved'
			and t.from_date <= %s and t.to_date >= %s
			""",
			(company, today, today),
		)[0][0]
	)


@frappe.whitelist()
def get_organisation_view():
	"""A read-only snapshot of the caller's own company (P5-R20): headcount,
	how many people are out today, each queue's backlog and its age, and
	this month's celebrations.

	Gated on `_is_hr()` -- the same predicate `can_configure` and
	`get_portal_config` already use (P5-U14) -- because this is company-wide
	read, not a manager's own team. No new role and no new permission delta:
	P5-U15's whole point is that HR Manager and System Manager already have
	the read this method projects.
	"""
	rate_limit_per_user("get_organisation_view")
	if not _is_hr():
		frappe.throw(_("Only HR may see the organisation view."), frappe.PermissionError)

	company = _caller_company()
	employee = (get_current_employee_info() or {}).get("name")
	today = user_today()

	return {
		"company": company,
		"headcount": _headcount(company),
		"on_leave_today": _on_leave_today(company, today),
		"queues": {
			queue["key"]: _queue_aggregate(
				queue["doctype"],
				queue["state_field"],
				queue["open_states"],
				queue["docstatus_zero"],
				company,
				today,
			)
			for queue in _ORGANISATION_QUEUES
		},
		"celebrations": (
			_get_celebrations(employee, getdate(today)) if employee else {"birthdays": [], "anniversaries": []}
		),
	}


def _headcount(company):
	if not company:
		return 0
	return frappe.db.count("Employee", {"status": "Active", "company": company})
