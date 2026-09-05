import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, add_to_date, get_datetime, today

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
		frappe.set_user(EMPLOYEE_USER)
		request = frappe.get_doc(
			{
				"doctype": "HR Request",
				"category": "HR Letter",
				"subject": "P2-U1 sharing check",
				"details": "no sharing",
			}
		)
		request.insert()

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
		from helixhr.tests.utils import ensure_holiday_list_assignment, ensure_leave_allocation

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
			name = frappe.get_doc(
				{
					"doctype": "HR Request",
					"category": "HR Letter",
					"subject": subject,
					"details": "parity",
				}
			).insert().name
		records["HR Request"] = name

		file_name = frappe.db.get_value(
			"File", {"attached_to_doctype": "HR Request", "attached_to_name": name}, "name"
		)
		if not file_name:
			file_name = frappe.get_doc(
				{
					"doctype": "File",
					"file_name": "p2-ae9.txt",
					"content": "parity",
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

	CUSTOMISED = ("Employee", "Leave Application", "Timesheet")

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

	def test_the_employee_role_can_submit_a_timesheet(self):
		"""R17: the portal's send-for-approval transition submits the week as
		the employee, and the base Timesheet DocPerm for role Employee has no
		`submit`."""
		self.assertEqual(_rule("Timesheet", "Employee").submit, 1)

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
