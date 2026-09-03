import hashlib
import json

import frappe
from frappe.model.workflow import apply_workflow
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from helixhr.api import act_on_approval, save_my_week
from helixhr.tests.utils import (
	EMPLOYEE_USER,
	MANAGER_USER,
	ensure_leave_allocation,
	ensure_leave_approver_role,
	make_test_employee_and_manager,
)
from helixhr.utils import get_week_bounds


class TestApiApprovals(IntegrationTestCase):
	def setUp(self):
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()
		frappe.db.set_value("Employee", self.employee_name, "reports_to", self.manager_name)
		frappe.db.set_value("Employee", self.employee_name, "leave_approver", MANAGER_USER)
		ensure_leave_approver_role(MANAGER_USER)

		# Hash the full test id (module+class+method), not just the bare
		# method name: two different test *files* each hashing their own
		# method names independently can still collide with each other on
		# the same week (confirmed directly against test_api_timesheet.py
		# -- two overlapping Timesheets landed in the same run). Also
		# widened from mod 5000 to mod 200000 for the same reason.
		digest = int(hashlib.md5(self.id().encode()).hexdigest(), 16)
		self.monday, _ = get_week_bounds(add_days(today(), (digest % 200000) * 7))
		# A separate, small offset for Leave Application dates -- ensure_
		# leave_allocation only covers the current calendar year, so the
		# wide multi-year spread used for self.monday (to keep Timesheet
		# weeks apart) would land outside the allocation for most test
		# methods. Capped well under a year so it can never cross into
		# next year's un-allocated range.
		self.leave_date = add_days(today(), digest % 100)

	def tearDown(self):
		frappe.set_user("Administrator")

	def _pending_leave(self):
		# self.monday is this test method's own unique week (see setUp) --
		# reused here too so different test methods never collide on the
		# same "tomorrow" date (state isn't rolled back between test
		# *methods* in this environment; see the runbook).
		ensure_leave_allocation(self.employee_name, "Casual Leave", 5)
		frappe.set_user(EMPLOYEE_USER)
		doc = frappe.get_doc(
			{
				"doctype": "Leave Application",
				"employee": self.employee_name,
				"leave_type": "Casual Leave",
				"from_date": str(self.leave_date),
				"to_date": str(self.leave_date),
				"description": "test",
				"leave_approver": MANAGER_USER,
			}
		)
		doc.insert()
		return doc

	def _make_project(self):
		from helixhr.tests.utils import TEST_COMPANY, ensure_test_company

		name = f"_Test Approval Project {self.id().split('.')[-1]}"
		existing = frappe.db.get_value("Project", {"project_name": name}, "name")
		if existing:
			return existing
		ensure_test_company()
		doc = frappe.get_doc(
			{"doctype": "Project", "project_name": name, "status": "Open", "company": TEST_COMPANY}
		)
		doc.insert(ignore_permissions=True)
		if not frappe.db.exists(
			"User Permission", {"user": EMPLOYEE_USER, "allow": "Project", "for_value": doc.name}
		):
			frappe.get_doc(
				{"doctype": "User Permission", "user": EMPLOYEE_USER, "allow": "Project", "for_value": doc.name}
			).insert(ignore_permissions=True)
		return doc.name

	def _pending_timesheet(self):
		from helixhr.tests.utils import ensure_holiday_list_assignment

		company = frappe.db.get_value("Employee", self.employee_name, "company")
		ensure_holiday_list_assignment(company)
		project = self._make_project()

		frappe.set_user(EMPLOYEE_USER)
		name = save_my_week(
			str(self.monday),
			json.dumps([{"date": str(self.monday), "project": project, "hours": 4, "note": ""}]),
		)
		apply_workflow({"doctype": "Timesheet", "name": name}, "Submit")
		return frappe.get_doc("Timesheet", name)

	def test_managers_list_contains_reports_pending_items_not_their_own(self):
		leave = self._pending_leave()

		frappe.set_user(MANAGER_USER)
		waiting = frappe.get_list(
			"Leave Application",
			filters={"leave_approver": MANAGER_USER, "status": "Open", "docstatus": 0},
			pluck="name",
		)
		self.assertIn(leave.name, waiting)

		own_leaves = frappe.get_list(
			"Leave Application", filters={"employee": self.manager_name}, pluck="name"
		)
		self.assertNotIn(leave.name, own_leaves)

	def test_reject_leave_without_comment_is_refused(self):
		leave = self._pending_leave()

		frappe.set_user(MANAGER_USER)
		with self.assertRaises(frappe.ValidationError):
			act_on_approval("Leave Application", leave.name, "Reject")

	def test_approve_leave_sets_status_and_employee_sees_it(self):
		leave = self._pending_leave()

		frappe.set_user(MANAGER_USER)
		act_on_approval("Leave Application", leave.name, "Approve")

		frappe.set_user(EMPLOYEE_USER)
		self.assertEqual(frappe.db.get_value("Leave Application", leave.name, "status"), "Approved")

	def test_wrong_approver_cannot_act_on_leave(self):
		"""AE4 analogue for leave (R26)."""
		leave = self._pending_leave()

		wrong_approver = "wrong-leave-approver@helixhr.test"
		if not frappe.db.exists("User", wrong_approver):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": wrong_approver,
					"first_name": "Wrong",
					"last_name": "Approver",
					"send_welcome_email": 0,
					"roles": [{"doctype": "Has Role", "role": "Employee"}],
				}
			).insert(ignore_permissions=True)
		ensure_leave_approver_role(wrong_approver)

		frappe.set_user(wrong_approver)
		with self.assertRaises(frappe.PermissionError):
			act_on_approval("Leave Application", leave.name, "Approve")

		self.assertEqual(frappe.db.get_value("Leave Application", leave.name, "status"), "Open")

	def test_reject_timesheet_with_comment_via_act_on_approval(self):
		"""AE4 wrong-manager for Timesheet is already covered directly by
		test_api_timesheet.py; this only proves act_on_approval routes to
		the same workflow transition (its condition/guard, not new logic
		here)."""
		ts = self._pending_timesheet()

		frappe.set_user(MANAGER_USER)
		act_on_approval("Timesheet", ts.name, "Reject", comment="Please fix your hours")

		doc = frappe.get_doc("Timesheet", ts.name)
		self.assertEqual(doc.workflow_state, "Rejected")

		comments = frappe.get_all(
			"Comment",
			filters={"reference_doctype": "Timesheet", "reference_name": ts.name, "comment_type": "Comment"},
			pluck="content",
		)
		self.assertIn("Please fix your hours", comments)

	def test_reject_timesheet_without_comment_is_refused(self):
		ts = self._pending_timesheet()

		frappe.set_user(MANAGER_USER)
		with self.assertRaises(frappe.ValidationError):
			act_on_approval("Timesheet", ts.name, "Reject")

		doc = frappe.get_doc("Timesheet", ts.name)
		self.assertEqual(doc.workflow_state, "Pending Approval")

	def test_invalid_doctype_is_refused(self):
		frappe.set_user(MANAGER_USER)
		with self.assertRaises(frappe.ValidationError):
			act_on_approval("Employee", self.employee_name, "Approve")
