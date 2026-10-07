"""Plan 2026-10-07-001: leave, shift and expense approvers follow Reports to.

U1 -- every Employee save derives the three approver fields from the Active
`reports_to` manager's login. U2 -- an approver change moves the employee's
pending HRMS requests to the new approver, who alone is notified.
"""

import hashlib

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from helixhr.api import act_on_approval
from helixhr.events import APPROVER_FIELDS, LEAVE_STAGE_HR
from helixhr.tests.utils import (
	clear_open_leave,
	ensure_holiday_list_assignment,
	ensure_leave_allocation,
	ensure_test_company,
	make_test_user,
)

MANAGER_A = "atr-manager-a@helixhr.test"
MANAGER_B = "atr-manager-b@helixhr.test"
EMPLOYEE = "atr-employee@helixhr.test"
RENAMED = "atr-manager-a-renamed@helixhr.test"


def _approvers(employee):
	return frappe.db.get_value("Employee", employee, APPROVER_FIELDS, as_dict=True)


def _shared_users(doctype, name):
	return sorted(
		frappe.get_all("DocShare", filters={"share_doctype": doctype, "share_name": name}, pluck="user")
	)


def _arrivals(user, doctype, name):
	return frappe.db.count(
		"Notification Log", {"for_user": user, "document_type": doctype, "document_name": name}
	)


class _ApproverCase(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		ensure_holiday_list_assignment(self.company)
		self.manager_a = make_test_user(MANAGER_A, self.company)
		self.manager_b = make_test_user(MANAGER_B, self.company)
		for manager, user in ((self.manager_a, MANAGER_A), (self.manager_b, MANAGER_B)):
			doc = frappe.get_doc("Employee", manager)
			if doc.status != "Active" or doc.user_id != user:
				doc.status, doc.user_id = "Active", user
				doc.save(ignore_permissions=True)
		self.employee = make_test_user(EMPLOYEE, self.company)
		self._report_to(self.manager_a)
		frappe.db.set_value("User", EMPLOYEE, "enabled", 1)
		clear_open_leave(self.employee)
		digest = int(hashlib.md5(self.id().encode()).hexdigest(), 16)
		self.leave_date = add_days(today(), 10 + digest % 100)

	def tearDown(self):
		frappe.set_user("Administrator")

	def _report_to(self, manager, **extra):
		doc = frappe.get_doc("Employee", self.employee)
		doc.reports_to = manager
		doc.update(extra)
		doc.save(ignore_permissions=True)
		return doc


class TestApproversAreDerived(_ApproverCase):
	"""U1."""

	def test_reports_to_sets_all_three_and_grants_the_roles(self):
		self.assertEqual(set(_approvers(self.employee).values()), {MANAGER_A})
		roles = frappe.get_roles(MANAGER_A)
		self.assertIn("Leave Approver", roles)
		self.assertIn("Expense Approver", roles)

	def test_changing_reports_to_rewrites_all_three(self):
		self._report_to(self.manager_b)
		self.assertEqual(set(_approvers(self.employee).values()), {MANAGER_B})

	def test_clearing_reports_to_clears_all_three(self):
		self._report_to(None)
		self.assertEqual(set(_approvers(self.employee).values()), {None})

	def test_a_new_employee_gets_the_managers_login_on_insert(self):
		"""Derived from the doc, not the database -- an insert has no row."""
		name = make_test_user("atr-new-joiner@helixhr.test", self.company, reports_to=self.manager_b)
		self.assertEqual(set(_approvers(name).values()), {MANAGER_B})

	def test_a_manager_without_a_login_or_not_active_leaves_no_approver(self):
		frappe.db.set_value("Employee", self.manager_b, "user_id", None)
		self.addCleanup(frappe.db.set_value, "Employee", self.manager_b, "user_id", MANAGER_B)
		self._report_to(self.manager_b)
		self.assertEqual(set(_approvers(self.employee).values()), {None})

		frappe.db.set_value("Employee", self.manager_b, "user_id", MANAGER_B)
		frappe.db.set_value("Employee", self.manager_b, "status", "Inactive")
		self.addCleanup(frappe.db.set_value, "Employee", self.manager_b, "status", "Active")
		self._report_to(self.manager_b)
		self.assertEqual(set(_approvers(self.employee).values()), {None})

	def test_a_typed_approver_is_overwritten_with_the_derived_one(self):
		self._report_to(self.manager_a, leave_approver=MANAGER_B, shift_request_approver=MANAGER_B)
		self.assertEqual(set(_approvers(self.employee).values()), {MANAGER_A})

		frappe.client.set_value("Employee", self.employee, "expense_approver", MANAGER_B)
		self.assertEqual(_approvers(self.employee).expense_approver, MANAGER_A)

	def test_the_managers_new_login_reaches_every_report(self):
		if not frappe.db.exists("User", RENAMED):
			frappe.get_doc(
				{"doctype": "User", "email": RENAMED, "first_name": "Renamed", "send_welcome_email": 0}
			).insert(ignore_permissions=True)
		manager = frappe.get_doc("Employee", self.manager_a)
		manager.user_id = RENAMED
		manager.save(ignore_permissions=True)
		self.addCleanup(self._restore_manager_a)

		self.assertEqual(set(_approvers(self.employee).values()), {RENAMED})
		self.assertIn("Leave Approver", frappe.get_roles(RENAMED))

	def test_suspending_the_manager_clears_reports_without_re_enabling_their_login(self):
		"""The cascade writes the report's fields; it never saves the report,
		because ERPNext's Employee save re-syncs (and can re-enable) the User."""
		frappe.db.set_value("User", EMPLOYEE, "enabled", 0)
		manager = frappe.get_doc("Employee", self.manager_a)
		manager.status = "Suspended"
		manager.save(ignore_permissions=True)
		self.addCleanup(self._restore_manager_a)

		self.assertEqual(set(_approvers(self.employee).values()), {None})
		self.assertEqual(frappe.db.get_value("User", EMPLOYEE, "enabled"), 0)

	def _restore_manager_a(self):
		frappe.set_user("Administrator")
		manager = frappe.get_doc("Employee", self.manager_a)
		manager.user_id, manager.status = MANAGER_A, "Active"
		manager.save(ignore_permissions=True)


class TestPendingRequestsMove(_ApproverCase):
	"""U2."""

	def _pending_leave(self, **fields):
		ensure_leave_allocation(self.employee, "Casual Leave", 5)
		doc = frappe.get_doc(
			{
				"doctype": "Leave Application",
				"employee": self.employee,
				"leave_type": "Casual Leave",
				"from_date": self.leave_date,
				"to_date": self.leave_date,
				"description": "atr",
				"leave_approver": MANAGER_A,
				**fields,
			}
		).insert(ignore_permissions=True)
		return doc.name

	def _raw(self, doctype, approver_field, **fields):
		"""A pending Shift Request / Expense Claim without their controllers
		(master data a test site lacks) -- the move only reads the row."""
		name = frappe.generate_hash(length=10)
		frappe.db.bulk_insert(
			doctype,
			["name", "employee", "company", "docstatus", approver_field, *fields],
			[(name, self.employee, self.company, 0, MANAGER_A, *fields.values())],
		)
		frappe.share.add_docshare(doctype, name, MANAGER_A, submit=1, flags={"ignore_share_permission": True})
		return name

	def test_a_pending_leave_moves_and_only_the_new_approver_is_told(self):
		name = self._pending_leave()
		told_a = _arrivals(MANAGER_A, "Leave Application", name)

		self._report_to(self.manager_b)

		row = frappe.db.get_value("Leave Application", name, ["leave_approver", "leave_approver_name"], as_dict=True)
		self.assertEqual(row.leave_approver, MANAGER_B)
		self.assertEqual(row.leave_approver_name, frappe.utils.get_fullname(MANAGER_B))
		self.assertEqual(_shared_users("Leave Application", name), [MANAGER_B])
		self.assertEqual(_arrivals(MANAGER_B, "Leave Application", name), 1)
		self.assertEqual(_arrivals(MANAGER_A, "Leave Application", name), told_a)

		frappe.set_user(MANAGER_B)
		modified = str(frappe.db.get_value("Leave Application", name, "modified"))
		act_on_approval("Leave Application", name, "Approve", expected_modified=modified, expected_state="Open")
		self.assertEqual(
			frappe.db.get_value("Leave Application", name, ["status", "docstatus"]), ("Approved", 1)
		)

	def test_shift_requests_and_expense_claims_move_the_same_way(self):
		shift = self._raw("Shift Request", "approver", status="Draft")
		claim = self._raw("Expense Claim", "expense_approver", approval_status="Draft")

		self._report_to(self.manager_b)

		self.assertEqual(frappe.db.get_value("Shift Request", shift, "approver"), MANAGER_B)
		self.assertEqual(frappe.db.get_value("Expense Claim", claim, "expense_approver"), MANAGER_B)
		self.assertEqual(_shared_users("Shift Request", shift), [MANAGER_B])
		self.assertEqual(_shared_users("Expense Claim", claim), [MANAGER_B])
		self.assertEqual(_arrivals(MANAGER_B, "Expense Claim", claim), 1)

	def test_decided_hr_stage_and_submitted_requests_stay_put(self):
		hr_stage = self._pending_leave()
		frappe.db.set_value("Leave Application", hr_stage, "helixhr_stage", LEAVE_STAGE_HR)
		rejected = self._pending_leave(from_date=add_days(self.leave_date, 1), to_date=add_days(self.leave_date, 1))
		frappe.db.set_value("Leave Application", rejected, "status", "Rejected")
		claim = self._raw("Expense Claim", "expense_approver", approval_status="Approved")

		self._report_to(self.manager_b)

		for doctype, name, field in (
			("Leave Application", hr_stage, "leave_approver"),
			("Leave Application", rejected, "leave_approver"),
			("Expense Claim", claim, "expense_approver"),
		):
			self.assertEqual(frappe.db.get_value(doctype, name, field), MANAGER_A, name)
			self.assertEqual(_arrivals(MANAGER_B, doctype, name), 0, name)

	def test_with_no_usable_manager_a_pending_leave_keeps_its_approver(self):
		"""HRMS refuses to decide a leave with no approver while "Leave
		Approver mandatory" is on, so nothing moves to nobody."""
		name = self._pending_leave()
		told = frappe.db.count("Notification Log", {"document_name": name})

		self._report_to(None)

		self.assertEqual(set(_approvers(self.employee).values()), {None})
		self.assertEqual(frappe.db.get_value("Leave Application", name, "leave_approver"), MANAGER_A)
		self.assertEqual(frappe.db.count("Notification Log", {"document_name": name}), told)

	def test_create_user_on_the_manager_reaches_reports(self):
		"""ERPNext's Create User `db_set`s `user_id` before saving, so the
		save's before/after diff never shows the change."""
		frappe.db.set_value("Employee", self.manager_b, "user_id", None)
		self._report_to(self.manager_b)
		self.assertEqual(set(_approvers(self.employee).values()), {None})

		frappe.db.set_value("Employee", self.manager_b, "user_id", MANAGER_B)
		frappe.get_doc("Employee", self.manager_b).save(ignore_permissions=True)
		self.assertEqual(set(_approvers(self.employee).values()), {MANAGER_B})
