"""Plan 2026-10-19-001 U3: HR Manager, HR User and IT Admin reach, and every
HR/IT mail recipient, come from assignments (R3, R6). U4: read, report,
project and portal-admin scope, the R7 global settings and People & access
reach come from them too (R2, R3, R7, R24).

Company A is the test company, B the baseline second company and C a third
one no HR or IT row here names. Every login is this module's own.
"""

import uuid

import frappe
from frappe.tests import IntegrationTestCase

from helixhr import reminders
from helixhr.api import (
	_can_edit_email_templates,
	_can_run_reports,
	_handler_comment_refusal,
	_holds_routed_role,
	_oversight_scope,
	act_on_approval,
	create_my_request,
	get_approval_detail,
	get_my_approvals,
	get_person,
	get_portal_bootstrap,
	get_project,
	request_export,
	save_email_theme,
	save_message_template,
	save_person,
	save_report_access,
	search_people,
	search_people_access,
	search_projects,
)
from helixhr.events import _enabled_users_with_role, _hr_managers, _is_hr
from helixhr.tests.test_approvals_cross_company import HR_REPORT_USER, _CrossCompanyCase
from helixhr.tests.utils import (
	create_test_company,
	ensure_baseline_company,
	ensure_test_company,
	make_assignment,
	make_test_project,
	make_test_user,
	set_report_access,
)
from helixhr.utils import (
	ACCESS_ADMIN_ROLE,
	DELIVERY_MANAGER_ROLE,
	NOTIFICATION_EVENTS,
	ORG_VIEWER_ROLE,
	PORTAL_ADMIN_ROLE,
	REPORT_MANAGER_ROLE,
	_company_role_holders,
	can_admin_portal,
	can_manage_access,
	clear_role_scope_cache,
	has_all_orgs_authority,
	portal_role_scope,
	reset_rate_limit,
	resolve_admin_scope,
	resolve_portal_admin_scope,
	resolve_project_read_scope,
	resolve_project_scope,
	resolve_read_scope,
	resolve_report_access,
)

COMPANY_C = "_Test Access Resolvers C"
LATER_COMPANY = "_Test Access Resolvers Later"

HR_AB = "res-hr-ab@helixhr.test"
HR_A = "res-hr-a@helixhr.test"
LEADER = "res-leader@helixhr.test"
ROLE_ONLY = "res-role-only@helixhr.test"
IT_AB = "res-it-ab@helixhr.test"
HR_USER_AB = "res-hr-user-ab@helixhr.test"
SYSTEM_MANAGER = "res-sm@helixhr.test"
REQ_A = "res-req-a@helixhr.test"
REQ_B = "res-req-b@helixhr.test"
REQ_C = "res-req-c@helixhr.test"
REQ_LATER = "res-req-later@helixhr.test"

_MODULE_USERS = (HR_AB, HR_A, LEADER, ROLE_ONLY, IT_AB, HR_USER_AB, SYSTEM_MANAGER)
_MANAGED = ("HR Manager", "HR User", "IT Team", "System Manager")


class _Resolvers(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.a = ensure_test_company()
		self.b = ensure_baseline_company()
		self.c = create_test_company(COMPANY_C, "TARC")

		# Rows and roles persist between methods (rollback is per class).
		frappe.db.delete("HelixHR Access Assignment", {"user": ["in", list(_MODULE_USERS)]})
		for user in _MODULE_USERS:
			# No self-scoping User Permission: it would hide every other
			# Employee's request from these handlers' Frappe lists.
			make_test_user(user, self.a, create_user_permission=0, status="Active")
			frappe.db.delete("Has Role", {"parent": user, "role": ["in", list(_MANAGED)]})
			frappe.clear_cache(user=user)
		clear_role_scope_cache()

		make_assignment(HR_AB, "HR Manager", [self.a, self.b])
		make_assignment(HR_A, "HR Manager", [self.a])
		make_assignment(LEADER, "HR Manager", "all")
		make_assignment(IT_AB, "IT Team", [self.a, self.b])
		make_assignment(HR_USER_AB, "HR User", [self.a, self.b])
		# R3: the Frappe roles alone, with no assignment behind them.
		frappe.get_doc("User", ROLE_ONLY).add_roles("HR Manager", "HR User", "IT Team")
		frappe.clear_cache(user=ROLE_ONLY)

		self.emp_a = make_test_user(REQ_A, self.a)
		self.emp_b = make_test_user(REQ_B, self.b)
		self.emp_c = make_test_user(REQ_C, self.c)

	def tearDown(self):
		frappe.set_user("Administrator")
		clear_role_scope_cache()

	def _file(self, user, category):
		frappe.set_user(user)
		reset_rate_limit("create_my_request", user)
		name = create_my_request(
			category=category, subject="Resolver check", details="", operation_key=str(uuid.uuid4())
		)["name"]
		frappe.set_user("Administrator")
		self.addCleanup(_delete_request, name)
		return name


def _delete_request(name):
	frappe.set_user("Administrator")
	if frappe.db.exists("HR Request", name):
		frappe.delete_doc("HR Request", name, force=True, ignore_permissions=True)


class TestHrManagerScope(_Resolvers):
	def test_hr_manager_on_two_orgs_decides_in_b_and_is_refused_in_c(self):
		in_b = self._file(REQ_B, "HR Letter")
		in_c = self._file(REQ_C, "HR Letter")

		frappe.set_user(HR_AB)
		self.assertEqual(
			resolve_admin_scope(HR_AB), {"kind": "companies", "companies": sorted([self.a, self.b])}
		)
		self.assertIn("Reject", get_approval_detail("request", in_b)["actions"])
		act_on_approval("HR Request", in_b, "Reject", comment="Not this way.", **_request_token(in_b))
		self.assertEqual(frappe.db.get_value("HR Request", in_b, "status"), "Rejected")
		with self.assertRaises(frappe.PermissionError):
			get_approval_detail("request", in_c)
		self.assertNotIn(in_c, frappe.get_list("HR Request", pluck="name"))

	def test_a_frappe_role_without_a_row_is_not_hr_and_has_no_hr_queue(self):
		in_a = self._file(REQ_A, "HR Letter")

		frappe.set_user(ROLE_ONLY)
		self.assertFalse(_is_hr())
		self.assertEqual(resolve_admin_scope(ROLE_ONLY)["kind"], "none")
		self.assertIsNone(_oversight_scope())
		self.assertFalse(_holds_routed_role())
		self.assertFalse([row for row in get_my_approvals()["pending"] if row.get("lane") == "hr"])
		with self.assertRaises(frappe.PermissionError):
			get_approval_detail("request", in_a)
		self.assertNotIn(in_a, frappe.get_list("HR Request", pluck="name"))

	def test_the_leadership_preset_acts_in_an_org_created_later(self):
		self.assertEqual(resolve_admin_scope(LEADER)["kind"], "unscoped")
		later = create_test_company(LATER_COMPANY, "TARL")
		make_test_user(REQ_LATER, later)
		in_later = self._file(REQ_LATER, "HR Letter")

		frappe.set_user(LEADER)
		self.assertTrue(_is_hr())
		self.assertIn("Reject", get_approval_detail("request", in_later)["actions"])

	def test_a_left_hr_manager_with_rows_is_not_hr(self):
		# R4: rows stay, reach goes.
		employee = frappe.db.get_value("Employee", {"user_id": HR_AB}, "name")
		frappe.db.set_value("Employee", employee, "status", "Left")
		clear_role_scope_cache()
		self.assertFalse(_is_hr(HR_AB))


class TestLineManagerWhoIsHr(_CrossCompanyCase):
	"""The cross-company fixture's HR Manager holds a row on A only; their
	direct report sits in B."""

	def test_hr_manager_on_a_decides_their_report_in_b_with_line_manager_actions_only(self):
		self.assertEqual(resolve_admin_scope(self.hr_user), {"kind": "company", "company": self.company_a})
		week = self._pending_timesheet(HR_REPORT_USER)

		frappe.set_user(self.hr_user)
		self.assertEqual(
			get_approval_detail("timesheet", week)["actions"], ["Approve", "Send Back", "Send to HR"]
		)


class TestItAdminScope(_Resolvers):
	def test_it_admin_on_two_orgs_handles_a_b_request(self):
		in_b = self._file(REQ_B, "IT / Asset")

		frappe.set_user(IT_AB)
		self.assertTrue(_holds_routed_role())
		self.assertIn(in_b, frappe.get_list("HR Request", pluck="name"))
		self.assertTrue(get_approval_detail("request", in_b)["actions"])
		self.assertIsNone(_handler_comment_refusal(frappe.get_doc("HR Request", in_b), IT_AB))

	def test_it_admin_never_sees_a_c_request(self):
		in_c = self._file(REQ_C, "IT / Asset")

		frappe.set_user(IT_AB)
		self.assertNotIn(in_c, frappe.get_list("HR Request", pluck="name"))
		self.assertFalse(frappe.has_permission("HR Request", doc=in_c, user=IT_AB))
		with self.assertRaises(frappe.PermissionError):
			get_approval_detail("request", in_c)
		self.assertIsNotNone(_handler_comment_refusal(frappe.get_doc("HR Request", in_c), IT_AB))

	def test_an_it_role_without_a_row_works_nothing(self):
		in_a = self._file(REQ_A, "IT / Asset")

		frappe.set_user(ROLE_ONLY)
		self.assertNotIn(in_a, frappe.get_list("HR Request", pluck="name"))
		self.assertIsNotNone(_handler_comment_refusal(frappe.get_doc("HR Request", in_a), ROLE_ONLY))

	def test_arrival_mail_reaches_the_assigned_it_admin_and_not_a_role_only_holder(self):
		recipients = _enabled_users_with_role("IT Team", self.emp_b)
		self.assertIn(IT_AB, recipients)
		self.assertNotIn(ROLE_ONLY, recipients)
		self.assertNotIn(IT_AB, _enabled_users_with_role("IT Team", self.emp_c))


class TestHrUserOversight(_Resolvers):
	def test_hr_user_oversees_the_orgs_their_rows_name(self):
		self.assertEqual(
			_oversight_scope(HR_USER_AB), {"hr_manager": False, "companies": sorted([self.a, self.b])}
		)

	def test_hr_user_role_without_a_row_oversees_nothing(self):
		self.assertIsNone(_oversight_scope(ROLE_ONLY))


class TestRecipients(_Resolvers):
	def test_hr_stage_mail_goes_to_hr_managers_covering_the_company(self):
		recipients = _hr_managers(self.emp_b)
		self.assertIn(HR_AB, recipients)
		self.assertIn(LEADER, recipients)
		self.assertNotIn(HR_A, recipients)
		self.assertNotIn(ROLE_ONLY, recipients)

	def test_a_system_manager_holding_the_role_keeps_todays_company_rule(self):
		frappe.get_doc("User", SYSTEM_MANAGER).add_roles("System Manager", "HR Manager")
		frappe.clear_cache(user=SYSTEM_MANAGER)
		clear_role_scope_cache()
		self.assertIn(SYSTEM_MANAGER, _company_role_holders("HR Manager", self.a))
		self.assertNotIn(SYSTEM_MANAGER, _company_role_holders("HR Manager", self.b))

	def test_overdue_summaries_follow_hr_scope(self):
		recipients = reminders._active_hr_managers()
		self.assertIn(LEADER, recipients)
		self.assertIn(HR_A, recipients)
		self.assertNotIn(ROLE_ONLY, recipients)

		items = [
			{"name": name, "employee": employee, "owners": [], "owner_name": name}
			for name, employee in (("in-a", self.emp_a), ("in-b", self.emp_b), ("in-c", self.emp_c))
		]

		def covered(user):
			groups = reminders._summary_owners(items, user, project=lambda item: item["name"])
			return {name for group in groups for name in group["items"]}

		self.assertEqual(covered(LEADER), {"in-a", "in-b", "in-c"})
		self.assertEqual(covered(HR_A), {"in-a"})


def _request_token(name):
	row = frappe.db.get_value("HR Request", name, ["modified", "status"], as_dict=True)
	return {"expected_modified": str(row.modified), "expected_state": row.status}


# --- U4: read, report, project and portal-admin scope ------------------------

PA_AB = "res-pa-ab@helixhr.test"
PA_A = "res-pa-a@helixhr.test"
PA_ALL = "res-pa-all@helixhr.test"
PA_LEFT = "res-pa-left@helixhr.test"
ACCESS_ADMIN = "res-access-admin@helixhr.test"
VIEWER_B = "res-viewer-b@helixhr.test"
REPORTS_AB = "res-reports-ab@helixhr.test"
REPORTS_NO_ROLE = "res-reports-no-role@helixhr.test"
DM_A = "res-dm-a@helixhr.test"
PLAIN = "res-plain@helixhr.test"
EMP_A = "res-emp-a@helixhr.test"
EMP_B = "res-emp-b@helixhr.test"
EMP_C = "res-emp-c@helixhr.test"

_PORTAL_USERS = (PA_AB, PA_A, PA_ALL, PA_LEFT, ACCESS_ADMIN, VIEWER_B, REPORTS_AB, REPORTS_NO_ROLE, DM_A, PLAIN)
_PORTAL_ROLES = (
	PORTAL_ADMIN_ROLE,
	ACCESS_ADMIN_ROLE,
	ORG_VIEWER_ROLE,
	REPORT_MANAGER_ROLE,
	DELIVERY_MANAGER_ROLE,
)
DIRECTORY = "employee_directory"
PROJECT_KEY = "hours_by_project"


def _as(user, fn, *args, **kwargs):
	frappe.set_user(user)
	try:
		return fn(*args, **kwargs)
	finally:
		frappe.set_user("Administrator")


class _PortalScope(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.a = ensure_test_company()
		self.b = ensure_baseline_company()
		self.c = create_test_company(COMPANY_C, "TARC")

		frappe.db.delete("HelixHR Access Assignment", {"user": ["in", list(_PORTAL_USERS)]})
		homes = {PA_AB: self.b, ACCESS_ADMIN: self.b}
		for user in _PORTAL_USERS:
			fields = {"status": "Left", "relieving_date": "2026-01-31"} if user == PA_LEFT else {"status": "Active"}
			make_test_user(user, homes.get(user, self.a), create_user_permission=0, **fields)
			frappe.db.delete("Has Role", {"parent": user, "role": ["in", list(_PORTAL_ROLES)]})
			frappe.clear_cache(user=user)
		clear_role_scope_cache()

		make_assignment(PA_AB, PORTAL_ADMIN_ROLE, [self.a, self.b])
		make_assignment(PA_A, PORTAL_ADMIN_ROLE, [self.a])
		make_assignment(PA_ALL, PORTAL_ADMIN_ROLE, "all")
		make_assignment(PA_LEFT, PORTAL_ADMIN_ROLE, [self.a])
		make_assignment(ACCESS_ADMIN, ACCESS_ADMIN_ROLE, "all")
		make_assignment(VIEWER_B, ORG_VIEWER_ROLE, [self.b])
		make_assignment(REPORTS_AB, REPORT_MANAGER_ROLE, [self.a, self.b])
		# Like a migrated "Reports only" grant: the row, no Frappe role.
		make_assignment(REPORTS_NO_ROLE, REPORT_MANAGER_ROLE, [self.b], add_role=False)
		make_assignment(DM_A, DELIVERY_MANAGER_ROLE, [self.a])

		self.emp_a = make_test_user(EMP_A, self.a)
		self.emp_b = make_test_user(EMP_B, self.b)
		self.emp_c = make_test_user(EMP_C, self.c)

	def tearDown(self):
		frappe.set_user("Administrator")
		clear_role_scope_cache()


class TestPeopleAccessReach(_PortalScope):
	def test_the_reported_bug_portal_admin_on_a_and_b_lists_org_a_employees(self):
		"""A Portal Admin whose own Employee is in B, with rows on A and B,
		reaches A in People & access -- with no Company User Permission."""
		self.assertFalse(frappe.db.exists("User Permission", {"user": PA_AB, "allow": "Company"}))
		found = {row["employee"] for row in _as(PA_AB, search_people_access, query="res-emp")["rows"]}
		self.assertIn(self.emp_a, found)
		self.assertIn(self.emp_b, found)
		self.assertNotIn(self.emp_c, found)

	def test_a_named_org_portal_admin_reaches_only_its_orgs(self):
		found = {row["employee"] for row in _as(PA_A, search_people_access, query="res-emp")["rows"]}
		self.assertEqual(found & {self.emp_a, self.emp_b, self.emp_c}, {self.emp_a})

	def test_manage_access_reaches_every_org_without_portal_admin(self):
		self.assertFalse(can_admin_portal(ACCESS_ADMIN))
		self.assertTrue(can_manage_access(ACCESS_ADMIN))
		self.assertTrue(_as(ACCESS_ADMIN, get_portal_bootstrap)["can_manage_access"])
		found = {row["employee"] for row in _as(ACCESS_ADMIN, search_people_access, query="res-emp")["rows"]}
		self.assertTrue({self.emp_a, self.emp_b, self.emp_c} <= found)

	def test_a_plain_employee_has_no_reach(self):
		self.assertFalse(_as(PLAIN, get_portal_bootstrap)["can_manage_access"])
		with self.assertRaises(frappe.PermissionError):
			_as(PLAIN, search_people_access)


class TestPortalAdminScope(_PortalScope):
	def test_scope_is_the_portal_admin_rows(self):
		self.assertEqual(
			resolve_portal_admin_scope(PA_AB), {"kind": "companies", "companies": sorted([self.a, self.b])}
		)
		self.assertEqual(resolve_portal_admin_scope(PA_ALL)["kind"], "unscoped")
		self.assertTrue(can_admin_portal(PA_A))

	def test_a_left_portal_admin_has_no_admin(self):
		self.assertEqual(resolve_portal_admin_scope(PA_LEFT)["kind"], "none")
		self.assertFalse(can_admin_portal(PA_LEFT))
		self.assertFalse(_as(PA_LEFT, get_portal_bootstrap)["can_admin_portal"])

	def test_a_frappe_role_without_a_row_is_no_portal_admin(self):
		frappe.get_doc("User", PLAIN).add_roles(PORTAL_ADMIN_ROLE)
		frappe.clear_cache(user=PLAIN)
		clear_role_scope_cache()
		self.assertFalse(can_admin_portal(PLAIN))

	def test_only_portal_only_roles_skip_the_frappe_role(self):
		with self.assertRaises(ValueError):
			portal_role_scope(PLAIN, "HR Manager")


class TestGlobalSettingsNeedAllOrgs(_PortalScope):
	"""R7: the report matrix, the email theme and the message templates."""

	def test_a_named_org_portal_admin_is_refused_on_every_global_setting(self):
		key = next(iter(NOTIFICATION_EVENTS))
		self.assertFalse(has_all_orgs_authority(PA_A))
		self.assertFalse(_as(PA_A, get_portal_bootstrap)["can_edit_email_templates"])
		with self.assertRaises(frappe.PermissionError):
			_as(PA_A, save_report_access, [{"key": DIRECTORY, "hr_user_run": 1}])
		with self.assertRaises(frappe.PermissionError):
			_as(PA_A, save_email_theme, brand_color="#123456")
		with self.assertRaises(frappe.PermissionError):
			_as(PA_A, save_message_template, key, subject="Changed")

	def test_all_orgs_portal_admin_and_manage_access_hold_the_authority(self):
		for user in (PA_ALL, ACCESS_ADMIN, "Administrator"):
			self.assertTrue(has_all_orgs_authority(user), user)
			self.assertTrue(_can_edit_email_templates(user), user)
		_as(PA_ALL, save_report_access, [{"key": DIRECTORY, "hr_user_run": 1}])
		self.assertEqual(frappe.db.get_value("HelixHR Report Access", DIRECTORY, "hr_user_run"), 1)


class TestOrgViewer(_PortalScope):
	def test_reads_b_people_and_nothing_else(self):
		self.assertEqual(resolve_read_scope(VIEWER_B), {"kind": "company", "company": self.b})
		self.assertEqual(resolve_admin_scope(VIEWER_B)["kind"], "none")
		found = {row["name"] for row in _as(VIEWER_B, search_people, "res-emp")["people"]}
		self.assertEqual(found & {self.emp_a, self.emp_b, self.emp_c}, {self.emp_b})
		with self.assertRaises(frappe.PermissionError):
			_as(VIEWER_B, get_person, self.emp_c)

	def test_no_edits_no_roster_no_hr_queue_and_masked_fields(self):
		person = _as(VIEWER_B, get_person, self.emp_b)
		self.assertFalse(person["can_edit"])
		for field in ("bank_ac_no", "iban", "pan_number", "passport_number"):
			self.assertNotIn(field, person["employee"])
		with self.assertRaises(frappe.PermissionError):
			_as(VIEWER_B, save_person, self.emp_b, designation=None)
		boot = _as(VIEWER_B, get_portal_bootstrap)
		self.assertTrue(boot["can_see_people"])
		self.assertFalse(boot["can_roster_everyone"])
		self.assertFalse(boot["can_configure"])
		self.assertFalse(_is_hr(VIEWER_B))

	def test_reads_b_projects_and_company_reports(self):
		theirs = make_test_project(self.b, "_Test Res Viewer B Project")
		self.assertEqual(resolve_project_scope(VIEWER_B), {"kind": "none"})
		self.assertEqual(resolve_project_read_scope(VIEWER_B), {"kind": "company", "company": self.b})
		self.assertIn(theirs, {row["name"] for row in _as(VIEWER_B, search_projects)["projects"]})
		access = resolve_report_access(VIEWER_B, DIRECTORY)
		self.assertEqual((access["tier"], access["company"], access["can_export"]), ("org_viewer", self.b, False))

	def test_an_all_orgs_viewer_reads_every_org(self):
		make_assignment(VIEWER_B, ORG_VIEWER_ROLE, "all")
		self.assertEqual(resolve_read_scope(VIEWER_B)["kind"], "unscoped")


class TestReportsRows(_PortalScope):
	def setUp(self):
		super().setUp()
		if not getattr(frappe.local, "response_headers", None):
			from werkzeug.datastructures import Headers

			frappe.local.response_headers = Headers()

	def test_reports_on_a_and_b_without_export_run_b_and_refuse_export(self):
		access = resolve_report_access(REPORTS_AB, DIRECTORY, self.b)
		self.assertTrue(access["can_run"])
		self.assertFalse(access["can_export"])
		self.assertEqual(access["scope"], {"kind": "company", "company": self.b})
		self.assertNotIn(self.c, {row["company"] for row in access["companies"]})
		with self.assertRaises(frappe.PermissionError):
			_as(REPORTS_AB, request_export, DIRECTORY, "csv", filters={"company": self.b})

	def test_export_follows_each_row(self):
		frappe.db.set_value(
			"HelixHR Access Assignment",
			{"user": REPORTS_AB, "role": REPORT_MANAGER_ROLE, "company": self.b},
			"allow_export",
			1,
		)
		clear_role_scope_cache()
		companies = {row["company"]: row["can_export"] for row in resolve_report_access(REPORTS_AB, DIRECTORY)["companies"]}
		self.assertEqual(companies, {self.a: False, self.b: True})

	def test_a_reports_row_without_the_frappe_role_still_runs(self):
		"""The portal-only rule: a migrated "Reports only" grant holder."""
		self.assertNotIn(REPORT_MANAGER_ROLE, frappe.get_roles(REPORTS_NO_ROLE))
		self.assertTrue(_can_run_reports(REPORTS_NO_ROLE))
		self.assertTrue(resolve_report_access(REPORTS_NO_ROLE, DIRECTORY, self.b)["can_run"])


class TestDeliveryManagerOrg(_PortalScope):
	def setUp(self):
		super().setUp()
		self.not_member = make_test_project(self.a, "_Test Res DM A Project")
		self.foreign = make_test_project(self.b, "_Test Res DM B Project", members=[DM_A])

	def test_sees_a_non_member_a_project_in_the_portal_and_never_b(self):
		self.assertEqual(resolve_project_scope(DM_A), {"kind": "company", "company": self.a})
		listed = {row["name"]: row for row in _as(DM_A, search_projects)["projects"]}
		self.assertTrue(listed[self.not_member]["can_edit"])
		self.assertNotIn(self.foreign, listed)
		self.assertEqual(_as(DM_A, get_project, self.not_member)["name"], self.not_member)
		with self.assertRaises(frappe.PermissionError):
			_as(DM_A, get_project, self.foreign)

	def test_project_reports_cover_their_org_and_never_b(self):
		from helixhr.reports import report_project_in_scope

		set_report_access(PROJECT_KEY, dm_run=1)
		access = resolve_report_access(DM_A, PROJECT_KEY)
		self.assertEqual((access["tier"], access["scope"]), ("delivery_manager", {"kind": "company", "company": self.a}))
		self.assertEqual([row["company"] for row in access["companies"]], [self.a])
		self.assertTrue(report_project_in_scope(self.not_member, access["scope"]))
		self.assertFalse(report_project_in_scope(self.foreign, access["scope"]))
		self.assertFalse(resolve_report_access(DM_A, DIRECTORY)["can_run"])

	def test_rest_get_and_list_of_project_and_task_stay_refused(self):
		"""KTD10, named-org and All-orgs alike (test_api_projects covers the
		write routes, for a login holding the Delivery Manager role alone)."""
		from frappe.client import get, get_list

		task = frappe.get_doc({"doctype": "Task", "project": self.not_member, "subject": "_Test Res DM Task"})
		task.insert(ignore_permissions=True)
		for orgs in ([self.a], "all"):
			make_assignment(DM_A, DELIVERY_MANAGER_ROLE, orgs)
			for doctype, name in (("Project", self.not_member), ("Task", task.name)):
				with self.assertRaises(frappe.PermissionError):
					_as(DM_A, get, doctype, name)
				# This login also holds Employee: the list route answers an
				# empty list rather than refusing, the same nothing.
				try:
					rows = _as(DM_A, get_list, doctype, filters={})
				except frappe.PermissionError:
					rows = []
				self.assertEqual(rows, [], (doctype, orgs))


class TestEmployeeOnly(_PortalScope):
	def test_unchanged_everywhere(self):
		for resolver in (resolve_read_scope, resolve_project_scope, resolve_project_read_scope, resolve_portal_admin_scope):
			self.assertEqual(resolver(PLAIN)["kind"], "none", resolver.__name__)
		self.assertFalse(resolve_report_access(PLAIN, DIRECTORY)["can_run"])
		self.assertFalse(can_admin_portal(PLAIN) or can_manage_access(PLAIN) or has_all_orgs_authority(PLAIN))
		boot = _as(PLAIN, get_portal_bootstrap)
		for flag in (
			"can_see_people",
			"can_see_projects",
			"can_run_reports",
			"can_admin_portal",
			"can_manage_access",
			"can_edit_email_templates",
		):
			self.assertFalse(boot[flag], flag)
