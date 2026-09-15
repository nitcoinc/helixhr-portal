from datetime import date

import frappe

TEST_COMPANY = "_Test Company"
MANAGER_USER = "manager@helixhr.test"
EMPLOYEE_USER = "employee@helixhr.test"
ORPHAN_USER = "no-employee@helixhr.test"
# P3-U5: the HR step of the attendance-request workflow (P3-KTD6) and a
# second manager the fixture employee does not report to (P3-AE8).
HR_MANAGER_USER = "hr-manager@helixhr.test"
# P4-KTD8/P4-R10: an HR Manager who is *also* an active employee, which is
# what the portal landing rule and every portal read require. Deliberately a
# second identity: HR_MANAGER_USER has no Employee record on purpose and must
# keep not having one.
HR_MANAGER_EMPLOYEE_USER = "hr-manager-employee@helixhr.test"
IT_TEAM_USER = "it-team@helixhr.test"
OTHER_MANAGER_USER = "other-manager@helixhr.test"
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


def ensure_hr_manager_user():
	"""A login holding HR Manager and nothing else -- no Employee record, so
	it reaches every Attendance Request through the role alone, the way HR
	confirms in Desk (P3-KTD7). Idempotent."""
	if not frappe.db.exists("User", HR_MANAGER_USER):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": HR_MANAGER_USER,
				"first_name": "HR",
				"last_name": "Manager",
				"new_password": TEST_PASSWORD,
				"send_welcome_email": 0,
				"roles": [{"doctype": "Has Role", "role": "HR Manager"}],
			}
		).insert(ignore_permissions=True)
	return HR_MANAGER_USER


def make_test_hr_manager_employee():
	"""An HR Manager with an Active Employee record, so they can open the
	portal and work the HR queue (P4-R10, P4-R11, P4-KTD8).

	Separate from `ensure_hr_manager_user`, which holds the role and *no*
	Employee on purpose -- `portal_home_page` refuses it and every portal
	method that starts from `get_current_employee` throws for it, so it can
	prove the role alone reaches a record in Desk and nothing else.

	Returns (employee_name, user).
	"""
	company = ensure_test_company()
	# `create_user_permission=0`, unlike every other fixture identity here,
	# and it is a precondition of the HR queue rather than a convenience: a
	# self-scoping User Permission on Employee beats HR Manager's own read
	# permission on Leave Application, Timesheet and Attendance Request, so
	# an HR Manager who has one can see nothing but their own records and has
	# no queue at all. HR staff are not scoped to themselves on a real site
	# for the same reason. See docs/deployment.md.
	employee_name = make_test_user(
		HR_MANAGER_EMPLOYEE_USER, company, create_user_permission=0
	)

	user = frappe.get_doc("User", HR_MANAGER_EMPLOYEE_USER)
	if "HR Manager" not in [row.role for row in user.roles]:
		user.append_roles("HR Manager")
		user.save(ignore_permissions=True)
		frappe.clear_cache(user=HR_MANAGER_EMPLOYEE_USER)

	return employee_name, HR_MANAGER_EMPLOYEE_USER


def make_test_it_user():
	"""An IT Team portal user with an Employee record but no Desk role (P5-U2)."""
	company = ensure_test_company()
	employee_name = make_test_user(IT_TEAM_USER, company)
	user = frappe.get_doc("User", IT_TEAM_USER)
	roles = [row.role for row in user.roles if row.role != "Employee"]
	if "IT Team" not in roles:
		roles.append("IT Team")
	if roles != [row.role for row in user.roles]:
		user.set("roles", [{"role": role} for role in roles])
		user.save(ignore_permissions=True)
		frappe.clear_cache(user=IT_TEAM_USER)
	return employee_name, IT_TEAM_USER


TEST_EMAIL_ACCOUNT = "_Test HelixHR Outgoing"


def ensure_test_email_account():
	"""A default outgoing Email Account, because several writes now send mail
	*inside the save* (P4-R12, P4-KTD9).

	An Email-channel Notification calls `frappe.sendmail` and
	`Communication.get_outgoing_email_account` from `on_change`, and both
	throw with no default outgoing account -- so on a site without one every
	Send to HR would fail, and the failure would look like the escalation
	being refused. The queue rows are what tests assert on; nothing is ever
	delivered, because the SMTP host below does not exist and
	`frappe.sendmail` only enqueues.

	`frappe.flags.in_patch` is Frappe's own escape hatch from the SMTP
	connection check in `email_account.validate` (which also exempts
	`frappe.in_test`, so this only matters on the Playwright route, where
	the fixtures are created over HTTP). Idempotent.
	"""
	existing = frappe.db.get_value(
		"Email Account", {"enable_outgoing": 1, "default_outgoing": 1}, "name"
	)
	if existing:
		return existing

	was_in_patch = frappe.flags.in_patch
	frappe.flags.in_patch = True
	try:
		account = frappe.get_doc(
			{
				"doctype": "Email Account",
				"email_account_name": TEST_EMAIL_ACCOUNT,
				"email_id": "portal-tests@helixhr.test",
				"enable_incoming": 0,
				"enable_outgoing": 1,
				"default_outgoing": 1,
				"smtp_server": "localhost",
				"smtp_port": 1025,
				"awaiting_password": 0,
				"password": TEST_PASSWORD,
			}
		)
		account.insert(ignore_permissions=True)
	finally:
		frappe.flags.in_patch = was_in_patch
	return account.name


def ensure_holiday_list_assignment_from(company, from_date):
	"""The shared company assignment, plus one that actually covers
	`from_date`.

	P3-U9: the attendance-request preview resolves the holiday list *per date*
	through Holiday List Assignment (`_holiday_list_spans`) instead of once as
	of today, which is what makes a range straddling an assignment change read
	the right list. A suite that books a past-year window therefore needs an
	assignment covering that window, or the preview correctly answers "cannot
	tell". Resolution takes the latest `from_date <= as_on`, so this extra row
	never changes what resolves for today.

	Idempotent, and committed by the callers' own fixtures: an assignment for
	the same company and `from_date` is a duplicate HRMS refuses.
	"""
	from frappe.utils import getdate

	list_name = ensure_holiday_list_assignment(company)
	holiday_list = frappe.get_doc("Holiday List", list_name)
	if getdate(from_date) < getdate(holiday_list.from_date):
		# The assignment's start must fall inside its list's own dates.
		holiday_list.from_date = str(from_date)
		holiday_list.save(ignore_permissions=True)

	if frappe.db.exists(
		"Holiday List Assignment",
		{
			"assigned_to": company,
			"holiday_list": list_name,
			"from_date": str(from_date),
			"docstatus": 1,
		},
	):
		return list_name

	assignment = frappe.get_doc(
		{
			"doctype": "Holiday List Assignment",
			"applicable_for": "Company",
			"assigned_to": company,
			"holiday_list": list_name,
			"from_date": str(from_date),
		}
	)
	assignment.insert(ignore_permissions=True)
	assignment.submit()
	return list_name


def ensure_test_holiday(holiday_date, weekly_off=False):
	"""One Holiday row on `_Test Holiday List` for `holiday_date`, widening
	the list's own date range backwards when the date falls before it.

	`erpnext...employee.is_holiday` resolves the list as of *today* and then
	looks the given date up among that list's rows, so a past-year row on
	the current list is how a test puts a holiday under a past-year request
	without touching the assignment every other suite relies on (P3-U5).
	"""
	from frappe.utils import getdate

	list_name = "_Test Holiday List"
	holiday_list = frappe.get_doc("Holiday List", list_name)
	if getdate(holiday_date) < getdate(holiday_list.from_date):
		holiday_list.from_date = holiday_date
	for row in holiday_list.holidays:
		if getdate(row.holiday_date) == getdate(holiday_date):
			return list_name
	holiday_list.append(
		"holidays",
		{
			"holiday_date": holiday_date,
			"description": "Weekly Off" if weekly_off else "_Test Holiday",
			"weekly_off": 1 if weekly_off else 0,
		},
	)
	holiday_list.save(ignore_permissions=True)
	return list_name


# P3-U4: a test site has no Shift Type at all, and the portal offers a punch
# only inside the window HRMS resolves for the moment of the punch (P3-KTD5),
# so every check-in scenario needs one of these plus an assignment.
PORTAL_SHIFT_TYPE = "_Test Portal Shift"
NIGHT_SHIFT_TYPE = "_Test Portal Night Shift"


def ensure_test_shift_type(
	name=PORTAL_SHIFT_TYPE,
	start_time="00:00:00",
	end_time="23:59:00",
	begin_before=0,
	allow_after=0,
):
	"""A Shift Type with auto attendance whose window covers the whole site
	day by default, so `get_actual_start_end_datetime_of_shift` resolves
	whatever time of day the suite runs at (P3-U4). The default grace periods
	are zero because HRMS refuses a window that, grace included, overlaps
	itself across midnight.

	The window is rewritten on reuse: a test that wants a narrow or a night
	window asks for one by name, and a stale window left over from an earlier
	run would silently decide the answer.
	"""
	fields = {
		"start_time": start_time,
		"end_time": end_time,
		"begin_check_in_before_shift_start_time": begin_before,
		"allow_check_out_after_shift_end_time": allow_after,
		"enable_auto_attendance": 1,
		# The portal derives IN/OUT itself and always sends a log type, so
		# either option works; alternating is HRMS's own default.
		"determine_check_in_and_check_out": "Alternating entries as IN and OUT during the same shift",
		"working_hours_calculation_based_on": "First Check-in and Last Check-out",
		"process_attendance_after": str(frappe.utils.add_days(frappe.utils.today(), -365)),
		"auto_update_last_sync": 1,
	}
	if frappe.db.exists("Shift Type", name):
		doc = frappe.get_doc("Shift Type", name)
		doc.update(fields)
		doc.save(ignore_permissions=True)
		return name
	doc = frappe.get_doc({"doctype": "Shift Type", "__newname": name, **fields})
	doc.insert(ignore_permissions=True)
	return doc.name


def assign_test_shift(employee, shift_type=PORTAL_SHIFT_TYPE, start_date=None, shift_location=None):
	"""One submitted, Active, open-ended Shift Assignment for `employee`.

	Every other assignment of theirs is removed first: HRMS refuses
	overlapping assignments, so a test that wants a different window cannot
	add one alongside the last test's. The rows are deleted at the table
	(they are submitted documents, and nothing links to them).
	"""
	frappe.db.delete("Shift Assignment", {"employee": employee})
	doc = frappe.get_doc(
		{
			"doctype": "Shift Assignment",
			"employee": employee,
			"shift_type": shift_type,
			"company": frappe.db.get_value("Employee", employee, "company"),
			"start_date": start_date or str(frappe.utils.add_days(frappe.utils.today(), -30)),
			"status": "Active",
			"shift_location": shift_location,
		}
	)
	doc.insert(ignore_permissions=True)
	doc.submit()
	return doc.name


def clear_test_shifts(employee):
	"""Back to a site with no shift at all -- the state P3-R8's "check-in is
	not set up" copy describes."""
	frappe.db.delete("Shift Assignment", {"employee": employee})
	frappe.db.set_value("Employee", employee, "default_shift", None)


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

	# P3-U4 scenario 8: the check-in button exists only while HR Settings
	# allows mobile check-in and HRMS resolves a shift window for now
	# (P3-KTD5), so `checkin.spec.ts` needs both. The seeded window covers
	# the whole site day, which is what makes the spec runnable at any hour.
	frappe.db.set_single_value("HR Settings", "allow_employee_checkin_from_mobile_app", 1)
	ensure_test_shift_type()
	assign_test_shift(employee_name)

	# P3-U3 scenario 5: `holidays.spec.ts` needs one holiday it can name, on
	# a date it can compute from the site's own today (`playwright_holiday_date`).
	ensure_test_holiday(playwright_holiday_date())

	# P3-U2 scenario 6: one submitted payslip, for last month, so
	# `payslips.spec.ts` has a real row to open and a real PDF to fetch
	# whatever day the suite runs on.
	period_start = frappe.utils.get_first_day(frappe.utils.add_months(frappe.utils.today(), -1))
	make_test_salary_slip(
		employee_name, str(period_start), str(frappe.utils.get_last_day(period_start))
	)

	# P3-U8 scenario 5: `directory.spec.ts` needs colleagues to find and a
	# published work email on the manager fixture, so the person sheet has a
	# real mailto: action (`ensure_directory_fixtures`).
	ensure_directory_fixtures()

	# P4-U3: a default outgoing Email Account, because the HR-queue
	# Notifications are Email-channel and throw inside the save without one
	# (P4-KTD9), and an HR Manager who is also an employee, which is the
	# third identity the approvals specs sign in as (P4-KTD8).
	ensure_test_email_account()
	make_test_hr_manager_employee()

	# P4-U5 scenario 6: `login-dashboard.spec.ts` needs one colleague with a
	# birthday in the month the run happens in, so Home's celebrations card
	# has a name and a day on it.
	ensure_celebration_fixtures()

	frappe.db.commit()  # nosemgrep


def ensure_leave_approver_role(user):
	"""HRMS auto-grants the "Leave Approver" role (needed to write
	Leave Application.status) via Employee's own on_update hook
	(hrms.overrides.employee_master.update_approver_role) whenever
	Employee.leave_approver is set through a real save() -- but this
	suite's fixtures set that field with frappe.db.set_value for speed,
	which is a raw SQL write and never fires that hook. Grant the role
	directly instead of routing every fixture through a real Employee
	save just for this side effect."""
	user_doc = frappe.get_doc("User", user)
	if "Leave Approver" not in [r.role for r in user_doc.roles]:
		user_doc.append_roles("Leave Approver")
		user_doc.save(ignore_permissions=True)


def ensure_leave_allocation(employee, leave_type, leaves):
	"""A submitted Leave Allocation covering this year and the next -- Leave
	Application only counts an allocation toward balance once it's
	docstatus 1 (hrms.hr.doctype.leave_application.leave_application.
	get_allocation_based_on_application_dates filters on docstatus == 1),
	so plain insert() alone leaves every application "outside leave
	allocation period" even with a matching date range.

	It ends with *next* year, not this one, because the suites book leave at
	fixed offsets from today -- up to 114 days out -- and a single calendar
	year silently stops covering them as the year runs down. That is a clock,
	not a code change: the same tests passed in CI on 6 September 2026 and
	errored on the 9th, when offset 114 first crossed into January.
	"""
	from frappe.utils import add_days, get_year_ending, get_year_start, today

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
			"to_date": get_year_ending(add_days(today(), 365)),
			"new_leaves_allocated": leaves,
			"company": company,
		}
	)
	allocation.insert(ignore_permissions=True)
	allocation.submit()
	return allocation.name


# P3-U2: payslips. A fresh site has no payroll master data at all, so every
# payslip scenario needs a Salary Structure with one earning and one
# deduction, a submitted Salary Structure Assignment, and a submitted Salary
# Slip. Deliberately not HRMS's own `make_salary_structure`: that helper
# rebuilds tax slabs, payroll periods and benefit components on every call,
# deletes the employee's other assignments (this suite needs two, one per
# currency) and picks a random Account, none of which a payslip *read* test
# needs.
PAYSLIP_COMPONENTS = {
	"Earning": ("_Test Portal Basic", "TPB"),
	"Deduction": ("_Test Portal Levy", "TPL"),
}

# Per currency: what one seeded slip pays. The two currencies carry
# deliberately different figures so a test can tell which row it is reading
# (P3-AE2) without depending on the currency symbol alone.
PAYSLIP_AMOUNTS = {"USD": (5000.0, 500.0), "INR": (90000.0, 9000.0)}


def _ensure_salary_component(component_type):
	name, abbr = PAYSLIP_COMPONENTS[component_type]
	if not frappe.db.exists("Salary Component", name):
		frappe.get_doc(
			{
				"doctype": "Salary Component",
				"salary_component": name,
				"salary_component_abbr": abbr,
				"type": component_type,
			}
		).insert(ignore_permissions=True)
	return name


def ensure_test_fiscal_year(any_date):
	"""The calendar-year Fiscal Year containing `any_date`.

	A headless site has none (the setup wizard creates them), and Salary
	Structure Assignment resolves the payroll cycle through
	`erpnext...get_fiscal_year`, which throws without one -- and throws
	*obscurely*, because formatting its own error message trips a Frappe
	locale bug on a site with no date format default.
	"""
	from frappe.utils import getdate

	date = getdate(any_date)
	existing = frappe.db.get_value(
		"Fiscal Year",
		{"year_start_date": ("<=", date), "year_end_date": (">=", date)},
		"name",
	)
	if existing:
		return existing
	doc = frappe.get_doc(
		{
			"doctype": "Fiscal Year",
			"year": str(date.year),
			"year_start_date": f"{date.year}-01-01",
			"year_end_date": f"{date.year}-12-31",
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def ensure_test_salary_structure(currency="USD", company=None):
	"""One submitted Salary Structure per currency, amounts fixed rather
	than formula-driven so the seeded slip's gross, deductions and net are
	known numbers. Idempotent."""
	company = company or ensure_test_company()
	name = f"_Test Portal Salary {currency}"
	if frappe.db.exists("Salary Structure", name):
		return name
	gross, deduction = PAYSLIP_AMOUNTS[currency]
	doc = frappe.get_doc(
		{
			"doctype": "Salary Structure",
			"__newname": name,
			"company": company,
			"currency": currency,
			"payroll_frequency": "Monthly",
			"earnings": [{"salary_component": _ensure_salary_component("Earning"), "amount": gross}],
			"deductions": [
				{"salary_component": _ensure_salary_component("Deduction"), "amount": deduction}
			],
		}
	)
	doc.insert(ignore_permissions=True)
	doc.submit()
	return name


def make_test_salary_slip(
	employee,
	start_date,
	end_date,
	currency="USD",
	docstatus=1,
	withheld=False,
	amended_from=None,
):
	"""A Salary Slip for `employee` covering `start_date`..`end_date`, in
	`currency`, at `docstatus` (0 draft, 1 submitted, 2 cancelled).

	Idempotent on the period: HRMS's own `check_existing` refuses a second
	non-cancelled slip for a period, so a re-run reuses the row it finds.

	`withheld` writes the status straight to the row rather than building a
	Salary Withholding: the portal reads `status`, and a real withholding
	cycle is a payroll-entry apparatus that no payslip *read* depends on.
	"""
	from frappe.utils import getdate

	company = frappe.db.get_value("Employee", employee, "company")
	ensure_holiday_list_assignment(company)
	ensure_test_fiscal_year(start_date)
	ensure_test_fiscal_year(end_date)
	structure = ensure_test_salary_structure(currency, company)

	assignment_from = getdate(start_date)
	if not frappe.db.exists(
		"Salary Structure Assignment",
		{
			"employee": employee,
			"salary_structure": structure,
			"from_date": assignment_from,
			"docstatus": 1,
		},
	):
		assignment = frappe.get_doc(
			{
				"doctype": "Salary Structure Assignment",
				"employee": employee,
				"salary_structure": structure,
				"from_date": assignment_from,
				"company": company,
				"currency": currency,
				"base": PAYSLIP_AMOUNTS[currency][0],
			}
		)
		assignment.insert(ignore_permissions=True)
		assignment.submit()

	existing = frappe.db.get_value(
		"Salary Slip",
		{"employee": employee, "start_date": start_date, "docstatus": docstatus},
		"name",
	)
	if existing:
		if withheld:
			frappe.db.set_value("Salary Slip", existing, "status", "Withheld")
		return existing

	gross, deduction = PAYSLIP_AMOUNTS[currency]
	slip = frappe.get_doc(
		{
			"doctype": "Salary Slip",
			"employee": employee,
			"company": company,
			"salary_structure": structure,
			"payroll_frequency": "Monthly",
			"currency": currency,
			"start_date": start_date,
			"end_date": end_date,
			"posting_date": end_date,
			"amended_from": amended_from,
			"earnings": [{"salary_component": PAYSLIP_COMPONENTS["Earning"][0], "amount": gross}],
			"deductions": [
				{"salary_component": PAYSLIP_COMPONENTS["Deduction"][0], "amount": deduction}
			],
		}
	)
	slip.insert(ignore_permissions=True)
	if docstatus >= 1:
		slip.submit()
	if docstatus == 2:
		slip.cancel()
	if withheld:
		frappe.db.set_value("Salary Slip", slip.name, "status", "Withheld")
	return slip.name


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


# --------------------------------------------------------------------------
# P2-U0: reproducible baseline seed profile
#
# Two orders of magnitude above setup_playwright_fixtures, so the
# performance harness (frontend/tests/e2e/performance.spec.ts) measures
# bounded-vs-unbounded queries against realistic row counts instead of the
# 3-row happy path. Staging/local only: every entry point is gated on the
# same `allow_tests` config bench run-tests needs, exactly like
# setup_playwright_fixtures, and it is deliberately NOT part of the CI test
# job -- CI stays fast and fixture-clean.
#
# Determinism (P2-U0 test scenario 1): every seeded row has a deterministic
# name (bulk rows) or is recorded in a ledger stored in the site's global
# defaults (full documents), and every date is derived from an anchor date
# frozen on the first run. A second run therefore inserts nothing new and
# reports identical counts, whatever day it runs on.
# --------------------------------------------------------------------------

BASELINE_TAG = "P2U0"
BASELINE_LEDGER_KEY = "helixhr_baseline_records"
BASELINE_COMPANY_B = "_Test Company B"
BASELINE_LEAVE_TYPE = "Privilege Leave"
BASELINE_PROJECT = "_Test Baseline Project"

# The frozen cardinalities. The harness reads these back over
# baseline_fixture_counts() and invalidates a run that does not match, so a
# measurement can never be quietly taken against a smaller dataset.
BASELINE_PROFILE = {
	"employees": 200,
	"attendance": 365,
	"checkins": 260,
	"timesheets": 52,
	"leave_applications": 40,
	"hr_requests": 100,
	"document_links": 75,
	"notification_logs": 250,
	"manager_reports": 20,
	"pending_approvals": 25,
}
# 12 timesheets + 13 leave applications = the 25 mixed pending approvals.
BASELINE_PENDING_TIMESHEETS = 12
BASELINE_PENDING_LEAVE = 13
BASELINE_UNREAD_NOTIFICATIONS = 50


def _require_allow_tests():
	if not frappe.conf.get("allow_tests"):
		frappe.throw("Baseline fixtures are disabled on this site (allow_tests is off).")


def _load_ledger():
	import json

	raw = frappe.db.get_global(BASELINE_LEDGER_KEY)
	return json.loads(raw) if raw else {}


def _save_ledger(ledger):
	import json

	frappe.db.set_global(BASELINE_LEDGER_KEY, json.dumps(ledger))


def _baseline_anchor(ledger):
	"""The seed's frozen "today". Stored on the first run so a rerun a week
	later still produces the same 365 attendance dates and the same 52
	timesheet weeks -- otherwise "the same profile" would silently drift by
	a day per day and the before/after comparison would not be like for
	like."""
	from frappe.utils import getdate, today

	if not ledger.get("anchor_date"):
		ledger["anchor_date"] = str(getdate(today()))
	return getdate(ledger["anchor_date"])


def _seed_bulk(doctype, prefix, fields, rows, docstatus=0):
	"""frappe.db.bulk_insert for rows whose controllers have no side effect
	worth running (P2-U0 approach step 1). Deterministic `<prefix>-<n>`
	names plus ignore_duplicates make a rerun a no-op rather than a
	doubling."""
	now = frappe.utils.now()
	columns = ["name", "creation", "modified", "modified_by", "owner", "docstatus", *fields]
	values = [
		(f"{prefix}-{index:04d}", now, now, "Administrator", "Administrator", docstatus, *row)
		for index, row in enumerate(rows)
	]
	frappe.db.bulk_insert(doctype, columns, values, ignore_duplicates=True)
	return frappe.db.count(doctype, {"name": ["like", f"{prefix}-%"]})


def ensure_baseline_company():
	if not frappe.db.exists("Company", BASELINE_COMPANY_B):
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": BASELINE_COMPANY_B,
				"abbr": "TCB",
				"default_currency": "USD",
				"country": "United States",
			}
		).insert(ignore_permissions=True)
	return BASELINE_COMPANY_B


def ensure_baseline_project(company, users):
	"""One Open Project the seeded timesheets book against. A User
	Permission, not a Project Users row, grants access -- appending to
	Project.users sends a collaboration invitation email, which throws on a
	site with no outgoing Email Account (same reason test_api_timesheet.py
	does it this way)."""
	name = frappe.db.get_value("Project", {"project_name": BASELINE_PROJECT}, "name")
	if not name:
		doc = frappe.get_doc(
			{
				"doctype": "Project",
				"project_name": BASELINE_PROJECT,
				"status": "Open",
				"company": company,
			}
		)
		doc.insert(ignore_permissions=True)
		name = doc.name

	for user in users:
		if not frappe.db.exists("User Permission", {"user": user, "allow": "Project", "for_value": name}):
			frappe.get_doc(
				{"doctype": "User Permission", "user": user, "allow": "Project", "for_value": name}
			).insert(ignore_permissions=True)
	return name


def _seed_employees(company_a, company_b, manager_name):
	"""200 Employees, split across the two companies, the first 20 of them
	reporting to the fixture manager. Full inserts (not bulk): Employee's
	own controller owns naming, and a hand-written row would be a different
	shape from every real Employee the portal reads.

	No `user_id`, so no User, no login and no User Permission -- these rows
	exist to give list queries, company scoping and manager lookups real
	volume, not to be signed in as."""
	created = 0
	for index in range(BASELINE_PROFILE["employees"]):
		number = f"{BASELINE_TAG}-EMP-{index:03d}"
		if frappe.db.exists("Employee", {"employee_number": number}):
			continue
		employee = frappe.get_doc(
			{
				"doctype": "Employee",
				"employee_number": number,
				"first_name": f"Baseline{index:03d}",
				"company": company_a if index % 2 == 0 else company_b,
				"date_of_birth": "1990-01-01",
				"date_of_joining": "2020-01-01",
				"gender": ensure_test_gender(),
				"status": "Active",
				"reports_to": manager_name if index < BASELINE_PROFILE["manager_reports"] else None,
			}
		)
		employee.insert(ignore_permissions=True)
		created += 1
	return created


def _seed_attendance(employee, employee_name, company, anchor):
	from frappe.utils import add_days

	rows = [
		(
			employee,
			employee_name,
			str(add_days(anchor, -(offset + 1))),
			"Present",
			company,
			1 if offset % 10 == 0 else 0,
		)
		for offset in range(BASELINE_PROFILE["attendance"])
	]
	# docstatus 1: only a submitted Attendance record counts anywhere in the
	# portal (get_my_attendance and the week spine both read submitted rows).
	return _seed_bulk(
		"Attendance",
		f"{BASELINE_TAG}-ATT",
		["employee", "employee_name", "attendance_date", "status", "company", "late_entry"],
		rows,
		docstatus=1,
	)


def _seed_checkins(employee, employee_name, anchor):
	from frappe.utils import add_days

	rows = []
	for index in range(BASELINE_PROFILE["checkins"]):
		day = add_days(anchor, -(index // 2 + 1))
		hour = "09:15:00" if index % 2 == 0 else "18:05:00"
		rows.append((employee, employee_name, f"{day} {hour}", "IN" if index % 2 == 0 else "OUT"))
	return _seed_bulk(
		"Employee Checkin",
		f"{BASELINE_TAG}-CHK",
		["employee", "employee_name", "time", "log_type"],
		rows,
	)


def _seed_notification_logs(user):
	rows = []
	for index in range(BASELINE_PROFILE["notification_logs"]):
		rows.append(
			(
				user,
				f"Baseline notification {index:03d}",
				"Alert",
				# The newest BASELINE_UNREAD_NOTIFICATIONS stay unread so the
				# shell badge and the unread poll have a realistic count to read.
				0 if index < BASELINE_UNREAD_NOTIFICATIONS else 1,
			)
		)
	return _seed_bulk(
		"Notification Log",
		f"{BASELINE_TAG}-NOTIF",
		["for_user", "subject", "type", "read"],
		rows,
	)


def _seed_document_links(company_a, company_b):
	rows = []
	for index in range(BASELINE_PROFILE["document_links"]):
		# Mixed visibility on purpose (P2-R19): a third global, a third for
		# each company, so a scoping regression shows up as a count change.
		company = (None, company_a, company_b)[index % 3]
		rows.append(
			(
				f"Baseline policy {index:03d}",
				f"https://example.invalid/policies/{index:03d}",
				company,
				"Seeded by the P2-U0 baseline profile.",
			)
		)
	return _seed_bulk(
		"HelixHR Document Link",
		f"{BASELINE_TAG}-DOC",
		["title", "url", "company", "description"],
		rows,
	)


def _seed_hr_requests(employee):
	categories = ("HR Letter", "IT / Asset", "Payroll Question", "Other")
	statuses = ("Open", "In Progress", "Done", "Rejected")
	rows = []
	for index in range(BASELINE_PROFILE["hr_requests"]):
		status = statuses[index % 4]
		rows.append(
			(
				"HR-REQ-.YYYY.-",
				employee,
				categories[index % 4],
				f"Baseline request {index:03d}",
				status,
				f"Seeded details for request {index:03d}.",
				# A closed request carrying a note is what the queue reads as
				# "HR replied" -- seed both kinds so that section is not empty.
				f"Seeded HR reply {index:03d}." if status in ("Done", "Rejected") else None,
			)
		)
	# Bulk, not insert(): HR Request.before_insert overwrites `employee` with
	# the *session* user's Employee, so a full insert run by Administrator
	# cannot seed rows for the fixture employee at all.
	return _seed_bulk(
		"HR Request",
		f"{BASELINE_TAG}-REQ",
		["naming_series", "employee", "category", "subject", "status", "details", "hr_note"],
		rows,
	)


def _seed_timesheets(employee, company, project, anchor, ledger):
	"""52 weeks of Timesheet, full documents: hours, day totals, the
	workflow state and the manager's DocShare are all controller/hook
	output that a hand-written row would not have (P2-U0 approach step 1).

	The most recent BASELINE_PENDING_TIMESHEETS weeks are moved to Pending
	Approval through a real save, so helixhr.events.timesheet_on_update
	creates the manager's DocShare exactly as a real submission would."""
	from frappe.utils import add_days, add_to_date, get_datetime

	from helixhr.utils import get_week_bounds

	names = ledger.setdefault("timesheets", [])
	existing_weeks = set(frappe.get_all("Timesheet", filters={"name": ["in", names]}, pluck="start_date"))
	for index in range(BASELINE_PROFILE["timesheets"]):
		# index 0 is last week, not this week: leaving the current week free
		# keeps the Timesheet page's own "start this week" path usable.
		monday, sunday = get_week_bounds(add_days(anchor, -7 * (index + 1)))
		if monday in existing_weeks or str(monday) in {str(week) for week in existing_weeks}:
			continue

		doc = frappe.new_doc("Timesheet")
		doc.employee = employee
		doc.company = company
		doc.start_date = str(monday)
		doc.end_date = str(sunday)
		for day in range(5):  # Monday..Friday, 8h
			start = get_datetime(f"{add_days(monday, day)} 09:00:00")
			doc.append(
				"time_logs",
				{
					"project": project,
					"hours": 8,
					"description": f"Baseline week {index:02d} day {day}",
					"activity_type": "General",
					"from_time": start,
					"to_time": add_to_date(start, hours=8),
				},
			)
		doc.insert(ignore_permissions=True)
		if index < BASELINE_PENDING_TIMESHEETS:
			doc.workflow_state = "Pending Approval"
			doc.save(ignore_permissions=True)
		names.append(doc.name)
	return len(names)


def _seed_leave_applications(employee, approver_user, anchor, ledger):
	"""40 Leave Applications, full documents: balance, allocation period and
	non-overlap are HRMS validations, and the approved ones must reach
	docstatus 1 so a Leave Ledger Entry exists (P2-U0 approach step 1).

	Single days two days apart so no pair overlaps, all inside the current
	allocation period."""
	from frappe.utils import add_days

	names = ledger.setdefault("leave_applications", [])
	if len(names) >= BASELINE_PROFILE["leave_applications"]:
		return len(names)

	ensure_leave_allocation(employee, BASELINE_LEAVE_TYPE, 60)
	ensure_leave_approver_role(approver_user)

	approved = BASELINE_PENDING_LEAVE  # index < this stays Open (the manager's queue)
	for index in range(len(names), BASELINE_PROFILE["leave_applications"]):
		day = str(add_days(anchor, 2 * index + 1))
		doc = frappe.get_doc(
			{
				"doctype": "Leave Application",
				"employee": employee,
				"leave_type": BASELINE_LEAVE_TYPE,
				"from_date": day,
				"to_date": day,
				"description": f"Baseline leave {index:03d}",
				"leave_approver": approver_user,
			}
		)
		doc.insert(ignore_permissions=True)

		if approved <= index < approved + 8:
			# Approved *and* submitted, so balance and ledger are real.
			doc.status = "Approved"
			doc.submit()
		elif index >= approved + 8:
			# A rejected application stays unsubmitted and non-consuming.
			frappe.db.set_value("Leave Application", doc.name, "status", "Rejected")
		names.append(doc.name)
	return len(names)


@frappe.whitelist()
def setup_baseline_fixtures():
	"""Seed the frozen P2-U0 performance profile on this site. Idempotent:
	safe (and cheap) to call again, and a second call must report the same
	counts as the first."""
	_require_allow_tests()

	employee_name, employee_user, manager_name, manager_user = make_test_employee_and_manager()
	make_test_user_without_employee()
	company_a = frappe.db.get_value("Employee", employee_name, "company")
	company_b = ensure_baseline_company()
	ensure_holiday_list_assignment(company_a)
	ensure_leave_allocation(employee_name, "Casual Leave", 5)
	frappe.db.set_value("Employee", employee_name, "leave_approver", manager_user)

	ledger = _load_ledger()
	anchor = _baseline_anchor(ledger)
	employee_label = frappe.db.get_value("Employee", employee_name, "employee_name")
	project = ensure_baseline_project(company_a, [employee_user])

	_seed_employees(company_a, company_b, manager_name)
	_seed_attendance(employee_name, employee_label, company_a, anchor)
	_seed_checkins(employee_name, employee_label, anchor)
	_seed_notification_logs(employee_user)
	_seed_document_links(company_a, company_b)
	_seed_hr_requests(employee_name)
	_seed_timesheets(employee_name, company_a, project, anchor, ledger)
	_seed_leave_applications(employee_name, manager_user, anchor, ledger)

	_save_ledger(ledger)
	frappe.db.commit()  # nosemgrep
	return baseline_fixture_counts()


@frappe.whitelist()
def baseline_fixture_counts():
	"""What the harness asserts against BASELINE_PROFILE before it trusts a
	measurement (P2-U0 test scenario 1 and 3)."""
	_require_allow_tests()

	employee_name = frappe.db.get_value("Employee", {"user_id": EMPLOYEE_USER}, "name")
	manager_name = frappe.db.get_value("Employee", {"user_id": MANAGER_USER}, "name")
	ledger = _load_ledger()
	timesheets = ledger.get("timesheets", [])
	leaves = ledger.get("leave_applications", [])

	def tagged(doctype, prefix):
		return frappe.db.count(doctype, {"name": ["like", f"{BASELINE_TAG}-{prefix}-%"]})

	return {
		"anchor_date": ledger.get("anchor_date"),
		"expected": BASELINE_PROFILE,
		"actual": {
			"employees": frappe.db.count("Employee", {"employee_number": ["like", f"{BASELINE_TAG}-EMP-%"]}),
			"attendance": tagged("Attendance", "ATT"),
			"checkins": tagged("Employee Checkin", "CHK"),
			"timesheets": len(timesheets),
			"leave_applications": len(leaves),
			"hr_requests": tagged("HR Request", "REQ"),
			"document_links": tagged("HelixHR Document Link", "DOC"),
			"notification_logs": tagged("Notification Log", "NOTIF"),
			"manager_reports": frappe.db.count("Employee", {"reports_to": manager_name}) - 1,
			"pending_approvals": (
				frappe.db.count(
					"Timesheet", {"name": ["in", timesheets or [""]], "workflow_state": "Pending Approval"}
				)
				+ frappe.db.count("Leave Application", {"name": ["in", leaves or [""]], "status": "Open"})
			),
		},
		"employee": employee_name,
		"manager": manager_name,
	}


@frappe.whitelist()
def teardown_baseline_fixtures():
	"""Remove the baseline profile. A fresh site is still the honest reset
	(see docs/runbook.md); this exists so a long-lived local site can drop
	the bulk volume without losing its other fixtures.

	Submitted documents are cancelled before deletion so HRMS unwinds the
	Leave Ledger Entries it created -- deleting them any other way would
	leave orphan ledger rows behind and quietly corrupt the next run's
	balances."""
	_require_allow_tests()

	ledger = _load_ledger()
	for name in ledger.get("leave_applications", []):
		if not frappe.db.exists("Leave Application", name):
			continue
		doc = frappe.get_doc("Leave Application", name)
		if doc.docstatus == 1:
			doc.cancel()
		frappe.delete_doc("Leave Application", name, force=True, ignore_permissions=True)
	for name in ledger.get("timesheets", []):
		if not frappe.db.exists("Timesheet", name):
			continue
		doc = frappe.get_doc("Timesheet", name)
		if doc.docstatus == 1:
			doc.cancel()
		frappe.db.delete("DocShare", {"share_doctype": "Timesheet", "share_name": name})
		frappe.delete_doc("Timesheet", name, force=True, ignore_permissions=True)

	for doctype, prefix in (
		("Attendance", "ATT"),
		("Employee Checkin", "CHK"),
		("Notification Log", "NOTIF"),
		("HelixHR Document Link", "DOC"),
		("HR Request", "REQ"),
	):
		frappe.db.delete(doctype, {"name": ("like", f"{BASELINE_TAG}-{prefix}-%")})

	for name in frappe.get_all(
		"Employee", filters={"employee_number": ["like", f"{BASELINE_TAG}-EMP-%"]}, pluck="name"
	):
		frappe.db.set_value("Employee", name, "reports_to", None)
		frappe.delete_doc("Employee", name, force=True, ignore_permissions=True)

	frappe.db.set_global(BASELINE_LEDGER_KEY, None)
	frappe.db.commit()  # nosemgrep
	return {"removed": True}


def playwright_holiday_date():
	"""The date `holidays.spec.ts` expects a holiday on: twelve days ahead of
	the *site's* today, clamped inside the calendar year so it stays covered
	by the year-long `_Test Holiday List` and by the year the Holidays page
	opens on (P3-U3). The spec computes the same date from `siteToday()`, so
	this rule has to stay simple enough to restate in one line of TypeScript.
	"""
	from frappe.utils import add_days, get_year_ending, getdate, today

	return str(min(getdate(add_days(today(), 12)), getdate(get_year_ending(today()))))


# --- Directory fixtures (P3-U8) --------------------------------------------

DIRECTORY_DESIGNATION = "_Test Directory Role"
DIRECTORY_DEPARTMENT = "_Test Directory Department"
DIRECTORY_COLLEAGUE_EMAIL = "directory-colleague@helixhr.test"
# The manager fixture's published work email -- what `directory.spec.ts` opens
# the sheet for, and the only thing on that row that becomes a mailto: action
# (P3-R22).
DIRECTORY_MANAGER_EMAIL = "manager.work@helixhr.test"
_DIRECTORY_TAG = "P3U8-DIR"


def _ensure_directory_masters(company):
	"""The Designation and Department the directory searches by. Both are
	master data a real site gets from the setup wizard; a headless install has
	none, and `make_test_employee_and_manager` deliberately leaves both fields
	empty, so the search-by-role and search-by-department scenarios need their
	own rows rather than the fixture employee's."""
	if not frappe.db.exists("Designation", DIRECTORY_DESIGNATION):
		frappe.get_doc(
			{"doctype": "Designation", "designation_name": DIRECTORY_DESIGNATION}
		).insert(ignore_permissions=True)

	department = frappe.db.get_value(
		"Department", {"department_name": DIRECTORY_DEPARTMENT, "company": company}, "name"
	)
	if not department:
		doc = frappe.get_doc(
			{
				"doctype": "Department",
				"department_name": DIRECTORY_DEPARTMENT,
				"company": company,
			}
		)
		doc.insert(ignore_permissions=True)
		department = doc.name
	return DIRECTORY_DESIGNATION, department


def _ensure_directory_employee(suffix, company, status="Active", **fields):
	"""One Employee with no `user_id` -- no login, no User Permission. These
	rows exist to be *found* in the directory, not signed in as. Idempotent on
	`employee_number`."""
	number = f"{_DIRECTORY_TAG}-{suffix}"
	name = frappe.db.get_value("Employee", {"employee_number": number}, "name")
	desired = {"status": status, "company": company, **fields}
	if status == "Left":
		desired.setdefault("relieving_date", "2024-12-31")

	if name:
		employee = frappe.get_doc("Employee", name)
		changed = False
		for field, value in desired.items():
			if employee.get(field) != value:
				employee.set(field, value)
				changed = True
		if changed:
			employee.save(ignore_permissions=True)
		return employee.name

	employee = frappe.get_doc(
		{
			"doctype": "Employee",
			"employee_number": number,
			"first_name": f"Directory {suffix.title()}",
			"date_of_birth": "1990-01-01",
			"date_of_joining": "2020-01-01",
			"gender": ensure_test_gender(),
			**desired,
		}
	)
	employee.insert(ignore_permissions=True)
	return employee.name


def ensure_directory_fixtures():
	"""The people P3-U8 needs to see, and the people it must not (P3-AE12).

	One active colleague in the fixture company with a role, a department and
	a published work email; a colleague who has Left and one who is Inactive
	in the same company; and an active employee in a second company. The
	manager fixture gets a work email so the sheet has a mailto: action.

	Idempotent, and returns the names by role so a test can name each one.
	"""
	company = ensure_test_company()
	other_company = ensure_baseline_company()
	designation, department = _ensure_directory_masters(company)

	employee_name, _, manager_name, _ = make_test_employee_and_manager()
	if frappe.db.get_value("Employee", manager_name, "company_email") != DIRECTORY_MANAGER_EMAIL:
		frappe.db.set_value("Employee", manager_name, "company_email", DIRECTORY_MANAGER_EMAIL)

	return {
		"company": company,
		"other_company": other_company,
		"designation": designation,
		"department": department,
		"employee": employee_name,
		"manager": manager_name,
		"colleague": _ensure_directory_employee(
			"COLLEAGUE",
			company,
			designation=designation,
			department=department,
			company_email=DIRECTORY_COLLEAGUE_EMAIL,
			reports_to=manager_name,
		),
		"left": _ensure_directory_employee("LEFT", company, status="Left"),
		"inactive": _ensure_directory_employee("INACTIVE", company, status="Inactive"),
		"other": _ensure_directory_employee("OTHERCO", other_company),
	}


# P4-U5: celebrations. Home's card reads a *projection* of `date_of_birth`
# and `date_of_joining` (P4-KTD14), so both the Python suite and
# `login-dashboard.spec.ts` need people whose dates fall in whatever month
# the run happens on. The dates are therefore recomputed on every call --
# a fixture with a fixed birthday drops out of the card the moment the month
# turns.
CELEBRATION_TAG = "P4U5-CELEBRATION"


def make_celebration_employee(suffix, company, date_of_birth, date_of_joining, **fields):
	"""One Employee with no `user_id` -- no login, no User Permission. These
	rows exist to be *named* on Home's celebrations card, never signed in as.
	Idempotent on `employee_number`, and re-dated on every call."""
	number = f"{CELEBRATION_TAG}-{suffix}"
	name = frappe.db.get_value("Employee", {"employee_number": number}, "name")
	desired = {
		"company": company,
		"date_of_birth": str(date_of_birth),
		"date_of_joining": str(date_of_joining),
		"status": "Active",
		**fields,
	}
	if desired["status"] == "Left":
		desired.setdefault("relieving_date", str(date_of_joining))

	if name:
		employee = frappe.get_doc("Employee", name)
		changed = False
		for field, value in desired.items():
			if str(employee.get(field) or "") != str(value or ""):
				employee.set(field, value)
				changed = True
		if changed:
			employee.save(ignore_permissions=True)
		return employee.name

	employee = frappe.get_doc(
		{
			"doctype": "Employee",
			"employee_number": number,
			"first_name": f"Celebration {suffix.title()}",
			"gender": ensure_test_gender(),
			**desired,
		}
	)
	employee.insert(ignore_permissions=True)
	return employee.name


def ensure_celebration_fixtures():
	"""One colleague of the fixture employee whose birthday falls in the
	current month, so `login-dashboard.spec.ts` can read a name and a day on
	Home's celebrations card (P4-U5 scenario 6).

	The 15th, not today: a day-and-month row is what the card is for, and a
	fixture pinned to today would only ever exercise the "Today" chip.
	Born in 1990 and joined in a past January, so the person is a birthday
	and (outside January) not also an anniversary.
	"""
	from frappe.utils import getdate

	company = ensure_test_company()
	today = getdate()
	return make_celebration_employee(
		"BIRTHDAY",
		company,
		date_of_birth=date(1990, today.month, 15),
		date_of_joining=date(today.year - 4, 1, 6),
	)


# P3-U7: the team week. `team.spec.ts` needs one *report* of the manager
# fixture who is on leave inside the current week, and it must not be the
# shared fixture employee: a committed leave row on that person's calendar
# collides with the overlap validation every other leave suite runs into, and
# the whole point of this fixture is a row that is simply there whenever the
# spec runs.
#
# So it is a dedicated Employee with no login (it exists to be looked at,
# never signed in as) and one **approved** leave. Approved on purpose: an
# Open row would join the manager's Approvals queue and change what every
# approval scenario on this site sees.
TEAM_REPORT_NUMBER = "P3U7-TEAM-REPORT"
TEAM_REPORT_LEAVE_NAME = "_TEST-P3U7-TEAM-LEAVE"
TEAM_REPORT_NAME = "Team Member"
TEAM_REPORT_LEAVE_TYPE = "Casual Leave"


@frappe.whitelist()
def ensure_team_week_fixtures():
	"""One active direct report of the manager fixture, on approved leave
	across the current week. Idempotent, and re-dated on every call so a run
	next week still finds the leave under today.

	The leave row is written with `db_insert()`: HRMS validates balance,
	allocation period and overlap on every save of a Leave Application, and
	this row is an *input* to a read-only calendar rather than a lifecycle
	being exercised. Same reasoning as `helixhr/tests/test_api_team.py`.
	"""
	if not frappe.conf.get("allow_tests"):
		frappe.throw("Test fixtures are disabled on this site (allow_tests is off).")

	from frappe.utils import add_days, today

	from helixhr.utils import get_week_bounds

	_, _, manager_name, _ = make_test_employee_and_manager()
	company = frappe.db.get_value("Employee", manager_name, "company")

	report = frappe.db.get_value("Employee", {"employee_number": TEAM_REPORT_NUMBER}, "name")
	if not report:
		doc = frappe.get_doc(
			{
				"doctype": "Employee",
				"employee_number": TEAM_REPORT_NUMBER,
				"first_name": "Team",
				"last_name": "Member",
				"company": company,
				"date_of_birth": "1990-01-01",
				"date_of_joining": "2020-01-01",
				"gender": ensure_test_gender(),
				"status": "Active",
				"reports_to": manager_name,
			}
		)
		doc.insert(ignore_permissions=True)
		report = doc.name
	else:
		frappe.db.set_value(
			"Employee", report, {"status": "Active", "reports_to": manager_name}
		)

	# A day either side of the week, so the leave covers today whichever
	# Monday the portal's own week starts on for the signed-in user.
	monday, sunday = get_week_bounds(today())
	from_date, to_date = str(add_days(monday, -1)), str(add_days(sunday, 1))

	if frappe.db.exists("Leave Application", TEAM_REPORT_LEAVE_NAME):
		frappe.db.set_value(
			"Leave Application",
			TEAM_REPORT_LEAVE_NAME,
			{"employee": report, "from_date": from_date, "to_date": to_date},
		)
	else:
		leave = frappe.get_doc(
			{
				"doctype": "Leave Application",
				"employee": report,
				"employee_name": TEAM_REPORT_NAME,
				"leave_type": TEAM_REPORT_LEAVE_TYPE,
				"from_date": from_date,
				"to_date": to_date,
				"description": "Seeded by helixhr.tests.utils.ensure_team_week_fixtures",
				"status": "Approved",
				"docstatus": 1,
			}
		)
		leave.name = TEAM_REPORT_LEAVE_NAME
		leave.db_insert()

	frappe.db.commit()  # nosemgrep -- test fixture, as in setup_playwright_fixtures
	return {
		"manager": manager_name,
		"report": report,
		"report_name": TEAM_REPORT_NAME,
		"leave": TEAM_REPORT_LEAVE_NAME,
		"leave_type": TEAM_REPORT_LEAVE_TYPE,
		"from_date": from_date,
		"to_date": to_date,
	}


def leaving_employee_fixture():
	"""An Employee with no `user_id`, for the one test that has to set a
	status of Left (P3-U4 scenario 3b).

	ERPNext disables the linked User when an Employee leaves, and that write
	survives a test's rollback while the restore inside the test does not --
	so a suite that borrows a shared fixture identity for this leaves it
	unable to sign in for the rest of the run. This row has no login to lose.
	Idempotent, and always handed back as Active.
	"""
	number = "P3U4-LEAVER"
	name = frappe.db.get_value("Employee", {"employee_number": number}, "name")
	if name:
		employee = frappe.get_doc("Employee", name)
		if employee.status != "Active" or employee.relieving_date:
			employee.status = "Active"
			employee.relieving_date = None
			employee.save(ignore_permissions=True)
		return employee.name

	employee = frappe.get_doc(
		{
			"doctype": "Employee",
			"employee_number": number,
			"first_name": "Checkin Leaver",
			"date_of_birth": "1990-01-01",
			"date_of_joining": "2020-01-01",
			"gender": ensure_test_gender(),
			"company": ensure_test_company(),
			"status": "Active",
		}
	)
	employee.insert(ignore_permissions=True)
	return employee.name
