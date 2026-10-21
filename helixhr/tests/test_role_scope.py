"""Plan 2026-10-19-001 U2: `role_scope`, the R4 person gate, and the fixture
helpers that now write HelixHR Access Assignment rows."""

import frappe
from frappe.tests import IntegrationTestCase

from helixhr.tests.utils import (
	GRANT_HOLDER_USER,
	TEST_PASSWORD,
	_make_role_user,
	create_test_company,
	ensure_baseline_company,
	ensure_hr_manager_user,
	ensure_test_company,
	make_assignment,
	make_test_company_grant,
	make_test_user,
)
from helixhr.utils import (
	ALL_ORGS_SCOPE_KEY,
	ORG_VIEWER_ROLE,
	REPORT_MANAGER_ROLE,
	clear_role_scope_cache,
	role_scope,
)

SCOPED_USER = "role-scope@helixhr.test"
NO_EMPLOYEE_USER = "role-scope-no-employee@helixhr.test"
LATER_COMPANY = "_Test Role Scope Later Co"

NONE = {"kind": "none", "company": None}
UNSCOPED = {"kind": "unscoped", "company": None}


def _rows(user, role=None):
	filters = {"user": user, **({"role": role} if role else {})}
	return {
		(row.role, row.scope_key, row.allow_export, row.origin)
		for row in frappe.get_all(
			"HelixHR Access Assignment",
			filters=filters,
			fields=["role", "scope_key", "allow_export", "origin"],
		)
	}


class _RoleScope(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.other = ensure_baseline_company()
		self.employee = make_test_user(SCOPED_USER, self.company, status="Active")
		frappe.db.set_value("User", SCOPED_USER, "enabled", 1)
		# Rows and roles persist between methods: IntegrationTestCase rolls
		# back per class.
		frappe.db.delete("HelixHR Access Assignment", {"user": ["in", [SCOPED_USER, GRANT_HOLDER_USER]]})
		frappe.db.delete(
			"Has Role", {"parent": SCOPED_USER, "role": ["in", ["System Manager", "HR Manager"]]}
		)
		frappe.clear_cache(user=SCOPED_USER)
		clear_role_scope_cache()

	def tearDown(self):
		frappe.set_user("Administrator")
		clear_role_scope_cache()


class TestRoleScopeShape(_RoleScope):
	def test_two_named_orgs_give_companies_sorted(self):
		make_assignment(SCOPED_USER, "HR Manager", [self.other, self.company])
		self.assertEqual(
			role_scope(SCOPED_USER, "HR Manager"),
			{"kind": "companies", "companies": sorted([self.company, self.other])},
		)

	def test_one_named_org_gives_company(self):
		make_assignment(SCOPED_USER, "HR Manager", [self.other])
		self.assertEqual(role_scope(SCOPED_USER, "HR Manager"), {"kind": "company", "company": self.other})

	def test_an_all_orgs_row_gives_unscoped(self):
		make_assignment(SCOPED_USER, "HR Manager", "all")
		self.assertEqual(role_scope(SCOPED_USER, "HR Manager"), UNSCOPED)

	def test_all_orgs_wins_over_named_rows(self):
		make_assignment(SCOPED_USER, "HR Manager", [self.company])
		make_assignment(SCOPED_USER, "HR Manager", "all")
		self.assertEqual(role_scope(SCOPED_USER, "HR Manager"), UNSCOPED)

	def test_an_org_created_later_is_inside_an_all_orgs_scope(self):
		make_assignment(SCOPED_USER, "HR Manager", "all")
		self.assertEqual(role_scope(SCOPED_USER, "HR Manager"), UNSCOPED)
		create_test_company(LATER_COMPANY, "TRSL")
		clear_role_scope_cache()
		# "unscoped" names no company list, so a new org needs no new row.
		self.assertEqual(role_scope(SCOPED_USER, "HR Manager"), UNSCOPED)

	def test_rows_are_per_role(self):
		make_assignment(SCOPED_USER, "HR Manager", [self.company])
		self.assertEqual(role_scope(SCOPED_USER, "IT Team"), NONE)

	def test_a_frappe_role_without_a_row_gives_none(self):
		# R3: the Frappe role alone gives no reach.
		frappe.get_doc("User", SCOPED_USER).add_roles("HR Manager")
		self.assertEqual(role_scope(SCOPED_USER, "HR Manager"), NONE)

	def test_administrator_is_unscoped(self):
		self.assertEqual(role_scope("Administrator", "HR Manager"), UNSCOPED)

	def test_system_manager_is_unscoped_whatever_its_rows(self):
		# R5: unscoped, even on a role it holds no row for or a named-org row.
		frappe.get_doc("User", SCOPED_USER).add_roles("System Manager")
		frappe.clear_cache(user=SCOPED_USER)
		make_assignment(SCOPED_USER, "HR Manager", [self.company])
		self.assertEqual(role_scope(SCOPED_USER, "HR Manager"), UNSCOPED)
		self.assertEqual(role_scope(SCOPED_USER, "IT Team"), UNSCOPED)


class TestPersonGate(_RoleScope):
	def test_only_employee_row_left_gives_none(self):
		make_assignment(SCOPED_USER, "HR Manager", "all")
		frappe.db.set_value("Employee", self.employee, "status", "Left")
		clear_role_scope_cache()
		self.assertEqual(role_scope(SCOPED_USER, "HR Manager"), NONE)

	def test_a_left_system_manager_gets_none(self):
		frappe.get_doc("User", SCOPED_USER).add_roles("System Manager")
		frappe.clear_cache(user=SCOPED_USER)
		frappe.db.set_value("Employee", self.employee, "status", "Left")
		clear_role_scope_cache()
		self.assertEqual(role_scope(SCOPED_USER, "HR Manager"), NONE)

	def test_disabled_user_gives_none(self):
		make_assignment(SCOPED_USER, "HR Manager", [self.company])
		frappe.db.set_value("User", SCOPED_USER, "enabled", 0)
		clear_role_scope_cache()
		self.assertEqual(role_scope(SCOPED_USER, "HR Manager"), NONE)

	def test_no_employee_keeps_the_rows_scope(self):
		if not frappe.db.exists("User", NO_EMPLOYEE_USER):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": NO_EMPLOYEE_USER,
					"first_name": "Scope",
					"new_password": TEST_PASSWORD,
					"send_welcome_email": 0,
				}
			).insert(ignore_permissions=True)
		make_assignment(NO_EMPLOYEE_USER, "HR Manager", [self.other])
		self.assertEqual(
			role_scope(NO_EMPLOYEE_USER, "HR Manager"), {"kind": "company", "company": self.other}
		)


class TestRoleScopeCache(_RoleScope):
	def test_a_second_call_in_the_request_makes_no_query(self):
		make_assignment(SCOPED_USER, "HR Manager", [self.company])
		role_scope(SCOPED_USER, "HR Manager")
		with self.assertQueryCount(0):
			self.assertEqual(
				role_scope(SCOPED_USER, "HR Manager"), {"kind": "company", "company": self.company}
			)
			# Another role reads the same cached rows and gate.
			self.assertEqual(role_scope(SCOPED_USER, "IT Team"), NONE)

	def test_clearing_the_cache_reads_rows_written_since(self):
		self.assertEqual(role_scope(SCOPED_USER, "HR Manager"), NONE)
		frappe.get_doc(
			{"doctype": "HelixHR Access Assignment", "user": SCOPED_USER, "role": "HR Manager", "all_orgs": 1}
		).insert(ignore_permissions=True)
		self.assertEqual(role_scope(SCOPED_USER, "HR Manager"), NONE, "cached for the request")
		clear_role_scope_cache(SCOPED_USER)
		self.assertEqual(role_scope(SCOPED_USER, "HR Manager"), UNSCOPED)


class TestFixtureRows(_RoleScope):
	def test_make_assignment_is_idempotent_and_adds_the_frappe_role(self):
		make_assignment(SCOPED_USER, "IT Team", [self.company, self.other])
		make_assignment(SCOPED_USER, "IT Team", [self.company])
		self.assertEqual(
			_rows(SCOPED_USER),
			{("IT Team", self.company, 0, "manual"), ("IT Team", self.other, 0, "manual")},
		)
		self.assertIn("IT Team", frappe.get_roles(SCOPED_USER))

	def test_role_user_gets_a_row_on_its_employee_company(self):
		_make_role_user(SCOPED_USER, "HelixHR Portal Admin")
		self.assertEqual(_rows(SCOPED_USER), {("HelixHR Portal Admin", self.company, 0, "manual")})

	def test_report_manager_row_carries_export_like_the_migration(self):
		_make_role_user(SCOPED_USER, REPORT_MANAGER_ROLE)
		self.assertEqual(_rows(SCOPED_USER), {(REPORT_MANAGER_ROLE, self.company, 1, "manual")})

	def test_no_employee_hr_manager_gets_an_all_orgs_row(self):
		user = ensure_hr_manager_user()
		self.assertIn(("HR Manager", ALL_ORGS_SCOPE_KEY, 0, "manual"), _rows(user, "HR Manager"))
		self.assertEqual(role_scope(user, "HR Manager"), UNSCOPED)

	def test_an_all_data_grant_mirrors_to_org_viewer_rows(self):
		make_test_user(GRANT_HOLDER_USER, self.company, status="Active")
		make_test_company_grant(GRANT_HOLDER_USER, [self.company, self.other], allow_export=1)
		self.assertEqual(
			_rows(GRANT_HOLDER_USER, ORG_VIEWER_ROLE),
			{(ORG_VIEWER_ROLE, self.company, 1, "migrated"), (ORG_VIEWER_ROLE, self.other, 1, "migrated")},
		)
		self.assertIn(ORG_VIEWER_ROLE, frappe.get_roles(GRANT_HOLDER_USER))

	def test_a_resaved_grant_replaces_its_rows(self):
		make_test_user(GRANT_HOLDER_USER, self.company, status="Active")
		make_test_company_grant(GRANT_HOLDER_USER, [self.company, self.other])
		make_test_company_grant(GRANT_HOLDER_USER, [self.other], level="Reports only", allow_export=1)
		self.assertEqual(_rows(GRANT_HOLDER_USER, ORG_VIEWER_ROLE), set())
		self.assertEqual(
			_rows(GRANT_HOLDER_USER, REPORT_MANAGER_ROLE), {(REPORT_MANAGER_ROLE, self.other, 1, "migrated")}
		)
		make_test_company_grant(GRANT_HOLDER_USER, [self.other], level="Reports only", enabled=0)
		self.assertEqual(_rows(GRANT_HOLDER_USER, REPORT_MANAGER_ROLE), set())
		self.assertEqual(role_scope(GRANT_HOLDER_USER, REPORT_MANAGER_ROLE), NONE)
