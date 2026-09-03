import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import get_first_day, get_last_day, today
from hrms.api import get_attendance_calendar_events

from helixhr.tests.utils import EMPLOYEE_USER, make_test_employee_and_manager


class TestAttendanceRead(IntegrationTestCase):
	def setUp(self):
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()

	def tearDown(self):
		frappe.set_user("Administrator")

	def _mark_attendance(self, employee, date, status):
		existing = frappe.db.exists("Attendance", {"employee": employee, "attendance_date": date})
		if existing:
			return existing
		company = frappe.db.get_value("Employee", employee, "company")
		doc = frappe.get_doc(
			{
				"doctype": "Attendance",
				"employee": employee,
				"attendance_date": date,
				"status": status,
				"company": company,
			}
		)
		doc.insert(ignore_permissions=True)
		doc.submit()
		return doc.name

	def test_month_with_mixed_statuses_shows_the_right_counts(self):
		start = str(get_first_day(today()))
		self._mark_attendance(self.employee_name, start, "Present")
		self._mark_attendance(self.employee_name, frappe.utils.add_days(start, 1), "Absent")
		self._mark_attendance(self.employee_name, frappe.utils.add_days(start, 2), "Half Day")

		frappe.set_user(EMPLOYEE_USER)
		events = get_attendance_calendar_events(start, str(get_last_day(today())))

		statuses = list(events.values())
		self.assertEqual(statuses.count("Present"), 1)
		self.assertEqual(statuses.count("Absent"), 1)
		self.assertEqual(statuses.count("Half Day"), 1)

	def test_employee_a_cannot_see_employee_b_checkins(self):
		frappe.set_user("Administrator")
		frappe.get_doc(
			{
				"doctype": "Employee Checkin",
				"employee": self.manager_name,
				"time": frappe.utils.now_datetime(),
				"log_type": "IN",
			}
		).insert(ignore_permissions=True)

		frappe.set_user(EMPLOYEE_USER)
		rows = frappe.get_list(
			"Employee Checkin", filters={"employee": self.manager_name}, fields=["name"]
		)
		self.assertEqual(len(rows), 0)

	def test_month_with_no_data_is_empty_not_an_error(self):
		frappe.set_user(EMPLOYEE_USER)
		events = get_attendance_calendar_events("2020-01-01", "2020-01-31")
		self.assertEqual(events, {})
