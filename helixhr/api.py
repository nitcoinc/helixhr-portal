import hashlib
import json
import math
import os
import re
from urllib.parse import quote

import frappe
from frappe import _
from frappe.permissions import AUTOMATIC_ROLES, add_user_permission
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

from helixhr import reminders
from helixhr.events import (
	APPROVER_FIELDS,
	DECISION_REASON_FIELD,
	HR_REPLY_SUBJECT_PREFIX,
	HR_REQUEST_DECLINED_PREFIX,
	HR_REQUEST_DONE,
	HR_REQUEST_IN_PROGRESS,
	HR_REQUEST_OPEN,
	HR_REQUEST_QUESTION_PREFIX,
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
	_is_hr,
	approver_drift,
	approver_problem,
	backdated_grace_days,
	backdated_leave_earliest,
	backdated_leave_reason,
	escape_request_comment,
	leave_overdraw,
	rederive_approvers,
	strip_request_comment_marker,
)
from helixhr.helixhr.doctype.helixhr_leaving.helixhr_leaving import OPEN_STATUSES as OPEN_LEAVING_STATUSES
from helixhr.helixhr.doctype.helixhr_report_export.helixhr_report_export import expire_unreachable_exports
from helixhr.helixhr.doctype.helixhr_roster_change.helixhr_roster_change import (
	ROSTER_APPROVED,
	ROSTER_DECLINED,
	ROSTER_DRAFT,
	ROSTER_PENDING,
	ROSTER_RETURNED,
	ROSTER_WITHDRAWN,
	pending_requests,
)
from helixhr.helixhr.doctype.helixhr_timesheet_change.helixhr_timesheet_change import (
	comment_problem,
)

# The routed roles that are *not* already unscoped HR access (P5-U5's own
# permission scope), reused here rather than re-listed so this gate and
# `hr_request.get_permission_query_conditions` cannot drift apart.
from helixhr.helixhr.doctype.hr_request.hr_request import _WORKER_ROLES as _ROUTED_WORKER_ROLES
from helixhr.roster_ops import AFTER_END as _ROSTER_CHANGE_AFTER_END
from helixhr.roster_ops import ASSIGN as ROSTER_OP_ASSIGN
from helixhr.roster_ops import BAD_DATE as _ROSTER_BAD_DATE
from helixhr.roster_ops import CHANGE as ROSTER_OP_CHANGE
from helixhr.roster_ops import CHANGE_ON_START as _ROSTER_CHANGE_ON_START
from helixhr.roster_ops import END as ROSTER_OP_END
from helixhr.roster_ops import END_BEFORE_START as _ROSTER_END_BEFORE_START
from helixhr.roster_ops import NO_SHIFT_ON_DATE as _ROSTER_NO_SHIFT_ON_DATE
from helixhr.roster_ops import NO_SHIFT_TYPE as _ROSTER_NO_SHIFT_TYPE
from helixhr.roster_ops import OVERLAP as _ROSTER_OVERLAP
from helixhr.roster_ops import affected_window, op_date, read_coverage, simulate, snapshot_key
from helixhr.tasks import (
	LEAVING_DOCTYPE,
	apply_leaving,
	leaving_manager_problem,
	save_employee_plainly,
)
from helixhr.utils import (
	ACCESS_ADMIN_ROLE,
	ALL_ORGS_ONLY_ROLES,
	ALL_ORGS_SCOPE_KEY,
	ASSIGNABLE_ROLES,
	EMAIL_THEME,
	EXPORT_ROLES,
	HOLIDAY_LIST_EDITABLE_FIELDS,
	HR_ROLES,
	HR_USER_ROLE,
	LEAVE_TYPE_EDITABLE_FIELDS,
	LOGIN_CREATED_MARK,
	NOTIFICATION_EVENTS,
	ORG_VIEWER_ROLE,
	PERSON_EDITABLE_FIELDS,
	PROFILE_CORRECTABLE_FIELDS,
	PROFILE_CORRECTION_CATEGORY,
	PROFILE_EDITABLE_FIELDS,
	PROFILE_LABELS,
	PROFILE_MASKED_FIELDS,
	PROFILE_SECTION_FIELDS,
	PROFILE_SECTION_TABLES,
	PROFILE_USER_LINK_FIELDS,
	REPORT_MANAGER_ROLE,
	ROLE_LABELS,
	SELF_EDIT_REFUSAL,
	SHIFT_TYPE_EDITABLE_FIELDS,
	THEME_PLACEHOLDERS,
	UPLOAD_MAX_BYTES,
	WELCOME_SENT_MARK,
	TemplateRejected,
	_company_role_holders,
	_employee_note,
	access_summary,
	active_employee_for_user,
	active_employees_for_users,
	admin_scope_employee_filters,
	as_administrator,
	assignable,
	can_admin_portal,
	can_manage_access,
	clear_role_scope_cache,
	company_role_holders,
	company_today,
	email_theme,
	employee_in_admin_scope,
	event_variables,
	get_manager_user,
	get_week_bounds,
	has_all_orgs_authority,
	has_bank_number,
	has_custom_wording,
	held_welcome_employees,
	is_photo_content,
	mask_identifier,
	may_assign,
	may_edit_target,
	message_brand,
	microsoft_sign_in_enabled,
	photo_file_filters,
	portal_home_page,
	portal_role_scope,
	prepare_profile_photo,
	project_in_scope,
	project_scope_filters,
	rate_limit_per_user,
	read_companies,
	render_message,
	request_memo,
	request_memo_get,
	resolve_admin_scope,
	resolve_portal_admin_scope,
	resolve_project_read_scope,
	resolve_project_scope,
	resolve_read_scope,
	role_scope,
	sample_context,
	scope_covers,
	send_notification,
	session_company,
	shared_admin_scope,
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
	employee = active_employee_for_user(frappe.session.user).name
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
	"""R3: the owner; HR within admin scope, or a company access holder
	within their read scope (any status); an Active
	colleague in the caller's own company (the Directory rule)."""
	target = frappe.db.get_value("Employee", employee, ["company", "status", "user_id"], as_dict=True)
	if not target:
		return False
	user = frappe.session.user
	if target.user_id and target.user_id == user:
		return True
	if employee_in_admin_scope(employee, resolve_read_scope(user)):
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


def user_today(user=None, time_zone=None):
	"""Today's date, as a `YYYY-MM-DD` calendar value, in the user's own
	timezone. This is the server-side half of P2-AE3: every date this API
	derives from "now" has to agree with what the portal renders.
	`time_zone` is that user's zone when the caller has already read it."""
	return get_datetime_in_timezone(time_zone or get_user_time_zone(user)).strftime("%Y-%m-%d")


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

	Read-only, so the whole read runs inside one `shared_admin_scope`: the
	admin-scope gates below (read scope, roster, reports, people) and the
	permission hooks they trigger resolve the caller's anchor once.
	"""
	with shared_admin_scope():
		return _portal_bootstrap()


def _portal_bootstrap():
	"""`get_portal_bootstrap`'s body, run inside one `shared_admin_scope`."""
	employee = get_current_employee_info() or None
	time_zone = get_user_time_zone()
	today = user_today(time_zone=time_zone)
	monday, sunday = get_week_bounds(today)
	# Organisation and People share one gate; read it once.
	read_scope_kind = resolve_read_scope(frappe.session.user)["kind"]

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
		# so the nav item and the server's own gate can never disagree. Since
		# plan 2026-10-14-001 that is the read scope, which an All-data
		# company access grant widens.
		"can_see_organisation": read_scope_kind != "none",
		# The Email templates page (theme, message templates, celebrations &
		# holidays): All-orgs authority (plan 2026-10-19-001 R7) -- the
		# predicate every endpoint behind the page enforces. A boolean, never a
		# role list.
		"can_edit_email_templates": _can_edit_email_templates(frappe.session.user),
		# P6-U4: same shape again -- `search_people` and `get_person` are
		# gated by `resolve_read_scope` (`resolve_admin_scope` widened by an
		# All-data grant, plan 2026-10-14-001), so the nav item and the
		# server's gate agree by construction.
		"can_see_people": read_scope_kind != "none",
		# Roster's "Everyone" mode stays on `resolve_admin_scope`: a grant is
		# read-only and never opens the roster (KTD11).
		"can_roster_everyone": resolve_admin_scope(frappe.session.user)["kind"] != "none",
		# Plan 2026-10-22-001 U2: People's "Add employee" -- the predicate
		# `create_employee` and `get_new_person_options` enforce.
		"can_add_people": _can_add_people(frappe.session.user),
		# P7-U5: same shape as `can_see_people` just above -- `search_projects`
		# and `get_project` are gated by `resolve_project_read_scope`, so the
		# nav item and the server's gate agree by construction.
		"can_see_projects": resolve_project_read_scope(frappe.session.user)["kind"] != "none",
		# Plan 2026-10-04-001 U4: the Reports nav item. True when
		# `get_report_catalog` would list at least one entry -- the same
		# `resolve_report_access` answer `run_report` enforces.
		"can_run_reports": _can_run_reports(frappe.session.user),
		# Portal Admin and System Manager: the access matrix, the export log
		# and the portal-role section -- `can_admin_portal` is the predicate
		# each of those endpoints enforces. HR Manager is refused since plan
		# 2026-10-06-001 U3 (R5/R6).
		"can_admin_portal": can_admin_portal(frappe.session.user),
		# Plan 2026-10-19-001 U4: a Manage access holder or System Manager --
		# People & access reaches every org for them (R24), with or without a
		# Portal Admin row.
		"can_manage_access": can_manage_access(frappe.session.user),
		# Plan 2026-10-15-002 U4: HR User may see pending timesheets.
		"can_oversee_timesheets": _can_oversee_timesheets(frappe.session.user),
		# The All pending tab: `get_pending_by_approver`'s non-timesheet gate.
		"can_see_all_pending": _can_see_all_pending(frappe.session.user),
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
		# Plan 2026-10-17-001 KTD7/KTD8: the approver tab swap and the
		# Approvals badge. Set below for a caller with an Employee; a desk-only
		# session has no queue of its own and keeps the zeros.
		"stable_approver": False,
		"approvals_count": 0,
		"approvals_count_capped": False,
		# Plan 2026-10-17-001 U6 / EP-10: whether Home offers the check-in chip.
		# HR Settings' own flag, and false with no Employee (nobody to punch).
		"mobile_checkin": False,
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
	# direct reports alone hid the entry from both (P2-R11). P3-KTD11 also
	# surfaces the reports as `has_reports`, which is what the Team nav item
	# reads. Plan 2026-10-17-001 U4: the reports are read as the line-manager
	# filter itself, inside one `_shared_queue_reads`, so the count read
	# below reuses them instead of asking again.
	title = "HelixHR portal bootstrap failed"
	with _shared_queue_reads():
		reports = _safe(lambda: _line_manager_filter(employee["name"]), title=title)
		report_count = len(reports[1]) if reports else 0
		queue = _safe(
			lambda: _approval_summaries(
				employee["name"], include_requests=False, count_only=True, today=today
			),
			title=title,
		)
	boot["has_reports"] = report_count > 0
	# P4-R13: the Approvals nav item is present for every HR Manager, empty
	# queue or not -- HR's work arrives without warning and a rail item that
	# comes and goes is a rail item nobody trusts. Still a nav decision, not
	# a grant: `_assert_may_act_on` re-checks every read and every action.
	# Plan 2026-10-16-001 KTD1: can_approve stops counting request rows.
	#
	# Plan 2026-10-17-001 KTD7: `stable_approver` is the half of that answer
	# that cannot change between two loads because a queue emptied -- reports,
	# or an HR approver. It swaps Approvals into the phone tab bar; a leave
	# approver with no reports stays queue-dependent and keeps the employee
	# bar, so no tab comes and goes (P4-R13's rule, applied to the tab bar).
	boot["stable_approver"] = report_count > 0 or _is_hr(frappe.session.user)
	# KTD8: the badge, seeded here once and then kept by the browser. The
	# count-only read is the Approvals page's own (`get_my_approvals`), so the
	# two numbers match; it replaces the full read `can_approve` used to make
	# for a caller with no reports, and for everyone else it is the one added
	# read (run above, beside the reports). A failure logs and counts 0 --
	# never a broken shell.
	if queue:
		rows, capped, _lane_counts = queue
		boot["approvals_count"] = len(rows)
		# `get_my_approvals`' `total_is_capped`, on the same rows.
		boot["approvals_count_capped"] = bool(capped or len(rows) > _QUEUE_HARD_CAP)
	boot["can_approve"] = boot["stable_approver"] or boot["approvals_count"] > 0
	boot["unread_notifications"] = _safe(_get_unread_notification_count, title=title) or 0
	boot["mobile_checkin"] = bool(_safe(_mobile_checkin_allowed, title=title))
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
	"""One card row: who, which day, and whether that day is today or past.

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
		# Already over this month: the card draws it last and dimmed.
		"is_past": event_date.day < today.day,
	}


def _get_celebrations(employee, today, company=None):
	"""This month's birthdays and work anniversaries in ``employee``'s
	company (or ``company``, for the Organisation view), today's first and
	then by day.

	An employee whose record carries no company gets empty lists rather
	than an error, exactly as `get_directory` does -- "we cannot tell which
	company you are in" is a thing for HR to fix, and the card simply does
	not appear.
	"""
	company = company or frappe.db.get_value("Employee", employee, "company")
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
	"""Today first, then the rest of the month still to come, then the days
	already past. Someone reading the card wants today's names and who is
	next at the top; the card shows five rows, so past days must not push an
	upcoming one out of sight."""
	return sorted(
		entries, key=lambda entry: (0 if entry["is_today"] else 2 if entry["is_past"] else 1, entry["day"])
	)


def _open_leave(employee):
	"""This employee's leave still waiting on somebody -- the rows the
	queue's "Waiting on others" list is drawn from -- with the stage and the
	approver's display name (plan 2026-10-31-001 R26).

	Bounded by _QUEUE_FETCH like every other queue source.
	"""
	rows = frappe.get_all(
		"Leave Application",
		filters={"employee": employee, "status": "Open"},
		fields=["name", "leave_type", "from_date", "to_date", "helixhr_stage", "leave_approver"],
		order_by="from_date asc",
		limit=_QUEUE_FETCH,
	)
	names = _leave_approver_names(row.leave_approver for row in rows)
	for row in rows:
		row.approver_name = names.get(row.leave_approver)
	return rows


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
	# KTD11: final outcomes, a sibling of `waiting` -- nobody's move, never
	# dropped by time.
	decided = []

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
				doctype="Timesheet",
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
				doctype="Leave Application",
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
				doctype="Attendance Request",
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
	# to edit, resend or chase, so it stays out of the queue of work. Plan
	# 2026-10-17-001 KTD11 moves it from "Waiting on others" (it waits on
	# nobody, EP-08) to the sibling "Decided" list, still with no expiry.
	# Once each: the two collectors above are scoped to the send-back states,
	# so a rejected record reaches exactly one of these lists and never both.
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
		decided.append(
			_queue_item(
				kind="leave_final_rejected",
				name=row.name,
				doctype="Leave Application",
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
		decided.append(
			_queue_item(
				kind="attendance_request_final_rejected",
				name=row.name,
				doctype="Attendance Request",
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
	#
	# Plan 2026-10-17-001 KTD12, from the same single read (a join, not a
	# second query): an unread *status-only* row on the employee's own Done or
	# Declined request is an update, not an ask, so it goes to "Decided".
	# Newest first, so `_collapse_by_record` keeps the newest text and the
	# hard cap drops the oldest replies rather than the newest (KTD10).
	log = frappe.qb.DocType("Notification Log")
	request = frappe.qb.DocType("HR Request")
	for row in (
		frappe.qb.from_(log)
		.left_join(request)
		.on(request.name == log.document_name)
		.select(log.name, log.document_name, log.subject, log.description, log.creation)
		.where(log.for_user == frappe.session.user)
		.where(log.read == 0)
		.where(log.document_type == "HR Request")
		.where(
			log.subject.like(f"{HR_REPLY_SUBJECT_PREFIX}%")
			| ((request.employee == employee) & request.status.isin([HR_REQUEST_DONE, HR_REQUEST_REJECTED]))
		)
		.orderby(log.creation, order=frappe.qb.desc)
		.limit(_QUEUE_HARD_CAP)
		.run(as_dict=True)
	):
		replied = (row.subject or "").startswith(HR_REPLY_SUBJECT_PREFIX)
		(items if replied else decided).append(
			_queue_item(
				kind="request_answered" if replied else "request_decided",
				name=row.document_name,
				doctype="HR Request",
				title=row.subject if replied else _notification_text(row.subject),
				detail=_notification_text(row.description),
				date=str(row.creation),
				day=None,
				age_days=age_days(row.creation),
				action="Read" if replied else "View",
				owner="you" if replied else "nobody",
				urgency="unread",
				to={"name": "RequestDetail", "params": {"name": row.document_name}},
				notification=row.name,
			)
		)

	# One row per decision, not a count: "3 requests waiting" is a link to a
	# list, and P2-R12 asks for the exact decision.
	# Plan 2026-10-16-001 KTD1/KTD8: requests route to the Team inbox.
	for decision in once("approvals", lambda: _pending_approvals(employee)):
		target_to = (
			{
				"name": "RequestDetail",
				"params": {"name": decision["reference_name"]},
				"query": {"tab": "work"},
			}
			if decision["route_kind"] == "request"
			else {
				"name": "ApprovalDetail",
				"params": {"kind": decision["route_kind"], "name": decision["reference_name"]},
			}
		)
		items.append(
			_queue_item(
				kind=decision["kind"],
				name=decision["reference_name"],
				doctype=decision["reference_doctype"],
				title=decision["title"],
				detail=None,
				date=decision["date"],
				day=day_for(decision["date"]),
				age_days=age_days(decision["date"]),
				action="Review",
				owner="you",
				urgency="decision",
				to=target_to,
			)
		)

	for row in once("open_leave", lambda: _open_leave(employee)):
		# Plan 2026-10-31-001 R26: "waiting for HR" only for the HR stage;
		# otherwise the manager by name, and the old words with nobody to name.
		with_hr = row.helixhr_stage == LEAVE_STAGE_HR
		waiting_for = "HR" if with_hr else row.approver_name or "your manager"
		waiting.append(
			_queue_item(
				kind="leave_waiting",
				name=row.name,
				doctype="Leave Application",
				title=f"{row.leave_type} waiting for {waiting_for}",
				detail=None,
				date=str(row.from_date),
				day=day_for(row.from_date),
				age_days=age_days(row.from_date),
				action="View",
				owner="hr" if with_hr else "manager",
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
				doctype="Attendance Request",
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

	# KTD10: one row per record, per list, *before* the sort and the slice,
	# so the sort sees each group's oldest age and `more` counts records.
	items = _collapse_by_record(items)
	for item in items:
		if item["kind"] == "request_answered" and item["count"] > 1:
			# "HR replied · 4 · Address proof": the count, then the subject
			# the reply subject already carries -- no added read (HR-04).
			subject = item["title"][len(HR_REPLY_SUBJECT_PREFIX) :].strip()
			item["title"] = f"HR replied · {item['count']} · {subject}"
	waiting = _collapse_by_record(waiting)
	# KTD12: a request the employee still owes (an unread message) is one row
	# in the queue, not a second one under "Decided".
	owed = {_record_key(item) for item in items}
	decided = [item for item in _collapse_by_record(decided) if _record_key(item) not in owed]

	items.sort(key=lambda item: (_URGENCY_RANK[item["urgency"]], -(item["age_days"] or 0)))
	waiting.sort(key=lambda item: -(item["age_days"] or 0))
	# P4-R4 / KTD11: decided rows are terminal receipts with no expiry. A
	# recency window was the alternative and is worse: it would drop a
	# rejection the employee never read, and the reason is the whole content
	# of the row. Newest first, so the window shows the latest outcomes and
	# `decided_more` counts the rest.
	decided.sort(key=lambda item: item["age_days"] if item["age_days"] is not None else 1 << 30)
	shown = items[:_QUEUE_LIMIT]
	return {
		"items": shown,
		"more": max(0, len(items) - len(shown)),
		"waiting": waiting[:_QUEUE_LIMIT],
		# The "View all (N)" count for the waiting list, as `more` is for the queue.
		"waiting_more": max(0, len(waiting) - _QUEUE_LIMIT),
		"decided": decided[:_QUEUE_LIMIT],
		"decided_more": max(0, len(decided) - _QUEUE_LIMIT),
	}


def _record_key(item):
	return (item["reference_doctype"], item["reference_name"])


def _collapse_by_record(rows):
	"""Plan 2026-10-17-001 KTD10: one row per target record.

	Same kind on one record (sixty unread replies on one request) folds into
	the first row seen, with `count` rows behind it and the group's *oldest*
	age and date. Every multi-row source is read newest-first, so the first
	row seen carries the newest text. Different kinds on one record keep the
	more urgent row by `_URGENCY_RANK`."""
	kept = {}
	for row in rows:
		key = _record_key(row)
		held = kept.get(key)
		if held is None:
			kept[key] = row
		elif held["kind"] == row["kind"]:
			held["count"] += 1
			if (row["age_days"] or 0) > (held["age_days"] or 0):
				held["age_days"] = row["age_days"]
				held["date"] = row["date"]
		elif _URGENCY_RANK[row["urgency"]] < _URGENCY_RANK[held["urgency"]]:
			kept[key] = row
	return list(kept.values())


def _queue_item(
	*,
	kind,
	name,
	doctype,
	title,
	detail,
	date,
	day,
	age_days,
	action,
	owner,
	urgency,
	to,
	notification=None,
):
	return {
		# Stable record identity, and the Vue list key. Never an index: a
		# re-ordered queue reused the wrong row's DOM state under one. The
		# record, not the notification, so a collapsed row keeps its key when
		# a new reply arrives (KTD10).
		"id": f"{kind}:{name}",
		"kind": kind,
		# The record the row is about -- the dedupe key, and what opening a
		# collapsed row marks read (`mark_record_notifications_read`).
		"reference_doctype": doctype,
		"reference_name": name,
		# How many rows of this kind this one row stands for (KTD10).
		"count": 1,
		# The newest unread log behind the row, when one is.
		"notification": notification,
		"title": title,
		"detail": detail,
		"date": date,
		"day": day,
		"age_days": age_days,
		"action": action,
		# Whose move it is. "you" rows are the queue; "manager" and "hr" rows
		# are the waiting list; "nobody" rows are "Decided" -- a decision that
		# has already been made and only needs reading (P4-R4, KTD11).
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
		# U4 / R10: why HR sees a manager-stage leave -- "approver_away",
		# "overdue" or, plan 2026-10-31-001 KTD15, "with_manager" -- and None
		# for everything else.
		"hr_reason": None,
		# Plan 2026-10-31-001 R22: who a manager-stage leave HR sees is
		# waiting on. None on every other row, and on a leave with no approver.
		"approver_name": None,
		# Plan 2026-10-04-003 KTD7: the "Needs a look" flags, computed on the
		# server and shipped on the row, so the display and the batch
		# endpoint's re-check can never disagree. None for the kinds that
		# carry no flags (attendance, request, change).
		"flags": None,
		"needs_look": None,
	}
	row.update(extra)
	return row


def _count_rows(kind, rows):
	"""The `count_only` answer of a queue collector (plan 2026-10-17-001
	KTD8): the same rows the full path keeps -- same reads, same Python
	subtractions, same bound -- cut to the two keys `_approval_summaries`
	needs to count and lane them. No enrichment, so the bootstrap's badge
	costs the reads and nothing else."""
	return [{"kind": kind, "name": row["name"]} for row in rows]


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


def _employment_span(employee):
	"""`(joined, relieved)` as dates, either None when unset: the
	joining..relieving clamp `get_my_month` and `get_my_week` share."""
	joined, relieved = frappe.db.get_value("Employee", employee, ["date_of_joining", "relieving_date"])
	return (getdate(joined) if joined else None, getdate(relieved) if relieved else None)


def _within_employment(days, span):
	"""The dates in `days` that fall inside the `_employment_span`."""
	joined, relieved = span
	return {
		day
		for day in days
		if (not joined or getdate(day) >= joined) and (not relieved or getdate(day) <= relieved)
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


_QUEUE_READS_FLAG = "helixhr_queue_reads"


def _shared_queue_reads():
	"""Inside this block, each queue scope read (`_line_manager_filter`,
	`_hr_queue_employee_filter`) runs once per employee instead of once per
	collector (plan 2026-10-17-001 U4's read budget). Neither answer can
	change between two collectors of one queue read, and the memo lives only
	as long as the block -- never across requests, so a reassignment is seen
	by the very next read. Nested blocks share the outer memo."""
	return request_memo(_QUEUE_READS_FLAG)


def _shared_queue_read(key, read):
	"""`read()`, answered once per `key` inside `_shared_queue_reads`."""
	return request_memo_get(_QUEUE_READS_FLAG, key, read)


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
	reports = _shared_queue_read(
		("line_manager", employee),
		lambda: frappe.get_all(
			"Employee",
			filters={"reports_to": employee, "status": "Active"},
			pluck="name",
		),
	)
	return ["in", reports] if reports else None


def _leave_summaries(employee, today, count_only=False):
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
	if count_only:
		return _count_rows("leave", [row for row in applications if row["name"] not in with_hr])

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


def _timesheet_summaries(employee, today, count_only=False):
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
		fields=["name"]
		if count_only
		else [
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
	if not rows or count_only:
		return _count_rows("timesheet", rows)

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


def _attendance_request_summaries(employee, today, count_only=False):
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
		fields=["name"]
		if count_only
		else [
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
	if count_only:
		return _count_rows("attendance", rows)
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
	return _shared_queue_read(("hr_queue", employee), lambda: _read_hr_queue_employee_filter(employee))


def _read_hr_queue_employee_filter(employee):
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


def _hr_leave_summaries(employee, today, count_only=False):
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
		fields=["name"]
		if count_only
		else [
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
	if count_only:
		return _count_rows("leave", rows) + _hr_stalled_leave_summaries(
			employee, today, employee_filter, count_only=True
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


# Plan 2026-10-31-001 KTD15 (R23): a manager-stage leave HR may see but does
# not owe -- the "With managers" group. Never in a count: not the badge, not
# `total`, not Home's decisions.
HR_REASON_WITH_MANAGER = "with_manager"


def _owed(row):
	"""Whether a queue row is work the caller owes (KTD15): everything except
	the "With managers" rows, which HR may decide but is not waiting on."""
	return row.get("hr_reason") != HR_REASON_WITH_MANAGER


def _leave_approver_names(users):
	"""The display name of each leave approver login, through the overdue
	digest's own resolver -- one Employee read and one User read whatever the
	count. An approver who has left or lost their login keeps a name; only
	"nobody" has none."""
	items = [{"approver": {"user": user}} for user in {user for user in users if user}]
	return {item["approver_user"]: item["approver_name"] for item in reminders.resolve_approvers(items)}


def _hr_stalled_leave_summaries(employee, today, employee_filter, count_only=False):
	"""Manager-stage leave HR sees (R10, KTD4; plan 2026-10-31-001 KTD15).
	Admin-scoped by `employee_filter`, never narrowed by
	`_line_manager_filter`: these are other managers' reports. The caller's
	own approvals are already in their line-manager half.

	Two kinds of row. `approver_away` and `overdue` are HR's to decide and
	count; `with_manager` is everything else, shown so HR can see who it is
	waiting on, and counted nowhere (`count_only` leaves it out).

	The stalled rows are read first, by their own filter, so a backlog of
	fresh requests can never push one past `_QUEUE_FETCH`: who is away comes
	from the distinct approvers in scope (one small read), and overdue is the
	same strict `is_overdue` test as a date bound on `helixhr_pending_since`.
	The "With managers" read is a second, separately bounded one.
	"""
	scope = {
		"status": "Open",
		"docstatus": 0,
		"helixhr_stage": ["in", ["", None, _LEAVE_STAGE_MANAGER]],
		"employee": employee_filter,
		# Not "not in ['', user]": a request with no approver is still waiting
		# on somebody, and HR is the one who can see that.
		"leave_approver": ["!=", frappe.session.user],
	}
	approvers = frappe.get_all("Leave Application", filters=scope, pluck="leave_approver", distinct=True)
	away = _approvers_away({user for user in approvers if user}, today)
	threshold = approval_overdue_days()
	stalled_or = {PENDING_SINCE_FIELD: ["<", str(add_days(today, -threshold))]}
	if away:
		stalled_or["leave_approver"] = ["in", list(away)]
	fields = [
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
	]
	rows = frappe.get_all(
		"Leave Application",
		filters=scope,
		or_filters=stalled_or,
		fields=["name", "leave_approver", PENDING_SINCE_FIELD] if count_only else fields,
		order_by="creation asc",
		limit=_QUEUE_FETCH,
	)
	reasons = {}
	for row in rows:
		if row.leave_approver in away:
			reasons[row.name] = "approver_away"
		elif is_overdue(row.get(PENDING_SINCE_FIELD), today, threshold):
			reasons[row.name] = "overdue"
	if count_only:
		# The away/overdue test is the subtraction, so `count_only` keeps it,
		# and appends only what `_count_rows` would keep.
		return [{"kind": "leave", "name": row.name} for row in rows if row.name in reasons]

	rows = [row for row in rows if row.name in reasons]
	rows += frappe.get_all(
		"Leave Application",
		filters={**scope, "name": ["not in", [row.name for row in rows] or [""]]},
		fields=fields,
		order_by="creation asc",
		limit=_QUEUE_FETCH,
	)
	names = _leave_approver_names(row.leave_approver for row in rows)
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
			for_hr=True,
			hr_reason=reasons.get(row.name, HR_REASON_WITH_MANAGER),
			approver_name=names.get(row.leave_approver),
		)
		for row in rows
	]


def _hr_timesheet_summaries(employee, today, count_only=False):
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
		fields=["name"]
		if count_only
		else [
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
	if count_only:
		return _count_rows("timesheet", rows)
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


def _hr_attendance_request_summaries(employee, today, count_only=False):
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
		fields=["name"]
		if count_only
		else [
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
	if count_only:
		return _count_rows("attendance", rows)
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


def _hr_request_summaries(
	employee,
	today,
	states=None,
	picked_up_by=None,
	routed_roles=None,
	limit=None,
	start=None,
	category=None,
	or_filters=None,
	names=None,
	sla_days=None,
	with_actions=False,
	order_by="creation asc",
):
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
	  cannot act on an IT-routed row, so neither the work feed nor (plan
	  2026-10-16-001 R4, via `_hr_request_queue`) the queue shows one.
	* `limit` / `start` page the feed; the queue stays bounded at
	  `_QUEUE_FETCH` and reports a floor instead (P3-R25).

	The filter dict itself is built by `_request_summaries_filters`, so the
	feed's page and its count can never ask two different questions.

	Plan 2026-10-17-001 U8 (all additive, the queue passes none of them):
	`category` and `or_filters` (the inbox search, `_request_search_or_filters`)
	narrow the page exactly as they narrow the feed's count; `names` reads one
	already-ordered page of names (the `sort=sla` page); `sla_days` reuses a
	category read the caller already made; `with_actions` adds each row's
	`actions` (`_request_row_actions`). Every row carries `token_modified`
	and `sla_over_days`.

	`order_by` lets the work feed's Closed chip read most-recently-closed
	first (`_REQUEST_CLOSED_ORDER`); every other caller keeps oldest first.
	Rows carry `closed_on`, so a closed row can say when it closed.

	Rows always carry `picked_up_by_name` (resolved server-side, KTD4) and
	`sla_overdue` (the category's SLA against age, `is_overdue`'s predicate)
	-- keys the queue's consumers ignore and the feed renders.
	"""
	filters = _request_summaries_filters(
		employee, states or (HR_REQUEST_OPEN, HR_REQUEST_IN_PROGRESS), picked_up_by, routed_roles, category
	)
	if names is not None:
		if not names:
			return []
		filters["name"] = ["in", names]
	rows = frappe.get_list(
		"HR Request",
		filters=filters,
		or_filters=or_filters or None,
		fields=[
			"name",
			"owner",
			"employee",
			"category",
			"subject",
			"status",
			"routed_to_role",
			"picked_up_by",
			"creation",
			"modified",
			"closed_on",
			"hr_note",
			# Plan 2026-10-02-001 U14 / R28: the masked copy only -- the
			# Password fields are never read into a queue.
			"correction_field",
			"correction_proposed_masked",
		],
		order_by=order_by,
		limit=_QUEUE_FETCH if limit is None else min(max(cint(limit), 1), _REQUEST_MAX_PAGE),
		**({"limit_start": max(cint(start), 0)} if start is not None else {}),
	)
	if not rows:
		return []

	employees = {
		row.name: row
		for row in frappe.get_all(
			"Employee",
			filters={"name": ["in", list({row.employee for row in rows})]},
			fields=["name", "employee_name", "first_name"],
		)
	}
	waiting_rows = [r for r in rows if r.status == HR_REQUEST_WAITING_ON_EMPLOYEE]
	latest_questions = {}
	if waiting_rows:
		comments = frappe.get_all(
			"Comment",
			filters={
				"reference_doctype": "HR Request",
				"reference_name": ["in", [r.name for r in waiting_rows]],
				"comment_type": "Comment",
				"content": ["like", f"{HR_REQUEST_QUESTION_PREFIX}%"],
			},
			fields=["reference_name", "creation"],
			order_by="creation desc",
		)
		for c in comments:
			if c.reference_name not in latest_questions:
				latest_questions[c.reference_name] = c.creation
	picker_names = _picker_display_names(row.picked_up_by for row in rows)
	# One read per page, not per row (P2-R22): SLA days ride beside the
	# routed role the categories already answer.
	if sla_days is None:
		sla_days = _request_sla_days()
	actions = _request_row_actions(rows, frappe.session.user) if with_actions else {}
	result = []
	for row in rows:
		waiting_on = None
		waiting_since = None
		if row.status == HR_REQUEST_WAITING_ON_EMPLOYEE:
			emp = employees.get(row.employee) or {}
			first_name = (emp.get("first_name") or "").strip()
			waiting_on = first_name if first_name else "the employee"
			since = latest_questions.get(row.name) or row.modified
			waiting_since = str(since) if since else None
		emp_name = (employees.get(row.employee) or {}).get("employee_name") or row.employee
		overdue = _request_sla_overdue(row, today, sla_days)
		result.append(
			_summary_row(
				"request",
				"HR Request",
				row.name,
				row.employee,
				emp_name,
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
				sla_overdue=overdue,
				# U8 / AI-17: how far past, so the pill can say "2 d over".
				sla_over_days=_age_in_days(row.creation, today) - sla_days.get(row.category, 0)
				if overdue
				else None,
				# U8 / AI-04: the row's own stale-write token, so "Assign to
				# me" from the list is checked like the detail's button.
				token_modified=str(row.modified),
				**({"actions": actions.get(row.name, [])} if with_actions else {}),
				for_hr=(row.routed_to_role == "HR Manager"),
				hr_note=row.hr_note,
				correction_field=row.correction_field,
				correction_proposed_masked=row.correction_proposed_masked,
				waiting_on=waiting_on,
				waiting_since=waiting_since,
				waiting_days=_age_in_days(waiting_since, today) if waiting_since else None,
				closed_on=str(row.closed_on) if row.closed_on else None,
			)
		)
	return result


def _request_summaries_filters(employee, states, picked_up_by=None, routed_roles=None, category=None):
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
	if category:
		filters["category"] = category
	return filters


# Plan 2026-10-17-001 U8 / KTD5: the bounded request search. `q` is trimmed,
# ignored under 2 characters, cut at 60, and its LIKE metacharacters are
# escaped, so "50%" finds "50%" rather than everything starting "50".
_REQUEST_SEARCH_MIN = 2
_REQUEST_SEARCH_MAX = 60
# The requester-name half resolves at most this many Employees.
_REQUEST_SEARCH_PEOPLE = 200


def _request_search_pattern(q):
	"""`q` as a LIKE pattern, or None when there is nothing to search for."""
	term = (q if isinstance(q, str) else "").strip()[:_REQUEST_SEARCH_MAX]
	if len(term) < _REQUEST_SEARCH_MIN:
		return None
	escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
	return f"%{escaped}%"


def _request_requester_scope(user):
	"""The Employee filter for "requesters this caller may see", or None for
	nobody -- the same branches as `hr_request.get_permission_query_conditions`:
	an HR/System Manager reads through `resolve_admin_scope`, a routed worker
	through their assignments for the roles they work (plan 2026-10-19-001
	U3), anyone else through their own company (their own requests)."""
	if user == "Administrator":
		return {}
	roles = set(frappe.get_roles(user))
	scopes = [role_scope(user, role) for role in _routed_roles_held(user) if role != "HR Manager"]
	if roles & {"HR Manager", "System Manager"}:
		scopes.append(resolve_admin_scope(user))
	companies = set()
	for scope in scopes:
		covered = read_companies(scope)
		if covered is None:
			return {}
		companies.update(covered)
	own = session_company(user)
	if own:
		companies.add(own)
	return {"company": ["in", sorted(companies)]} if companies else None


def _request_search_or_filters(q, user):
	"""The inbox search as `or_filters`: subject, or a requester whose name
	matches (U8; U5's palette reuses it). None when `q` is too short.

	The requester half is one bounded Employee read inside the caller's own
	scope, turned into `employee in (...)` on the same permission-aware
	`get_list` the page and its count already run -- so a name match can
	only ever narrow what the caller could already list, never widen it."""
	pattern = _request_search_pattern(q)
	if not pattern:
		return None
	or_filters = [["subject", "like", pattern]]
	scope = _request_requester_scope(user)
	if scope is not None:
		requesters = frappe.get_all(
			"Employee",
			filters={**scope, "employee_name": ["like", pattern]},
			pluck="name",
			limit=_REQUEST_SEARCH_PEOPLE,
		)
		if requesters:
			or_filters.append(["employee", "in", requesters])
	return or_filters


def _request_sla_days():
	"""{category -> SLA days}, one read over a bounded table."""
	return {
		name: cint(sla)
		for name, sla in frappe.get_all("HelixHR Request Category", fields=["name", "sla_days"], as_list=True)
	}


def _request_sla_overdue(row, today, sla_days):
	"""The one SLA predicate for a request row: the row's pill and the
	`sort=sla` order both read it, so they can never disagree."""
	return bool(
		row.status in (HR_REQUEST_OPEN, HR_REQUEST_IN_PROGRESS)
		and is_overdue(row.creation, today, sla_days.get(row.category, 0))
	)


def _request_row_actions(rows, user):
	"""{name -> the actions this user may take on that row}, with no query
	per row (U8 / AI-04: the inbox's "Assign to me").

	Drawn from the same `HR Request Handling` transitions `_allowed_actions`
	derives the detail's `actions` from -- state, held role, self-approval --
	in `_APPROVAL_ACTIONS` order. The fixture conditions' two clauses are the
	feed's own filters, applied before a row gets here: the transition's role
	is the row's stored `routed_to_role` (`_may_act_on_hr_request`), and the
	caller's own requests are excluded (`employee != caller`). Evaluating the
	condition itself would cost a query per row. `act_on_approval` re-runs
	`_allowed_actions` on every click, so a row button can be refused, never
	widened."""
	from frappe.model.workflow import get_workflow, has_approval_access

	transitions = get_workflow("HR Request").transitions
	roles = set(frappe.get_roles(user))
	actions = {}
	for row in rows:
		reachable = {
			transition.action
			for transition in transitions
			if transition.state == row.status
			and transition.allowed == row.routed_to_role
			and transition.allowed in roles
			and transition.action in _APPROVAL_ACTIONS
			and has_approval_access(user, row, transition)
		}
		actions[row.name] = [action for action in _APPROVAL_ACTIONS if action in reachable]
	return actions


# Plan 2026-10-04-002 U3: the "To work on" chips. Each chip names the
# statuses it can mean; "open" is the working set, "waiting" is the ball in
# the employee's hands, "closed" is off by default.
# Plan 2026-10-16-001 KTD2: open and mine gain Waiting on Employee so picked-up
# and active requests never vanish from handler views.
_REQUEST_WORK_STATES = {
	"open": (HR_REQUEST_OPEN, HR_REQUEST_IN_PROGRESS, HR_REQUEST_WAITING_ON_EMPLOYEE),
	"mine": (HR_REQUEST_OPEN, HR_REQUEST_IN_PROGRESS, HR_REQUEST_WAITING_ON_EMPLOYEE),
	"waiting": (HR_REQUEST_WAITING_ON_EMPLOYEE,),
	"closed": (HR_REQUEST_DONE, HR_REQUEST_REJECTED),
}
# Closed-order and search fix (2026-10-17): a search ignores the state chip and covers
# every status the chips above can already list -- never more.
_REQUEST_SEARCH_STATES = tuple(dict.fromkeys(s for states in _REQUEST_WORK_STATES.values() for s in states))
_REQUEST_CLOSED_STATES = _REQUEST_WORK_STATES["closed"]
# The Closed chip reads most recently closed first; a row closed before
# `closed_on` was stamped falls back to its last change, after the dated ones
# (MariaDB sorts NULL last on DESC).
_REQUEST_CLOSED_ORDER = "closed_on desc, modified desc"


def _request_closed_first_key(row):
	"""Python twin of `_REQUEST_CLOSED_ORDER`, for the name-list paths:
	used with `reverse=True`, dated rows first, newest first, then `modified`."""
	return (row.closed_on is not None, row.closed_on or row.modified, row.modified)


@frappe.whitelist()
def get_request_work(state=None, limit=None, start=0, q=None, sort=None, category=None):
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

	Plan 2026-10-17-001 U8 (R13), all additive:

	* `q` searches subject or requester name (`_request_search_or_filters`)
	  and `category` names one category; both narrow the page *and* `total`.
	  `action_count` stays unfiltered -- it is the tab's badge.
	* `sort=sla` puts rows past their category's SLA first, each half oldest
	  first; the default (`''`) keeps plain oldest-first. Frappe's `get_list`
	  cannot order by a per-category date expression, so the count read --
	  which already lists every matching row -- also carries `category`,
	  `creation` and `status`, the rows are ordered here by the same
	  predicate the row's pill uses (`_request_sla_overdue`), and the page is
	  read by name. No query is added for it.
	* Rows carry `token_modified` and the caller's `actions` for the row, so
	  "Assign to me" works from the list.

	Closed order and all-state search (2026-10-17 fix):

	* `state=closed` lists the most recently closed first
	  (`_REQUEST_CLOSED_ORDER`); the other chips keep oldest-first triage.
	* A real search (`q` of 2+ characters) ignores the state chip -- `mine`
	  included -- and covers every status the chips can list
	  (`_REQUEST_SEARCH_STATES`), through the same permission-aware read and
	  role/route filters, so it can only find what some chip already shows.
	  Active rows come first (oldest first, or SLA order under `sort=sla`),
	  then closed rows most recently closed first; the page is cut from that
	  one ordered name list, as `sort=sla` already does. `searched` says
	  which mode answered.
	"""
	rate_limit_per_user("get_request_work")
	if not (_is_hr() or _holds_routed_role()):
		frappe.throw(_("You don't have permission to do that."), frappe.PermissionError)

	state = (state or "").strip().lower() or "open"
	if state not in _REQUEST_WORK_STATES:
		frappe.throw(_("There is no work filter called {0}.").format(state))
	sort = sort.strip().lower() if isinstance(sort, str) else (sort or "")
	if sort not in ("", "sla"):
		frappe.throw(_("There is no sort called {0}.").format(sort))
	category = _valid_request_category(category)

	routed_roles = _routed_roles_held()
	if not routed_roles:
		# An HR-adjacent session (System Manager) without either role that
		# routes requests: nothing here is theirs to work.
		return {
			"work": [],
			"total": 0,
			"action_count": 0,
			"categories": [],
			"limit": _REQUEST_PAGE,
			"today": user_today(),
		}

	employee = get_current_employee()
	today = _as_date(user_today())
	page = min(max(cint(limit) or _REQUEST_PAGE, 1), _REQUEST_MAX_PAGE)
	start = max(cint(start), 0)
	or_filters = _request_search_or_filters(q, frappe.session.user)
	searched = or_filters is not None
	states = _REQUEST_SEARCH_STATES if searched else _REQUEST_WORK_STATES[state]
	picked_up_by = frappe.session.user if state == "mine" and not searched else None
	order_by = _REQUEST_CLOSED_ORDER if state == "closed" and not searched else "creation asc"
	# One category read serves the SLA order, every row's pill and the
	# inbox's category chips (the categories routed to a role held here).
	categories = frappe.get_all(
		"HelixHR Request Category", fields=["name", "sla_days", "route_to_role"], order_by="name asc"
	)
	sla_days = {row.name: cint(row.sla_days) for row in categories}
	filters = {
		"states": states,
		"routed_roles": routed_roles,
		"picked_up_by": picked_up_by,
		"category": category,
		"or_filters": or_filters,
		"sla_days": sla_days,
		"with_actions": True,
	}

	# One count for the same filter, so Load More has an honest end (P2-R22).
	# Read through `get_list`, not a bare COUNT: the caller's permission
	# conditions are the scope, and the count must answer exactly what the
	# page below was scoped to.
	matching = frappe.get_list(
		"HR Request",
		filters=_request_summaries_filters(employee, states, picked_up_by, routed_roles, category),
		or_filters=or_filters,
		order_by=order_by,
		limit_page_length=0,
		**(
			{"fields": ["name", "category", "creation", "status", "closed_on", "modified"]}
			if sort == "sla" or searched
			else {"pluck": "name"}
		),
	)
	total = len(matching)
	if sort == "sla" or searched:
		# Active rows (oldest first, overdue lifted under `sort=sla`), then
		# closed rows most recently closed first. Without a search only one
		# half is ever non-empty, so the plain chips read as before.
		active = [row for row in matching if row.status not in _REQUEST_CLOSED_STATES]
		closed = sorted(
			(row for row in matching if row.status in _REQUEST_CLOSED_STATES),
			key=_request_closed_first_key,
			reverse=True,
		)
		if sort == "sla":
			late = {row.name for row in active if _request_sla_overdue(row, today, sla_days)}
			active = [row for row in active if row.name in late] + [row for row in active if row.name not in late]
		ordered = [row.name for row in active] + [row.name for row in closed]
		names = ordered[start : start + page]
		position = {name: index for index, name in enumerate(names)}
		rows = sorted(
			_hr_request_summaries(employee, today, names=names, limit=page, start=0, **filters),
			key=lambda row: position[row["name"]],
		)
	else:
		rows = _hr_request_summaries(employee, today, limit=page, start=start, order_by=order_by, **filters)
	# Plan 2026-10-16-001 R8: Team inbox count is requests needing team action
	# (New and In progress), matching Home count.
	action_count = _aggregate_count(
		frappe.get_list(
			"HR Request",
			filters=_request_summaries_filters(
				employee,
				(HR_REQUEST_OPEN, HR_REQUEST_IN_PROGRESS),
				None,
				routed_roles,
			),
			fields=[{"COUNT": "*"}],
			limit_page_length=0,
		)[0]
	)
	return {
		"work": rows,
		"total": total,
		"action_count": action_count,
		# Plan 2026-10-16-001 U6: the inbox header names the team -- "HR" or
		# "IT" from the roles actually read, none when the caller works both.
		"team": _handled_by_team(routed_roles[0]) if len(routed_roles) == 1 else None,
		"categories": [row.name for row in categories if row.route_to_role in routed_roles],
		"limit": page,
		"today": str(today),
		"searched": searched,
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


# Plan 2026-10-15-002 KTD1: requests routed here make up the IT lane.
_IT_ROLE = "IT Team"


def _approval_summaries(employee, include_requests=True, count_only=False, today=None):
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

	Plan 2026-10-15-002 KTD1: each row carries a ``lane`` key: ``"team"``
	for line-manager items, ``"hr"`` for HR-stage items, ``"it"`` for
	IT-routed requests. ``counts.lanes`` reports per-lane totals.

	Plan 2026-10-16-001 KTD1: `include_requests=False` is Approvals' read --
	routed requests are not collected at all, so they can neither show nor
	fill a collector and mark the decisions as capped. Home keeps the default.

	Plan 2026-10-17-001 KTD8: `count_only=True` is the bootstrap's badge
	read. Every collector runs the same reads, the same Python subtractions
	and the same per-kind bound, so `len(rows)` and `capped` are exactly what
	`get_my_approvals` would answer; the enrichment (balances, overlap,
	flags, senders, photos) and the sort are skipped, and each row is only
	`{kind, name, lane}`. `today` saves the caller's own timezone read.
	"""
	with _shared_queue_reads():
		return _collect_approval_summaries(employee, include_requests, count_only, today)


def _collect_approval_summaries(employee, include_requests, count_only, today):
	"""`_approval_summaries`' body, run inside one `_shared_queue_reads`."""
	today = _as_date(today or user_today())
	rows = []
	# Whether any collector came back full, and the total is therefore a floor
	# rather than a count. Each read is bounded at `_QUEUE_FETCH` per kind
	# (P3-R25), so a backlog of 200 used to be reported as 50 with nothing
	# saying so.
	capped = False
	lane_counts = {"team": 0, "hr": 0, "it": 0}

	def _extend(collected, lane):
		"""Tag `collected` with `lane` and add it. A routed request goes to
		the lane of the role it is routed to, whichever half read it (KTD1:
		HR keeps its read-only view of IT-routed rows, tagged `it`)."""
		for row in collected:
			if row["kind"] == "request":
				row["lane"] = "it" if row.get("routed_to_role") == _IT_ROLE else "hr"
			else:
				row["lane"] = lane
			# Plan 2026-10-31-001 KTD15: "With managers" rows ride in the HR
			# lane but count nowhere, so the full read's counts are the badge's.
			if _owed(row):
				lane_counts[row["lane"]] += 1
		rows.extend(collected)

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
			collected = collect(employee, today, count_only=count_only)
			capped = capped or len(collected) >= bound
			_extend(collected, "team")
	# P4-R11: one queue. An HR Manager's own reports' work arrived above,
	# narrowed by `_line_manager_filter`; everything waiting for HR is added
	# here and tagged, so the two halves are one oldest-first backlog rather
	# than two lists to poll. Plan 2026-10-15-002 KTD1: tagged "hr".
	# Plan 2026-10-16-001 R4: HR Managers see HR requests only; IT requests
	# belong to IT Team.
	if _is_hr():
		for doctype, kind in _APPROVAL_KINDS.items():
			if doctype == "HR Request" and not include_requests:
				continue
			collected = kind["hr_queue"](employee, today, count_only=count_only)
			capped = capped or sum(1 for row in collected if _owed(row)) >= _QUEUE_FETCH
			_extend(collected, "hr")
	elif include_requests and _holds_routed_role():
		# P5-R11: a routed-role holder (IT Team today) has no line-manager
		# half and no standing on Leave Application, Timesheet or Attendance
		# Request at all -- calling their `hr_queue` collectors the way the
		# `_is_hr()` branch above does would be the same hard PermissionError
		# the line-manager loop hit above, not an empty answer. Their whole
		# queue is `_hr_request_summaries`, and only that one is reached.
		collected = _hr_request_queue(employee, today)
		capped = capped or len(collected) >= _QUEUE_FETCH
		_extend(collected, "hr")

	if count_only:
		return rows, capped, lane_counts
	# Oldest first: the queue is a backlog, and the person who has waited
	# longest is the one the manager is holding up (P2-U7 step 7).
	rows.sort(key=lambda entry: (entry["sent_on"] or "", entry["name"]))
	return _with_photo_urls(rows), capped, lane_counts


def _holds_routed_role(user=None):
	"""Whether the caller works a role a category may route work to, other
	than HR Manager -- `_is_hr` already covers HR Manager, and this is the
	other half of P5-R11's "is HR or holds a routed role" gate."""
	return any(role != "HR Manager" for role in _routed_roles_held(user))


def _routed_roles_held(user=None):
	"""The routed roles ``user`` works: HR Manager and each routed worker
	role (IT Team) they hold in Frappe *and* reach some org with by
	assignment (plan 2026-10-19-001 U3, R3). A Frappe role with no
	assignment works nothing; a System Manager works only the routed roles
	it holds, as before."""
	user = user or frappe.session.user
	roles = set(frappe.get_roles(user))
	return [
		role
		for role in ("HR Manager", *sorted(_ROUTED_WORKER_ROLES))
		if role in roles and role_scope(user, role)["kind"] != "none"
	]


def _can_edit_email_templates(user=None):
	"""Whether the caller owns every portal email -- the shared theme, the
	message templates and the celebration / holiday mail. These are global
	settings, so plan 2026-10-19-001 R7 needs All-orgs authority
	(`has_all_orgs_authority`): an All-orgs Portal Admin, Manage access, or
	System Manager. A named-org Portal Admin, the HR roles and the
	Notification Manager are refused. The one gate for the whole page, reads
	included: it is one global editor, not a per-org view."""
	return has_all_orgs_authority(user or frappe.session.user)


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
	rows, _capped, _lane_counts = _approval_summaries(employee)
	for row in rows:
		# Plan 2026-10-31-001 KTD15: "With managers" is not a decision owed.
		if not _owed(row):
			continue
		title = _QUEUE_TITLE[row["kind"]](row)
		if row.get("hr_reason"):
			# Plan 2026-10-31-001 R22: a manager-stage leave HR may decide
			# (approver away, or overdue) is still waiting on its approver,
			# not on HR -- it used to read "waiting for HR" here.
			approver = row.get("approver_name")
			waiting = f"waiting for {approver}" if approver else "no approver set"
			title = f"{title} · {waiting}"
		elif row["for_hr"]:
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
	# Plan 2026-10-17-001 HR-04 / AI-03: the subject tells five rows apart, and
	# the possessive drops the "a IT" article. Fields already on the row.
	"request": lambda row: f"{row['employee_name']}'s {row['category']} request: {row['subject']}",
	"change": lambda row: f"{row['employee_name']} asked to change an approved week",
	# Plan 2026-10-31-001 U9: the submitting manager, and how many people.
	"roster": lambda row: f"{row['employee_name']} asked to change shifts for {_people_count(row['people_count'])}",
}


def _people_count(count):
	return f"{count} {'person' if count == 1 else 'people'}"


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
	decided_by = row.get("helixhr_decided_by")
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
		# Plan 2026-10-31-001 U11 (R25): who made the final decision, from the
		# stamp alone. A record decided before the stamp existed names nobody
		# rather than guessing the approver.
		"decided_by_name": approver_names.get(decided_by) if decided_by else None,
		"decided_by_hr": bool(cint(row.get("helixhr_decided_as_hr"))),
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
	"helixhr_decided_by",
	"helixhr_decided_as_hr",
	"posting_date",
	"owner",
	"creation",
	"modified",
]


def _approver_names(rows):
	"""Display names for the approver and the decider of each row, in one
	User read."""
	ids = {
		row.get(field) for row in rows for field in ("leave_approver", "helixhr_decided_by") if row.get(field)
	}
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

	# The caller's own Employee, resolved above, is the gate. HRMS's
	# `get_leave_details` also lists Leave Types through `get_list`, and a
	# routed worker who is a Website User without the Employee role (IT Team,
	# P5-KTD10) has no read on Leave Type -- the whole page was a 403 for it.
	# Same posture as `get_person`'s balances section.
	with as_administrator():
		balances = _leave_balances(employee)

	return {
		"balances": balances,
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
def get_my_week(week_start=None, include_non_working=0):
	"""The one Timesheet for the Monday..Sunday week containing
	`week_start` (any date in that week; defaults to today), or None if
	the employee hasn't started one yet (KTD10: one week is one
	Timesheet -- the newest non-cancelled one for that Monday).

	`include_non_working` adds `non_working` (U9 / KTD9), which only "Copy
	last week" reads; every other load skips its reads."""
	employee = get_current_employee()
	monday, sunday = get_week_bounds(week_start or user_today())

	current = _week_timesheet(employee, monday, ("name",), sunday)

	timesheet = None
	if current:
		doc = frappe.get_doc("Timesheet", current.name)
		# U9 / KTD9: "Copy last week" names what it could not copy, so a row
		# carries its project's and task's words, not only their ids. One
		# query for the week's tasks; a task the caller can no longer read --
		# one outside the projects they belong to (`_my_project_names`, the
		# rule HelixHR reads Task by; Frappe's Task permission answers no for
		# a plain employee) -- falls back to its id, so a hand-written task id
		# on a draft row cannot read another project's task subject.
		task_ids = {row.task for row in doc.time_logs if row.task}
		member_of = _my_project_names(frappe.session.user) if task_ids else set()
		subjects = (
			dict(
				frappe.get_all(
					"Task",
					filters={"name": ["in", list(task_ids)], "project": ["in", list(member_of)]},
					fields=["name", "subject"],
					as_list=True,
				)
			)
			if member_of
			else {}
		)
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
					"project_name": row.project_name or row.project,
					"task": row.task,
					"task_subject": subjects.get(row.task) or row.task,
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

	result = {
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
	if cint(include_non_working):
		# U9 / KTD9: `{date: bool}` for all seven days -- True on a holiday,
		# approved leave, or a day outside joining..relieving. "Copy last
		# week" reads it to keep hours off days the employee is not expected
		# to work.
		result["non_working"] = _week_non_working(employee, monday, sunday)
	return result


def _week_non_working(employee, monday, sunday):
	"""`{date: True}` for each day of the week the employee is not expected
	at work, `False` for the rest (U9 / KTD9).

	The working days are `_working_days_index` (holiday list + approved
	leave) clamped to the joining..relieving span exactly as `get_my_month`
	clamps them. A half-day leave is a day off here because it is one in
	the index the approval flags (`hours_off`, `missing_day`) read: a copy
	that put a full day's hours on it would raise the very flag that keeps
	the week out of "Approve all". The employee adds the half day by hand.
	"""
	index = _working_days_index([employee], monday, sunday)
	working = _within_employment(
		_employee_working_days(index, employee, monday, sunday),
		_employment_span(employee),
	)
	return {
		str(add_days(monday, offset)): str(add_days(monday, offset)) not in working for offset in range(7)
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

	span = _employment_span(employee)

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
		days = _within_employment(_employee_working_days(index, employee, monday, sunday), span)
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
	"""Projects `user` may book on: a Project Users row, a User Permission
	on Project, or (plan 2026-10-31-001 KTD6) an Open project marked "Open
	to everyone" in the company of `user`'s current Active Employee. The one
	booking rule the task picker, week reads and save/submit validation
	share (R9). The open branch writes no row anywhere, so it widens booking
	only, never access to the project.

	Plan 2026-11-01-001 U2 (R3): an Open, flagged project also counts when
	`user`'s company is one of its extra open companies
	(`helixhr_open_companies`), in one more indexed query."""
	names = set(frappe.get_all("Project User", filters={"user": user}, pluck="parent")) | set(
		frappe.get_all("User Permission", filters={"user": user, "allow": "Project"}, pluck="for_value")
	)
	company = active_employee_for_user(user).company
	if company:
		names |= set(
			frappe.get_all(
				"Project",
				filters={"company": company, "status": "Open", "helixhr_open_to_all": 1},
				pluck="name",
			)
		)
		project, row = frappe.qb.DocType("Project"), frappe.qb.DocType(_OPEN_COMPANY_DOCTYPE)
		names |= set(
			frappe.qb.from_(row)
			.join(project)
			.on(project.name == row.parent)
			.select(project.name)
			.where(
				(row.parenttype == "Project")
				& (row.parentfield == _OPEN_COMPANIES_FIELD)
				& (row.company == company)
				& (project.status == "Open")
				& (project.helixhr_open_to_all == 1)
			)
			.run(pluck=True)
		)
	return names


@frappe.whitelist()
def get_my_projects():
	"""Open Projects the session user may book time on (`_my_project_names`:
	Project Users, a User Permission on Project, or an "Open to everyone"
	project in their company), each with its own open Tasks
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


def _change_request_summaries(employee, today, count_only=False):
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
		fields=["name"]
		if count_only
		else ["name", "employee", "employee_name", "timesheet", "week_start", "comment", "creation"],
		order_by="creation asc",
		limit=_QUEUE_FETCH,
	)
	if count_only:
		return _count_rows("change", rows)
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


def _hr_change_request_summaries(employee, today, count_only=False):
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
		fields=["name"]
		if count_only
		else [
			"name",
			"employee",
			"employee_name",
			"timesheet",
			"week_start",
			"comment",
			"approver_user",
			"creation",
		],
		order_by="creation asc",
		limit=_QUEUE_FETCH,
	)
	if count_only:
		return _count_rows("change", rows)
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
_DECIDED_DAYS = 14
_DECIDED_LIMIT = 25
# A search of Decided is a history, not a receipt: no date window, so it is
# bounded by rows instead -- per collector and in total.
_DECIDED_SEARCH_LIMIT = 50
# What a handed-over leave reads as in the receipt list. Leave has no workflow
# state of its own (P2-KTD17), so this is the one place the word lives -- the
# same word `TIMESHEET_PENDING_HR` and `REQUEST_PENDING_HR` already carry, so
# the three kinds report one state under one name.
_LEAVE_PENDING_HR = "Pending HR"


# U6 / R13. The kinds a queue row can be, in the order the chips render.
# Plan 2026-10-16-001 KTD1: requests leave Approvals.
# Plan 2026-10-31-001 U9: roster requests are HR's, in the HR lane.
_APPROVAL_FILTER_KINDS = ("leave", "timesheet", "attendance", "change", "roster")


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
def get_my_approvals(kind=None, category=None, q=None):
	"""The manager's queue: everything waiting on them, oldest first, plus
	the handful of decisions they made this week (P2-U7 step 1).

	Summary only. The evidence -- timesheet rows and day totals, a leave's
	reason -- costs a document read per item, so it is loaded by
	`get_approval_detail` for the one item actually selected (P2-R22).

	U6 / R13: `kind` and `category` narrow the page; `counts` is always the
	caller's whole queue, so every chip says what choosing it would show.
	A category implies kind "request" -- only requests have one.

	`q` (2+ characters, `_request_search_pattern`'s rules) searches: pending
	narrows to rows whose person, leave type or reason, kind or record name
	contains it -- `counts` stays the whole queue, as above -- and `decided`
	becomes an all-time history of this caller's own decisions matching it
	(`_recently_decided`), timesheets included. `searched` says which answer
	this is; a shorter `q` is no search at all.
	"""
	rate_limit_per_user("get_my_approvals")
	pattern = _request_search_pattern(q)
	if kind and kind not in _APPROVAL_FILTER_KINDS and kind != "request":
		frappe.throw(_("There is no approval kind called {0}.").format(kind))
	category = _valid_request_category(category)
	if category:
		kind = "request"
	employee = _my_employee()
	# Plan 2026-10-16-001 KTD1: requests leave Approvals rows, lanes, counts,
	# categories and Decided list.
	pending, capped, _lane_counts = _approval_summaries(employee, include_requests=False)
	# Plan 2026-10-31-001 KTD15: the counts and `total` are owed work only, so
	# the badge `total` sets is the bootstrap's; "With managers" rows are
	# still in `pending`, for the screen to group.
	owed = [row for row in pending if _owed(row)]
	lanes = {"team": sum(1 for row in owed if row.get("lane") == "team")}
	if _is_hr():
		lanes["hr"] = sum(1 for row in owed if row.get("lane") == "hr")
	counts = {
		"kinds": [
			{"name": name, "count": sum(1 for row in owed if row["kind"] == name)}
			for name in _APPROVAL_FILTER_KINDS
		],
		"categories": [],
		"lanes": lanes,
	}
	if kind:
		pending = [row for row in pending if row["kind"] == kind]
	if category:
		pending = [row for row in pending if row.get("category") == category]
	if pattern:
		term = q.strip()[:_REQUEST_SEARCH_MAX].lower()
		pending = [row for row in pending if _approval_row_matches(row, term)]
	# Plan 2026-10-04-003 KTD8 / R19: the 25-row page is gone for the kinds
	# the redesign groups per person (timesheet, leave, and the mixed view
	# those live in); attendance keeps theirs. The hard cap stands behind all of it.
	shown = pending[:_APPROVAL_PAGE] if kind == "attendance" else pending[:_QUEUE_HARD_CAP]
	_mark_blocked_rows(shown)
	decided = _recently_decided(employee, pattern)

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
		"total": sum(1 for row in pending if _owed(row)),
		"counts": counts,
		"people": people,
		# `total` is what came back, and every kind's read is bounded, so on a
		# very large backlog it is a floor and not a count. The flag is what
		# lets the screen say "50+" rather than lie about 50; a real COUNT per
		# kind on every poll is the thing being avoided (P2-R22, P3-R25).
		"total_is_capped": capped or len(pending) > len(shown),
		"decided": decided,
		"searched": bool(pattern),
		# A search's history is bounded by rows, not by date: at the bound
		# there may be older matches, so the screen says "50+", not "50".
		"decided_is_capped": bool(pattern) and len(decided) >= _DECIDED_SEARCH_LIMIT,
	}


def _approval_row_matches(row, term):
	"""Whether a pending row answers the search `term` (already trimmed and
	lower-cased): a case-insensitive substring of the person, the leave type
	or attendance reason or the record name, or a prefix of the kind. Only a
	prefix for the kind: as a substring, "an" or "tim" typed toward a
	person's name would match every attendance or timesheet row."""
	return str(row.get("kind") or "").startswith(term) or any(
		term in str(row.get(field) or "").lower() for field in ("employee_name", "leave_type", "reason", "name")
	)


def _decided_window(since, pattern, date_field="modified", fields=("employee_name", "name")):
	"""One decided collector's extra `filters`, `or_filters` and `limit`.

	With `since`, the receipt: rows decided since then, `_DECIDED_LIMIT` of
	them. With `since=None`, the search: no date window, narrowed to rows
	whose `fields` match the LIKE `pattern`, `_DECIDED_SEARCH_LIMIT` of them.
	The caller's own scoping filters are untouched either way, so a search
	can only narrow what the receipt would read."""
	filters = {date_field: [">=", str(since)]} if since else {}
	or_filters = {field: ["like", pattern] for field in fields} if pattern else None
	return filters, or_filters, _DECIDED_LIMIT if since else _DECIDED_SEARCH_LIMIT


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


def _decided_leave(employee, since, pattern=None):
	"""What this approver decided, plus what they handed to HR.

	The handover row is the same receipt a timesheet and an attendance
	request have had since P3-KTD7: a manager who escalates has finished
	with the record and needs to see that their step happened. It was the one
	kind of the three that reported nothing at all.
	"""
	window, or_filters, limit = _decided_window(since, pattern, fields=("employee_name", "name", "leave_type"))
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
					**window,
				},
				or_filters=or_filters,
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
				limit=limit,
			)
		]

	# Plan 2026-10-31-001 U11 (R27): a final decision belongs to whoever the
	# stamp names, so HR deciding a manager-stage leave lands in HR's list and
	# not the manager's. `leave_approver` answers only for a row with no stamp:
	# a send-back (a save, never stamped) and anything decided before the
	# stamp existed. Two reads because `or_filters` already carries the search.
	def decided(filters):
		return frappe.get_list(
			"Leave Application",
			filters={
				**filters,
				"employee": ["!=", employee],
				"status": ["in", ["Approved", "Rejected"]],
				**window,
			},
			or_filters=or_filters,
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
			limit=limit,
		)

	user = frappe.session.user
	rows = decided({"helixhr_decided_by": user}) + decided(
		{"helixhr_decided_by": ["is", "not set"], "leave_approver": user}
	)
	rows.sort(key=lambda row: row.modified, reverse=True)
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
		for row in rows[:limit]
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


def _decided_timesheets(employee, since, pattern=None):
	"""Plan 2026-10-04-003 KTD2: a cancelled week reads "Cancelled" here.

	The Cancelled workflow state (docstatus 2) joins the receipt list, and
	every docstatus-2 row reads Cancelled regardless of its stored state --
	a week cancelled in Desk before the state existed keeps
	`workflow_state = "Approved"`, and an approver must never see that
	row as an approval."""
	window, or_filters, limit = _decided_window(since, pattern)
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
				**window,
				# A search lists timesheets for every caller, and a line
				# manager's nested-set read reaches weeks other approvers
				# decided, so "Decided by me" keys on the decider (the last
				# writer) for everyone there, as `_decided_by_me` does for HR.
				**({"modified_by": frappe.session.user} if pattern else _decided_by_me()),
			},
			or_filters=or_filters,
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
			limit=limit,
		)
	]


def _decided_attendance_requests(employee, since, pattern=None):
	"""A request the manager sent on reads "Waiting for HR" here, which is
	the honest receipt: their step is done and HR's has not happened yet
	(P3-KTD7). Approved, Sent Back and Rejected are the later outcomes of the
	same row. See `_decided_hr_handover_states` for why HR's own queue does
	not appear in HR's receipt.
	"""
	window, or_filters, limit = _decided_window(since, pattern)
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
				**window,
				**_decided_by_me(),
			},
			or_filters=or_filters,
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
			limit=limit,
		)
	]


def _decided_change_requests(employee, since, pattern=None):
	"""Plan 2026-10-15-002 R1/KTD8: change requests this caller accepted or
	declined. `decided_by` is stamped by the decision itself, so it is the
	scope; `frappe.get_all` because role Employee has no DocPerm here (see
	`_change_request_summaries`)."""
	window, or_filters, limit = _decided_window(since, pattern, date_field="decided_on")
	return [
		_decided_row(
			"change",
			row.name,
			row.employee_name,
			"Timesheet change",
			row.week_start,
			add_days(row.week_start, 6),
			row.status,
			row.decided_on,
		)
		for row in frappe.get_all(
			"HelixHR Timesheet Change",
			filters={
				"decided_by": frappe.session.user,
				"employee": ["!=", employee],
				"status": ["in", ["Accepted", "Declined"]],
				**window,
			},
			or_filters=or_filters,
			fields=["name", "employee_name", "week_start", "status", "decided_on"],
			order_by="decided_on desc",
			limit=limit,
		)
	]


def _decided_routed_requests(employee, since):
	"""Plan 2026-10-15-002 R1/KTD8: routed requests this caller picked up and
	closed. The picker is the decider (`picked_up_by` is stamped on pick-up),
	so it scopes the read; `frappe.get_all` because a routed-role holder may
	have no DocPerm on HR Request at all."""
	rows = frappe.get_all(
		"HR Request",
		filters={
			"picked_up_by": frappe.session.user,
			"employee": ["!=", employee],
			"status": ["in", ["Done", "Rejected"]],
			"closed_on": [">=", str(since)],
		},
		fields=["name", "employee", "subject", "status", "closed_on"],
		order_by="closed_on desc",
		limit=_DECIDED_LIMIT,
	)
	names = dict(
		frappe.get_all(
			"Employee",
			filters={"name": ["in", list({row.employee for row in rows})]},
			fields=["name", "employee_name"],
			as_list=True,
		)
	) if rows else {}
	return [
		_decided_row(
			"request",
			row.name,
			names.get(row.employee) or row.employee,
			row.subject,
			None,
			None,
			row.status,
			row.closed_on,
		)
		for row in rows
	]


# Workflow kinds read through `frappe.get_list` and need role Employee; the
# change and routed kinds are scoped by their own decider stamp and run for
# every caller (a routed-role holder may have no Employee role, P5-KTD10).
# Plan 2026-10-16-001 KTD1: drops _decided_routed_requests from Decided.
_DECIDED_COLLECTORS = (_decided_leave, _decided_attendance_requests)
_DECIDED_STAMPED_COLLECTORS = (_decided_change_requests,)


def _recently_decided(employee, pattern=None):
	"""What this approver decided in the last week, newest first.

	With a search `pattern` (`get_my_approvals`' `q`), the same collectors
	plus timesheets run with no date window, narrowed to records whose
	person or name matches -- an all-time history of this caller's own
	decisions, `_DECIDED_SEARCH_LIMIT` rows at most. The same scoping
	(`_decided_by_me`, the HR-handover rule, the decider stamps) applies,
	so it never lists a record the receipt could not.

	Best effort by design. A Timesheet's DocShare is removed the moment it
	is decided (P2-U7 scenario 8), and an Attendance Request's the moment it
	leaves Pending Manager, so a decided record is only still visible to a
	manager who can read it some other way -- the nested-set User Permission
	over their own reports. An approver who is nobody's manager sees their
	leave decisions here and nothing else, which is correct: the group is a
	receipt for work this user did, not a record they own.
	"""
	since = None if pattern else add_days(user_today(), -_DECIDED_DAYS)
	# Timesheets left the receipt (plan 2026-10-15-002 KTD8: they have their
	# own tab), but a search of history has to reach them.
	collectors = (*_DECIDED_COLLECTORS, _decided_timesheets) if pattern else _DECIDED_COLLECTORS
	search = {"pattern": pattern} if pattern else {}
	today = _as_date(user_today())
	decided = []
	# P5-U11: the same gate `_approval_summaries` needed for its line-manager
	# loop. A routed-role holder with no `Employee` role (IT Team, P5-KTD10)
	# has no DocPerm row on Leave Application, Timesheet or Attendance
	# Request at all, so `frappe.get_list` inside these collectors answers a
	# hard PermissionError rather than an empty list -- first reachable here
	# once `get_my_approvals` served a caller of that shape (P5-U11 is that
	# caller; their routed and change decisions come from
	# `_DECIDED_STAMPED_COLLECTORS`, which need no DocPerm).
	if "Employee" in frappe.get_roles():
		for collect in collectors:
			decided.extend(collect(employee, since, **search))
	for collect in _DECIDED_STAMPED_COLLECTORS:
		decided.extend(collect(employee, since, **search))

	decided.sort(key=lambda entry: entry["decided_on"] or "", reverse=True)
	for entry in decided:
		entry["age_days"] = _age_in_days(entry["decided_on"], today)
	return _with_photo_urls(decided[: _DECIDED_SEARCH_LIMIT if pattern else _DECIDED_LIMIT])


def _oversight_scope(user=None):
	"""Plan 2026-10-15-002 KTD4: who may see pending work by approver, and
	where. ``{"hr_manager", "companies"}`` or None for anyone else.

	- HR Manager (`_is_hr`): their admin scope -- one company, or ``None``
	  companies (every company) for the unscoped Desk persona. An HR Manager
	  whose own Employee is not Active resolves to nobody (`resolve_admin_scope`
	  "none") and is refused, never widened.
	- HR User: the orgs their HR User assignments name (`role_scope`, plan
	  2026-10-19-001 U3), timesheets only; ``None`` companies on All orgs.

	Someone with both gets the union (R3): ``companies`` merges the two org
	lists (``None`` when either is every company), and ``hr_manager`` is True
	whenever they have any HR Manager scope. The shape has one company list,
	so an HR Manager on A who is HR User on B also lists B's non-timesheet
	pending items; deciding any of them is still gated per record
	(`_assert_may_act_on`, `_decides_as_hr`), which holds HR to A.

	Company access grants never widen this: each company's HR chases its own
	managers (plan scope boundary)."""
	user = user or frappe.session.user
	hr = read_companies(resolve_admin_scope(user)) if _is_hr(user) else []
	hr_manager = hr is None or bool(hr)
	scopes = [hr]
	if HR_USER_ROLE in frappe.get_roles(user):
		scopes.append(read_companies(role_scope(user, HR_USER_ROLE)))
	if any(companies is None for companies in scopes):
		return {"hr_manager": hr_manager, "companies": None}
	companies = list(dict.fromkeys(name for found in scopes for name in found))
	return {"hr_manager": hr_manager, "companies": companies} if companies else None


def _can_oversee_timesheets(user=None):
	"""The bootstrap's `can_oversee_timesheets` (U4): `_oversight_scope`'s
	own gate, so the nav item and the endpoints can never disagree."""
	return _oversight_scope(user) is not None


def _can_see_all_pending(user=None):
	"""The bootstrap's `can_see_all_pending`: the gate
	`get_pending_by_approver` applies to the non-timesheet kinds."""
	scope = _oversight_scope(user)
	return bool(scope and scope["hr_manager"])


def _pending_groups(scope, kinds, overdue_only):
	"""Pending items in `scope`, grouped by approver (KTD3), oldest wait
	first, plus whether a read hit the bound. Groups key on the approver's
	user, then their Employee, then a role, then one no-approver bucket."""
	stats = {}
	cutoff = None if overdue_only else reminders.ALL_PENDING
	items = reminders.collect_overdue(
		getdate(user_today()),
		cutoff=cutoff,
		companies=scope["companies"],
		kinds=kinds,
		resolve_owners=False,
		stats=stats,
	)
	reminders.resolve_approvers(items)
	# Plan 2026-10-19-001 U9 (R27): an HR Manager decides any of these from the
	# row, through the same panel and `_allowed_actions` as their own queue --
	# except their own items. HR User oversight stays read-only.
	own_employee = active_employee_for_user(frappe.session.user).name

	groups = {}
	for item in items:
		if item["approver_role"]:
			key = f"role:{item['approver_role']}"
		elif item["approver_user"]:
			key = f"user:{item['approver_user']}"
		elif item["approver_employee"]:
			key = f"employee:{item['approver_employee']}"
		else:
			key = "none"
		group = groups.setdefault(
			key,
			{
				"key": key,
				"approver_user": item["approver_user"],
				"approver_employee": item["approver_employee"],
				"approver_name": item["approver_name"] or _("No approver"),
				"approver_role": item["approver_role"],
				"reason": None if item["approver_role"] else item["reason"],
				"is_you": bool(item["approver_user"]) and item["approver_user"] == frappe.session.user,
				"companies": set(),
				"items": [],
			},
		)
		if item["route_kind"] == "timesheet":
			group["companies"].add(item["company"])
		group["items"].append(
			{
				"kind": item["kind"],
				"route_kind": item["route_kind"],
				"doctype": _APPROVAL_DOCTYPES[item["route_kind"]],
				"name": item["name"],
				"is_own": item["employee"] == own_employee,
				"can_decide": bool(scope["hr_manager"])
				and item["employee"] != own_employee
				# A roster request is the HR Manager role's of its own company.
				and (item["route_kind"] != "roster" or _may_decide_roster_for(frappe.session.user, item["company"])),
				"title": item["title"],
				"employee": item["employee"],
				"employee_name": item["employee_name"],
				"date": item["date"],
				"pending_since": item["pending_since"],
				# R6: a row with no `helixhr_pending_since` is "since unknown",
				# never counted as 0 days.
				"age_days": item["age_days"],
				"since_unknown": item["age_days"] is None,
				"threshold_days": item["threshold_days"],
				"overdue": item["overdue"],
			}
		)

	result = []
	for group in groups.values():
		ages = [row["age_days"] for row in group["items"] if row["age_days"] is not None]
		group["count"] = len(group["items"])
		group["oldest_days"] = max(ages) if ages else None
		# R9/R11/R12: timesheets only, a person (never a role), reason none.
		companies_of = sorted(company for company in group.pop("companies") if company)
		remindable = (
			bool(companies_of)
			and not group["approver_role"]
			and group["reason"] == reminders.APPROVER_OK
		)
		waits = [reminders.next_reminder_at(group["approver_user"], company) for company in companies_of] if remindable else []
		# Blocked only while every company's slot is taken; then the earliest frees it.
		blocked = bool(waits) and all(waits)
		group["remindable"] = remindable and not blocked
		group["next_reminder_at"] = str(min(waits)) if blocked else None
		result.append(group)
	result.sort(key=lambda group: (-(group["oldest_days"] if group["oldest_days"] is not None else -1), group["approver_name"]))
	return result, bool(stats.get("capped"))


# Plan 2026-10-16-001 KTD1: requests leave All pending oversight.
# Plan 2026-10-31-001 U9: a Pending roster request waits on HR, grouped under
# the HR Manager role -- the shared queue, never one named approver.
_OVERSIGHT_KINDS = ("leave", "attendance", "change", "roster")


@frappe.whitelist()
def get_pending_by_approver(kind=None, overdue_only=0):
	"""Plan 2026-10-15-002 U2/KTD2: every pending item in the caller's
	company, grouped by approver.

	``kind="timesheet"`` is the Timesheets tab's "All pending timesheets"
	(HR Manager and HR User). Any other kind, or none, is the All pending
	tab's non-timesheet kinds -- HR Manager only. ``overdue_only=1`` keeps
	only late items, which reproduces the old Overdue tab."""
	rate_limit_per_user("get_pending_by_approver")
	scope = _oversight_scope()
	if not scope:
		frappe.throw(_("Only HR can see pending approvals by approver."), frappe.PermissionError)
	if kind not in (None, "", "timesheet", *_OVERSIGHT_KINDS):
		frappe.throw(_("Not a valid kind."))
	if kind != "timesheet" and not scope["hr_manager"]:
		frappe.throw(_("You can only see pending timesheets."), frappe.PermissionError)

	kinds = [kind] if kind else list(_OVERSIGHT_KINDS)
	groups, capped = _pending_groups(scope, kinds, cint(overdue_only))
	response = {
		"groups": groups,
		"count": sum(group["count"] for group in groups),
		"total_is_capped": capped,
	}
	if kind != "timesheet":
		# R8: the All pending header links to the Timesheets tab with this count.
		company_filter = {"company": ["in", scope["companies"]]} if scope["companies"] is not None else {}
		response["timesheet_count"] = frappe.db.count(
			"Timesheet",
			{"docstatus": 0, "workflow_state": ["in", ("Pending Approval", TIMESHEET_PENDING_HR)], **company_filter},
		)
	return response


@frappe.whitelist()
def get_overdue_approvals():
	"""U12 / R26. HR's late items, every kind, grouped by who owes the
	decision, within the caller's admin scope (P6-R6).

	Plan 2026-10-15-002 KTD2: a thin wrapper over the All pending tab's
	collector with the overdue filter on, so there is one collector, not two.
	Kept for its dotted path; the page now reads `get_pending_by_approver`.
	Groups keep the digest summary's `owner_name` / `inactive` keys."""
	rate_limit_per_user("get_overdue_approvals")
	if not _is_hr():
		frappe.throw(_("Only HR can see overdue approvals."), frappe.PermissionError)
	scope = _oversight_scope()
	if not scope:
		return {"groups": [], "count": 0}
	groups, _capped = _pending_groups(scope, list(reminders.PENDING_KINDS), True)
	for group in groups:
		group["owner_name"] = f"{group['approver_name']} (you)" if group["is_you"] else group["approver_name"]
		group["inactive"] = group["reason"] not in (None, reminders.APPROVER_OK)
	return {"groups": groups, "count": sum(group["count"] for group in groups)}


_REMINDER_REASON_TEXT = {
	reminders.APPROVER_INACTIVE: "This approver has left. Set a new Reports to in Settings → Approvers.",
	reminders.APPROVER_NO_LOGIN: "This approver has no portal login. Fix their user in Settings → Approvers.",
	reminders.APPROVER_UNMAILABLE: "This approver has no email address. Fix their user in Settings → Approvers.",
	reminders.APPROVER_NO_APPROVER: "These timesheets have no approver. Set Reports to in Settings → Approvers.",
}
_REMINDER_NOT_FOUND = "No pending timesheets wait on this approver in your company."
_REMINDER_ERROR_TITLE = "HelixHR timesheet reminder"


@frappe.whitelist(methods=["POST"])
def send_timesheet_reminder(approver_user):
	"""Plan 2026-10-15-002 U3: one batched reminder to an approver about all
	their pending timesheets in the caller's company -- one mail
	(`timesheet_approval_reminder`), one bell, an Info comment per timesheet.

	The server rebuilds the list (KTD5): only rows whose resolved approver is
	``approver_user`` with reason "none" are sent; a blocking reason refuses.
	At most one reminder per (approver, company) per 24h, whoever sends it,
	claimed atomically before sending (KTD6). Returns ``sent`` and
	``next_reminder_at``; ``sent`` is False only when every company's slot
	was already taken."""
	rate_limit_per_user("send_timesheet_reminder")
	if not isinstance(approver_user, str) or not approver_user.strip():
		frappe.throw(_("Choose an approver."))
	approver_user = approver_user.strip()
	scope = _oversight_scope()
	if not scope:
		frappe.throw(_("Only HR can send reminders."), frappe.PermissionError)

	items = reminders.collect_overdue(
		getdate(user_today()),
		cutoff=reminders.ALL_PENDING,
		companies=scope["companies"],
		kinds=["timesheet"],
		resolve_owners=False,
	)
	mine = [
		item
		for item in reminders.resolve_approvers(items)
		if item["approver_user"] == approver_user and not item["approver_role"]
	]
	if not mine:
		frappe.throw(_(_REMINDER_NOT_FOUND), frappe.PermissionError)
	blocking = next((item["reason"] for item in mine if item["reason"] != reminders.APPROVER_OK), None)
	if blocking:
		frappe.throw(_(_REMINDER_REASON_TEXT[blocking]))

	by_company = {}
	for item in mine:
		by_company.setdefault(item["company"], []).append(item)

	sender = frappe.utils.get_fullname(frappe.session.user)
	sent, waits = [], []
	# Every slot this request claimed goes back if anything after the claim
	# fails: the request's writes (mail queue, bell, comments) roll back with
	# it, so a held slot would block a reminder that never went.
	claimed = []
	try:
		for company, listed in sorted(by_company.items()):
			if not reminders.claim_reminder(approver_user, company):
				waits.append(reminders.next_reminder_at(approver_user, company))
				continue
			claimed.append(company)
			queued = send_notification(
				"timesheet_approval_reminder",
				[approver_user],
				{
					"items": [
						{
							"employee_name": item["employee_name"],
							"week": frappe.utils.format_date(item["date"]) if item["date"] else "",
							"age_days": item["age_days"],
							"url": item["url"],
						}
						for item in listed
					],
					"count": len(listed),
					"company": company,
					"sender_name": sender,
				},
			)
			if not queued:
				# Switched off in Email templates, or the mail failed (logged by
				# `send_notification`): nothing went, so the slot goes back.
				reminders.release_reminder(approver_user, company)
				claimed.remove(company)
				continue
			# KTD7: the bell is its own write, pointing at the oldest timesheet.
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"for_user": approver_user,
					"from_user": frappe.session.user,
					"type": "Alert",
					"document_type": "Timesheet",
					"document_name": listed[0]["name"],
					"subject": _("{0} timesheet(s) are waiting for your approval").format(len(listed)),
				}
			).insert(ignore_permissions=True)
			# R13: the timesheet's own history shows it was chased.
			note = _("Approval reminder sent to {0} by {1}.").format(listed[0]["approver_name"], sender)
			for item in listed:
				try:
					frappe.get_doc(
						{
							"doctype": "Comment",
							"comment_type": "Info",
							"reference_doctype": "Timesheet",
							"reference_name": item["name"],
							"content": note,
						}
					).insert(ignore_permissions=True)
				except Exception:
					frappe.log_error(frappe.get_traceback(), _REMINDER_ERROR_TITLE)
			sent.append(company)
			waits.append(reminders.next_reminder_at(approver_user, company))
	except Exception:
		for company in claimed:
			reminders.release_reminder(approver_user, company)
		raise

	next_at = max((wait for wait in waits if wait), default=None)
	if sent:
		return {
			"sent": True,
			"message": _("Reminder sent to {0}.").format(mine[0]["approver_name"]),
			"next_reminder_at": str(next_at) if next_at else None,
		}
	if not any(waits):
		frappe.throw(_("The reminder mail is switched off or could not be sent. Nothing was sent."))
	return {
		"sent": False,
		"message": _("A reminder already went to {0} in the last 24 hours.").format(mine[0]["approver_name"]),
		"next_reminder_at": str(min(wait for wait in waits if wait)),
	}


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

	# Plan 2026-10-15-002 KTD4: an HR User reads a pending timesheet in their
	# assigned orgs (every org on All orgs), read-only. Decided from the row's
	# columns before the document is loaded; any other kind or company falls
	# through to the approver rule below and its not-found refusal. An HR
	# Manager who is HR User elsewhere reads those orgs' the same way (R3
	# union); in their HR scope they decide, below.
	scope = _oversight_scope() if kind == "timesheet" else None
	oversees = bool(scope) and bool(
		frappe.db.exists(
			"Timesheet",
			{
				"name": name,
				"docstatus": 0,
				**({"company": ["in", scope["companies"]]} if scope["companies"] is not None else {}),
				"workflow_state": ["in", ("Pending Approval", TIMESHEET_PENDING_HR)],
			},
		)
	)

	doc = frappe.get_doc(doctype, name)
	if (
		oversees
		and get_manager_user(doc.employee) != frappe.session.user
		and not (_is_hr() and employee_in_admin_scope(doc.employee, resolve_admin_scope(frappe.session.user)))
	):
		detail = _APPROVAL_KINDS[doctype]["detail"](doc)
		detail["actions"] = []
		detail["read_only"] = True
		return detail

	_assert_may_act_on(doc)
	detail = _APPROVAL_KINDS[doctype]["detail"](doc)
	# Plan 2026-10-18-001 R10: the approver's own record, which their account
	# cannot change -- said in words, with no buttons the act would refuse.
	problem = _approver_access_problem(doc, frappe.session.user)
	if problem:
		detail["actions"] = []
		detail["read_only"] = True
		detail["read_only_reason"] = problem
		return detail
	# P4-R1 / P4-KTD6: the outcomes that are legal for *this* record and
	# *this* approver, from the one rule table `act_on_approval` validates
	# against. The screen draws exactly this list, so it can never offer a
	# button the server would refuse.
	detail["actions"] = _allowed_actions(doc, frappe.session.user)
	return detail


# The two kinds a line manager decides through the record's own Workflow,
# which runs Frappe's write/submit check on the record as the manager -- and
# the one state each is the manager's to decide in.
_LINE_MANAGER_WORKFLOW_KINDS = {"Timesheet": PENDING_STATE, "Attendance Request": REQUEST_PENDING_MANAGER}


def _approver_access_problem(doc, user):
	"""Why a line manager who owns this decision still cannot make it, in
	words; None when nothing is missing (plan 2026-10-18-001 R10).

	`apply_workflow` needs the Employee role (the manager transitions'
	`allowed`) and Frappe write and submit on the record -- a DocShare, or
	the manager's own User Permission reaching their report. Without one of
	them the record was listed, opened and then refused with a raw
	PermissionError on the click. HR inside its scope acts through its own
	DocPerms, and Leave and the change request write with
	`ignore_permissions`, so only the two workflow kinds are checked, and only
	in the manager's own state: past it the share is gone by design and the
	empty action list is the honest answer.
	"""
	manager_state = _LINE_MANAGER_WORKFLOW_KINDS.get(doc.doctype)
	if not manager_state or doc.get("workflow_state") != manager_state or _decides_as_hr(doc, user):
		return None
	if "Employee" not in frappe.get_roles(user):
		return _("You can see this, but your account isn't set up to decide it yet. Ask HR to check your access.")
	if not all(frappe.has_permission(doc.doctype, ptype, doc=doc, user=user) for ptype in ("write", "submit")):
		return _(
			"You can see this, but your account can't change {0}'s records yet. Ask HR to check your access."
		).format(doc.employee_name)
	return None


def _mark_blocked_rows(rows):
	"""`can_act` and `blocked_reason` on each queue row (R10), so a row its
	approver cannot decide says why before it is opened and never joins a
	batch. Only the line manager's own workflow rows can be blocked -- the
	team lane holds direct reports only, so it is the short half of the page --
	and one `shared_admin_scope` keeps the HR-scope read to one per request."""
	user = frappe.session.user
	with shared_admin_scope():
		for row in rows:
			problem = None
			if row.get("doctype") in _LINE_MANAGER_WORKFLOW_KINDS and row.get("lane") == "team":
				try:
					doc = frappe.get_doc(row["doctype"], row["name"])
				except frappe.DoesNotExistError:
					# Withdrawn since the queue read it; the act refuses it anyway.
					doc = None
				problem = doc and _approver_access_problem(doc, user)
			row["can_act"] = not problem
			row["blocked_reason"] = problem


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
	# Plan 2026-10-31-001 U9 / KTD13: a manager's roster request, HR's alone.
	"roster": "HelixHR Roster Change",
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
	elif doc.doctype == _ROSTER_CHANGE:
		actions = _roster_allowed_actions(doc, user)
	else:
		# Plan 2026-10-18-001 KTD6 / R8: outside their HR scope an HR user is
		# this record's line manager and nothing more, so the HR-role
		# transitions -- every HR-stage decision among them -- are dropped.
		# Plan 2026-10-19-001 U3: so is a holder of the Frappe HR role with no
		# assignment behind it (R3) -- the role alone opens no HR transition.
		# A routed request has no line manager: its worker is gated by
		# `_may_act_on_hr_request` (stored route and assignment) instead.
		line_manager_only = doc.doctype != "HR Request" and not _decides_as_hr(doc, user)
		actions = _workflow_allowed_actions(doc, user, line_manager_only=line_manager_only)

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
	HR: an HR Manager whose assignments cover the employee's company (plan
	2026-10-19-001 U3, the same holders HR-stage mail goes to) and who is not
	this record's requester. Administrator and Guest never count --
	Administrator is the migration and backfill account, not a person HR
	work can be handed to.

	`_company_role_holders` reads with `frappe.get_all`, which skips
	permissions: an ordinary employee has no read on Has Role, this method
	owns the whole scope, and what leaves the function is a boolean.
	"""
	company = frappe.db.get_value("Employee", doc.employee, "company")
	requester = _requester_user(doc)
	return any(user != requester for user in _company_role_holders("HR Manager", company))


def _decides_as_hr(doc, user):
	"""Whether `user` decides `doc` as HR: they are HR, and the employee is in
	their admin scope (plan 2026-10-18-001 KTD6). Outside it an HR user is
	what any other employee is -- this record's line manager, or nobody."""
	return _is_hr(user) and employee_in_admin_scope(doc.employee, resolve_admin_scope(user))


def _workflow_allowed_actions(doc, user, line_manager_only=False):
	"""The two workflow kinds' list, derived from the fixture's own
	transitions.

	Filtered through `has_approval_access` as well as by action name, because
	`apply_workflow` applies it too and it keys on `doc.owner`: a manager who
	*filed* a request for one of their reports would otherwise be offered the
	Employee-role outcomes (`allow_self_approval = 0`) and then refused with
	"Self approval is not allowed" on the click. P4-KTD6's promise is that the
	offered list and the enforced list cannot disagree.

	`line_manager_only` keeps just the Employee-role transitions -- the line
	manager's own step -- for an HR user deciding outside their HR scope.
	"""
	from frappe.model.workflow import get_transitions, has_approval_access

	# `get_transitions` raises on a record the caller cannot read -- a manager's
	# week once it has moved on to HR and its share is gone. No read, no move.
	if not frappe.has_permission(doc.doctype, "read", doc=doc, user=user):
		return []
	reachable = {
		transition.get("action")
		for transition in get_transitions(doc)
		if transition.get("action") in _APPROVAL_ACTIONS
		and has_approval_access(user, doc, transition)
		and (not line_manager_only or transition.get("allowed") == "Employee")
	}
	return [action for action in _APPROVAL_ACTIONS if action in reachable]


def _leave_allowed_actions(doc, user):
	"""The HTD table, as code. A user who is both HR and this employee's
	line manager gets the union -- minus Send to HR once the request is
	already with HR, because there is nowhere left to send it. HR counts only
	inside the caller's HR scope (plan 2026-10-18-001 R8)."""
	is_hr = _decides_as_hr(doc, user)
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
	# Plan 2026-10-31-001 R22: a manager-stage leave opened by anyone but its
	# approver (HR, in practice) names who it is waiting on. None when there
	# is no approver, when it is with HR, when the caller is the approver, or
	# once it is decided -- the detail also serves decided records.
	waiting_on = None
	if (
		doc.status == "Open"
		and cint(doc.docstatus) == 0
		and (doc.get("helixhr_stage") or _LEAVE_STAGE_MANAGER) != LEAVE_STAGE_HR
		and doc.leave_approver
		and doc.leave_approver != frappe.session.user
	):
		waiting_on = _leave_approver_names([doc.leave_approver]).get(doc.leave_approver)
	detail.update(
		{
			"kind": "leave",
			"waiting_on": waiting_on,
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
	problem = _approver_access_problem(doc, frappe.session.user)
	if problem:
		frappe.throw(problem, frappe.PermissionError)
	if action not in _allowed_actions(doc, frappe.session.user):
		frappe.throw(_("That isn't something you can do to this request."))
	_assert_expected_state(expected_modified, current_modified)
	_assert_expected_workflow_state(doc, expected_state)
	if reason and doctype == "HR Request":
		# Plan 2026-10-15-003 R10: Need info and Reject reasons join the
		# employee's thread, so they get the same guard as a comment.
		_refuse_bank_number(doc, reason, _BANK_NUMBER_HANDLER_REFUSAL)

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
		# Plan 2026-10-16-001 KTD5: question and declined markers for HR Request.
		if doctype == "HR Request" and action == "Need info":
			comment_text = f"{HR_REQUEST_QUESTION_PREFIX} {reason}"
		elif doctype == "HR Request" and action in ("Reject", "Decline"):
			comment_text = f"{HR_REQUEST_DECLINED_PREFIX} {reason}"
		elif action == "Send to HR":
			comment_text = f"{HR_HANDOVER_NOTE_PREFIX} {reason}"
		else:
			comment_text = reason
		doc.add_comment("Comment", comment_text)

	# U9: leave keeps its reason as a Comment, so the decision email reads it
	# from here (`events.leave_application_on_submit` / `_on_update`).
	doc.flags.helixhr_decision_note = reason
	outcome = _APPROVAL_KINDS[doctype]["act"](doc, action)
	_record_hr_acting_for_approver(doc, action)
	return {
		"name": doc.name,
		"action": action,
		"state": doc.get(_APPROVAL_KINDS[doctype]["state_field"]),
		# A kind whose decision can end other than as asked says so here: a
		# roster approval that came back Returned (`returned`, `reasons`).
		**(outcome if isinstance(outcome, dict) else {}),
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

	Only reached for a session that is not HR for this employee --
	`_assert_may_act_on` returns early for HR inside its scope -- so the stage
	check is the whole of "the manager loses the request once it is with HR". The raw route (the `submit=1` DocShare HRMS
	grants the approver on every save) is closed by
	`events.leave_application_before_submit`, not here.
	"""
	# Not-yours first: only the approver may learn where the request is.
	if user != doc.leave_approver:
		frappe.throw(
			_(_APPROVAL_NOT_FOUND),
			frappe.PermissionError,
		)
	if doc.get("helixhr_stage") == LEAVE_STAGE_HR:
		frappe.throw(
			_("This leave request is with HR now, so only HR can decide it."),
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
	if doc.doctype == _ROSTER_CHANGE:
		# Plan 2026-10-31-001 U9: scoped by the request's own company (KTD12),
		# not by the submitting manager's -- HR's general reach below would
		# read `doc.employee`, who is that manager.
		_may_act_on_roster_change(doc, user)
		return
	if doc.doctype == "HR Request" and doc.routed_to_role != "HR Manager":
		# Plan 2026-10-19-001 U3 (R3): a request routed to a worker role (IT
		# Team) is that role's, for HR and non-HR callers alike -- the Frappe
		# role plus that role's own assignments covering the employee's org.
		# HR scope never stands in for it: an HR Manager on A who is IT Admin
		# on B works B's IT requests, not A's.
		if user != "Administrator":
			_may_act_on_hr_request(doc, user)
		return
	if _is_hr(user):
		# HR's reach is the same one every administrative read uses
		# (`resolve_admin_scope`, P6-R6): a company-anchored HR Manager
		# decides -- and reads the evidence for -- their own company's
		# records and no other's. Before this, "is HR" alone was the whole
		# check, and the list routes were scoped while the record routes were
		# not. The refusal is the same words as for a missing record, so this
		# endpoint cannot be used to learn whether a record exists.
		if employee_in_admin_scope(doc.employee, resolve_admin_scope(user)):
			# Plan 2026-10-16-001 R4: an HR-routed request needs the HR
			# Manager role itself (a System Manager without it is refused).
			if (
				doc.doctype == "HR Request"
				and user != "Administrator"
				and doc.routed_to_role not in frappe.get_roles(user)
			):
				frappe.throw(_APPROVAL_NOT_FOUND, frappe.PermissionError)
			return
		# Plan 2026-10-18-001 KTD6 / R7: outside their HR scope an HR user
		# falls through to the line-manager rule below, so an HR Manager in
		# one company still decides their own direct report's week in another.
		# `_allowed_actions` keeps them to the line manager's actions there.
		# A routed request has no line manager, so it stays refused.
		if doc.doctype == "HR Request":
			frappe.throw(_APPROVAL_NOT_FOUND, frappe.PermissionError)

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
	if action == "Approve" and not _decides_as_hr(doc, frappe.session.user):
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
	it (P5-R5, P5-R9), and their assignments for that role must cover the
	employee's company (plan 2026-10-19-001 U3). `_assert_may_act_on` sends
	every worker-routed request here, HR caller or not; an HR Manager-routed
	one reaches it only for a non-HR session, which holds no HR Manager
	scope -- so it is only ever decided by HR, never by an IT Team holder."""
	if doc.routed_to_role not in frappe.get_roles(user) or not scope_covers(
		role_scope(user, doc.routed_to_role), frappe.db.get_value("Employee", doc.employee, "company")
	):
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

	Plan 2026-10-15-003 KTD8: each entry carries an ``author`` field with
	the resolved name. Employee entries show the employee's name. Handler
	entries show the commenter's name. Administrator and system entries
	show the team label by routed role.
	"""
	employee = frappe.db.get_value("Employee", doc.employee, ["user_id", "employee_name"], as_dict=True) or {}
	employee_user = employee.get("user_id")
	employee_name = employee.get("employee_name") or doc.employee
	entries = []
	opening = frappe.utils.strip_html(doc.details or "").strip()
	if opening:
		entries.append(
			{"author": employee_name, "message": opening, "on": str(doc.creation), "kind": "opening"}
		)

	team_label = _("IT team") if doc.routed_to_role == "IT Team" else _("HR team")
	rows = frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": "HR Request",
			"reference_name": doc.name,
			"comment_type": "Comment",
		},
		fields=["content", "owner", "creation"],
		order_by="creation asc",
		limit=_COMMENT_FETCH,
	)
	# R5: one batched User read for every handler name; the employee has no
	# read on User, so the names are resolved here, never on the client.
	names = {
		user.name: user.full_name
		for user in frappe.get_all(
			"User",
			filters={"name": ["in", list({row.owner for row in rows}) or [""]]},
			fields=["name", "full_name"],
		)
	}
	for row in rows:
		text = frappe.utils.strip_html(row.content or "").strip()
		if not text or text.startswith(HR_HANDOVER_NOTE_PREFIX):
			continue
		stripped_text, entry_kind = strip_request_comment_marker(text)
		if employee_user and row.owner == employee_user:
			author = employee_name
			entry_kind = "note"
		elif row.owner in ("Administrator", "Guest") or not names.get(row.owner):
			author = team_label
		else:
			author = names[row.owner]
		entries.append(
			{"author": author, "message": stripped_text, "on": str(row.creation), "kind": entry_kind}
		)
	return entries


def _request_decision_detail(doc):
	"""The evidence a worker needs to decide a routed request: what the
	employee wrote, the category it came in under, and the conversation so
	far (P5-R10, P5-R11)."""
	person = frappe.db.get_value("Employee", doc.employee, ["employee_name", "first_name"], as_dict=True) or {}
	employee_name = person.get("employee_name") or doc.employee
	return {
		# Plan 2026-10-16-001 R6: the detail badge names the person, as the row does.
		"waiting_on": ((person.get("first_name") or "").strip() or "the employee")
		if doc.status == HR_REQUEST_WAITING_ON_EMPLOYEE
		else None,
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
		"creation": str(doc.creation) if doc.creation else None,
		"picked_up_on": str(doc.picked_up_on) if doc.picked_up_on else None,
		"replied_on": str(doc.replied_on) if doc.replied_on else None,
		"closed_on": str(doc.closed_on) if doc.closed_on else None,
		"sent_on": str(doc.creation) if doc.creation else None,
		"age_days": _age_in_days(doc.creation, _as_date(user_today())),
		"thread": _request_thread(doc),
		# Plan 2026-10-15-003 R3/R4: the comment box shows only when
		# `comment_on_request` would accept this caller.
		"can_comment": _handler_comment_refusal(doc, frappe.session.user) is None,
		"closed": doc.status not in _REQUEST_OPEN_STATES,
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


# Plan 2026-10-15-003: the states a request takes comments in (R1, R3).
_REQUEST_OPEN_STATES = (HR_REQUEST_OPEN, HR_REQUEST_IN_PROGRESS, HR_REQUEST_WAITING_ON_EMPLOYEE)
# KTD7 / R10: one refusal per side, so each is told where the number belongs.
_BANK_NUMBER_EMPLOYEE_REFUSAL = (
	"Don't put account numbers in comments. Use the bank detail fields on this request."
)
_BANK_NUMBER_HANDLER_REFUSAL = (
	"Don't put account numbers in comments. Ask the employee to use the bank detail fields."
)


def _refuse_bank_number(doc, text, refusal):
	"""KTD7: refuse ``text`` on a correction request when it holds an
	account-number-shaped value. Every other request passes untouched."""
	if doc.get("correction_field") and has_bank_number(text):
		frappe.throw(_(refusal))


def _handler_comment_refusal(doc, user):
	"""Why ``user`` may not comment on ``doc`` as its handler (R3), or None.

	Runs after `_assert_may_act_on`, which already holds HR to its admin
	scope. On top of that: the caller must hold the stored routed role
	(Administrator exempt), the handler's assignments for that role must
	cover the employee's company -- HR is exempt on HR Manager-routed
	requests only, where its admin scope already did (plan 2026-10-19-001
	U3: an IT holder with no row is refused, HR or not) -- and the request
	must be open. Returns the refusal text so `can_comment` and the endpoint
	share one rule."""
	if user != "Administrator":
		if doc.routed_to_role not in frappe.get_roles(user):
			return _(_APPROVAL_NOT_FOUND)
		if doc.routed_to_role != "HR Manager" or not _is_hr(user):
			company = frappe.db.get_value("Employee", doc.employee, "company")
			if not scope_covers(role_scope(user, doc.routed_to_role), company):
				return _(_APPROVAL_NOT_FOUND)
	if doc.status not in _REQUEST_OPEN_STATES:
		return _("This request is {0}. Comments are only allowed while it is open.").format(doc.status)
	return None


def _notify_request_comment(doc, recipients, author_name, message, bell_subject, action_url, event_key):
	"""KTD6: one mail per recipient through `send_notification` and one
	Notification Log bell each, never to the author. R11: a correction
	request's mail and bell carry no message text."""
	recipients = [user for user in dict.fromkeys(recipients) if user and user != frappe.session.user]
	if not recipients:
		return
	excerpt = "" if doc.get("correction_field") else message[:300]
	send_notification(
		event_key,
		recipients,
		{
			"author_name": author_name,
			"employee_name": author_name,
			"category": doc.category,
			"subject": doc.subject,
			"comment_excerpt": excerpt,
			"reply_excerpt": excerpt,
			"action_url": action_url,
		},
		reference_doctype="HR Request",
		reference_name=doc.name,
	)
	for user in recipients:
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"for_user": user,
				"from_user": frappe.session.user,
				"type": "Alert",
				"document_type": "HR Request",
				"document_name": doc.name,
				"subject": bell_subject,
				"description": frappe.utils.escape_html(excerpt),
			}
		).insert(ignore_permissions=True)


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


def _hr_request_queue(employee, today, count_only=False):
	"""Plan 2026-10-16-001 R4: HR Managers see HR requests only; IT requests
	belong to IT Team.

	`count_only` is accepted so every `hr_queue` reader shares one signature,
	and ignored: the one count read (the bootstrap's) never collects requests
	(`include_requests=False`), so there is no request count to make cheap."""
	routed_roles = _routed_roles_held()
	if not routed_roles:
		return []
	return _hr_request_summaries(employee, today, routed_roles=routed_roles)


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
		"hr_queue": _hr_request_queue,
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


def _picker_display_names(users):
	"""`{user: full name}` for many pickers in one read, by the rules of
	`_picker_display_name`: no picker, the Administrator account, a disabled
	or missing user are simply absent, so `.get()` answers None for them."""
	wanted = list({user for user in users if user and user != "Administrator"})
	if not wanted:
		return {}
	rows = frappe.get_all(
		"User", filters={"name": ["in", wanted], "enabled": 1}, fields=["name", "full_name"]
	)
	return {row.name: row.full_name for row in rows}


def _own_requests_filters(employee, category=None, pattern=None):
	"""The one filter for "`employee`'s own requests": optionally one
	category, and a subject matching the LIKE `pattern` (U8's bounded
	`_request_search_pattern`). The employee always comes from the caller's
	session. `_requests_summary` and `portal_search` both read through it
	(plan 2026-10-17-001 KTD5)."""
	filters = {"employee": employee, **({"category": category} if category else {})}
	if pattern:
		filters["subject"] = ["like", pattern]
	return filters


def _request_page_limit(value):
	"""A page size the list may ask for: 1.._REQUEST_MAX_PAGE, `_REQUEST_PAGE`
	when unset (P2-R22)."""
	return min(max(cint(value) or _REQUEST_PAGE, 1), _REQUEST_MAX_PAGE)


# Per-group paging: the most unread HR-reply logs one "Needs you" read looks
# at. A reply notice goes to the requester only, one per reply, so this is far
# past any real backlog; a request whose only reply logs fall beyond it lists
# under Open or Closed, still carrying its `unread_reply` flag.
_REPLY_LOG_CAP = 500


def _unread_reply_request_names():
	"""The HR Requests the session user holds an unread message about from
	the handling team (`HR_REPLY_SUBJECT_PREFIX`), newest reply first -- one
	bounded read. Not scoped to an employee: every caller intersects it with
	`_own_requests_filters`, which is the boundary, so a log about somebody
	else's request lists nothing."""
	return list(
		dict.fromkeys(
			frappe.get_all(
				"Notification Log",
				filters={
					"for_user": frappe.session.user,
					"document_type": "HR Request",
					"read": 0,
					"subject": ["like", f"{HR_REPLY_SUBJECT_PREFIX}%"],
				},
				pluck="document_name",
				order_by="creation desc",
				limit=_REPLY_LOG_CAP,
			)
		)
	)


def _my_request_groups(filters, open_limit=None, closed_limit=None):
	"""Needs you / Open / Closed over *every* request `filters` matches, each
	its own bounded, ordered page with its own total (per-group paging; the
	split used to happen in the browser over one page of 20, so group
	membership and order depended on which rows happened to load).

	The rules are KTD12's, as `isOwed` in lib/requests.js states them:
	  * needs_you -- waits on the employee, or holds an unread message from
	    the team. Newest sent first, every one up to `_REQUEST_MAX_PAGE`.
	  * open -- not owed and not closed. Newest sent first, `open_limit`.
	  * closed -- not owed, Done or Rejected. Most recently closed first;
	    rows closed before `closed_on` was stamped follow, newest sent first
	    (MariaDB sorts NULL last on DESC). `closed_limit`.

	Rows are raw (with `picked_up_by`); `_request_list_rows` decorates them.
	"""
	fields = [*_REQUEST_FIELDS, "picked_up_by"]
	replied = _unread_reply_request_names()
	waiting = {**filters, "status": HR_REQUEST_WAITING_ON_EMPLOYEE}
	not_owed = {**filters, "name": ["not in", replied]} if replied else filters

	def page(group_filters, order_by, limit, or_filters=None, total=None):
		limit = _request_page_limit(limit)
		rows = frappe.get_all(
			"HR Request",
			filters=group_filters,
			or_filters=or_filters,
			fields=fields,
			order_by=order_by,
			limit=limit,
		)
		if total is None:
			total = frappe.db.count("HR Request", group_filters)
		return {"rows": rows, "total": total, "limit": limit}

	if replied:
		# `or_filters` has no count; the two halves are disjoint, so they add.
		needs_you = page(
			filters,
			"creation desc",
			_REQUEST_MAX_PAGE,
			or_filters=[["status", "=", HR_REQUEST_WAITING_ON_EMPLOYEE], ["name", "in", replied]],
			total=frappe.db.count("HR Request", waiting)
			+ frappe.db.count(
				"HR Request",
				{**filters, "name": ["in", replied], "status": ["!=", HR_REQUEST_WAITING_ON_EMPLOYEE]},
			),
		)
	else:
		needs_you = page(waiting, "creation desc", _REQUEST_MAX_PAGE)
	return {
		"needs_you": needs_you,
		"open": page(
			{**not_owed, "status": ["in", (HR_REQUEST_OPEN, HR_REQUEST_IN_PROGRESS)]},
			"creation desc",
			open_limit,
		),
		"closed": page(
			{**not_owed, "status": ["in", _REQUEST_CLOSED_STATES]},
			"closed_on desc, creation desc",
			closed_limit,
		),
	}


def _request_list_rows(*pages):
	"""Each page of raw HR Request rows as the list renders them: the picker
	and team named, the unread flags, the file count. One read each for the
	notifications, files, teams and pickers across the union of the pages,
	never one per row or per page (P2-R22)."""
	names = list(dict.fromkeys(row.name for rows in pages for row in rows))
	replies = set()
	unread = _unread_request_notifications(names, replies)
	counts = _attachment_counts(names)
	# Team labels come from the category, one query for the whole page.
	teams = _category_teams()
	pickers = _picker_display_names(row.picked_up_by for rows in pages for row in rows)

	def shown(row):
		return {
			**{key: value for key, value in row.items() if key != "picked_up_by"},
			"picked_up_by_name": pickers.get(row.picked_up_by),
			"handled_by_team": teams.get(row.category) or "HR",
			"unread": row.name in unread,
			# Plan 2026-10-17-001 KTD12: an unread *message* from the
			# handling team (the HR_REPLY_SUBJECT_PREFIX bell), as opposed
			# to an unread status change. Only the message puts a row
			# under "Needs you", the same rule Home applies.
			"unread_reply": row.name in replies,
			"attachments": counts.get(row.name, 0),
		}

	return [[shown(row) for row in rows] for rows in pages]


def _requests_summary(employee, limit=None, category=None, q=None, group_limits=None):
	"""A bounded page of `employee`'s requests, newest first, optionally of
	one category (U6 / R13; `counts` is per category across all of them)
	and matching a subject search (plan 2026-10-17-001 U8 / ED-19: the same
	bounded `_request_search_pattern` the inbox uses; `total` counts the
	matches, the category chips do not).

	Carries what the list actually renders and nothing else: the lifecycle
	dates, HR's reply, how many files are on it, and whether there is an
	unread notification about it -- which is what puts a row under "Needs
	you" rather than a status word (P2-R13).

	`group_limits` (`{"open_limit": n, "closed_limit": n}`) adds `groups`,
	the per-group pages of `_my_request_groups` under the same category and
	search. Only `get_my_requests` asks for it, so the HR person view and the
	dashboard pay nothing for it; `requests`, `total`, `counts` and `limit`
	mean the same with or without it.
	"""
	limit = _request_page_limit(limit)
	category = _valid_request_category(category)
	filters = _own_requests_filters(employee, category, _request_search_pattern(q))

	rows = frappe.get_all(
		"HR Request",
		filters=filters,
		fields=[*list(_REQUEST_FIELDS), "picked_up_by"],
		order_by="creation desc",
		limit=limit,
	)
	groups = None if group_limits is None else _my_request_groups(filters, **group_limits)
	pages = [rows, *(group["rows"] for group in (groups or {}).values())]
	rows, *group_rows = _request_list_rows(*pages)
	summary = {
		"requests": rows,
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
	if groups is not None:
		for group, shown in zip(groups.values(), group_rows, strict=True):
			group["rows"] = shown
		summary["groups"] = groups
	return summary


@frappe.whitelist()
def get_my_requests(limit=None, category=None, q=None, open_limit=None, closed_limit=None):
	"""The caller's own requests (`_requests_summary`), plus `groups`:
	`{"needs_you" | "open" | "closed": {"rows", "total", "limit"}}`, each
	ordered and counted over every matching request, not one page.

	`open_limit` / `closed_limit` (1-100, default 20) grow only their own
	group; Needs you lists up to 100. `limit` still sizes the flat
	`requests` page, unchanged for callers that read it."""
	return _requests_summary(
		get_current_employee(),
		limit,
		category,
		q,
		group_limits={"open_limit": open_limit, "closed_limit": closed_limit},
	)


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
	row["creation"] = str(row["creation"]) if row.get("creation") else None
	row["picked_up_on"] = str(row["picked_up_on"]) if row.get("picked_up_on") else None
	row["replied_on"] = str(row["replied_on"]) if row.get("replied_on") else None
	row["closed_on"] = str(row["closed_on"]) if row.get("closed_on") else None

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
		# Plan 2026-10-15-003 R1/R4 (replaces `can_reply`): the owner may
		# comment while the request is open; the thread is read-only after.
		"can_comment": row.status in _REQUEST_OPEN_STATES,
		"closed": row.status not in _REQUEST_OPEN_STATES,
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


def _unread_request_notifications(names, replies=None):
	"""{request name -> the caller's unread Notification Log rows about it}.

	When a `replies` set is passed, the names whose unread rows include a
	message from the handling team (`HR_REPLY_SUBJECT_PREFIX`) are added to
	it -- from the same read, not a second one (plan 2026-10-17-001 KTD12).

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
		fields=["name", "document_name", "subject"],
	):
		found.setdefault(row.document_name, []).append(row.name)
		if replies is not None and (row.subject or "").startswith(HR_REPLY_SUBJECT_PREFIX):
			replies.add(row.document_name)
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
	# A string, so the name goes into the shared write's filter as a value,
	# never as an operator list.
	if not isinstance(name, str) or frappe.db.get_value("HR Request", name, "employee") != employee:
		frappe.throw(_("That request isn't yours."), frappe.PermissionError)
	return _mark_record_read("HR Request", name)


@frappe.whitelist(methods=["POST"])
def mark_record_notifications_read(reference_doctype, reference_name):
	"""Mark read every unread Notification Log the *caller* holds about one
	record (plan 2026-10-17-001 U6 / KTD10; U10 reuses it).

	Opening a collapsed Home row ("HR replied · 4") clears all four logs, not
	only the newest. Unlike `mark_my_request_read` this asks nothing about who
	owns the record: a manager or handler can hold logs about a record that is
	not theirs, and clearing their own logs must not fail on it. The scope is
	`for_user` alone, in the filter of the one write -- another user's logs
	about the same record are never read or touched. Unknown or foreign
	records simply clear nothing.

	Returns the new total so the badge moves in the same interaction.
	"""
	rate_limit_per_user("mark_notifications_read")
	reference_doctype = reference_doctype.strip() if isinstance(reference_doctype, str) else ""
	reference_name = reference_name.strip() if isinstance(reference_name, str) else ""
	if not (0 < len(reference_doctype) <= 140 and 0 < len(reference_name) <= 140):
		frappe.throw(_("That record couldn't be found."), frappe.ValidationError)
	return _mark_record_read(reference_doctype, reference_name)


def _mark_record_read(doctype, name):
	"""Mark read every unread Notification Log the caller holds about one
	record, and answer `{cleared, unread}` (the caller's new total). Scoped
	to `for_user` in the filter of the one write, so it can only ever mark
	the caller's own rows. The callers gate and rate-limit."""
	filters = {
		"for_user": frappe.session.user,
		"document_type": doctype,
		"document_name": name,
		"read": 0,
	}
	cleared = frappe.db.count("Notification Log", filters)
	if cleared:
		frappe.db.set_value("Notification Log", filters, "read", 1, update_modified=False)
	return {"cleared": cleared, "unread": _get_unread_notification_count()}


_NOTIFICATION_PAGE = 50
_NOTIFICATION_MAX_PAGE = 200
_NOTIFICATION_FIELDS = (
	"name",
	"subject",
	"description",
	"email_content",
	"document_type",
	"document_name",
	"type",
	"read",
	"creation",
)


@frappe.whitelist()
def get_my_notifications(unread_only=0, limit=None):
	"""The caller's Notification Logs, newest first (plan 2026-10-17-001 U10,
	ED-04 / EP-07).

	Frappe's `get_notification_logs` takes only a count, so an "Unread" view
	built on it was a client filter over the newest 50 rows -- 60 unread
	notifications showed as however many of them happened to be in that page.
	`unread_only` is a server filter here, so the Unread view is every unread
	row up to `_NOTIFICATION_MAX_PAGE`.

	It is also uncached: `get_notification_logs` carries a 60s private HTTP
	cache on GET, which hands a reload the pre-mark-read answer.

	`for_user` is the session user in the filter -- `frappe.get_all` skips
	the doctype's permission query, and Administrator is exempt from it
	anyway, so the filter is the boundary. `unread` is the caller's total,
	so the badge can agree with the list in the same response.
	"""
	limit = min(max(cint(limit) or _NOTIFICATION_PAGE, 1), _NOTIFICATION_MAX_PAGE)
	filters = {"for_user": frappe.session.user}
	if cint(unread_only):
		filters["read"] = 0
	logs = frappe.get_all(
		"Notification Log",
		filters=filters,
		fields=list(_NOTIFICATION_FIELDS),
		order_by="creation desc",
		limit=limit,
	)
	return {"logs": logs, "unread": _get_unread_notification_count()}


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
	"""The one gate every Email templates method calls first: All-orgs
	authority (`_can_edit_email_templates`). HR Manager is refused."""
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

	if scope_covers(resolve_admin_scope(frappe.session.user), company):
		return
	frappe.throw(_("You don't have permission to do that."), frappe.PermissionError)


def _companies_in_admin_scope():
	"""The company selector's options: everything for an unscoped caller,
	the one company for an anchored one."""
	from helixhr.utils import resolve_admin_scope

	companies = read_companies(resolve_admin_scope(frappe.session.user))
	if companies is None:
		return frappe.get_all("Company", pluck="name", order_by="company_name asc")
	return companies


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
		["employee", "status", "modified", "routed_to_role", "picked_up_by"],
		as_dict=True,
	)
	if not row or row.employee != employee:
		frappe.throw(_("That request isn't yours."), frappe.PermissionError)
	if row.status not in _REQUEST_OPEN_STATES:
		frappe.throw(
			_("This request is {0}, so it no longer takes comments. Start a new request instead.").format(
				row.status
			)
		)
	# KTD1: the token guards only the status move. A comment while Open or In
	# Progress changes nothing a handler is looking at.
	moves_status = row.status == HR_REQUEST_WAITING_ON_EMPLOYEE
	if moves_status:
		if not expected_modified:
			frappe.throw(_("Open this request before replying, then try again."))
		if get_datetime(expected_modified) != get_datetime(row.modified):
			frappe.throw(_("Somebody changed this while you were looking at it. Reload and try again."))

	message = (message or "").strip()
	if not message:
		frappe.throw(_("Say something before sending your reply."))
	if len(message) > _DETAILS_MAX:
		frappe.throw(_("That reply is too long. Keep it under {0} characters.").format(_DETAILS_MAX))

	doc = frappe.get_doc("HR Request", name)
	_refuse_bank_number(doc, message, _BANK_NUMBER_EMPLOYEE_REFUSAL)
	doc.add_comment("Comment", escape_request_comment(message))
	if moves_status:
		doc.db_set("status", HR_REQUEST_IN_PROGRESS)

	# R8/R9: a reply goes to the routed role's holders in the employee's
	# company; a plain comment goes to the picker when there is one.
	# `send_notification` never raises, so a mail failure cannot undo this.
	employee_name = frappe.db.get_value("Employee", employee, "employee_name") or employee
	if not moves_status and row.picked_up_by and frappe.db.get_value("User", row.picked_up_by, "enabled"):
		recipients = [row.picked_up_by]
	else:
		recipients = company_role_holders(row.routed_to_role, employee)
	_notify_request_comment(
		doc,
		recipients,
		employee_name,
		message,
		_("{0} commented: {1}").format(employee_name, doc.subject),
		frappe.utils.get_url(f"/helixhr/requests/{name}?tab=work"),
		"request_reply" if moves_status else "request_comment",
	)

	return {"name": name, "status": HR_REQUEST_IN_PROGRESS if moves_status else row.status}


@frappe.whitelist(methods=["POST"])
def comment_on_request(name, message):
	"""Plan 2026-10-15-003 KTD2: the handler's comment on an open request.

	Gate order: `_assert_may_act_on` (own request, HR admin scope), then
	`_handler_comment_refusal` (routed role, company, open state), then the
	message checks, then `add_comment`. Never changes status, never writes
	`helixhr_decision_reason`, takes no modified token: Frappe writes
	`_comments` without bumping `modified`. `add_comment` inserts with
	`ignore_permissions` -- the documented exception `reply_to_my_request`
	already is, after this function's own checks have authorised it.
	"""
	rate_limit_per_user("comment_on_request")
	if not isinstance(name, str) or not name or not frappe.db.exists("HR Request", name):
		frappe.throw(_(_APPROVAL_NOT_FOUND), frappe.PermissionError)

	doc = frappe.get_doc("HR Request", name)
	_assert_may_act_on(doc)
	refusal = _handler_comment_refusal(doc, frappe.session.user)
	if refusal:
		if refusal == _(_APPROVAL_NOT_FOUND):
			frappe.throw(refusal, frappe.PermissionError)
		frappe.throw(refusal)

	message = (message or "").strip()
	if not message:
		frappe.throw(_("Say something before sending your comment."))
	if len(message) > _DETAILS_MAX:
		frappe.throw(_("That comment is too long. Keep it under {0} characters.").format(_DETAILS_MAX))
	_refuse_bank_number(doc, message, _BANK_NUMBER_HANDLER_REFUSAL)

	doc.add_comment("Comment", escape_request_comment(message))

	# R8 / KTD6: the employee hears about it; the bell carries the HR-reply
	# prefix so Home's Needs-you and `mark_my_request_read` pick it up.
	employee_user = frappe.db.get_value("Employee", {"name": doc.employee, "status": "Active"}, "user_id")
	if employee_user and frappe.db.get_value("User", employee_user, "enabled"):
		_notify_request_comment(
			doc,
			[employee_user],
			frappe.utils.get_fullname(frappe.session.user),
			message,
			f"{HR_REPLY_SUBJECT_PREFIX} {doc.subject}",
			frappe.utils.get_url(f"/helixhr/requests/{doc.name}"),
			"request_comment",
		)

	return {"name": name, "status": doc.status}


@frappe.whitelist()
def get_request_view(name):
	"""Plan 2026-10-16-001 KTD8: one read that returns 'employee', 'handler'
	or 'none' for an HR Request name.

	Called by Notifications.vue when opening an HR Request notification so
	it routes to the right view without leaking records or guessing.
	"""
	rate_limit_per_user("get_request_view")
	if not name or not isinstance(name, str) or not frappe.db.exists("HR Request", name):
		return "none"

	doc = frappe.get_doc("HR Request", name)

	employee = get_current_employee()
	if employee and doc.employee == employee:
		return "employee"

	# The two rules that already decide this elsewhere, not a third copy:
	# `hr_request.has_permission` is the read scope (company for a routed
	# holder, `resolve_admin_scope` -- grants included -- for HR), and
	# `_assert_may_act_on` is the handler rule (stored route, own-request).
	if not frappe.has_permission("HR Request", "read", doc=doc):
		return "none"
	try:
		_assert_may_act_on(doc)
	except frappe.PermissionError:
		return "none"
	return "handler"


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


def _directory_scope(company, pattern, department=None):
	"""The directory's one Employee scope, as `frappe.get_all` keyword
	arguments: Active people in `company` (optionally one department),
	matched by the LIKE `pattern`, or everyone in scope when it is None.
	`get_directory` and `portal_search` both read through it (plan
	2026-10-17-001 KTD5), so the colleague a page finds and the one the
	palette finds are the same set."""
	filters = {"status": "Active", "company": company}
	if department:
		filters["department"] = department
	or_filters = None
	if pattern:
		# P8-U3: `name` (the employee id) and `company_email` widen this to
		# match what the project member picker's own placeholder already
		# promises ("Name, employee number or work email") -- this reader
		# is the one it reuses (Projects.vue's own comment explains why:
		# no admin permission required, every employee's own company).
		# Purely additive over the existing three fields, so Directory.vue's
		# own search only ever matches more, never less, and stays scoped
		# to the caller's own company exactly as before.
		or_filters = [
			[field, "like", pattern]
			for field in ("name", "employee_name", "designation", "department", "company_email")
		]
	return {"filters": filters, "or_filters": or_filters, "ignore_permissions": True}


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

	needle = (query or "").strip()[:_DIRECTORY_QUERY_MAX]
	scope = _directory_scope(
		company, f"%{needle}%" if len(needle) >= _DIRECTORY_QUERY_MIN else None, department
	)
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
	"date_of_joining",
	"status",
)
# Plan 2026-10-22-001 R16: People can include people who have left; "all"
# is every status. Active stays the default.
_PEOPLE_SEARCH_STATUSES = ("Active", "Left", "all")


def _people_search_projection(row):
	return {
		"name": row.name,
		"employee_name": row.employee_name,
		"employee_number": row.employee_number,
		"initials": _initials(row.employee_name),
		"designation": row.designation or None,
		"department": row.department or None,
		"company": row.company,
		# Plan 2026-10-17-001 U17: the People table sorts by it. The person
		# view already shows it to the same caller.
		"date_of_joining": str(row.date_of_joining) if row.date_of_joining else None,
		"status": row.status,
	}


def _people_search_scope(scope, pattern, status="Active"):
	"""People's one Employee scope, as `frappe.get_all` keyword arguments:
	Active employees inside a granted `resolve_read_scope` answer, matched by
	the LIKE `pattern` on name, employee number or work email (everyone in
	scope when None), of ``status`` (one of `_PEOPLE_SEARCH_STATUSES`). The
	caller has already refused scope "none".
	`search_people` and `portal_search` both read through it (plan
	2026-10-17-001 KTD5)."""
	# Left and Inactive employees are excluded by default -- looking somebody
	# up means a current colleague unless HR says otherwise (R16).
	filters = {**(admin_scope_employee_filters(scope) or {})}
	if status != "all":
		filters["status"] = status
	or_filters = None
	if pattern:
		or_filters = [
			[field, "like", pattern] for field in ("employee_name", "employee_number", "company_email")
		]
	return {"filters": filters, "or_filters": or_filters, "ignore_permissions": True}


@frappe.whitelist()
def search_people(query=None, start=0, limit=None, status=None):
	"""A bounded page of active employees this caller may read, matched by
	name, employee number or work email (P6-R1, P6-R8), across every company
	their read scope covers; each row names its company and status.
	``status`` "Left" or "all" widens the page to people who have left (plan
	2026-10-22-001 R16); empty means Active.

	Refused server-side, before any row is read, for anyone
	`resolve_read_scope` does not grant a scope to (P6-R6, P6-R13) --
	`PermissionError`, the same as every other admin-only read in this file.
	"""
	rate_limit_per_user("search_people")
	scope = resolve_read_scope(frappe.session.user)
	if scope["kind"] == "none":
		frappe.throw(_("You are not authorised to look up other people."), frappe.PermissionError)

	status = status or "Active"
	if status not in _PEOPLE_SEARCH_STATUSES:
		frappe.throw(_("Choose Active, Left or all people."))
	limit = min(max(cint(limit) or _PEOPLE_SEARCH_PAGE, 1), _PEOPLE_SEARCH_MAX_PAGE)
	start = max(cint(start), 0)

	needle = (query or "").strip()[:_PEOPLE_SEARCH_QUERY_MAX]
	scope_query = _people_search_scope(
		scope, f"%{needle}%" if len(needle) >= _PEOPLE_SEARCH_QUERY_MIN else None, status
	)
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
# The command palette's one server read (plan 2026-10-17-001 U5, KTD5)
#
# Built on the same private scope helpers the page endpoints read through
# (`_directory_scope`, `_people_search_scope`, `_own_requests_filters`,
# `_request_summaries_filters`), never on the whitelisted wrappers: each of
# those spends its own page's rate-limit bucket. Rows are cut to an
# allow-list; there are no totals, facets or photos. Reports are matched in
# the browser from the page's own `get_report_catalog` read.

_PORTAL_SEARCH_ROWS = 5
# A subset of `_directory_projection`'s keys: no manager, and never
# `employee_number`, `company` or `user_id`. `email` only when HR published one.
_PORTAL_SEARCH_DIRECTORY_KEYS = ("name", "employee_name", "initials", "designation", "department", "email")
# A subset of `_people_search_projection`'s keys, for a caller People admits.
_PORTAL_SEARCH_PEOPLE_KEYS = ("name", "employee_name", "initials", "designation", "department", "company")
# A request row needs no more than its name to route; never `hr_note` or a
# `correction_*` field.
_PORTAL_SEARCH_REQUEST_FIELDS = ("name", "subject", "status", "category")
_PORTAL_SEARCH_REQUEST_STATES = (
	HR_REQUEST_OPEN,
	HR_REQUEST_IN_PROGRESS,
	HR_REQUEST_WAITING_ON_EMPLOYEE,
	HR_REQUEST_DONE,
	HR_REQUEST_REJECTED,
)


def _portal_search_people(info, pattern):
	"""People for the palette: the People scope and projection for a caller
	`resolve_read_scope` admits (the same gate as `can_see_people`), else the
	directory's own-company scope and projection. Nobody for a session with
	neither (a Portal Admin with no Employee)."""

	def read(fields, query):
		return frappe.get_all(
			"Employee", fields=list(fields), order_by="employee_name asc", limit=_PORTAL_SEARCH_ROWS, **query
		)

	def cut(person, keys):
		return {key: value for key, value in person.items() if key in keys}

	scope = resolve_read_scope(frappe.session.user)
	if scope["kind"] != "none":
		rows = read(_PEOPLE_SEARCH_FIELDS, _people_search_scope(scope, pattern))
		return [cut(_people_search_projection(row), _PORTAL_SEARCH_PEOPLE_KEYS) for row in rows]
	if info.get("company"):
		rows = read(_DIRECTORY_FIELDS, _directory_scope(info["company"], pattern))
		return [cut(_directory_projection(row, {}), _PORTAL_SEARCH_DIRECTORY_KEYS) for row in rows]
	return []


def _portal_search_inbox(employee, pattern):
	"""Requests this caller works, by subject: `_request_summaries_filters`
	with the routed roles they hold and their own requests excluded, read
	permission-aware so `hr_request`'s query conditions still scope the
	company. A session holding no routing role (System Manager alone) has
	nothing here."""
	routed_roles = _routed_roles_held()
	if not routed_roles:
		return []
	filters = _request_summaries_filters(employee, _PORTAL_SEARCH_REQUEST_STATES, routed_roles=routed_roles)
	filters["subject"] = ["like", pattern]
	return frappe.get_list(
		"HR Request",
		filters=filters,
		fields=list(_PORTAL_SEARCH_REQUEST_FIELDS),
		order_by="creation desc",
		limit=_PORTAL_SEARCH_ROWS,
	)


@frappe.whitelist()
def portal_search(q=None):
	"""The command palette's people and requests for `q` (plan 2026-10-17-001
	U5, KTD5): `{people, requests, inbox, failed}`.

	`q` is the only argument: trimmed, 2-60 characters (anything else answers
	empty groups without a query) and LIKE-escaped (`_request_search_pattern`).
	Each group runs only when the bootstrap flag that draws it would be true --
	a group the caller has no right to is skipped, never refused, so nothing is
	thrown or logged for it:

	* `people` -- `can_see_people`: People's scope and projection; otherwise
	  the directory's own company and projection.
	* `requests` -- the caller's own, by subject; the employee is the session's.
	* `inbox` -- `can_handle_requests` with an Employee: routed roles held,
	  own requests excluded.

	At most `_PORTAL_SEARCH_ROWS` allow-listed rows per group. A group that
	fails is named in `failed` (`get_dashboard`'s `failed_sections` shape)
	instead of failing the call.
	"""
	rate_limit_per_user("portal_search")
	result = {"people": [], "requests": [], "inbox": [], "failed": []}
	pattern = _request_search_pattern(q)
	if not pattern:
		return result

	missing = object()

	def group(name, fn):
		value = _safe(fn, title=f"HelixHR portal search group failed: {name}", default=missing)
		if value is missing:
			result["failed"].append(name)
		else:
			result[name] = value

	info = get_current_employee_info() or {}
	employee = info.get("name")
	# People's scope and the inbox's permission hook ask the same question.
	with shared_admin_scope():
		group("people", lambda: _portal_search_people(info, pattern))
		if employee:
			group(
				"requests",
				lambda: frappe.get_all(
					"HR Request",
					filters=_own_requests_filters(employee, pattern=pattern),
					fields=list(_PORTAL_SEARCH_REQUEST_FIELDS),
					order_by="creation desc",
					limit=_PORTAL_SEARCH_ROWS,
				),
			)
			if _is_hr() or _holds_routed_role():
				group("inbox", lambda: _portal_search_inbox(employee, pattern))
	return result


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
	row = active_employees_for_users([user])[user]
	# Display only: a Left approver is still named, as before (U17).
	shown = row if row.state == "active" else row.latest
	return (shown.name, shown.employee_name) if shown else (None, None)


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


def _report_access(report_key, company=None, right="can_run"):
	"""`resolve_report_access` for the session user and the requested
	``company`` (plan 2026-10-14-001 KTD6), refused uniformly unless it
	grants ``right``. Every report endpoint resolves the company here, at
	the boundary, and nowhere else."""
	from helixhr.utils import resolve_report_access

	access = resolve_report_access(frappe.session.user, report_key, company)
	if not access[right]:
		frappe.throw(_(_REPORT_NOT_OFFERED), frappe.PermissionError)
	return access


def _requested_company(filters):
	"""The ``company`` a report request names, if any (the "All my
	companies" sentinel included)."""
	from helixhr import reports

	raw = reports._parse(filters, {})
	return raw.get("company") if isinstance(raw, dict) else None


@frappe.whitelist()
def run_report(report_key, filters=None, group_by=None, sort=None, **kwargs):
	"""Run one catalog report for the screen (R8, R9, R22).

	Access comes from `resolve_report_access` alone: an unknown key, a
	deny-listed report and a report this caller may not run all get the same
	refusal. `filters` is allowlisted against the entry's filter specs,
	``filters.company`` must be a company this caller may run the report for
	(else it is listed in ``filters_removed`` and the default company runs),
	and entity filters must be plain in-scope strings (see
	`reports.resolve_filters`). Anything else in the
	request -- including a key naming a user to run as -- lands in
	``**kwargs`` and is never read.

	Returns ``{columns, rows, total_rows, totals, groups_applied, truncated,
	filters_removed, filters, can_export, extra}``. ``rows`` is shaped (``_kind`` row /
	subtotal / total) and capped at `reports.SCREEN_ROW_CAP` data rows at a
	group boundary; ``totals`` and the trailing total row cover every row.
	"""
	from helixhr import reports

	rate_limit_per_user("run_report")
	access = _report_access(report_key, _requested_company(filters))

	result = reports.run(report_key, access["scope"], filters, group_by, sort)
	if access["company_removed"]:
		# R14: the company fell back rather than emptying the run.
		result["filters_removed"] = [*result["filters_removed"], "company"]
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
		"combined_option": access.get("combined_option", False),
		"combined_exportable": access.get("combined_exportable", False),
		"blocking_companies": access.get("blocking_companies", []),
		"company_counts": result["company_counts"],
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
	from helixhr.utils import (
		DELIVERY_MANAGER_ROLE,
		HR_USER_ROLE,
		ORG_VIEWER_ROLE,
		REPORT_MANAGER_ROLE,
	)

	# Most callers hold no report tier at all; answer them without resolving
	# every catalog entry (two lookups each) on every bootstrap. `role_scope`
	# reads the caller's rows once for all four roles.
	if resolve_admin_scope(user)["kind"] == "none" and all(
		role_scope(user, role)["kind"] == "none"
		for role in (REPORT_MANAGER_ROLE, HR_USER_ROLE, DELIVERY_MANAGER_ROLE, ORG_VIEWER_ROLE)
	):
		return False
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
	for tasks, and the picked ``company``."""
	from helixhr import reports

	rate_limit_per_user("search_report_options")
	parsed = frappe.parse_json(context) if isinstance(context, str) and context else context
	company = parsed.get("company") if isinstance(parsed, dict) else None
	access = _report_access(report_key, company)
	if access["company_removed"]:
		# Options for a company the caller may not use: none, never home's.
		return []
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
	from helixhr.utils import SENTINEL_ALL

	rate_limit_per_user("request_export")
	access = _report_access(report_key, _requested_company(filters), right="can_export")
	if access["company_removed"]:
		# A file is never silently another company's data.
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
	exporter_company = frappe.db.get_value(  # plan 2026-10-14-001 KTD12/U9
		"Employee", {"user_id": frappe.session.user, "status": "Active"}, "company"
	)
	combined = access["company"] == SENTINEL_ALL
	row = {
		"doctype": "HelixHR Report Export",
		"report_key": report_key,
		"report_label": entry["label"],
		"format": format,
		# Plan 2026-10-15-001 KTD5: a combined export keeps the home company
		# (or the site default) here and its concrete company set -- unscoped
		# HR included -- in ``companies``, which every re-check reads (R7).
		"company": (exporter_company or frappe.defaults.get_global_default("company"))
		if combined
		else access["company"],
		"companies": [{"company": name} for name in access["combined_companies"]] if combined else [],
		"exporter_company": exporter_company,
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
	metadata only, never a file. A company-scoped caller sees rows whose
	data company, exporter company or stored combined company set touches
	their grant reach; a caller
	whose scope resolves to none sees no rows. HR Manager is refused
	since plan 2026-10-06-001 U3 (R6)."""
	rate_limit_per_user("get_export_log")
	if not can_admin_portal(frappe.session.user):
		frappe.throw(_("You don't have permission to do that."), frappe.PermissionError)
	start = max(cint(start), 0)
	page_length = min(max(cint(page_length), 1), _EXPORT_LOG_PAGE_MAX)

	scope = resolve_portal_admin_scope(frappe.session.user)
	if scope["kind"] == "none":
		return {"rows": [], "has_more": False}
	filters, or_filters = {}, None
	reach = read_companies(scope)
	if reach is not None:
		if not reach:
			return {"rows": [], "has_more": False}
		# Plan 2026-10-15-001 KTD5: a combined export is visible when its
		# stored company set touches the caller's reach. Upgrade path if the
		# log grows large: a joined subquery instead of the name list.
		combined = frappe.get_all(
			"HelixHR Report Export Company",
			filters={"parenttype": "HelixHR Report Export", "company": ["in", reach]},
			pluck="parent",
			distinct=True,
		)
		or_filters = [["company", "in", reach], ["exporter_company", "in", reach]]
		if combined:
			or_filters.append(["name", "in", combined])
	rows = frappe.get_all(
		"HelixHR Report Export",
		filters=filters,
		or_filters=or_filters,
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
			"exporter_company",
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
			"HelixHR Report Export",
			export,
			["owner", "status", "file", "file_name", "format", "report_key", "company"],
			as_dict=True,
		)
		if isinstance(export, str)
		else None
	)
	if not row or row.owner != frappe.session.user or row.status != "Ready" or not row.file:
		frappe.throw(_(_EXPORT_GONE), frappe.PermissionError)
	from helixhr.utils import stored_export_companies, stored_export_scope

	# R15: the right to this company's data is re-checked at download time;
	# a combined export re-checks every stored company (plan 2026-10-15-001 R7).
	stored = stored_export_companies(export)
	if stored_export_scope(frappe.session.user, row.report_key, row.company, stored) is None:
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


def _viewer_company(user):
	"""The company a view is shared within: the caller's Active Employee's --
	never a company reached through a grant, so a holder's shared view is
	not published into another company (plan 2026-10-14-001). None for an
	unscoped caller, who sees every shared view."""
	if resolve_admin_scope(user)["kind"] == "unscoped":
		return None
	return frappe.db.get_value("Employee", {"user_id": user, "status": "Active"}, "company")


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
		if key in filter_names or key == "company":
			# A saved company is re-checked on every run (R14).
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


def _can_delete_view(view, user):
	if view.owner == user:
		return True
	if view.visibility != "Shared" or not _is_hr(user):
		return False
	return scope_covers(resolve_admin_scope(user), view.company)


@frappe.whitelist()
def list_report_views(report_key):
	"""The caller's own views of ``report_key`` plus views shared within
	their company. A report the caller can no longer run lists nothing (the
	views are kept, not deleted)."""
	rate_limit_per_user("list_report_views")
	_report_access(report_key)
	user = frappe.session.user
	company = _viewer_company(user)
	shared = {"visibility": "Shared", "report_key": report_key}
	if resolve_admin_scope(user)["kind"] != "unscoped":
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
	_report_access(report_key)
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
			"company": _viewer_company(user),
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


def _assert_report_access_admin(edit=False):
	"""Any Portal Admin may read the matrix; changing it -- one global policy
	-- needs All-orgs authority (plan 2026-10-19-001 R7), which may read it
	too: a Manage access holder with no Portal Admin row saves, and the save
	answers with the read."""
	user = frappe.session.user
	if not (has_all_orgs_authority(user) or (not edit and can_admin_portal(user))):
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
	_assert_report_access_admin(edit=True)
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


# --- Portal Admin gate ---------------------------------------------------------


def _assert_portal_admin():
	"""Portal Admin / System Manager with a non-empty scope (HR Manager and
	HR User are refused since plan 2026-10-06-001 U3)."""
	user = frappe.session.user
	scope = resolve_portal_admin_scope(user) if can_admin_portal(user) else {"kind": "none"}
	if scope["kind"] == "none":
		frappe.throw(_("You don't have permission to do that."), frappe.PermissionError)
	return scope


_ROLE_PROFILE_REFUSAL = "This person's roles come from a role profile. Change it in Desk."


def _apply_role_diff(user_doc, add, remove):
	"""Add ``add`` and remove ``remove`` on one User in one save. Returns
	``(added, removed)``: the roles that actually changed. Refuses when a
	Role Profile re-derives the roles on save."""
	held = {row.role for row in user_doc.roles}
	added = sorted(set(add) - held)
	removed = sorted(set(remove) & held)
	if not added and not removed:
		return [], []
	if added:
		user_doc.append_roles(*added)
	if removed:
		user_doc.set("roles", [row for row in user_doc.roles if row.role not in removed])
	# The caller holds no write on User (Portal Admin has no DocPerm at all);
	# the endpoint's gate is the permission. Validation still runs.
	user_doc.flags.ignore_permissions = True
	user_doc.save()
	now = {row.role for row in user_doc.roles}
	if not set(added) <= now or set(removed) & now:
		frappe.throw(_(_ROLE_PROFILE_REFUSAL))
	return added, removed


# --- People & access (plan 2026-10-15-001 U5, 2026-10-19-001 U5-U6) --------
#
# One person's assignments, read and saved in one place. Reach is
# `_people_access_reach` (R24); what the caller may change within it is
# `assignable` and `may_edit_target` (R11-R13).

_PEOPLE_ACCESS_PAGE_MAX = 100
_PERSON_NOT_FOUND = "You are not authorised to see this person."


def people_access_reach(user):
	"""The companies People & access reaches for ``user`` (plan 2026-10-19-001
	R24): every company for Manage access and System Manager, else the orgs
	their Portal Admin rows name, else none. No Desk User Permission is read.
	The migration's parity check asks it per user."""
	if can_manage_access(user):
		return frappe.get_all("Company", pluck="name", order_by="name asc")
	reach = read_companies(resolve_portal_admin_scope(user))
	if reach is None:
		return frappe.get_all("Company", pluck="name", order_by="name asc")
	return reach


def _people_access_reach():
	"""`people_access_reach` for the caller, refused when it is none."""
	reach = people_access_reach(frappe.session.user)
	if not reach:
		frappe.throw(_("You don't have permission to do that."), frappe.PermissionError)
	return reach


def _flag(value):
	"""True or False for a boolean-ish request value (1/0, "true"/"false",
	bools), else None -- so a garbage value is neither on nor off."""
	value = frappe.utils.sbool(value)
	if value in (True, False):
		return bool(value)
	return None


def _set_self_employee_permission(user, employee, scoped):
	"""R13: an HR Manager is never scoped to their own Employee (preflight
	`check_hr_manager_self_scope`); anyone else is."""
	# Only the self-scoping row: any other Employee User Permission the user
	# carries (a manager's reach, an HR-made restriction) is not ours to drop.
	existing = frappe.get_all(
		"User Permission", filters={"user": user, "allow": "Employee", "for_value": employee}, pluck="name"
	)
	if scoped and not existing:
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": user,
				"allow": "Employee",
				"for_value": employee,
				"apply_to_all_doctypes": 1,
			}
		).insert(ignore_permissions=True)
	elif not scoped:
		for name in existing:
			frappe.delete_doc("User Permission", name, ignore_permissions=True)


# --- Saving a person's access (plan 2026-10-19-001 U5, R8-R16) --------------
#
# The one write path for assignments. The payload is the target's whole
# desired row set; the server re-checks every rule (R16) in the order of the
# plan's "Who may act on whom", then applies the rows, the Frappe roles the
# save touched, the HR Manager self-permission rule, one change-log row and,
# for a high-risk save, the notifications -- in one savepoint.

_ACCESS_ROWS_MAX = 200
_ACCESS_REASON_MAX = 500
_ACCESS_STALE = "Someone changed this person's access. Reload and re-apply."
_ACCESS_LAST_ADMIN = "This would leave nobody who can manage access."
_ACCESS_REMOVAL_ONLY = (
	"This person's login is disabled or their employee record is not Active: access can only be removed."
)
_ACCESS_ROW_FIELDS = ("name", "role", "all_orgs", "company", "allow_export", "origin", "modified")
# Assignment rows that may have been migrated from a legacy grant: removing
# one switches the grant off, turning its export off turns the grant's off,
# so a rollback to the grant code cannot revive either (`_legacy_grant_for`).
_GRANT_BACKED_ROLES = (ORG_VIEWER_ROLE, REPORT_MANAGER_ROLE)


def _access_rows(user):
	return frappe.get_all(
		"HelixHR Access Assignment",
		filters={"user": user},
		fields=list(_ACCESS_ROW_FIELDS),
		order_by="name asc",
	)


def _access_target_user(user):
	"""``user`` as the User's stored name, refused like a person outside
	reach when there is none, or it is Administrator or Guest. The table's
	collation matches ``ALICE@corp.com`` to alice@corp.com, so every identity
	check after this one -- self-edit (R11), the lock (R13), the last holder
	(R14) -- must compare the canonical name, never the request's text."""
	name = frappe.db.get_value("User", user, "name") if isinstance(user, str) and user else None
	if not name or name in ("Administrator", "Guest"):
		frappe.throw(_(_PERSON_NOT_FOUND), frappe.PermissionError)
	return name


def _locked_access_token(user):
	"""`access_token` read with the target's User row and assignment rows
	locked (``FOR UPDATE``), so two saves on one person run one after the
	other and the second compares its token with the first's result -- a
	locking read sees the latest commit, a plain one this transaction's
	snapshot."""
	modified = frappe.db.get_value("User", user, "modified", for_update=True)
	table = frappe.qb.DocType("HelixHR Access Assignment")
	rows = (
		frappe.qb.from_(table)
		.select(table.name, table.modified)
		.where(table.user == user)
		.for_update()
		.run(as_dict=True)
	)
	return _access_token(rows, modified)


def _access_key(row):
	return (row["role"], ALL_ORGS_SCOPE_KEY if row["all_orgs"] else row["company"])


def _access_token(rows, user_modified):
	parts = sorted([row.name, str(row.modified)] for row in rows)
	return hashlib.sha256(frappe.as_json([parts, str(user_modified)]).encode()).hexdigest()


def access_token(user):
	"""``user``'s stale token for People & access: a hash of every assignment
	row's name and modified plus the User's modified, so any insert, delete
	or edit of any row -- not only the newest -- or a Has Role change moves
	it."""
	return _access_token(_access_rows(user), frappe.db.get_value("User", user, "modified"))


def _access_snapshot(rows):
	"""Rows as the change log and the response carry them."""
	return sorted(
		(
			{
				"role": row["role"],
				"all_orgs": cint(row["all_orgs"]),
				"company": None if row["all_orgs"] else row["company"],
				"allow_export": cint(row["allow_export"]),
			}
			for row in rows
		),
		key=lambda row: (row["role"], row["company"] or ""),
	)


def _access_line(row):
	"""One row in words: "HR Manager · All orgs", "Reports · Org A, with export"."""
	where = _("All orgs") if row["all_orgs"] else row["company"]
	line = f"{_(ROLE_LABELS[row['role']])} · {where}"
	return _("{0}, with export").format(line) if row["allow_export"] else line


def _parse_json_arg(value):
	"""A request argument that may arrive as JSON text; unparseable text is
	None, for the caller's own refusal."""
	if not isinstance(value, str):
		return value
	try:
		return json.loads(value)
	except ValueError:
		return None


def _desired_access_rows(rows):
	"""The payload as ``{(role, scope_key): row}``, refused whole on any bad
	row. The doctype re-validates on insert; this refuses before anything is
	written, with one message per mistake."""
	rows = _parse_json_arg(rows)
	if not isinstance(rows, list) or len(rows) > _ACCESS_ROWS_MAX:
		frappe.throw(_("Invalid access rows."))
	desired = {}
	for row in rows:
		if not isinstance(row, dict) or row.get("role") not in ASSIGNABLE_ROLES:
			frappe.throw(_("Invalid access rows."))
		role = row["role"]
		all_orgs = _flag(row.get("all_orgs") or 0)
		allow_export = _flag(row.get("allow_export") or 0)
		company = row.get("company") or ""
		if all_orgs is None or allow_export is None or not isinstance(company, str):
			frappe.throw(_("Invalid access rows."))
		if all_orgs == bool(company):
			frappe.throw(_("Choose All orgs or one org for {0}.").format(_(ROLE_LABELS[role])))
		if company and not frappe.db.exists("Company", company):
			frappe.throw(_("{0} is not a company.").format(company))
		if role in ALL_ORGS_ONLY_ROLES and not all_orgs:
			frappe.throw(_("{0} can only be given on All orgs.").format(_(ROLE_LABELS[role])))
		if allow_export and role not in EXPORT_ROLES:
			frappe.throw(_("Export can only be allowed on Reports or Org viewer."))
		clean = frappe._dict(
			role=role, all_orgs=int(all_orgs), company=company, allow_export=int(allow_export)
		)
		if _access_key(clean) in desired:
			frappe.throw(_("{0} is listed twice.").format(_access_line(clean)))
		desired[_access_key(clean)] = clean
	return desired


def _access_target_in_reach(caller, user, rows, reach):
	"""People & access reach for one person (R24): everyone for Manage access
	and System Manager; else their Active Employee's company, or a named org
	on one of their rows, inside the caller's reach. Someone with only
	All-orgs rows and no Employee is reachable by full authority only."""
	return can_manage_access(caller) or _reached(reach, rows, active_employee_for_user(user))


def _reached(reach, rows, employee):
	"""`_access_target_in_reach` below full authority, from answers already
	read: ``employee`` as `active_employee_for_user` gives it."""
	if employee.state == "active" and employee.company in reach:
		return True
	return any(not row.all_orgs and row.company in reach for row in rows)


def _assignable_refusal(row):
	if row["role"] in HR_ROLES:
		return _("Only Manage access can give HR roles.")
	if row["all_orgs"]:
		return _("You can't give {0} on All orgs.").format(_(ROLE_LABELS[row["role"]]))
	return _("You can't give {0} in {1}.").format(_(ROLE_LABELS[row["role"]]), row["company"])


def _manage_access_left(removing):
	"""R14: how many All-orgs Manage access holders other than ``removing``
	still pass R4. Locks every such row first (``FOR UPDATE``), so two
	holders removing each other at once are counted one after the other and
	the second sees the first's removal."""
	table = frappe.qb.DocType("HelixHR Access Assignment")
	holders = (
		frappe.qb.from_(table)
		.select(table.user)
		.where((table.role == ACCESS_ADMIN_ROLE) & (table.all_orgs == 1))
		.for_update()
		.run(pluck=True)
	)
	left = 0
	for holder in set(holders) - {removing}:
		# Fresh: a cached gate from earlier in this request may predate a
		# disable or a Left status.
		clear_role_scope_cache(holder)
		if portal_role_scope(holder, ACCESS_ADMIN_ROLE)["kind"] != "none":
			left += 1
	return left


_GRANT_LEVEL = {ORG_VIEWER_ROLE: "All data", REPORT_MANAGER_ROLE: "Reports only"}


def _legacy_grant_for(user, row):
	"""Rollback safety: the name of ``user``'s enabled legacy `HelixHR Company
	Access` grant that migrated ``row`` came from -- Org viewer from an "All
	data" grant, Reports from a "Reports only" one, naming ``row``'s org --
	or None. A Reports row migrated from the Report Manager role has no
	grant behind it."""
	if row.origin != "migrated" or row.role not in _GRANT_LEVEL or row.all_orgs:
		return None
	grant = frappe.db.get_value(
		"HelixHR Company Access", {"user": user, "enabled": 1}, ["name", "level"], as_dict=True
	)
	if not grant or grant.level != _GRANT_LEVEL[row.role]:
		return None
	named = frappe.db.exists(
		"HelixHR Company Access Company",
		{"parenttype": "HelixHR Company Access", "parent": grant.name, "company": row.company},
	)
	return grant.name if named else None


def _disable_legacy_grant(user, row):
	"""Switch off the legacy grant removed ``row`` came from, which the
	pre-assignment code would otherwise still apply. A plain write -- the
	grant's own hooks would expire exports before commit; the save's
	after-commit does that instead."""
	name = _legacy_grant_for(user, row)
	if name:
		frappe.db.set_value(
			"HelixHR Company Access",
			name,
			{
				"enabled": 0,
				"disabled_reason": _("Removed in People & access by {0}").format(
					frappe.utils.get_fullname(frappe.session.user)
				),
			},
		)


def _turn_off_legacy_export(user, row):
	"""Export turned off on ``row``: off on the grant it came from too, so a
	rollback does not turn it back on. The grant has one export flag for all
	its orgs, so this errs narrow. Export a Report Manager role gave is not
	ours to remove; the runbook's rollback notes say so."""
	name = _legacy_grant_for(user, row)
	if name:
		frappe.db.set_value("HelixHR Company Access", name, "allow_export", 0)


def _notify_high_risk_access(change, user, lines, reason):
	"""R15: one bell entry for every System Manager and Manage access holder
	but the caller. Names and the reason are typed text: escaped here, since
	the bell renders the subject as HTML."""
	caller = frappe.session.user
	recipients = set(
		frappe.get_all("Has Role", filters={"parenttype": "User", "role": "System Manager"}, pluck="parent")
	)
	recipients |= set(
		frappe.get_all("HelixHR Access Assignment", filters={"role": ACCESS_ADMIN_ROLE}, pluck="user")
	)
	recipients -= {caller, "Guest"}
	if not recipients:
		return
	escape = frappe.utils.escape_html
	subject = _("{0} gave {1} high-risk access: {2}. Reason: {3}").format(
		escape(frappe.utils.get_fullname(caller)),
		escape(frappe.utils.get_fullname(user)),
		escape("; ".join(lines)),
		escape(reason),
	)
	for recipient in frappe.get_all(
		"User", filters={"name": ["in", sorted(recipients)], "enabled": 1}, pluck="name"
	):
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"for_user": recipient,
				"from_user": caller,
				"type": "Alert",
				"document_type": "HelixHR Access Change",
				"document_name": change.name,
				"subject": subject,
			}
		).insert(ignore_permissions=True)


def _after_access_save(user):
	clear_role_scope_cache(user)
	frappe.clear_cache(user=user)
	expire_unreachable_exports(user)


@frappe.whitelist(methods=["POST"])
def save_person_access(user, rows, token, reason=None, sync_roles=None):
	"""Save ``user``'s assignments (plan 2026-10-19-001 U5, R8-R16).

	``rows`` is the whole desired set, each ``{role, all_orgs, company,
	allow_export}``; ``token`` is `access_token` as the screen loaded it.
	``sync_roles`` names managed roles whose Frappe role should follow the
	rows even though no row of theirs changed: "Remove role" on a role set
	outside HelixHR, "Restore role" on one missing in Frappe (R9).

	Refused, in order, with nothing written: the caller's own access (R11),
	a person outside the caller's reach (one refusal whether or not they
	exist), a person holding a role the caller can't assign (R13), a Role
	Profile target (R10), a stale token, any added or changed row outside
	the caller's authority (R12, R7), new access for a disabled or Left
	person, a high-risk save without a plain-text reason of at most 500
	characters (R15), and a save that would leave no All-orgs Manage access
	holder passing R4, unless the caller is a System Manager (R14). A row the
	caller may not assign is kept even when the payload omits it.

	One savepoint applies the rows, the Frappe roles of the touched roles
	only (KTD4), the HR Manager self Employee permission, one `HelixHR
	Access Change` row and the high-risk notifications. Cache clearing and
	export expiry run after commit."""
	rate_limit_per_user("save_person_access")
	caller = frappe.session.user
	reach = set(_people_access_reach())
	user = _access_target_user(user)
	if user == caller:
		frappe.throw(_(SELF_EDIT_REFUSAL), frappe.PermissionError)
	user_doc = frappe.get_doc("User", user)
	stored = _access_rows(user)
	if not _access_target_in_reach(caller, user, stored, reach):
		frappe.throw(_(_PERSON_NOT_FOUND), frappe.PermissionError)
	held = {row.role for row in user_doc.roles}
	locked = may_edit_target(caller, user, stored, held)
	if locked:
		frappe.throw(_(locked), frappe.PermissionError)
	if user_doc.get("role_profiles") or user_doc.get("role_profile_name"):
		frappe.throw(_(_ROLE_PROFILE_REFUSAL))
	if not isinstance(token, str) or token != _access_token(stored, user_doc.modified):
		frappe.throw(_(_ACCESS_STALE), frappe.TimestampMismatchError)

	desired = _desired_access_rows(rows)
	sync_roles = _parse_json_arg(sync_roles) or []
	if not isinstance(sync_roles, list) or not all(role in ASSIGNABLE_ROLES for role in sync_roles):
		frappe.throw(_("Invalid roles."))
	authority = assignable(caller)
	if set(sync_roles) - authority["roles"]:
		frappe.throw(_("You can't change that role."), frappe.PermissionError)

	current = {_access_key(row): row for row in stored}
	added = [row for key, row in desired.items() if key not in current]
	# A row the caller may not assign stays, whatever the payload says.
	removed = [row for key, row in current.items() if key not in desired and may_assign(authority, row)]
	changed = [
		(current[key], row)
		for key, row in desired.items()
		if key in current and cint(current[key].allow_export) != row.allow_export
	]
	for row in [*added, *(new for _old, new in changed)]:
		if not may_assign(authority, row):
			frappe.throw(_assignable_refusal(row), frappe.PermissionError)
	opens = added or any(new.allow_export for _old, new in changed)
	if opens and (not user_doc.enabled or active_employee_for_user(user).state == "inactive"):
		frappe.throw(_(_ACCESS_REMOVAL_ONLY))

	removed_keys = {_access_key(row) for row in removed}
	final = [desired.get(key, row) for key, row in current.items() if key not in removed_keys] + added
	touched = {row.role for row in (*added, *removed)} | {new.role for _old, new in changed} | set(sync_roles)
	wanted = {row.role for row in final} & touched
	role_add = wanted - held
	role_remove = (touched - wanted) & held
	if not (added or removed or changed or role_add or role_remove):
		return {
			"user": user,
			"rows": _access_snapshot(stored),
			"token": _access_token(stored, user_doc.modified),
			"change": None,
		}

	# R15: what this save gives that is high-risk, in words.
	lines = [
		_access_line(row)
		for row in added
		if row.all_orgs or row.allow_export or row.role in (ACCESS_ADMIN_ROLE, *HR_ROLES)
	]
	lines += [_access_line(new) for _old, new in changed if new.allow_export]
	# "Restore role" on an HR row gives HRMS Desk access back (KTD12).
	lines += [_(ROLE_LABELS[role]) for role in sorted(role_add & set(HR_ROLES) - {row.role for row in added})]
	reason = reason.strip() if isinstance(reason, str) else ""
	if len(reason) > _ACCESS_REASON_MAX:
		frappe.throw(_("Keep the reason to {0} characters or fewer.").format(_ACCESS_REASON_MAX))
	if lines and not reason:
		frappe.throw(_("This change gives high-risk access: give a reason."))
	if any(row.role == ACCESS_ADMIN_ROLE for row in removed) and _manage_access_left(user) == 0:
		if caller != "Administrator" and "System Manager" not in frappe.get_roles(caller):
			frappe.throw(_(_ACCESS_LAST_ADMIN))

	frappe.db.savepoint("save_person_access")
	try:
		# The check above ran unlocked: a save that committed since is
		# caught here, before anything is written.
		if _locked_access_token(user) != token:
			frappe.throw(_(_ACCESS_STALE), frappe.TimestampMismatchError)
		for row in removed:
			frappe.delete_doc("HelixHR Access Assignment", row.name, ignore_permissions=True)
			_disable_legacy_grant(user, row)
		for old, new in changed:
			doc = frappe.get_doc("HelixHR Access Assignment", old.name)
			doc.allow_export = new.allow_export
			doc.save(ignore_permissions=True)
			if not new.allow_export:
				_turn_off_legacy_export(user, old)
		for row in added:
			frappe.get_doc(
				{
					"doctype": "HelixHR Access Assignment",
					"user": user,
					"role": row.role,
					"all_orgs": row.all_orgs,
					# "" not None: a None Company picks up the caller's default
					# company on insert, which an All-orgs row refuses.
					"company": row.company,
					"allow_export": row.allow_export,
					"origin": "manual",
				}
			).insert(ignore_permissions=True)
		role_add, role_remove = _apply_role_diff(user_doc, role_add, role_remove)
		employee = active_employee_for_user(user)
		if employee.state == "active" and "HR Manager" in role_add:
			_set_self_employee_permission(user, employee.name, scoped=False)
		elif employee.state == "active" and "HR Manager" in role_remove:
			_set_self_employee_permission(user, employee.name, scoped=True)
		change = frappe.get_doc(
			{
				"doctype": "HelixHR Access Change",
				"target_user": user,
				"changed_by": caller,
				"reason": reason or None,
				"before": frappe.as_json(_access_snapshot(stored)),
				"after": frappe.as_json(_access_snapshot(final)),
			}
		).insert(ignore_permissions=True)
		if lines:
			_notify_high_risk_access(change, user, lines, reason)
	except Exception:
		frappe.db.rollback(save_point="save_person_access")
		# Anything cached from inside the rolled-back savepoint is wrong now.
		frappe.clear_cache(user=user)
		clear_role_scope_cache(user)
		raise

	clear_role_scope_cache(user)
	frappe.db.after_commit.add(lambda: _after_access_save(user))
	saved = _access_rows(user)
	return {
		"user": user,
		"rows": _access_snapshot(saved),
		"token": _access_token(saved, frappe.db.get_value("User", user, "modified")),
		"change": change.name,
	}


# --- People & access reads (plan 2026-10-19-001 U6, R17-R24) ----------------
#
# The Elevated table, the All employees search and the person editor's data.
# Each answers only for people in the caller's reach (`_reached`); a row
# outside it on a reachable person is shown read-only, never dropped.

# R19, in display order. "No login" is not among them: every elevated person
# is a User, so `search_people_access` marks it per employee (``has_login``).
_ACCESS_WARNINGS = {
	"employee_left": "problem",
	"user_disabled": "problem",
	"role_outside_helixhr": "problem",
	"role_missing_in_frappe": "problem",
	"own_org_not_covered": "problem",
	"can_manage_access": "info",
	"export_on": "info",
}
_ACCESS_HISTORY_MAX = 20
_SYSTEM_MANAGER = "System Manager"


def _missing_in_frappe(row, held, changed):
	"""R9 "Role missing in Frappe" for one assignment ``row``. U8 writes the
	Org viewer and Reports rows of a migrated grant without the Frappe role
	(rollback safety; `PORTAL_ONLY_ROLES` reach needs only the row), so that
	gap is expected until the person's first save -- ``changed``: a HelixHR
	Access Change exists for them. From then on the save keeps the role in
	step, and a gap on any row is drift."""
	if row.role in held:
		return False
	return bool(changed) or row.origin != "migrated" or row.role not in _GRANT_BACKED_ROLES


def _access_warnings(rows, held, enabled, employee, changed):
	"""R19 for one person, as ``[{key, kind}]`` in `_ACCESS_WARNINGS` order.
	``held`` is their Frappe roles, ``employee`` `active_employee_for_user`'s
	answer. A System Manager is unscoped (R5): no drift warning and no
	uncovered own org (KTD4)."""
	keys = set()
	if employee.state == "inactive":
		keys.add("employee_left")
	if not enabled:
		keys.add("user_disabled")
	if _SYSTEM_MANAGER not in held:
		if (held & set(ASSIGNABLE_ROLES)) - {row.role for row in rows}:
			keys.add("role_outside_helixhr")
		if any(_missing_in_frappe(row, held, changed) for row in rows):
			keys.add("role_missing_in_frappe")
		if (
			employee.state == "active"
			and rows
			and not any(row.all_orgs or row.company == employee.company for row in rows)
		):
			keys.add("own_org_not_covered")
	# "All orgs" is the role chip's own badge, not a warning (2026-10-21);
	# Desk HR reach stays global by decision, so it is not flagged either.
	if any(row.role == ACCESS_ADMIN_ROLE for row in rows):
		keys.add("can_manage_access")
	if any(row.allow_export for row in rows):
		keys.add("export_on")
	return [{"key": key, "kind": kind} for key, kind in _ACCESS_WARNINGS.items() if key in keys]


def _grouped_access_roles(rows, held):
	"""R18: one entry per role with its orgs -- ``{role, label, assigned,
	all_orgs, companies, export}`` in `ASSIGNABLE_ROLES` order. A managed
	role held in Frappe with no row is an entry with no orgs and ``assigned``
	false, except on a System Manager (no drift, KTD4)."""
	by_role = {}
	for row in rows:
		entry = by_role.setdefault(
			row.role,
			{
				"role": row.role,
				"label": _(ROLE_LABELS[row.role]),
				"assigned": True,
				"all_orgs": False,
				"companies": [],
				"export": False,
			},
		)
		if row.all_orgs:
			entry["all_orgs"] = True
		else:
			entry["companies"].append(row.company)
		entry["export"] = entry["export"] or bool(row.allow_export)
	if _SYSTEM_MANAGER not in held:
		for role in (held & set(ASSIGNABLE_ROLES)) - set(by_role):
			by_role[role] = {
				"role": role,
				"label": _(ROLE_LABELS[role]),
				"assigned": False,
				"all_orgs": False,
				"companies": [],
				"export": False,
			}
	for entry in by_role.values():
		entry["companies"].sort()
	return [by_role[role] for role in ASSIGNABLE_ROLES if role in by_role]


def _latest_employee(employee):
	"""``(name, employee_name)`` of the Active Employee, else the newest row
	of any status (display only, never an anchor)."""
	shown = employee if employee.state == "active" else employee.latest
	return (shown.name, shown.employee_name) if shown else (None, None)


@frappe.whitelist()
def get_elevated_access():
	"""The Elevated access table (R17-R20): everyone in the caller's reach
	who holds an assignment or a managed Frappe role, and every enabled
	System Manager, with their roles grouped by org, R19 warnings and last
	change. Sorted problems first, then All-orgs and Manage access holders,
	then by name; ``counts`` drives the attention banner.

	A fixed number of set reads whatever the head count: assignments, Has
	Role, Employee, change log, User."""
	rate_limit_per_user("get_elevated_access")
	reach = set(_people_access_reach())
	full = can_manage_access(frappe.session.user)

	rows = {}
	for row in frappe.get_all(
		"HelixHR Access Assignment",
		fields=["user", "role", "all_orgs", "company", "allow_export", "origin"],
		order_by="name asc",
	):
		rows.setdefault(row.user, []).append(row)
	held = {}
	for row in frappe.get_all(
		"Has Role",
		filters={"parenttype": "User", "role": ["in", [*ASSIGNABLE_ROLES, _SYSTEM_MANAGER]]},
		fields=["parent", "role"],
	):
		held.setdefault(row.parent, set()).add(row.role)
	candidates = (set(rows) | set(held)) - {"Administrator", "Guest"}
	employees = active_employees_for_users(candidates)
	people = sorted(
		user for user in candidates if full or _reached(reach, rows.get(user, []), employees[user])
	)
	if not people:
		return {
			"people": [],
			"counts": {"needs_attention": 0, "by_warning": dict.fromkeys(_ACCESS_WARNINGS, 0)},
		}

	# One row per save, so this stays small; the newest per person wins.
	last = {}
	for change in frappe.get_all(
		"HelixHR Access Change",
		filters={"target_user": ["in", people]},
		fields=["target_user", "changed_by", "creation"],
		order_by="creation desc",
	):
		last.setdefault(change.target_user, change)
	accounts = {
		account.name: account
		for account in frappe.get_all(
			"User",
			filters={"name": ["in", sorted({*people, *(change.changed_by for change in last.values())})]},
			fields=["name", "full_name", "enabled"],
		)
	}

	out = []
	for user in people:
		account = accounts.get(user)
		user_rows = rows.get(user, [])
		user_held = held.get(user, set())
		if not account or (not account.enabled and not user_rows and not user_held - {_SYSTEM_MANAGER}):
			# A disabled System Manager with nothing else holds no access.
			continue
		employee = employees[user]
		employee_id, employee_name = _latest_employee(employee)
		change = last.get(user)
		changer = accounts.get(change.changed_by) if change else None
		out.append(
			{
				"user": user,
				"full_name": account.full_name or employee_name or user,
				"employee": employee_id,
				"employee_name": employee_name,
				"own_company": employee.company,
				"has_login": bool(account.enabled),
				"system_manager": _SYSTEM_MANAGER in user_held,
				"roles": _grouped_access_roles(user_rows, user_held),
				"warnings": _access_warnings(user_rows, user_held, account.enabled, employee, change),
				"last_change": {
					"by": change.changed_by,
					"by_name": (changer.full_name if changer else None) or change.changed_by,
					"at": change.creation,
				}
				if change
				else None,
			}
		)

	def problem(person):
		return any(warning["kind"] == "problem" for warning in person["warnings"])

	out.sort(
		key=lambda person: (
			not problem(person),
			not any(role["all_orgs"] or role["role"] == ACCESS_ADMIN_ROLE for role in person["roles"]),
			person["full_name"].lower(),
			person["user"],
		)
	)
	by_warning = dict.fromkeys(_ACCESS_WARNINGS, 0)
	for person in out:
		for warning in person["warnings"]:
			by_warning[warning["key"]] += 1
	return {
		"people": out,
		"counts": {"needs_attention": sum(1 for person in out if problem(person)), "by_warning": by_warning},
	}


@frappe.whitelist()
def search_people_access(query=None, company=None, start=0, page_length=50):
	"""All employees (R21): every Active Employee in the caller's reach, with
	or without a login, filtered by name, ID or email (two characters or
	more) and by ``company``, paged. A ``company`` outside the reach is
	refused, not emptied. ``companies`` is the whole reach, for the screen's
	org filter."""
	rate_limit_per_user("search_people_access")
	reach = _people_access_reach()
	companies = list(reach)
	if company:
		if not isinstance(company, str) or company not in reach:
			frappe.throw(_("You don't have permission to do that."), frappe.PermissionError)
		reach = [company]
	start = max(cint(start), 0)
	page_length = min(max(cint(page_length), 1), _PEOPLE_ACCESS_PAGE_MAX)
	query = query.strip()[:80] if isinstance(query, str) else ""
	or_filters = None
	if len(query) >= 2:
		like = f"%{query}%"
		or_filters = {"employee_name": ["like", like], "name": ["like", like], "user_id": ["like", like]}
	employees = frappe.get_all(
		"Employee",
		filters={"status": "Active", "company": ["in", reach]},
		or_filters=or_filters,
		fields=["name", "employee_name", "user_id", "company"],
		order_by="employee_name asc, name asc",
		start=start,
		limit=page_length + 1,
	)
	page = employees[:page_length]
	users = sorted({row.user_id for row in page if row.user_id})
	enabled = (
		set(frappe.get_all("User", filters={"name": ["in", users], "enabled": 1}, pluck="name"))
		if users
		else set()
	)
	return {
		"rows": [
			{
				"employee": row.name,
				"employee_name": row.employee_name,
				"company": row.company,
				"user": row.user_id or None,
				"has_login": row.user_id in enabled,
			}
			for row in page
		],
		"has_more": len(employees) > page_length,
		"companies": companies,
	}


def _access_change_lines(before, after):
	"""A change log row's gains and losses in words (R22, R23)."""
	before = {_access_key(row): row for row in frappe.parse_json(before or "[]")}
	after = {_access_key(row): row for row in frappe.parse_json(after or "[]")}
	gains = [_access_line(row) for key, row in after.items() if key not in before]
	losses = [_access_line(row) for key, row in before.items() if key not in after]
	for key in before.keys() & after.keys():
		if cint(before[key]["allow_export"]) != cint(after[key]["allow_export"]):
			line = _("Export on {0}").format(_access_line({**after[key], "allow_export": 0}))
			(gains if cint(after[key]["allow_export"]) else losses).append(line)
	return sorted(gains), sorted(losses)


def _access_history(user):
	"""R23: ``user``'s last `_ACCESS_HISTORY_MAX` changes, newest first --
	who, when, the plain-text reason, and what was gained and lost."""
	changes = frappe.get_all(
		"HelixHR Access Change",
		filters={"target_user": user},
		fields=["name", "changed_by", "creation", "reason", "before", "after"],
		order_by="creation desc",
		limit=_ACCESS_HISTORY_MAX,
	)
	names = dict(
		frappe.get_all(
			"User",
			filters={"name": ["in", sorted({change.changed_by for change in changes})]},
			fields=["name", "full_name"],
			as_list=True,
		)
		if changes
		else []
	)
	history = []
	for change in changes:
		gains, losses = _access_change_lines(change.before, change.after)
		history.append(
			{
				"name": change.name,
				"changed_by": change.changed_by,
				"changed_by_name": names.get(change.changed_by) or change.changed_by,
				"at": change.creation,
				"reason": change.reason,
				"gains": gains,
				"losses": losses,
			}
		)
	return history


@frappe.whitelist()
def get_person_access(user):
	"""One person's access for the editor (R22, R23), keyed by User: the
	summary sentences, every assignment row -- a row the caller may not
	assign is ``editable`` false, never dropped -- managed roles held in
	Frappe with no row, R19 warnings, the lock, what the caller may assign,
	the presets, the stale token and the recent history.

	``lock`` is None when the caller may edit; else ``{reason,
	removal_only}``: the whole person is read-only for the caller's own
	access (R11), a role the caller can't assign (R13) or a Role Profile
	(R10); for a disabled or Left person only removing rows is allowed.
	Refused, alike whether or not the person exists, outside the caller's
	reach."""
	rate_limit_per_user("get_person_access")
	caller = frappe.session.user
	reach = _people_access_reach()
	user = _access_target_user(user)
	stored = _access_rows(user)
	if not _access_target_in_reach(caller, user, stored, set(reach)):
		frappe.throw(_(_PERSON_NOT_FOUND), frappe.PermissionError)
	user_doc = frappe.get_doc("User", user)
	held = {row.role for row in user_doc.roles}
	employee = active_employee_for_user(user)
	changed = frappe.db.exists("HelixHR Access Change", {"target_user": user})

	lock = None
	locked = may_edit_target(caller, user, stored, held)
	if locked:
		lock = {"reason": _(locked), "removal_only": False}
	elif user_doc.get("role_profiles") or user_doc.get("role_profile_name"):
		lock = {"reason": _(_ROLE_PROFILE_REFUSAL), "removal_only": False}
	elif not user_doc.enabled or employee.state == "inactive":
		lock = {"reason": _(_ACCESS_REMOVAL_ONLY), "removal_only": True}
	authority = assignable(caller)
	may_change = lock is None or lock["removal_only"]
	system_manager = _SYSTEM_MANAGER in held
	order = {role: index for index, role in enumerate(ASSIGNABLE_ROLES)}
	direct_reports = (
		frappe.db.count("Employee", {"reports_to": employee.name, "status": "Active"})
		if employee.state == "active"
		else 0
	)
	employee_id, employee_name = _latest_employee(employee)
	return {
		"user": user,
		"full_name": user_doc.full_name or employee_name or user,
		"employee": employee_id,
		"employee_name": employee_name,
		"own_company": employee.company,
		"has_login": bool(user_doc.enabled),
		"system_manager": system_manager,
		"summary": access_summary(
			stored,
			held,
			direct_reports,
			own_company=employee.company,
			enabled=bool(user_doc.enabled),
			employee_state=employee.state,
		),
		"rows": [
			{
				"name": row.name,
				"role": row.role,
				"label": _(ROLE_LABELS[row.role]),
				"all_orgs": bool(row.all_orgs),
				"company": None if row.all_orgs else row.company,
				"allow_export": bool(row.allow_export),
				"origin": row.origin,
				"missing_in_frappe": not system_manager and _missing_in_frappe(row, held, changed),
				"editable": may_change and may_assign(authority, row),
			}
			for row in sorted(
				stored, key=lambda row: (order.get(row.role, len(order)), not row.all_orgs, row.company or "")
			)
		],
		"held_roles_without_rows": [
			{"role": role, "label": _(ROLE_LABELS[role])}
			for role in ASSIGNABLE_ROLES
			if role in held and not system_manager and role not in {row.role for row in stored}
		],
		"warnings": _access_warnings(stored, held, user_doc.enabled, employee, changed),
		"lock": lock,
		"assignable": {
			"roles": [
				{"role": role, "label": _(ROLE_LABELS[role])}
				for role in ASSIGNABLE_ROLES
				if role in authority["roles"]
			],
			"companies": reach if authority["companies"] is None else authority["companies"],
			"all_orgs": authority["all_orgs"],
		},
		# R6: Leadership is HR Manager on All orgs, offered when it is the
		# caller's to give.
		"presets": ["Leadership"]
		if lock is None and may_assign(authority, {"role": "HR Manager", "all_orgs": 1, "company": ""})
		else [],
		"token": _access_token(stored, user_doc.modified),
		"history": _access_history(user),
	}


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
	plus the scope check above are the permission, as in `save_person_access`.
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

	Resolved through `resolve_read_scope` before anything else is read
	(P6-R6): a caller who may not read `employee` is refused before any
	record is touched, and the refusal is the same `PermissionError` whether
	or not the employee exists (P6-R8) -- it never discloses which.

	Read-only (P6-R3): no field above Employee permlevel 0, no leave
	*reason*, no check-in *coordinates* -- a faster route to what HR already
	reaches in Desk through the roles it holds, never a wider one (P6-R5).
	``company_today`` is the employee's company's own date (the relieve
	form's "today"), for an editor only. ``can_edit`` is false for someone reached only through a company access
	grant (plan 2026-10-14-001 KTD4): every edit endpoint refuses them too.
	"""
	rate_limit_per_user("get_person")
	user = frappe.session.user
	scope = resolve_read_scope(user)
	if scope["kind"] == "none" or not employee_in_admin_scope(employee, scope):
		frappe.throw(_("You are not authorised to view this person."), frappe.PermissionError)
	can_edit = employee_in_admin_scope(employee, resolve_admin_scope(user))

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

	def leave_balances():
		# HRMS's `get_leave_details` checks the session user's read on the
		# Employee, which a company access holder's own self User Permission
		# refuses under strict mode (plan 2026-10-14-001 KTD5). The gate above
		# has already decided; this section alone is read as Administrator,
		# because `_requests_summary` keys its unread flags on the caller.
		with as_administrator():
			return _leave_balances(employee)

	return {
		"employee": section("employee", lambda: _person_profile(employee)),
		"leave_balances": section("leave_balances", leave_balances),
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
		# no other method in this plan hands out this employee's Desk URL --
		# and for someone outside the caller's admin scope.
		"desk_url": get_url_to_form("Employee", employee) if can_edit and _can_open_desk(user) else None,
		"can_edit": can_edit,
		# Plan 2026-10-22-001 U1: only for a caller who may act on it.
		"login": section("login", lambda: _person_login(employee)) if can_edit else None,
		# U3: the leaving HR can act on, and the team that would move.
		"leaving": section("leaving", lambda: _person_leaving(employee)) if can_edit else None,
		"direct_reports": section("direct_reports", lambda: _direct_report_count(employee))
		if can_edit
		else None,
		"company_today": section(
			"company_today",
			lambda: str(company_today(frappe.db.get_value("Employee", employee, "company"))),
		)
		if can_edit
		else None,
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
	return {
		"designations": frappe.get_all("Designation", pluck="name", order_by="name asc"),
		"branches": frappe.get_all("Branch", pluck="name", order_by="name asc"),
		"employment_types": frappe.get_all("Employment Type", pluck="name", order_by="name asc"),
		"grades": frappe.get_all("Employee Grade", pluck="name", order_by="name asc"),
		"shift_types": frappe.get_all("Shift Type", pluck="name", order_by="name asc"),
		"holiday_lists": frappe.get_all("Holiday List", pluck="name", order_by="name asc"),
		**_company_person_options(company),
	}


def _company_person_options(company):
	"""The company-bound pickers: `departments` of ``company`` and the Active
	`people` there to report to (every company's when ``company`` is empty).
	Shared by `get_person_form_options` and `get_new_person_options`; the
	caller has already passed its scope gate."""
	people = frappe.get_all(
		"Employee",
		filters={"status": "Active", **({"company": company} if company else {})},
		fields=["name", "employee_name"],
		order_by="employee_name asc",
		ignore_permissions=True,
	)
	return {
		"departments": frappe.get_all(
			"Department",
			filters={"company": company} if company else {},
			pluck="name",
			order_by="name asc",
		),
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
	# Plan 2026-10-22-001 R15: Relieve is the only path to Left -- it asks
	# for the date, moves the team, and leaves the audit row -- and Desk the
	# only path back. Inactive and Suspended still save here, and an
	# unchanged status (the joining card re-sends it) is no change.
	if doc.status == "Left" and fields.get("status", "Left") != "Left":
		frappe.throw(_("{0} has left. Rehiring is done in Desk.").format(doc.employee_name))
	if fields.get("status") == "Left" and doc.status != "Left":
		frappe.throw(
			_(
				"Use Relieve on {0}'s page to record that they have left: it moves their team and turns off their login."
			).format(doc.employee_name)
		)

	resolved = dict(fields)
	if "reports_to" in fields:
		resolved["reports_to"] = _validate_reports_to(fields["reports_to"], scope)

	_apply_allowed_fields(doc, resolved, _PERSON_ALL_EDITABLE_FIELDS)
	doc.save()

	return _person_profile(employee)


# ---------------------------------------------------------------------------
# Plan 2026-10-22-001 U1: a basic login for an employee, and HelixHR's
# welcome email. Creating a User needs `create` on User, which only System
# Manager holds; a DocPerm for HR would let HR edit any user's roles in Desk
# and bypass access assignments (KTD1). So the scope gate is the permission,
# the roles are fixed here, and the Employee link goes through a normal save.
# ---------------------------------------------------------------------------

_EMPLOYEE_LOGIN_ROLES = ("Employee", "Employee Self Service")


def _person_login(employee):
	"""`get_person`'s login block: `state` (none, active or disabled), the
	login `user`, and `welcome_held` -- an active login made in HelixHR
	whose welcome email has not gone yet."""
	user = frappe.db.get_value("Employee", employee, "user_id")
	enabled = frappe.db.get_value("User", user, "enabled") if user else None
	if enabled is None:
		return {"state": "none", "user": None, "welcome_held": False}
	return {
		"state": "active" if enabled else "disabled",
		"user": user,
		"welcome_held": bool(enabled) and employee in held_welcome_employees([employee]),
	}


def _login_email_holder(employee, email):
	"""Who already holds ``email`` -- a User, or another Employee's company
	email or login -- for the refusal; None when it is free. Named only when
	the holder is an Employee inside the caller's admin scope, else "someone
	else": the refusal must not tell HR who works at a company it cannot
	see. Rehire and linking an existing account stay in Desk (R6)."""
	scope = resolve_admin_scope(frappe.session.user)

	def named(holder):
		if holder and employee_in_admin_scope(holder.name, scope):
			return holder.employee_name
		return _("someone else")

	if frappe.db.exists("User", email):
		return named(frappe.db.get_value("Employee", {"user_id": email}, ["name", "employee_name"], as_dict=True))
	other = frappe.get_all(
		"Employee",
		filters={"name": ["!=", employee]},
		or_filters={"company_email": email, "user_id": email},
		fields=["name", "employee_name"],
		limit=1,
	)
	return named(other[0]) if other else None


def _send_employee_welcome(employee):
	"""Queue the welcome email to the company email: "sent", "held" while no
	Microsoft sign-in is enabled (the login has no password), or "not_sent"
	when the template is switched off or the send failed (logged). Never
	raises, so a failed welcome cannot roll back the login (R8)."""
	if not microsoft_sign_in_enabled():
		return "held"
	row = frappe.db.get_value(
		"Employee", employee, ["first_name", "company", "company_email", "user_id"], as_dict=True
	)
	portal_url = get_url("/helixhr")
	queued = 0
	try:
		queued = send_notification(
			"employee_welcome",
			[row.company_email],
			{
				# KTD8: a brand-new address may not resolve its company yet.
				"recipient_first_name": row.first_name,
				"company": row.company,
				"login_email": row.user_id,
				"portal_url": portal_url,
				"action_url": portal_url,
			},
			reference_doctype="Employee",
			reference_name=employee,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "HelixHR employee_welcome mail failed")
	if not queued:
		return "not_sent"
	_employee_note(
		employee,
		WELCOME_SENT_MARK,
		_("{0} sent the welcome email to {1}.").format(
			frappe.utils.get_fullname(frappe.session.user), row.company_email
		),
	)
	return "sent"


def _assert_can_administer(employee):
	"""`save_person`'s gate: refused before anything is read, the same way
	whether or not the employee exists."""
	scope = resolve_admin_scope(frappe.session.user)
	if scope["kind"] == "none" or not employee_in_admin_scope(employee, scope):
		frappe.throw(_("You are not authorised to view this person."), frappe.PermissionError)


@frappe.whitelist(methods=["POST"])
def create_employee_login(employee):
	"""Give ``employee`` a login (R4, R5): their company email, holding only
	Employee and Employee Self Service, no password, no Frappe welcome
	email, and the Employee and Company User Permissions -- the state
	ERPNext's own `create_user` reaches with "Create User Permission" on.

	Refused, with nothing written: a caller outside their admin scope, a
	Left employee, no company email, a different login already linked, and
	an email already held by a User or another Employee (R6). The same
	email already linked returns that login, with no second mail.

	Returns `{"login": <get_person's login block>, "created", "welcome"}`,
	`welcome` being "sent", "held" or "not_sent" (None when nothing was
	created)."""
	rate_limit_per_user("create_employee_login")
	_assert_can_administer(employee)

	doc = frappe.get_doc("Employee", employee)
	if doc.status == "Left":
		frappe.throw(_("{0} has left, so they can't have a login.").format(doc.employee_name))
	email = (doc.company_email or "").strip().lower()
	if not email:
		frappe.throw(_("Add a company email for {0} first: it becomes their login.").format(doc.employee_name))
	if doc.user_id:
		if doc.user_id.lower() == email:
			return {"login": _person_login(employee), "created": False, "welcome": None}
		frappe.throw(_("{0} already signs in as {1}.").format(doc.employee_name, doc.user_id))
	holder = _login_email_holder(employee, email)
	if holder:
		frappe.throw(
			_("{0} already belongs to {1}. Linking an existing account is done in Desk.").format(email, holder)
		)

	# ERPNext's own order (`employee.create_user`): the User hook
	# `validate_employee_role` strips both roles from a User no Employee
	# names yet, so the link is written first, inside this transaction.
	frappe.db.set_value("Employee", employee, "user_id", email, update_modified=False)
	# Documented `ignore_permissions` (KTD1): the gate above is the
	# permission, and nothing from the request reaches the roles.
	frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": doc.first_name,
			"middle_name": doc.middle_name,
			"last_name": doc.last_name,
			"gender": doc.gender,
			"birth_date": doc.date_of_birth,
			"phone": doc.cell_number,
			"enabled": 1,
			"send_welcome_email": 0,
			"roles": [{"role": role} for role in _EMPLOYEE_LOGIN_ROLES],
		}
	).insert(ignore_permissions=True)
	# The caller's own permissions: Employee's validation and on_update
	# (user sync, User Permissions, HelixHR's hooks) run unchanged.
	doc.user_id = email
	doc.create_user_permission = 1
	doc.save()
	# `update_user_permissions` adds these only when the checkbox changed;
	# ERPNext's `create_user` adds them either way.
	add_user_permission("Employee", doc.name, email, ignore_permissions=True)
	add_user_permission("Company", doc.company, email, ignore_permissions=True)
	_employee_note(
		employee,
		LOGIN_CREATED_MARK,
		_("{0} created a login for {1}.").format(frappe.utils.get_fullname(frappe.session.user), email),
	)

	welcome = _send_employee_welcome(employee)
	return {"login": _person_login(employee), "created": True, "welcome": welcome}


@frappe.whitelist(methods=["POST"])
def send_employee_welcome(employee):
	"""Send (or resend) the welcome email for ``employee``'s active login
	(R7). Held, not sent, while no Microsoft sign-in is enabled.

	Returns `{"login": <get_person's login block>, "welcome"}` as
	`create_employee_login` does."""
	rate_limit_per_user("send_employee_welcome")
	_assert_can_administer(employee)
	row = frappe.db.get_value("Employee", employee, ["employee_name", "company_email"], as_dict=True)
	if _person_login(employee)["state"] != "active":
		frappe.throw(_("{0} has no active login.").format(row.employee_name))
	if not row.company_email:
		frappe.throw(_("Add a company email for {0} first.").format(row.employee_name))
	welcome = _send_employee_welcome(employee)
	return {"login": _person_login(employee), "welcome": welcome}


# ---------------------------------------------------------------------------
# Plan 2026-10-22-001 U2: adding an employee from People (R1-R4). The
# Employee is inserted with the caller's own permissions (R2), so ERPNext's
# and HelixHR's validation (approver derivation, date rules) runs unchanged;
# the company and the manager are checked against the admin scope first.
# ---------------------------------------------------------------------------

# The add form's write surface; anything else in the payload is ignored, so
# `status`, `user_id` and every permlevel field stay out of reach (R1).
_NEW_PERSON_FIELDS = (
	"first_name",
	"last_name",
	"gender",
	"date_of_birth",
	"date_of_joining",
	"company",
	"designation",
	"department",
	"reports_to",
	"company_email",
)
# ERPNext's required fields, refused in plain words before the insert would.
_NEW_PERSON_REQUIRED = {
	"first_name": "Enter their first name.",
	"gender": "Choose their gender.",
	"date_of_birth": "Enter their date of birth.",
	"date_of_joining": "Enter their joining date.",
	"company": "Choose the company they join.",
}


def _can_add_people(user):
	"""`create_employee`'s gate, asked by the bootstrap too: an admin scope
	(HR Manager or System Manager) and Frappe's own create on Employee, which
	the insert runs under (R2). ERPNext grants System Manager no Employee
	DocPerm, so a site without a custom one is not offered a form that would
	refuse it."""
	return resolve_admin_scope(user)["kind"] != "none" and bool(
		frappe.has_permission("Employee", "create", user=user)
	)


def _new_person_date(values, field, label):
	try:
		return getdate(values[field])
	except Exception:
		frappe.throw(_("Enter the {0} as a date.").format(_(label)))


@frappe.whitelist()
def get_new_person_options():
	"""The add form's option lists, in one call: the `companies` inside the
	caller's admin scope, `genders`, `designations`, and `by_company` --
	each company's `departments` and Active `people` to report to.

	Refused (`PermissionError`) for a caller who may not add people."""
	rate_limit_per_user("get_new_person_options")
	if not _can_add_people(frappe.session.user):
		frappe.throw(_("You are not authorised to add people."), frappe.PermissionError)
	companies = _companies_in_admin_scope()
	return {
		"companies": companies,
		"genders": frappe.get_all("Gender", pluck="name", order_by="name asc"),
		"designations": frappe.get_all("Designation", pluck="name", order_by="name asc"),
		"by_company": {company: _company_person_options(company) for company in companies},
	}


@frappe.whitelist(methods=["POST"])
def create_employee(create_login=0, **fields):
	"""Add an Active Employee (R1-R3) from `_NEW_PERSON_FIELDS`, then, with
	``create_login``, their login through `create_employee_login` (R4).

	Refused in plain words, with nothing written: a caller who may not add
	people, a company or manager outside their scope (`PermissionError`), a
	missing required field, a date of birth in the future or after the
	joining date, ``create_login`` without a company email, and a likely
	duplicate -- same first and last name, date of birth and company, still
	Active -- named in the refusal.

	The login runs in its own savepoint: a refused or failed login never
	takes the new employee with it. Returns `{"employee", "employee_name",
	"login": <get_person's login block>, "login_created": bool,
	"login_error": str or None, "welcome": "sent", "held", "not_sent" or
	None}`."""
	rate_limit_per_user("create_employee")
	if not _can_add_people(frappe.session.user):
		frappe.throw(_("You are not authorised to add people."), frappe.PermissionError)
	scope = resolve_admin_scope(frappe.session.user)

	values = {
		field: (fields[field].strip() if isinstance(fields[field], str) else fields[field]) or None
		for field in _NEW_PERSON_FIELDS
		if field in fields
	}
	for field, message in _NEW_PERSON_REQUIRED.items():
		if not values.get(field):
			frappe.throw(_(message))
	company = values["company"]
	if not frappe.db.exists("Company", company) or not scope_covers(scope, company):
		frappe.throw(_("You are not authorised to add people to {0}.").format(company), frappe.PermissionError)
	values["reports_to"] = _validate_reports_to(values.get("reports_to"), scope)
	if values.get("company_email"):
		values["company_email"] = values["company_email"].lower()
	elif cint(create_login):
		frappe.throw(_("Add a company email to create a login, or turn off Create login now."))

	# ERPNext's `validate_date`, said the way HR would.
	born = _new_person_date(values, "date_of_birth", "date of birth")
	joined = _new_person_date(values, "date_of_joining", "joining date")
	if born > getdate():
		frappe.throw(_("The date of birth can't be in the future."))
	if joined < born:
		frappe.throw(_("The joining date can't be before the date of birth."))

	duplicate = frappe.get_all(
		"Employee",
		filters={
			"first_name": values["first_name"],
			"last_name": values.get("last_name") or ["is", "not set"],
			"date_of_birth": born,
			"company": company,
			"status": "Active",
		},
		fields=["name", "employee_name"],
		limit=1,
		ignore_permissions=True,
	)
	if duplicate:
		frappe.throw(
			_(
				"{0} ({1}) already works at {2} with this name and date of birth. Open their page instead of adding them again."
			).format(duplicate[0].employee_name, duplicate[0].name, company)
		)

	doc = frappe.new_doc("Employee")
	_apply_allowed_fields(doc, values, _NEW_PERSON_FIELDS)
	doc.status = "Active"
	doc.insert()
	_employee_note(
		doc.name,
		"helixhr:employee_added",
		_("{0} added {1} to {2}.").format(
			frappe.utils.get_fullname(frappe.session.user), doc.employee_name, company
		),
	)

	login_created, login_error, welcome = False, None, None
	if cint(create_login):
		frappe.db.savepoint("helixhr_new_employee_login")
		try:
			result = create_employee_login(doc.name)
			login_created, welcome = result["created"], result["welcome"]
		except Exception as exc:
			frappe.db.rollback(save_point="helixhr_new_employee_login")
			frappe.clear_last_message()
			# Our own refusals are worded for the screen; anything else is
			# logged, not echoed (`apply_approver_cleanup`'s rule).
			if isinstance(exc, frappe.ValidationError | frappe.PermissionError):
				login_error = " ".join(frappe.utils.strip_html(str(exc)).split())
			else:
				frappe.log_error(
					title="HelixHR new employee login failed", reference_doctype="Employee", reference_name=doc.name
				)
			login_error = login_error or _("The login could not be created. Try again from their page.")

	return {
		"employee": doc.name,
		"employee_name": doc.employee_name,
		"login": _person_login(doc.name),
		"login_created": login_created,
		"login_error": login_error,
		"welcome": welcome,
	}


# ---------------------------------------------------------------------------
# Plan 2026-10-22-001 U3: relieving an employee, today or on a future date.
# One open `HelixHR Leaving` row per employee holds the schedule (KTD2);
# scheduling also writes `relieving_date` and the reason on the still-Active
# Employee (KTD3), and `tasks.apply_leaving` does the rest on the day.
# ---------------------------------------------------------------------------

_LEAVING_FIELDS = [
	"name",
	"status",
	"leaving_date",
	"reason",
	"new_manager",
	"new_manager_name",
	"error",
	"scheduled_by",
	"applied_on",
	"modified",
]


def _person_leaving(employee):
	"""`get_person`'s leaving block: the open (Pending or Failed) leaving,
	else for someone who has left the one that applied, else None.
	`modified` is the token `relieve_employee`, `cancel_leaving` and
	`retry_leaving` take as ``expected_modified``."""
	row = frappe.db.get_value(
		LEAVING_DOCTYPE,
		{"employee": employee, "status": ["in", OPEN_LEAVING_STATUSES]},
		_LEAVING_FIELDS,
		as_dict=True,
	)
	if not row and frappe.db.get_value("Employee", employee, "status") == "Left":
		applied = frappe.get_all(
			LEAVING_DOCTYPE,
			filters={"employee": employee, "status": "Applied"},
			fields=_LEAVING_FIELDS,
			order_by="applied_on desc",
			limit=1,
		)
		row = applied[0] if applied else None
	if row:
		row["scheduled_by_name"] = frappe.utils.get_fullname(row.scheduled_by)
	return row


def _direct_report_count(employee):
	"""The Active people reporting to ``employee`` now: who moves if they leave."""
	return frappe.db.count("Employee", {"reports_to": employee, "status": "Active"})


def _open_leaving(employee):
	name = frappe.db.get_value(
		LEAVING_DOCTYPE, {"employee": employee, "status": ["in", OPEN_LEAVING_STATUSES]}
	)
	return frappe.get_doc(LEAVING_DOCTYPE, name) if name else None


def _leaving_gate(employee):
	"""The scope gate, the employee row lock (two HR tabs serialise), and
	R14's "not yourself". Returns the Employee."""
	_assert_can_administer(employee)
	_lock_employee(employee)
	doc = frappe.get_doc("Employee", employee)
	if doc.user_id and doc.user_id == frappe.session.user:
		frappe.throw(_("You can't relieve yourself. Ask another HR manager."))
	return doc


def _assert_leaving_unchanged(open_row, expected_modified):
	"""``expected_modified`` is the open leaving's `modified` as the form saw
	it, empty when it saw none: a leaving scheduled, changed or cancelled
	from another tab since is refused rather than overwritten."""
	current = open_row.modified if open_row else None
	if bool(expected_modified) != bool(current) or (
		current and get_datetime(expected_modified) != get_datetime(current)
	):
		frappe.throw(_("Somebody changed this while you were looking at it. Reload and try again."))


@frappe.whitelist(methods=["POST"])
def relieve_employee(employee, leaving_date, reason=None, new_manager=None, expected_modified=None):
	"""Relieve ``employee`` (R9-R12, R14): ``leaving_date`` is their first
	day without access, ``new_manager`` takes every Active direct report
	(required when there are any). A date on or before the company's own
	today applies now; a later one is scheduled, and the same call with the
	open leaving's ``expected_modified`` changes it.

	Refused in plain words, with nothing written: a caller outside their
	admin scope or relieving themselves, someone not Active, a missing
	reason, a date before joining, and a new manager who is the leaver, not
	Active, leaving on or before the same date, below the leaver's direct
	reports, or outside the caller's scope (`PermissionError`).

	Returns `{"leaving": <get_person's leaving block>, "applied": bool}`."""
	rate_limit_per_user("relieve_employee")
	scope = resolve_admin_scope(frappe.session.user)
	doc = _leaving_gate(employee)
	if doc.status != "Active":
		frappe.throw(
			_("{0} is {1}. Only an Active employee can be relieved.").format(doc.employee_name, _(doc.status))
		)
	open_row = _open_leaving(employee)
	_assert_leaving_unchanged(open_row, expected_modified)

	reason = (reason or "").strip()
	if not reason:
		frappe.throw(_("Say why {0} is leaving.").format(doc.employee_name))
	if not leaving_date:
		frappe.throw(_("Choose the leaving date: the first day {0} no longer has access.").format(doc.employee_name))
	leaving_date = getdate(leaving_date)
	if leaving_date < getdate(doc.date_of_joining):
		frappe.throw(
			_("{0} joined on {1}, so the leaving date can't be before that.").format(
				doc.employee_name, frappe.utils.formatdate(doc.date_of_joining)
			)
		)
	new_manager = _validate_reports_to(new_manager or None, scope)
	problem = leaving_manager_problem(employee, new_manager, leaving_date)
	if problem:
		frappe.throw(problem)
	# The other side of `leaving_manager_problem`: someone set to take over a
	# team cannot leave on or before that team moves, or that leaving fails.
	taking_over = frappe.db.get_value(
		LEAVING_DOCTYPE,
		{
			"new_manager": employee,
			"employee": ["!=", employee],
			"status": ["in", OPEN_LEAVING_STATUSES],
			"leaving_date": [">=", leaving_date],
		},
		["employee", "leaving_date"],
		as_dict=True,
		order_by="leaving_date asc",
	)
	if taking_over:
		other = frappe.db.get_value("Employee", taking_over.employee, "employee_name")
		frappe.throw(
			_(
				"{0} is taking over {1}'s team on {2}, so they can't leave before then. Choose a later date, or change {1}'s new manager first."
			).format(doc.employee_name, other, frappe.utils.formatdate(taking_over.leaving_date))
		)
	reports = _direct_report_count(employee)
	if reports and not new_manager:
		frappe.throw(
			_("{0} has {1} people reporting to them. Choose who they report to next.").format(
				doc.employee_name, reports
			)
		)

	row = open_row or frappe.new_doc(LEAVING_DOCTYPE)
	row.update(
		{
			"employee": employee,
			"company": doc.company,
			"leaving_date": leaving_date,
			"reason": reason,
			"new_manager": new_manager,
			"status": "Pending",
			"error": None,
			"scheduled_by": frappe.session.user,
		}
	)
	# Documented `ignore_permissions`: only System Manager reads this
	# DocType in Desk; the gate above is the permission.
	row.save(ignore_permissions=True)

	if leaving_date <= company_today(doc.company):
		apply_leaving(row.name)
		return {"leaving": _person_leaving(employee), "applied": True}

	# KTD3: headcount, leavers and the employment span already read a future
	# `relieving_date`. The caller's own permissions, like `save_person`.
	doc.relieving_date = leaving_date
	doc.reason_for_leaving = reason
	save_employee_plainly(doc)
	actor = frappe.utils.get_fullname(frappe.session.user)
	when = frappe.utils.formatdate(leaving_date)
	if open_row:
		_employee_note(
			employee,
			"helixhr:leaving_changed",
			_("{0} changed {1}'s leaving to {2}.").format(actor, doc.employee_name, when),
		)
	else:
		_employee_note(
			employee,
			"helixhr:leaving_scheduled",
			_("{0} scheduled {1}'s leaving for {2}.").format(actor, doc.employee_name, when),
		)
	return {"leaving": _person_leaving(employee), "applied": False}


@frappe.whitelist(methods=["POST"])
def cancel_leaving(employee, expected_modified=None):
	"""Cancel ``employee``'s scheduled or failed leaving (R12) and clear the
	`relieving_date` and reason it wrote -- unless they were already set
	Left in Desk, whose date and reason stay. An applied leaving is not
	undone here. Returns `{"leaving": <get_person's leaving block>}`."""
	rate_limit_per_user("cancel_leaving")
	doc = _leaving_gate(employee)
	open_row = _open_leaving(employee)
	if not open_row:
		frappe.throw(_("No leaving is scheduled for {0}.").format(doc.employee_name))
	_assert_leaving_unchanged(open_row, expected_modified)

	open_row.status = "Cancelled"
	open_row.save(ignore_permissions=True)
	# Someone already set Left in Desk keeps the date and reason recorded
	# there; only the portal's own schedule is cleared.
	if doc.status != "Left":
		doc.relieving_date = None
		doc.reason_for_leaving = None
		save_employee_plainly(doc)
	_employee_note(
		employee,
		"helixhr:leaving_cancelled",
		_("{0} cancelled {1}'s leaving on {2}.").format(
			frappe.utils.get_fullname(frappe.session.user),
			doc.employee_name,
			frappe.utils.formatdate(open_row.leaving_date),
		),
	)
	return {"leaving": _person_leaving(employee)}


@frappe.whitelist(methods=["POST"])
def retry_leaving(employee, expected_modified=None):
	"""Apply ``employee``'s open leaving now (R13): after fixing why it
	failed, or when the job has not run. Refused before its date, and in
	plain words when it still cannot apply (the row keeps its last error).
	Returns `{"leaving": <get_person's leaving block>, "applied": bool}`:
	false when the person had already been set Left in Desk, which closes
	the leaving instead (`tasks.apply_leaving`)."""
	rate_limit_per_user("retry_leaving")
	doc = _leaving_gate(employee)
	open_row = _open_leaving(employee)
	if not open_row:
		frappe.throw(_("No leaving is waiting for {0}.").format(doc.employee_name))
	_assert_leaving_unchanged(open_row, expected_modified)
	if getdate(open_row.leaving_date) > company_today(open_row.company):
		frappe.throw(
			_("{0}'s leaving is set for {1}. It applies on that day.").format(
				doc.employee_name, frappe.utils.formatdate(open_row.leaving_date)
			)
		)
	applied = apply_leaving(open_row.name)
	return {"leaving": _person_leaving(employee), "applied": applied.status == "Applied"}


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

# Plan 2026-11-01-001 U1/U2: the companies besides its own a project is
# "Open to everyone" for. Only `set_project_open_to_all` writes the table.
_OPEN_COMPANY_DOCTYPE = "HelixHR Project Company"
_OPEN_COMPANIES_FIELD = "helixhr_open_companies"

_PROJECT_SEARCH_FIELDS = (
	"name",
	"project_name",
	"status",
	"company",
	# Plan 2026-10-31-001 U1: the list table's columns and filters.
	"project_type",
	"priority",
	"helixhr_is_billable",
	"expected_end_date",
	"helixhr_open_to_all",
)

_PROJECT_FIELDS = (
	"name",
	"project_name",
	"status",
	"company",
	"expected_start_date",
	"expected_end_date",
	"helixhr_is_billable",
	"helixhr_open_to_all",
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
		"project_type": row.project_type,
		"priority": row.priority,
		"billable": bool(row.helixhr_is_billable),
		"expected_end_date": row.expected_end_date,
		"open_to_all": bool(row.helixhr_open_to_all),
	}


def _project_list_counts(names):
	"""`({project: members}, {project: open tasks})` for the list's rows, in
	two grouped reads (plan 2026-10-17-001 U17, HR-15). "Open" is every task
	status but `_CLOSED_TASK_STATUSES`, the same rule `get_project` lists by.
	Called only after the caller's project scope has picked `names`."""
	if not names:
		return {}, {}
	members = frappe.get_all(
		"Project User",
		filters={"parenttype": "Project", "parent": ["in", names]},
		fields=["parent", {"COUNT": "name"}],
		group_by="parent",
		ignore_permissions=True,
	)
	tasks = frappe.get_all(
		"Task",
		filters={"project": ["in", names], "status": ["not in", _CLOSED_TASK_STATUSES]},
		fields=["project", {"COUNT": "name"}],
		group_by="project",
		ignore_permissions=True,
	)
	return (
		{row.parent: _aggregate_count(row) for row in members},
		{row.project: _aggregate_count(row) for row in tasks},
	)


@frappe.whitelist()
def search_projects():
	"""Every project this caller administers, per `resolve_project_scope`
	(P7-R1-R4): unscoped for System Manager, the caller's own company for an
	HR Manager, exactly the projects a HelixHR Delivery Manager is a member
	of, refused for anyone else.

	No `query`/paging parameters -- unlike `search_people`'s employee search,
	a project list is small enough per caller (a company, or one person's
	memberships) that a page control would be UI the plan never asked for.

	Since plan 2026-10-14-001 the list is `resolve_project_read_scope`'s --
	an All-data company access grant adds every project in its companies --
	and each row says whether the caller may change it (``can_edit``, from
	`resolve_project_scope`, which every project write still enforces).
	``can_create`` mirrors `create_project`'s own gate."""
	rate_limit_per_user("search_projects")
	user = frappe.session.user
	scope = resolve_project_read_scope(user)
	if scope["kind"] == "none":
		frappe.throw(_("You are not authorised to view projects here."), frappe.PermissionError)

	write = resolve_project_scope(user)

	def can_edit(row):
		return scope_covers(write, row.company)

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
		members, open_tasks = _project_list_counts([row.name for row in rows])
		projects = [
			{
				**_project_search_projection(row),
				"can_edit": can_edit(row),
				"member_count": members.get(row.name, 0),
				"open_task_count": open_tasks.get(row.name, 0),
			}
			for row in rows
		]

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
		"can_create": write["kind"] != "none",
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

	# Display only: a Left member is still named, from their newest row.
	employees = {
		user: row if row.state == "active" else row.latest
		for user, row in active_employees_for_users(logins).items()
		if row.state != "none"
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

	Resolved through `resolve_project_read_scope` before anything else is
	read: a caller outside their scope is refused with `_PROJECT_NOT_FOUND`,
	the same message a nonexistent project id gets, so this can never become
	an oracle for which project ids exist. ``can_edit`` is the write scope's
	answer (plan 2026-10-14-001 KTD4).

	Plan 2026-11-01-001 U2: ``company``, and for a caller who may edit,
	``open_companies`` (`_covered_companies`: who can already book without
	being a member), ``open_company_choices`` (the other companies the
	caller may open it to), ``stored_open_companies`` (every stored extra
	company, whatever the project's status, so the dialog starts from what
	is saved) and ``fixed_open_companies`` (those outside the caller's write
	scope, which their edits keep).
	"""
	rate_limit_per_user("get_project")
	scope = resolve_project_read_scope(frappe.session.user)
	if scope["kind"] == "none" or not project_in_scope(project, scope):
		frappe.throw(_(_PROJECT_NOT_FOUND), frappe.PermissionError)

	data = frappe.db.get_value("Project", project, list(_PROJECT_FIELDS), as_dict=True)
	write = resolve_project_scope(frappe.session.user)
	can_edit = project_in_scope(project, write)
	stored = _project_open_rows(project) if can_edit else []

	return {
		"name": data.name,
		"project_name": data.project_name,
		"status": data.status,
		"company": data.company,
		"billable": bool(data.helixhr_is_billable),
		"open_to_all": bool(data.helixhr_open_to_all),
		"priority": data.priority,
		"project_type": data.project_type,
		"expected_start_date": data.expected_start_date,
		"expected_end_date": data.expected_end_date,
		"tasks": _project_open_tasks(project),
		"members": _project_members(project),
		"can_edit": can_edit,
		# Plan 2026-10-31-001 U5: the bulk-add picker's department choices,
		# only for a caller who may add people.
		"departments": _project_departments(write) if can_edit else [],
		"open_companies": _covered_companies(data, stored) if can_edit else [],
		"open_company_choices": _open_company_choices(write, data.company) if can_edit else [],
		"stored_open_companies": stored,
		"fixed_open_companies": [company for company in stored if not scope_covers(write, company)],
	}


def _project_open_rows(project):
	"""``project``'s stored extra open companies, in row order."""
	return frappe.get_all(
		_OPEN_COMPANY_DOCTYPE,
		filters={"parent": project, "parenttype": "Project", "parentfield": _OPEN_COMPANIES_FIELD},
		pluck="company",
		order_by="idx asc",
		ignore_permissions=True,
	)


def _covered_companies(project, stored):
	"""Plan 2026-11-01-001 KTD "covered": the companies whose Active employees
	can already book ``project`` without being members -- its own company
	plus the ``stored`` extra ones, own first -- while it is Open and "Open
	to everyone"; otherwise none. ``project`` needs ``company``, ``status``
	and ``helixhr_open_to_all``. `add_project_members` skips by it and
	`get_project` reports it, so the picker and the server agree."""
	if project.status != "Open" or not cint(project.helixhr_open_to_all):
		return []
	return list(dict.fromkeys([project.company, *stored]))


def _open_company_choices(scope, own):
	"""The companies an editor may add to a project's open list: every one
	``scope`` (`resolve_project_scope`) covers, or every Company when
	unscoped, minus the project's own company ``own``."""
	companies = read_companies(scope)
	if companies is None:
		companies = frappe.get_all("Company", pluck="name", order_by="name asc")
	return sorted(company for company in companies if company != own)


def _project_departments(scope):
	"""Departments of the companies ``scope`` (`resolve_project_scope`)
	covers, for the "add a whole department" picker. The company-less tree
	root is left out: it would mean every company's people."""
	companies = read_companies(scope)
	if companies == []:
		return []
	return frappe.get_all(
		"Department",
		filters={"company": ["in", companies] if companies is not None else ["is", "set"]},
		fields=["name", "department_name", "company", "parent_department"],
		order_by="department_name asc",
		ignore_permissions=True,
	)


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
	own Employee record, and must be inside `resolve_project_scope` (plan
	2026-10-19-001 U4: a Delivery Manager or HR Manager whose orgs no longer
	include their own company is refused), so a company named in the request body
	(accepted here only via `**kwargs`, then ignored, the same pattern
	`get_dashboard` uses to swallow extra caller input) can never steer
	which company the project lands in.

	`priority` and `project_type` (P8-U2) are the two fields Desk asks for
	that the portal's create form did not -- both optional, both left to
	`doc.insert()`'s own validation (`priority` against `Project`'s own
	Select options, `project_type` as a plain Link) rather than re-checked
	here, so the two can never drift from what the doctype itself accepts.

	The creator is added as a `Project User` in the same operation, so they
	can book time on it.
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
	if not scope_covers(scope, company):
		frappe.throw(_("You are not authorised to create projects here."), frappe.PermissionError)

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
# Plan 2026-10-31-001 U1/U4/U5: bulk task add, "Open to everyone", bulk
# member add. Gated by the same `resolve_project_scope` / `project_in_scope`
# pair as the writes above.
# ---------------------------------------------------------------------------

_BULK_TASK_LIMIT = 100
# `Task.subject` is a Data field.
_TASK_SUBJECT_MAX = 140
_BULK_MEMBER_LIMIT = 500


def _project_write_gate(project):
	"""Refuse ``project`` unless the caller's write scope covers it, with the
	one not-found wording (no oracle for which ids exist)."""
	scope = resolve_project_scope(frappe.session.user)
	if scope["kind"] == "none" or not project_in_scope(project, scope):
		frappe.throw(_(_PROJECT_NOT_FOUND), frappe.PermissionError)
	return scope


def _string_list(value, label):
	"""A JSON list of strings from the request, or a plain refusal."""
	if isinstance(value, str):
		value = frappe.parse_json(value)
	if value is None:
		return []
	if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
		frappe.throw(_("{0} must be a list of text lines.").format(label))
	return value


@frappe.whitelist(methods=["POST"])
def add_tasks(project, subjects):
	"""Add one Open task per non-blank line of ``subjects`` to ``project``
	(plan 2026-10-31-001 U1, R4), all or nothing.

	Lines are trimmed; blanks are dropped; a line that repeats an earlier
	line or an existing open task (case-insensitive) is dropped and reported
	in ``duplicates``. More than 100 lines, or one longer than 140
	characters (named by its line number), refuses the whole call. Refused
	on a project that is not Open. Every insert runs in one savepoint, so a
	failure leaves no task behind. Subjects go through the same
	`Task.insert` as `save_task`, so Frappe's own HTML sanitising applies.
	"""
	rate_limit_per_user("add_tasks")
	_project_write_gate(project)
	lines = _string_list(subjects, _("Tasks"))

	if frappe.db.get_value("Project", project, "status") != "Open":
		frappe.throw(_("Tasks can only be added to an Open project."))

	numbered = [(number, line.strip()) for number, line in enumerate(lines, start=1) if line.strip()]
	if not numbered:
		frappe.throw(_("Give at least one task a subject."))
	if len(numbered) > _BULK_TASK_LIMIT:
		frappe.throw(_("Add at most {0} tasks at a time.").format(_BULK_TASK_LIMIT))
	for number, subject in numbered:
		if len(subject) > _TASK_SUBJECT_MAX:
			frappe.throw(_("Line {0} is longer than {1} characters.").format(number, _TASK_SUBJECT_MAX))

	existing = frappe.get_all(
		"Task",
		filters={"project": project, "status": ["not in", _CLOSED_TASK_STATUSES]},
		pluck="subject",
		ignore_permissions=True,
	)
	seen = {subject.casefold() for subject in existing if subject}
	fresh, duplicates = [], []
	for _number, subject in numbered:
		if subject.casefold() in seen:
			duplicates.append(subject)
			continue
		seen.add(subject.casefold())
		fresh.append(subject)

	savepoint = "helixhr_add_tasks"
	frappe.db.savepoint(savepoint)
	try:
		created = [
			frappe.get_doc(
				{"doctype": "Task", "project": project, "subject": subject, "status": "Open"}
			).insert(ignore_permissions=True)
			for subject in fresh
		]
	except Exception:
		frappe.db.rollback(save_point=savepoint)
		raise
	frappe.db.release_savepoint(savepoint)

	return {"tasks": [_task_projection(doc) for doc in created], "duplicates": duplicates}


@frappe.whitelist(methods=["POST"])
def set_project_open_to_all(project, value, companies=None):
	"""Mark ``project`` "Open to everyone" or not (plan 2026-10-31-001 U4,
	R8). Booking reads the flag in `_my_project_names`; nothing else is
	written -- no Project User row, User Permission or DocShare -- so it
	never widens access to the project itself. Turning it off follows the
	member-removal rule: a draft week still naming the project is refused
	on its next save.

	Plan 2026-11-01-001 U2 (R1, R2): ``companies`` names the extra companies
	besides the project's own, each inside the caller's write scope (else
	the not-here refusal and nothing is written); the project's own company
	is dropped from it. While on, the list replaces only the rows the
	caller's scope covers and keeps the rest. Off clears every row, so a
	later turn-on starts from exactly what that editor picks: with
	``companies`` left out, own company only. Left out while already on,
	the rows stay as they are. A billable project is refused any turn-on
	or edit, and may only be turned off. Written field-and-rows under a row
	lock, not as a document save (see the comment below).

	Returns ``open_to_all``, ``open_companies`` (`_covered_companies`),
	``stored_open_companies`` (the saved rows) and ``fixed_open_companies``
	(rows outside the caller's scope)."""
	rate_limit_per_user("set_project_open_to_all")
	scope = _project_write_gate(project)
	open_to_all = cint(value) == 1
	picked = None if companies is None else _string_list(companies, _("Companies"))

	# Not a document save: ERPNext's Project.validate would copy template
	# tasks, recompute status from task progress, mail pending invitations
	# and re-check legacy dates -- none of it this switch's business. The
	# row lock serialises two editors; `project_validate` keeps any other
	# save path off the table.
	doc = frappe.db.get_value(
		"Project",
		project,
		["company", "status", "helixhr_is_billable", "helixhr_open_to_all"],
		as_dict=True,
		for_update=True,
	)
	was_open = bool(cint(doc.helixhr_open_to_all))
	stored = _project_open_rows(project)
	if not open_to_all:
		rows = []
	else:
		if cint(doc.helixhr_is_billable):
			frappe.throw(_("A billable project can't be opened to everyone."))
		for company in picked or []:
			if not scope_covers(scope, company) or not frappe.db.exists("Company", company):
				frappe.throw(_("That company isn't here."))
		if picked is None:
			rows = stored if was_open else []
		else:
			kept = [company for company in stored if not scope_covers(scope, company)] if was_open else []
			rows = [*kept, *picked]
		rows = [company for company in dict.fromkeys(rows) if company != doc.company]

	frappe.db.delete(
		_OPEN_COMPANY_DOCTYPE,
		{"parent": project, "parenttype": "Project", "parentfield": _OPEN_COMPANIES_FIELD},
	)
	for idx, company in enumerate(rows, start=1):
		frappe.get_doc(
			{
				"doctype": _OPEN_COMPANY_DOCTYPE,
				"parent": project,
				"parenttype": "Project",
				"parentfield": _OPEN_COMPANIES_FIELD,
				"idx": idx,
				"company": company,
			}
		).db_insert()
	frappe.db.set_value("Project", project, "helixhr_open_to_all", int(open_to_all))
	frappe.clear_document_cache("Project", project)
	doc.helixhr_open_to_all = int(open_to_all)
	return {
		"open_to_all": open_to_all,
		"open_companies": _covered_companies(doc, rows),
		"stored_open_companies": rows,
		"fixed_open_companies": [company for company in rows if not scope_covers(scope, company)],
	}


def _department_candidates(department, scope):
	"""Active employees in ``department`` and its sub-departments, in a
	company ``scope`` covers. Refuses a department outside scope, or one
	resolving to more than `_BULK_MEMBER_LIMIT` people."""
	if not isinstance(department, str):
		frappe.throw(_("That department isn't here."))
	dept = frappe.db.get_value(
		"Department", department, ["lft", "rgt", "company", "department_name"], as_dict=True
	)
	if not dept or (dept.company and not scope_covers(scope, dept.company)):
		frappe.throw(_("That department isn't here."))
	subtree = frappe.get_all(
		"Department", filters={"lft": [">=", dept.lft], "rgt": ["<=", dept.rgt]}, pluck="name"
	)
	filters = {"department": ["in", subtree], "status": "Active"}
	companies = read_companies(scope)
	if companies is not None:
		filters["company"] = ["in", companies]
	if frappe.db.count("Employee", filters) > _BULK_MEMBER_LIMIT:
		frappe.throw(
			_("{0} has more than {1} people. Add a smaller department.").format(
				dept.department_name or department, _BULK_MEMBER_LIMIT
			)
		)
	return frappe.get_all("Employee", filters=filters, pluck="name", order_by="employee_name asc")


_BULK_DEPARTMENT_LIMIT = 50


@frappe.whitelist(methods=["POST"])
def add_project_members(project, employees=None, department=None, departments=None):
	"""Append people to ``project``'s members (plan 2026-10-31-001 U5, R5-R7,
	KTD4, KTD5): the named ``employees``, and/or every Active employee in
	``department`` and its sub-departments. Never removes anyone.

	Only Active employees in a company the caller's project write scope
	covers are added. Existing members and people with no portal login are
	skipped and named. An id that is not one of those (missing, not Active,
	another company) comes back as ``"not available"`` with no name, so the
	response cannot be used to read names across companies.

	Appended rows carry ``welcome_email_sent = 1`` (ERPNext's
	`Project.validate` would otherwise mail each one a "Project
	Collaboration Invitation"), and the write goes through
	`_write_project_users`, which strips ERPNext's auto-share.

	Plan 2026-11-01-001 U3 (R4, R6): ``departments`` takes up to 50
	departments in one call, beside the single ``department``; their people
	are unioned and deduplicated, and one department outside scope refuses
	the whole call. Someone who can already book here without being a
	member (`_covered_companies`) is skipped as ``"can already book"``, after
	the not-available check so an out-of-scope id still gets no name.
	`_BULK_MEMBER_LIMIT` counts the people who would actually be added."""
	rate_limit_per_user("add_project_members")
	scope = _project_write_gate(project)

	ids = _string_list(employees, _("People"))
	if len(ids) > _BULK_MEMBER_LIMIT:
		frappe.throw(_("Add at most {0} people at a time.").format(_BULK_MEMBER_LIMIT))
	chosen = _string_list(departments, _("Departments"))
	if len(chosen) > _BULK_DEPARTMENT_LIMIT:
		frappe.throw(_("Add at most {0} departments at a time.").format(_BULK_DEPARTMENT_LIMIT))
	for name in dict.fromkeys([department, *chosen] if department else chosen):
		ids += _department_candidates(name, scope)
	ids = list(dict.fromkeys(ids))
	if not ids:
		frappe.throw(_("Choose people or a department to add."))

	doc = frappe.get_doc("Project", project, for_update=True)
	covered = set(_covered_companies(doc, [row.company for row in doc.get(_OPEN_COMPANIES_FIELD)]))
	rows = frappe.get_all(
		"Employee",
		filters={"name": ["in", ids]},
		fields=["name", "employee_name", "user_id", "company", "status"],
	)
	by_name = {row.name: row for row in rows}
	members = {row.user for row in doc.users}

	adding, skipped = [], []
	for employee in ids:
		row = by_name.get(employee)
		if not row or row.status != "Active" or not scope_covers(scope, row.company):
			skipped.append({"employee": employee, "employee_name": None, "reason": _("not available")})
		elif not row.user_id:
			skipped.append(
				{"employee": row.name, "employee_name": row.employee_name, "reason": _("no portal login")}
			)
		elif row.user_id in members:
			skipped.append(
				{"employee": row.name, "employee_name": row.employee_name, "reason": _("already a member")}
			)
		elif row.company in covered:
			skipped.append(
				{"employee": row.name, "employee_name": row.employee_name, "reason": _("can already book")}
			)
		else:
			members.add(row.user_id)
			adding.append(row)

	if len(adding) > _BULK_MEMBER_LIMIT:
		frappe.throw(
			_("That would add {0} people. Add at most {1} at a time.").format(len(adding), _BULK_MEMBER_LIMIT)
		)
	added = []
	for row in adding:
		doc.append("users", {"user": row.user_id, "welcome_email_sent": 1})
		added.append(
			{"employee": row.name, "employee_name": row.employee_name, "initials": _initials(row.employee_name)}
		)

	if added:
		_write_project_users(doc, insert=False)

	return {"added": _with_photo_urls(added), "skipped": skipped, "members": _project_members(project)}


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
_ROSTER_CANCEL_BLOCKED = (
	"This shift can't be cancelled because check-ins or attendance are already recorded against it. "
	"End it on a date instead."
)
_ROSTER_INACTIVE_EMPLOYEE = "Shifts can only be assigned to someone who is currently employed."
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


def _roster_cells(rows, monday, sunday, can_edit, exposed=()):
	"""`{employee: [cell x 7]}` from one Shift Assignment read and one Shift
	Type read for the whole page, whatever the row count. A cell names its
	assignment for HR (`can_edit`) and for the `exposed` employees -- a
	manager's direct reports, whose roster request rows point at one (plan
	2026-10-31-001 U8)."""
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
			if can_edit or employee in exposed:
				# Only HR acts on a cell, and a manager proposes on a report's,
				# so only those payloads name the record.
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
	# Plan 2026-10-31-001 U8 / R10: team mode is only reachable with Active
	# direct reports (`_roster_employee_scope`), so it always may propose.
	can_request = mode == "team"
	me = _my_employee() if can_request else None
	exposed = {row.name for row in employees if row.name != me} if can_request else ()
	cells, times = _roster_cells(employees, monday, sunday, can_edit, exposed)
	# R19: who is already in a Pending request, for HR's pill and the
	# manager's "already asked" state.
	pending = pending_requests([row.name for row in employees]) if mode in ("team", "hr") else {}
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
				"pending_request": pending.get(row.name),
			}
		)
	_with_photo_urls(rows)

	shift_types = []
	if can_edit or can_request:
		# One past the cap, so a full list is told apart from a cut one. A
		# proposing manager picks from the same list HR does; Shift Type
		# names and times are not personal data.
		shift_types = [
			{"name": shift.name, "start_time": _hh_mm(shift.start_time), "end_time": _hh_mm(shift.end_time)}
			for shift in frappe.get_all(
				"Shift Type",
				fields=["name", "start_time", "end_time"],
				order_by="name asc",
				limit=_ROSTER_SHIFT_TYPE_LIMIT + 1,
				ignore_permissions=can_request,
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
		"can_request": can_request,
		"draft": _roster_draft_payload(me) if can_request else None,
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
	roster never shows either, so neither is anything to act on. Loaded under
	the employee's row lock, which roster request approval also takes, so an
	HR write and an approval for the same person serialise."""
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
	_lock_employee(row.employee)
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


def _end_roster_assignment(doc, end):
	"""End the submitted assignment `doc` on `end` (`end_date` is
	`allow_on_submit`). No savepoint and no date rules of its own: the HR
	endpoint wraps it in `_run_roster_write` after its own checks, and a
	roster request's approval runs it inside its one savepoint after the
	simulator (plan 2026-10-31-001 KTD10). Raises on refusal."""
	_assert_config_write(doc)
	doc.end_date = end
	doc.save()
	return doc


def _change_roster_assignment(doc, start, shift_type):
	"""Change `doc`'s shift from `start`: it ends the day before and a new
	assignment of `shift_type` starts on `start`, keeping the old end.
	Savepoint-free, like `_end_roster_assignment`; returns `(ended, new)`."""
	old_end = getdate(doc.end_date) if doc.end_date else None
	_assert_config_write(doc)
	doc.end_date = add_days(start, -1)
	doc.save()
	return doc, _new_roster_assignment(doc.employee, shift_type, start, old_end)


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
	the payload, `company` included, is ignored. Takes the employee's row
	lock, as roster request approval does, so the two serialise."""
	rate_limit_per_user("assign_shift")
	_assert_roster_employee(employee)
	_lock_employee(employee)
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
	return _roster_assignment_projection(_run_roster_write(lambda: _end_roster_assignment(doc, end)))


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
	ended, assigned = _run_roster_write(lambda: _change_roster_assignment(doc, start, shift_type))
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


# Manager roster requests (plan 2026-10-31-001 U8 / R10-R13, R17, R20)
#
# A manager drafts shift operations for their Active direct reports and
# submits them to HR as `HelixHR Roster Change` requests (KTD7). The DocType
# has no Employee DocPerm: every read and write here is a projection that
# authorizes first, and every status move sets `flags.helixhr_roster_api`,
# which the controller's lifecycle guard requires. The rules each operation
# must pass are `roster_ops.simulate` (KTD9), run at save, submit and (U9)
# approval against live coverage. U9 adds HR's decision through Approvals.

_ROSTER_CHANGE = "HelixHR Roster Change"
_ROSTER_DRAFT_MAX_ROWS = 200
_ROSTER_NOTE_MAX = 1000
_ROSTER_RECENT_LIMIT = 10
# Fields an operation keeps per action; the rest are blanked, so a stray
# `to_date` on a Change never reaches a Date column unvalidated.
_ROSTER_OP_FIELDS = {
	ROSTER_OP_ASSIGN: ("shift_type", "from_date", "to_date"),
	ROSTER_OP_CHANGE: ("shift_type", "from_date", "assignment"),
	ROSTER_OP_END: ("to_date", "assignment"),
}
_ROSTER_ALL_OP_FIELDS = ("shift_type", "from_date", "to_date", "assignment")
_ROSTER_REQUEST_NOT_FOUND = "That roster request could not be found."
_ROSTER_REQUEST_NOT_YOURS = "That roster request isn't yours."


def _roster_draft_ops(rows):
	"""The draft payload as plain operation dicts, refused whole when it is
	not a list of objects or is longer than the cap. The simulator, not
	this, judges each operation."""
	rows = _parse_json_arg(rows)
	if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
		frappe.throw(_("Invalid shift changes."))
	if len(rows) > _ROSTER_DRAFT_MAX_ROWS:
		frappe.throw(_("A request can hold at most {0} shift changes.").format(_ROSTER_DRAFT_MAX_ROWS))
	ops = []
	for row in rows:
		action = str(row.get("action") or "").strip()
		keep = _ROSTER_OP_FIELDS.get(action, _ROSTER_ALL_OP_FIELDS)
		op = {"employee": str(row.get("employee") or "").strip(), "action": action}
		for field in _ROSTER_ALL_OP_FIELDS:
			value = str(row.get(field) or "").strip() if field in keep else ""
			op[field] = value or None
		ops.append(op)
	return ops


def _roster_note(note):
	text = (note or "").strip()
	if len(text) > _ROSTER_NOTE_MAX:
		frappe.throw(_("Keep the note under {0} characters.").format(_ROSTER_NOTE_MAX))
	return text


def _request_ops(doc):
	return [
		{
			"employee": row.employee,
			"action": row.action,
			"shift_type": row.shift_type,
			"from_date": str(getdate(row.from_date)) if row.from_date else None,
			"to_date": str(getdate(row.to_date)) if row.to_date else None,
			"assignment": row.assignment,
		}
		for row in doc.rows
	]


def _review_roster_ops(me, ops):
	"""R10, R13, KTD9 for `ops` as `me` would submit them. Returns
	`_dict(team, before, after, problems)`: `team` the direct reports the
	ops name (`{employee: row}`), `before`/`after` their live and simulated
	coverage, and `problems` every refusal as `{index, employee, message}`,
	each a plain sentence naming the person, in operation order.

	Only Active direct reports (`_direct_report_filters`) -- never the
	manager -- and only from tomorrow on that employee's company clock. An
	operation that fails those is left out of the simulation."""
	ids = list(dict.fromkeys(op["employee"] for op in ops if op["employee"]))
	team = {}
	if ids:
		team = {
			row.name: row
			for row in frappe.get_all(
				"Employee",
				filters={**_direct_report_filters(me), "name": ["in", ids]},
				fields=["name", "employee_name", "company"],
				ignore_permissions=True,
			)
		}
		team.pop(me, None)
	shift_types = {op["shift_type"] for op in ops if op["shift_type"]}
	known_types = (
		set(
			frappe.get_all(
				"Shift Type",
				filters={"name": ["in", list(shift_types)]},
				pluck="name",
				ignore_permissions=True,
			)
		)
		if shift_types
		else set()
	)
	todays = {}
	problems = []
	runnable = []
	for index, op in enumerate(ops):
		employee = op["employee"]
		person = team.get(employee)

		def refuse(message, index=index, employee=employee):
			problems.append({"index": index, "employee": employee, "message": message})

		if employee and employee == me:
			refuse(_("You can't request changes to your own shifts."))
			continue
		if not person:
			# Named only when they are the caller's own report: an arbitrary
			# id gets no name back.
			name = (
				frappe.db.get_value("Employee", {"name": employee, "reports_to": me}, "employee_name")
				if employee
				else None
			)
			if name:
				refuse(_("{0} is no longer active, so their shifts can't be changed.").format(name))
			else:
				refuse(_("{0} doesn't report to you.").format(employee or _("That person")))
			continue
		day = op_date(op)
		if day:
			if person.company not in todays:
				todays[person.company] = company_today(person.company)
			if day <= todays[person.company]:
				refuse(_("{0}: a change can start tomorrow at the earliest.").format(person.employee_name))
				continue
		if op["shift_type"] and op["shift_type"] not in known_types:
			refuse(_("{0}: that shift doesn't exist.").format(person.employee_name))
			continue
		runnable.append((index, op))

	before, after = {}, {}
	dates = [op_date(op) for _index, op in runnable if op_date(op)]
	if runnable:
		employees = {op["employee"] for _index, op in runnable}
		before = read_coverage(employees, min(dates) if dates else user_today())
		after, simulated = simulate(before, [op for _index, op in runnable])
		for problem in simulated:
			index, op = runnable[problem["index"]]
			problems.append(
				{
					"index": index,
					"employee": op["employee"],
					"message": _("{0}: {1}").format(
						team[op["employee"]].employee_name, _(problem["message"])
					),
				}
			)
	problems.sort(key=lambda problem: problem["index"])
	return frappe._dict(team=team, before=before, after=after, problems=problems)


def _refuse_roster_problems(review):
	if review.problems:
		frappe.throw(review.problems[0]["message"])


def _coverage_payload(coverage):
	"""The simulator's coverage as JSON-ready `{employee: [assignment]}`."""
	return {
		employee: [
			{
				"key": item["key"],
				"shift_type": item["shift_type"],
				"start_date": str(item["start_date"]),
				"end_date": str(item["end_date"]) if item["end_date"] else None,
			}
			for item in rows
		]
		for employee, rows in coverage.items()
	}


def _roster_request_payload(doc, merged=False):
	"""`merged`: already reopened into a Draft (`_roster_merged`), so the
	strip offers no Reopen that would only refuse."""
	return {
		"name": doc.name,
		"status": doc.status,
		"merged": merged,
		"company": doc.company,
		"note": doc.note or "",
		"return_reasons": doc.return_reasons or "",
		"decision_note": doc.decision_note or "",
		"decided_on": str(doc.decided_on) if doc.decided_on else None,
		"modified": str(doc.modified),
		"rows": [
			{"employee_name": row.employee_name, **op}
			for row, op in zip(doc.rows, _request_ops(doc), strict=True)
		],
	}


def _my_roster_draft(me, for_update=False):
	"""The manager's one Draft (R11), or None."""
	name = frappe.db.get_value(
		_ROSTER_CHANGE,
		{"employee": me, "status": ROSTER_DRAFT},
		"name",
		order_by="creation asc",
		for_update=for_update,
	)
	return frappe.get_doc(_ROSTER_CHANGE, name) if name else None


def _roster_draft_payload(me, doc=None):
	"""The Draft with its preview: `preview` is the simulated coverage of
	every employee it names (the server's answer the browser mirror of
	`roster_ops` must agree with), `problems` what would refuse it now --
	coverage moves under a saved draft. None when there is no draft."""
	doc = doc or _my_roster_draft(me)
	if not doc:
		return None
	review = _review_roster_ops(me, _request_ops(doc))
	return {
		**_roster_request_payload(doc),
		"preview": _coverage_payload(review.after),
		"problems": review.problems,
	}


@frappe.whitelist()
def get_roster_draft():
	"""The caller's Draft (rows plus simulated preview) and their latest
	non-draft requests with status and HR's words (R11, R17)."""
	rate_limit_per_user("get_roster_draft")
	me = _my_employee()
	recent = frappe.get_all(
		_ROSTER_CHANGE,
		filters={"employee": me, "status": ["!=", ROSTER_DRAFT]},
		pluck="name",
		order_by="modified desc",
		limit=_ROSTER_RECENT_LIMIT,
		ignore_permissions=True,
	)
	merged = _roster_merged(recent)
	return {
		"draft": _roster_draft_payload(me),
		"recent": [
			_roster_request_payload(frappe.get_doc(_ROSTER_CHANGE, name), merged=name in merged)
			for name in recent
		],
	}


def _save_draft_rows(doc, me, ops, note=None):
	doc.set("rows", ops)
	if note is not None:
		doc.note = note
	doc.employee = me
	doc.manager_user = frappe.session.user
	doc.flags.helixhr_roster_api = True
	doc.save(ignore_permissions=True)
	return doc


@frappe.whitelist(methods=["POST"])
def save_roster_draft(rows, note=None):
	"""Replace the caller's one Draft with `rows` (R10, R11, R13), creating
	it on first save. Refused whole, naming the person, when any operation
	is not one the caller may ask for. An empty list keeps an empty Draft
	rather than deleting it: a reopened request keeps its history."""
	rate_limit_per_user("save_roster_draft")
	me = _my_employee()
	ops = _roster_draft_ops(rows)
	note = _roster_note(note) if note is not None else None
	# The manager's own row serialises their saves, submits and reopens, so
	# two tabs cannot each create "the" Draft.
	_lock_employee(me)
	review = _review_roster_ops(me, ops)
	_refuse_roster_problems(review)
	doc = _my_roster_draft(me, for_update=True)
	if not doc:
		if not ops:
			return {"draft": None}
		doc = frappe.new_doc(_ROSTER_CHANGE)
		doc.status = ROSTER_DRAFT
	_save_draft_rows(doc, me, ops, note)
	return {"draft": _roster_draft_payload(me, doc)}


@frappe.whitelist(methods=["POST"])
def submit_roster_change(note=None):
	"""Send the caller's Draft to HR (R12, R13): one Pending request per
	company in the batch (KTD12), each carrying its KTD8 coverage snapshot.
	The Draft itself becomes the first company's request. Refused when any
	person in it is already in another Pending request."""
	rate_limit_per_user("submit_roster_change")
	me = _my_employee()
	note = _roster_note(note) if note is not None else None
	_lock_employee(me)
	draft = _my_roster_draft(me, for_update=True)
	if not draft or not draft.rows:
		frappe.throw(_("There is nothing in your draft to send."))
	ops = _request_ops(draft)
	# Every person's row, in one order, so a concurrent submit naming the
	# same people waits here and then sees this one's Pending request.
	for employee in sorted({op["employee"] for op in ops}):
		_lock_employee(employee)
	review = _review_roster_ops(me, ops)
	_refuse_roster_problems(review)
	taken = pending_requests(list(review.team))
	if taken:
		names = ", ".join(
			review.team[employee].employee_name for employee in review.team if employee in taken
		)
		frappe.throw(
			_(
				"Already waiting for HR in another request: {0}. Wait for HR's decision or withdraw it."
			).format(names)
		)

	by_company = {}
	for op in ops:
		by_company.setdefault(review.team[op["employee"]].company, []).append(op)
	if note is not None:
		draft.note = note
	created = []
	for position, (company, company_ops) in enumerate(by_company.items()):
		doc = draft if position == 0 else frappe.new_doc(_ROSTER_CHANGE)
		if position:
			doc.update({"employee": me, "manager_user": frappe.session.user, "note": draft.note})
		doc.set("rows", company_ops)
		doc.company = company
		doc.snapshot = json.dumps(_roster_snapshot(review.before, company_ops), sort_keys=True)
		# A reopened request is resent as itself: last round's decision is
		# not this round's, so HR and the strip start from a clean slate.
		doc.update({"decided_by": None, "decided_on": None, "decision_note": None, "return_reasons": None})
		doc.status = ROSTER_PENDING
		doc.flags.helixhr_roster_api = True
		if position:
			doc.insert(ignore_permissions=True)
		else:
			doc.save(ignore_permissions=True)
		created.append(doc.name)
	return {"names": created}


def _roster_snapshot(coverage, ops):
	"""KTD8: per employee, the affected window and the coverage key over it.
	The window is stored so approval re-derives over exactly the same days."""
	snapshot = {}
	for employee, (start, end) in affected_window(ops).items():
		snapshot[employee] = {
			"window": [str(start), str(end) if end else None],
			"coverage": snapshot_key(coverage.get(employee, []), (start, end)),
		}
	return snapshot


def _my_roster_request(name, me):
	"""The caller's own request `name`, locked; refused for anybody else's
	(R21's other half: only the submitting manager withdraws or reopens)."""
	owner = frappe.db.get_value(_ROSTER_CHANGE, name, "employee", for_update=True) if name else None
	if not owner:
		frappe.throw(_(_ROSTER_REQUEST_NOT_FOUND), frappe.DoesNotExistError)
	if owner != me:
		frappe.throw(_(_ROSTER_REQUEST_NOT_YOURS), frappe.PermissionError)
	return frappe.get_doc(_ROSTER_CHANGE, name)


@frappe.whitelist(methods=["POST"])
def withdraw_roster_change(name):
	"""Take back the caller's own Pending request (R15)."""
	rate_limit_per_user("withdraw_roster_change")
	me = _my_employee()
	doc = _my_roster_request(name, me)
	if doc.status != ROSTER_PENDING:
		frappe.throw(_("Only a request waiting for HR can be withdrawn."))
	doc.status = ROSTER_WITHDRAWN
	doc.flags.helixhr_roster_api = True
	doc.save(ignore_permissions=True)
	return {"name": doc.name, "status": doc.status}


# The marker a merged request carries, so it is not reopened twice. English
# on purpose: it is matched, not shown in the portal.
_ROSTER_MERGED_INTO = "Reopened into draft {0}"


def _roster_merged(names):
	"""Which of the requests `names` were merged into a Draft, in one read."""
	if not names:
		return set()
	return set(
		frappe.get_all(
			"Comment",
			filters={
				"reference_doctype": _ROSTER_CHANGE,
				"reference_name": ["in", list(names)],
				"comment_type": "Info",
				"content": ["like", _ROSTER_MERGED_INTO.format("%")],
			},
			pluck="reference_name",
		)
	)


@frappe.whitelist(methods=["POST"])
def reopen_roster_change(name):
	"""Bring the caller's Returned or Withdrawn request back as their Draft
	(R17). With no Draft, the request itself becomes it. With one, its rows
	are appended to that Draft -- refused, naming the person, when somebody
	is in both or the merged draft would not pass -- and the reopened
	request keeps its status and history, marked as merged, so there is
	still exactly one Draft (R11)."""
	rate_limit_per_user("reopen_roster_change")
	me = _my_employee()
	_lock_employee(me)
	doc = _my_roster_request(name, me)
	if doc.status not in (ROSTER_RETURNED, ROSTER_WITHDRAWN):
		frappe.throw(_("Only a returned or withdrawn request can be reopened."))
	if _roster_merged([doc.name]):
		frappe.throw(_("This request is already in your draft."))

	draft = _my_roster_draft(me, for_update=True)
	if not draft:
		doc.status = ROSTER_DRAFT
		doc.flags.helixhr_roster_api = True
		doc.save(ignore_permissions=True)
		return {"draft": _roster_draft_payload(me, doc), "merged": False}

	drafted = {row.employee for row in draft.rows}
	for row in doc.rows:
		if row.employee in drafted:
			frappe.throw(
				_(
					"{0} is already in your draft. Send or clear that draft before reopening this request."
				).format(row.employee_name or row.employee)
			)
	ops = _request_ops(draft) + _request_ops(doc)
	if len(ops) > _ROSTER_DRAFT_MAX_ROWS:
		frappe.throw(_("A request can hold at most {0} shift changes.").format(_ROSTER_DRAFT_MAX_ROWS))
	_refuse_roster_problems(_review_roster_ops(me, ops))
	_save_draft_rows(draft, me, ops)
	doc.add_comment("Info", _ROSTER_MERGED_INTO.format(draft.name))
	return {"draft": _roster_draft_payload(me, draft), "merged": True}


# Roster requests in Approvals (plan 2026-10-31-001 U9 / R14-R16, R18, R21)
#
# The sixth approval kind (KTD13). `act_on_approval` -> `_decide_one` lock the
# request row, authorize (`_may_act_on_roster_change`) and check the
# `expected_modified` token before `_act_on_roster_change` runs. Approve
# re-checks every employee under the Employee row locks (KTD8, R16) and then
# applies every operation in one savepoint, as the HR Manager's own session
# (KTD10), so HRMS's validation runs and the assignments are HR's. A failed
# re-check or a refusal mid-apply leaves no shift written and the request
# Returned with the reasons (KTD11).

_ROSTER_DECIDER_ROLE = "HR Manager"
# Its own name: an HR endpoint's `helixhr_roster_write` savepoint never nests
# in it, but a shared name would make the two indistinguishable in a trace.
_ROSTER_APPROVE_SAVEPOINT = "helixhr_roster_approve"
_ROSTER_DECISIONS = ["Approve", "Send Back", "Decline"]


def _may_decide_roster_for(user, company):
	"""Whether `user` decides roster requests for `company` (R14): the HR
	Manager Frappe role -- the one that holds create, submit and write on
	Shift Assignment, so a System Manager without it is not offered a
	decision its apply could not make -- and an HR scope covering the
	request's company, never the submitting manager's (KTD12)."""
	if _ROSTER_DECIDER_ROLE not in frappe.get_roles(user):
		return False
	return scope_covers(resolve_admin_scope(user), company)


def _may_act_on_roster_change(doc, user):
	"""R14, R21. `_assert_may_act_on` has already refused the submitter by
	their Employee login; `manager_user` is the login that submitted, kept
	in case the Employee's login has changed since."""
	if user == doc.manager_user:
		frappe.throw(_("You can't decide your own request. Ask your manager or HR."), frappe.PermissionError)
	if not _may_decide_roster_for(user, doc.company):
		frappe.throw(_(_APPROVAL_NOT_FOUND), frappe.PermissionError)


def _roster_allowed_actions(doc, user):
	"""R15: Approve, Send back or Decline, each final for this round. The
	change request's "Decline" (reason required) is the vocabulary, so the
	screen's existing reason dialog serves it."""
	if user == doc.manager_user or not _may_decide_roster_for(user, doc.company):
		return []
	return list(_ROSTER_DECISIONS)


def _roster_change_summaries(employee, today, count_only=False):
	"""Pending roster requests in the HR Manager's scope (R14), by the
	request's company, oldest wait first. Never the caller's own (R21): a
	manager who is also HR does not see their request in their queue."""
	user = frappe.session.user
	if _ROSTER_DECIDER_ROLE not in frappe.get_roles(user):
		return []
	companies = read_companies(resolve_admin_scope(user))
	if companies is not None and not companies:
		return []
	filters = {"status": ROSTER_PENDING, "manager_user": ["!=", user]}
	if employee:
		filters["employee"] = ["!=", employee]
	if companies is not None:
		filters["company"] = ["in", companies]
	rows = frappe.get_all(
		_ROSTER_CHANGE,
		filters=filters,
		fields=["name"]
		if count_only
		else ["name", "employee", "employee_name", "company", "note", "helixhr_pending_since", "modified"],
		order_by="helixhr_pending_since asc, creation asc",
		limit=_QUEUE_FETCH,
	)
	if count_only:
		return _count_rows("roster", rows)
	ops = {}
	if rows:
		for op in frappe.get_all(
			"HelixHR Roster Change Row",
			filters={"parenttype": _ROSTER_CHANGE, "parent": ["in", [row.name for row in rows]]},
			fields=["parent", "employee", "action", "from_date", "to_date"],
			order_by="idx asc",
		):
			ops.setdefault(op.parent, []).append(op)
	summaries = []
	for row in rows:
		mine = ops.get(row.name, [])
		dates = [day for day in map(op_date, mine) if day]
		summaries.append(
			_summary_row(
				"roster",
				_ROSTER_CHANGE,
				row.name,
				row.employee,
				row.employee_name,
				min(dates) if dates else None,
				max(dates) if dates else None,
				row.helixhr_pending_since or row.modified,
				ROSTER_PENDING,
				today,
				for_hr=True,
				company=row.company,
				change_count=len(mine),
				people_count=len({op.employee for op in mine}),
				note=(row.note or "").strip() or None,
			)
		)
	return summaries


def _roster_request_review(doc, ops, for_update=False):
	"""R16, read now: each employee's stored window (KTD8), live coverage
	over it (`before`), the simulated result (`after`), and every reason the
	request cannot be applied as it stands -- `conflicts`, one per employee,
	`{employee, employee_name, message}`, the message naming them. The
	detail shows these before HR clicks; approval re-runs it under the
	Employee locks with `for_update` (a locking coverage read, so an HR
	roster edit committed while it waited is seen) and returns the request
	on any of them."""
	employees = list(dict.fromkeys(op["employee"] for op in ops))
	stored = json.loads(doc.snapshot) if doc.snapshot else {}
	computed = affected_window(ops)
	windows = {}
	for employee in employees:
		window = (stored.get(employee) or {}).get("window")
		windows[employee] = (
			(getdate(window[0]), getdate(window[1]) if window[1] else None) if window else computed.get(employee)
		)
	people = {
		row.name: row
		for row in frappe.get_all(
			"Employee",
			filters={"name": ["in", employees or [""]]},
			fields=["name", "employee_name", "status", "reports_to", "company"],
		)
	}
	names = {row.employee: row.employee_name or row.employee for row in doc.rows}
	starts = [window[0] for window in windows.values() if window]
	before = read_coverage(employees, min(starts) if starts else user_today(), for_update=for_update)
	after, simulated = simulate(before, ops)
	manager_name = doc.employee_name or doc.employee
	todays = {}
	conflicts = []
	for employee in employees:
		person = people.get(employee)
		name = (person and person.employee_name) or names.get(employee) or employee
		days = [op_date(op) for op in ops if op["employee"] == employee]
		message = None
		if not person or person.status != "Active":
			message = _("{0} is no longer active.").format(name)
		elif person.reports_to != doc.employee:
			message = _("{0} no longer reports to {1}.").format(name, manager_name)
		elif person.company != doc.company:
			message = _("{0} has moved to another company.").format(name)
		else:
			if person.company not in todays:
				todays[person.company] = company_today(person.company)
			if any(day is None or day <= todays[person.company] for day in days):
				message = _("{0}: a change date has passed. Changes can start tomorrow at the earliest.").format(
					name
				)
			elif employee not in stored or snapshot_key(before.get(employee, []), windows[employee]) != stored[
				employee
			].get("coverage"):
				message = _("{0}'s shifts changed after this request was sent.").format(name)
			else:
				problem = next((item for item in simulated if item["employee"] == employee), None)
				if problem:
					message = _("{0}: {1}").format(name, _(problem["message"]))
		if message:
			conflicts.append({"employee": employee, "employee_name": name, "message": message})
	return frappe._dict(windows=windows, before=before, after=after, conflicts=conflicts)


def _roster_window_rows(rows, window):
	"""Coverage `rows` that touch `window`, for the detail's before and after."""
	if not window:
		return []
	start, end = window
	return [
		{
			"shift_type": row["shift_type"],
			"start_date": str(row["start_date"]),
			"end_date": str(row["end_date"]) if row["end_date"] else None,
			# Made by one of this request's operations (`roster_ops` keys).
			"new": str(row["key"]).startswith("new-"),
		}
		for row in rows
		if (end is None or row["start_date"] <= end) and (row["end_date"] is None or row["end_date"] >= start)
	]


def _roster_change_decision_detail(doc):
	"""The request as HR decides it: who asked, for which company, their
	note, and per employee the operations with the affected days before and
	after (U9). `conflicts` is R16's re-check run now, so the screen can warn
	before Approve; a request no longer Pending has no before/after or
	conflicts -- its coverage has moved on."""
	ops = _request_ops(doc)
	review = _roster_request_review(doc, ops) if doc.status == ROSTER_PENDING else None
	names = {row.employee: row.employee_name or row.employee for row in doc.rows}
	employees = []
	for employee in dict.fromkeys(op["employee"] for op in ops):
		window = review.windows.get(employee) if review else None
		employees.append(
			{
				"employee": employee,
				"employee_name": names.get(employee),
				"window_start": str(window[0]) if window else None,
				"window_end": str(window[1]) if window and window[1] else None,
				"operations": [
					{"index": index, **op} for index, op in enumerate(ops) if op["employee"] == employee
				],
				"before": _roster_window_rows(review.before.get(employee, []), window) if review else None,
				"after": _roster_window_rows(review.after.get(employee, []), window) if review else None,
			}
		)
	dates = [day for day in map(op_date, ops) if day]
	manager_name = doc.employee_name or doc.employee
	sent_on = doc.helixhr_pending_since or doc.modified
	detail = _decision_head(doc, manager_name)
	detail.update(
		{
			"kind": "roster",
			"state": doc.status,
			"status": doc.status,
			"manager": doc.employee,
			"manager_name": manager_name,
			"company": doc.company,
			"note": (doc.note or "").strip() or None,
			"from_date": str(min(dates)) if dates else None,
			"to_date": str(max(dates)) if dates else None,
			"change_count": len(ops),
			"employees": employees,
			"conflicts": review.conflicts if review else [],
			"decision_note": doc.decision_note or None,
			"return_reasons": doc.return_reasons or None,
			"submitted_on": str(sent_on) if sent_on else None,
			"sent_on": str(sent_on) if sent_on else None,
			"age_days": _age_in_days(sent_on, _as_date(user_today())),
		}
	)
	return detail


def _close_roster_change(doc, status, note=None, return_reasons=None):
	"""Record HR's outcome. A flagged `save`, so the lifecycle guard allows it
	and `events.roster_change_on_change` tells the manager (R20)."""
	doc.status = status
	doc.decided_by = frappe.session.user
	doc.decided_on = now_datetime()
	doc.decision_note = note or None
	doc.return_reasons = return_reasons or None
	doc.flags.helixhr_roster_api = True
	doc.save(ignore_permissions=True)


def _return_roster_change(doc, reasons):
	_close_roster_change(doc, ROSTER_RETURNED, return_reasons="\n".join(item["message"] for item in reasons))
	return {"returned": True, "reasons": reasons}


def _roster_op_target(employee, assignment, day):
	"""The submitted assignment a Change or End acts on, read now -- after
	the earlier operations of the same approval. The row's stored
	`assignment` only while it still exists and covers `day`; otherwise the
	one covering `day` (a chained operation on what an earlier one made)."""
	filters = {"employee": employee, "docstatus": 1, "start_date": ["<=", str(day)]}
	or_filters = [["end_date", "is", "not set"], ["end_date", ">=", str(day)]]
	for extra in ([{"name": assignment}] if assignment else []) + [{}]:
		found = frappe.get_all(
			"Shift Assignment",
			filters={**filters, **extra},
			or_filters=or_filters,
			pluck="name",
			order_by="start_date asc, name asc",
			limit=1,
		)
		if found:
			return frappe.get_doc("Shift Assignment", found[0])
	return None


def _apply_roster_op(op):
	"""One operation through the HR roster's own writers (KTD10). Raises on
	any refusal; the caller owns the savepoint."""
	day = op_date(op)
	if op["action"] == ROSTER_OP_ASSIGN:
		end = getdate(op["to_date"]) if op["to_date"] else None
		return _new_roster_assignment(op["employee"], op["shift_type"], day, end)
	target = _roster_op_target(op["employee"], op["assignment"], day)
	if not target:
		frappe.throw(_(_ROSTER_NO_SHIFT_ON_DATE))
	if op["action"] == ROSTER_OP_CHANGE:
		return _change_roster_assignment(target, day, op["shift_type"])
	return _end_roster_assignment(target, day)


def _approve_roster_change(doc):
	"""R16 then R18. The request row is already locked (`_decide_one`); the
	Employee rows are locked here in one order, then re-checked. Any
	conflict returns the request with nothing written. Clean, every
	operation runs in order inside one savepoint; a refusal rolls all of
	them back, its stray messages are cleared, and the request is returned
	with HRMS's sentence. Either way the response says `returned`."""
	from frappe.utils.messages import clear_messages
	from hrms.hr.doctype.shift_assignment.shift_assignment import MultipleShiftError, OverlappingShiftError

	ops = _request_ops(doc)
	for employee in sorted({op["employee"] for op in ops}):
		_lock_employee(employee)
	conflicts = _roster_request_review(doc, ops, for_update=True).conflicts
	if conflicts:
		return _return_roster_change(doc, conflicts)

	names = {row.employee: row.employee_name or row.employee for row in doc.rows}
	frappe.db.savepoint(_ROSTER_APPROVE_SAVEPOINT)
	current = None
	try:
		for current in ops:
			_apply_roster_op(current)
	except frappe.PermissionError:
		# HR's own access, not the request: it stays Pending.
		frappe.db.rollback(save_point=_ROSTER_APPROVE_SAVEPOINT)
		clear_messages()
		frappe.throw(_(_ROSTER_REFUSED), frappe.PermissionError)
	except frappe.ValidationError as error:
		frappe.db.rollback(save_point=_ROSTER_APPROVE_SAVEPOINT)
		clear_messages()
		if isinstance(error, OverlappingShiftError | MultipleShiftError):
			sentence = _(_ROSTER_OVERLAP)
		else:
			sentence = frappe.utils.strip_html(str(error)).strip() or _(_ROSTER_SAVE_FAILED)
		name = names.get(current["employee"]) or current["employee"]
		return _return_roster_change(
			doc,
			[{"employee": current["employee"], "employee_name": name, "message": _("{0}: {1}").format(name, sentence)}],
		)
	frappe.db.release_savepoint(_ROSTER_APPROVE_SAVEPOINT)
	_close_roster_change(doc, ROSTER_APPROVED)
	return {"returned": False, "reasons": []}


def _act_on_roster_change(doc, action):
	"""R15. Send back returns it to the manager with HR's reason; Decline is
	final. Both reasons are required by `act_on_approval`."""
	reason = (doc.flags.helixhr_decision_note or "").strip()
	if action == "Approve":
		return _approve_roster_change(doc)
	if action == "Send Back":
		_close_roster_change(doc, ROSTER_RETURNED, note=reason, return_reasons=reason)
	elif action == "Decline":
		_close_roster_change(doc, ROSTER_DECLINED, note=reason)
	else:
		frappe.throw(_("Not a valid action."))
	return None


def _decided_roster_changes(employee, since, pattern=None):
	"""Roster requests this caller approved, declined or sent back --
	`decided_by` is the scope, as for change requests. An automatic
	withdrawal stamps no decider, so it is nobody's receipt."""
	window, or_filters, limit = _decided_window(since, pattern, date_field="decided_on")
	return [
		_decided_row(
			"roster",
			row.name,
			row.employee_name,
			"Roster change",
			None,
			None,
			row.status,
			row.decided_on,
		)
		for row in frappe.get_all(
			_ROSTER_CHANGE,
			filters={
				"decided_by": frappe.session.user,
				"employee": ["!=", employee],
				"status": ["in", [ROSTER_APPROVED, ROSTER_DECLINED, ROSTER_RETURNED]],
				**window,
			},
			or_filters=or_filters,
			fields=["name", "employee_name", "status", "decided_on"],
			order_by="decided_on desc",
			limit=limit,
		)
	]


# The kind joins the approval tables here, beside its functions, the way the
# change kind joins `_APPROVAL_SUMMARY_COLLECTORS`. It has no line-manager
# half: like HR Request, its whole queue is its `hr_queue`.
_APPROVAL_KINDS[_ROSTER_CHANGE] = {
	"state_field": "status",
	"detail": _roster_change_decision_detail,
	"may_act": _may_act_on_roster_change,
	"is_open": lambda doc: doc.status == ROSTER_PENDING,
	"open_message": "This roster request is no longer waiting for HR. Reload to see where it is.",
	"hr_state": lambda doc: True,
	"hr_queue": _roster_change_summaries,
	"act": _act_on_roster_change,
}
_DECIDED_STAMPED_COLLECTORS = (*_DECIDED_STAMPED_COLLECTORS, _decided_roster_changes)


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
def get_organisation_view(company=None):
	"""A read-only snapshot of one company (P5-R20): headcount, how many
	people are out today, each queue's backlog and its age, and this month's
	celebrations.

	Gated on `resolve_read_scope` -- HR Manager and System Manager, plus a
	holder of an All-data company access grant (plan 2026-10-14-001 U6) --
	because this is company-wide read, not a manager's own team. ``company``
	picks one of the companies that scope covers (``companies``); it
	defaults to the caller's own, and anything else is refused the same way
	as a company that does not exist.
	"""
	rate_limit_per_user("get_organisation_view")
	scope = resolve_read_scope(frappe.session.user)
	if scope["kind"] == "none":
		frappe.throw(_("Only HR may see the organisation view."), frappe.PermissionError)

	companies = read_companies(scope)
	if companies is None:
		companies = frappe.get_all("Company", pluck="name", order_by="company_name asc")
	if not companies:
		frappe.throw(_("Only HR may see the organisation view."), frappe.PermissionError)
	home = session_company(frappe.session.user)
	if not home and scope["kind"] == "unscoped":
		home = frappe.db.get_single_value("Global Defaults", "default_company")
	if company in (None, ""):
		company = home if home in companies else companies[0]
	elif company not in companies:
		frappe.throw(_("That company is not available here."), frappe.PermissionError)
	today = user_today()

	return {
		"company": company,
		"companies": companies,
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
		"celebrations": _get_celebrations(None, getdate(today), company=company),
	}


def _headcount(company):
	if not company:
		return 0
	return frappe.db.count("Employee", {"status": "Active", "company": company})
