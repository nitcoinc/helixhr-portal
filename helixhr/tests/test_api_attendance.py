import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, get_first_day, get_last_day, getdate, today

from helixhr.api import _ATTENDANCE_MAX_DAYS, get_my_attendance, get_my_checkins
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

	def _mark(self, date, status="Present", late=0, early=0, request=None):
		"""One Attendance row. `request` links it to an Attendance Request the
		way HRMS's own `create_or_update_attendance` does -- link plus, for a
		half day, `half_day_status` 'Absent' (P3-R19)."""
		frappe.set_user("Administrator")
		doc = frappe.get_doc(
			{
				"doctype": "Attendance",
				"employee": self.employee_name,
				"attendance_date": str(getdate(date)),
				"status": status,
				"late_entry": late,
				"early_exit": early,
				"attendance_request": request,
				"half_day_status": "Absent" if status == "Half Day" else None,
				"docstatus": 1,
			}
		)
		doc.flags.ignore_validate = True
		doc.flags.ignore_mandatory = True
		doc.insert(ignore_permissions=True)
		frappe.db.commit()
		frappe.set_user(EMPLOYEE_USER)
		return doc.name

	def _request(self, date, reason="Work From Home", half_day=False):
		"""An Attendance Request for one day, as the link target of a row
		`_mark` writes.

		Left in draft on purpose: submitting it would have HRMS write the
		Attendance rows itself, and *which* days it then writes depends on the
		employee's holiday list, shift assignments and leave -- none of which
		this test is about. What `get_my_attendance` reads is the Attendance
		row's `attendance_request` link, and that is what this gives it.
		"""
		frappe.set_user("Administrator")
		doc = frappe.get_doc(
			{
				"doctype": "Attendance Request",
				"employee": self.employee_name,
				"company": frappe.db.get_value("Employee", self.employee_name, "company"),
				"from_date": str(getdate(date)),
				"to_date": str(getdate(date)),
				"reason": reason,
				"explanation": "P3-R19 test",
				"half_day": 1 if half_day else 0,
				"half_day_date": str(getdate(date)) if half_day else None,
			}
		)
		doc.flags.ignore_validate = True
		doc.flags.ignore_mandatory = True
		doc.insert(ignore_permissions=True)
		frappe.db.commit()
		self.addCleanup(self._drop_request, doc.name)
		frappe.set_user(EMPLOYEE_USER)
		return doc.name

	def _drop_request(self, name):
		frappe.set_user("Administrator")
		if frappe.db.exists("Attendance Request", name):
			frappe.delete_doc("Attendance Request", name, force=True, ignore_permissions=True)
		frappe.db.commit()

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

	# --- days an approved request marked (P3-R19) --------------------------

	def test_a_day_a_work_from_home_request_marked_is_shown_but_never_an_exception(self):
		"""P3-R19. The day is on the calendar with its status and carries
		`by_request`, so the page can say who fixed it -- and it is not an
		exception, because the employee has already had it put right."""
		start, end = self._month()
		date = str(getdate(add_days(today(), -2)))
		self._mark(date, status="Work From Home", request=self._request(date))

		result = get_my_attendance(start, end)

		self.assertIn(date, result["days"])
		self.assertEqual(result["days"][date]["status"], "Work From Home")
		self.assertTrue(result["days"][date]["by_request"])
		self.assertNotIn(date, result["missing"])
		self.assertEqual(result["exceptions"]["absent"], 0)
		self.assertEqual(result["exceptions"]["half_day"], 0)
		self.assertEqual(result["exceptions"]["late"], 0)

	def test_a_half_day_a_request_marked_is_not_a_half_day_exception(self):
		"""The one HRMS actually writes: a half-day request leaves a Half Day
		row whose other half is Absent. Counting it would send the employee
		back to HR about a day HR has already fixed."""
		start, end = self._month()
		date = str(getdate(add_days(today(), -2)))
		self._mark(date, status="Half Day", request=self._request(date, half_day=True))

		result = get_my_attendance(start, end)

		self.assertEqual(result["days"][date]["status"], "Half Day")
		self.assertTrue(result["days"][date]["by_request"])
		self.assertEqual(result["exceptions"]["half_day"], 0)

	def test_a_late_day_a_request_marked_is_not_a_late_exception(self):
		start, end = self._month()
		date = str(getdate(add_days(today(), -2)))
		self._mark(date, late=1, request=self._request(date))

		result = get_my_attendance(start, end)

		self.assertTrue(result["days"][date]["late"])
		self.assertEqual(result["exceptions"]["late"], 0)

	def test_the_same_days_marked_the_ordinary_way_are_still_exceptions(self):
		"""The other side of it: without a request, nothing changes -- the
		exclusion is the request, not the status."""
		start, end = self._month()
		absent = str(getdate(add_days(today(), -3)))
		half = str(getdate(add_days(today(), -2)))
		late = str(getdate(add_days(today(), -1)))
		self._mark(absent, status="Absent")
		self._mark(half, status="Half Day")
		self._mark(late, late=1)

		result = get_my_attendance(start, end)

		self.assertFalse(result["days"][absent]["by_request"])
		self.assertEqual(result["exceptions"]["absent"], 1)
		self.assertEqual(result["exceptions"]["half_day"], 1)
		self.assertEqual(result["exceptions"]["late"], 1)

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

	# --- bounds (P2-U5 scenario 8, P2-R22) ---------------------------------

	def test_a_reversed_range_is_refused(self):
		start, end = self._month()

		with self.assertRaises(frappe.ValidationError):
			get_my_attendance(end, start)

	def test_a_range_longer_than_a_year_is_refused(self):
		"""Refused from the parameters alone, before a single Attendance row
		or holiday list is read -- an over-limit span must be cheap to say no
		to, not expensive to answer."""
		start = str(getdate(today()))
		end = str(getdate(add_days(today(), _ATTENDANCE_MAX_DAYS + 1)))

		with self.assertRaises(frappe.ValidationError):
			get_my_attendance(start, end)

	def test_a_valid_month_returns_the_same_answer_twice(self):
		"""The holiday list is now resolved once per request rather than once
		per question asked of it (P2-U5 step 6). Same input, same statuses,
		same exception counts."""
		start, end = self._month()
		self._mark(add_days(today(), -2), status="Absent")
		self._mark(add_days(today(), -1), late=1)

		first = get_my_attendance(start, end)
		second = get_my_attendance(start, end)

		self.assertEqual(first["days"], second["days"])
		self.assertEqual(first["summary"], second["summary"])
		self.assertEqual(first["exceptions"], second["exceptions"])
		self.assertEqual(first["exceptions"]["absent"], 1)
		self.assertEqual(first["exceptions"]["late"], 1)

	# --- the day sheet's own read (P2-R27) ---------------------------------

	def test_checkins_are_scoped_to_the_caller(self):
		frappe.set_user("Administrator")

		# Registered before the first insert, and again as the last statement
		# of the test, because the rows below are committed: a run interrupted
		# between the insert and the hand cleanup used to leave a punch behind,
		# and the next run collided with it on HRMS's same-timestamp rule
		# rather than failing on anything real.
		def _clear_punches():
			frappe.set_user("Administrator")
			frappe.db.delete(
				"Employee Checkin", {"employee": ["in", [self.employee_name, self.manager_name]]}
			)
			frappe.db.commit()  # nosemgrep

		_clear_punches()
		self.addCleanup(_clear_punches)

		frappe.get_doc(
			{
				"doctype": "Employee Checkin",
				"employee": self.manager_name,
				"time": f"{getdate(today())} 09:15:00",
				"log_type": "IN",
			}
		).insert(ignore_permissions=True)
		mine = frappe.get_doc(
			{
				"doctype": "Employee Checkin",
				"employee": self.employee_name,
				"time": f"{getdate(today())} 09:05:00",
				"log_type": "IN",
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()

		frappe.set_user(EMPLOYEE_USER)
		rows = get_my_checkins(str(getdate(today())))

		self.assertEqual([row["name"] for row in rows], [mine.name])

		# Committed above so the employee session can see them, so they are
		# removed by hand -- the per-test rollback cannot reach a commit.
		frappe.set_user("Administrator")
		frappe.db.delete("Employee Checkin", {"employee": ["in", [self.employee_name, self.manager_name]]})
		frappe.db.commit()

	def test_checkins_for_a_day_with_nothing_recorded_are_empty(self):
		frappe.set_user(EMPLOYEE_USER)

		self.assertEqual(get_my_checkins("2020-01-01"), [])

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
