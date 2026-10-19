"""Plan 2026-10-14-001 U1: the HelixHR Company Access grant and the read
resolvers built on it -- since plan 2026-10-19-001 U4, on the Org viewer and
Reports rows its migration writes. The offboarding hook that switched a grant
off is gone (U10, KTD2); `test_preflight.TestAccessLifecycle` covers what
replaced it."""

import frappe
from frappe.tests import IntegrationTestCase

from helixhr.tests.utils import (
	GRANT_HOLDER_USER,
	clear_test_company_grants,
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
		clear_test_company_grants()
		self.foreign = make_test_user("grant-foreign@helixhr.test", self.other)

	def tearDown(self):
		frappe.set_user("Administrator")
		clear_test_company_grants()


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
		self.assertEqual(resolve_read_scope(GRANT_HOLDER_USER)["kind"], "none")

	def test_grant_never_changes_the_write_resolvers(self):
		_, hr = make_test_hr_manager_employee()
		before = (resolve_admin_scope(hr), resolve_project_scope(hr))
		make_test_company_grant(hr, [self.other])
		self.assertEqual((resolve_admin_scope(hr), resolve_project_scope(hr)), before)


class TestProjectReadScope(_Grants):
	def test_delivery_manager_reads_their_org_and_gains_granted_companies(self):
		"""Plan 2026-10-19-001 U4: a Delivery Manager reaches every project
		their org owns, member or not, and the grant's Org viewer rows add
		the granted company's."""
		_, dm = make_test_delivery_manager()
		mine = make_test_project(self.company, "_T Grant DM own", members=(dm,))
		theirs = make_test_project(self.other, "_T Grant DM theirs")
		home_other = make_test_project(self.company, "_T Grant DM not member")
		make_test_company_grant(dm, [self.other])

		scope = resolve_project_read_scope(dm)
		self.assertEqual(scope, {"kind": "companies", "companies": [self.company, self.other]})
		for project in (mine, theirs, home_other):
			self.assertTrue(project_in_scope(project, scope), project)
		listed = frappe.get_all("Project", filters=project_scope_filters(scope), pluck="name")
		self.assertTrue({mine, theirs, home_other} <= set(listed))
		self.assertEqual(resolve_project_scope(dm), {"kind": "company", "company": self.company})

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
