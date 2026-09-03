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
	start, end = get_first_day(today()), get_last_day(today())
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
