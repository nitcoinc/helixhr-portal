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


def ensure_holiday_list_assignment(company):
	"""A headless install has no Holiday List either, and
	hrms.utils.holiday_list.get_holiday_list_for_employee -- called by
	Leave Application's submit path -- throws without one covering the
	current date for the employee or their company. Only needed by tests
	that actually submit (approve) a Leave Application; withdraw/insert
	alone don't reach this check."""
	from frappe.utils import get_year_ending, get_year_start, today

	list_name = "_Test Holiday List"
	if not frappe.db.exists("Holiday List", list_name):
		frappe.get_doc(
			{
				"doctype": "Holiday List",
				"holiday_list_name": list_name,
				"from_date": get_year_start(today()),
				"to_date": get_year_ending(today()),
			}
		).insert(ignore_permissions=True)

	existing = frappe.db.exists(
		"Holiday List Assignment", {"assigned_to": company, "holiday_list": list_name, "docstatus": 1}
	)
	if existing:
		return list_name

	assignment = frappe.get_doc(
		{
			"doctype": "Holiday List Assignment",
			"applicable_for": "Company",
			"assigned_to": company,
			"holiday_list": list_name,
			"from_date": get_year_start(today()),
			"to_date": get_year_ending(today()),
		}
	)
	assignment.insert(ignore_permissions=True)
	assignment.submit()
	return list_name


def make_test_user(user, company, **employee_fields):
	"""Create (or reuse) a User with password login and an Employee record
	for it, with create_user_permission=1 -- the User Permission that
	scopes the user to their own Employee is the entire portal
	authorization story (brief D4, plan KTD5).

	`employee_fields` are applied even to an already-existing Employee
	(diffed and saved only if changed) so a field added here later also
	lands on a fixture created by an earlier unit's test run, on a real
	bench where these persist across runs. Idempotent either way."""
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

	desired_fields = {"first_name": user.split("@")[0].capitalize(), **employee_fields}

	existing_name = frappe.db.get_value("Employee", {"user_id": user}, "name")
	if existing_name:
		employee = frappe.get_doc("Employee", existing_name)
		changed = False
		for field, value in desired_fields.items():
			if employee.get(field) != value:
				employee.set(field, value)
				changed = True
		if changed:
			employee.save(ignore_permissions=True)
		return employee.name

	employee = frappe.get_doc(
		{
			"doctype": "Employee",
			"employee_number": user.split("@")[0],
			"company": company,
			"user_id": user,
			"date_of_birth": "1990-01-01",
			"date_of_joining": "2020-01-01",
			"gender": ensure_test_gender(),
			"status": "Active",
			"create_user_permission": 1,
			**desired_fields,
		}
	)
	employee.insert(ignore_permissions=True)
	return employee.name


def make_test_employee_and_manager():
	"""
	Create (or reuse) two test users with real Employee records: an
	employee and their manager (reports_to). Returns
	(employee_name, employee_user, manager_name, manager_user).

	Deliberately does not set designation/department: both are optional
	everywhere they're shown (Dashboard.vue only renders them when
	present) and creating fresh Designation/Department master data --
	the first-ever row in either table on this bench -- reproducibly hit
	a MariaDB lock-wait timeout in this fixture's setUp, every time, even
	starting from a confirmed-empty lock table. Root cause not found (not
	a leftover transaction -- checked); not worth fixture-blocking on
	further. A real site's real HR data won't hit an empty-table insert
	like this, since Designation/Department are seeded by the setup
	wizard R6 already assumes.
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
	employee_name, _, _, _ = make_test_employee_and_manager()
	make_test_user_without_employee()

	# So leave.spec.ts can apply for leave and see a real, non-error
	# "Waiting for ..." status rather than hedging on whichever plain
	# error a leave-less fixture happens to hit.
	frappe.db.set_value("Employee", employee_name, "leave_approver", MANAGER_USER)
	company = frappe.db.get_value("Employee", employee_name, "company")
	ensure_leave_allocation(employee_name, "Casual Leave", 5)
	ensure_holiday_list_assignment(company)

	frappe.db.commit()  # nosemgrep


def ensure_leave_allocation(employee, leave_type, leaves):
	"""A submitted Leave Allocation covering the current year -- Leave
	Application only counts an allocation toward balance once it's
	docstatus 1 (hrms.hr.doctype.leave_application.leave_application.
	get_allocation_based_on_application_dates filters on docstatus == 1),
	so plain insert() alone leaves every application "outside leave
	allocation period" even with a matching date range."""
	from frappe.utils import get_year_ending, get_year_start, today

	company = frappe.db.get_value("Employee", employee, "company")
	existing = frappe.db.exists(
		"Leave Allocation", {"employee": employee, "leave_type": leave_type, "docstatus": 1}
	)
	if existing:
		return existing

	allocation = frappe.get_doc(
		{
			"doctype": "Leave Allocation",
			"employee": employee,
			"leave_type": leave_type,
			"from_date": get_year_start(today()),
			"to_date": get_year_ending(today()),
			"new_leaves_allocated": leaves,
			"company": company,
		}
	)
	allocation.insert(ignore_permissions=True)
	allocation.submit()
	return allocation.name


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
