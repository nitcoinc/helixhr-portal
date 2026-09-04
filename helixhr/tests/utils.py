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
