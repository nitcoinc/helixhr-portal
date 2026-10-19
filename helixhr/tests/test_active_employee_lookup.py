"""Plan 2026-10-18-001 U17 (R9): every "this login's Employee" lookup resolves
the Active row, so a rehired or transferred login -- an Active row plus a
newer Left one -- keeps its scope, while "only Left rows" and "no rows at
all" stay the two different answers they always were."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from helixhr.tests.utils import (
	ensure_baseline_company,
	ensure_test_company,
	make_assignment,
	make_test_company_grant,
	make_test_user,
)
from helixhr.utils import (
	_company_role_holders,
	active_company_grant,
	active_employees_for_users,
	clear_role_scope_cache,
	resolve_admin_scope,
	resolve_project_scope,
)

HR_USER = "u17-hr@helixhr.test"
DM_USER = "u17-dm@helixhr.test"
GRANT_USER = "u17-grant@helixhr.test"
LEFT_USER = "u17-left@helixhr.test"
NO_EMPLOYEE_HR = "u17-no-employee-hr@helixhr.test"


def _add_role(user, role):
	if role not in frappe.get_roles(user):
		frappe.get_doc("User", user).add_roles(role)
	frappe.clear_cache(user=user)


def _newer_left_row(user, company):
	"""The rehire's old row, newer than the Active one. ERPNext refuses a second
	row for one login on insert, so the login is set afterwards -- the way
	HRMS's Employee Transfer leaves the old row."""
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


class TestActiveEmployeeLookups(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company_a = ensure_test_company()
		self.company_b = ensure_baseline_company()
		self.added = []

	def tearDown(self):
		frappe.set_user("Administrator")
		for name in self.added:
			frappe.delete_doc("Employee", name, force=True, ignore_permissions=True)

	def _rehire(self, user):
		active = make_test_user(user, self.company_a, create_user_permission=0)
		self.added.append(_newer_left_row(user, self.company_b))
		return active

	def test_rehired_hr_manager_keeps_their_company_scope(self):
		self._rehire(HR_USER)
		make_assignment(HR_USER, "HR Manager", [self.company_a])
		self.assertEqual(resolve_admin_scope(HR_USER), {"kind": "company", "company": self.company_a})

	def test_hr_manager_with_only_a_left_row_has_no_scope(self):
		employee = make_test_user(LEFT_USER, self.company_a, create_user_permission=0)
		make_assignment(LEFT_USER, "HR Manager", [self.company_a])
		frappe.db.set_value("Employee", employee, {"status": "Left", "relieving_date": today()})
		clear_role_scope_cache()
		self.assertEqual(resolve_admin_scope(LEFT_USER)["kind"], "none")

	def test_hr_manager_with_no_employee_stays_unscoped(self):
		if not frappe.db.exists("User", NO_EMPLOYEE_HR):
			frappe.get_doc(
				{"doctype": "User", "email": NO_EMPLOYEE_HR, "first_name": "Nobody", "send_welcome_email": 0}
			).insert(ignore_permissions=True)
		make_assignment(NO_EMPLOYEE_HR, "HR Manager", "all")
		self.assertEqual(resolve_admin_scope(NO_EMPLOYEE_HR)["kind"], "unscoped")

	def test_rehired_delivery_manager_keeps_project_scope(self):
		self._rehire(DM_USER)
		make_assignment(DM_USER, "HelixHR Delivery Manager", [self.company_a])
		self.assertEqual(resolve_project_scope(DM_USER), {"kind": "company", "company": self.company_a})

	def test_rehired_grant_holder_keeps_their_grant(self):
		make_test_user(GRANT_USER, self.company_a, create_user_permission=0)
		make_test_company_grant(GRANT_USER, [self.company_b])
		self.added.append(_newer_left_row(GRANT_USER, self.company_b))
		grant = active_company_grant(GRANT_USER)
		self.assertIsNotNone(grant)
		self.assertEqual(grant["home_company"], self.company_a)
		self.assertIn(self.company_b, grant["companies"])

	def test_rehired_role_holder_is_counted_in_their_active_company(self):
		self._rehire(HR_USER)
		make_assignment(HR_USER, "HR Manager", [self.company_a])
		self.assertIn(HR_USER, _company_role_holders("HR Manager", self.company_a))
		self.assertNotIn(HR_USER, _company_role_holders("HR Manager", self.company_b))

	def test_batch_answers_every_login(self):
		active = self._rehire(HR_USER)
		answers = active_employees_for_users([HR_USER, "u17-unknown@helixhr.test"])
		self.assertEqual((answers[HR_USER].state, answers[HR_USER].name), ("active", active))
		self.assertEqual(answers["u17-unknown@helixhr.test"].state, "none")
		self.assertEqual(active_employees_for_users([]), {})
