import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, get_first_day, get_last_day, getdate, today

from helixhr.api import get_my_attendance
from helixhr.tests.utils import EMPLOYEE_USER, make_test_employee_and_manager


class TestHelixHRAttendance(IntegrationTestCase):
	"""R16's exceptions: absent, half day, late and missing.

	The company has no check-in device yet, so the behaviour that matters most
	here is what happens with *no* data: "missing" has to stay dormant rather
	than paint every past working day red.
	"""

	def setUp(self):
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()
		self._clear()
		frappe.set_user(EMPLOYEE_USER)

	def tearDown(self):
		frappe.set_user("Administrator")
		self._clear()

	def _clear(self):
		frappe.set_user("Administrator")
		names = frappe.get_all("Attendance", filters={"employee": self.employee_name}, pluck="name")
		if names:
			frappe.db.delete("Attendance", {"name": ["in", names]})
		frappe.db.commit()

	def _month(self):
		return str(get_first_day(today())), str(get_last_day(today()))

	def _mark(self, date, status="Present", late=0, early=0):
		frappe.set_user("Administrator")
		doc = frappe.get_doc(
			{
				"doctype": "Attendance",
				"employee": self.employee_name,
				"attendance_date": str(getdate(date)),
				"status": status,
				"late_entry": late,
				"early_exit": early,
				"docstatus": 1,
			}
		)
		doc.flags.ignore_validate = True
		doc.flags.ignore_mandatory = True
		doc.insert(ignore_permissions=True)
		frappe.db.commit()
		frappe.set_user(EMPLOYEE_USER)
		return doc.name

	# --- the no-device case, which is the one that ships today -------------

	def test_no_attendance_at_all_reports_nothing_missing(self):
		"""The whole point: with no device configured, a naive
		'working day with no record' rule would mark every past day missing."""
		start, end = self._month()

		result = get_my_attendance(start, end)

		self.assertFalse(result["tracked"])
		self.assertIsNone(result["tracking_since"])
		self.assertEqual(result["missing"], [])
		self.assertEqual(result["exceptions"]["missing"], 0)
		self.assertEqual(result["days"], {})

	def test_shape_is_stable_with_no_data_so_the_page_can_always_render(self):
		start, end = self._month()

		result = get_my_attendance(start, end)

		for key in ("tracked", "tracking_since", "working_days_known", "days", "missing", "summary"):
			self.assertIn(key, result)
		for key in ("absent", "half_day", "late", "missing"):
			self.assertIn(key, result["exceptions"])

	# --- once real data arrives --------------------------------------------

	def test_late_arrival_is_counted_as_an_exception(self):
		start, end = self._month()
		self._mark(add_days(today(), -1), late=1)

		result = get_my_attendance(start, end)

		self.assertEqual(result["exceptions"]["late"], 1)
		self.assertTrue(result["days"][str(getdate(add_days(today(), -1)))]["late"])

	def test_absent_and_half_day_are_counted(self):
		start, end = self._month()
		self._mark(add_days(today(), -2), status="Absent")
		self._mark(add_days(today(), -1), status="Half Day")

		result = get_my_attendance(start, end)

		self.assertEqual(result["exceptions"]["absent"], 1)
		self.assertEqual(result["exceptions"]["half_day"], 1)

	def test_nothing_before_the_first_record_can_be_missing(self):
		"""Tracking that starts mid-month must not retro-flag the days before
		it -- the company was not recording then."""
		start, end = self._month()
		first = getdate(add_days(today(), -2))
		self._mark(first)

		result = get_my_attendance(start, end)

		self.assertTrue(result["tracked"])
		self.assertEqual(result["tracking_since"], str(first))
		for iso in result["missing"]:
			self.assertGreaterEqual(getdate(iso), first, f"{iso} predates tracking")

	def test_today_is_never_missing(self):
		start, end = self._month()
		self._mark(add_days(today(), -3))

		result = get_my_attendance(start, end)

		self.assertNotIn(str(getdate(today())), result["missing"])

	def test_a_day_already_recorded_is_never_missing(self):
		start, end = self._month()
		yesterday = str(getdate(add_days(today(), -1)))
		self._mark(add_days(today(), -3))
		self._mark(yesterday)

		result = get_my_attendance(start, end)

		self.assertNotIn(yesterday, result["missing"])

	def test_another_employees_attendance_never_leaks(self):
		start, end = self._month()
		frappe.set_user("Administrator")
		other = frappe.get_doc(
			{
				"doctype": "Attendance",
				"employee": self.manager_name,
				"attendance_date": str(getdate(add_days(today(), -1))),
				"status": "Present",
				"docstatus": 1,
			}
		)
		other.flags.ignore_validate = True
		other.flags.ignore_mandatory = True
		other.insert(ignore_permissions=True)
		frappe.db.commit()
		frappe.set_user(EMPLOYEE_USER)

		result = get_my_attendance(start, end)

		self.assertEqual(result["days"], {})
		self.assertFalse(result["tracked"])
		# raw delete: Frappe refuses delete_doc on a submitted document
		frappe.set_user("Administrator")
		frappe.db.delete("Attendance", {"name": other.name})
		frappe.db.commit()
