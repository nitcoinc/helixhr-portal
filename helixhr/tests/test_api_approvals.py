import hashlib
import json

import frappe
from frappe.model.workflow import apply_workflow
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from helixhr.api import (
	_allowed_actions,
	act_on_approval,
	create_my_attendance_request,
	get_approval_detail,
	get_my_approvals,
	get_portal_bootstrap,
	save_my_week,
	send_my_attendance_request,
)
from helixhr.events import (
	DECISION_REASON_FIELD,
	REQUEST_PENDING_HR,
	REQUEST_PENDING_MANAGER,
	REQUEST_REJECTED,
)
from helixhr.tests.utils import (
	EMPLOYEE_USER,
	MANAGER_USER,
	OTHER_MANAGER_USER,
	ensure_holiday_list_assignment,
	ensure_holiday_list_assignment_from,
	ensure_hr_manager_user,
	ensure_leave_allocation,
	ensure_leave_approver_role,
	ensure_test_email_account,
	make_test_employee_and_manager,
	make_test_hr_manager_employee,
	make_test_user,
)
from helixhr.utils import get_week_bounds

DOCTYPE = "Attendance Request"


def token(doctype, name):
	"""The concurrency token the screen always sends (P2-U7 step 3):
	`get_approval_detail` hands the manager a `modified` and a state, and
	`act_on_approval` refuses a decision that does not carry them back."""
	field = "status" if doctype == "Leave Application" else "workflow_state"
	row = frappe.db.get_value(doctype, name, ["modified", field], as_dict=True)
	return {"expected_modified": str(row.modified), "expected_state": row.get(field)}


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

	def test_send_back_without_comment_is_refused(self):
		leave = self._pending_leave()

		frappe.set_user(MANAGER_USER)
		with self.assertRaises(frappe.ValidationError):
			act_on_approval("Leave Application", leave.name, "Send Back")

	def test_reject_without_comment_is_refused_too(self):
		"""P4-R4: the final no needs a reason for the same reason the
		recoverable one does -- and more, since the employee cannot resend."""
		leave = self._pending_leave()

		frappe.set_user(MANAGER_USER)
		with self.assertRaises(frappe.ValidationError):
			act_on_approval("Leave Application", leave.name, "Reject")

	def test_approve_leave_sets_status_and_employee_sees_it(self):
		"""Approval submits the native document (P2-U1), which walks HRMS's
		own holiday-list lookup for the leave date -- the one call in this
		class that reaches it, since every other test here is refused
		before submit. Needs its own assignment; nothing upstream in this
		class's setUp provides one (unlike TestLeaveApprovalIsNative)."""
		ensure_holiday_list_assignment(frappe.db.get_value("Employee", self.employee_name, "company"))
		leave = self._pending_leave()

		frappe.set_user(MANAGER_USER)
		act_on_approval("Leave Application", leave.name, "Approve", **token("Leave Application", leave.name))

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
			act_on_approval(
				"Leave Application", leave.name, "Approve", **token("Leave Application", leave.name)
			)

		self.assertEqual(frappe.db.get_value("Leave Application", leave.name, "status"), "Open")

	def test_send_back_timesheet_with_comment_via_act_on_approval(self):
		"""AE4 wrong-manager for Timesheet is already covered directly by
		test_api_timesheet.py; this only proves act_on_approval routes to
		the same workflow transition (its condition/guard, not new logic
		here)."""
		ts = self._pending_timesheet()

		frappe.set_user(MANAGER_USER)
		act_on_approval(
			"Timesheet",
			ts.name,
			"Send Back",
			comment="Please fix your hours",
			**token("Timesheet", ts.name),
		)

		doc = frappe.get_doc("Timesheet", ts.name)
		self.assertEqual(doc.workflow_state, "Sent Back")

		comments = frappe.get_all(
			"Comment",
			filters={"reference_doctype": "Timesheet", "reference_name": ts.name, "comment_type": "Comment"},
			pluck="content",
		)
		self.assertIn("Please fix your hours", comments)

	def test_send_back_timesheet_without_comment_is_refused(self):
		ts = self._pending_timesheet()

		frappe.set_user(MANAGER_USER)
		with self.assertRaises(frappe.ValidationError):
			act_on_approval("Timesheet", ts.name, "Send Back")

		doc = frappe.get_doc("Timesheet", ts.name)
		self.assertEqual(doc.workflow_state, "Pending Approval")

	def test_a_timesheet_is_never_finally_rejected(self):
		"""P4-KTD2: a week is one row and the hours still have to be
		recorded, so the Timesheet workflow carries no Reject transition and
		the derived action list therefore never offers one."""
		ts = self._pending_timesheet()

		frappe.set_user(MANAGER_USER)
		self.assertEqual(
			get_approval_detail("timesheet", ts.name)["actions"],
			["Approve", "Send Back", "Send to HR"],
		)
		with self.assertRaises(frappe.ValidationError):
			act_on_approval(
				"Timesheet", ts.name, "Reject", comment="no", **token("Timesheet", ts.name)
			)
		self.assertEqual(
			frappe.db.get_value("Timesheet", ts.name, "workflow_state"), "Pending Approval"
		)

	def test_invalid_doctype_is_refused(self):
		frappe.set_user(MANAGER_USER)
		with self.assertRaises(frappe.ValidationError):
			act_on_approval("Employee", self.employee_name, "Approve")


class TestApprovalQueueAndEvidence(IntegrationTestCase):
	"""P2-U7. The queue a manager decides from, the evidence they read
	before deciding, and the share that carries the access."""

	SECOND_MANAGER = "second-manager@helixhr.test"
	OUTSIDER = "outsider-manager@helixhr.test"

	def setUp(self):
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()
		frappe.db.set_value("Employee", self.employee_name, "reports_to", self.manager_name)
		frappe.db.set_value("Employee", self.employee_name, "leave_approver", MANAGER_USER)
		ensure_leave_approver_role(MANAGER_USER)
		self.company = frappe.db.get_value("Employee", self.employee_name, "company")
		ensure_holiday_list_assignment(self.company)

		digest = int(hashlib.md5(self.id().encode()).hexdigest(), 16)
		self.monday, _ = get_week_bounds(add_days(today(), (digest % 200000) * 7))
		self.leave_date = add_days(today(), digest % 100)
		frappe.cache.delete(f"helixhr:rate-limit:save_my_week:{EMPLOYEE_USER}")

	def tearDown(self):
		frappe.set_user("Administrator")
		# Every method leaves the reporting line as it found it -- the
		# reassignment test moves it on purpose.
		frappe.db.set_value("Employee", self.employee_name, "reports_to", self.manager_name)

	# helpers

	def _other(self, user):
		from helixhr.tests.utils import make_test_user

		frappe.set_user("Administrator")
		return make_test_user(user, self.company)

	def _project(self):
		from helixhr.tests.utils import TEST_COMPANY, ensure_test_company

		project_name = f"_Test Evidence Project {self.id().split('.')[-1]}"
		existing = frappe.db.get_value("Project", {"project_name": project_name}, "name")
		if not existing:
			ensure_test_company()
			existing = frappe.get_doc(
				{
					"doctype": "Project",
					"project_name": project_name,
					"status": "Open",
					"company": TEST_COMPANY,
				}
			).insert(ignore_permissions=True).name
		if not frappe.db.exists(
			"User Permission", {"user": EMPLOYEE_USER, "allow": "Project", "for_value": existing}
		):
			frappe.get_doc(
				{
					"doctype": "User Permission",
					"user": EMPLOYEE_USER,
					"allow": "Project",
					"for_value": existing,
				}
			).insert(ignore_permissions=True)
		return existing

	def _pending_timesheet(self, rows=None):
		project = self._project()
		frappe.set_user(EMPLOYEE_USER)
		name = save_my_week(
			str(self.monday),
			json.dumps(
				rows
				or [
					{"date": str(self.monday), "project": project, "hours": 8, "note": "long Monday"},
					{"date": str(add_days(self.monday, 1)), "project": project, "hours": 6.5, "note": ""},
				]
			),
		)
		apply_workflow({"doctype": "Timesheet", "name": name}, "Submit")
		frappe.set_user("Administrator")
		return name

	def _pending_leave(self, approver=MANAGER_USER):
		ensure_leave_allocation(self.employee_name, "Casual Leave", 5)
		frappe.set_user(EMPLOYEE_USER)
		doc = frappe.get_doc(
			{
				"doctype": "Leave Application",
				"employee": self.employee_name,
				"leave_type": "Casual Leave",
				"from_date": str(self.leave_date),
				"to_date": str(self.leave_date),
				"description": "Nephew's wedding",
				"leave_approver": approver,
			}
		).insert()
		frappe.set_user("Administrator")
		return doc

	def _shared_users(self, timesheet):
		return frappe.get_all(
			"DocShare",
			filters={"share_doctype": "Timesheet", "share_name": timesheet},
			pluck="user",
		)

	# P2-U7 scenario 1 / P2-AE6

	def test_timesheet_evidence_is_complete_and_an_unrelated_manager_gets_none_of_it(self):
		name = self._pending_timesheet()

		frappe.set_user(MANAGER_USER)
		detail = get_approval_detail("timesheet", name)
		self.assertEqual(detail["kind"], "timesheet")
		self.assertEqual(detail["week_start"], str(self.monday))
		self.assertEqual(len(detail["day_totals"]), 7)
		self.assertEqual(detail["day_totals"][0]["hours"], 8)
		self.assertEqual(detail["day_totals"][1]["hours"], 6.5)
		self.assertEqual(detail["total_hours"], 14.5)
		self.assertEqual(sum(line["total"] for line in detail["lines"]), 14.5)
		self.assertIn("long Monday", detail["note"])
		# The token the decision has to carry back.
		self.assertTrue(detail["modified"])
		self.assertEqual(detail["state"], "Pending Approval")

		self._other(self.OUTSIDER)
		frappe.set_user(self.OUTSIDER)
		queued = [row["name"] for row in get_my_approvals()["pending"]]
		self.assertNotIn(name, queued)
		with self.assertRaises(frappe.PermissionError):
			get_approval_detail("timesheet", name)

	# P2-U7 scenario 2

	def test_leave_evidence_carries_the_reason_dates_days_and_status(self):
		leave = self._pending_leave()

		frappe.set_user(MANAGER_USER)
		detail = get_approval_detail("leave", leave.name)
		self.assertEqual(detail["reason"], "Nephew's wedding")
		self.assertEqual(detail["from_date"], str(self.leave_date))
		self.assertEqual(detail["to_date"], str(self.leave_date))
		self.assertEqual(detail["total_days"], 1)
		self.assertEqual(detail["status"], "Open")
		self.assertEqual(detail["state"], "Open")
		self.assertEqual(detail["employee_name"], frappe.db.get_value("Employee", self.employee_name, "employee_name"))

	def test_the_queue_mixes_both_kinds_oldest_first_and_excludes_the_managers_own_week(self):
		leave = self._pending_leave()
		timesheet = self._pending_timesheet()

		frappe.set_user(MANAGER_USER)
		result = get_my_approvals()
		by_name = {row["name"]: row for row in result["pending"]}
		self.assertIn(leave.name, by_name)
		self.assertIn(timesheet, by_name)
		self.assertEqual(by_name[leave.name]["kind"], "leave")
		self.assertEqual(by_name[timesheet]["kind"], "timesheet")
		self.assertTrue(by_name[timesheet]["initials"])
		self.assertIsNotNone(by_name[timesheet]["age_days"])

		sent = [row["sent_on"] for row in result["pending"] if row["sent_on"]]
		self.assertEqual(sent, sorted(sent), "the queue is oldest first")

		self.assertNotIn(
			self.manager_name,
			[row["employee"] for row in result["pending"]],
			"a manager never decides their own record",
		)

	# P2-U7 scenario 6

	def test_a_leave_approver_with_no_reports_sees_only_the_leave_assigned_to_them(self):
		from helixhr.api import get_portal_bootstrap

		approver_employee = self._other("lone-approver@helixhr.test")
		self.assertEqual(
			frappe.db.count("Employee", {"reports_to": approver_employee, "status": "Active"}), 0
		)
		frappe.db.set_value("Employee", self.employee_name, "leave_approver", "lone-approver@helixhr.test")
		ensure_leave_approver_role("lone-approver@helixhr.test")
		try:
			# Assigned at insert, not patched afterwards: HRMS's own
			# `share_doc_with_approver` runs on save and is what gives an
			# approver outside the reporting line any sight of the record
			# at all (P2-U1 step 2).
			leave = self._pending_leave(approver="lone-approver@helixhr.test")
			timesheet = self._pending_timesheet()

			frappe.set_user("lone-approver@helixhr.test")
			self.assertTrue(get_portal_bootstrap()["can_approve"])
			pending = get_my_approvals()["pending"]
			self.assertIn(leave.name, [row["name"] for row in pending])
			self.assertNotIn(
				timesheet,
				[row["name"] for row in pending],
				"a leave approver is not the timesheet approver",
			)
		finally:
			frappe.set_user("Administrator")
			frappe.db.set_value("Employee", self.employee_name, "leave_approver", MANAGER_USER)

	# P2-U7 scenario 3 and 5

	def test_a_decision_with_no_token_is_refused_before_anything_happens(self):
		leave = self._pending_leave()

		frappe.set_user(MANAGER_USER)
		with self.assertRaises(frappe.ValidationError):
			act_on_approval("Leave Application", leave.name, "Approve")

		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Leave Application", leave.name, "status"), "Open")

	def test_a_timesheet_decided_twice_transitions_once(self):
		name = self._pending_timesheet()

		frappe.set_user(MANAGER_USER)
		current = token("Timesheet", name)
		act_on_approval("Timesheet", name, "Approve", **current)
		# The double tap: the same evidence, sent twice.
		with self.assertRaises(frappe.ValidationError):
			act_on_approval("Timesheet", name, "Approve", **current)
		with self.assertRaises(frappe.ValidationError):
			act_on_approval("Timesheet", name, "Send Back", comment="on second thoughts", **current)

		frappe.set_user("Administrator")
		doc = frappe.get_doc("Timesheet", name)
		self.assertEqual(doc.workflow_state, "Approved")
		self.assertEqual(doc.docstatus, 1)
		self.assertEqual(
			frappe.get_all(
				"Comment",
				filters={
					"reference_doctype": "Timesheet",
					"reference_name": name,
					"comment_type": "Comment",
				},
				pluck="content",
			),
			[],
			"the loser of a concurrent decision leaves no comment",
		)

	def test_a_state_that_moved_under_the_manager_is_refused(self):
		name = self._pending_timesheet()

		frappe.set_user(MANAGER_USER)
		with self.assertRaises(frappe.ValidationError):
			act_on_approval(
				"Timesheet",
				name,
				"Approve",
				expected_modified=frappe.db.get_value("Timesheet", name, "modified"),
				expected_state="Draft",
			)

		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Timesheet", name, "workflow_state"), "Pending Approval")

	# P2-U7 scenario 8

	def test_the_share_exists_only_while_the_week_is_pending(self):
		name = self._pending_timesheet()
		self.assertIn(MANAGER_USER, self._shared_users(name))

		frappe.set_user(MANAGER_USER)
		act_on_approval("Timesheet", name, "Approve", **token("Timesheet", name))

		frappe.set_user("Administrator")
		self.assertEqual(self._shared_users(name), [])

	def test_cancelling_an_approved_week_leaves_no_share_behind(self):
		name = self._pending_timesheet()
		frappe.set_user(MANAGER_USER)
		act_on_approval("Timesheet", name, "Approve", **token("Timesheet", name))

		frappe.set_user("Administrator")
		doc = frappe.get_doc("Timesheet", name)
		doc.cancel()
		self.assertEqual(self._shared_users(name), [])

	# P2-U7 scenario 7

	def test_reassigning_the_manager_moves_every_pending_share(self):
		second_manager_employee = self._other(self.SECOND_MANAGER)
		name = self._pending_timesheet()
		self.assertEqual(self._shared_users(name), [MANAGER_USER])

		frappe.set_user("Administrator")
		employee = frappe.get_doc("Employee", self.employee_name)
		employee.reports_to = second_manager_employee
		employee.save()

		shared = self._shared_users(name)
		self.assertEqual(shared, [self.SECOND_MANAGER], "only the current manager holds the week")

		# The old manager loses the decision as well as the read.
		frappe.set_user(MANAGER_USER)
		self.assertNotIn(name, [row["name"] for row in get_my_approvals()["pending"]])
		with self.assertRaises(frappe.PermissionError):
			get_approval_detail("timesheet", name)
		with self.assertRaises(frappe.PermissionError):
			act_on_approval(
				"Timesheet",
				name,
				"Approve",
				expected_modified=frappe.db.get_value("Timesheet", name, "modified"),
				expected_state="Pending Approval",
			)

		# The new one gets both.
		frappe.set_user(self.SECOND_MANAGER)
		self.assertIn(name, [row["name"] for row in get_my_approvals()["pending"]])
		self.assertEqual(get_approval_detail("timesheet", name)["name"], name)

	def test_changing_the_managers_own_login_moves_every_pending_share(self):
		"""The same defect from the other side. A rename, an Entra migration
		or a duplicate-account cleanup changes the *manager's* `user_id`;
		`_approver_user` then answers the new address and the portal refuses
		the old one, while the DocShare granting write+submit on every
		pending week still pointed at the old account -- reachable through
		`frappe.client.set_value`, `/api/resource` and `apply_workflow`."""
		name = self._pending_timesheet()
		self.assertEqual(self._shared_users(name), [MANAGER_USER])

		frappe.set_user("Administrator")
		renamed = "manager-renamed@helixhr.test"
		if not frappe.db.exists("User", renamed):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": renamed,
					"first_name": "Renamed",
					"last_name": "Manager",
					"send_welcome_email": 0,
					"roles": [{"doctype": "Has Role", "role": "Employee"}],
				}
			).insert(ignore_permissions=True)
		self.addCleanup(self._restore_manager_record)

		manager = frappe.get_doc("Employee", self.manager_name)
		manager.user_id = renamed
		manager.save()

		self.assertEqual(self._shared_users(name), [renamed])
		frappe.set_user(MANAGER_USER)
		with self.assertRaises(frappe.PermissionError):
			get_approval_detail("timesheet", name)

		# And a manager who leaves keeps nothing at all.
		frappe.set_user("Administrator")
		manager.reload()
		manager.status = "Inactive"
		manager.save()
		self.assertEqual(self._shared_users(name), [])

	def _restore_manager_record(self):
		frappe.set_user("Administrator")
		manager = frappe.get_doc("Employee", self.manager_name)
		manager.status = "Active"
		manager.user_id = MANAGER_USER
		manager.save(ignore_permissions=True)


class TestLeaveApprovalIsNative(IntegrationTestCase):
	"""P2-U1 / P2-R10 / P2-AE1: approving leave through the portal must run
	the native HRMS submit lifecycle, not just stamp a status field, and
	nothing at all may happen before the caller is authorized."""

	def setUp(self):
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()
		frappe.db.set_value("Employee", self.employee_name, "reports_to", self.manager_name)
		frappe.db.set_value("Employee", self.employee_name, "leave_approver", MANAGER_USER)
		ensure_leave_approver_role(MANAGER_USER)
		ensure_holiday_list_assignment(frappe.db.get_value("Employee", self.employee_name, "company"))
		# One day per test method, and the method clears that day first.
		# Leave state is not rolled back between test methods on a real
		# bench (see docs/runbook.md), and HRMS refuses two applications
		# that overlap -- so a shared or reused day makes this class fail
		# on its second run rather than on a real defect. Consecutive
		# even offsets from day 96 keep every date inside the current
		# allocation period (the allocation ends with the calendar year, and
		# each method added here costs two more days), clear of the other fixtures in this repo
		# (which sit inside the first 98 days, on odd offsets), and never
		# consecutive -- Casual Leave caps continuous days, and HRMS reads
		# two applications on adjacent days as one continuous leave.
		methods = sorted(name for name in dir(self) if name.startswith("test_"))
		self.leave_date = add_days(today(), 96 + 2 * methods.index(self.id().split(".")[-1]))
		self._clear_leave_on(self.leave_date)

	def tearDown(self):
		frappe.set_user("Administrator")

	# helpers

	def _clear_leave_on(self, date):
		for name in frappe.get_all(
			"Leave Application",
			filters={
				"employee": self.employee_name,
				"from_date": ["<=", str(date)],
				"to_date": [">=", str(date)],
			},
			pluck="name",
		):
			doc = frappe.get_doc("Leave Application", name)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Leave Application", name, force=True, ignore_permissions=True)

	def _pending_leave(self):
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
		frappe.set_user("Administrator")
		return doc

	def _balance(self):
		from hrms.hr.doctype.leave_application.leave_application import get_leave_balance_on

		return get_leave_balance_on(self.employee_name, "Casual Leave", str(self.leave_date))

	def _ledger(self, name):
		return frappe.get_all(
			"Leave Ledger Entry",
			filters={"transaction_name": name, "docstatus": 1},
			fields=["leaves"],
		)

	def _comments(self, name):
		return frappe.get_all(
			"Comment",
			filters={
				"reference_doctype": "Leave Application",
				"reference_name": name,
				"comment_type": "Comment",
			},
			pluck="content",
		)

	def _notifications(self, name):
		return frappe.get_all(
			"Notification Log",
			filters={"document_type": "Leave Application", "document_name": name},
			pluck="subject",
		)

	# P2-AE1

	def test_approval_submits_writes_the_ledger_and_consumes_balance(self):
		leave = self._pending_leave()
		before = self._balance()

		frappe.set_user(MANAGER_USER)
		result = act_on_approval(
			"Leave Application", leave.name, "Approve", **token("Leave Application", leave.name)
		)
		self.assertEqual(result["state"], "Approved")

		frappe.set_user("Administrator")
		doc = frappe.get_doc("Leave Application", leave.name)
		self.assertEqual(doc.status, "Approved")
		self.assertEqual(doc.docstatus, 1)

		ledger = self._ledger(leave.name)
		self.assertEqual(len(ledger), 1)
		self.assertEqual(ledger[0].leaves, -1.0)
		self.assertEqual(self._balance(), before - 1)

		self.assertTrue(
			[subject for subject in self._notifications(leave.name) if "Approved" in subject],
			"the HelixHR Leave Status Changed notification should carry the new status",
		)

	def test_rejection_keeps_the_reason_stays_unsubmitted_and_consumes_nothing(self):
		leave = self._pending_leave()
		before = self._balance()

		frappe.set_user(MANAGER_USER)
		act_on_approval(
			"Leave Application",
			leave.name,
			"Reject",
			comment="Two people are already out",
			**token("Leave Application", leave.name),
		)

		frappe.set_user("Administrator")
		doc = frappe.get_doc("Leave Application", leave.name)
		self.assertEqual(doc.status, "Rejected")
		self.assertEqual(doc.docstatus, 0)

		reasons = [frappe.utils.strip_html(c).strip() for c in self._comments(leave.name)]
		self.assertIn("Two people are already out", reasons)

		self.assertEqual(self._ledger(leave.name), [])
		self.assertEqual(self._balance(), before)

	def test_nobody_but_the_approver_can_act_or_even_leave_a_comment(self):
		leave = self._pending_leave()

		unrelated = "unrelated-manager@helixhr.test"
		if not frappe.db.exists("User", unrelated):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": unrelated,
					"first_name": "Unrelated",
					"send_welcome_email": 0,
					"roles": [{"doctype": "Has Role", "role": "Employee"}],
				}
			).insert(ignore_permissions=True)
		ensure_leave_approver_role(unrelated)

		current = token("Leave Application", leave.name)
		for user in (unrelated, EMPLOYEE_USER):
			frappe.set_user(user)
			with self.assertRaises(frappe.PermissionError):
				act_on_approval("Leave Application", leave.name, "Approve", **current)
			with self.assertRaises(frappe.PermissionError):
				act_on_approval("Leave Application", leave.name, "Reject", comment="mine now", **current)

		frappe.set_user("Administrator")
		doc = frappe.get_doc("Leave Application", leave.name)
		self.assertEqual(doc.status, "Open")
		self.assertEqual(doc.docstatus, 0)
		# The P2-U1 defect: add_comment used to run before the approver check.
		self.assertEqual(self._comments(leave.name), [])
		self.assertEqual(self._ledger(leave.name), [])

	def test_the_employee_cannot_submit_their_own_leave_on_the_generic_route(self):
		leave = self._pending_leave()

		frappe.set_user(EMPLOYEE_USER)
		self.assertFalse(frappe.has_permission("Leave Application", "submit", doc=leave.name))
		doc = frappe.get_doc("Leave Application", leave.name)
		doc.status = "Approved"
		with self.assertRaises(frappe.PermissionError):
			doc.submit()

		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Leave Application", leave.name, "docstatus"), 0)
		self.assertEqual(self._ledger(leave.name), [])

	def test_a_second_decision_is_refused_with_no_second_effect(self):
		"""Concurrent approve/approve and approve/reject: the row is locked
		for update and the state check runs before any side effect, so the
		loser changes nothing and leaves no contradicting comment."""
		leave = self._pending_leave()

		frappe.set_user(MANAGER_USER)
		stale = token("Leave Application", leave.name)
		act_on_approval("Leave Application", leave.name, "Approve", **stale)

		# The loser of a concurrent approve/approve and of an
		# approve/reject: same record, same token, already decided.
		with self.assertRaises(frappe.ValidationError):
			act_on_approval("Leave Application", leave.name, "Approve", **stale)
		with self.assertRaises(frappe.ValidationError):
			act_on_approval(
				"Leave Application", leave.name, "Reject", comment="changed my mind", **stale
			)
		# And with a *fresh* token, which is the reassure-yourself-and-retry
		# case: still refused, because the record is no longer open.
		with self.assertRaises(frappe.ValidationError):
			act_on_approval(
				"Leave Application", leave.name, "Reject", comment="changed my mind",
				**token("Leave Application", leave.name),
			)

		frappe.set_user("Administrator")
		self.assertEqual(len(self._ledger(leave.name)), 1)
		self.assertEqual(self._comments(leave.name), [])
		self.assertEqual(frappe.db.get_value("Leave Application", leave.name, "status"), "Approved")

	def test_a_stale_expected_modified_is_refused(self):
		leave = self._pending_leave()

		frappe.set_user(MANAGER_USER)
		with self.assertRaises(frappe.ValidationError):
			act_on_approval(
				"Leave Application",
				leave.name,
				"Reject",
				comment="stale",
				expected_modified="2000-01-01 00:00:00",
			)

		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Leave Application", leave.name, "status"), "Open")
		self.assertEqual(self._comments(leave.name), [])

		# The current token still works.
		frappe.set_user(MANAGER_USER)
		act_on_approval("Leave Application", leave.name, "Approve", **token("Leave Application", leave.name))
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Leave Application", leave.name, "docstatus"), 1)

	def test_the_approvers_submit_grant_is_native(self):
		"""P2-U1 step 2: the portal calls doc.submit() with no
		ignore_permissions, so the grant has to already exist. Employee is a
		nested set, so a manager's own User Permission covers their reports;
		the Leave Approver role HRMS auto-grants carries submit at permlevel
		0. An approver outside the reporting line instead gets the submit=1
		DocShare hrms.hr.utils.share_doc_with_approver creates on save."""
		leave = self._pending_leave()

		frappe.set_user(MANAGER_USER)
		self.assertTrue(frappe.has_permission("Leave Application", "submit", doc=leave.name))

		frappe.set_user("Administrator")
		roles = frappe.get_roles(MANAGER_USER)
		shared = frappe.get_all(
			"DocShare",
			filters={
				"share_doctype": "Leave Application",
				"share_name": leave.name,
				"user": MANAGER_USER,
				"submit": 1,
			},
			pluck="name",
		)
		self.assertTrue(
			"Leave Approver" in roles or shared,
			"neither the native Leave Approver role nor an HRMS DocShare grants submit",
		)

	def test_hr_manager_can_approve_a_leave_they_are_not_the_approver_of(self):
		"""The error copy promises "or HR", and `_assert_may_act_on` lets HR
		through -- so the native submit has to accept them too. HRMS's
		`validate_leave_approver` only checks that the field is *set* (it
		never compares it to the session user) and `validate_for_self_approval`
		only blocks the employee themselves, so the HR Manager role's own
		submit permission carries it. Checked here rather than assumed: if
		HRMS starts requiring the named approver, this fails instead of
		production."""
		leave = self._pending_leave()
		self.assertEqual(leave.leave_approver, MANAGER_USER)

		frappe.set_user("Administrator")
		hr_user = "hr-manager-leave@helixhr.test"
		if not frappe.db.exists("User", hr_user):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": hr_user,
					"first_name": "HR",
					"last_name": "Approver",
					"send_welcome_email": 0,
					"roles": [{"doctype": "Has Role", "role": "HR Manager"}],
				}
			).insert(ignore_permissions=True)

		frappe.set_user(hr_user)
		result = act_on_approval(
			"Leave Application", leave.name, "Approve", **token("Leave Application", leave.name)
		)

		self.assertEqual(result["state"], "Approved")
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Leave Application", leave.name)
		self.assertEqual(doc.docstatus, 1)
		self.assertEqual(doc.leave_approver, MANAGER_USER, "the named approver is not rewritten")
		self.assertTrue(self._ledger(leave.name), "an HR approval consumes balance like any other")

	def test_leave_with_no_approver_is_refused_by_hr_settings(self):
		"""P2-U1 step 3: the refusal is HR Settings, not portal copy."""
		original = frappe.db.get_single_value(
			"HR Settings", "leave_approver_mandatory_in_leave_application"
		)
		frappe.db.set_single_value("HR Settings", "leave_approver_mandatory_in_leave_application", 1)
		try:
			ensure_leave_allocation(self.employee_name, "Casual Leave", 5)
			frappe.set_user(EMPLOYEE_USER)
			with self.assertRaises(frappe.ValidationError):
				frappe.get_doc(
					{
						"doctype": "Leave Application",
						"employee": self.employee_name,
						"leave_type": "Casual Leave",
						"from_date": str(self.leave_date),
						"to_date": str(self.leave_date),
						"description": "no approver",
					}
				).insert()
		finally:
			frappe.set_user("Administrator")
			frappe.db.set_single_value(
				"HR Settings", "leave_approver_mandatory_in_leave_application", original
			)

	def test_self_approval_is_refused_by_hr_settings(self):
		"""P4-R8 now refuses this one step earlier -- `_assert_may_act_on`
		checks "not your own request" before anything else and does not
		consult HR Settings at all. The setting is still what closes the
		Desk route, so the switch is left flipped here on purpose."""
		original = frappe.db.get_single_value("HR Settings", "prevent_self_leave_approval")
		frappe.db.set_single_value("HR Settings", "prevent_self_leave_approval", 1)
		try:
			ensure_leave_allocation(self.employee_name, "Casual Leave", 5)
			frappe.db.set_value("Employee", self.employee_name, "leave_approver", EMPLOYEE_USER)
			ensure_leave_approver_role(EMPLOYEE_USER)
			frappe.set_user(EMPLOYEE_USER)
			leave = frappe.get_doc(
				{
					"doctype": "Leave Application",
					"employee": self.employee_name,
					"leave_type": "Casual Leave",
					"from_date": str(self.leave_date),
					"to_date": str(self.leave_date),
					"description": "self approval",
					"leave_approver": EMPLOYEE_USER,
				}
			)
			leave.insert()
			with self.assertRaises(frappe.ValidationError):
				act_on_approval(
					"Leave Application", leave.name, "Approve", **token("Leave Application", leave.name)
				)
			frappe.set_user("Administrator")
			self.assertEqual(frappe.db.get_value("Leave Application", leave.name, "docstatus"), 0)
		finally:
			frappe.set_user("Administrator")
			frappe.db.set_value("Employee", self.employee_name, "leave_approver", MANAGER_USER)
			frappe.db.set_single_value("HR Settings", "prevent_self_leave_approval", original)
			# Hand the role back. Leave Approver carries submit on Leave
			# Application at permlevel 0, so leaving it on the employee
			# would quietly grant them the very permission the next test
			# asserts they do not have.
			user_doc = frappe.get_doc("User", EMPLOYEE_USER)
			user_doc.set("roles", [row for row in user_doc.roles if row.role != "Leave Approver"])
			user_doc.save(ignore_permissions=True)

	def test_an_unsubmitted_approved_row_is_not_treated_as_leave(self):
		"""P2-R10 / P2-U1 step 4 and 10: the legacy defect state. It consumes
		nothing, preflight counts it, and the server never reports it as a
		day off."""
		from helixhr.api import _leave_days
		from helixhr.preflight import WARN, check_unsubmitted_approved_leave

		leave = self._pending_leave()
		# Exactly what the pre-P2-U1 approval path left behind.
		frappe.db.set_value("Leave Application", leave.name, "status", "Approved", update_modified=False)

		self.assertEqual(self._ledger(leave.name), [])
		self.assertNotIn(
			str(self.leave_date), _leave_days(self.employee_name, self.leave_date, self.leave_date)
		)

		result = check_unsubmitted_approved_leave()
		self.assertEqual(result["status"], WARN)
		self.assertIn("never submitted", result["detail"])


class TestAttendanceRequestApprovals(IntegrationTestCase):
	"""P3-U6. The third kind in the manager's queue: what it lists, what the
	manager reads before deciding, and every refusal (P3-R16, P3-KTD7,
	P3-AE7, P3-AE8).

	Each method picks its own past-year window, hashed from the test id and
	never nearer than 450 days back, and removes what it wrote in tearDown:
	IntegrationTestCase does not roll back between methods here
	(docs/runbook.md) and HRMS refuses two overlapping requests for one
	employee.
	"""

	def setUp(self):
		frappe.set_user("Administrator")
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()
		frappe.db.set_value("Employee", self.employee_name, "reports_to", self.manager_name)
		self.company = frappe.db.get_value("Employee", self.employee_name, "company")
		ensure_holiday_list_assignment(self.company)
		self.hr_user = ensure_hr_manager_user()

		digest = int(hashlib.md5(self.id().encode()).hexdigest(), 16)
		self.start = add_days(today(), -(450 + 3 * (digest % 600)))
		# The attendance-request preview resolves the holiday list per date
		# (P3-U9), so this method's past-year window needs an assignment that
		# covers it and not only the current-year one.
		ensure_holiday_list_assignment_from(self.company, self.start)
		self.created = []

	def tearDown(self):
		frappe.set_user("Administrator")
		for name in self.created:
			if not frappe.db.exists(DOCTYPE, name):
				continue
			doc = frappe.get_doc(DOCTYPE, name)
			if doc.docstatus == 1:
				doc.cancel()
			for row in frappe.get_all("Attendance", filters={"attendance_request": name}, pluck="name"):
				attendance = frappe.get_doc("Attendance", row)
				if attendance.docstatus == 1:
					attendance.cancel()
				frappe.delete_doc("Attendance", row, force=True, ignore_permissions=True)
			frappe.db.delete("DocShare", {"share_doctype": DOCTYPE, "share_name": name})
			frappe.delete_doc(DOCTYPE, name, force=True, ignore_permissions=True)
		frappe.db.set_value("Employee", self.employee_name, "reports_to", self.manager_name)

	# --- helpers -----------------------------------------------------------

	def _day(self, offset=0):
		return str(add_days(self.start, offset))

	def _pending_manager(self, days=1, reason="Work From Home", explanation="Router died"):
		frappe.set_user(EMPLOYEE_USER)
		created = create_my_attendance_request(
			from_date=self._day(),
			to_date=self._day(days - 1),
			reason=reason,
			explanation=explanation,
		)
		self.created.append(created["name"])
		send_my_attendance_request(created["name"], expected_modified=created["modified"])
		frappe.set_user("Administrator")
		return created["name"]

	def _state(self, name):
		return frappe.db.get_value(DOCTYPE, name, "workflow_state")

	def _queue_names(self, kind="attendance"):
		return [row["name"] for row in get_my_approvals()["pending"] if row["kind"] == kind]

	# --- the queue and the evidence (P3-AE7) --------------------------------

	def test_the_queue_carries_the_reason_dates_explanation_and_what_the_calendar_shows(self):
		name = self._pending_manager(days=2)

		frappe.set_user(MANAGER_USER)
		row = next(entry for entry in get_my_approvals()["pending"] if entry["name"] == name)
		self.assertEqual(row["kind"], "attendance")
		self.assertEqual(row["doctype"], DOCTYPE)
		self.assertEqual(row["reason"], "Work From Home")
		self.assertEqual(row["explanation"], "Router died")
		self.assertEqual(row["status"], "Pending Manager")
		self.assertEqual(row["total_days"], 2)
		self.assertFalse(row["half_day"])
		self.assertEqual([day["date"] for day in row["calendar"]], [self._day(), self._day(1)])
		self.assertEqual({day["status"] for day in row["calendar"]}, {None})

		detail = get_approval_detail("attendance", name)
		self.assertEqual(detail["kind"], "attendance")
		self.assertEqual(detail["explanation"], "Router died")
		self.assertEqual(detail["state"], "Pending Manager")
		self.assertEqual(detail["total_days"], 2)
		self.assertTrue(detail["working_days_known"])
		self.assertEqual(len(detail["days"]), 2)
		self.assertEqual(detail["modified"], str(frappe.db.get_value(DOCTYPE, name, "modified")))

	def test_the_managers_approve_is_the_decision_and_writes_the_attendance(self):
		"""P4-R6: one step. The manager's Approve submits the request, so the
		Attendance rows exist the moment they decide -- it used to move the
		request to Pending HR and write nothing."""
		name = self._pending_manager()

		frappe.set_user(MANAGER_USER)
		result = act_on_approval(DOCTYPE, name, "Approve", **token(DOCTYPE, name))
		self.assertEqual(result["state"], "Approved")

		frappe.set_user("Administrator")
		self.assertEqual(self._state(name), "Approved")
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, "docstatus"), 1)
		self.assertEqual(
			len(frappe.get_all("Attendance", filters={"attendance_request": name})),
			1,
			"HRMS's cascaded Attendance insert has to succeed under the manager's session",
		)
		self.assertEqual(
			frappe.get_all("DocShare", filters={"share_doctype": DOCTYPE, "share_name": name}),
			[],
			"the manager's access ends with their decision",
		)

	# --- refusals (P3-AE8) --------------------------------------------------

	def test_send_back_without_a_reason_is_refused_and_leaves_no_comment(self):
		name = self._pending_manager()

		frappe.set_user(MANAGER_USER)
		with self.assertRaises(frappe.ValidationError):
			act_on_approval(DOCTYPE, name, "Send Back", **token(DOCTYPE, name))

		frappe.set_user("Administrator")
		self.assertEqual(self._state(name), "Pending Manager")
		self.assertEqual(
			frappe.get_all(
				"Comment",
				filters={"reference_doctype": DOCTYPE, "reference_name": name, "comment_type": "Comment"},
				pluck="content",
			),
			[],
		)

	def test_send_back_with_a_reason_records_it(self):
		name = self._pending_manager()

		frappe.set_user(MANAGER_USER)
		act_on_approval(DOCTYPE, name, "Send Back", comment="Pick the Tuesday", **token(DOCTYPE, name))

		frappe.set_user("Administrator")
		self.assertEqual(self._state(name), "Sent Back")
		self.assertEqual(
			frappe.db.get_value(DOCTYPE, name, DECISION_REASON_FIELD),
			"Pick the Tuesday",
			"P4-KTD7a: the reason lives on the record, which outlives its comments",
		)
		self.assertEqual(
			frappe.utils.strip_html(
				frappe.db.get_value(
					"Comment",
					{"reference_doctype": DOCTYPE, "reference_name": name, "comment_type": "Comment"},
					"content",
				)
			).strip(),
			"Pick the Tuesday",
		)

	# P3-U6 scenario 5

	def test_an_unrelated_manager_gets_no_detail_and_cannot_act(self):
		frappe.set_user("Administrator")
		make_test_user(OTHER_MANAGER_USER, self.company)
		name = self._pending_manager()

		frappe.set_user(OTHER_MANAGER_USER)
		with self.assertRaises(frappe.PermissionError):
			get_approval_detail("attendance", name)
		with self.assertRaises(frappe.PermissionError):
			act_on_approval(DOCTYPE, name, "Approve", **token(DOCTYPE, name))
		self.assertNotIn(name, self._queue_names())

		frappe.set_user("Administrator")
		self.assertEqual(self._state(name), "Pending Manager")

	# P3-U6 scenario 6 / P3-KTD7

	def test_a_decided_request_is_in_nobodys_portal_queue_and_cannot_be_acted_on(self):
		"""P4-U1: the state the manager's Approve reaches is Approved, not
		Pending HR. The HR queue itself arrives in P4-U3; until then HR's own
		portal action on a decided request is refused like anybody's."""
		name = self._pending_manager()
		frappe.set_user(MANAGER_USER)
		act_on_approval(DOCTYPE, name, "Approve", **token(DOCTYPE, name))
		self.assertNotIn(name, self._queue_names())

		# The manager's own second attempt, and an HR Manager's from the
		# portal: both are refused as already decided.
		with self.assertRaises(frappe.ValidationError) as refused:
			act_on_approval(DOCTYPE, name, "Approve", **token(DOCTYPE, name))
		self.assertIn("already been decided", str(refused.exception))

		# The HR Manager fixture holds the role and no Employee record, so
		# it has no portal queue at all -- only the action to refuse.
		frappe.set_user(self.hr_user)
		with self.assertRaises(frappe.ValidationError):
			act_on_approval(DOCTYPE, name, "Approve", **token(DOCTYPE, name))

		frappe.set_user("Administrator")
		self.assertEqual(self._state(name), "Approved")

	def test_a_stale_token_is_refused(self):
		name = self._pending_manager()
		stale = token(DOCTYPE, name)

		frappe.set_user("Administrator")
		frappe.db.set_value(DOCTYPE, name, "explanation", "HR touched this")

		frappe.set_user(MANAGER_USER)
		with self.assertRaises(frappe.ValidationError):
			act_on_approval(DOCTYPE, name, "Approve", **stale)
		# And the other half of the token: the state, read from
		# `workflow_state` rather than from a `status` field this doctype
		# does not have (P3-U6 step 0).
		with self.assertRaises(frappe.ValidationError):
			act_on_approval(
				DOCTYPE,
				name,
				"Approve",
				expected_modified=frappe.db.get_value(DOCTYPE, name, "modified"),
				expected_state="Pending HR",
			)

		frappe.set_user("Administrator")
		self.assertEqual(self._state(name), "Pending Manager")

	def test_every_kind_is_registered_in_every_per_kind_map(self):
		"""P3-U6 step 0, P3-U9. The table replaced `if timesheet else leave`,
		where a third doctype silently became a Timesheet. A kind missing one
		of the per-kind answers would reintroduce exactly that."""
		from helixhr import api

		doctypes = set(api._APPROVAL_DOCTYPES.values())
		self.assertEqual(set(api._APPROVAL_KINDS), doctypes)
		for doctype, kind in api._APPROVAL_KINDS.items():
			self.assertEqual(
				set(kind),
				{
					"state_field",
					"detail",
					"may_act",
					"is_open",
					"open_message",
					"hr_state",
					"hr_queue",
					"act",
				},
				msg=doctype,
			)
		self.assertEqual(set(api._QUEUE_TITLE), set(api._APPROVAL_DOCTYPES))
		self.assertEqual(len(api._APPROVAL_SUMMARY_COLLECTORS), len(doctypes))
		self.assertEqual(len(api._DECIDED_COLLECTORS), len(doctypes))
		# Each kind's "already decided" sentence names its own record type.
		self.assertEqual(
			len({kind["open_message"] for kind in api._APPROVAL_KINDS.values()}), len(doctypes)
		)

	def test_a_decided_request_is_the_managers_receipt(self):
		name = self._pending_manager()
		frappe.set_user(MANAGER_USER)
		act_on_approval(DOCTYPE, name, "Approve", **token(DOCTYPE, name))

		receipts = [row for row in get_my_approvals()["decided"] if row["name"] == name]
		self.assertEqual(len(receipts), 1)
		self.assertEqual(receipts[0]["kind"], "attendance")
		self.assertEqual(receipts[0]["label"], "Attendance request")
		self.assertEqual(receipts[0]["status"], "Approved")


class TestFourOutcomesAndTheHrQueue(IntegrationTestCase):
	"""P4-U3. The four outcomes on the server, the one rule table behind
	them, and the HR queue inside the portal (P4-R1, R5, R6, R8, R9, R11,
	R12, R13).

	Its own employees on purpose. `EMPLOYEE_USER`'s Leave Allocation on a
	long-lived bench predates this suite and drifts (docs/runbook.md), and
	this class needs three distinct reporting lines anyway: a manager's
	report, an HR Manager's *own* report, and the HR Manager themselves.

	Attendance windows are the past-year pattern the other request suites
	use -- hashed from the test id, never nearer than 450 days back -- so two
	methods can never collide on HRMS's overlap rule.
	"""

	HR_REPORT_USER = "hr-queue-report@helixhr.test"
	QUEUE_EMPLOYEE_USER = "hr-queue-employee@helixhr.test"

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# Every Send to HR fires an Email-channel Notification from inside
		# the save, and `frappe.sendmail` throws with no default outgoing
		# account (P4-KTD9).
		ensure_test_email_account()

	def setUp(self):
		frappe.set_user("Administrator")
		_, _, self.manager_name, _ = make_test_employee_and_manager()
		self.company = frappe.db.get_value("Employee", self.manager_name, "company")
		ensure_holiday_list_assignment(self.company)

		self.hr_employee, self.hr_user = make_test_hr_manager_employee()
		self.employee_name = make_test_user(
			self.QUEUE_EMPLOYEE_USER, self.company, reports_to=self.manager_name
		)
		self.hr_report = make_test_user(
			self.HR_REPORT_USER, self.company, reports_to=self.hr_employee
		)
		# The HR Manager is somebody's report too, so a request of their own
		# has a manager to go to -- which is what makes "nobody decides their
		# own" testable on the one identity that used to bypass it.
		frappe.db.set_value("Employee", self.hr_employee, "reports_to", self.manager_name)
		for employee in (self.employee_name, self.hr_employee, self.hr_report):
			frappe.db.set_value("Employee", employee, "leave_approver", MANAGER_USER)
		ensure_leave_approver_role(MANAGER_USER)

		digest = int(hashlib.md5(self.id().encode()).hexdigest(), 16)
		self.start = add_days(today(), -(450 + 3 * (digest % 600)))
		ensure_holiday_list_assignment_from(self.company, self.start)
		self.leave_date = add_days(today(), 20 + (digest % 60))
		self.created = []
		self.attendance = []

	def tearDown(self):
		frappe.set_user("Administrator")
		for name in self.created:
			if not frappe.db.exists(DOCTYPE, name):
				continue
			doc = frappe.get_doc(DOCTYPE, name)
			if doc.docstatus == 1:
				doc.cancel()
			for row in frappe.get_all("Attendance", filters={"attendance_request": name}, pluck="name"):
				self.attendance.append(row)
			frappe.db.delete("DocShare", {"share_doctype": DOCTYPE, "share_name": name})
			frappe.delete_doc(DOCTYPE, name, force=True, ignore_permissions=True)
		for row in set(self.attendance):
			if not frappe.db.exists("Attendance", row):
				continue
			doc = frappe.get_doc("Attendance", row)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Attendance", row, force=True, ignore_permissions=True)

	# --- helpers -----------------------------------------------------------

	def _day(self, offset=0):
		return str(add_days(self.start, offset))

	def _pending_manager_request(self, employee_user=None, days=1):
		frappe.set_user(employee_user or self.QUEUE_EMPLOYEE_USER)
		created = create_my_attendance_request(
			from_date=self._day(),
			to_date=self._day(days - 1),
			reason="Work From Home",
			explanation="Router died",
		)
		self.created.append(created["name"])
		send_my_attendance_request(created["name"], expected_modified=created["modified"])
		frappe.set_user("Administrator")
		return created["name"]

	def _seed_attendance(self, offset, status, employee=None):
		frappe.set_user("Administrator")
		doc = frappe.get_doc(
			{
				"doctype": "Attendance",
				"employee": employee or self.employee_name,
				"attendance_date": self._day(offset),
				"status": status,
				"company": self.company,
			}
		)
		doc.insert(ignore_permissions=True)
		doc.submit()
		self.attendance.append(doc.name)
		return doc.name

	def _project(self):
		from helixhr.tests.utils import TEST_COMPANY, ensure_test_company

		project_name = "_Test HR Queue Project"
		existing = frappe.db.get_value("Project", {"project_name": project_name}, "name")
		if not existing:
			ensure_test_company()
			existing = (
				frappe.get_doc(
					{
						"doctype": "Project",
						"project_name": project_name,
						"status": "Open",
						"company": TEST_COMPANY,
					}
				)
				.insert(ignore_permissions=True)
				.name
			)
		for user in (self.QUEUE_EMPLOYEE_USER, self.HR_REPORT_USER):
			if not frappe.db.exists(
				"User Permission", {"user": user, "allow": "Project", "for_value": existing}
			):
				frappe.get_doc(
					{
						"doctype": "User Permission",
						"user": user,
						"allow": "Project",
						"for_value": existing,
					}
				).insert(ignore_permissions=True)
		return existing

	def _pending_timesheet(self, employee_user=None):
		"""One Pending Approval week, on a window hashed from the test id so
		two methods never write the same week for the same employee."""
		project = self._project()
		user = employee_user or self.QUEUE_EMPLOYEE_USER
		digest = int(hashlib.md5(f"{self.id()}:{user}".encode()).hexdigest(), 16)
		monday, _ = get_week_bounds(add_days(today(), (digest % 200000) * 7))
		frappe.set_user(user)
		frappe.cache.delete(f"helixhr:rate-limit:save_my_week:{user}")
		name = save_my_week(
			str(monday), json.dumps([{"date": str(monday), "project": project, "hours": 4, "note": ""}])
		)
		apply_workflow({"doctype": "Timesheet", "name": name}, "Submit")
		frappe.set_user("Administrator")
		self.addCleanup(self._remove_timesheet, name)
		return name

	def _remove_timesheet(self, name):
		frappe.set_user("Administrator")
		if not frappe.db.exists("Timesheet", name):
			return
		doc = frappe.get_doc("Timesheet", name)
		if doc.docstatus == 1:
			doc.cancel()
		frappe.db.delete("DocShare", {"share_doctype": "Timesheet", "share_name": name})
		frappe.delete_doc("Timesheet", name, force=True, ignore_permissions=True)

	def _open_leave(self, employee=None, user=None, leave_type="Casual Leave"):
		employee = employee or self.employee_name
		user = user or self.QUEUE_EMPLOYEE_USER
		ensure_leave_allocation(employee, leave_type, 30)
		frappe.set_user("Administrator")
		for existing in frappe.get_all(
			"Leave Application",
			filters={"employee": employee, "from_date": str(self.leave_date)},
			pluck="name",
		):
			frappe.delete_doc("Leave Application", existing, force=True, ignore_permissions=True)
		frappe.set_user(user)
		doc = frappe.get_doc(
			{
				"doctype": "Leave Application",
				"employee": employee,
				"leave_type": leave_type,
				"from_date": str(self.leave_date),
				"to_date": str(self.leave_date),
				"description": "hr queue",
				"leave_approver": frappe.db.get_value("Employee", employee, "leave_approver"),
			}
		)
		doc.insert()
		frappe.set_user("Administrator")
		self.addCleanup(self._remove_leave, doc.name)
		return doc.name

	def _remove_leave(self, name):
		frappe.set_user("Administrator")
		if not frappe.db.exists("Leave Application", name):
			return
		doc = frappe.get_doc("Leave Application", name)
		if doc.docstatus == 1:
			doc.cancel()
		frappe.delete_doc("Leave Application", name, force=True, ignore_permissions=True)

	def _queue(self):
		return {row["name"]: row for row in get_my_approvals()["pending"]}

	# --- the action list is a server fact (P4-R1, P4-KTD6) -----------------

	def test_the_manager_of_an_attendance_request_is_offered_all_four_outcomes(self):
		name = self._pending_manager_request()

		frappe.set_user(MANAGER_USER)
		self.assertEqual(
			get_approval_detail("attendance", name)["actions"],
			["Approve", "Send Back", "Reject", "Send to HR"],
		)

	def test_hr_is_offered_everything_but_the_hand_over(self):
		"""P4-R5: only a manager sends to HR; HR never has the button, on any
		kind. The three lists differ only in what their own lifecycle can
		do -- no Reject on a timesheet (P4-KTD2)."""
		request = self._pending_manager_request()
		frappe.set_user(MANAGER_USER)
		act_on_approval(DOCTYPE, request, "Send to HR", comment="policy check", **token(DOCTYPE, request))

		timesheet = self._pending_timesheet()
		frappe.set_user(MANAGER_USER)
		act_on_approval(
			"Timesheet", timesheet, "Send to HR", **token("Timesheet", timesheet)
		)

		leave = self._open_leave()
		frappe.set_user(MANAGER_USER)
		act_on_approval(
			"Leave Application", leave, "Send to HR", **token("Leave Application", leave)
		)

		frappe.set_user(self.hr_user)
		self.assertEqual(
			get_approval_detail("attendance", request)["actions"],
			["Approve", "Send Back", "Reject"],
		)
		self.assertEqual(
			get_approval_detail("timesheet", timesheet)["actions"], ["Approve", "Send Back"]
		)
		self.assertEqual(
			get_approval_detail("leave", leave)["actions"], ["Approve", "Send Back", "Reject"]
		)

	def test_the_requester_is_offered_nothing_and_refused(self):
		"""P4-R8, and the reason `_assert_may_act_on` checks it before the HR
		short-circuit: an HR Manager is otherwise waved straight through to
		their own request."""
		mine = self._pending_manager_request(employee_user=self.HR_REPORT_USER)

		frappe.set_user(self.HR_REPORT_USER)
		with self.assertRaises(frappe.PermissionError):
			get_approval_detail("attendance", mine)
		with self.assertRaises(frappe.PermissionError):
			act_on_approval(DOCTYPE, mine, "Approve", **token(DOCTYPE, mine))

		# The HR Manager's own leave, decided by themselves, through the
		# route that used to allow it.
		own = self._open_leave(employee=self.hr_employee, user=self.hr_user)
		frappe.set_user(self.hr_user)
		self.assertEqual(_allowed_actions(frappe.get_doc("Leave Application", own), self.hr_user), [])
		for action in ("Approve", "Send Back", "Reject", "Send to HR"):
			with self.assertRaises(frappe.PermissionError, msg=action):
				act_on_approval(
					"Leave Application",
					own,
					action,
					comment="mine",
					**token("Leave Application", own),
				)

		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Leave Application", own, "status"), "Open")

	def test_the_action_list_is_derived_from_the_workflow_and_not_from_code(self):
		"""P4-KTD6. Narrowing a transition's condition in Desk narrows the
		button row and the act together, with no code change -- which is the
		whole point of deriving the list from `get_transitions`."""
		name = self._pending_timesheet()
		frappe.set_user(MANAGER_USER)
		self.assertIn("Send to HR", get_approval_detail("timesheet", name)["actions"])

		frappe.set_user("Administrator")
		original = self._set_transition_condition("Send to HR", 'doc.name == "no such week"')
		self.addCleanup(self._set_transition_condition, "Send to HR", original)

		frappe.set_user(MANAGER_USER)
		self.assertNotIn("Send to HR", get_approval_detail("timesheet", name)["actions"])
		with self.assertRaises(frappe.ValidationError):
			act_on_approval("Timesheet", name, "Send to HR", **token("Timesheet", name))

	def _set_transition_condition(self, action, condition):
		frappe.set_user("Administrator")
		workflow = frappe.get_doc("Workflow", "Timesheet Approval")
		row = next(
			transition
			for transition in workflow.transitions
			if transition.action == action and transition.allowed == "Employee"
		)
		previous = row.condition
		row.condition = condition
		workflow.save(ignore_permissions=True)
		frappe.clear_cache()
		return previous

	# --- reasons and notes (P4-R3, P4-R4, P4-R9) ---------------------------

	def test_a_hand_over_takes_an_optional_note_and_a_decision_takes_a_reason(self):
		name = self._pending_manager_request()

		frappe.set_user(MANAGER_USER)
		# No note at all is accepted for a routing act.
		result = act_on_approval(DOCTYPE, name, "Send to HR", **token(DOCTYPE, name))
		self.assertEqual(result["state"], REQUEST_PENDING_HR)

		frappe.set_user(self.hr_user)
		for action in ("Send Back", "Reject"):
			with self.assertRaises(frappe.ValidationError, msg=action):
				act_on_approval(DOCTYPE, name, action, **token(DOCTYPE, name))

		act_on_approval(DOCTYPE, name, "Reject", comment="not on the roster", **token(DOCTYPE, name))
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, "workflow_state"), REQUEST_REJECTED)
		self.assertEqual(
			frappe.db.get_value(DOCTYPE, name, DECISION_REASON_FIELD), "not on the roster"
		)

	def test_stale_evidence_from_before_a_hand_over_is_refused(self):
		"""P4-R9: the token is what the approver was looking at, and a Send
		to HR moves the record like any other outcome."""
		name = self._pending_manager_request()
		frappe.set_user(MANAGER_USER)
		stale = token(DOCTYPE, name)
		act_on_approval(DOCTYPE, name, "Send to HR", comment="policy check", **stale)

		frappe.set_user(self.hr_user)
		with self.assertRaises(frappe.ValidationError):
			act_on_approval(DOCTYPE, name, "Approve", **stale)

		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, "workflow_state"), REQUEST_PENDING_HR)

	# --- P4-KTD5: the overwrite gate moved to the manager's Approve --------

	def test_the_managers_approve_is_refused_once_a_day_has_attendance(self):
		"""Auto-attendance can mark a day Present between the send and the
		decision, and the manager cannot see Attendance at all -- so the
		overwrite decision stays with HR, who can."""
		name = self._pending_manager_request()
		self._seed_attendance(0, "Present")

		frappe.set_user(MANAGER_USER)
		with self.assertRaises(frappe.ValidationError) as refused:
			act_on_approval(DOCTYPE, name, "Approve", **token(DOCTYPE, name))
		self.assertIn("send this to HR instead", str(refused.exception))

		frappe.set_user("Administrator")
		self.assertEqual(
			frappe.db.get_value(DOCTYPE, name, "workflow_state"), REQUEST_PENDING_MANAGER
		)

		# HR is not gated: they can read the calendar and weigh the rewrite.
		frappe.set_user(self.hr_user)
		self.assertIn("Approve", get_approval_detail("attendance", name)["actions"])
		act_on_approval(DOCTYPE, name, "Approve", **token(DOCTYPE, name))

		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, "docstatus"), 1)

	# --- the HR queue (P4-R11, P4-KTD7) ------------------------------------

	def test_the_hr_queue_carries_every_kind_tagged_with_its_sender_and_note(self):
		request = self._pending_manager_request()
		timesheet = self._pending_timesheet()
		leave = self._open_leave()

		frappe.set_user(MANAGER_USER)
		act_on_approval(DOCTYPE, request, "Send to HR", comment="policy check", **token(DOCTYPE, request))
		act_on_approval(
			"Timesheet", timesheet, "Send to HR", comment="hours on Sunday", **token("Timesheet", timesheet)
		)
		act_on_approval(
			"Leave Application", leave, "Send to HR", comment="special case", **token("Leave Application", leave)
		)

		frappe.set_user(self.hr_user)
		queue = self._queue()
		for name, note in (
			(request, "policy check"),
			(timesheet, "hours on Sunday"),
			(leave, "special case"),
		):
			self.assertIn(name, queue)
			self.assertTrue(queue[name]["for_hr"], msg=name)
			self.assertTrue(queue[name]["sent_to_hr_by"], msg=name)
			self.assertEqual(queue[name]["hr_note"], note, msg=name)

		# Oldest first, across the two halves of the one queue.
		sent = [row["sent_on"] for row in get_my_approvals()["pending"]]
		self.assertEqual(sent, sorted(sent))

		# And the same three keys on the detail head, so the screen needs no
		# second request to say "sent by X".
		detail = get_approval_detail("timesheet", timesheet)
		self.assertTrue(detail["for_hr"])
		self.assertEqual(detail["hr_note"], "hours on Sunday")

		# The manager who handed them over has no queue left.
		frappe.set_user(MANAGER_USER)
		mine = self._queue()
		for name in (request, timesheet, leave):
			self.assertNotIn(name, mine, msg=name)

	def test_hr_sees_their_own_reports_and_hr_work_and_nothing_else(self):
		"""P4-KTD7, P4-R6. HR Manager holds native read on all three
		doctypes, so the line-manager half of their queue has to be narrowed
		by `reports_to` -- without it the queue is every pending request in
		the company."""
		theirs = self._pending_manager_request(employee_user=self.HR_REPORT_USER)
		somebody_elses = self._pending_manager_request()
		theirs_week = self._pending_timesheet(employee_user=self.HR_REPORT_USER)
		somebody_elses_week = self._pending_timesheet()

		frappe.set_user(self.hr_user)
		queue = self._queue()
		self.assertIn(theirs, queue)
		self.assertIn(theirs_week, queue)
		self.assertFalse(queue[theirs]["for_hr"], "their own report's work is not HR work")
		self.assertNotIn(
			somebody_elses, queue, "another manager's pending request is not HR's to decide"
		)
		self.assertNotIn(somebody_elses_week, queue)

	def test_a_stage_hr_leave_leaves_its_approvers_queue(self):
		"""P4-R7. `leave_approver` deliberately stays the manager so HRMS's
		own validation and DocShare keep working, so the queue is what has to
		drop the row."""
		leave = self._open_leave()

		frappe.set_user(MANAGER_USER)
		self.assertIn(leave, self._queue())
		act_on_approval("Leave Application", leave, "Send to HR", **token("Leave Application", leave))
		self.assertNotIn(leave, self._queue())

		frappe.set_user(self.hr_user)
		self.assertIn(leave, self._queue())

	def test_an_hr_managers_own_request_is_never_in_their_own_queue(self):
		own = self._pending_manager_request(employee_user=self.hr_user)

		frappe.set_user(self.hr_user)
		self.assertNotIn(own, self._queue())

	def test_the_approvals_nav_item_is_there_for_hr_with_an_empty_queue(self):
		"""P4-R13. HR work arrives without warning; a rail item that comes
		and goes is one nobody trusts."""
		frappe.set_user("Administrator")
		frappe.db.set_value("Employee", self.hr_report, "reports_to", self.manager_name)
		self.addCleanup(
			frappe.db.set_value, "Employee", self.hr_report, "reports_to", self.hr_employee
		)

		frappe.set_user(self.hr_user)
		boot = get_portal_bootstrap()
		self.assertEqual(self._queue(), {})
		self.assertFalse(boot["has_reports"])
		self.assertTrue(boot["can_approve"])
