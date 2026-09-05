import hashlib
import json

import frappe
from frappe.model.workflow import apply_workflow
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from helixhr.api import act_on_approval, get_approval_detail, get_my_approvals, save_my_week
from helixhr.tests.utils import (
	EMPLOYEE_USER,
	MANAGER_USER,
	ensure_holiday_list_assignment,
	ensure_leave_allocation,
	ensure_leave_approver_role,
	make_test_employee_and_manager,
)
from helixhr.utils import get_week_bounds


def token(doctype, name):
	"""The concurrency token the screen always sends (P2-U7 step 3):
	`get_approval_detail` hands the manager a `modified` and a state, and
	`act_on_approval` refuses a decision that does not carry them back."""
	field = "workflow_state" if doctype == "Timesheet" else "status"
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

	def test_reject_leave_without_comment_is_refused(self):
		leave = self._pending_leave()

		frappe.set_user(MANAGER_USER)
		with self.assertRaises(frappe.ValidationError):
			act_on_approval("Leave Application", leave.name, "Reject")

	def test_approve_leave_sets_status_and_employee_sees_it(self):
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

	def test_reject_timesheet_with_comment_via_act_on_approval(self):
		"""AE4 wrong-manager for Timesheet is already covered directly by
		test_api_timesheet.py; this only proves act_on_approval routes to
		the same workflow transition (its condition/guard, not new logic
		here)."""
		ts = self._pending_timesheet()

		frappe.set_user(MANAGER_USER)
		act_on_approval(
			"Timesheet", ts.name, "Reject", comment="Please fix your hours", **token("Timesheet", ts.name)
		)

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
			act_on_approval("Timesheet", name, "Reject", comment="on second thoughts", **current)

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
		# even offsets from day 98 keep every date inside the current
		# allocation period, clear of the other fixtures in this repo
		# (which sit inside the first 98 days, on odd offsets), and never
		# consecutive -- Casual Leave caps continuous days, and HRMS reads
		# two applications on adjacent days as one continuous leave.
		methods = sorted(name for name in dir(self) if name.startswith("test_"))
		self.leave_date = add_days(today(), 98 + 2 * methods.index(self.id().split(".")[-1]))
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
