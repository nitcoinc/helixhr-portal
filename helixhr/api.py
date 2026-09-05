import json

import frappe
from frappe import _
from frappe.utils import (
	flt,
	get_datetime,
	get_datetime_in_timezone,
	get_first_day,
	get_last_day,
	get_system_timezone,
)
from hrms.api import (
	get_attendance_calendar_events,
	get_current_employee,
	get_current_employee_info,
	get_leave_balance_map,
)

from helixhr.events import HR_REPLY_SUBJECT_PREFIX
from helixhr.utils import (
	PROFILE_EDITABLE_FIELDS,
	get_manager_user,
	get_week_bounds,
	rate_limit_per_user,
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
	employee = get_current_employee()
	failed = []
	cache = {}

	def once(key, fn):
		if key not in cache:
			cache[key] = fn()
		return cache[key]

	def section(name, fn):
		try:
			return fn()
		except Exception:
			failed.append(name)
			frappe.log_error(title=f"HelixHR dashboard section failed: {name}")
			return None

	return {
		"employee": section("employee", lambda: _get_employee_header(employee)),
		"leave_balances": section("leave_balances", get_leave_balance_map),
		"attendance_this_month": section(
			"attendance_this_month", lambda: _get_attendance_summary(once)
		),
		"timesheet_this_week": None,  # wired up in U8
		"pending": section("pending", lambda: _get_pending_counts(employee, once)),
		"unread_notifications": section("unread_notifications", _get_unread_notification_count),
		# The two sections the week-spine dashboard is built on. Kept inside
		# get_dashboard rather than split into their own endpoints so the home
		# screen stays one request.
		"week": section("week", lambda: _get_week_spine(employee, once)),
		"needs_you": section("needs_you", lambda: _get_needs_you(employee, once)),
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
	rate_limit_per_user("update_my_profile", limit=20, seconds=60)
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
		"report_count": 0,
		"can_approve": False,
		"unread_notifications": 0,
	}

	if not employee or not employee.get("name"):
		# A signed-in user with no active Employee record. Everything below
		# is scoped to an Employee, so there is nothing more to say -- and
		# saying it plainly is what lets the browser tell this apart from a
		# service failure (P2-U2 scenario 3).
		return boot

	boot["report_count"] = _safe(lambda: _count_direct_reports(employee["name"])) or 0
	# A leave approver need not be anybody's manager, and a manager's only
	# pending work may be a timesheet -- gating the Approvals nav item on
	# direct reports alone hid the entry from both (P2-R11). The count is
	# still tried first because it is one indexed count and short-circuits
	# the two list reads for the common case.
	boot["can_approve"] = boot["report_count"] > 0 or bool(
		_safe(lambda: _pending_approvals(employee["name"]))
	)
	boot["unread_notifications"] = _safe(_get_unread_notification_count) or 0
	return boot


def _count_direct_reports(employee):
	return frappe.db.count("Employee", {"reports_to": employee, "status": "Active"})


def _safe(fn):
	try:
		return fn()
	except Exception:
		frappe.log_error(title="HelixHR dashboard section failed")
		return None


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


def _get_pending_counts(employee, once):
	return {
		"my_open_leave": len(once("open_leave", lambda: _open_leave(employee))),
		"my_open_requests": frappe.db.count("HR Request", {"employee": employee, "status": "Open"}),
		"approvals_waiting_for_me": len(once("approvals", lambda: _pending_approvals(employee))),
	}


def _open_leave(employee):
	"""This employee's leave that is still with their manager. One read: the
	count on the dashboard's `pending` section and the rows in the queue's
	"Waiting on others" list are the same question asked twice.

	Bounded by _QUEUE_FETCH like every other queue source -- the count is
	an approximation above that, which no real employee reaches.
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


def _week_timesheet(employee, monday):
	"""The one Timesheet this week's hours and this week's status both come
	from. One lookup: the spine used to ask for its name, then ask again
	for its workflow_state."""
	return frappe.db.get_value(
		"Timesheet",
		{"employee": employee, "start_date": str(monday), "docstatus": ["!=", 2]},
		["name", "workflow_state"],
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
		filters={"employee": employee, "workflow_state": "Rejected", "docstatus": ["!=", 2]},
		fields=["name", "start_date", "end_date"],
		order_by="start_date asc",
		limit=_QUEUE_FETCH,
	)
	reasons = _rejection_comments([row.name for row in rejected])
	for row in rejected:
		items.append(
			_queue_item(
				kind="timesheet_rejected",
				doctype="Timesheet",
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
				# (P2-AE5).
				to={"name": "TimesheetWeek", "params": {"weekStart": str(row.start_date)}},
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
				doctype="HR Request",
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
				doctype=decision["reference_doctype"],
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
				doctype="Leave Application",
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

	items.sort(key=lambda item: (_URGENCY_RANK[item["urgency"]], -(item["age_days"] or 0)))
	waiting.sort(key=lambda item: -(item["age_days"] or 0))
	shown = items[:_QUEUE_LIMIT]
	return {
		"items": shown,
		"more": max(0, len(items) - len(shown)),
		"waiting": waiting[:_QUEUE_LIMIT],
	}


def _queue_item(
	*, kind, doctype, name, title, detail, date, day, age_days, action, owner, urgency, to, notification=None
):
	return {
		# Stable record identity, and the Vue list key. Never an index: a
		# re-ordered queue reused the wrong row's DOM state under one.
		"id": f"{kind}:{notification or name}",
		"kind": kind,
		"reference_doctype": doctype,
		"reference_name": name,
		"notification": notification,
		"title": title,
		"detail": detail,
		"date": date,
		"day": day,
		"age_days": age_days,
		"action": action,
		# Whose move it is. "you" rows are the queue; "manager" rows are the
		# waiting list.
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
_QUEUE_FETCH = 50
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


def _rejection_comments(timesheets):
	"""The manager's reason for every sent-back timesheet, in one query
	(P2-R22: server queries avoid per-record comment lookups). This was one
	Comment read per queue row."""
	if not timesheets:
		return {}

	latest = {}
	for row in frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": "Timesheet",
			"reference_name": ["in", timesheets],
			"comment_type": "Comment",
		},
		fields=["reference_name", "content"],
		# Ascending, so the newest comment is the last one written into the
		# map and wins.
		order_by="creation asc",
	):
		latest[row.reference_name] = frappe.utils.strip_html(row.content).strip() if row.content else None
	return latest


def _last_rejection_comment(timesheet):
	"""The manager's reason, shown inline so the employee doesn't have to
	open the timesheet to find out what to change."""
	comment = frappe.db.get_value(
		"Comment",
		{"reference_doctype": "Timesheet", "reference_name": timesheet, "comment_type": "Comment"},
		"content",
		order_by="creation desc",
	)
	return frappe.utils.strip_html(comment).strip() if comment else None


def _pending_approvals(employee):
	"""Every decision the session user may make right now -- leave *and*
	timesheet (P2-R11). A manager whose only pending work is a timesheet had
	no approval row on Home and no Approvals nav item at all, because both
	read a leave-only count.

	Both reads run as the session user, so Frappe's own permissions decide
	what comes back: HRMS filters leave by `leave_approver`, and a timesheet
	is visible to its approver through the DocShare `timesheet_on_update`
	grants at Pending Approval. Nothing here is an authorization decision of
	its own -- `act_on_approval` re-checks who may act, on the server, every
	time.
	"""
	from hrms.api import get_leave_applications

	decisions = []
	for row in get_leave_applications(employee, approver_id=frappe.session.user, for_approval=True):
		decisions.append(
			{
				"kind": "approval_leave",
				"reference_doctype": "Leave Application",
				"reference_name": row["name"],
				"route_kind": "leave",
				"title": f"{row.get('employee_name')} asked for {row.get('leave_type')}",
				"date": str(row.get("from_date")) if row.get("from_date") else None,
			}
		)

	for row in frappe.get_all(
		"Timesheet",
		filters={
			"workflow_state": "Pending Approval",
			"docstatus": 0,
			"employee": ["!=", employee],
		},
		fields=["name", "employee_name", "start_date"],
		order_by="start_date asc",
		limit=_QUEUE_FETCH,
	):
		decisions.append(
			{
				"kind": "approval_timesheet",
				"reference_doctype": "Timesheet",
				"reference_name": row.name,
				"route_kind": "timesheet",
				"title": f"{row.employee_name} sent a week for your approval",
				"date": str(row.start_date) if row.start_date else None,
			}
		)

	return decisions


# Attendance (U7, R16)


@frappe.whitelist()
def get_my_attendance(from_date, to_date):
	"""One month of attendance for the logged-in employee: a status per day,
	the late/early flags Frappe records, and the four exceptions R16 asks for
	(absent, half day, late, missing).

	"Missing" is the careful one. No check-in device is configured yet, so a
	naive "working day with no Attendance record" would mark *every* past day
	as missing and drown the page in red -- worse than showing nothing. A day
	only counts as missing when it falls on or after the first Attendance
	record this employee has ever had: before that date the company simply
	was not recording, so nothing can be absent from it. That makes the whole
	feature dormant until real data arrives, and correct the moment it does,
	with no further change here.
	"""
	employee = get_current_employee()
	start, end = _as_date(from_date), _as_date(to_date)

	records = frappe.get_all(
		"Attendance",
		filters={
			"employee": employee,
			"attendance_date": ["between", [str(start), str(end)]],
			"docstatus": 1,
		},
		fields=["attendance_date", "status", "late_entry", "early_exit"],
	)

	days = {
		str(row.attendance_date): {
			"status": row.status,
			"late": bool(row.late_entry),
			"early": bool(row.early_exit),
		}
		for row in records
	}

	tracking_since = frappe.db.get_value(
		"Attendance",
		{"employee": employee, "docstatus": 1},
		"attendance_date",
		order_by="attendance_date asc",
	)
	missing = _missing_attendance_days(employee, start, end, days, tracking_since)

	summary = {}
	for entry in days.values():
		summary[entry["status"]] = summary.get(entry["status"], 0) + 1

	return {
		"tracked": bool(tracking_since),
		"tracking_since": str(tracking_since) if tracking_since else None,
		# False when the employee has no resolvable holiday list, in which case
		# working days are unknowable and `missing` is deliberately empty
		# rather than guessed.
		"working_days_known": _holiday_dates(employee, start, end) is not None,
		"days": days,
		"missing": missing,
		"summary": summary,
		"exceptions": {
			"absent": summary.get("Absent", 0),
			"half_day": summary.get("Half Day", 0),
			"late": sum(1 for entry in days.values() if entry["late"]),
			"missing": len(missing),
		},
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


def _missing_attendance_days(employee, start, end, days, tracking_since):
	from frappe.utils import add_days

	if not tracking_since:
		return []
	holidays = _holiday_dates(employee, start, end)
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


# Timesheets (U8, KTD7, KTD10, KTD11)


@frappe.whitelist()
def get_my_week(week_start=None):
	"""The one Timesheet for the Monday..Sunday week containing
	`week_start` (any date in that week; defaults to today), or None if
	the employee hasn't started one yet (KTD10: one week is one
	Timesheet -- the newest non-cancelled one for that Monday)."""
	employee = get_current_employee()
	monday, sunday = get_week_bounds(week_start or user_today())

	name = frappe.db.get_value(
		"Timesheet",
		{"employee": employee, "start_date": str(monday), "docstatus": ["!=", 2]},
		"name",
		order_by="creation desc",
	)

	timesheet = None
	if name:
		doc = frappe.get_doc("Timesheet", name)
		timesheet = {
			"name": doc.name,
			"workflow_state": doc.workflow_state,
			"total_hours": doc.total_hours,
			"docstatus": doc.docstatus,
			# Resolved here rather than by the page, which used to read it with
			# `frappe.client.get_list` on Comment -- a doctype the Employee Self
			# Service role cannot read, so that call 403'd and the employee was
			# told their week was sent back without ever being told why. The
			# e2e only asserted the "Sent back" label, so it never caught it.
			"rejection_comment": _last_rejection_comment(doc.name)
			if doc.workflow_state == "Rejected"
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

	return {"week_start": str(monday), "week_end": str(sunday), "timesheet": timesheet}


def _row_date(row):
	return str(get_datetime(row.from_time).date()) if row.from_time else None


@frappe.whitelist()
def get_my_projects():
	"""Open Projects the session user may book time on -- Project Users
	or a User Permission on Project, each with its own open Tasks
	(KTD11: no "bookable projects" API exists upstream)."""
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
	for project in projects:
		project["tasks"] = frappe.get_all(
			"Task",
			filters={"project": project.name, "status": ["not in", ["Cancelled", "Completed"]]},
			fields=["name", "subject"],
			order_by="subject",
		)
	return projects


@frappe.whitelist(methods=["POST"])
def save_my_week(week_start, rows):
	"""Insert or update the one draft Timesheet for this week (KTD10).
	Refuses to touch anything but a Draft or Rejected timesheet -- once a
	week is Pending Approval or Approved it isn't this method's to edit
	(the workflow's own `allow_edit` per state backs this up too, this
	is just a clearer error than a generic permission failure).
	"""
	rate_limit_per_user("save_my_week", limit=30, seconds=60)
	employee = get_current_employee()
	if isinstance(rows, str):
		rows = json.loads(rows)
	monday, sunday = get_week_bounds(week_start)

	bookable_projects = {p["name"] for p in get_my_projects()}
	_validate_rows(rows, bookable_projects)

	existing_name = frappe.db.get_value(
		"Timesheet",
		{"employee": employee, "start_date": str(monday), "docstatus": ["!=", 2]},
		"name",
		order_by="creation desc",
	)

	if existing_name:
		doc = frappe.get_doc("Timesheet", existing_name)
		if doc.workflow_state not in ("Draft", "Rejected", None):
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
	for row in rows:
		start = get_datetime(f"{row['date']} 09:00:00")
		hours = flt(row["hours"])
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
	return doc.name


def _validate_rows(rows, bookable_projects):
	if not rows:
		frappe.throw(_("Add at least one row before saving."))

	day_totals = {}
	for row in rows:
		if not row.get("project"):
			frappe.throw(_("Every row needs a project."))
		if row["project"] not in bookable_projects:
			frappe.throw(_("You can't book time on {0}.").format(row["project"]))
		if not row.get("date"):
			frappe.throw(_("Every row needs a date."))

		hours = flt(row.get("hours"))
		if hours < 0.25 or hours > 24:
			frappe.throw(_("Hours must be between 0.25 and 24."))

		day_totals[row["date"]] = day_totals.get(row["date"], 0) + hours
		if day_totals[row["date"]] > 24:
			frappe.throw(_("{0} has more than 24 hours booked.").format(row["date"]))


def _get_unread_notification_count():
	return frappe.db.count("Notification Log", {"for_user": frappe.session.user, "read": 0})


# Approvals (U12, R25, R26, KTD7)


@frappe.whitelist(methods=["POST"])
def act_on_approval(doctype, name, action, comment=None, expected_modified=None):
	"""Approve or reject a report's pending Leave Application or Timesheet.
	`comment` is required for a reject (R25). Who may actually act is
	checked here on the server, not assumed from what the portal chose to
	show (R26) -- Timesheet goes through the same workflow transition
	the portal's own Submit/Edit actions use (its condition and
	before_submit guard are the real check); Leave Application has no
	Workflow (KTD17), so the equivalent check is explicit here.

	Order matters, and it is the P2-U1 fix. The sequence is: lock the
	native row, authorize, check the expected state, and only then create
	any side effect. Before P2-U1 the comment was added first, so an
	unauthorized caller left a real Comment on somebody else's leave
	before the approver check refused them (P2-R10, P2-U1 step 9).

	`expected_modified` is the optional concurrency token (P2-R25): pass
	the `modified` value the screen was rendered from and a decision made
	against stale data is refused instead of overwriting a decision
	somebody else already made.
	"""
	rate_limit_per_user("act_on_approval", limit=60, seconds=60)
	if doctype not in ("Leave Application", "Timesheet"):
		frappe.throw(_("Not a valid request."))
	if action not in ("Approve", "Reject"):
		frappe.throw(_("Not a valid action."))
	if action == "Reject" and not comment:
		frappe.throw(_("A comment is required to reject."))

	# SELECT ... FOR UPDATE on the one row: two concurrent decisions
	# serialize here, so the second one reads the first one's result and is
	# refused by the state check below rather than racing it (P2-U1 step 1).
	current_modified = frappe.db.get_value(doctype, name, "modified", for_update=True)
	if current_modified is None:
		frappe.throw(_("That request no longer exists."), frappe.DoesNotExistError)

	doc = frappe.get_doc(doctype, name)
	_assert_may_act_on(doc)
	_assert_still_open(doc)
	_assert_expected_state(expected_modified, current_modified)

	if comment:
		doc.add_comment("Comment", comment)

	if doctype == "Timesheet":
		from frappe.model.workflow import apply_workflow

		apply_workflow(doc, action)
		return

	_act_on_leave_application(doc, action)


def _assert_may_act_on(doc):
	"""Refuse anyone but this record's own approver (or HR) before a single
	side effect runs (P2-U1 step 9)."""
	user = frappe.session.user
	if user == "Administrator" or set(frappe.get_roles(user)) & {"HR Manager", "System Manager"}:
		return

	if doc.doctype == "Timesheet":
		# The same rule events.timesheet_before_submit enforces on submit,
		# applied here so a Reject (which never submits) and the comment
		# that goes with it are covered by it too.
		if user == get_manager_user(doc.employee):
			return
		frappe.throw(
			_("Only {0}'s manager or HR can act on this timesheet.").format(doc.employee),
			frappe.PermissionError,
		)

	if user == doc.leave_approver:
		return
	frappe.throw(
		_("Only {0}'s approver or HR can act on this leave request.").format(doc.employee),
		frappe.PermissionError,
	)


def _assert_still_open(doc):
	"""One decision per record. A second decision -- the losing half of a
	concurrent approve/approve or approve/reject -- is refused here, before
	it can add a contradicting comment or a second ledger effect."""
	if doc.doctype == "Timesheet":
		if doc.workflow_state != "Pending Approval":
			frappe.throw(_("This timesheet has already been decided. Reload to see the result."))
		return

	if doc.docstatus != 0 or doc.status != "Open":
		frappe.throw(_("This leave request has already been decided. Reload to see the result."))


def _assert_expected_state(expected_modified, current_modified):
	if not expected_modified:
		return
	if get_datetime(expected_modified) != get_datetime(current_modified):
		frappe.throw(_("Somebody changed this while you were looking at it. Reload and try again."))


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
	"""
	if action == "Approve":
		doc.status = "Approved"
		doc.submit()
	else:
		doc.status = "Rejected"
		doc.save()


# Documents (R19, P2-R19)


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
	employee = get_current_employee()
	company = frappe.db.get_value("Employee", employee, "company")
	return frappe.get_all(
		"HelixHR Document Link",
		or_filters=[["company", "is", "not set"], ["company", "=", company]],
		fields=["name", "title", "url", "company", "description"],
		order_by="title asc",
	)
