import frappe
from frappe.utils import get_first_day, get_last_day, today
from hrms.api import get_attendance_calendar_events, get_current_employee, get_leave_balance_map


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


def _get_unread_notification_count():
	return frappe.db.count("Notification Log", {"for_user": frappe.session.user, "read": 0})
