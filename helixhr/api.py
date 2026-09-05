import json

import frappe
from frappe import _
from frappe.utils import (
	add_days,
	cint,
	date_diff,
	flt,
	get_datetime,
	get_datetime_in_timezone,
	get_first_day,
	get_last_day,
	get_system_timezone,
	getdate,
)
from hrms.api import (
	get_attendance_calendar_events,
	get_current_employee,
	get_current_employee_info,
	get_leave_approval_details,
	get_leave_balance_map,
	get_leave_types,
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
				doctype="Leave Application",
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


def _approval_summaries(employee):
	"""Every decision the session user may make right now, as one bounded,
	typed, oldest-first list -- leave *and* timesheet (P2-R11, P2-U7 step 1).

	This is the single source of the three things that used to be answered
	separately and could therefore disagree: what the Approvals queue shows,
	what Home counts as a decision the manager owes, and whether the
	Approvals nav item is drawn at all. It is *not* an authorization
	decision -- `_assert_may_act_on` re-checks who may act, on the server,
	on every read of a detail and on every action.

	Both reads run as the session user through `frappe.get_list`, so
	Frappe's own permissions decide what comes back: HRMS filters leave by
	`leave_approver`, and a timesheet reaches its approver through the
	Pending-Approval DocShare `timesheet_on_update` grants plus the nested-set
	User Permission a manager holds over their reports.

	The timesheet read used `frappe.get_all` until P2-U7. `get_all` is
	`get_list` with `ignore_permissions=True`, so it answered with *every*
	pending timesheet on the site regardless of who was asking -- the
	employee-name and week of every person in the company, to anyone with a
	session, through Home and through this queue.
	"""
	from hrms.api import get_leave_applications

	today = _as_date(user_today())
	rows = []

	for row in get_leave_applications(employee, approver_id=frappe.session.user, for_approval=True):
		sent_on = row.get("creation") or row.get("posting_date")
		rows.append(
			{
				# Stable identity, and the Vue list key.
				"id": f"leave:{row['name']}",
				"kind": "leave",
				"doctype": "Leave Application",
				"name": row["name"],
				"employee": row.get("employee"),
				"employee_name": row.get("employee_name"),
				"initials": _initials(row.get("employee_name")),
				"leave_type": row.get("leave_type"),
				"from_date": str(row["from_date"]) if row.get("from_date") else None,
				"to_date": str(row["to_date"]) if row.get("to_date") else None,
				"total_days": flt(row.get("total_leave_days")),
				"total_hours": None,
				"status": row.get("status"),
				"sent_on": str(sent_on) if sent_on else None,
				"age_days": _age_in_days(sent_on, today),
			}
		)

	for row in frappe.get_list(
		"Timesheet",
		filters={
			"workflow_state": "Pending Approval",
			"docstatus": 0,
			"employee": ["!=", employee],
		},
		fields=["name", "employee", "employee_name", "start_date", "end_date", "total_hours", "modified"],
		order_by="start_date asc",
		limit=_QUEUE_FETCH,
	):
		# `modified` is when the week last moved, which for a Pending
		# Approval timesheet is when it was sent. Timesheet has no
		# submitted-on field of its own and the workflow transition is a
		# plain field update, so this is the closest honest answer.
		rows.append(
			{
				"id": f"timesheet:{row.name}",
				"kind": "timesheet",
				"doctype": "Timesheet",
				"name": row.name,
				"employee": row.employee,
				"employee_name": row.employee_name,
				"initials": _initials(row.employee_name),
				"leave_type": None,
				"from_date": str(row.start_date) if row.start_date else None,
				"to_date": str(row.end_date) if row.end_date else None,
				"total_days": None,
				"total_hours": flt(row.total_hours),
				"status": "Pending Approval",
				"sent_on": str(row.modified) if row.modified else None,
				"age_days": _age_in_days(row.modified, today),
			}
		)

	# Oldest first: the queue is a backlog, and the person who has waited
	# longest is the one the manager is holding up (P2-U7 step 7).
	rows.sort(key=lambda entry: (entry["sent_on"] or "", entry["name"]))
	return rows


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
	for row in _approval_summaries(employee):
		if row["kind"] == "leave":
			title = f"{row['employee_name']} asked for {row['leave_type']}"
		else:
			title = f"{row['employee_name']} sent a week for your approval"
		decisions.append(
			{
				"kind": "approval_leave" if row["kind"] == "leave" else "approval_timesheet",
				"reference_doctype": row["doctype"],
				"reference_name": row["name"],
				"route_kind": row["kind"],
				"title": title,
				"date": row["from_date"],
			}
		)
	return decisions


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
	  decided         docstatus 1, anything else
	  cancelled       docstatus 2, or status Cancelled
	"""
	docstatus = cint(docstatus)
	if docstatus == 2 or status == "Cancelled":
		return "cancelled"
	if docstatus == 1:
		return "approved" if status == "Approved" else "decided"
	if status == "Rejected":
		return "sent_back"
	if status == "Approved":
		return "waiting_for_hr"
	return "open"


def _may_withdraw(state):
	"""Withdrawal is a property of the lifecycle, not of the screen. Only a
	still-unsubmitted request of this employee's own can be removed; an
	approved one is a submitted document with a ledger entry behind it and
	the honest path is "Ask HR to cancel"."""
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
		text = frappe.utils.strip_html(row.content).strip() if row.content else None
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
		"can_withdraw": _may_withdraw(state),
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


def _leave_balances(employee):
	"""Allocated / used / left per leave type, in the shape the field block
	draws. `get_leave_balance_map` is already session-scoped to the caller's
	own Employee, so nothing here widens it."""
	balances = []
	for leave_type, details in (get_leave_balance_map() or {}).items():
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

	types = []
	for leave_type in get_leave_types(employee, today) or []:
		entry = balances.get(leave_type)
		types.append(
			{
				"leave_type": leave_type,
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
	return {"name": doc.name, "status": doc.status, "total_leave_days": flt(doc.total_leave_days)}


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
	employee = get_current_employee()
	current = frappe.db.get_value(
		"Leave Application",
		name,
		["employee", "docstatus", "status"],
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

	frappe.delete_doc("Leave Application", name)
	return {"name": name, "withdrawn": True}


# Attendance (U7, R16)

# One year, the widest span the screen can ask for (P2-U5 step 6).
_ATTENDANCE_MAX_DAYS = 366
# A day's worth of punches. Nobody badges fifty times; the cap is there so a
# malformed date can never turn the day sheet into an unbounded read.
_CHECKIN_LIMIT = 50


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
	return frappe.get_all(
		"Employee Checkin",
		filters={
			"employee": employee,
			"time": ["between", [f"{day} 00:00:00", f"{day} 23:59:59"]],
		},
		fields=["name", "time", "log_type"],
		order_by="time asc",
		limit=_CHECKIN_LIMIT,
	)


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
	reasons = _rejection_comments([row.name for row in rows if row.workflow_state == "Rejected"])

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
	rate_limit_per_user("save_my_week", limit=30, seconds=60)
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

	rate_limit_per_user("save_my_week", limit=30, seconds=60)
	employee = get_current_employee()
	monday, sunday = get_week_bounds(week_start)

	frappe.db.get_value("Employee", employee, "name", for_update=True)

	current = frappe.db.get_value(
		"Timesheet",
		{"employee": employee, "start_date": str(monday), "docstatus": ["!=", 2]},
		["name", "workflow_state", "modified"],
		order_by="creation desc",
		as_dict=True,
	)
	_assert_week_is_still_sendable(current, expected_modified)

	doc = _write_my_week(employee, monday, sunday, rows)
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
	if current and current.workflow_state not in ("Draft", "Rejected", None):
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


def _write_my_week(employee, monday, sunday, rows):
	"""Replace this week's rows. Shared by `save_my_week` and
	`submit_my_week` so a week is validated and written exactly one way."""
	from frappe.model.workflow import apply_workflow

	if isinstance(rows, str):
		rows = json.loads(rows)
	if not isinstance(rows, list):
		frappe.throw(_("Those rows aren't in a shape we can save."))

	_validate_rows(rows, _bookable_tasks_by_project(), monday, sunday)

	existing_name = frappe.db.get_value(
		"Timesheet",
		{"employee": employee, "start_date": str(monday), "docstatus": ["!=", 2]},
		"name",
		order_by="creation desc",
	)

	if existing_name:
		doc = frappe.get_doc("Timesheet", existing_name)
		if doc.workflow_state == "Rejected":
			# Rejected is not an editable state for an Employee (the
			# workflow gives `allow_edit` to HR Manager), so a sent-back
			# week has to travel Rejected -> Draft before it can be
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


@frappe.whitelist()
def get_my_approvals():
	"""The manager's queue: everything waiting on them, oldest first, plus
	the handful of decisions they made this week (P2-U7 step 1).

	Summary only. The evidence -- timesheet rows and day totals, a leave's
	reason -- costs a document read per item, so it is loaded by
	`get_approval_detail` for the one item actually selected (P2-R22).
	"""
	employee = get_current_employee()
	pending = _approval_summaries(employee)
	return {
		"today": user_today(),
		"pending": pending[:_APPROVAL_PAGE],
		"total": len(pending),
		"decided": _recently_decided(employee),
	}


def _recently_decided(employee):
	"""What this approver decided in the last week, newest first.

	Best effort by design. A Timesheet's DocShare is removed the moment it
	is decided (P2-U7 scenario 8), so a decided week is only still visible
	to a manager who can read it some other way -- the nested-set User
	Permission over their own reports. An approver who is nobody's manager
	sees their leave decisions here and nothing else, which is correct: the
	group is a receipt for work this user did, not a record they own.
	"""
	since = add_days(user_today(), -_DECIDED_DAYS)
	today = _as_date(user_today())
	decided = []

	for row in frappe.get_list(
		"Leave Application",
		filters={
			"leave_approver": frappe.session.user,
			"employee": ["!=", employee],
			"status": ["in", ["Approved", "Rejected"]],
			"modified": [">=", str(since)],
		},
		fields=["name", "employee_name", "leave_type", "from_date", "to_date", "status", "modified"],
		order_by="modified desc",
		limit=_DECIDED_LIMIT,
	):
		decided.append(
			{
				"id": f"leave:{row.name}",
				"kind": "leave",
				"name": row.name,
				"employee_name": row.employee_name,
				"initials": _initials(row.employee_name),
				"label": row.leave_type,
				"from_date": str(row.from_date) if row.from_date else None,
				"to_date": str(row.to_date) if row.to_date else None,
				"status": row.status,
				"decided_on": str(row.modified) if row.modified else None,
			}
		)

	for row in frappe.get_list(
		"Timesheet",
		filters={
			"employee": ["!=", employee],
			"workflow_state": ["in", ["Approved", "Rejected"]],
			"modified": [">=", str(since)],
		},
		fields=["name", "employee_name", "start_date", "end_date", "workflow_state", "modified"],
		order_by="modified desc",
		limit=_DECIDED_LIMIT,
	):
		decided.append(
			{
				"id": f"timesheet:{row.name}",
				"kind": "timesheet",
				"name": row.name,
				"employee_name": row.employee_name,
				"initials": _initials(row.employee_name),
				"label": "Timesheet",
				"from_date": str(row.start_date) if row.start_date else None,
				"to_date": str(row.end_date) if row.end_date else None,
				"status": row.workflow_state,
				"decided_on": str(row.modified) if row.modified else None,
			}
		)

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
	doctype = _APPROVAL_DOCTYPES.get(kind)
	if not doctype:
		frappe.throw(_("Not a valid request."))

	if not frappe.db.exists(doctype, name):
		frappe.throw(_("That request no longer exists."), frappe.DoesNotExistError)

	doc = frappe.get_doc(doctype, name)
	_assert_may_act_on(doc)

	if doctype == "Leave Application":
		return _leave_decision_detail(doc)
	return _timesheet_decision_detail(doc)


_APPROVAL_DOCTYPES = {"leave": "Leave Application", "timesheet": "Timesheet"}


def _decision_head(doc, employee_name):
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
	"""Approve or send back a report's pending Leave Application or
	Timesheet. `comment` is required for a Reject (R25) -- the employee is
	told what to change, not just that it came back.

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
	rate_limit_per_user("act_on_approval", limit=60, seconds=60)
	if doctype not in ("Leave Application", "Timesheet"):
		frappe.throw(_("Not a valid request."))
	if action not in ("Approve", "Reject"):
		frappe.throw(_("Not a valid action."))
	if action == "Reject" and not comment:
		frappe.throw(_("A comment is required to reject."))
	if not expected_modified:
		frappe.throw(_("Open this request before deciding it, then try again."))

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
	_assert_expected_workflow_state(doc, expected_state)

	if comment:
		doc.add_comment("Comment", comment)

	if doctype == "Timesheet":
		from frappe.model.workflow import apply_workflow

		apply_workflow(doc, action)
		return {"name": doc.name, "action": action, "state": doc.workflow_state}

	_act_on_leave_application(doc, action)
	return {"name": doc.name, "action": action, "state": doc.status}


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


def _assert_expected_workflow_state(doc, expected_state):
	"""The second half of the token (P2-U7 step 3). `modified` says the row
	moved; this says what it moved *to*, which is the difference between a
	harmless edit and a decision somebody else already made."""
	if not expected_state:
		return
	current = doc.workflow_state if doc.doctype == "Timesheet" else doc.status
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
