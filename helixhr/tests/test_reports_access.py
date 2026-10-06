"""Plan 2026-10-04-001 U1: the Report Manager role, the HelixHR Report Access
matrix, `resolve_report_access`, and the preflight guards around them."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from helixhr import preflight
from helixhr.api import search_report_options
from helixhr.tests.utils import (
	_make_role_user,
	ensure_baseline_company,
	ensure_hr_manager_user,
	ensure_test_company,
	make_test_delivery_manager,
	make_test_hr_manager_employee,
	make_test_hr_user,
	make_test_portal_admin,
	make_test_project,
	make_test_user,
	make_test_user_without_employee,
	set_report_access,
)
from helixhr.utils import resolve_admin_scope, resolve_report_access

COMPANY_ONLY_KEY = "leave_ledger"
PROJECT_KEY = "hours_by_project"


class TestResolveReportAccess(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_anchored_hr_manager_is_company_scoped_with_full_rights(self):
		_, user = make_test_hr_manager_employee()
		access = resolve_report_access(user, COMPANY_ONLY_KEY)
		self.assertEqual(access["scope"], {"kind": "company", "company": self.company})
		self.assertTrue(access["can_run"] and access["can_export"])
		self.assertEqual(access["tier"], "admin")

	def test_anchorless_hr_manager_is_unscoped(self):
		access = resolve_report_access(ensure_hr_manager_user(), PROJECT_KEY)
		self.assertEqual(access["scope"]["kind"], "unscoped")
		self.assertTrue(access["can_export"])

	def test_report_manager_with_active_employee_gets_company_and_full_rights(self):
		other = ensure_baseline_company()
		_, user = _make_role_user("report-manager-b@helixhr.test", "HelixHR Report Manager", other)
		for key in (COMPANY_ONLY_KEY, PROJECT_KEY):
			access = resolve_report_access(user, key)
			self.assertEqual(access["scope"], {"kind": "company", "company": other})
			self.assertTrue(access["can_run"] and access["can_export"])
			self.assertEqual(access["tier"], "report_manager")

	def test_report_manager_left_or_without_employee_resolves_to_none(self):
		_, left = _make_role_user(
			"report-manager-left@helixhr.test",
			"HelixHR Report Manager",
			status="Left",
			relieving_date="2026-01-31",
		)
		self.assertFalse(resolve_report_access(left, COMPANY_ONLY_KEY)["can_run"])

		orphan = make_test_user_without_employee()
		doc = frappe.get_doc("User", orphan)
		doc.append_roles("HelixHR Report Manager")
		doc.save(ignore_permissions=True)
		self.assertFalse(resolve_report_access(orphan, COMPANY_ONLY_KEY)["can_run"])

	def test_hr_user_run_only_grant(self):
		_, user = make_test_hr_user()
		set_report_access(COMPANY_ONLY_KEY, hr_user_run=1)
		access = resolve_report_access(user, COMPANY_ONLY_KEY)
		self.assertTrue(access["can_run"])
		self.assertFalse(access["can_export"])
		self.assertEqual(access["scope"], {"kind": "company", "company": self.company})
		self.assertEqual(access["export_scope"], {"kind": "none"})

	def test_hr_user_without_a_row_is_denied(self):
		_, user = make_test_hr_user()
		frappe.db.delete("HelixHR Report Access", {"name": COMPANY_ONLY_KEY})
		access = resolve_report_access(user, COMPANY_ONLY_KEY)
		self.assertFalse(access["can_run"] or access["can_export"])

	def test_delivery_manager_granted_a_project_entry_gets_project_scope(self):
		_, user = make_test_delivery_manager()
		set_report_access(PROJECT_KEY, dm_run=1, dm_export=1)
		access = resolve_report_access(user, PROJECT_KEY)
		self.assertEqual(access["scope"], {"kind": "assigned", "user": user})
		self.assertTrue(access["can_export"])
		self.assertFalse(resolve_report_access(user, COMPANY_ONLY_KEY)["can_run"])

	def test_delivery_manager_grant_on_company_only_entry_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			set_report_access(COMPANY_ONLY_KEY, dm_run=1)

	def test_export_without_run_is_invalid(self):
		with self.assertRaises(frappe.ValidationError):
			set_report_access(COMPANY_ONLY_KEY, hr_user_export=1)
		with self.assertRaises(frappe.ValidationError):
			set_report_access(PROJECT_KEY, dm_export=1)

	def test_unknown_report_key_row_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			set_report_access("not_a_report", hr_user_run=1)

	def test_hr_user_plus_delivery_manager_scope_is_per_report(self):
		"""Resolved decision 2: scope comes only from tiers granting the
		report. DM-only grant -> project scope; both -> company scope."""
		_, user = _make_role_user("hr-user-dm@helixhr.test", "HR User")
		doc = frappe.get_doc("User", user)
		doc.append_roles("HelixHR Delivery Manager")
		doc.save(ignore_permissions=True)

		set_report_access(PROJECT_KEY, dm_run=1)
		access = resolve_report_access(user, PROJECT_KEY)
		self.assertEqual(access["scope"]["kind"], "assigned")
		self.assertEqual(access["tier"], "delivery_manager")

		set_report_access(PROJECT_KEY, hr_user_run=1, dm_run=1, dm_export=1)
		access = resolve_report_access(user, PROJECT_KEY)
		self.assertEqual(access["scope"], {"kind": "company", "company": self.company})
		self.assertTrue(access["can_export"])
		# Export comes only from the Delivery Manager tier, so it is project-scoped.
		self.assertEqual(access["export_scope"]["kind"], "assigned")

	def test_unknown_and_denied_keys_are_identical(self):
		_, user = make_test_hr_user()
		frappe.db.delete("HelixHR Report Access", {"name": COMPANY_ONLY_KEY})
		self.assertEqual(
			resolve_report_access(user, COMPANY_ONLY_KEY), resolve_report_access(user, "no_such_report")
		)
		self.assertEqual(
			resolve_report_access(user, "no_such_report"), resolve_report_access(user, ["in", "x"])
		)

	def test_hr_user_gains_no_people_reach(self):
		"""R24: HR User's new reach is reports only."""
		from helixhr.api import get_person

		employee, user = make_test_hr_user()
		self.assertEqual(resolve_admin_scope(user)["kind"], "none")
		frappe.set_user(user)
		with self.assertRaises(frappe.PermissionError):
			get_person(employee)


class TestReportOptions(IntegrationTestCase):
	"""U3: `search_report_options` serves only in-scope values, only for
	filter types the entry declares. U4: the catalog and nav flag agree with
	`resolve_report_access`."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.other_company = ensure_baseline_company()
		self.mine = make_test_user("opt-search-mine@helixhr.test", self.company)
		self.theirs = make_test_user("opt-search-theirs@helixhr.test", self.other_company)
		self.left = make_test_user(
			"opt-search-left@helixhr.test", self.company, status="Left", relieving_date="2026-01-31"
		)

	def tearDown(self):
		frappe.set_user("Administrator")

	def _search(self, user, key, filter, **kwargs):
		frappe.set_user(user)
		try:
			return search_report_options(key, filter, **kwargs)
		finally:
			frappe.set_user("Administrator")

	def test_hr_user_gets_only_own_company_active_employees_for_a_granted_report(self):
		_, user = make_test_hr_user()
		set_report_access(COMPANY_ONLY_KEY, hr_user_run=1)
		values = {o["value"] for o in self._search(user, COMPANY_ONLY_KEY, "employee", query="opt-search")}
		self.assertIn(self.mine, values)
		self.assertNotIn(self.theirs, values)
		self.assertNotIn(self.left, values)

		frappe.db.delete("HelixHR Report Access", {"name": COMPANY_ONLY_KEY})
		with self.assertRaises(frappe.PermissionError):
			self._search(user, COMPANY_ONLY_KEY, "employee", query="opt-search")
		with self.assertRaises(frappe.PermissionError):
			self._search(user, "no_such_report", "employee", query="opt-search")

	def test_value_lookup_resolves_a_label_only_inside_scope(self):
		_, user = make_test_hr_user()
		set_report_access(COMPANY_ONLY_KEY, hr_user_run=1)
		[option] = self._search(user, COMPANY_ONLY_KEY, "employee", value=self.mine)
		self.assertEqual(option["value"], self.mine)
		self.assertTrue(option["label"])
		self.assertEqual(self._search(user, COMPANY_ONLY_KEY, "employee", value=self.theirs), [])
		self.assertEqual(self._search(user, COMPANY_ONLY_KEY, "employee", value=["in", [self.theirs]]), [])

	def test_delivery_manager_sees_only_member_projects_tasks_and_members(self):
		_, dm = make_test_delivery_manager()
		set_report_access(PROJECT_KEY, dm_run=1)
		member = make_test_project(
			self.company, "_Test Opt Member Project", members=[dm, "opt-search-mine@helixhr.test"]
		)
		other = make_test_project(self.company, "_Test Opt Other Project")
		for project in (member, other):
			if not frappe.db.exists("Task", {"project": project, "subject": "_Test Opt Task"}):
				frappe.get_doc(
					{"doctype": "Task", "project": project, "subject": "_Test Opt Task", "status": "Open"}
				).insert(ignore_permissions=True)

		projects = {o["value"] for o in self._search(dm, PROJECT_KEY, "project", query="_Test Opt")}
		self.assertEqual(projects, {member})

		tasks = self._search(dm, PROJECT_KEY, "task", query="_Test Opt Task")
		self.assertEqual({o["description"] for o in tasks}, {member})
		# A dependent picker narrowed to an out-of-scope project gets nothing.
		self.assertEqual(
			self._search(dm, PROJECT_KEY, "task", query="_Test Opt Task", context={"project": other}), []
		)

		employees = {o["value"] for o in self._search(dm, PROJECT_KEY, "employee", query="opt-search")}
		self.assertEqual(employees, {self.mine})

	def test_undeclared_or_pickerless_filters_are_refused(self):
		_, user = make_test_hr_manager_employee()
		with self.assertRaises(frappe.ValidationError):
			self._search(user, COMPANY_ONLY_KEY, "project", query="ab")
		with self.assertRaises(frappe.ValidationError):
			self._search(user, COMPANY_ONLY_KEY, "from_date", query="20")

	def test_empty_query_browses_open_projects_and_a_query_finds_completed_ones(self):
		"""Plan 2026-10-05-001 U7 (KTD7): pickers list current options on focus."""
		_, user = make_test_hr_manager_employee()
		open_project = make_test_project(self.company, "_Test Opt Browse Open")
		done = make_test_project(self.company, "_Test Opt Browse Done")
		frappe.db.set_value("Project", done, "status", "Completed")
		theirs = make_test_project(self.other_company, "_Test Opt Browse Theirs")

		browse = self._search(user, PROJECT_KEY, "project", query="")
		values = {o["value"] for o in browse}
		self.assertIn(open_project, values)
		self.assertNotIn(done, values)
		self.assertNotIn(theirs, values)
		self.assertLessEqual(len(browse), 20)
		self.assertEqual(
			{frappe.db.get_value("Project", v, "status") for v in values}, {"Open"} if values else set()
		)

		found = [o["value"] for o in self._search(user, PROJECT_KEY, "project", query="_Test Opt Browse")]
		self.assertIn(done, found)
		self.assertNotIn(theirs, found)
		# Active matches list before inactive ones.
		self.assertLess(found.index(open_project), found.index(done))

	def test_task_browse_with_a_project_lists_only_that_projects_open_tasks(self):
		_, user = make_test_hr_manager_employee()
		project = make_test_project(self.company, "_Test Opt Task Browse A")
		other = make_test_project(self.company, "_Test Opt Task Browse B")
		names = {}
		for parent, subject, status in (
			(project, "_Test Opt Browse T1", "Open"),
			(project, "_Test Opt Browse T2", "Completed"),
			(other, "_Test Opt Browse T3", "Open"),
		):
			names[subject] = frappe.db.get_value("Task", {"project": parent, "subject": subject}) or (
				frappe.get_doc(
					{"doctype": "Task", "project": parent, "subject": subject, "status": status}
				).insert(ignore_permissions=True).name
			)
		values = {
			o["value"] for o in self._search(user, PROJECT_KEY, "task", query="", context={"project": project})
		}
		self.assertEqual(values, {names["_Test Opt Browse T1"]})

	def test_results_are_capped_and_one_character_searches(self):
		from helixhr import reports

		_, user = make_test_hr_manager_employee()
		self.assertLessEqual(len(self._search(user, COMPANY_ONLY_KEY, "employee", query="o")), 20)
		with patch.object(reports, "OPTIONS_LIMIT", 1):
			self.assertEqual(len(self._search(user, COMPANY_ONLY_KEY, "employee", query="opt-search")), 1)
		self.assertLessEqual(len(self._search(user, COMPANY_ONLY_KEY, "employee", query="es")), 20)

	def test_select_link_values_must_exist(self):
		from helixhr import reports

		entry = reports.get_entry("employee_directory")
		scope = {"kind": "company", "company": self.company}
		clean, removed = reports.resolve_filters(entry, {"designation": "No Such Designation"}, scope)
		self.assertEqual(removed, ["designation"])
		self.assertNotIn("designation", clean)

	def test_catalog_and_nav_flag_follow_access(self):
		from helixhr.api import get_portal_bootstrap, get_report_catalog

		_, hr_user = make_test_hr_user()
		set_report_access(COMPANY_ONLY_KEY, hr_user_run=1, hr_user_export=1)
		set_report_access(PROJECT_KEY, hr_user_run=0)
		_, dm = make_test_delivery_manager()
		set_report_access(PROJECT_KEY, dm_run=1)
		employee_user = "opt-search-mine@helixhr.test"

		def as_user(user, fn):
			frappe.set_user(user)
			try:
				return fn()
			finally:
				frappe.set_user("Administrator")

		hr_catalog = {e["key"]: e for e in as_user(hr_user, get_report_catalog)}
		self.assertIn(COMPANY_ONLY_KEY, hr_catalog)
		self.assertNotIn(PROJECT_KEY, hr_catalog)
		self.assertTrue(hr_catalog[COMPANY_ONLY_KEY]["can_export"])
		self.assertTrue(as_user(hr_user, get_portal_bootstrap)["can_run_reports"])

		# Only the keys this test controls: other entries carry seeded
		# Delivery Manager default grants (e.g. project_timesheet).
		dm_catalog = {e["key"]: e for e in as_user(dm, get_report_catalog)}
		self.assertIn(PROJECT_KEY, dm_catalog)
		self.assertNotIn(COMPANY_ONLY_KEY, dm_catalog)
		self.assertFalse(dm_catalog[PROJECT_KEY]["can_open_in_desk"])
		self.assertTrue(as_user(dm, get_portal_bootstrap)["can_run_reports"])

		self.assertEqual(as_user(employee_user, get_report_catalog), [])
		self.assertFalse(as_user(employee_user, get_portal_bootstrap)["can_run_reports"])


class TestReportAccessPreflight(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")

	def test_report_manager_role_passes_on_a_clean_site(self):
		self.assertEqual(preflight.check_report_manager_role()["status"], preflight.PASS)

	def test_report_manager_desk_access_fails(self):
		real_get_value = frappe.db.get_value

		def _desk_role(doctype, filters=None, *args, **kwargs):
			if doctype == "Role" and filters == preflight.REPORT_MANAGER:
				return frappe._dict(desk_access=1, is_custom=0)
			return real_get_value(doctype, filters, *args, **kwargs)

		with patch.object(preflight.frappe.db, "get_value", side_effect=_desk_role):
			result = preflight.check_report_manager_role()
		self.assertEqual(result["status"], preflight.FAIL)
		self.assertIn("desk_access", result["detail"])

	def test_report_manager_report_or_export_grant_fails(self):
		from frappe.permissions import add_permission

		add_permission("Employee", preflight.REPORT_MANAGER, 0)
		name = frappe.db.get_value(
			"Custom DocPerm", {"parent": "Employee", "role": preflight.REPORT_MANAGER, "permlevel": 0}
		)
		self.addCleanup(frappe.delete_doc, "Custom DocPerm", name, force=True)
		self.addCleanup(frappe.clear_cache, doctype="Employee")
		for right in ("report", "export"):
			frappe.db.set_value("Custom DocPerm", name, {"report": 0, "export": 0, right: 1})
			result = preflight.check_report_manager_role()
			self.assertEqual(result["status"], preflight.FAIL)
			self.assertIn(right, result["detail"])

	def test_timesheet_guard_covers_every_fixture_role(self):
		"""The widened guard catches Notification and Report Manager, which
		the old two-role list missed."""
		from frappe.permissions import add_permission

		self.assertIn(preflight.NOTIFICATION_MANAGER, preflight._fixture_roles())
		self.assertIn(preflight.REPORT_MANAGER, preflight._fixture_roles())
		for role in (preflight.NOTIFICATION_MANAGER, preflight.REPORT_MANAGER):
			add_permission("Timesheet", role, 0)
			name = frappe.db.get_value(
				"Custom DocPerm", {"parent": "Timesheet", "role": role, "permlevel": 0}
			)
			frappe.db.set_value("Custom DocPerm", name, "report", 1)
			frappe.clear_cache(doctype="Timesheet")
			result = preflight.check_no_timesheet_report_permission()
			frappe.delete_doc("Custom DocPerm", name, force=True)
			frappe.clear_cache(doctype="Timesheet")
			self.assertEqual(result["status"], preflight.FAIL)
			self.assertIn(role, result["detail"])

	def test_report_manager_role_adds_no_desk_report_reach(self):
		"""The role alone (no Employee role, which HRMS grants its own report
		rights) cannot run an HRMS report through Desk's endpoint."""
		from frappe.desk.query_report import run

		user = "report-manager-only@helixhr.test"
		if not frappe.db.exists("User", user):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": user,
					"first_name": "Report only",
					"send_welcome_email": 0,
					"roles": [{"doctype": "Has Role", "role": "HelixHR Report Manager"}],
				}
			).insert(ignore_permissions=True)
		frappe.set_user(user)
		with self.assertRaises(frappe.PermissionError):
			run(
				"Employee Leave Balance",
				filters={
					"company": ensure_test_company(),
					"from_date": "2026-01-01",
					"to_date": "2026-01-31",
				},
			)


class TestReportAccessMatrix(IntegrationTestCase):
	"""U6: `get_report_access` / `save_report_access`, Portal Admin and System
	Manager only (HR Manager is refused since plan 2026-10-06-001 U3),
	all-or-nothing batches, and immediate effect."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		_, self.hr_manager = make_test_hr_manager_employee()
		_, self.hr_user = make_test_hr_user()
		_, self.portal_admin = make_test_portal_admin()

	def tearDown(self):
		frappe.set_user("Administrator")

	def _as(self, user, fn, *args, **kwargs):
		frappe.set_user(user)
		try:
			return fn(*args, **kwargs)
		finally:
			frappe.set_user("Administrator")

	def test_grant_and_revoke_take_effect_on_the_next_call(self):
		from helixhr.api import get_report_catalog, run_report, save_report_access

		self._as(
			self.portal_admin,
			save_report_access,
			[{"key": COMPANY_ONLY_KEY, "hr_user_run": 1, "hr_user_export": 1}],
		)
		catalog = {e["key"]: e for e in self._as(self.hr_user, get_report_catalog)}
		self.assertTrue(catalog[COMPANY_ONLY_KEY]["can_export"])

		self._as(self.portal_admin, save_report_access, [{"key": COMPANY_ONLY_KEY, "hr_user_run": 0}])
		self.assertNotIn(COMPANY_ONLY_KEY, {e["key"] for e in self._as(self.hr_user, get_report_catalog)})
		with self.assertRaises(frappe.PermissionError):
			self._as(self.hr_user, run_report, COMPANY_ONLY_KEY)

	def test_non_admin_roles_are_refused(self):
		from helixhr.api import get_report_access, save_report_access
		from helixhr.tests.utils import make_test_report_manager

		_, report_manager = make_test_report_manager()
		# HR Manager joins the refused set in plan 2026-10-06-001 U3 (R6).
		for user in (self.hr_user, report_manager, self.hr_manager):
			with self.assertRaises(frappe.PermissionError):
				self._as(user, save_report_access, [{"key": COMPANY_ONLY_KEY, "hr_user_run": 1}])
			with self.assertRaises(frappe.PermissionError):
				self._as(user, get_report_access)

	def test_a_bad_row_rejects_the_whole_batch(self):
		from helixhr.api import save_report_access

		set_report_access(COMPANY_ONLY_KEY, hr_user_run=0)
		for bad in (
			{"key": "not_a_report", "hr_user_run": 1},
			{"key": "employee_directory", "dm_run": 1},
			{"key": "employee_directory", "hr_user_export": 1},
		):
			with self.assertRaises(frappe.ValidationError):
				self._as(
					self.portal_admin, save_report_access, [{"key": COMPANY_ONLY_KEY, "hr_user_run": 1}, bad]
				)
			self.assertEqual(frappe.db.get_value("HelixHR Report Access", COMPANY_ONLY_KEY, "hr_user_run"), 0)

	def test_matrix_lists_every_entry_and_marks_dm_forbidden_cells(self):
		from helixhr import reports
		from helixhr.api import get_report_access

		rows = {row["key"]: row for row in self._as(self.portal_admin, get_report_access)}
		self.assertEqual(set(rows), {entry["key"] for entry in reports.CATALOG})
		self.assertTrue(rows[PROJECT_KEY]["dm_allowed"])
		self.assertFalse(rows[COMPANY_ONLY_KEY]["dm_allowed"])

	def test_system_manager_is_allowed(self):
		"""The one non-Portal-Admin allowed role has a positive path too."""
		from helixhr.api import get_report_access

		_, system_manager = _make_role_user("matrix-system-manager@helixhr.test", "System Manager")
		rows = self._as(system_manager, get_report_access)
		self.assertTrue(rows)

	def test_hr_manager_has_no_docperm_on_the_matrix(self):
		"""Review fix (security, plan U3): the portal gate is not the only
		door. A standard DocPerm would let HR Manager write the matrix through
		`/api/resource/HelixHR Report Access/<key>`, off the endpoint entirely;
		`apply_permission_deltas` removes it."""
		self.assertFalse(frappe.has_permission("HelixHR Report Access", "write", user=self.hr_manager))
		self.assertFalse(frappe.has_permission("HelixHR Report Access", "create", user=self.hr_manager))
