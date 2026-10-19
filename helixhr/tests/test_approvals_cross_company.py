"""Plan 2026-10-18-001 Release 1 (U1, U2): a line manager decides their direct
report's pending items whatever company either of them is in (R7), an HR user
outside their HR scope gets only the line manager's actions (R8), every
lookup of "this login's Employee" resolves the Active row (R9), and a row its
approver cannot act on says why instead of failing on the click (R10).

One test per confirmed path of "sees it, can't approve it":

1. The Timesheet manager transitions looked up the caller's Employee with no
   status filter, so a newer Left row made the condition fail.
2. `_assert_may_act_on` refused an HR Manager outside their HR scope before
   the line-manager rule was ever tried.
3. A missing role or permission let the row render while `apply_workflow`
   refused the act with a raw PermissionError.
"""

import hashlib
import json
import uuid
from unittest.mock import patch

import frappe
from frappe.model.workflow import apply_workflow
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from helixhr.api import (
	act_on_approval,
	approve_clean_items,
	create_my_attendance_request,
	create_my_request,
	get_approval_detail,
	get_my_approvals,
	save_my_week,
	send_my_attendance_request,
)
from helixhr.patches.v1_0 import active_manager_workflow_conditions
from helixhr.tests.test_api_approvals import token
from helixhr.tests.utils import (
	ensure_baseline_company,
	ensure_holiday_list_assignment,
	ensure_holiday_list_assignment_from,
	ensure_leave_allocation,
	ensure_leave_approver_role,
	ensure_test_company,
	ensure_test_email_account,
	make_assignment,
	make_test_project,
	make_test_user,
)
from helixhr.utils import active_employee_for_user, get_week_bounds

HR_USER = "xco-hr@helixhr.test"
HR_REPORT_USER = "xco-hr-report@helixhr.test"
MANAGER_USER = "xco-manager@helixhr.test"
REPORT_USER = "xco-report@helixhr.test"
STRANGER_USER = "xco-stranger@helixhr.test"
STRANGER_MANAGER_USER = "xco-b-manager@helixhr.test"

_OLD_CONDITION = (
	'frappe.db.get_value("Employee", doc.employee, "reports_to") == '
	'frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "name")'
)


class TestActiveEmployeeForUser(IntegrationTestCase):
	"""U1: one helper, three answers."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()

	def test_no_rows_is_none(self):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": "xco-nobody@helixhr.test",
				"first_name": "Nobody",
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True, ignore_if_duplicate=True)
		found = active_employee_for_user("xco-nobody@helixhr.test")
		self.assertEqual((found.state, found.name), ("none", None))

	def test_only_a_left_row_is_inactive(self):
		employee = make_test_user("xco-left@helixhr.test", self.company)
		frappe.db.set_value("Employee", employee, {"status": "Left", "relieving_date": today()})
		found = active_employee_for_user("xco-left@helixhr.test")
		self.assertEqual((found.state, found.name, found.company), ("inactive", None, None))

	def test_the_active_row_wins_over_a_newer_left_row(self):
		active = make_test_user(MANAGER_USER, self.company)
		left = _newer_left_row(MANAGER_USER, ensure_baseline_company())
		self.addCleanup(frappe.delete_doc, "Employee", left, force=True, ignore_permissions=True)
		found = active_employee_for_user(MANAGER_USER)
		self.assertEqual((found.state, found.name, found.company), ("active", active, self.company))
		self.assertNotEqual(found.name, left)


def _newer_left_row(user, company):
	"""A second, Left Employee row for `user`, created after the Active one --
	the rehired or transferred login the unfiltered lookup used to pick.
	ERPNext refuses a second row for one login on insert, so the login is set
	afterwards, the way HRMS's Employee Transfer leaves the old row."""
	doc = frappe.get_doc(
		{
			"doctype": "Employee",
			"first_name": "Former",
			"company": company,
			"date_of_birth": "1990-01-01",
			"date_of_joining": "2015-01-01",
			"relieving_date": "2019-12-31",
			"gender": frappe.db.get_value("Gender", {}, "name"),
			"status": "Left",
			"create_user_permission": 0,
		}
	).insert(ignore_permissions=True)
	frappe.db.set_value(
		"Employee", doc.name, {"user_id": user, "creation": add_days(today(), 1)}, update_modified=False
	)
	return doc.name


class _CrossCompanyCase(IntegrationTestCase):
	"""U2's fixtures: two companies and three reporting lines.

	Company A is the test company and company B the baseline second company.
	The HR Manager is anchored in A, so their HR scope is A; their direct
	report sits in B. A plain manager in A has a report in B too. Every login
	is this module's own, so a committed batch cannot leak into other suites.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_test_email_account()

	def setUp(self):
		frappe.set_user("Administrator")
		self.company_a = ensure_test_company()
		self.company_b = ensure_baseline_company()
		for company in (self.company_a, self.company_b):
			ensure_holiday_list_assignment(company)

		# Not `make_test_hr_manager_employee`: other suites assert that shared
		# HR login has no reports, and the bulk class below commits.
		self.hr_employee = make_test_user(HR_USER, self.company_a, create_user_permission=0)
		self.hr_user = HR_USER
		make_assignment(HR_USER, "HR Manager", [self.company_a])
		self.hr_report = make_test_user(HR_REPORT_USER, self.company_b, reports_to=self.hr_employee)
		self.manager = make_test_user(MANAGER_USER, self.company_a)
		self.report = make_test_user(REPORT_USER, self.company_b, reports_to=self.manager)
		self.stranger_manager = make_test_user(STRANGER_MANAGER_USER, self.company_b)
		self.stranger = make_test_user(STRANGER_USER, self.company_b, reports_to=self.stranger_manager)
		self.project = make_test_project(self.company_b, "_Test Cross Company Week")
		for user in (HR_REPORT_USER, REPORT_USER, STRANGER_USER):
			if not frappe.db.exists(
				"User Permission", {"user": user, "allow": "Project", "for_value": self.project}
			):
				frappe.get_doc(
					{
						"doctype": "User Permission",
						"user": user,
						"allow": "Project",
						"for_value": self.project,
					}
				).insert(ignore_permissions=True)

		digest = int(hashlib.md5(self.id().encode()).hexdigest(), 16)
		self.digest = digest
		self.start = add_days(today(), -(450 + 3 * (digest % 600)))

	def tearDown(self):
		frappe.set_user("Administrator")

	# --- helpers -----------------------------------------------------------

	def _pending_timesheet(self, user):
		digest = int(hashlib.md5(f"{self.id()}:{user}".encode()).hexdigest(), 16)
		monday, _ = get_week_bounds(add_days(today(), (digest % 200000) * 7))
		frappe.set_user(user)
		frappe.cache.delete(f"helixhr:rate-limit:save_my_week:{user}")
		name = save_my_week(
			str(monday), json.dumps([{"date": str(monday), "project": self.project, "hours": 4, "note": ""}])
		)
		apply_workflow({"doctype": "Timesheet", "name": name}, "Submit")
		frappe.set_user("Administrator")
		self.addCleanup(self._remove, "Timesheet", name)
		return name

	def _pending_attendance(self, user):
		ensure_holiday_list_assignment_from(self.company_b, self.start)
		frappe.set_user(user)
		created = create_my_attendance_request(
			from_date=str(self.start),
			to_date=str(self.start),
			reason="Work From Home",
			explanation="Router died",
		)
		send_my_attendance_request(created["name"], expected_modified=created["modified"])
		frappe.set_user("Administrator")
		self.addCleanup(self._remove, "Attendance Request", created["name"])
		return created["name"]

	def _remove(self, doctype, name):
		frappe.set_user("Administrator")
		if not frappe.db.exists(doctype, name):
			return
		# The bulk path commits per item, so a decided record outlives the
		# class rollback; cancel it in place rather than through the workflow.
		if frappe.db.get_value(doctype, name, "docstatus") == 1:
			frappe.db.set_value(doctype, name, "docstatus", 2, update_modified=False)
		if doctype == "Attendance Request":
			frappe.db.delete("Attendance", {"attendance_request": name})
		frappe.db.delete("DocShare", {"share_doctype": doctype, "share_name": name})
		frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)

	def _restore_employee_role(self):
		frappe.set_user("Administrator")
		frappe.get_doc("User", MANAGER_USER).add_roles("Employee")
		frappe.clear_cache(user=MANAGER_USER)

	def _act(self, doctype, name, action, comment=None):
		return act_on_approval(doctype, name, action, comment=comment, **token(doctype, name))


class TestCrossCompanyLineManager(_CrossCompanyCase):
	"""U2: the approval matrix across two companies."""

	# --- path 2: HR outside their scope is a line manager there -------------

	def test_hr_manager_approves_their_direct_report_in_another_company(self):
		week = self._pending_timesheet(HR_REPORT_USER)

		frappe.set_user(self.hr_user)
		self.assertIn(week, {row["name"] for row in get_my_approvals()["pending"]})
		self.assertEqual(
			get_approval_detail("timesheet", week)["actions"], ["Approve", "Send Back", "Send to HR"]
		)
		self._act("Timesheet", week, "Approve")
		self.assertEqual(frappe.db.get_value("Timesheet", week, "workflow_state"), "Approved")

	def test_out_of_scope_hr_manager_loses_the_week_once_it_is_with_hr(self):
		week = self._pending_timesheet(HR_REPORT_USER)

		frappe.set_user(self.hr_user)
		self._act("Timesheet", week, "Send to HR", comment="policy check")
		self.assertEqual(frappe.db.get_value("Timesheet", week, "workflow_state"), "Pending HR")
		# B's HR decides it now; their Approve/Send Back are HR-stage actions.
		self.assertEqual(get_approval_detail("timesheet", week)["actions"], [])
		with self.assertRaises(frappe.ValidationError):
			self._act("Timesheet", week, "Approve")
		self.assertEqual(frappe.db.get_value("Timesheet", week, "workflow_state"), "Pending HR")

	def test_hr_manager_is_refused_a_non_report_in_another_company(self):
		week = self._pending_timesheet(STRANGER_USER)

		frappe.set_user(self.hr_user)
		with self.assertRaises(frappe.PermissionError):
			get_approval_detail("timesheet", week)
		with self.assertRaises(frappe.PermissionError):
			self._act("Timesheet", week, "Approve")

	def test_hr_manager_decides_their_report_attendance_request_in_another_company(self):
		request = self._pending_attendance(HR_REPORT_USER)

		frappe.set_user(self.hr_user)
		self.assertEqual(
			get_approval_detail("attendance", request)["actions"],
			["Approve", "Send Back", "Reject", "Send to HR"],
		)
		self._act("Attendance Request", request, "Approve")
		self.assertEqual(frappe.db.get_value("Attendance Request", request, "docstatus"), 1)

	def test_nobody_decides_their_own_week(self):
		week = self._pending_timesheet(HR_REPORT_USER)

		frappe.set_user(HR_REPORT_USER)
		with self.assertRaises(frappe.PermissionError):
			get_approval_detail("timesheet", week)

	def _open_leave(self, user, employee):
		ensure_leave_allocation(employee, "Casual Leave", 30)
		day = str(add_days(today(), 40 + (self.digest % 300)))
		frappe.set_user(user)
		doc = frappe.get_doc(
			{
				"doctype": "Leave Application",
				"employee": employee,
				"leave_type": "Casual Leave",
				"from_date": day,
				"to_date": day,
				"description": "cross company",
				"leave_approver": frappe.db.get_value("Employee", employee, "leave_approver"),
			}
		).insert()
		frappe.set_user("Administrator")
		self.addCleanup(self._remove, "Leave Application", doc.name)
		return doc.name

	def test_hr_manager_decides_their_report_leave_in_another_company_until_it_is_with_hr(self):
		frappe.db.set_value("Employee", self.hr_report, "leave_approver", self.hr_user)
		ensure_leave_approver_role(self.hr_user)
		leave = self._open_leave(HR_REPORT_USER, self.hr_report)

		frappe.set_user(self.hr_user)
		self.assertEqual(
			get_approval_detail("leave", leave)["actions"], ["Approve", "Send Back", "Reject", "Send to HR"]
		)
		self._act("Leave Application", leave, "Send to HR")
		# With HR now, and HR here means company B's.
		with self.assertRaises(frappe.PermissionError):
			get_approval_detail("leave", leave)

	def test_hr_manager_is_refused_another_company_leave_that_is_not_theirs(self):
		frappe.db.set_value("Employee", self.stranger, "leave_approver", STRANGER_MANAGER_USER)
		leave = self._open_leave(STRANGER_USER, self.stranger)

		frappe.set_user(self.hr_user)
		with self.assertRaises(frappe.PermissionError) as refused:
			get_approval_detail("leave", leave)
		self.assertIn("isn't here", str(refused.exception))

	def test_hr_manager_is_refused_a_routed_request_in_another_company(self):
		frappe.set_user(HR_REPORT_USER)
		request = create_my_request(
			category="HR Letter", subject="Letter please", operation_key=str(uuid.uuid4())
		)["name"]
		frappe.set_user("Administrator")
		self.addCleanup(self._remove, "HR Request", request)

		frappe.set_user(self.hr_user)
		with self.assertRaises(frappe.PermissionError):
			get_approval_detail("request", request)

	def test_out_of_scope_hr_line_manager_meets_the_overwrite_gate(self):
		request = self._pending_attendance(HR_REPORT_USER)
		attendance = frappe.get_doc(
			{
				"doctype": "Attendance",
				"employee": self.hr_report,
				"attendance_date": str(self.start),
				"status": "Present",
				"company": self.company_b,
			}
		).insert(ignore_permissions=True)
		attendance.submit()
		self.addCleanup(self._remove, "Attendance", attendance.name)

		frappe.set_user(self.hr_user)
		with self.assertRaises(frappe.ValidationError):
			self._act("Attendance Request", request, "Approve")
		self.assertEqual(frappe.db.get_value("Attendance Request", request, "docstatus"), 0)

	# --- a plain manager, report in another company ------------------------

	def test_plain_manager_approves_a_report_in_another_company(self):
		week = self._pending_timesheet(REPORT_USER)

		frappe.set_user(MANAGER_USER)
		self.assertIn("Approve", get_approval_detail("timesheet", week)["actions"])
		self._act("Timesheet", week, "Approve")
		self.assertEqual(frappe.db.get_value("Timesheet", week, "workflow_state"), "Approved")

	# --- path 1: a newer Left row for the manager's login --------------------

	def test_manager_with_a_newer_left_employee_row_can_still_approve(self):
		week = self._pending_timesheet(REPORT_USER)
		left = _newer_left_row(MANAGER_USER, self.company_b)
		self.addCleanup(frappe.delete_doc, "Employee", left, force=True, ignore_permissions=True)

		frappe.set_user(MANAGER_USER)
		self.assertIn(week, {row["name"] for row in get_my_approvals()["pending"]})
		self.assertEqual(
			get_approval_detail("timesheet", week)["actions"], ["Approve", "Send Back", "Send to HR"]
		)
		self._act("Timesheet", week, "Approve")
		self.assertEqual(frappe.db.get_value("Timesheet", week, "workflow_state"), "Approved")

	def test_a_caller_with_no_active_row_never_matches_a_week_with_no_manager(self):
		"""`or ""` in the condition: None must not equal None."""
		week = self._pending_timesheet(STRANGER_USER)
		frappe.db.set_value("Employee", self.stranger, "reports_to", None)
		self.addCleanup(frappe.db.set_value, "Employee", self.stranger, "reports_to", self.stranger_manager)
		ghost = make_test_user("xco-ghost@helixhr.test", self.company_a)
		frappe.db.set_value("Employee", ghost, {"status": "Left", "relieving_date": today()})

		from frappe.model.workflow import is_transition_condition_satisfied

		doc = frappe.get_doc("Timesheet", week)
		transitions = frappe.get_all(
			"Workflow Transition",
			filters={
				"parent": "Timesheet Approval",
				"state": "Pending Approval",
				"action": "Approve",
				"allowed": "Employee",
			},
			fields=["condition"],
		)
		# The condition alone: the ghost has no read on the week, which
		# `get_transitions` would refuse before ever evaluating it.
		frappe.set_user("xco-ghost@helixhr.test")
		self.assertFalse(is_transition_condition_satisfied(transitions[0], doc))

	# --- path 3: the record is theirs, the account can't change it -----------

	def test_missing_employee_role_reads_view_only_and_refuses_in_words(self):
		week = self._pending_timesheet(REPORT_USER)
		frappe.db.delete("Has Role", {"parent": MANAGER_USER, "role": "Employee"})
		frappe.clear_cache(user=MANAGER_USER)
		self.addCleanup(self._restore_employee_role)

		frappe.set_user(MANAGER_USER)
		detail = get_approval_detail("timesheet", week)
		self.assertEqual(detail["actions"], [])
		self.assertTrue(detail["read_only"])
		self.assertIn("isn't set up", detail["read_only_reason"])
		with self.assertRaises(frappe.PermissionError) as refused:
			self._act("Timesheet", week, "Approve")
		self.assertIn("isn't set up", str(refused.exception))

	def test_a_row_without_write_access_is_marked_in_the_queue(self):
		week = self._pending_timesheet(REPORT_USER)
		# The manager reaches the week through a read-only share alone: their
		# User Permission stops at their own record, and the approver share
		# lost its write and submit.
		frappe.db.set_value(
			"User Permission",
			{"user": MANAGER_USER, "allow": "Employee", "for_value": self.manager},
			"hide_descendants",
			1,
		)
		frappe.db.set_value(
			"DocShare",
			{"share_doctype": "Timesheet", "share_name": week, "user": MANAGER_USER},
			{"write": 0, "submit": 0},
		)
		frappe.clear_cache(user=MANAGER_USER)
		self.addCleanup(
			frappe.db.set_value,
			"User Permission",
			{"user": MANAGER_USER, "allow": "Employee", "for_value": self.manager},
			"hide_descendants",
			0,
		)

		frappe.set_user(MANAGER_USER)
		row = {row["name"]: row for row in get_my_approvals()["pending"]}[week]
		self.assertFalse(row["can_act"])
		self.assertIn("can't change", row["blocked_reason"])
		self.assertEqual(get_approval_detail("timesheet", week)["actions"], [])
		with self.assertRaises(frappe.PermissionError):
			self._act("Timesheet", week, "Approve")
		with patch("helixhr.api._item_flags", return_value={}):
			result = approve_clean_items([{"doctype": "Timesheet", "name": week, **token("Timesheet", week)}])
		self.assertFalse(result[0]["ok"], result)

	def test_past_the_manager_step_the_week_is_plainly_not_theirs_to_decide(self):
		week = self._pending_timesheet(REPORT_USER)
		frappe.set_user(MANAGER_USER)
		self._act("Timesheet", week, "Send to HR", comment="policy check")

		detail = get_approval_detail("timesheet", week)
		self.assertEqual(detail["actions"], [])
		self.assertNotIn("read_only_reason", detail)


class TestCrossCompanyBulkApproval(_CrossCompanyCase):
	"""The batch path on its own class: `approve_clean_items` commits per item,
	which would make every earlier test's fixture writes permanent."""

	def test_plain_manager_bulk_approves_a_report_in_another_company(self):
		# The batch commits, so the class rollback cannot undo this week.
		# Registered first so it runs last (cleanups are LIFO): it keeps the
		# delete `_pending_timesheet`'s cleanup makes.
		self.addCleanup(frappe.db.commit)
		week = self._pending_timesheet(REPORT_USER)

		frappe.set_user(MANAGER_USER)
		# Authorization is what this proves; the R16 flags have their own suite.
		with patch("helixhr.api._item_flags", return_value={}):
			result = approve_clean_items([{"doctype": "Timesheet", "name": week, **token("Timesheet", week)}])
		self.assertTrue(result[0]["ok"], result)
		self.assertEqual(frappe.db.get_value("Timesheet", week, "workflow_state"), "Approved")


def _next(action):
	return {"Approve": "Approved", "Send Back": "Sent Back", "Send to HR": "Pending HR"}[action]


class TestActiveManagerConditionPatch(IntegrationTestCase):
	"""KTD7: the backstop rewrites a stale condition, and a rerun is a no-op."""

	def _manager_rows(self):
		return frappe.get_all(
			"Workflow Transition",
			filters={
				"parent": "Timesheet Approval",
				"state": "Pending Approval",
				"allowed": "Employee",
				"action": ["in", ["Approve", "Send Back", "Send to HR"]],
			},
			fields=["name", "action", "condition", "modified"],
		)

	def test_stale_condition_is_rewritten_once(self):
		rows = [row for row in self._manager_rows() if row.condition]
		self.assertEqual(len(rows), 3)
		for row in rows:
			frappe.db.set_value("Workflow Transition", row.name, "condition", _OLD_CONDITION)

		active_manager_workflow_conditions.execute()
		after = {row.name: row for row in self._manager_rows()}
		for row in rows:
			self.assertIn('"status": "Active"', after[row.name].condition)

		active_manager_workflow_conditions.execute()
		again = {row.name: row for row in self._manager_rows()}
		self.assertEqual(
			{name: (row.condition, row.modified) for name, row in after.items()},
			{name: (row.condition, row.modified) for name, row in again.items()},
		)

	def test_fixture_and_patch_agree(self):
		active_manager_workflow_conditions.execute()
		wanted = active_manager_workflow_conditions._fixture_conditions()
		for row in self._manager_rows():
			self.assertEqual(
				row.condition, wanted[("Pending Approval", row.action, _next(row.action), "Employee")]
			)
