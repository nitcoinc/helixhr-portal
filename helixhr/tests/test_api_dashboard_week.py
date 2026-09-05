import uuid

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, today

from helixhr.api import get_dashboard
from helixhr.tests.utils import EMPLOYEE_USER, MANAGER_USER, make_test_employee_and_manager
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
		# HR Requests and the reply notifications they produce outlive a test
		# method exactly like the leave and timesheet rows below, and an
		# unread reply is a queue row -- left behind, it turns every later
		# test's "the queue is empty" into a coin toss.
		requests = frappe.get_all("HR Request", filters={"employee": ["in", employees]}, pluck="name")
		if requests:
			frappe.db.delete("Notification Log", {"document_type": "HR Request", "document_name": ["in", requests]})
			frappe.db.delete("HR Request", {"name": ["in", requests]})
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

	def test_open_leave_waits_on_the_manager_and_is_not_the_employees_action(self):
		"""P2-U4 scenario 3. It is still their leave and still visible -- but
		the only honest action on it is "wait", so it belongs in the quieter
		waiting list, not in a queue called "Needs you"."""
		monday, _ = get_week_bounds(today())
		name = self._make_leave(add_days(monday, 1), add_days(monday, 1), status="Open", submit=False)

		queue = get_dashboard()["needs_you"]

		self.assertEqual(queue["items"], [])
		self.assertEqual(len(queue["waiting"]), 1)
		row = queue["waiting"][0]
		self.assertEqual(row["kind"], "leave_waiting")
		self.assertEqual(row["owner"], "manager")
		self.assertEqual(row["urgency"], "waiting")
		self.assertEqual(row["reference_doctype"], "Leave Application")
		self.assertEqual(row["reference_name"], name)
		self.assertEqual(row["to"], {"name": "LeaveDetail", "params": {"name": name}})
		self.assertEqual(row["action"], "View")
		self.assertEqual(row["id"], f"leave_waiting:{name}")

	def test_a_sent_back_timesheet_leads_the_queue_and_carries_the_reason(self):
		monday, _ = get_week_bounds(today())
		self._make_leave(add_days(monday, 1), add_days(monday, 1), status="Open", submit=False)
		timesheet = self._make_rejected_timesheet("Friday hours are missing.")

		queue = get_dashboard()["needs_you"]
		items = queue["items"]

		# Blocked work is the queue; something merely waiting on somebody
		# else is beside it, not above or below it.
		self.assertEqual([item["kind"] for item in items], ["timesheet_rejected"])
		self.assertEqual([item["kind"] for item in queue["waiting"]], ["leave_waiting"])
		self.assertEqual(items[0]["detail"], "Friday hours are missing.")
		self.assertEqual(items[0]["action"], "Edit and resubmit")
		self.assertEqual(items[0]["day"], str(monday))
		self.assertEqual(items[0]["urgency"], "blocked")
		self.assertEqual(items[0]["owner"], "you")
		self.assertEqual(
			items[0]["to"], {"name": "TimesheetWeek", "params": {"weekStart": str(monday)}}
		)
		frappe.delete_doc("Timesheet", timesheet, force=True, ignore_permissions=True)

	def test_two_sent_back_weeks_are_two_stable_items_each_opening_its_own_week(self):
		"""Covers P2-AE5. Opening the older item has to open the older week
		and the older reason -- both rows pointed at "/timesheet", which
		resolves to whichever week is current when the link is followed."""
		monday, _ = get_week_bounds(today())
		older = add_days(monday, -21)
		newer = self._make_rejected_timesheet("this week", start=monday)
		older_name = self._make_rejected_timesheet("three weeks ago", start=older)

		items = get_dashboard()["needs_you"]["items"]

		self.assertEqual(len(items), 2)
		self.assertEqual(
			[item["id"] for item in items],
			[f"timesheet_rejected:{older_name}", f"timesheet_rejected:{newer}"],
		)
		self.assertEqual(
			[item["to"] for item in items],
			[
				{"name": "TimesheetWeek", "params": {"weekStart": str(older)}},
				{"name": "TimesheetWeek", "params": {"weekStart": str(monday)}},
			],
		)
		self.assertEqual([item["detail"] for item in items], ["three weeks ago", "this week"])

	def test_an_hr_reply_is_a_queue_row_for_as_long_as_its_notification_is_unread(self):
		"""P2-U4 scenario 5 / P2-KTD6. The obligation *is* the unread
		notification, so reading it clears the row -- no second seen-state
		model, and no "HR replied" row that never goes away."""
		request = self._make_request_with_reply("Collect it from reception.")

		items = get_dashboard()["needs_you"]["items"]
		self.assertEqual(len(items), 1)
		row = items[0]
		self.assertEqual(row["kind"], "request_answered")
		self.assertEqual(row["urgency"], "unread")
		self.assertEqual(row["reference_name"], request)
		self.assertEqual(row["to"], {"name": "RequestDetail", "params": {"name": request}})
		self.assertEqual(row["detail"], "Collect it from reception.")
		self.assertTrue(row["notification"])

		frappe.db.set_value("Notification Log", row["notification"], "read", 1)

		self.assertEqual(get_dashboard()["needs_you"]["items"], [])

	def test_a_manager_sees_both_kinds_of_decision_each_opening_the_exact_one(self):
		"""P2-U4 scenario 2. A pending timesheet was invisible everywhere:
		the queue row and the Approvals nav item both read a leave-only
		count."""
		monday, _ = get_week_bounds(today())
		leave = self._make_leave(
			add_days(monday, 1), add_days(monday, 1), status="Open", submit=False
		)
		timesheet = self._make_pending_timesheet(start=monday)

		frappe.set_user(MANAGER_USER)
		queue = get_dashboard()["needs_you"]
		by_kind = {item["kind"]: item for item in queue["items"]}

		self.assertIn("approval_leave", by_kind)
		self.assertIn("approval_timesheet", by_kind)
		self.assertEqual(
			by_kind["approval_leave"]["to"],
			{"name": "ApprovalDetail", "params": {"kind": "leave", "name": leave}},
		)
		self.assertEqual(
			by_kind["approval_timesheet"]["to"],
			{"name": "ApprovalDetail", "params": {"kind": "timesheet", "name": timesheet}},
		)
		self.assertEqual(by_kind["approval_leave"]["urgency"], "decision")
		self.assertEqual(get_dashboard()["pending"]["approvals_waiting_for_me"], 2)

	def test_an_approver_with_no_direct_reports_still_gets_the_approvals_entry(self):
		"""The second half of scenario 2: `can_approve` gated the nav item on
		the direct-report count, so a leave approver who manages nobody could
		not reach the decision they had been asked to make."""
		from helixhr.api import get_portal_bootstrap

		monday, _ = get_week_bounds(today())
		self._make_leave(add_days(monday, 1), add_days(monday, 1), status="Open", submit=False)
		frappe.set_user("Administrator")
		frappe.db.set_value("Employee", self.employee_name, "reports_to", None)
		self.addCleanup(
			frappe.db.set_value, "Employee", self.employee_name, "reports_to", self.manager_name
		)

		frappe.set_user(MANAGER_USER)
		boot = get_portal_bootstrap()

		self.assertEqual(boot["report_count"], 0)
		self.assertTrue(boot["can_approve"])

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

	def test_get_my_week_carries_the_managers_reason_for_sending_it_back(self):
		"""Regression: the Timesheet page read this with
		`frappe.client.get_list` on Comment, which the Employee Self Service
		role cannot read -- the call 403'd, so an employee was told their week
		was sent back and never told why. It ships with the week now."""
		from helixhr.api import get_my_week

		monday, _ = get_week_bounds(today())
		self._make_rejected_timesheet("Friday hours are missing.", start=monday)

		week = get_my_week()

		self.assertEqual(week["timesheet"]["workflow_state"], "Rejected")
		self.assertEqual(week["timesheet"]["rejection_comment"], "Friday hours are missing.")

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

	def _make_request_with_reply(self, note):
		"""An HR Request the employee owns, answered by somebody else -- the
		reply notification is written by the doc event, exactly as it is in
		production."""
		from helixhr.api import create_my_request

		frappe.set_user(EMPLOYEE_USER)
		# P2-U8: the portal's own method -- role Employee has no generic
		# `create` on HR Request any more.
		doc = frappe.get_doc(
			"HR Request",
			create_my_request(
				category="HR Letter",
				subject="Address proof",
				details="For my bank.",
				operation_key=str(uuid.uuid4()),
			)["name"],
		)
		frappe.set_user("Administrator")
		doc.reload()
		doc.hr_note = note
		doc.save()
		frappe.set_user(EMPLOYEE_USER)
		return doc.name

	def _make_pending_timesheet(self, start=None):
		"""A timesheet waiting on the manager. Inserted straight into
		Pending Approval and shared the way `timesheet_on_update` shares it,
		so the read under test is the manager's real permission path (a
		DocShare), not an ignore_permissions shortcut."""
		start = start or get_week_bounds(today())[0]
		frappe.set_user("Administrator")
		doc = frappe.get_doc(
			{
				"doctype": "Timesheet",
				"employee": self.employee_name,
				"start_date": str(start),
				"end_date": str(add_days(start, 6)),
			}
		)
		doc.flags.ignore_validate = True
		doc.flags.ignore_mandatory = True
		doc.insert(ignore_permissions=True)
		frappe.db.set_value("Timesheet", doc.name, "workflow_state", "Pending Approval")
		frappe.share.add_docshare(
			"Timesheet",
			doc.name,
			MANAGER_USER,
			write=1,
			submit=1,
			flags={"ignore_share_permission": True},
		)
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
