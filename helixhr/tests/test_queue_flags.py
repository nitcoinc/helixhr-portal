"""Plan 2026-10-04-003 U5: the queue's flags, grouping and caps.

R16's flag table, scenario by scenario, computed by the server and shipped
on the row (`KTD7`: the UI never derives a flag, so the display and U6's
batch guard cannot disagree). Weeks are seeded through the real write path
where the scenario is about the write path's side effects (resubmission)
and planted into the tables where the scenario is about the read
(holidays, expected hours), the same split `test_api_team.py` makes.
"""

import hashlib
import json
from datetime import datetime

import frappe
from frappe.model.workflow import apply_workflow
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, today

from helixhr.api import PENDING_SINCE_FIELD, get_my_approvals, save_my_week, submit_my_week
from helixhr.tests.test_api_timesheet import make_test_project
from helixhr.tests.utils import (
	EMPLOYEE_USER,
	MANAGER_USER,
	ensure_leave_allocation,
	ensure_leave_approver_role,
	make_test_employee_and_manager,
	make_test_user,
)
from helixhr.utils import get_week_bounds

STANDARD_HOURS = 8


class TestQueueFlags(IntegrationTestCase):
	def setUp(self):
		self.employee_name, self.employee_user, self.manager_name, self.manager_user = (
			make_test_employee_and_manager()
		)
		# This suite submits leave applications *as the manager*: writing
		# Leave Application.status is a permlevel-1 field, and HRMS's own
		# on_submit refuses an Open status -- without the Leave Approver
		# role, frappe resets permlevel-1 fields to their stored value on
		# every save (the same grant the approval endpoints' fixtures make).
		ensure_leave_approver_role(self.manager_user)
		frappe.db.set_value("Employee", self.employee_name, "reports_to", self.manager_name)
		self.company = frappe.db.get_value("Employee", self.employee_name, "company")

		digest = int(hashlib.md5(self.id().encode()).hexdigest(), 16)
		self.monday = get_week_bounds(add_days(today(), (digest % 200) + 30))[0]
		self.project = make_test_project(f"flags-{self.id().split('.')[-1]}", users=[EMPLOYEE_USER])

		# Weeks from earlier runs of this class persist (the runbook's
		# long-lived-bench note) and a Pending Approval week is not editable,
		# so this test's week starts empty every run.
		for name in frappe.get_all(
			"Timesheet",
			filters={
				"employee": self.employee_name,
				"start_date": ["between", [str(self.monday), str(add_days(self.monday, 6))]],
			},
			pluck="name",
		):
			doc = frappe.get_doc("Timesheet", name)
			if doc.docstatus == 1:
				doc.flags.ignore_permissions = True
				doc.cancel()
			frappe.delete_doc("Timesheet", name, force=1, ignore_permissions=True)

		frappe.set_user("Administrator")
		self._original_standard = frappe.db.get_single_value("HR Settings", "standard_working_hours")
		self._original_holiday_list = frappe.db.get_value("Employee", self.employee_name, "holiday_list")
		frappe.db.set_single_value("HR Settings", "standard_working_hours", STANDARD_HOURS)
		self._assign_five_day_week()

	def tearDown(self):
		frappe.set_user("Administrator")
		# Committed teardown: an addCleanup's raw delete runs after the last
		# commit and is rolled back at the next test's boundary, so the
		# assignment this suite submitted for the shared fixture employee
		# would leak into the holiday suites that follow.
		frappe.db.delete("Holiday List Assignment", {"assigned_to": self.employee_name})
		frappe.db.commit()
		frappe.db.set_single_value("HR Settings", "standard_working_hours", self._original_standard)
		frappe.db.set_value("Employee", self.employee_name, "holiday_list", self._original_holiday_list)

	def _drop_assignment(self, employee, list_name):
		frappe.set_user("Administrator")
		frappe.db.delete(
			"Holiday List Assignment", {"assigned_to": employee, "holiday_list": list_name}
		)
		frappe.db.commit()

	def _assign_five_day_week(self):
		"""A holiday list whose only non-working days are this week's Saturday
		and Sunday, so the expected arithmetic is exactly 8 x 5 = 40 and no
		bench holiday can drift into the scenario."""
		saturday, sunday = add_days(self.monday, 5), add_days(self.monday, 6)
		list_name = f"_Test Queue Flags {self.id().split('.')[-1]}"
		if not frappe.db.exists("Holiday List", list_name):
			frappe.get_doc(
				{
					"doctype": "Holiday List",
					"holiday_list_name": list_name,
					"from_date": self.monday,
					"to_date": sunday,
					"holidays": [
						{"holiday_date": saturday, "weekly_off": 1, "description": "weekly off"},
						{"holiday_date": sunday, "weekly_off": 1, "description": "weekly off"},
					],
				}
			).insert(ignore_permissions=True)
		frappe.db.set_value("Employee", self.employee_name, "holiday_list", list_name)
		self.holiday_list = list_name

	def _row(self, hours_by_date, state="Pending Approval", employee=None, user=None):
		"""One pending (or otherwise) week for the fixture employee, written
		through the real path when the state allows it."""
		employee = employee or self.employee_name
		user = user or EMPLOYEE_USER
		frappe.set_user(user)
		rows = [
			{"date": date, "project": self.project, "task": "", "hours": hours, "note": ""}
			for date, hours in hours_by_date.items()
		]
		name = save_my_week(str(self.monday), json.dumps(rows))
		if state != "Draft":
			apply_workflow({"doctype": "Timesheet", "name": name}, "Submit")
			if state == "Approved":
				frappe.set_user(self.manager_user)
				apply_workflow({"doctype": "Timesheet", "name": name}, "Approve")
				frappe.set_user(user)
		return name

	def _queue(self, user=None):
		frappe.set_user(user or self.manager_user)
		return get_my_approvals(kind="timesheet")

	def _timesheet_row(self, payload, name):
		return next(row for row in payload["pending"] if row["name"] == name)

	def _week_hours(self, day_hours):
		"""The five working days with the hours the scenario names; a None
		day is left out entirely rather than written as a 0-hour row."""
		return {
			str(add_days(self.monday, offset)): hours
			for offset, hours in enumerate(day_hours)
			if hours is not None
		}

	def test_a_full_week_that_matches_expectation_carries_no_flags(self):
		name = self._row(self._week_hours([8, 8, 8, 8, 8]))
		row = self._timesheet_row(self._queue(), name)
		self.assertEqual(row["flags"], {})
		self.assertFalse(row["needs_look"])
		self.assertEqual(row["expected_hours"], 40)

	def test_fifteen_percent_under_expected_flags_hours_off(self):
		name = self._row(self._week_hours([8, 8, 8, 8, 2]))
		row = self._timesheet_row(self._queue(), name)
		self.assertTrue(row["flags"].get("hours_off"))

	def test_exactly_ten_percent_over_is_not_a_flag_but_more_is(self):
		from helixhr.api import recall_my_week

		at_threshold = self._row(self._week_hours([8, 8, 8, 10, 10]))
		self.assertNotIn(
			"hours_off", self._timesheet_row(self._queue(), at_threshold)["flags"]
		)

		# The same week, recalled and rewritten 10% over: +10.0% exactly is
		# not a flag, +11.25% is.
		frappe.set_user(EMPLOYEE_USER)
		recall_my_week(str(self.monday))
		over = self._row(self._week_hours([9, 9, 9, 9, 9]))
		over = self._timesheet_row(self._queue(), over)
		self.assertTrue(over["flags"].get("hours_off"))

	def test_an_empty_working_day_flags_missing_day(self):
		# Four days written, the fifth working day simply not there -- the
		# write path refuses a 0-hour row outright.
		name = self._row(self._week_hours([8, 8, 8, 8, None]))
		row = self._timesheet_row(self._queue(), name)
		self.assertTrue(row["flags"].get("missing_day"))
		self.assertTrue(row["flags"].get("hours_off"))

	def test_holiday_and_leave_days_are_not_expected_to_have_hours(self):
		"""Four of the five working days are off -- two on the holiday list,
		two on approved leave -- so one filled day is a complete week and no
		flag fires."""
		frappe.set_user("Administrator")
		# A fresh list of its own: Wednesday and Thursday join the weekend,
		# and the list is rebuilt every run rather than appended to (the bench
		# keeps this class's rows).
		list_name = f"_Test Queue Flags {self.id().split('.')[-1]}"
		if frappe.db.exists("Holiday List", list_name):
			frappe.delete_doc("Holiday List", list_name, force=1, ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Holiday List",
				"holiday_list_name": list_name,
				"from_date": self.monday,
				"to_date": add_days(self.monday, 6),
				"holidays": [
					{"holiday_date": add_days(self.monday, offset), "weekly_off": 1, "description": "off"}
					for offset in (2, 3, 5, 6)
				],
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value("Employee", self.employee_name, "holiday_list", list_name)
		# The leave half is *submitted*, and the tips path resolves the
		# holiday list through submitted `Holiday List Assignment` rows only
		# (the `Employee.holiday_list` field no longer feeds that path).
		assignment = frappe.get_doc(
			{
				"doctype": "Holiday List Assignment",
				"assigned_to": self.employee_name,
				"holiday_list": list_name,
				"from_date": str(self.monday),
				"to_date": str(add_days(self.monday, 6)),
			}
		)
		assignment.insert(ignore_permissions=True)
		assignment.submit()
		self.addCleanup(
			self._drop_assignment,
			self.employee_name,
			list_name,
		)

		ensure_leave_allocation(self.employee_name, "Casual Leave", 5)
		# Earlier runs leave their leave behind; this week starts clean.
		for name in frappe.get_all(
			"Leave Application",
			filters={
				"employee": self.employee_name,
				"from_date": ["<=", str(add_days(self.monday, 6))],
				"to_date": [">=", str(self.monday)],
			},
			pluck="name",
		):
			doc = frappe.get_doc("Leave Application", name)
			if doc.docstatus == 1:
				doc.flags.ignore_permissions = True
				doc.cancel()
			frappe.delete_doc("Leave Application", name, force=1, ignore_permissions=True)
		frappe.set_user(self.employee_user)
		for offset in (1, 4):
			doc = frappe.get_doc(
				{
					"doctype": "Leave Application",
					"employee": self.employee_name,
					"leave_type": "Casual Leave",
					"from_date": str(add_days(self.monday, offset)),
					"to_date": str(add_days(self.monday, offset)),
					"description": "flag test",
					"leave_approver": self.manager_user,
				}
			)
			doc.insert()
			# Approved leave is what removes the day from the expectation;
			# an open request would not. The approver submits it -- role
			# Employee has no submit on Leave Application.
			doc.status = "Approved"
			frappe.set_user(self.manager_user)
			doc.submit()
			frappe.set_user(self.employee_user)

		frappe.set_user("Administrator")
		name = self._row({str(self.monday): 8})
		row = self._timesheet_row(self._queue(), name)
		self.assertEqual(row["flags"], {})
		self.assertEqual(row["expected_hours"], 8)

	def test_a_resubmission_after_send_back_flags_resubmitted(self):
		from helixhr.api import act_on_approval

		name = self._row(self._week_hours([8, 8, 8, 8, 8]))
		frappe.set_user(self.manager_user)
		modified = frappe.db.get_value("Timesheet", name, "modified")
		act_on_approval(
			"Timesheet",
			name,
			"Send Back",
			comment="Friday is missing entirely.",
			expected_modified=str(modified),
			expected_state="Pending Approval",
		)
		frappe.set_user(EMPLOYEE_USER)
		apply_workflow({"doctype": "Timesheet", "name": name}, "Edit")
		apply_workflow({"doctype": "Timesheet", "name": name}, "Submit")
		frappe.set_user(self.manager_user)

		row = self._timesheet_row(self._queue(), name)
		self.assertTrue(row["flags"].get("resubmitted"))

	def test_an_amended_week_flags_amended(self):
		name = self._row(self._week_hours([8, 8, 8, 8, 8]))
		frappe.set_user("Administrator")
		# The accept path sets this for real (U3's own suite); the flag reads
		# the column, so the column is what the read test plants.
		frappe.db.set_value("Timesheet", name, "amended_from", name)
		frappe.set_user(self.manager_user)

		row = self._timesheet_row(self._queue(), name)
		self.assertTrue(row["flags"].get("amended"))

	def test_a_week_past_the_threshold_flags_overdue(self):
		name = self._row(self._week_hours([8, 8, 8, 8, 8]))
		frappe.set_user("Administrator")
		frappe.db.set_value("Timesheet", name, PENDING_SINCE_FIELD, add_days(today(), -10))
		frappe.set_user(self.manager_user)

		row = self._timesheet_row(self._queue(), name)
		self.assertTrue(row["flags"].get("overdue"))

	def test_no_standard_hours_silences_the_hours_flag_but_nothing_else(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value("HR Settings", "standard_working_hours", 0)

		name = self._row(self._week_hours([8, 8, 8, 8, 2]))
		frappe.db.set_value("Timesheet", name, PENDING_SINCE_FIELD, add_days(today(), -10))
		frappe.set_user(self.manager_user)

		row = self._timesheet_row(self._queue(), name)
		self.assertNotIn("hours_off", row["flags"])
		self.assertIsNone(row["expected_hours"])
		self.assertTrue(row["flags"].get("overdue"))


class TestQueueLeaveFlags(IntegrationTestCase):
	def setUp(self):
		self.employee_name, self.employee_user, self.manager_name, self.manager_user = (
			make_test_employee_and_manager()
		)
		frappe.db.set_value("Employee", self.employee_name, "reports_to", self.manager_name)
		self.company = frappe.db.get_value("Employee", self.employee_name, "company")

	def tearDown(self):
		frappe.set_user("Administrator")

	def _own_employee(self, email):
		"""A fresh employee per method: these tests file Open leave, and both
		this class's transaction and earlier runs leave rows behind, so the
		reused employee's leave history and allocations are cleared first --
		a leftover pending request would change every later balance."""
		name = make_test_user(email, self.company, reports_to=self.manager_name)
		frappe.set_user("Administrator")
		frappe.db.set_value("Employee", name, "reports_to", self.manager_name)
		for leave in frappe.get_all("Leave Application", filters={"employee": name}, pluck="name"):
			doc = frappe.get_doc("Leave Application", leave)
			if doc.docstatus == 1:
				doc.flags.ignore_permissions = True
				doc.cancel()
			frappe.delete_doc("Leave Application", leave, force=1, ignore_permissions=True)
		for allocation in frappe.get_all("Leave Allocation", filters={"employee": name}, pluck="name"):
			doc = frappe.get_doc("Leave Allocation", allocation)
			if doc.docstatus == 1:
				doc.flags.ignore_permissions = True
				doc.cancel()
			frappe.delete_doc("Leave Allocation", allocation, force=1, ignore_permissions=True)
		return name, email

	def _leave(self, user, employee, from_date, to_date, leave_type="Casual Leave"):
		frappe.set_user(user)
		doc = frappe.get_doc(
			{
				"doctype": "Leave Application",
				"employee": employee,
				"leave_type": leave_type,
				"from_date": str(from_date),
				"to_date": str(to_date),
				"description": "flags",
				"leave_approver": self.manager_user,
			}
		)
		doc.insert()
		return doc

	def test_a_balance_that_goes_negative_flags_the_row(self):
		"""A balance that shrank after the request was filed: HRMS refuses an
		over-balance request at insert, but a revocation between filing and
		deciding leaves exactly the row the flag exists for."""
		from helixhr.tests.utils import ensure_holiday_list_assignment

		ensure_holiday_list_assignment(self.company)
		employee, user = self._own_employee("flags-negative@helixhr.test")
		ensure_leave_allocation(employee, "Casual Leave", 5)
		leave = self._leave(user, employee, add_days(today(), 300), add_days(today(), 302))

		frappe.set_user("Administrator")
		allocation = frappe.db.get_value(
			"Leave Allocation", {"employee": employee, "leave_type": "Casual Leave", "docstatus": 1}, "name"
		)
		# Shrink through the document, not a raw column write: HRMS keeps the
		# balance in the Leave Ledger, and only the document's own save
		# adjusts the entries to match.
		allocation_doc = frappe.get_doc("Leave Allocation", allocation)
		allocation_doc.new_leaves_allocated = 1
		allocation_doc.save(ignore_permissions=True)

		frappe.set_user(self.manager_user)
		payload = get_my_approvals(kind="leave")
		row = next(row for row in payload["pending"] if row["name"] == leave.name)
		self.assertTrue(row["flags"].get("negative_balance"))
		self.assertTrue(row["needs_look"])
		self.assertIsNotNone(row["balance_after"])
		self.assertLess(row["balance_after"], 0)

	def test_overlapping_leave_counts_the_other_report(self):
		from helixhr.tests.utils import ensure_holiday_list_assignment

		ensure_holiday_list_assignment(self.company)
		first_employee, first_user = self._own_employee("flags-overlap-a@helixhr.test")
		second_employee, second_user = self._own_employee("flags-overlap-b@helixhr.test")
		ensure_leave_allocation(first_employee, "Casual Leave", 5)
		ensure_leave_allocation(second_employee, "Casual Leave", 5)

		start = add_days(today(), 320)
		first = self._leave(first_user, first_employee, start, add_days(start, 1))
		second = self._leave(second_user, second_employee, start, add_days(start, 1))

		frappe.set_user(self.manager_user)
		payload = get_my_approvals(kind="leave")
		row = next(row for row in payload["pending"] if row["name"] == first.name)
		self.assertTrue(row["flags"].get("overlap"))
		self.assertGreaterEqual(row["overlap_count"], 1)
		row = next(row for row in payload["pending"] if row["name"] == second.name)
		self.assertTrue(row["flags"].get("overlap"))

	def test_leave_starting_within_two_days_flags_short_notice(self):
		from helixhr.tests.utils import ensure_holiday_list_assignment

		ensure_holiday_list_assignment(self.company)
		employee, user = self._own_employee("flags-notice@helixhr.test")
		ensure_leave_allocation(employee, "Casual Leave", 5)
		leave = self._leave(user, employee, add_days(today(), 1), add_days(today(), 2))

		frappe.set_user(self.manager_user)
		payload = get_my_approvals(kind="leave")
		row = next(row for row in payload["pending"] if row["name"] == leave.name)
		self.assertTrue(row["flags"].get("short_notice"))


class TestQueueGrouping(IntegrationTestCase):
	"""R14 / KTD8: the queue is grouped per person on the server, oldest
	waiting first, and a 50-report team comes back whole with a bounded
	number of queries."""

	def setUp(self):
		self.employee_name, self.employee_user, self.manager_name, self.manager_user = (
			make_test_employee_and_manager()
		)
		self.company = frappe.db.get_value("Employee", self.employee_name, "company")
		frappe.set_user("Administrator")
		for name in frappe.get_all(
			"Employee", filters={"employee_number": ["like", "_test-queue-grp-%"]}, pluck="name"
		):
			frappe.db.set_value("Employee", name, "reports_to", None)
		frappe.db.set_value("Employee", self.employee_name, "reports_to", self.manager_name)
		digest = int(hashlib.md5(self.id().encode()).hexdigest(), 16)
		self.monday = get_week_bounds(add_days(today(), (digest % 200) + 40))[0]
		self.planted = []
		# Earlier runs leave their planted weeks behind; clear every Pending
		# Approval week this team could show so this run's queue is this
		# run's (approved and decided rows never reach the queue).
		self._drop_bulk_weeks()
		for name in frappe.get_all(
			"Timesheet",
			filters={
				"employee": self.employee_name,
				"workflow_state": "Pending Approval",
				"docstatus": 0,
			},
			pluck="name",
		):
			frappe.db.set_value("Timesheet", name, "workflow_state", "Cancelled")
			frappe.db.set_value("Timesheet", name, "docstatus", 2)
			frappe.delete_doc("Timesheet", name, force=1, ignore_permissions=True)

	def _drop_bulk_weeks(self):
		bulk = frappe.get_all(
			"Employee", filters={"employee_number": ["like", "_test-queue-grp-%"]}, pluck="name"
		)
		for name in frappe.get_all(
			"Timesheet", filters={"employee": ["in", bulk or [""]]}, pluck="name"
		):
			doc = frappe.get_doc("Timesheet", name)
			if doc.docstatus == 1:
				doc.flags.ignore_permissions = True
				doc.cancel()
			frappe.delete_doc("Timesheet", name, force=1, ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		for name in self.planted:
			if frappe.db.exists("Timesheet", name):
				doc = frappe.get_doc("Timesheet", name)
				if doc.docstatus == 1:
					doc.flags.ignore_permissions = True
					doc.cancel()
				frappe.delete_doc("Timesheet", name, force=1, ignore_permissions=True)
		self._drop_bulk_weeks()
		for name in frappe.get_all(
			"Employee", filters={"employee_number": ["like", "_test-queue-grp-%"]}, pluck="name"
		):
			frappe.db.set_value("Employee", name, "reports_to", None)

	def _report(self, index):
		number = f"_test-queue-grp-{index}"
		name = frappe.db.get_value("Employee", {"employee_number": number}, "name")
		if not name:
			name = (
				frappe.get_doc(
					{
						"doctype": "Employee",
						"employee_number": number,
						"first_name": "Queue",
						"last_name": str(index),
						"company": self.company,
						"date_of_birth": "1990-01-01",
						"date_of_joining": "2020-01-01",
						"gender": frappe.db.get_value("Gender", {}, "name"),
						"status": "Active",
					}
				)
				.insert(ignore_permissions=True)
				.name
			)
		frappe.db.set_value("Employee", name, "reports_to", self.manager_name)
		return name

	def _pending_week(self, employee, hours, monday=None):
		"""One Pending Approval week, planted -- the read under test is the
		queue's grouping, not the write path. Idempotent across runs (the
		bench keeps this class's data), and the manager's DocShare is written
		the way `timesheet_on_update` would have, because that share is part
		of what makes a pending week visible to this caller at all."""
		monday = monday or self.monday
		for name in frappe.get_all(
			"Timesheet",
			filters={"employee": employee, "start_date": ["between", [str(monday), str(add_days(monday, 6))]]},
			pluck="name",
		):
			doc = frappe.get_doc("Timesheet", name)
			if doc.docstatus == 1:
				doc.flags.ignore_permissions = True
				doc.cancel()
			frappe.delete_doc("Timesheet", name, force=1, ignore_permissions=True)

		doc = frappe.get_doc(
			{
				"doctype": "Timesheet",
				"employee": employee,
				"company": self.company,
				"time_logs": [
					{
						"project": None,
						"activity_type": "General",
						"from_time": datetime.strptime(f"{monday} 09:00:00", "%Y-%m-%d %H:%M:%S"),
						"to_time": datetime.strptime(f"{monday} {9 + hours}:00:00", "%Y-%m-%d %H:%M:%S"),
						"hours": hours,
					}
				],
			}
		)
		doc.insert(ignore_permissions=True)
		frappe.db.set_value("Timesheet", doc.name, "workflow_state", "Pending Approval")
		frappe.share.add_docshare(
			"Timesheet",
			doc.name,
			self.manager_user,
			write=1,
			submit=1,
			flags={"ignore_share_permission": True},
		)
		self.planted.append(doc.name)
		return doc.name

	def test_oldest_waiting_first_and_one_group_per_person(self):
		first = self._pending_week(self.employee_name, 4)
		second_report = self._report(1)
		second = self._pending_week(second_report, 2)
		# The same person's second, older week waits longer than anyone's.
		oldest = self._pending_week(self.employee_name, 4, add_days(self.monday, -7))
		# All three were planted inside the same second; give the older week
		# the modified time it would have had, so the oldest-first order is
		# decided by waiting and not by the name tie-break.
		frappe.db.set_value(
			"Timesheet",
			oldest,
			"modified",
			str(add_days(frappe.utils.today(), -3)),
			update_modified=False,
		)

		frappe.set_user(self.manager_user)
		payload = get_my_approvals(kind="timesheet")
		names = [row["name"] for row in payload["pending"]]
		self.assertEqual(names, [oldest, first, second])

		people = {group["employee"]: group for group in payload["people"]}
		self.assertEqual(len(payload["people"]), 2)
		self.assertEqual(people[self.employee_name]["count"], 2)
		self.assertEqual(people[self.employee_name]["oldest_sent_on"], payload["pending"][0]["sent_on"])
		self.assertEqual(people[second_report]["count"], 1)

	def test_fifty_reports_come_back_whole_with_a_bounded_number_of_queries(self):
		self._pending_week(self.employee_name, 1)
		for index in range(49):
			self._pending_week(self._report(index), 1)
		frappe.set_user(self.manager_user)

		payload = get_my_approvals(kind="timesheet")
		self.assertEqual(len(payload["pending"]), 50)
		self.assertEqual(len(payload["people"]), 50)

		# The batching assertion aims at the timesheet collector itself -- the
		# whole-queue call legitimately also runs the other kinds' collectors
		# (their rows feed `counts`), and counting those would measure the
		# queue, not this read.
		from unittest.mock import patch

		from helixhr.api import _timesheet_summaries

		count = {"n": 0}
		real_sql = frappe.db.sql

		def counting_sql(*args, **kwargs):
			count["n"] += 1
			return real_sql(*args, **kwargs)

		employee = frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "name")
		today = getdate(frappe.utils.today())
		_timesheet_summaries(employee, today)  # warm the meta caches
		with patch.object(frappe.db, "sql", side_effect=counting_sql):
			rows = _timesheet_summaries(employee, today)

		self.assertEqual(len(rows), 50)
		self.assertLessEqual(count["n"], 12, "the timesheet queue must stay batched")

		# Hand the bulk reports back, the way the team suite does.
		frappe.set_user("Administrator")
		for index in range(49):
			name = frappe.db.get_value("Employee", {"employee_number": f"_test-queue-grp-{index}"})
			frappe.db.set_value("Employee", name, "reports_to", None)
