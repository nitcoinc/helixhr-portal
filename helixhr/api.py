import json
import math
import os
import re
from urllib.parse import quote

import frappe
from frappe import _
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
	get_url_to_form,
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
	get_leave_approval_details,
	get_leave_balance_map,
	get_leave_types,
)
from hrms.utils.holiday_list import get_holiday_list_for_employee

from helixhr.events import (
	DECISION_REASON_FIELD,
	HR_REPLY_SUBJECT_PREFIX,
	HR_REQUEST_IN_PROGRESS,
	HR_REQUEST_OPEN,
	HR_REQUEST_WAITING_ON_EMPLOYEE,
	LEAVE_STAGE_HR,
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
)

# The routed roles that are *not* already unscoped HR access (P5-U5's own
# permission scope), reused here rather than re-listed so this gate and
# `hr_request.get_permission_query_conditions` cannot drift apart.
from helixhr.helixhr.doctype.hr_request.hr_request import _WORKER_ROLES as _ROUTED_WORKER_ROLES
from helixhr.utils import (
	ADMIN_REPORTS,
	HOLIDAY_LIST_EDITABLE_FIELDS,
	LEAVE_TYPE_EDITABLE_FIELDS,
	PROFILE_EDITABLE_FIELDS,
	SHIFT_TYPE_EDITABLE_FIELDS,
	TEMPLATE_TOKENS,
	UPLOAD_MAX_BYTES,
	admin_scope_employee_filters,
	employee_in_admin_scope,
	get_manager_user,
	get_week_bounds,
	rate_limit_per_user,
	resolve_admin_scope,
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
		# P5-U14: the same predicate `get_portal_config` itself enforces, so
		# the nav item and the server's own gate can never disagree. Read
		# unconditionally on role, like `can_work_requests` above -- HR
		# Manager need not be anybody's employee for Settings to make sense.
		"can_configure": _is_hr(frappe.session.user),
		# P5-U15: the same predicate `get_organisation_view` itself enforces,
		# so the nav item and the server's own gate can never disagree --
		# same shape as `can_configure` just above.
		"can_see_organisation": _is_hr(frappe.session.user),
		# P6-U4: same shape again -- `search_people` and `get_person` are
		# gated by `resolve_admin_scope`, which grants a scope to exactly
		# the roles `_is_hr` names, so the nav item and the server's gate
		# agree by construction.
		"can_see_people": resolve_admin_scope(frappe.session.user)["kind"] != "none",
		# P6-KTD4: resolved on the caller's own ability to reach Desk (a
		# System User holding a `desk_access` role), never on "is HR" --
		# the two are correlated today but the flag must not assume they
		# stay that way.
		"can_open_desk": _can_open_desk(frappe.session.user),
		"unread_notifications": 0,
	}

	if not employee or not employee.get("name"):
		# A signed-in user with no active Employee record. Everything below
		# is scoped to an Employee, so there is nothing more to say -- and
		# saying it plainly is what lets the browser tell this apart from a
		# service failure (P2-U2 scenario 3).
		return boot

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


def _count_direct_reports(employee):
	return frappe.db.count("Employee", {"reports_to": employee, "status": "Active"})


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
# tables hold a handful of rows per employee.
_QUEUE_LIMIT = 8

# The rail card's ceiling (P4-U9). Five rows is what fits the rail beside the
# queue without becoming the taller column; the page behind it is the full
# searchable catalogue.
_LINKS_LIMIT = 5
_QUEUE_FETCH = 50
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
	}
	row.update(extra)
	return row


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
	this caller has no line-manager queue at all.

	For an ordinary manager it is "anybody but me": the DocShare
	`events.timesheet_on_update` / `attendance_request_on_update` grants,
	plus the nested-set User Permission a manager holds over their reports,
	are what narrow the answer to their own people.

	An HR Manager has native read on every Timesheet and Attendance Request
	in the company, so the same filter would answer with the whole company's
	backlog rather than with their own reports (P4-KTD7, P4-R11). Their
	line-manager half is therefore narrowed by `reports_to` explicitly; the
	HR half of their queue is a separate collector.
	"""
	if not _is_hr():
		return ["!=", employee]
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
	"""
	from hrms.api import get_leave_applications

	applications = get_leave_applications(
		employee, approver_id=frappe.session.user, for_approval=True
	)
	with_hr = _leave_names_in_hr_stage([row["name"] for row in applications])

	rows = []
	for row in applications:
		if row["name"] in with_hr:
			continue
		sent_on = row.get("creation") or row.get("posting_date")
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
	"""
	scope = _line_manager_filter(employee)
	if scope is None:
		return []
	return [
		# `modified` is when the week last moved, which for a Pending
		# Approval timesheet is when it was sent. Timesheet has no
		# submitted-on field of its own and the workflow transition is a
		# plain field update, so this is the closest honest answer.
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
		)
		for row in frappe.get_list(
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
			],
			order_by="start_date asc",
			limit=_QUEUE_FETCH,
		)
	]


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
			sent_to_hr_by=senders[row.name]["by"],
			hr_note=senders[row.name]["note"],
		)
		for row in rows
	]


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


def _hr_request_summaries(employee, today):
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
	"""
	rows = frappe.get_list(
		"HR Request",
		filters={
			"status": ["in", (HR_REQUEST_OPEN, HR_REQUEST_IN_PROGRESS)],
			"employee": ["!=", employee],
		},
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
		],
		order_by="creation asc",
		limit=_QUEUE_FETCH,
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
			for_hr=(row.routed_to_role == "HR Manager"),
			hr_note=row.hr_note,
		)
		for row in rows
	]


# Per-kind, never "not leave means timesheet" (P3-U6 step 0). A third kind
# landed in P3-U5, and every one of these helpers used to branch on one
# doctype and treat everything else as the other.
_APPROVAL_SUMMARY_COLLECTORS = (
	_leave_summaries,
	_timesheet_summaries,
	_attendance_request_summaries,
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
		for collect in _APPROVAL_SUMMARY_COLLECTORS:
			collected = collect(employee, today)
			capped = capped or len(collected) >= _QUEUE_FETCH
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
	return rows, capped


def _holds_routed_role(user=None):
	"""Whether the caller holds a role a category may route work to, other
	than HR Manager -- `_is_hr` already covers HR Manager, and this is the
	other half of P5-R11's "is HR or holds a routed role" gate."""
	user = user or frappe.session.user
	return bool(set(frappe.get_roles(user)) & _ROUTED_WORKER_ROLES)


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
	"""
	employee = get_current_employee()
	today = user_today()
	details = get_leave_approval_details(employee) or {}
	balances = {entry["leave_type"]: entry for entry in _leave_balances(employee)}

	names = get_leave_types(employee, today) or []
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
		"approver": details.get("leave_approver"),
		"approver_name": details.get("leave_approver_name"),
	}


@frappe.whitelist()
def get_leave_day_count(leave_type, from_date, to_date, half_day=0, half_day_date=None):
	"""The day count HRMS itself will store, plus the non-working days it
	skipped and what the balance looks like afterwards (P2-U5 scenario 2).

	This calls HRMS's own `get_number_of_leave_days`, deliberately: a
	browser-side count would be a second implementation of the
	`include_holiday` rule, and the first time the two disagreed the
	employee would see one number and get another.

	It is a *preview*, never a gate. The browser shows what comes back and
	still sends the request; whether the leave is allowed is decided by
	HRMS on insert, and a refusal there wins over anything shown here.
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
	return {
		"total_leave_days": days,
		"skipped": skipped,
		"skipped_label": _skipped_label(skipped),
		"balance": balance,
		"balance_after": balance - days,
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
	approver = (get_leave_approval_details(employee) or {}).get("leave_approver")
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

	return {
		"week_start": str(monday),
		"week_end": str(sunday),
		# Who this week goes to when it is sent. The desktop grid names them
		# beside Submit, and an employee with nobody named is told before
		# they fill a week in rather than by a refusal afterwards.
		"approver_name": _approver_name(employee),
		"full_week_hours": FULL_WEEK_HOURS,
		"timesheet": timesheet,
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
			}
		)

	return {
		"weeks": weeks,
		"total": frappe.db.count("Timesheet", scope),
		"full_week_hours": FULL_WEEK_HOURS,
	}


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


@frappe.whitelist()
def get_my_projects():
	"""Open Projects the session user may book time on -- Project Users
	or a User Permission on Project, each with its own open Tasks
	(KTD11: no "bookable projects" API exists upstream).

	Tasks come back in **one** query for the whole allowed project set
	(P2-R22). It used to be one Task query per project, so an employee on
	a dozen projects paid a dozen round trips to fill a dropdown.
	"""
	user = frappe.session.user

	project_names = set(
		frappe.get_all("Project User", filters={"user": user}, pluck="parent")
	) | set(
		frappe.get_all(
			"User Permission", filters={"user": user, "allow": "Project"}, pluck="for_value"
		)
	)
	if not project_names:
		return []

	projects = frappe.get_all(
		"Project",
		filters={"name": ["in", list(project_names)], "status": "Open"},
		fields=["name", "project_name"],
		order_by="project_name",
	)
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


def _bookable_tasks_by_project():
	"""`{project: {task ids}}` for the session user -- the allow-list both
	writes below validate against. The browser's dropdown is a convenience;
	this is the check (P2-R27)."""
	return {project["name"]: {task["name"] for task in project["tasks"]} for project in get_my_projects()}


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
# HRMS submit lifecycle. There is no bulk approve, deliberately: the whole
# point of P2-U7 is that a decision is made against evidence, and a button
# that decides eight records at once cannot have been.
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


@frappe.whitelist()
def get_my_approvals():
	"""The manager's queue: everything waiting on them, oldest first, plus
	the handful of decisions they made this week (P2-U7 step 1).

	Summary only. The evidence -- timesheet rows and day totals, a leave's
	reason -- costs a document read per item, so it is loaded by
	`get_approval_detail` for the one item actually selected (P2-R22).
	"""
	rate_limit_per_user("get_my_approvals")
	employee = get_current_employee()
	pending, capped = _approval_summaries(employee)
	return {
		"today": user_today(),
		"pending": pending[:_APPROVAL_PAGE],
		"total": len(pending),
		# `total` is what came back, and every kind's read is bounded, so on a
		# very large backlog it is a floor and not a count. The flag is what
		# lets the screen say "50+" rather than lie about 50; a real COUNT per
		# kind on every poll is the thing being avoided (P2-R22, P3-R25).
		"total_is_capped": capped,
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
	return [
		_decided_row(
			"timesheet",
			row.name,
			row.employee_name,
			"Timesheet",
			row.start_date,
			row.end_date,
			row.workflow_state,
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
	return decided[:_DECIDED_LIMIT]


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
}

# One refusal for "missing", "not yours to decide" and "outside your company"
# alike, the way `_PAYSLIP_NOT_FOUND` already works: record names are
# sequential, so distinct messages would let any signed-in employee walk the
# id space and learn which records exist and whose they are.
_APPROVAL_NOT_FOUND = "That request isn't here."


# The seven outcomes, in the order the screen draws them: the decision, the
# recoverable no, the final no, the hand-over (P4-R1..R5), and the routed
# request's own three (Pick up, Need info, Done -- P5-U6). Timesheets reach
# three of the first four and never Reject (P4-KTD2); HR Request never
# reaches Approve or Send to HR at all -- both are facts of each workflow's
# own transitions rather than a rule written here.
_APPROVAL_ACTIONS = ("Approve", "Send Back", "Reject", "Send to HR", "Pick up", "Need info", "Done")

# The three that are meaningless without one: the employee is told what to
# change, why the answer is final, or what is missing before it can be
# finished (P4-R3, P4-R4, P5-U6).
_REASON_REQUIRED = ("Send Back", "Reject", "Need info")

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
	if not expected_modified:
		frappe.throw(_("Open this request before deciding it, then try again."))

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

	_APPROVAL_KINDS[doctype]["act"](doc, action)
	return {
		"name": doc.name,
		"action": action,
		"state": doc.get(_APPROVAL_KINDS[doctype]["state_field"]),
	}


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

	No `ignore_permissions`: the caller has already been authorized above,
	and the submit itself runs under the grant HRMS sets up natively --
	Employee is a nested set, so a manager's own User Permission covers
	their reports' records, and the Leave Approver role HRMS auto-grants
	when `Employee.leave_approver` is set carries submit at permlevel 0.
	An approver who is not in the reporting line instead gets the
	`submit=1` DocShare hrms.hr.utils.share_doc_with_approver creates on
	every save. See docs/architecture.md and
	test_the_approvers_submit_grant_is_native.

	P4-U2 gives the same function the other three outcomes. Send back is
	unchanged; Reject is the *submitted* twin of it -- HRMS's own on_submit
	accepts Approved and Rejected, a submitted application writes no Leave
	Ledger Entry unless it is Approved, and docstatus 1 is what makes the row
	unresendable (P4-R4). Send to HR touches no HRMS field at all: it moves
	`helixhr_stage`, which is permlevel 1, so it goes through `db_set` after
	the authorization `act_on_approval` has already done (P4-KTD4).
	"""
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
}


# Documents (R19, P2-R19)


def _visible_document_links(employee):
	"""The policy links one employee may see: global ones plus their own
	company's (P2-R19).

	One query, two callers -- `get_my_documents` for the full searchable page
	and the dashboard's bounded card -- so the scope has one definition.
	"""
	company = frappe.db.get_value("Employee", employee, "company")
	return frappe.get_all(
		"HelixHR Document Link",
		or_filters=[["company", "is", "not set"], ["company", "=", company]],
		fields=["name", "title", "url", "company", "description"],
		order_by="title asc",
	)


def _get_documents_card(employee):
	"""The rail card: the first `_LINKS_LIMIT` links and how many were not
	shown. Ordered by title, the only stable order this catalogue has -- there
	is no priority field, and adding one would give HR a column to maintain for
	no gain at five rows."""
	links = _visible_document_links(employee)
	shown = links[:_LINKS_LIMIT]
	return {"items": shown, "more": max(0, len(links) - len(shown))}


@frappe.whitelist()
def get_my_documents():
	"""The policy links this employee may see: global ones plus their own
	company's (P2-R19).

	The scope is not this method's only enforcement -- HelixHR Document
	Link registers `permission_query_conditions` and `has_permission`
	(hooks.py), so a caller reaching for frappe.client.get_list,
	/api/resource, report view, print or export gets the same answer. This
	method exists so the portal asks a session-scoped question instead of
	sending the filter itself (KTD5, R27).
	"""
	return _visible_document_links(get_current_employee())


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


def _requests_summary(employee, limit=None):
	"""A bounded page of `employee`'s requests, newest first.

	Carries what the list actually renders and nothing else: the lifecycle
	dates, HR's reply, how many files are on it, and whether there is an
	unread notification about it -- which is what puts a row under "Needs
	you" rather than a status word (P2-R13).
	"""
	limit = min(max(cint(limit) or _REQUEST_PAGE, 1), _REQUEST_MAX_PAGE)

	rows = frappe.get_all(
		"HR Request",
		filters={"employee": employee},
		fields=list(_REQUEST_FIELDS),
		order_by="creation desc",
		limit=limit,
	)
	names = [row.name for row in rows]
	unread = _unread_request_notifications(names)
	counts = _attachment_counts(names)

	return {
		"requests": [
			{
				**row,
				"unread": row.name in unread,
				"attachments": counts.get(row.name, 0),
			}
			for row in rows
		],
		"total": frappe.db.count("HR Request", {"employee": employee}),
		"limit": limit,
		"today": user_today(),
	}


@frappe.whitelist()
def get_my_requests(limit=None):
	return _requests_summary(get_current_employee(), limit)


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
		"HR Request", name, [*_REQUEST_FIELDS, "details", "employee"], as_dict=True
	)
	if not row:
		frappe.throw(_("That request no longer exists."), frappe.DoesNotExistError)
	if row.employee != employee:
		# Not "not found": the caller is authenticated and this is a refusal,
		# which the portal renders as its own state with no Retry (P2-R2).
		frappe.throw(_("That request isn't yours."), frappe.PermissionError)
	row.pop("employee")

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
def create_my_request(category, subject, details=None, operation_key=None):
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
	"""The active request categories the employee may file (P5-R1)."""
	rate_limit_per_user("get_request_categories")
	return frappe.get_all(
		"HelixHR Request Category",
		filters={"is_active": 1},
		fields=["name", "category_name", "hint"],
		limit=_REQUEST_CATEGORY_LIMIT,
		order_by="category_name asc",
	)


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
_CATEGORY_EDITABLE_FIELDS = ("hint", "route_to_role", "sla_days", "is_active")


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


@frappe.whitelist()
def get_portal_config():
	"""Everything the Settings screen needs, in one call (P5-R13, P5-R14,
	P5-R16). HR only: an IT Team holder works requests but does not
	configure the site."""
	rate_limit_per_user("get_portal_config")
	if not _is_hr():
		frappe.throw(_("You don't have permission to do that."), frappe.PermissionError)

	return {
		"categories": frappe.get_all(
			"HelixHR Request Category",
			fields=["name", "category_name", "hint", "route_to_role", "sla_days", "is_active"],
			order_by="category_name asc",
		),
		"templates": frappe.get_all(
			"HelixHR Message Template",
			fields=["name", "template_key", "subject", "body", "is_enabled"],
			order_by="template_key asc",
		),
		"template_tokens": TEMPLATE_TOKENS,
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


@frappe.whitelist(methods=["POST"])
def save_message_template(template_key, subject=None, body=None, is_enabled=None):
	"""Edit the wording of one message the portal sends (P5-R14). The body
	is stored as-is and rendered later by `helixhr.utils.render_tokens` --
	plain substitution, never Jinja (P5-R15, P5-KTD11) -- so nothing here
	ever executes what HR types."""
	rate_limit_per_user("save_message_template")
	if template_key not in TEMPLATE_TOKENS:
		frappe.throw(_("Not a valid message."))

	if frappe.db.exists("HelixHR Message Template", template_key):
		doc = frappe.get_doc("HelixHR Message Template", template_key)
	else:
		doc = frappe.new_doc("HelixHR Message Template")
		doc.template_key = template_key

	_assert_config_write(doc)
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
	doc.save()
	return {
		"template_key": doc.template_key,
		"subject": doc.subject,
		"body": doc.body,
		"is_enabled": cint(doc.is_enabled),
	}


@frappe.whitelist(methods=["POST"])
def save_leave_type(name, **fields):
	"""Create or update a leave type through the five-field set P5-KTD12
	names -- HRMS's own `validate()` still runs on `doc.save()` (P5-KTD15),
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

	role = row.routed_to_role
	recipients = _enabled_users_with_role(role)
	if recipients:
		try:
			frappe.sendmail(
				recipients=recipients,
				subject=f"New reply on a {row.category} request: {row.subject}",
				message=(
					f"{frappe.utils.escape_html(row.category)} request "
					f"“{frappe.utils.escape_html(row.subject)}” has a new reply. "
					f"<a href=\"{frappe.utils.get_url('/helixhr/requests')}\">Open requests</a>."
				),
				reference_doctype="HR Request",
				reference_name=name,
			)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "HelixHR request reply mail failed")

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
	employee = get_current_employee()
	limit = min(max(cint(limit) or _DIRECTORY_PAGE, 1), _DIRECTORY_MAX_PAGE)
	start = max(cint(start), 0)

	company = frappe.db.get_value("Employee", employee, "company")
	if not company:
		return {"people": [], "total": 0, "limit": limit, "start": start, "departments": []}

	filters = {"status": "Active", "company": company}
	if department:
		filters["department"] = department

	needle = (query or "").strip()[:_DIRECTORY_QUERY_MAX]
	or_filters = None
	if len(needle) >= _DIRECTORY_QUERY_MIN:
		or_filters = [
			["employee_name", "like", f"%{needle}%"],
			["designation", "like", f"%{needle}%"],
			["department", "like", f"%{needle}%"],
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
		"people": [_directory_projection(row, manager_names) for row in rows],
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
		"people": [_people_search_projection(row) for row in rows],
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


def _person_profile(employee):
	"""Identity, manager, employment status and joining date -- the part of
	the person view that is not one of the portal's other existing readers."""
	fields = [
		"name",
		"employee_name",
		"designation",
		"department",
		"branch",
		"reports_to",
		"status",
		"date_of_joining",
	]
	data = frappe.db.get_value("Employee", employee, fields, as_dict=True)
	data["manager_name"] = (
		frappe.db.get_value("Employee", data.reports_to, "employee_name") if data.reports_to else None
	)
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
	if report not in ADMIN_REPORTS:
		frappe.throw(_("That report is not offered here."), frappe.PermissionError)
	if not _can_open_desk(frappe.session.user):
		frappe.throw(_("You do not have access to Frappe's Desk."), frappe.PermissionError)

	filters = None
	if employee:
		if not employee_in_admin_scope(employee, scope):
			frappe.throw(_("You are not authorised to view this person."), frappe.PermissionError)
		filters = {"employee": employee}

	return get_report_url(report, filters)


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
		filters={"reports_to": manager, "status": "Active"},
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

	by_employee = {}
	waiting_count = 0
	if reports:
		# Explicitly *not* `description`, and explicitly not `*` (P3-R21).
		# `docstatus` and `status` are read to decide `waiting` and are
		# translated into that one flag rather than passed through -- the
		# screen has no use for either word (design system copy rules).
		for row in frappe.get_all(
			"Leave Application",
			filters={
				"employee": ["in", [report.name for report in reports]],
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

	employee = get_current_employee()
	company = frappe.db.get_value("Employee", employee, "company") if employee else None
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
