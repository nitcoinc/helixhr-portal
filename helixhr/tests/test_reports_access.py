"""Plan 2026-10-04-001 U1: the Report Manager role, the HelixHR Report Access
matrix, `resolve_report_access`, and the preflight guards around them."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from helixhr import preflight
from helixhr.tests.utils import (
	_make_role_user,
	ensure_baseline_company,
	ensure_hr_manager_user,
	ensure_test_company,
	make_test_delivery_manager,
	make_test_hr_manager_employee,
	make_test_hr_user,
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
