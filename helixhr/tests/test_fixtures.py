import uuid

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, add_to_date, get_datetime, today

from helixhr.tests.test_hr_request import SAFE_PDF_BASE64
from helixhr.tests.utils import EMPLOYEE_USER, MANAGER_USER, make_test_employee_and_manager
from helixhr.utils import get_week_bounds


def _rules(doctype, role):
	"""The effective permission rules for one role, after Frappe has replaced
	the standard DocPerm rows with the Custom DocPerm rows that
	`patches.v1_0.apply_permission_deltas` maintains."""
	return [p for p in frappe.get_meta(doctype).permissions if p.role == role]


def _rule(doctype, role, permlevel=0, if_owner=0):
	for rule in _rules(doctype, role):
		if frappe.utils.cint(rule.permlevel) == permlevel and frappe.utils.cint(rule.if_owner) == if_owner:
			return rule
	return None


class TestHelixHRTestFixtures(IntegrationTestCase):
	"""The employee/manager fixture used by every later unit's tests and
	by the Playwright auth setup (U3). Prove it here once so a broken
	fixture fails loudly and close to the cause, not as a mystery
	failure three units later."""

	def test_creates_employee_reporting_to_manager_with_user_permissions(self):
		employee_name, employee_user, manager_name, manager_user = make_test_employee_and_manager()

		employee = frappe.get_doc("Employee", employee_name)
		self.assertEqual(employee.user_id, employee_user)
		self.assertEqual(employee.reports_to, manager_name)
		self.assertEqual(employee.status, "Active")

		manager = frappe.get_doc("Employee", manager_name)
		self.assertEqual(manager.user_id, manager_user)

	def test_is_idempotent(self):
		first = make_test_employee_and_manager()
		second = make_test_employee_and_manager()

		self.assertEqual(first, second)


class TestLeastPrivilegePermissions(IntegrationTestCase):
	"""P2-U1 step 7: the app grants no capability the portal does not use."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_an_employee_cannot_share_their_own_request(self):
		# P2-U8: role Employee has no `create` on HR Request any more, so the
		# fixture goes through the portal's own session-scoped method.
		from helixhr.api import create_my_request

		frappe.set_user(EMPLOYEE_USER)
		created = create_my_request(
			category="HR Letter",
			subject="P2-U1 sharing check",
			details="no sharing",
			operation_key=str(uuid.uuid4()),
		)
		request = frappe.get_doc("HR Request", created["name"])

		self.assertFalse(frappe.has_permission("HR Request", "share", doc=request.name))
		with self.assertRaises(frappe.PermissionError):
			frappe.share.add("HR Request", request.name, MANAGER_USER, read=1)

	def test_an_employee_cannot_share_their_own_leave_request(self):
		"""P2-U1 step 7. The portal has no sharing UI, so the Employee role's
		`share` right on Leave Application is dropped by
		`patches.v1_0.apply_permission_deltas`.

		HRMS's own Employee Self Service rule grants `share` independently --
		this app does not own that rule and must not change it, so the
		assertion is on the Employee role's own rules rather than on
		`has_permission` for a user who may also hold ESS. Removing sharing
		site-wide is System Settings' "Disable Document Sharing"; see
		docs/architecture.md.
		"""
		rules = _rules("Leave Application", "Employee")
		self.assertTrue(rules, "the Employee role lost its Leave Application rules entirely")
		for rule in rules:
			self.assertEqual(rule.share, 0, "this app must not grant the Employee role sharing")
			self.assertEqual(rule.submit, 0, "leave is submitted by the approver, never by the employee")

	def test_document_links_are_read_only_reference_data_for_employees(self):
		frappe.set_user(EMPLOYEE_USER)
		for ptype in ("write", "create", "delete", "share", "report", "print", "export", "email"):
			self.assertFalse(
				frappe.has_permission("HelixHR Document Link", ptype),
				f"Employee should not have {ptype} on HelixHR Document Link",
			)
		self.assertTrue(frappe.has_permission("HelixHR Document Link", "read"))


class TestStrictPermissionParity(IntegrationTestCase):
	"""P2-AE9 / P2-R26: with strict User Permissions on -- the way every
	real site is configured, and now the way CI runs -- the owning employee
	still reaches every record the portal shows them, and an unrelated
	employee reaches none of them, through the generic Frappe routes rather
	than only through this app's own methods.
	"""

	OUTSIDER = "outsider@helixhr.test"
	OTHER_COMPANY = "_Test Company Outsider"

	def setUp(self):
		frappe.set_user("Administrator")
		self.assertTrue(
			frappe.utils.cint(
				frappe.db.get_single_value("System Settings", "apply_strict_user_permissions")
			),
			"This suite asserts production-like authorization: turn on System Settings' "
			"Apply Strict User Permissions (preflight.check_strict_user_permissions, "
			"and the CI job sets it) before running it.",
		)
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()
		self.company = frappe.db.get_value("Employee", self.employee_name, "company")
		self.outsider_employee = self._make_outsider()
		self.records = self._seed_records()

	def tearDown(self):
		frappe.set_user("Administrator")

	def _make_outsider(self):
		from helixhr.tests.utils import make_test_user

		if not frappe.db.exists("Company", self.OTHER_COMPANY):
			frappe.get_doc(
				{
					"doctype": "Company",
					"company_name": self.OTHER_COMPANY,
					"abbr": "TCOU",
					"default_currency": "USD",
					"country": "United States",
				}
			).insert(ignore_permissions=True)
		# Deliberately no reports_to: Employee is a nested set, so a manager
		# in the same line would legitimately inherit access to their
		# reports' records.
		return make_test_user(self.OUTSIDER, self.OTHER_COMPANY)

	def _seed_records(self):
		from helixhr.tests.utils import (
			ensure_holiday_list_assignment,
			ensure_leave_allocation,
			make_test_salary_slip,
		)

		ensure_holiday_list_assignment(self.company)
		employee_label = frappe.db.get_value("Employee", self.employee_name, "employee_name")
		records = {}

		attendance_date = add_days(today(), -400)
		name = frappe.db.get_value(
			"Attendance", {"employee": self.employee_name, "attendance_date": attendance_date}, "name"
		)
		if not name:
			doc = frappe.get_doc(
				{
					"doctype": "Attendance",
					"employee": self.employee_name,
					"attendance_date": attendance_date,
					"status": "Present",
					"company": self.company,
				}
			)
			doc.insert(ignore_permissions=True)
			doc.submit()
			name = doc.name
		records["Attendance"] = name

		checkin_time = f"{add_days(today(), -400)} 09:00:00"
		name = frappe.db.get_value(
			"Employee Checkin", {"employee": self.employee_name, "time": checkin_time}, "name"
		)
		if not name:
			name = frappe.get_doc(
				{
					"doctype": "Employee Checkin",
					"employee": self.employee_name,
					"employee_name": employee_label,
					"time": checkin_time,
					"log_type": "IN",
				}
			).insert(ignore_permissions=True).name
		records["Employee Checkin"] = name

		# P3-U1 step 5a: a draft Attendance Request, far enough back to
		# overlap nothing another suite writes.
		request_date = add_days(today(), -410)
		name = frappe.db.get_value(
			"Attendance Request", {"employee": self.employee_name, "from_date": request_date}, "name"
		)
		if not name:
			name = frappe.get_doc(
				{
					"doctype": "Attendance Request",
					"employee": self.employee_name,
					"company": self.company,
					"from_date": request_date,
					"to_date": request_date,
					"reason": "Work From Home",
					"explanation": "P3-AE9 parity",
				}
			).insert(ignore_permissions=True).name
		records["Attendance Request"] = name

		# P3-U2: the record-level half of the Salary Slip matrix (P3-R3).
		# A closed period of its own, so the payslip suite's own year keeps
		# its exact counts.
		records["Salary Slip"] = make_test_salary_slip(
			self.employee_name, "2021-03-01", "2021-03-31", currency="USD"
		)

		ensure_leave_allocation(self.employee_name, "Casual Leave", 5)
		leave_date = add_days(today(), 94)
		name = frappe.db.get_value(
			"Leave Application", {"employee": self.employee_name, "from_date": leave_date}, "name"
		)
		if not name:
			name = frappe.get_doc(
				{
					"doctype": "Leave Application",
					"employee": self.employee_name,
					"leave_type": "Casual Leave",
					"from_date": leave_date,
					"to_date": leave_date,
					"description": "P2-AE9",
					"leave_approver": MANAGER_USER,
				}
			).insert(ignore_permissions=True).name
		records["Leave Application"] = name

		# Monday-anchored: get_my_week looks a week up by its Monday (KTD10).
		week_start = self.week_start = get_week_bounds(add_days(today(), -420))[0]
		name = frappe.db.get_value(
			"Timesheet", {"employee": self.employee_name, "start_date": week_start}, "name"
		)
		if not name:
			doc = frappe.get_doc(
				{
					"doctype": "Timesheet",
					"employee": self.employee_name,
					"company": self.company,
					"start_date": week_start,
					"end_date": add_days(week_start, 6),
				}
			)
			# Timesheet is mandatory-empty without at least one row.
			start = get_datetime(f"{week_start} 09:00:00")
			doc.append(
				"time_logs",
				{
					"project": self._project(),
					"hours": 1,
					"description": "P2-AE9",
					"activity_type": "General",
					"from_time": start,
					"to_time": add_to_date(start, hours=1),
				},
			)
			doc.insert(ignore_permissions=True)
			name = doc.name
		records["Timesheet"] = name

		frappe.set_user(EMPLOYEE_USER)
		subject = "P2-AE9 permission parity"
		name = frappe.db.get_value("HR Request", {"subject": subject}, "name")
		if not name:
			# P2-U8: the portal method, because role Employee no longer has a
			# generic `create` on this DocType.
			from helixhr.api import create_my_request

			name = create_my_request(
				category="HR Letter",
				subject=subject,
				details="parity",
				operation_key=str(uuid.uuid4()),
			)["name"]
		records["HR Request"] = name

		file_name = frappe.db.get_value(
			"File", {"attached_to_doctype": "HR Request", "attached_to_name": name}, "name"
		)
		if not file_name:
			file_name = frappe.get_doc(
				{
					"doctype": "File",
					# A real PDF: the P2-U9 upload policy refuses a text body
					# under any name, and this file exists to be *reached*,
					# not to test the policy (that is test_upload_security).
					"file_name": "p2-ae9.pdf",
					"content": SAFE_PDF_BASE64,
					"decode": 1,
					"attached_to_doctype": "HR Request",
					"attached_to_name": name,
					"is_private": 1,
				}
			).insert().name
		records["File"] = file_name
		frappe.set_user("Administrator")

		records["HelixHR Document Link"] = self._document_link(
			"P2-AE9 own company policy", self.company
		)
		records["HelixHR Document Link (other company)"] = self._document_link(
			"P2-AE9 other company policy", self.OTHER_COMPANY
		)
		return records

	def _project(self):
		title = "_Test P2-AE9 Project"
		name = frappe.db.get_value("Project", {"project_name": title}, "name")
		if not name:
			name = frappe.get_doc(
				{"doctype": "Project", "project_name": title, "status": "Open", "company": self.company}
			).insert(ignore_permissions=True).name
		# Strict User Permissions reach into child rows too: without a
		# Project permission the employee cannot read their own Timesheet,
		# which is the same grant get_my_projects/save_my_week rely on.
		if not frappe.db.exists(
			"User Permission", {"user": EMPLOYEE_USER, "allow": "Project", "for_value": name}
		):
			frappe.get_doc(
				{"doctype": "User Permission", "user": EMPLOYEE_USER, "allow": "Project", "for_value": name}
			).insert(ignore_permissions=True)
		return name

	def _document_link(self, title, company):
		existing = frappe.db.get_value("HelixHR Document Link", {"title": title}, "name")
		if existing:
			return existing
		return frappe.get_doc(
			{
				"doctype": "HelixHR Document Link",
				"title": title,
				"url": "https://example.com/p2-ae9",
				"company": company,
			}
		).insert(ignore_permissions=True).name

	def test_the_owning_employee_reaches_every_record_the_portal_shows(self):
		frappe.set_user(EMPLOYEE_USER)
		self.assertTrue(frappe.has_permission("Employee", "read", self.employee_name))
		for label in (
			"Attendance",
			"Employee Checkin",
			"Attendance Request",
			"Salary Slip",
			"Leave Application",
			"HR Request",
			"File",
			"HelixHR Document Link",
		):
			self.assertTrue(
				frappe.has_permission(label, "read", self.records[label]),
				f"the owning employee should be able to read their own {label}",
			)

	def test_the_employee_reaches_their_own_timesheet(self):
		"""Timesheet needs its own test because strict User Permissions also
		refuse a document whose scoped link field is *empty*, and
		`Timesheet.parent_project` is empty on every week the portal
		creates. `fixtures/property_setter.json` marks that field
		`ignore_user_permissions` for exactly this reason -- without it
		save_my_week cannot insert, the employee cannot read their own week,
		and the manager's Approvals list comes back empty.
		"""
		from helixhr.api import get_my_week

		frappe.set_user(EMPLOYEE_USER)
		self.assertTrue(frappe.has_permission("Timesheet", "read", self.records["Timesheet"]))
		week = get_my_week(str(self.week_start))
		self.assertEqual(week["timesheet"]["name"], self.records["Timesheet"])

	def test_an_unrelated_employee_reaches_none_of_them(self):
		frappe.set_user(self.OUTSIDER)
		self.assertFalse(frappe.has_permission("Employee", "read", self.employee_name))
		for label, doctype in (
			("Attendance", "Attendance"),
			("Employee Checkin", "Employee Checkin"),
			("Attendance Request", "Attendance Request"),
			("Salary Slip", "Salary Slip"),
			("Leave Application", "Leave Application"),
			("Timesheet", "Timesheet"),
			("HR Request", "HR Request"),
			("File", "File"),
			("HelixHR Document Link", "HelixHR Document Link"),
		):
			self.assertFalse(
				frappe.has_permission(doctype, "read", self.records[label]),
				f"an unrelated employee must not be able to read somebody else's {label}",
			)

	def test_generic_list_routes_do_not_leak_across_identities(self):
		frappe.set_user(self.OUTSIDER)
		for label, doctype in (
			("Attendance", "Attendance"),
			("Employee Checkin", "Employee Checkin"),
			("Attendance Request", "Attendance Request"),
			("Salary Slip", "Salary Slip"),
			("Leave Application", "Leave Application"),
			("Timesheet", "Timesheet"),
			("HR Request", "HR Request"),
		):
			names = frappe.get_list(
				doctype, filters={"employee": self.employee_name}, pluck="name", limit=0
			)
			self.assertNotIn(self.records[label], names, f"{doctype} list leaked another employee's row")

		links = frappe.get_list("HelixHR Document Link", pluck="name", limit=0)
		self.assertIn(self.records["HelixHR Document Link (other company)"], links)
		self.assertNotIn(self.records["HelixHR Document Link"], links)

	def test_salary_slips_are_read_and_print_only_for_the_employee_role(self):
		"""P3-KTD2 / P3-R3: HRMS ships role Employee with `read` and `print`
		on Salary Slip and nothing else, which is exactly what the payslip
		wrapper and its PDF endpoint rely on -- no delta is needed, so this
		pins the shipped shape. P3-U2 seeds real slips for the record-level
		half of the matrix."""
		rules = _rules("Salary Slip", "Employee")
		self.assertTrue(rules, "the Employee role lost its Salary Slip rules entirely")
		for rule in rules:
			self.assertEqual(rule.read, 1)
			self.assertEqual(rule.print, 1)
			for ptype in ("write", "create", "delete", "submit", "cancel", "amend", "share", "report", "export"):
				self.assertEqual(rule.get(ptype), 0, f"Employee must not hold {ptype} on Salary Slip")

	def test_the_generic_employee_list_still_returns_only_self(self):
		"""P3-AE12 / P3-R23: the directory is a server projection that reads
		Employee with `ignore_permissions` (P3-KTD1), which is only defensible
		while the generic route stays shut. Under strict user permissions an
		employee's own list route answers with their own record and nothing
		else -- no colleague, and no employee of another company."""
		frappe.set_user(EMPLOYEE_USER)
		names = frappe.get_list("Employee", pluck="name", limit=0)
		self.assertEqual(names, [self.employee_name])
		self.assertNotIn(self.outsider_employee, names)

		from frappe.client import get_list as client_get_list

		client = [row["name"] for row in client_get_list("Employee", limit_page_length=0)]
		self.assertEqual(client, [self.employee_name])


class TestPermissionDeltas(IntegrationTestCase):
	"""P2-U1: `patches.v1_0.apply_permission_deltas` replaced the three Custom
	DocPerm fixtures.

	Frappe *discards* every standard DocPerm for a doctype that has any Custom
	DocPerm row (`frappe.permissions.get_valid_perms`), so shipping a partial
	set of roles as a fixture silently removed HR Manager, HR User, Leave
	Approver and Projects User on every fresh site. The patch snapshots the
	site's own standard rows first (`setup_custom_perms`) and then applies only
	this app's deltas, so these tests assert the *effective* permissions rather
	than the contents of a file.
	"""

	# P3-KTD13 added Employee Checkin and Attendance Request to the delta
	# table; Salary Slip is here because HRMS gives it Custom DocPerm rows of
	# its own (Employee Self Service) and the payslip pages depend on no
	# standard role having been stripped from it.
	CUSTOMISED = (
		"Employee",
		"Leave Application",
		"Timesheet",
		"Employee Checkin",
		"Attendance Request",
		"Salary Slip",
		"HR Request",
		"HelixHR Request Category",
	)

	def test_no_standard_role_lost_access_to_a_customised_doctype(self):
		"""The regression this patch exists for. Fails the moment a change
		re-strips the roles this app does not own."""
		for doctype in self.CUSTOMISED:
			standard = {
				(row.role, frappe.utils.cint(row.permlevel))
				for row in frappe.get_all(
					"DocPerm",
					filters={"parent": doctype},
					fields=["role", "permlevel"],
					parent_doctype="DocType",
				)
			}
			effective = {
				(rule.role, frappe.utils.cint(rule.permlevel))
				for rule in frappe.get_meta(doctype).permissions
			}
			self.assertEqual(
				set(),
				standard - effective,
				f"{doctype}: these standard role rules were replaced with nothing",
			)

	def test_the_employee_permlevel_lock_is_readable_and_writable_by_hr_only(self):
		"""fixtures/property_setter.json moves every field an employee may not
		edit to permlevel 1 and the HR-only ones to permlevel 2. Standard
		Employee DocPerms stop at level 0, so these rules are entirely this
		app's -- without them not even HR could read a locked field."""
		self.assertEqual(_rule("Employee", "Employee").write, 1, "profile edit needs level 0 write")

		employee_level_1 = _rule("Employee", "Employee", 1)
		self.assertIsNotNone(employee_level_1, "the employee must still be able to read locked fields")
		self.assertEqual(employee_level_1.read, 1)
		self.assertEqual(employee_level_1.write, 0, "level 1 is exactly the fields an employee cannot edit")
		self.assertIsNone(_rule("Employee", "Employee", 2), "level 2 is HR-only")

		for role in ("HR Manager", "HR User", "System Manager"):
			for permlevel in (1, 2):
				rule = _rule("Employee", role, permlevel)
				self.assertIsNotNone(rule, f"{role} lost level {permlevel} on Employee")
				self.assertEqual(rule.read, 1)
				self.assertEqual(rule.write, 1)

	def test_an_employee_may_delete_only_their_own_leave_application(self):
		"""KTD17: withdraw is `delete` on a pending request, and the base
		DocPerm for role Employee carries none. It is granted as a separate
		`if_owner` rule on purpose -- putting `if_owner` on the base rule would
		move read and write into the owner-only bucket too, and an employee
		would stop being able to see a request HR filed for them."""
		base = _rule("Leave Application", "Employee")
		self.assertEqual(base.delete, 0, "delete must not be granted for everyone's leave")
		self.assertEqual(base.read, 1)

		own = _rule("Leave Application", "Employee", if_owner=1)
		self.assertIsNotNone(own, "withdraw needs an if_owner delete rule")
		self.assertEqual(own.delete, 1)

		as_owner = frappe.permissions.get_role_permissions(
			"Leave Application", user=EMPLOYEE_USER, is_owner=True
		)
		self.assertTrue(as_owner["if_owner"].get("delete"), "an employee must be able to withdraw their own")
		not_owner = frappe.permissions.get_role_permissions(
			"Leave Application", user=EMPLOYEE_USER, is_owner=False
		)
		self.assertFalse(not_owner["if_owner"].get("delete"), "and only their own")
		self.assertFalse(not_owner.get("delete"), "an employee must not delete another employee's leave")
		self.assertTrue(not_owner.get("read"), "reading a request HR filed for them must still work")

	def test_it_team_permissions_keep_existing_hr_request_roles_and_limit_category_access(self):
		for permlevel in (0, 1):
			rule = _rule("HR Request", "IT Team", permlevel=permlevel)
			self.assertIsNotNone(rule, f"IT Team is missing HR Request level {permlevel}")
			self.assertTrue(rule.read and rule.write)

		category = _rule("HelixHR Request Category", "IT Team")
		self.assertIsNotNone(category)
		self.assertTrue(category.read)
		for ptype in ("create", "write", "delete", "share"):
			self.assertEqual(category.get(ptype), 0, ptype)

	def test_the_employee_role_can_submit_a_timesheet(self):
		"""R17: the portal's send-for-approval transition submits the week as
		the employee, and the base Timesheet DocPerm for role Employee has no
		`submit`."""
		self.assertEqual(_rule("Timesheet", "Employee").submit, 1)

	def test_the_employee_role_cannot_create_edit_or_delete_punches(self):
		"""P3-KTD13 / P3-R7a: the portal method is the only way an employee
		writes an Employee Checkin; `read` stays for the Attendance page."""
		rule = _rule("Employee Checkin", "Employee")
		self.assertIsNotNone(rule, "the Employee role lost its Employee Checkin rule")
		self.assertEqual(rule.read, 1)
		for ptype in ("create", "write", "delete"):
			self.assertEqual(rule.get(ptype), 0, f"Employee must not hold {ptype} on Employee Checkin")

	def test_the_employee_role_cannot_share_an_attendance_request(self):
		"""P3-KTD13 / P3-R17a: `share` would let an employee grant a colleague
		`submit` on their own request and skip both approval steps."""
		rules = _rules("Attendance Request", "Employee")
		self.assertTrue(rules, "the Employee role lost its Attendance Request rules entirely")
		for rule in rules:
			self.assertEqual(rule.share, 0, "this app must not grant the Employee role sharing")
			self.assertEqual(rule.submit, 0, "the HR step is the only submit")

	def test_generic_punch_routes_are_closed_to_an_employee(self):
		"""P3-AE5a. After the delta an employee's `frappe.client.insert`,
		`set_value` and `delete` on Employee Checkin are all refused, while
		reading their own punch still works."""
		from frappe.client import delete as client_delete
		from frappe.client import insert as client_insert
		from frappe.client import set_value as client_set_value

		frappe.set_user("Administrator")
		employee_name, _, _, _ = make_test_employee_and_manager()
		employee_label = frappe.db.get_value("Employee", employee_name, "employee_name")
		punch_time = f"{add_days(today(), -430)} 09:00:00"
		punch = frappe.db.get_value("Employee Checkin", {"employee": employee_name, "time": punch_time}, "name")
		if not punch:
			punch = frappe.get_doc(
				{
					"doctype": "Employee Checkin",
					"employee": employee_name,
					"employee_name": employee_label,
					"time": punch_time,
					"log_type": "IN",
				}
			).insert(ignore_permissions=True).name

		frappe.set_user(EMPLOYEE_USER)
		self.addCleanup(frappe.set_user, "Administrator")
		self.assertTrue(frappe.has_permission("Employee Checkin", "read", punch))

		with self.assertRaises(frappe.PermissionError):
			client_insert(
				{
					"doctype": "Employee Checkin",
					"employee": employee_name,
					"time": f"{add_days(today(), -431)} 09:00:00",
					"log_type": "IN",
				}
			)
		with self.assertRaises(frappe.PermissionError):
			client_set_value("Employee Checkin", punch, "time", f"{add_days(today(), -430)} 10:00:00")
		with self.assertRaises(frappe.PermissionError):
			client_delete("Employee Checkin", punch)
		self.assertEqual(frappe.db.get_value("Employee Checkin", punch, "time"), get_datetime(punch_time))

	def test_the_patch_is_idempotent(self):
		"""It runs once through the patch log, but a re-run by hand (or a
		restored site) must not double-apply or duplicate a rule."""
		from helixhr.patches.v1_0 import apply_permission_deltas

		before = self._snapshot()
		apply_permission_deltas.execute()
		self.assertEqual(before, self._snapshot())

	def _snapshot(self):
		return frappe.get_all(
			"Custom DocPerm",
			filters={"parent": ("in", self.CUSTOMISED)},
			fields=["parent", "role", "permlevel", "if_owner", "read", "write", "create", "delete",
				"submit", "cancel", "amend", "report", "export", "print", "email", "share"],
			order_by="parent, role, permlevel, if_owner",
		)


_MANAGER = "Employee"
_HR = "HR Manager"

_MANAGER = "Employee"
_HR = "HR Manager"

# The plan's "who may do what, per state" table, as (state, action,
# next_state, allowed role) edges.
_TIMESHEET_EDGES = frozenset(
	{
		("Draft", "Submit", "Pending Approval", _MANAGER),
		("Pending Approval", "Approve", "Approved", _MANAGER),
		("Pending Approval", "Send Back", "Sent Back", _MANAGER),
		("Pending Approval", "Send to HR", "Pending HR", _MANAGER),
		("Pending Approval", "Approve", "Approved", _HR),
		("Pending Approval", "Send Back", "Sent Back", _HR),
		("Pending HR", "Approve", "Approved", _HR),
		("Pending HR", "Send Back", "Sent Back", _HR),
		("Sent Back", "Edit", "Draft", _MANAGER),
	}
)
_HR_REQUEST_EDGES = frozenset(
	{
		(state, action, next_state, role)
		for role in ("HR Manager", "IT Team")
		for state, action, next_state in (
			("Open", "Pick up", "In Progress"),
			("Open", "Reject", "Rejected"),
			("In Progress", "Need info", "Waiting on Employee"),
			("In Progress", "Done", "Done"),
			("In Progress", "Reject", "Rejected"),
		)
	}
)

_ATTENDANCE_EDGES = frozenset(
	{
		("Draft", "Submit", "Pending Manager", _MANAGER),
		("Draft", "Approve", "Approved", _HR),
		("Pending Manager", "Approve", "Approved", _MANAGER),
		("Pending Manager", "Send Back", "Sent Back", _MANAGER),
		("Pending Manager", "Reject", "Rejected", _MANAGER),
		("Pending Manager", "Send to HR", "Pending HR", _MANAGER),
		("Pending Manager", "Approve", "Approved", _HR),
		("Pending Manager", "Send Back", "Sent Back", _HR),
		("Pending Manager", "Reject", "Rejected", _HR),
		("Pending HR", "Approve", "Approved", _HR),
		("Pending HR", "Send Back", "Sent Back", _HR),
		("Pending HR", "Reject", "Rejected", _HR),
		("Sent Back", "Edit", "Draft", _MANAGER),
	}
)


class TestApprovalWorkflowFixtures(IntegrationTestCase):
	"""P4-U1. Both workflows, transition by transition, against the plan's
	"who may do what, per state" table.

	The fixture is imported on every migrate and a typo in a condition string
	breaks approvals site-wide, so the shape is asserted rather than assumed
	-- and the *order* of the states matters on its own:
	`Workflow.on_update` backfills a null `workflow_state` from the first
	state of each docstatus, so Draft has to stay first at docstatus 0 or
	every legacy row would be backfilled into a pending state.
	"""

	def _workflow(self, name):
		self.assertTrue(frappe.db.exists("Workflow", name), f"{name} did not import")
		return frappe.get_doc("Workflow", name)

	def _edges(self, workflow):
		return {(t.state, t.action, t.next_state, t.allowed) for t in workflow.transitions}

	def test_the_timesheet_workflow_matches_the_table(self):
		workflow = self._workflow("Timesheet Approval")
		self.assertEqual(self._edges(workflow), _TIMESHEET_EDGES)
		self.assertEqual(
			[row.state for row in workflow.states],
			["Draft", "Pending Approval", "Pending HR", "Approved", "Sent Back"],
		)
		# P4-KTD2: no terminal reject on a week, in either pending state.
		self.assertNotIn("Reject", {t.action for t in workflow.transitions})

	def test_the_hr_request_workflow_matches_the_table(self):
		workflow = self._workflow("HR Request Handling")
		self.assertEqual(self._edges(workflow), _HR_REQUEST_EDGES)
		self.assertEqual(workflow.workflow_state_field, "status")
		self.assertEqual(workflow.states[0].state, "Open")
		self.assertEqual(
			[row.state for row in workflow.states],
			["Open", "In Progress", "Waiting on Employee", "Done", "Rejected"],
		)

	def test_the_attendance_request_workflow_matches_the_table(self):
		workflow = self._workflow("Attendance Request Approval")
		self.assertEqual(self._edges(workflow), _ATTENDANCE_EDGES)
		self.assertEqual(
			[row.state for row in workflow.states],
			["Draft", "Pending Manager", "Pending HR", "Approved", "Sent Back", "Rejected"],
		)
		# P4-KTD3: Rejected is terminal -- nothing leads out of it.
		self.assertEqual([t for t in workflow.transitions if t.state == "Rejected"], [])

	def test_draft_is_the_first_state_per_docstatus_in_both(self):
		for name in ("Timesheet Approval", "Attendance Request Approval"):
			workflow = self._workflow(name)
			first_per_docstatus = {}
			for row in workflow.states:
				first_per_docstatus.setdefault(str(row.doc_status), row.state)
			self.assertEqual(first_per_docstatus["0"], "Draft", msg=name)
			self.assertEqual(first_per_docstatus["1"], "Approved", msg=name)

	def test_every_hr_transition_refuses_the_requesters_own_record(self):
		"""P4-R8. Frappe's own `allow_self_approval` guard is owner-based, and
		HR is the owner of anything HR filed for somebody else, so the rule
		lives in the condition instead -- on every HR transition, both
		workflows. `events.timesheet_before_submit` and
		`events.attendance_request_before_submit` are the raw-route halves.
		"""
		for name in ("Timesheet Approval", "Attendance Request Approval"):
			for transition in self._workflow(name).transitions:
				if transition.allowed != _HR:
					continue
				self.assertIn(
					"user_id",
					transition.condition or "",
					msg=f"{name}: {transition.state} -> {transition.action}",
				)
				self.assertIn("frappe.session.user", transition.condition or "")

	def test_the_decision_reason_field_installed_on_both_kinds_at_permlevel_one(self):
		"""P4-KTD7a. The reason has to survive the removal of a rejected
		request (P4-KTD3), so it is a field of the record; permlevel 1 is
		what stops the employee or their manager writing it."""
		for doctype in ("Timesheet", "Attendance Request"):
			field = frappe.db.get_value(
				"Custom Field",
				{"dt": doctype, "fieldname": "helixhr_decision_reason"},
				["fieldtype", "permlevel", "module"],
				as_dict=True,
			)
			self.assertIsNotNone(field, msg=doctype)
			self.assertEqual(field.fieldtype, "Small Text", msg=doctype)
			self.assertEqual(frappe.utils.cint(field.permlevel), 1, msg=doctype)
			self.assertEqual(field.module, "HelixHR", msg=doctype)
			hr = _rule(doctype, "HR Manager", permlevel=1)
			self.assertIsNotNone(hr, msg=doctype)
			self.assertTrue(hr.read and hr.write, msg=doctype)
			self.assertIsNone(_rule(doctype, "Employee", permlevel=1), msg=doctype)

	def test_the_leave_stage_and_hr_approves_fields_are_installed(self):
		"""P4-KTD4 / P4-R7. Leave has no Workflow, so these two fields *are*
		the routing: the stage says which queue a request waits in and the
		Leave Type flag says which queue it starts in. Permlevel 1 on the
		stage is what stops the employee or their approver moving it with a
		generic write -- role Employee has write on its own open Leave
		Application and HRMS shares every application with its approver at
		`submit=1` (P4-R8a).
		"""
		stage = frappe.db.get_value(
			"Custom Field",
			{"dt": "Leave Application", "fieldname": "helixhr_stage"},
			["fieldtype", "permlevel", "module", "options", "default", "allow_on_submit"],
			as_dict=True,
		)
		self.assertIsNotNone(stage)
		self.assertEqual(stage.fieldtype, "Select")
		self.assertEqual(frappe.utils.cint(stage.permlevel), 1)
		self.assertEqual(stage.module, "HelixHR")
		self.assertEqual(stage.options.split("\n"), ["Manager", "HR"])
		self.assertEqual(stage.default, "Manager")
		self.assertEqual(frappe.utils.cint(stage.allow_on_submit), 0)

		hr = _rule("Leave Application", "HR Manager", permlevel=1)
		self.assertIsNotNone(hr)
		self.assertTrue(hr.read and hr.write)
		self.assertIsNone(_rule("Leave Application", "Employee", permlevel=1))

		flag = frappe.db.get_value(
			"Custom Field",
			{"dt": "Leave Type", "fieldname": "helixhr_hr_approves"},
			["fieldtype", "permlevel", "module"],
			as_dict=True,
		)
		self.assertIsNotNone(flag)
		self.assertEqual(flag.fieldtype, "Check")
		self.assertEqual(frappe.utils.cint(flag.permlevel), 0)
		self.assertEqual(flag.module, "HelixHR")

		# A leave HR files in Desk starts with the manager, like any other:
		# the default is on the field, not in the portal method.
		self.assertEqual(frappe.new_doc("Leave Application").helixhr_stage, "Manager")

	def test_the_reminder_template_pickers_are_installed_on_hr_settings(self):
		"""P4-R17 / P4-KTD10. Picking a template is how HR switches the
		branded email on, so the two fields are the switch itself -- and they
		sit in HRMS's own Reminders section, next to the checkboxes they
		replace."""
		from helixhr.reminders import EVENTS

		for spec in EVENTS.values():
			field = frappe.db.get_value(
				"Custom Field",
				{"dt": "HR Settings", "fieldname": spec["template_field"]},
				["fieldtype", "options", "module", "insert_after", "label"],
				as_dict=True,
			)
			self.assertIsNotNone(field, msg=spec["template_field"])
			self.assertEqual(field.fieldtype, "Link", msg=spec["template_field"])
			self.assertEqual(field.options, "Email Template", msg=spec["template_field"])
			self.assertEqual(field.module, "HelixHR", msg=spec["template_field"])
			# The refusal and the preflight line quote these back to HR, so
			# the label on the form has to be the label they name.
			self.assertEqual(field.label, spec["template_label"], msg=spec["template_field"])
			self.assertEqual(field.insert_after, spec["hrms_field"], msg=spec["template_field"])

	def test_the_rename_patch_is_idempotent_and_leaves_submitted_rows_alone(self):
		"""P4-KTD1. A site with legacy docstatus-0 Rejected rows ends with
		them in Sent Back; a submitted row is untouched; a second run changes
		nothing."""
		from helixhr.patches.v1_0 import rename_sent_back_state

		employee_name, _, _, _ = make_test_employee_and_manager()
		frappe.set_user("Administrator")
		monday, _ = get_week_bounds(add_days(today(), -3500))
		rows = {}
		for offset, (docstatus, state) in enumerate(((0, "Rejected"), (1, "Approved"))):
			doc = frappe.get_doc(
				{
					"doctype": "Timesheet",
					"employee": employee_name,
					"company": frappe.db.get_value("Employee", employee_name, "company"),
					"time_logs": [
						{
							"activity_type": "General",
							"from_time": f"{add_days(monday, offset)} 09:00:00",
							"hours": 1,
							"description": "legacy",
						}
					],
				}
			)
			doc.insert(ignore_permissions=True)
			# Raw writes on purpose: a real submit would run the workflow this
			# test is pretending predates the rename.
			frappe.db.set_value(
				"Timesheet", doc.name, {"docstatus": docstatus, "workflow_state": state}
			)
			rows[docstatus] = doc.name
			self.addCleanup(self._remove_timesheet, doc.name)

		rename_sent_back_state.execute()
		self.assertEqual(
			frappe.db.get_value("Timesheet", rows[0], "workflow_state"), "Sent Back"
		)
		self.assertEqual(frappe.db.get_value("Timesheet", rows[1], "workflow_state"), "Approved")

		rename_sent_back_state.execute()
		self.assertEqual(
			frappe.db.get_value("Timesheet", rows[0], "workflow_state"), "Sent Back"
		)

	def _remove_timesheet(self, name):
		"""The row was pushed to its docstatus with a raw write, so it has to
		come back to Draft the same way before Frappe will delete it."""
		frappe.set_user("Administrator")
		frappe.db.set_value("Timesheet", name, "docstatus", 0)
		frappe.delete_doc("Timesheet", name, force=True, ignore_permissions=True)

