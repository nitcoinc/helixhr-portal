import frappe

TEST_COMPANY = "_Test Company"
MANAGER_USER = "manager@helixhr.test"
EMPLOYEE_USER = "employee@helixhr.test"
ORPHAN_USER = "no-employee@helixhr.test"
# Not "password" -- some sites (any with System Settings' password policy
# enabled, unlike a barebones fresh test site) reject it as a top-10
# common password.
TEST_PASSWORD = "Helixhr-Test-Fixture-2026!"

# Deliberately does NOT import erpnext.setup.doctype.employee.test_employee.
# That module imports erpnext.tests.utils.ERPNextTestSuite, whose *module
# load* runs BootStrapTestData() as a side effect -- it tries to create
# fiscal years and other master data unconditionally. On a pristine site
# that's harmless; on any site that already has real Company/Fiscal Year
# records (any dev site that's been used at all) it throws a validation
# error on overlap. Found by calling this file's whitelisted setup method
# against the dev site (U3) -- the fresh test.localhost site never
# surfaced it because there was nothing yet to collide with.


def ensure_test_gender():
	"""Gender is normally seeded by the setup wizard, which a headless
	`bench new-site --install-app` never runs -- so a fresh site has zero
	Gender records even though Employee.gender is mandatory."""
	gender = frappe.db.get_value("Gender", {}, "name")
	if gender:
		return gender
	frappe.get_doc({"doctype": "Gender", "gender": "Other"}).insert(ignore_permissions=True)
	return "Other"


def ensure_test_company():
	"""ERPNext ships no Company until the setup wizard runs on a fresh
	site. Create the one company these fixtures need if it's missing.

	Company.create_default_warehouses() unconditionally creates a "Goods
	In Transit" warehouse tagged warehouse_type="Transit" -- also normally
	seeded by the setup wizard, also absent on a headless install."""
	if not frappe.db.exists("Warehouse Type", "Transit"):
		frappe.get_doc({"doctype": "Warehouse Type", "name": "Transit"}).insert(
			ignore_permissions=True
		)
	if not frappe.db.exists("Company", TEST_COMPANY):
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": TEST_COMPANY,
				"abbr": "TC",
				"default_currency": "USD",
				"country": "United States",
			}
		).insert(ignore_permissions=True)
	return TEST_COMPANY


def make_test_user(user, company, **employee_fields):
	"""Create (or reuse) a User with password login and an Employee record
	for it, with create_user_permission=1 -- the User Permission that
	scopes the user to their own Employee is the entire portal
	authorization story (brief D4, plan KTD5)."""
	if not frappe.db.exists("User", user):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": user,
				"first_name": user.split("@")[0],
				"new_password": TEST_PASSWORD,
				"send_welcome_email": 0,
				"roles": [{"doctype": "Has Role", "role": "Employee"}],
			}
		).insert(ignore_permissions=True)

	employee_name = frappe.db.get_value("Employee", {"user_id": user})
	if employee_name:
		return employee_name

	employee = frappe.get_doc(
		{
			"doctype": "Employee",
			"employee_number": user.split("@")[0],
			"first_name": user.split("@")[0],
			"company": company,
			"user_id": user,
			"date_of_birth": "1990-01-01",
			"date_of_joining": "2020-01-01",
			"gender": ensure_test_gender(),
			"status": "Active",
			"create_user_permission": 1,
			**employee_fields,
		}
	)
	employee.insert(ignore_permissions=True)
	return employee.name


def make_test_employee_and_manager():
	"""
	Create (or reuse) two test users with real Employee records: an
	employee and their manager (reports_to). Returns
	(employee_name, employee_user, manager_name, manager_user).
	"""
	company = ensure_test_company()

	manager_name = make_test_user(MANAGER_USER, company)
	employee_name = make_test_user(EMPLOYEE_USER, company, reports_to=manager_name)

	assert_has_employee_user_permission(MANAGER_USER, manager_name)
	assert_has_employee_user_permission(EMPLOYEE_USER, employee_name)

	return employee_name, EMPLOYEE_USER, manager_name, MANAGER_USER


def make_test_user_without_employee():
	"""A logged-in user with no active Employee -- for the R3 "not linked"
	page. Password login only (this fixture is for local/CI, no Entra)."""
	if not frappe.db.exists("User", ORPHAN_USER):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": ORPHAN_USER,
				"first_name": "No Employee",
				"new_password": TEST_PASSWORD,
				"send_welcome_email": 0,
				"roles": [{"doctype": "Has Role", "role": "Employee"}],
			}
		).insert(ignore_permissions=True)
	return ORPHAN_USER


@frappe.whitelist()
def setup_playwright_fixtures():
	"""Whitelisted so Playwright's Node-side setup project can create the
	fixtures over HTTP; gated on the same allow_tests config bench
	run-tests itself requires, so it can never do anything on a real
	site."""
	if not frappe.conf.get("allow_tests"):
		frappe.throw("Test fixtures are disabled on this site (allow_tests is off).")
	make_test_employee_and_manager()
	make_test_user_without_employee()
	frappe.db.commit()  # nosemgrep


def assert_has_employee_user_permission(user, employee_name):
	exists = frappe.db.exists(
		"User Permission", {"user": user, "allow": "Employee", "for_value": employee_name}
	)
	if not exists:
		frappe.throw(
			f"Expected a User Permission scoping {user} to Employee {employee_name} "
			"(set by Employee's create_user_permission checkbox). Without it, every "
			"portal authorization assumption in this app (brief D4, plan KTD5) is "
			"false for this test user -- fix the fixture, don't skip this check."
		)
