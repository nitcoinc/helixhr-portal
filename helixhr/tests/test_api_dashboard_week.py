import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, today

from helixhr.api import get_dashboard
from helixhr.tests.utils import EMPLOYEE_USER, make_test_employee_and_manager
from helixhr.utils import get_week_bounds


def _as_weekday_offset(monday):
	"""Days from `monday` to today -- the age of a Monday-dated row is
	measured from today, not from the start of the week."""
	return (getdate(today()) - monday).days


class TestHelixHRDashboardWeek(IntegrationTestCase):
	"""The week spine and the action queue -- the two sections the
	redesigned dashboard is built on. Ordering in `needs_you` is the
	screen's whole argument, so it is asserted here rather than left to
	the component."""

	def setUp(self):
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()
		self._clear_week_data()
		frappe.set_user(EMPLOYEE_USER)

	def tearDown(self):
		frappe.set_user("Administrator")
		self._clear_week_data()

	def _clear_week_data(self):
		"""IntegrationTestCase does not roll back between test *methods* on
		this stack (see docs/runbook.md), and every test here reads whatever
		Leave Applications and Timesheets exist for the fixture employee --
		which the fixture deliberately reuses across tests. Without this,
		each test sees the previous one's rows and the suite's result
		depends on method order.

		Scoped to the two fixture employees on purpose: an unfiltered delete
		would also wipe whatever a Playwright run left on a shared dev site,
		which is somebody else's data as far as this test is concerned."""
		frappe.set_user("Administrator")
		employees = [self.employee_name, self.manager_name]
		for doctype in ("Leave Application", "Timesheet", "Attendance"):
			names = frappe.get_all(doctype, filters={"employee": ["in", employees]}, pluck="name")
			if not names:
				continue
			frappe.db.delete(
				"Comment", {"reference_doctype": doctype, "reference_name": ["in", names]}
			)
			frappe.db.delete(doctype, {"name": ["in", names]})
		frappe.db.commit()

	def test_week_is_seven_days_monday_first_anchored_on_today(self):
		week = get_dashboard()["week"]
		monday, sunday = get_week_bounds(today())

		self.assertEqual(week["week_start"], str(monday))
		self.assertEqual(week["week_end"], str(sunday))
		self.assertEqual(len(week["days"]), 7)
		self.assertEqual(week["days"][0]["weekday"], monday.strftime("%a"))
		self.assertEqual(week["days"][0]["date"], str(monday))

	def test_exactly_one_day_is_today_and_later_days_are_future(self):
		days = get_dashboard()["week"]["days"]

		self.assertEqual([day["is_today"] for day in days].count(True), 1)
		index = next(i for i, day in enumerate(days) if day["is_today"])
		self.assertFalse(days[index]["is_future"])
		for day in days[index + 1 :]:
			self.assertTrue(day["is_future"])

	def test_week_has_no_hours_and_no_leave_before_anything_is_booked(self):
		week = get_dashboard()["week"]

		self.assertEqual(week["total_hours"], 0)
		self.assertIsNone(week["timesheet_state"])
		self.assertTrue(all(day["hours"] == 0 for day in week["days"]))
		self.assertTrue(all(day["on_leave"] is False for day in week["days"]))

	def test_approved_leave_marks_every_day_it_covers_inside_the_week(self):
		monday, _ = get_week_bounds(today())
		# A Leave Application stores a range, so the spine has to expand it
		# across the days it touches -- Tue..Thu here, and nothing else.
		self._make_approved_leave(add_days(monday, 1), add_days(monday, 3))

		days = {day["date"]: day["on_leave"] for day in get_dashboard()["week"]["days"]}

		for offset in (1, 2, 3):
			self.assertTrue(days[str(add_days(monday, offset))], f"day {offset} should be on leave")
		for offset in (0, 4, 5, 6):
			self.assertFalse(days[str(add_days(monday, offset))], f"day {offset} should not be")

	def test_leave_running_past_the_week_edge_is_clipped_to_the_week(self):
		monday, sunday = get_week_bounds(today())
		self._make_approved_leave(add_days(monday, -3), add_days(sunday, 3))

		days = get_dashboard()["week"]["days"]

		self.assertEqual(len(days), 7)
		self.assertTrue(all(day["on_leave"] for day in days))

	def test_attendance_summary_counts_a_real_record(self):
		"""Regression: this section passed date objects to
		hrms.api.get_attendance_calendar_events, which is annotated `str`, so
		Frappe's typing validation raised and _safe turned the whole card
		into null -- "Nothing recorded yet" for everyone, forever."""
		frappe.set_user("Administrator")
		frappe.get_doc(
			{
				"doctype": "Attendance",
				"employee": self.employee_name,
				"attendance_date": today(),
				"status": "Present",
				"docstatus": 1,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		frappe.set_user(EMPLOYEE_USER)

		summary = get_dashboard()["attendance_this_month"]

		self.assertIsNotNone(summary, "the section must not swallow itself into null")
		self.assertEqual(summary.get("Present"), 1)

	def test_needs_you_is_empty_for_an_employee_with_nothing_outstanding(self):
		self.assertEqual(get_dashboard()["needs_you"]["items"], [])

	def test_open_leave_shows_up_as_a_row_pointing_at_the_leave_page(self):
		monday, _ = get_week_bounds(today())
		self._make_leave(add_days(monday, 1), add_days(monday, 1), status="Open", submit=False)

		items = get_dashboard()["needs_you"]["items"]

		self.assertEqual(len(items), 1)
		self.assertEqual(items[0]["kind"], "leave_waiting")
		self.assertEqual(items[0]["to"], "/leave")
		self.assertEqual(items[0]["action"], "View")

	def test_a_sent_back_timesheet_leads_the_queue_and_carries_the_reason(self):
		monday, _ = get_week_bounds(today())
		self._make_leave(add_days(monday, 1), add_days(monday, 1), status="Open", submit=False)
		timesheet = self._make_rejected_timesheet("Friday hours are missing.")

		items = get_dashboard()["needs_you"]["items"]
		kinds = [item["kind"] for item in items]

		# Blocked work outranks something merely waiting on somebody else.
		self.assertEqual(kinds[0], "timesheet_rejected")
		self.assertIn("leave_waiting", kinds)
		self.assertEqual(items[0]["detail"], "Friday hours are missing.")
		self.assertEqual(items[0]["action"], "Edit and resubmit")
		self.assertEqual(items[0]["day"], str(monday))
		frappe.delete_doc("Timesheet", timesheet, force=True, ignore_permissions=True)

	def test_an_older_rejection_outranks_a_newer_one_and_reports_its_age(self):
		"""The direction's named risk: a stale item must not sort under a
		fresh one just because the fresh one belongs to this week."""
		monday, _ = get_week_bounds(today())
		self._make_rejected_timesheet("this week", start=monday)
		self._make_rejected_timesheet("three weeks ago", start=add_days(monday, -21))

		items = get_dashboard()["needs_you"]["items"]

		self.assertEqual(items[0]["detail"], "three weeks ago")
		self.assertEqual(items[0]["age_days"], 21 + _as_weekday_offset(monday))
		self.assertIsNone(items[0]["day"], "an out-of-week row carries no day tag")
		self.assertEqual(items[1]["detail"], "this week")
		self.assertEqual(items[1]["day"], str(monday))

	def test_queue_reports_how_many_rows_it_could_not_show(self):
		monday, _ = get_week_bounds(today())
		for offset in range(10):
			self._make_rejected_timesheet(f"week {offset}", start=add_days(monday, -7 * offset))

		queue = get_dashboard()["needs_you"]

		self.assertEqual(len(queue["items"]), 8)
		self.assertEqual(queue["more"], 2)

	def test_queue_never_leaks_another_employees_work(self):
		other = frappe.db.get_value("Employee", {"name": ["!=", self.employee_name]}, "name")
		self.assertTrue(other)
		frappe.set_user("Administrator")
		self._make_rejected_timesheet("not yours", employee=other)

		frappe.set_user(EMPLOYEE_USER)
		self.assertEqual(get_dashboard()["needs_you"]["items"], [])

	# helpers

	def _make_approved_leave(self, from_date, to_date):
		return self._make_leave(from_date, to_date, status="Approved", submit=True)

	def _make_leave(self, from_date, to_date, status, submit):
		leave_type = frappe.db.get_value("Leave Type", {"name": "Casual Leave"}) or frappe.get_doc(
			{"doctype": "Leave Type", "leave_type_name": "Casual Leave"}
		).insert(ignore_permissions=True).name

		doc = frappe.get_doc(
			{
				"doctype": "Leave Application",
				"employee": self.employee_name,
				"leave_type": leave_type,
				"from_date": str(getdate(from_date)),
				"to_date": str(getdate(to_date)),
				"status": status,
				"leave_approver": frappe.db.get_value("Employee", self.manager_name, "user_id"),
			}
		)
		# The spine reads rows straight out of the table; leave validation
		# (allocation, leave period, holiday list) is HRMS's own concern and
		# is covered by its tests, not re-litigated here.
		doc.flags.ignore_validate = True
		doc.flags.ignore_mandatory = True
		doc.insert(ignore_permissions=True)
		if submit:
			frappe.db.set_value("Leave Application", doc.name, "docstatus", 1)
		return doc.name

	def _make_rejected_timesheet(self, comment, employee=None, start=None):
		start = start or get_week_bounds(today())[0]
		doc = frappe.get_doc(
			{
				"doctype": "Timesheet",
				"employee": employee or self.employee_name,
				"start_date": str(start),
				"end_date": str(add_days(start, 6)),
			}
		)
		doc.flags.ignore_validate = True
		doc.flags.ignore_mandatory = True
		doc.insert(ignore_permissions=True)
		frappe.db.set_value("Timesheet", doc.name, "workflow_state", "Rejected")
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Comment",
				"reference_doctype": "Timesheet",
				"reference_name": doc.name,
				"content": comment,
			}
		).insert(ignore_permissions=True)
		return doc.name
