"""Plan 2026-10-14-001 U1: the HelixHR Company Access grant, the read
resolvers built on it, and the offboarding hook that switches it off."""

import frappe
from frappe.tests import IntegrationTestCase

from helixhr.tests.utils import (
	GRANT_HOLDER_USER,
	ensure_baseline_company,
	ensure_hr_manager_user,
	ensure_test_company,
	make_test_company_grant,
	make_test_delivery_manager,
	make_test_hr_manager_employee,
	make_test_project,
	make_test_user,
)
from helixhr.utils import (
	active_company_grant,
	employee_in_admin_scope,
	project_in_scope,
	project_scope_filters,
	resolve_admin_scope,
	resolve_project_read_scope,
	resolve_project_scope,
	resolve_read_scope,
)


class _Grants(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.other = ensure_baseline_company()
		self.holder_employee = make_test_user(GRANT_HOLDER_USER, self.company, status="Active")
		# The e2e fixtures commit grants; every test starts from none.
		frappe.db.delete("HelixHR Company Access Company", {"parent": ["like", "%@helixhr.test"]})
		frappe.db.delete("HelixHR Company Access", {"user": ["like", "%@helixhr.test"]})
		self.foreign = make_test_user("grant-foreign@helixhr.test", self.other)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.delete("HelixHR Company Access Company", {"parent": ["like", "%@helixhr.test"]})
		frappe.db.delete("HelixHR Company Access", {"user": ["like", "%@helixhr.test"]})


class TestReadScope(_Grants):
	def test_all_data_grant_reads_exactly_the_named_companies(self):
		make_test_company_grant(GRANT_HOLDER_USER, [self.company, self.other])
		scope = resolve_read_scope(GRANT_HOLDER_USER)
		self.assertEqual(scope, {"kind": "companies", "companies": [self.company, self.other]})
		self.assertTrue(employee_in_admin_scope(self.foreign, scope))
		# Writes and REST hooks still see nothing for a plain employee.
		self.assertEqual(resolve_admin_scope(GRANT_HOLDER_USER)["kind"], "none")

	def test_a_grant_on_another_company_alone_does_not_add_home(self):
		make_test_company_grant(GRANT_HOLDER_USER, [self.other])
		scope = resolve_read_scope(GRANT_HOLDER_USER)
		self.assertEqual(scope, {"kind": "company", "company": self.other})
		self.assertFalse(employee_in_admin_scope(self.holder_employee, scope))

	def test_reports_only_grant_reads_no_people(self):
		make_test_company_grant(GRANT_HOLDER_USER, [self.other], level="Reports only")
		self.assertEqual(resolve_read_scope(GRANT_HOLDER_USER)["kind"], "none")
		self.assertEqual(active_company_grant(GRANT_HOLDER_USER)["companies"], [self.other])

	def test_anchored_hr_manager_with_a_grant_reads_both(self):
		_, hr = make_test_hr_manager_employee()
		make_test_company_grant(hr, [self.other])
		self.assertEqual(
			resolve_read_scope(hr), {"kind": "companies", "companies": [self.company, self.other]}
		)
		# The grant naming home only is the same as no grant.
		make_test_company_grant(hr, [self.company])
		self.assertEqual(resolve_read_scope(hr), resolve_admin_scope(hr))

	def test_anchorless_hr_manager_stays_unscoped(self):
		self.assertEqual(resolve_read_scope(ensure_hr_manager_user())["kind"], "unscoped")

	def test_disabled_grant_is_ignored(self):
		make_test_company_grant(GRANT_HOLDER_USER, [self.other], enabled=0)
		self.assertIsNone(active_company_grant(GRANT_HOLDER_USER))
		self.assertEqual(resolve_read_scope(GRANT_HOLDER_USER)["kind"], "none")

	def test_grant_on_an_inactive_or_moved_holder_is_ignored(self):
		make_test_company_grant(GRANT_HOLDER_USER, [self.other])
		frappe.db.set_value("Employee", self.holder_employee, "status", "Suspended")
		self.assertIsNone(active_company_grant(GRANT_HOLDER_USER))
		frappe.db.set_value("Employee", self.holder_employee, "status", "Active")
		self.assertIsNotNone(active_company_grant(GRANT_HOLDER_USER))
		# `db_set` skips the hook; the stored home company still guards it.
		frappe.db.set_value("Employee", self.holder_employee, "company", self.other)
		try:
			self.assertIsNone(active_company_grant(GRANT_HOLDER_USER))
		finally:
			frappe.db.set_value("Employee", self.holder_employee, "company", self.company)

	def test_grant_never_changes_the_write_resolvers(self):
		_, hr = make_test_hr_manager_employee()
		before = (resolve_admin_scope(hr), resolve_project_scope(hr))
		make_test_company_grant(hr, [self.other])
		self.assertEqual((resolve_admin_scope(hr), resolve_project_scope(hr)), before)


class TestProjectReadScope(_Grants):
	def test_delivery_manager_keeps_assigned_projects_and_gains_granted_companies(self):
		_, dm = make_test_delivery_manager()
		mine = make_test_project(self.company, "_T Grant DM own", members=(dm,))
		theirs = make_test_project(self.other, "_T Grant DM theirs")
		home_other = make_test_project(self.company, "_T Grant DM not member")
		make_test_company_grant(dm, [self.other])

		scope = resolve_project_read_scope(dm)
		self.assertEqual(scope["kind"], "companies")
		self.assertTrue(project_in_scope(mine, scope))
		self.assertTrue(project_in_scope(theirs, scope))
		self.assertFalse(project_in_scope(home_other, scope))
		names = set(project_scope_filters(scope)["name"][1])
		self.assertTrue({mine, theirs} <= names)
		self.assertNotIn(home_other, names)
		self.assertEqual(resolve_project_scope(dm), {"kind": "assigned", "user": dm})

	def test_non_delivery_manager_holder_reads_granted_company_projects(self):
		theirs = make_test_project(self.other, "_T Grant reader theirs")
		make_test_company_grant(GRANT_HOLDER_USER, [self.other])
		scope = resolve_project_read_scope(GRANT_HOLDER_USER)
		self.assertTrue(project_in_scope(theirs, scope))
		self.assertEqual(resolve_project_scope(GRANT_HOLDER_USER)["kind"], "none")


class TestGrantDoctype(_Grants):
	def test_duplicate_unknown_and_empty_companies_are_refused(self):
		with self.assertRaises(frappe.ValidationError):
			make_test_company_grant(GRANT_HOLDER_USER, [self.other, self.other])
		with self.assertRaises(frappe.ValidationError):
			make_test_company_grant(GRANT_HOLDER_USER, ["_No Such Company"])
		with self.assertRaises(frappe.ValidationError):
			make_test_company_grant(GRANT_HOLDER_USER, [])

	def test_only_system_manager_holds_a_docperm(self):
		roles = {row.role for row in frappe.get_meta("HelixHR Company Access").permissions}
		self.assertEqual(roles, {"System Manager"})


class TestOffboardingHook(_Grants):
	def _holder(self, suffix):
		user = f"grant-hook-{suffix}@helixhr.test"
		employee = make_test_user(user, self.company)
		make_test_company_grant(user, [self.other])
		return employee, user

	def _grant(self, user):
		return frappe.db.get_value(
			"HelixHR Company Access", user, ["enabled", "disabled_reason"], as_dict=True
		)

	def test_leaving_active_disables_and_reactivating_does_not_re_enable(self):
		employee, user = self._holder("inactive")
		doc = frappe.get_doc("Employee", employee)
		doc.status = "Inactive"
		doc.save(ignore_permissions=True)
		grant = self._grant(user)
		self.assertEqual(grant.enabled, 0)
		self.assertIn("Inactive", grant.disabled_reason)
		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{
					"reference_doctype": "User",
					"reference_name": user,
					"content": ["like", "%company access%"],
				},
			)
		)
		doc.reload()
		doc.status = "Active"
		doc.save(ignore_permissions=True)
		self.assertEqual(self._grant(user).enabled, 0)

	def test_login_change_disables(self):
		employee, user = self._holder("login")
		make_test_user("grant-hook-login-new@helixhr.test", self.company)
		# Free the new login from the Employee the factory gave it.
		frappe.db.set_value("Employee", {"user_id": "grant-hook-login-new@helixhr.test"}, "user_id", None)
		doc = frappe.get_doc("Employee", employee)
		doc.user_id = "grant-hook-login-new@helixhr.test"
		doc.save(ignore_permissions=True)
		self.assertEqual(self._grant(user).enabled, 0)

	def test_company_move_disables(self):
		employee, user = self._holder("move")
		doc = frappe.get_doc("Employee", employee)
		doc.company = self.other
		doc.save(ignore_permissions=True)
		self.assertEqual(self._grant(user).enabled, 0)
		self.assertIn(self.other, self._grant(user).disabled_reason)

	def test_transfer_to_a_new_employee_disables(self):
		employee, user = self._holder("transfer")
		old = frappe.get_doc("Employee", employee)
		# An HRMS Employee Transfer moves the login to a new record
		# without saving the old one first (plan 2026-10-14-001 R16).
		frappe.db.set_value("Employee", employee, "user_id", None)
		new = frappe.copy_doc(old)
		new.user_id = user
		new.insert(ignore_permissions=True)
		self.assertEqual(self._grant(user).enabled, 0)
		self.assertIn(new.name, self._grant(user).disabled_reason)
