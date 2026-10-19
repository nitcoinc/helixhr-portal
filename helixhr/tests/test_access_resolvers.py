"""Plan 2026-10-19-001 U3: HR Manager, HR User and IT Admin reach, and every
HR/IT mail recipient, come from assignments (R3, R6).

Company A is the test company, B the baseline second company and C a third
one no HR or IT row here names. Every login is this module's own.
"""

import uuid

import frappe
from frappe.tests import IntegrationTestCase

from helixhr import reminders
from helixhr.api import (
	_handler_comment_refusal,
	_holds_routed_role,
	_oversight_scope,
	act_on_approval,
	create_my_request,
	get_approval_detail,
	get_my_approvals,
)
from helixhr.events import _enabled_users_with_role, _hr_managers, _is_hr
from helixhr.tests.test_approvals_cross_company import HR_REPORT_USER, _CrossCompanyCase
from helixhr.tests.utils import (
	create_test_company,
	ensure_baseline_company,
	ensure_test_company,
	make_assignment,
	make_test_user,
)
from helixhr.utils import (
	_company_role_holders,
	clear_role_scope_cache,
	reset_rate_limit,
	resolve_admin_scope,
)

COMPANY_C = "_Test Access Resolvers C"
LATER_COMPANY = "_Test Access Resolvers Later"

HR_AB = "res-hr-ab@helixhr.test"
HR_A = "res-hr-a@helixhr.test"
LEADER = "res-leader@helixhr.test"
ROLE_ONLY = "res-role-only@helixhr.test"
IT_AB = "res-it-ab@helixhr.test"
HR_USER_AB = "res-hr-user-ab@helixhr.test"
SYSTEM_MANAGER = "res-sm@helixhr.test"
REQ_A = "res-req-a@helixhr.test"
REQ_B = "res-req-b@helixhr.test"
REQ_C = "res-req-c@helixhr.test"
REQ_LATER = "res-req-later@helixhr.test"

_MODULE_USERS = (HR_AB, HR_A, LEADER, ROLE_ONLY, IT_AB, HR_USER_AB, SYSTEM_MANAGER)
_MANAGED = ("HR Manager", "HR User", "IT Team", "System Manager")


class _Resolvers(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.a = ensure_test_company()
		self.b = ensure_baseline_company()
		self.c = create_test_company(COMPANY_C, "TARC")

		# Rows and roles persist between methods (rollback is per class).
		frappe.db.delete("HelixHR Access Assignment", {"user": ["in", list(_MODULE_USERS)]})
		for user in _MODULE_USERS:
			# No self-scoping User Permission: it would hide every other
			# Employee's request from these handlers' Frappe lists.
			make_test_user(user, self.a, create_user_permission=0, status="Active")
			frappe.db.delete("Has Role", {"parent": user, "role": ["in", list(_MANAGED)]})
			frappe.clear_cache(user=user)
		clear_role_scope_cache()

		make_assignment(HR_AB, "HR Manager", [self.a, self.b])
		make_assignment(HR_A, "HR Manager", [self.a])
		make_assignment(LEADER, "HR Manager", "all")
		make_assignment(IT_AB, "IT Team", [self.a, self.b])
		make_assignment(HR_USER_AB, "HR User", [self.a, self.b])
		# R3: the Frappe roles alone, with no assignment behind them.
		frappe.get_doc("User", ROLE_ONLY).add_roles("HR Manager", "HR User", "IT Team")
		frappe.clear_cache(user=ROLE_ONLY)

		self.emp_a = make_test_user(REQ_A, self.a)
		self.emp_b = make_test_user(REQ_B, self.b)
		self.emp_c = make_test_user(REQ_C, self.c)

	def tearDown(self):
		frappe.set_user("Administrator")
		clear_role_scope_cache()

	def _file(self, user, category):
		frappe.set_user(user)
		reset_rate_limit("create_my_request", user)
		name = create_my_request(
			category=category, subject="Resolver check", details="", operation_key=str(uuid.uuid4())
		)["name"]
		frappe.set_user("Administrator")
		self.addCleanup(_delete_request, name)
		return name


def _delete_request(name):
	frappe.set_user("Administrator")
	if frappe.db.exists("HR Request", name):
		frappe.delete_doc("HR Request", name, force=True, ignore_permissions=True)


class TestHrManagerScope(_Resolvers):
	def test_hr_manager_on_two_orgs_decides_in_b_and_is_refused_in_c(self):
		in_b = self._file(REQ_B, "HR Letter")
		in_c = self._file(REQ_C, "HR Letter")

		frappe.set_user(HR_AB)
		self.assertEqual(
			resolve_admin_scope(HR_AB), {"kind": "companies", "companies": sorted([self.a, self.b])}
		)
		self.assertIn("Reject", get_approval_detail("request", in_b)["actions"])
		act_on_approval("HR Request", in_b, "Reject", comment="Not this way.", **_request_token(in_b))
		self.assertEqual(frappe.db.get_value("HR Request", in_b, "status"), "Rejected")
		with self.assertRaises(frappe.PermissionError):
			get_approval_detail("request", in_c)
		self.assertNotIn(in_c, frappe.get_list("HR Request", pluck="name"))

	def test_a_frappe_role_without_a_row_is_not_hr_and_has_no_hr_queue(self):
		in_a = self._file(REQ_A, "HR Letter")

		frappe.set_user(ROLE_ONLY)
		self.assertFalse(_is_hr())
		self.assertEqual(resolve_admin_scope(ROLE_ONLY)["kind"], "none")
		self.assertIsNone(_oversight_scope())
		self.assertFalse(_holds_routed_role())
		self.assertFalse([row for row in get_my_approvals()["pending"] if row.get("lane") == "hr"])
		with self.assertRaises(frappe.PermissionError):
			get_approval_detail("request", in_a)
		self.assertNotIn(in_a, frappe.get_list("HR Request", pluck="name"))

	def test_the_leadership_preset_acts_in_an_org_created_later(self):
		self.assertEqual(resolve_admin_scope(LEADER)["kind"], "unscoped")
		later = create_test_company(LATER_COMPANY, "TARL")
		make_test_user(REQ_LATER, later)
		in_later = self._file(REQ_LATER, "HR Letter")

		frappe.set_user(LEADER)
		self.assertTrue(_is_hr())
		self.assertIn("Reject", get_approval_detail("request", in_later)["actions"])

	def test_a_left_hr_manager_with_rows_is_not_hr(self):
		# R4: rows stay, reach goes.
		employee = frappe.db.get_value("Employee", {"user_id": HR_AB}, "name")
		frappe.db.set_value("Employee", employee, "status", "Left")
		clear_role_scope_cache()
		self.assertFalse(_is_hr(HR_AB))


class TestLineManagerWhoIsHr(_CrossCompanyCase):
	"""The cross-company fixture's HR Manager holds a row on A only; their
	direct report sits in B."""

	def test_hr_manager_on_a_decides_their_report_in_b_with_line_manager_actions_only(self):
		self.assertEqual(resolve_admin_scope(self.hr_user), {"kind": "company", "company": self.company_a})
		week = self._pending_timesheet(HR_REPORT_USER)

		frappe.set_user(self.hr_user)
		self.assertEqual(
			get_approval_detail("timesheet", week)["actions"], ["Approve", "Send Back", "Send to HR"]
		)


class TestItAdminScope(_Resolvers):
	def test_it_admin_on_two_orgs_handles_a_b_request(self):
		in_b = self._file(REQ_B, "IT / Asset")

		frappe.set_user(IT_AB)
		self.assertTrue(_holds_routed_role())
		self.assertIn(in_b, frappe.get_list("HR Request", pluck="name"))
		self.assertTrue(get_approval_detail("request", in_b)["actions"])
		self.assertIsNone(_handler_comment_refusal(frappe.get_doc("HR Request", in_b), IT_AB))

	def test_it_admin_never_sees_a_c_request(self):
		in_c = self._file(REQ_C, "IT / Asset")

		frappe.set_user(IT_AB)
		self.assertNotIn(in_c, frappe.get_list("HR Request", pluck="name"))
		self.assertFalse(frappe.has_permission("HR Request", doc=in_c, user=IT_AB))
		with self.assertRaises(frappe.PermissionError):
			get_approval_detail("request", in_c)
		self.assertIsNotNone(_handler_comment_refusal(frappe.get_doc("HR Request", in_c), IT_AB))

	def test_an_it_role_without_a_row_works_nothing(self):
		in_a = self._file(REQ_A, "IT / Asset")

		frappe.set_user(ROLE_ONLY)
		self.assertNotIn(in_a, frappe.get_list("HR Request", pluck="name"))
		self.assertIsNotNone(_handler_comment_refusal(frappe.get_doc("HR Request", in_a), ROLE_ONLY))

	def test_arrival_mail_reaches_the_assigned_it_admin_and_not_a_role_only_holder(self):
		recipients = _enabled_users_with_role("IT Team", self.emp_b)
		self.assertIn(IT_AB, recipients)
		self.assertNotIn(ROLE_ONLY, recipients)
		self.assertNotIn(IT_AB, _enabled_users_with_role("IT Team", self.emp_c))


class TestHrUserOversight(_Resolvers):
	def test_hr_user_oversees_the_orgs_their_rows_name(self):
		self.assertEqual(
			_oversight_scope(HR_USER_AB), {"hr_manager": False, "companies": sorted([self.a, self.b])}
		)

	def test_hr_user_role_without_a_row_oversees_nothing(self):
		self.assertIsNone(_oversight_scope(ROLE_ONLY))


class TestRecipients(_Resolvers):
	def test_hr_stage_mail_goes_to_hr_managers_covering_the_company(self):
		recipients = _hr_managers(self.emp_b)
		self.assertIn(HR_AB, recipients)
		self.assertIn(LEADER, recipients)
		self.assertNotIn(HR_A, recipients)
		self.assertNotIn(ROLE_ONLY, recipients)

	def test_a_system_manager_holding_the_role_keeps_todays_company_rule(self):
		frappe.get_doc("User", SYSTEM_MANAGER).add_roles("System Manager", "HR Manager")
		frappe.clear_cache(user=SYSTEM_MANAGER)
		clear_role_scope_cache()
		self.assertIn(SYSTEM_MANAGER, _company_role_holders("HR Manager", self.a))
		self.assertNotIn(SYSTEM_MANAGER, _company_role_holders("HR Manager", self.b))

	def test_overdue_summaries_follow_hr_scope(self):
		recipients = reminders._active_hr_managers()
		self.assertIn(LEADER, recipients)
		self.assertIn(HR_A, recipients)
		self.assertNotIn(ROLE_ONLY, recipients)

		items = [
			{"name": name, "employee": employee, "owners": [], "owner_name": name}
			for name, employee in (("in-a", self.emp_a), ("in-b", self.emp_b), ("in-c", self.emp_c))
		]

		def covered(user):
			groups = reminders._summary_owners(items, user, project=lambda item: item["name"])
			return {name for group in groups for name in group["items"]}

		self.assertEqual(covered(LEADER), {"in-a", "in-b", "in-c"})
		self.assertEqual(covered(HR_A), {"in-a"})


def _request_token(name):
	row = frappe.db.get_value("HR Request", name, ["modified", "status"], as_dict=True)
	return {"expected_modified": str(row.modified), "expected_state": row.status}
