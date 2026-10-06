"""HelixHR Portal Admin: a portal-only role that edits the report access
matrix, reads the export log and manages the four portal-only roles -- and
sees no HR data."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from helixhr import preflight
from helixhr.api import (
	get_export_log,
	get_person,
	get_portal_bootstrap,
	get_portal_role_holders,
	get_report_access,
	get_report_catalog,
	run_report,
	save_report_access,
	search_people,
	set_portal_role,
)
from helixhr.tests.utils import (
	_make_role_user,
	ensure_baseline_company,
	ensure_hr_manager_user,
	ensure_test_company,
	make_test_hr_manager_employee,
	make_test_hr_user,
	make_test_portal_admin,
	make_test_user,
	set_report_access,
)
from helixhr.utils import (
	MANAGED_PORTAL_ROLES,
	PORTAL_ADMIN_ROLE,
	can_admin_portal,
	resolve_admin_scope,
	resolve_portal_admin_scope,
	resolve_report_access,
)

KEY = "leave_ledger"
TARGET_USER = "portal-role-target@helixhr.test"
OTHER_COMPANY_TARGET = "portal-role-other@helixhr.test"


def _as(user, fn, *args, **kwargs):
	frappe.set_user(user)
	try:
		return fn(*args, **kwargs)
	finally:
		frappe.set_user("Administrator")


def _roles(user):
	return set(frappe.get_roles(user))


class TestPortalAdminScope(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()

	def test_active_anchor_gives_company_scope_and_no_admin_scope(self):
		_, user = make_test_portal_admin()
		self.assertTrue(can_admin_portal(user))
		self.assertEqual(resolve_portal_admin_scope(user), {"kind": "company", "company": self.company})
		self.assertEqual(resolve_admin_scope(user)["kind"], "none")

	def test_left_anchor_or_no_employee_resolves_to_none(self):
		_, left = _make_role_user(
			"portal-admin-left@helixhr.test", PORTAL_ADMIN_ROLE, status="Left", relieving_date="2026-01-31"
		)
		self.assertEqual(resolve_portal_admin_scope(left)["kind"], "none")
		if not frappe.db.exists("User", "portal-admin-bare@helixhr.test"):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": "portal-admin-bare@helixhr.test",
					"first_name": "Bare",
					"send_welcome_email": 0,
					"roles": [{"doctype": "Has Role", "role": PORTAL_ADMIN_ROLE}],
				}
			).insert(ignore_permissions=True)
		self.assertEqual(resolve_portal_admin_scope("portal-admin-bare@helixhr.test")["kind"], "none")

	def test_hr_manager_keeps_admin_scope_and_hr_user_has_none(self):
		self.assertEqual(resolve_portal_admin_scope(ensure_hr_manager_user())["kind"], "unscoped")
		_, hr_manager = make_test_hr_manager_employee()
		self.assertEqual(resolve_portal_admin_scope(hr_manager)["kind"], "company")
		_, hr_user = make_test_hr_user()
		self.assertFalse(can_admin_portal(hr_user))
		self.assertEqual(resolve_portal_admin_scope(hr_user)["kind"], "none")


class TestPortalAdminSeesNoHrData(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.employee, self.user = make_test_portal_admin()
		set_report_access(KEY, hr_user_run=1, hr_user_export=1)

	def test_cannot_run_reports_or_reach_people(self):
		access = resolve_report_access(self.user, KEY)
		self.assertFalse(access["can_run"] or access["can_export"])
		self.assertEqual(_as(self.user, get_report_catalog), [])
		with self.assertRaises(frappe.PermissionError):
			_as(self.user, run_report, KEY, {"from_date": "2000-01-01", "to_date": "2100-01-01"})
		with self.assertRaises(frappe.PermissionError):
			_as(self.user, search_people, "a")
		with self.assertRaises(frappe.PermissionError):
			_as(self.user, get_person, self.employee)

	def test_bootstrap_flags(self):
		boot = _as(self.user, get_portal_bootstrap)
		self.assertTrue(boot["can_admin_portal"])
		self.assertFalse(boot["can_configure"])
		self.assertFalse(boot["can_run_reports"])
		self.assertFalse(boot["can_see_people"])
		_, hr_user = make_test_hr_user()
		self.assertFalse(_as(hr_user, get_portal_bootstrap)["can_admin_portal"])
		# Plan 2026-10-06-001 U3: HR Manager is refused alongside HR User.
		self.assertFalse(_as(ensure_hr_manager_user(), get_portal_bootstrap)["can_admin_portal"])

	def test_edits_the_access_matrix(self):
		rows = _as(self.user, get_report_access)
		self.assertTrue(any(row["key"] == KEY for row in rows))
		_as(self.user, save_report_access, [{"key": KEY, "hr_user_run": 1, "hr_user_export": 0}])
		self.assertEqual(frappe.db.get_value("HelixHR Report Access", KEY, "hr_user_export"), 0)
		_, hr_user = make_test_hr_user()
		with self.assertRaises(frappe.PermissionError):
			_as(hr_user, save_report_access, [{"key": KEY, "hr_user_run": 1}])

	def test_export_log_is_company_scoped(self):
		other = ensure_baseline_company()
		mine = frappe.get_doc(
			{
				"doctype": "HelixHR Report Export",
				"report_key": KEY,
				"format": "csv",
				"company": ensure_test_company(),
			}
		).insert(ignore_permissions=True)
		theirs = frappe.get_doc(
			{"doctype": "HelixHR Report Export", "report_key": KEY, "format": "csv", "company": other}
		).insert(ignore_permissions=True)
		names = {row.name for row in _as(self.user, get_export_log, page_length=200)["rows"]}
		self.assertIn(mine.name, names)
		self.assertNotIn(theirs.name, names)

		_, left = _make_role_user(
			"portal-admin-left@helixhr.test", PORTAL_ADMIN_ROLE, status="Left", relieving_date="2026-01-31"
		)
		self.assertEqual(_as(left, get_export_log)["rows"], [])
		_, hr_user = make_test_hr_user()
		with self.assertRaises(frappe.PermissionError):
			_as(hr_user, get_export_log)


class TestPortalRoleManagement(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		_, self.admin = make_test_portal_admin()
		self.target = make_test_user(TARGET_USER, self.company)
		self.other_target = make_test_user(OTHER_COMPANY_TARGET, ensure_baseline_company())

	def test_grant_and_remove_an_allowed_role_with_an_audit_comment(self):
		role = "HelixHR Report Manager"
		out = _as(self.admin, set_portal_role, self.target, role, 1)
		self.assertTrue(out["roles"][role])
		self.assertIn(role, _roles(TARGET_USER))
		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{
					"reference_doctype": "User",
					"reference_name": TARGET_USER,
					"comment_type": "Info",
					"content": ["like", f"%granted {role}%"],
				},
			)
		)
		listed = _as(self.admin, get_portal_role_holders)["rows"]
		self.assertIn(self.target, [row["employee"] for row in listed])

		_as(self.admin, set_portal_role, self.target, role, 0)
		self.assertNotIn(role, _roles(TARGET_USER))

	def test_every_managed_role_is_grantable(self):
		for role in MANAGED_PORTAL_ROLES:
			_as(self.admin, set_portal_role, self.target, role, 1)
			self.assertIn(role, _roles(TARGET_USER))
			_as(self.admin, set_portal_role, self.target, role, 0)

	def test_roles_outside_the_allow_list_are_refused(self):
		for role in ("HR Manager", "HR User", "System Manager", PORTAL_ADMIN_ROLE, "Employee", ""):
			with self.assertRaises(frappe.PermissionError):
				_as(self.admin, set_portal_role, self.target, role, 1)
		self.assertFalse(_roles(TARGET_USER) & {"HR Manager", "HR User", "System Manager", PORTAL_ADMIN_ROLE})

	def test_out_of_company_or_missing_target_is_refused_alike(self):
		for employee in (self.other_target, "HR-EMP-DOES-NOT-EXIST"):
			with self.assertRaises(frappe.PermissionError) as caught:
				_as(self.admin, set_portal_role, employee, "IT Team", 1)
			self.assertIn("not authorised", str(caught.exception))
		self.assertNotIn("IT Team", _roles(OTHER_COMPANY_TARGET))
		names = [row["employee"] for row in _as(self.admin, get_portal_role_holders, "portal-role")["rows"]]
		self.assertNotIn(self.other_target, names)

	def test_own_account_is_refused(self):
		own = frappe.db.get_value("Employee", {"user_id": self.admin}, "name")
		with self.assertRaises(frappe.PermissionError):
			_as(self.admin, set_portal_role, own, "HelixHR Report Manager", 1)

	def test_hr_user_and_plain_employee_are_refused(self):
		_, hr_user = make_test_hr_user()
		for user in (hr_user, TARGET_USER):
			with self.assertRaises(frappe.PermissionError):
				_as(user, set_portal_role, self.target, "IT Team", 1)
			with self.assertRaises(frappe.PermissionError):
				_as(user, get_portal_role_holders)

	def test_hr_manager_is_refused_too(self):
		"""Plan 2026-10-06-001 U3 (R5/R6): administering portal settings moved
		to Portal Admin and System Manager; HR Manager no longer holds it."""
		hr_manager = ensure_hr_manager_user()
		with self.assertRaises(frappe.PermissionError):
			_as(hr_manager, set_portal_role, self.other_target, "IT Team", 1)
		with self.assertRaises(frappe.PermissionError):
			_as(hr_manager, get_portal_role_holders)
		self.assertNotIn("IT Team", _roles(OTHER_COMPANY_TARGET))


class TestPortalAdminPreflight(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")

	def test_passes_on_a_clean_site_and_is_a_fixture_role(self):
		self.assertEqual(preflight.check_portal_admin_role()["status"], preflight.PASS)
		self.assertIn(preflight.PORTAL_ADMIN, preflight._fixture_roles())

	def test_desk_access_fails(self):
		real_get_value = frappe.db.get_value

		def _desk_role(doctype, filters=None, *args, **kwargs):
			if doctype == "Role" and filters == preflight.PORTAL_ADMIN:
				return frappe._dict(desk_access=1, is_custom=0)
			return real_get_value(doctype, filters, *args, **kwargs)

		with patch.object(preflight.frappe.db, "get_value", side_effect=_desk_role):
			result = preflight.check_portal_admin_role()
		self.assertEqual(result["status"], preflight.FAIL)
		self.assertIn("desk_access", result["detail"])

	def test_any_report_export_write_or_create_grant_fails(self):
		from frappe.permissions import add_permission

		add_permission("Employee", preflight.PORTAL_ADMIN, 0)
		name = frappe.db.get_value(
			"Custom DocPerm", {"parent": "Employee", "role": preflight.PORTAL_ADMIN, "permlevel": 0}
		)
		self.addCleanup(frappe.delete_doc, "Custom DocPerm", name, force=True)
		self.addCleanup(frappe.clear_cache, doctype="Employee")
		for right in ("report", "export", "write", "create"):
			frappe.db.set_value(
				"Custom DocPerm", name, {"report": 0, "export": 0, "write": 0, "create": 0, right: 1}
			)
			result = preflight.check_portal_admin_role()
			self.assertEqual(result["status"], preflight.FAIL)
			self.assertIn(right, result["detail"])

	def test_timesheet_guard_covers_portal_admin(self):
		from frappe.permissions import add_permission

		add_permission("Timesheet", preflight.PORTAL_ADMIN, 0)
		name = frappe.db.get_value(
			"Custom DocPerm", {"parent": "Timesheet", "role": preflight.PORTAL_ADMIN, "permlevel": 0}
		)
		self.addCleanup(frappe.delete_doc, "Custom DocPerm", name, force=True)
		self.addCleanup(frappe.clear_cache, doctype="Timesheet")
		frappe.db.set_value("Custom DocPerm", name, "report", 1)
		result = preflight.check_no_timesheet_report_permission()
		self.assertEqual(result["status"], preflight.FAIL)
		self.assertIn(preflight.PORTAL_ADMIN, result["detail"])
