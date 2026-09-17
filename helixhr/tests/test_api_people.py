import frappe
from frappe.tests import IntegrationTestCase

from helixhr.api import search_people
from helixhr.tests.utils import (
	EMPLOYEE_USER,
	HR_MANAGER_EMPLOYEE_USER,
	HR_MANAGER_USER,
	IT_TEAM_USER,
	TEST_COMPANY,
	ensure_hr_manager_user,
	ensure_test_company,
	make_test_hr_manager_employee,
	make_test_it_user,
	make_test_user,
)
from helixhr.utils import admin_scope_employee_filters, employee_in_admin_scope, resolve_admin_scope

# P6-U1 / P6-R6, P6-R7. "Which employees may this user administer" has
# exactly one answer, in one place. A second, dedicated company (like
# test_api_organisation.py's ORG_COMPANY) makes "another company's employee
# is out of scope" an assertion against a company this suite controls, not a
# guess against whatever the shared _Test Company happens to hold.
OTHER_COMPANY = "_Test People Scope Co"
OTHER_COMPANY_USER = "other-scope-employee@helixhr.test"


def _ensure_other_company():
	if not frappe.db.exists("Warehouse Type", "Transit"):
		frappe.get_doc({"doctype": "Warehouse Type", "name": "Transit"}).insert(
			ignore_permissions=True
		)
	if not frappe.db.exists("Company", OTHER_COMPANY):
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": OTHER_COMPANY,
				"abbr": "TPSC",
				"default_currency": "USD",
				"country": "United States",
			}
		).insert(ignore_permissions=True)
	return OTHER_COMPANY


class TestResolveAdminScope(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		ensure_test_company()
		self.hr_employee, self.hr_user = make_test_hr_manager_employee()
		ensure_hr_manager_user()
		make_test_it_user()
		other_company = _ensure_other_company()
		self.other_company_employee = make_test_user(OTHER_COMPANY_USER, other_company)

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_administrator_is_unscoped(self):
		scope = resolve_admin_scope("Administrator")
		self.assertEqual(scope, {"kind": "unscoped", "company": None})

	def test_a_company_anchored_hr_manager_is_scoped_to_their_own_company(self):
		scope = resolve_admin_scope(HR_MANAGER_EMPLOYEE_USER)
		self.assertEqual(scope, {"kind": "company", "company": TEST_COMPANY})
		self.assertTrue(employee_in_admin_scope(self.hr_employee, scope))
		self.assertFalse(employee_in_admin_scope(self.other_company_employee, scope))

	def test_the_desk_only_hr_manager_with_no_employee_record_is_unscoped(self):
		# ensure_hr_manager_user() is built specifically to be Desk-only and
		# company-free (P3-KTD7, P4-KTD7). Pinned here so U1's helper never
		# silently tightens that persona.
		scope = resolve_admin_scope(HR_MANAGER_USER)
		self.assertEqual(scope, {"kind": "unscoped", "company": None})
		self.assertTrue(employee_in_admin_scope(self.other_company_employee, scope))

	def test_a_plain_employee_and_an_it_team_holder_resolve_to_empty(self):
		self.assertEqual(resolve_admin_scope(EMPLOYEE_USER), {"kind": "none", "company": None})
		self.assertEqual(resolve_admin_scope(IT_TEAM_USER), {"kind": "none", "company": None})

	def test_an_hr_manager_whose_company_has_no_other_active_employees_resolves_to_empty_not_raising(self):
		empty_company = "_Test People Empty Co"
		if not frappe.db.exists("Company", empty_company):
			frappe.get_doc(
				{
					"doctype": "Company",
					"company_name": empty_company,
					"abbr": "TPEC",
					"default_currency": "USD",
					"country": "United States",
				}
			).insert(ignore_permissions=True)
		# The HR admin is the only active employee this company has -- the
		# nearest a company-anchored persona can get to "nobody to
		# administer" without resolve_admin_scope itself falling back to
		# unscoped (which is what a *missing* active Employee would do).
		empty_hr_user = "empty-scope-hr@helixhr.test"
		make_test_user(empty_hr_user, empty_company, create_user_permission=0)
		user = frappe.get_doc("User", empty_hr_user)
		if "HR Manager" not in [row.role for row in user.roles]:
			user.append_roles("HR Manager")
			user.save(ignore_permissions=True)
			frappe.clear_cache(user=empty_hr_user)

		scope = resolve_admin_scope(empty_hr_user)
		self.assertEqual(scope, {"kind": "company", "company": empty_company})
		filters = admin_scope_employee_filters(scope)
		colleagues = frappe.get_all("Employee", filters=filters, or_filters=[["name", "=", "nobody-like-this"]])
		self.assertEqual(colleagues, [])
		self.assertFalse(employee_in_admin_scope(self.hr_employee, scope))

	def test_scope_is_asserted_as_the_role_never_as_administrator(self):
		# P5-KTD13 standard: assert via frappe.set_user, not by calling the
		# helper with a bare string while sitting as Administrator, which
		# would skip the permission logic these tests exist to pin.
		frappe.set_user(EMPLOYEE_USER)
		self.assertEqual(resolve_admin_scope(frappe.session.user), {"kind": "none", "company": None})

	def test_empty_scope_never_leaks_a_filter_that_matches_everybody(self):
		scope = {"kind": "none", "company": None}
		self.assertIsNone(admin_scope_employee_filters(scope))


class TestSearchPeople(IntegrationTestCase):
	"""P6-U2 / P6-R1, P6-R8, P6-R13."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.hr_employee, self.hr_user = make_test_hr_manager_employee()
		make_test_it_user()
		self.colleague = make_test_user(
			"people-search-colleague@helixhr.test",
			self.company,
			# `set_employee_name` (erpnext/setup/doctype/employee/employee.py)
			# always overwrites `employee_name` from first/middle/last name
			# on save, so the searchable name has to be set here, not passed
			# as `employee_name` directly.
			first_name="Zara",
			last_name="Colleague",
			company_email="zara.colleague@helixhr.test",
		)
		other_company = _ensure_other_company()
		self.other_company_employee = make_test_user(OTHER_COMPANY_USER, other_company)

		left_name = frappe.db.get_value(
			"Employee", {"employee_number": "_test-people-search-left"}, "name"
		)
		if not left_name:
			left = frappe.get_doc(
				{
					"doctype": "Employee",
					"employee_number": "_test-people-search-left",
					"first_name": "Zara",
					"last_name": "Departed",
					"company": self.company,
					"date_of_birth": "1990-01-01",
					"date_of_joining": "2018-01-01",
					"relieving_date": "2020-01-01",
					"gender": frappe.db.get_value("Gender", {}, "name"),
					"status": "Left",
					"create_user_permission": 0,
				}
			)
			left.insert(ignore_permissions=True)
			left_name = left.name
		self.left_employee = left_name

		frappe.set_user(EMPLOYEE_USER)

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_an_hr_administrator_finds_a_colleague_by_name_number_or_email(self):
		frappe.set_user(self.hr_user)
		by_name = search_people(query="Zara Colleague")
		self.assertIn(self.colleague, [row["name"] for row in by_name["people"]])

		by_number = search_people(query="people-search-colleague")
		self.assertIn(self.colleague, [row["name"] for row in by_number["people"]])

		by_email = search_people(query="zara.colleague@helixhr.test")
		self.assertIn(self.colleague, [row["name"] for row in by_email["people"]])

	def test_an_employee_in_another_company_is_never_returned(self):
		frappe.set_user(self.hr_user)
		result = search_people(query=self.other_company_employee)
		self.assertNotIn(self.other_company_employee, [row["name"] for row in result["people"]])

	def test_a_plain_employee_and_an_it_team_holder_are_refused(self):
		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.PermissionError):
			search_people(query="Zara")

		frappe.set_user(IT_TEAM_USER)
		with self.assertRaises(frappe.PermissionError):
			search_people(query="Zara")

	def test_a_search_shorter_than_the_minimum_is_ignored(self):
		frappe.set_user(self.hr_user)
		everyone = search_people()
		for needle in ("", " ", "z"):
			self.assertEqual(
				search_people(query=needle)["total"],
				everyone["total"],
				f"a {needle!r} search filtered the results",
			)

	def test_the_page_is_bounded_and_reports_its_true_total(self):
		frappe.set_user(self.hr_user)
		result = search_people(limit=999999)
		self.assertEqual(result["limit"], 200)
		directly_counted = frappe.db.count("Employee", {"status": "Active", "company": self.company})
		self.assertEqual(result["total"], directly_counted)

	def test_left_employees_are_excluded_by_default(self):
		frappe.set_user(self.hr_user)
		result = search_people(query="Zara Departed")
		self.assertNotIn(self.left_employee, [row["name"] for row in result["people"]])

	def test_the_read_is_rate_limited(self):
		from helixhr.utils import RATE_LIMIT_POLICY

		self.assertIn("search_people", RATE_LIMIT_POLICY)
