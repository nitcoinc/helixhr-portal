import json

import frappe
from frappe import _
from frappe.utils import flt, get_datetime, get_first_day, get_last_day, today
from hrms.api import get_attendance_calendar_events, get_current_employee, get_leave_balance_map

from helixhr.utils import PROFILE_EDITABLE_FIELDS, get_week_bounds, rate_limit_per_user


@frappe.whitelist()
def get_dashboard(**kwargs):
	"""One-screen summary for the logged-in employee (R6). The employee is
	always resolved from the session, never from an argument -- any extra
	query args (an `employee` a caller might try to pass) are accepted and
	ignored via **kwargs, they never change whose data comes back (KTD5).

	Each section is independent: a doctype that doesn't exist yet (HR
	Request, Timesheet workflow -- both land in later units) or any other
	section-specific failure returns null for that section only, never an
	error for the whole page.
	"""
	employee = get_current_employee()
	return {
		"employee": _safe(lambda: _get_employee_header(employee)),
		"leave_balances": _safe(get_leave_balance_map),
		"attendance_this_month": _safe(_get_attendance_summary),
		"timesheet_this_week": None,  # wired up in U8
		"pending": _safe(lambda: _get_pending_counts(employee)),
		"unread_notifications": _safe(_get_unread_notification_count),
		# The two sections the week-spine dashboard is built on. Kept inside
		# get_dashboard rather than split into their own endpoints so the home
		# screen stays one request, and wrapped in _safe like every other
		# section so a failure here blanks one region, not the page.
		"week": _safe(lambda: _get_week_spine(employee)),
		"needs_you": _safe(lambda: _get_needs_you(employee)),
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


def _get_attendance_summary():
	# str(), not the date objects get_first_day/get_last_day return:
	# hrms.api.get_attendance_calendar_events is annotated `from_date: str`
	# and Frappe's typing validation raises FrappeTypeError on a date. _safe
	# swallowed it, so this card returned null and rendered "Nothing recorded
	# yet" for every employee regardless of their real attendance.
	start, end = str(get_first_day(today())), str(get_last_day(today()))
	events = get_attendance_calendar_events(start, end)
	summary = {}
	for status in events.values():
		summary[status] = summary.get(status, 0) + 1
	return summary


def _get_pending_counts(employee):
	return {
		"my_open_leave": frappe.db.count("Leave Application", {"employee": employee, "status": "Open"}),
		"my_open_requests": frappe.db.count("HR Request", {"employee": employee, "status": "Open"}),
		"approvals_waiting_for_me": _count_leave_approvals_waiting(employee),
	}


def _get_week_spine(employee):
	"""Monday..Sunday for the current week, one entry per day: the
	attendance status Frappe recorded, the hours booked on that day's
	timesheet rows, and whether approved leave covers it.

	Same Monday-anchored week as the Timesheet screen (get_week_bounds,
	KTD10), so "Thu" means the same day on both screens.
	"""
	from frappe.utils import add_days, getdate

	monday, sunday = get_week_bounds(today())
	attendance = get_attendance_calendar_events(str(monday), str(sunday)) or {}
	hours = _hours_by_day(employee, monday)
	leave_days = _leave_days(employee, monday, sunday)
	current = getdate(today())

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
		"timesheet_state": _week_timesheet_state(employee, monday),
	}


def _hours_by_day(employee, monday):
	"""Booked hours per day from this week's Timesheet. Timesheet Detail
	carries `from_time`, not a date column, so the day is derived the same
	way get_my_week does it (_row_date)."""
	name = frappe.db.get_value(
		"Timesheet",
		{"employee": employee, "start_date": str(monday), "docstatus": ["!=", 2]},
		"name",
		order_by="creation desc",
	)
	if not name:
		return {}

	totals = {}
	for row in frappe.get_all(
		"Timesheet Detail", filters={"parent": name}, fields=["from_time", "hours"]
	):
		if not row.from_time:
			continue
		day = str(get_datetime(row.from_time).date())
		totals[day] = flt(totals.get(day, 0)) + flt(row.hours)
	return totals


def _week_timesheet_state(employee, monday):
	return frappe.db.get_value(
		"Timesheet",
		{"employee": employee, "start_date": str(monday), "docstatus": ["!=", 2]},
		"workflow_state",
		order_by="creation desc",
	)


def _leave_days(employee, monday, sunday):
	"""Set of ISO dates inside the week covered by an approved leave. Leave
	Applications store a range, so each one is expanded across the days it
	overlaps with this week."""
	from frappe.utils import add_days, getdate

	covered = set()
	applications = frappe.get_all(
		"Leave Application",
		filters={
			"employee": employee,
			"status": "Approved",
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


# The action queue. Order is the screen's whole argument, so it lives here on
# the server rather than in the component. Severity leads -- blocked work
# (something sent back, waiting on this person) outranks an answered request,
# which outranks a decision owed as an approver, which outranks this person's
# own item waiting on somebody else -- and inside a tier the oldest comes
# first. That last part is the direction's named risk: a three-week-old
# rejection is more overdue than this week's, so it must not sort underneath
# it. `day` ties a row to the spine above; `age_days` is what the row shows
# when it falls outside the week, so an old item reads as old rather than
# merely losing two words of context.
def _get_needs_you(employee):
	monday, sunday = get_week_bounds(today())
	current = _as_date(today())
	items = []

	def day_for(date):
		return str(date) if date and monday <= _as_date(date) <= sunday else None

	def age_days(date):
		"""How overdue this is, in days. None for rows with no date (an
		approvals count is about now, not about a past date)."""
		return (current - _as_date(date)).days if date else None

	for row in frappe.get_all(
		"Timesheet",
		filters={"employee": employee, "workflow_state": "Rejected", "docstatus": ["!=", 2]},
		fields=["name", "start_date", "end_date"],
		order_by="start_date asc",
		limit=_QUEUE_FETCH,
	):
		items.append(
			{
				"kind": "timesheet_rejected",
				"title": "Your timesheet was sent back",
				"detail": _last_rejection_comment(row.name),
				"date": str(row.start_date),
				"day": day_for(row.start_date),
				"age_days": age_days(row.start_date),
				"action": "Edit and resubmit",
				"to": "/timesheet",
				"tone": "danger",
			}
		)

	# "HR has responded" is a closed request carrying a note -- HR Request has
	# no Answered state, its options are Open/In Progress/Done/Rejected, and a
	# closed one with no note has nothing for the employee to read.
	for row in frappe.get_all(
		"HR Request",
		filters={
			"employee": employee,
			"status": ["in", ["Done", "Rejected"]],
			"hr_note": ["is", "set"],
		},
		fields=["name", "subject", "hr_note", "status", "modified"],
		order_by="modified asc",
		limit=_QUEUE_FETCH,
	):
		items.append(
			{
				"kind": "request_answered",
				"title": f"HR replied about {row.subject}",
				"detail": row.hr_note,
				"date": str(row.modified),
				"day": None,
				"age_days": age_days(row.modified),
				"action": "Read",
				"to": "/requests",
				"tone": "info",
			}
		)

	waiting = _count_leave_approvals_waiting(employee)
	if waiting:
		items.append(
			{
				"kind": "approvals",
				"title": f"{waiting} request{'' if waiting == 1 else 's'} waiting for your approval",
				"detail": None,
				"date": None,
				"day": None,
				"age_days": None,
				"action": "Review",
				"to": "/approvals",
				"tone": "action",
			}
		)

	for row in frappe.get_all(
		"Leave Application",
		filters={"employee": employee, "status": "Open"},
		fields=["name", "leave_type", "from_date", "to_date"],
		order_by="from_date asc",
		limit=_QUEUE_FETCH,
	):
		items.append(
			{
				"kind": "leave_waiting",
				"title": f"{row.leave_type} waiting for your manager",
				"detail": None,
				"date": str(row.from_date),
				"day": day_for(row.from_date),
				"age_days": age_days(row.from_date),
				"action": "View",
				"to": "/leave",
				"tone": "muted",
			}
		)

	# Severity still leads -- blocked work outranks something merely waiting
	# on somebody else -- but inside a tier the oldest item comes first, so
	# a three-week-old rejection cannot hide under this week's.
	items.sort(key=lambda item: (_TONE_RANK.get(item["tone"], 9), -(item["age_days"] or 0)))
	shown = items[:_QUEUE_LIMIT]
	return {"items": shown, "more": max(0, len(items) - len(shown))}


# Shown on the screen, versus fetched per source. Fetching limit+1 would only
# ever prove "at least one more exists"; a bounded window instead makes the
# "and N more" count exact without a second COUNT query per source, and these
# tables hold a handful of rows per employee.
_QUEUE_LIMIT = 8
_QUEUE_FETCH = 50
# blocked work, then something answered and waiting to be read, then a
# decision this person owes, then their own item waiting on somebody else
_TONE_RANK = {"danger": 0, "info": 1, "action": 2, "muted": 3}


def _as_date(value):
	from frappe.utils import getdate

	return getdate(value)


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


def _count_leave_approvals_waiting(employee):
	# Timesheet approvals join this count in U8/U12, once a Workflow exists
	# for Timesheet to check against.
	from hrms.api import get_leave_applications

	return len(get_leave_applications(employee, approver_id=frappe.session.user, for_approval=True))


# Timesheets (U8, KTD7, KTD10, KTD11)


@frappe.whitelist()
def get_my_week(week_start=None):
	"""The one Timesheet for the Monday..Sunday week containing
	`week_start` (any date in that week; defaults to today), or None if
	the employee hasn't started one yet (KTD10: one week is one
	Timesheet -- the newest non-cancelled one for that Monday)."""
	employee = get_current_employee()
	monday, sunday = get_week_bounds(week_start or today())

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
def act_on_approval(doctype, name, action, comment=None):
	"""Approve or reject a report's pending Leave Application or Timesheet.
	`comment` is required for a reject (R25). Who may actually act is
	checked here on the server, not assumed from what the portal chose to
	show (R26) -- Timesheet goes through the same workflow transition
	the portal's own Submit/Edit actions use (its condition and
	before_submit guard are the real check); Leave Application has no
	Workflow (KTD17), so the equivalent check is explicit here.
	"""
	if doctype not in ("Leave Application", "Timesheet"):
		frappe.throw(_("Not a valid request."))
	if action not in ("Approve", "Reject"):
		frappe.throw(_("Not a valid action."))
	if action == "Reject" and not comment:
		frappe.throw(_("A comment is required to reject."))

	doc = frappe.get_doc(doctype, name)

	if comment:
		doc.add_comment("Comment", comment)

	if doctype == "Timesheet":
		from frappe.model.workflow import apply_workflow

		apply_workflow(doc, action)
		return

	_act_on_leave_application(doc, action)


def _act_on_leave_application(doc, action):
	user = frappe.session.user
	is_hr = set(frappe.get_roles(user)) & {"HR Manager", "System Manager"}
	if not is_hr and user != doc.leave_approver:
		frappe.throw(
			_("Only {0}'s approver or HR can act on this leave request.").format(doc.employee),
			frappe.PermissionError,
		)

	doc.status = "Approved" if action == "Approve" else "Rejected"
	doc.save()
