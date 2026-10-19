"""Plan 2026-10-18-001 U5 (R26, R27), Release 2 half: the pure mapping from
today's access to assignment rows, the read-only dry-run and the resolver
snapshot.

`plan_rows` takes a plain state dict, so the mapping table is tested without
a database. The dry-run and snapshot are tested on the site.
"""

import json
import os
import stat

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

from helixhr.access_migration import SECURITY_FIXES, dry_run, plan_rows

A, B, C = "Org A", "Org B", "Org C"
COMPANIES = [A, B, C]


def _user(roles, employees=(), **extra):
	return {
		"enabled": True,
		"roles": set(roles),
		"employees": list(employees),
		"company_permissions": [],
		"grant": None,
		"project_orgs": {},
		**extra,
	}


def _active(company, name="EMP-1"):
	return {"name": name, "company": company, "status": "Active"}


def _left(company, name="EMP-0"):
	return {"name": name, "company": company, "status": "Left"}


def _plan(**users):
	state = {"companies": COMPANIES, "org_projects": {A: 5, B: 3, C: 0}, "users": users}
	return plan_rows(state)


def _rows(plan, user):
	return {
		(row["role"], "ALL" if row["all_orgs"] else row["company"]): (row["enabled"], row["allow_export"])
		for row in plan[user]["rows"]
	}


class TestPlanRows(UnitTestCase):
	def test_every_row_is_migrated(self):
		plan = _plan(u=_user({"HR Manager"}, [_active(A)]))
		self.assertTrue(all(row["origin"] == "migrated" for row in plan["u"]["rows"]))

	# --- HR roles -------------------------------------------------------------

	def test_hr_manager_with_employee_gets_their_company(self):
		plan = _plan(u=_user({"HR Manager"}, [_active(A)]))
		self.assertEqual(_rows(plan, "u"), {("HR Manager", A): (1, 0)})
		self.assertEqual((plan["u"]["employee"], plan["u"]["home_company"]), ("EMP-1", A))

	def test_hr_roles_with_no_employee_get_all_orgs_and_an_empty_anchor(self):
		plan = _plan(m=_user({"HR Manager"}), h=_user({"HR User"}), p=_user({"HelixHR Portal Admin"}))
		self.assertEqual(_rows(plan, "m"), {("HR Manager", "ALL"): (1, 0)})
		self.assertEqual(_rows(plan, "h"), {("HR User", "ALL"): (1, 0)})
		self.assertEqual(_rows(plan, "p"), {("HelixHR Portal Admin", "ALL"): (1, 0)})
		for user in ("m", "h", "p"):
			self.assertIsNone(plan[user]["employee"])
		# HR User with no Employee had no portal reach: listed as widened.
		self.assertTrue(plan["h"]["widened"])
		self.assertFalse(plan["m"]["widened"])

	def test_a_rehired_login_anchors_on_the_active_row(self):
		plan = _plan(u=_user({"HR Manager"}, [_left(B, "EMP-NEW"), _active(A, "EMP-OLD")]))
		self.assertEqual(plan["u"]["employee"], "EMP-OLD")
		self.assertEqual(_rows(plan, "u"), {("HR Manager", A): (1, 0)})

	def test_an_inactive_anchor_writes_disabled_rows_with_a_reason(self):
		plan = _plan(u=_user({"HR Manager", "IT Team"}, [_left(A)]))
		self.assertEqual(_rows(plan, "u"), {("HR Manager", A): (0, 0), ("IT Team", A): (0, 0)})
		self.assertTrue(all(row["disabled_reason"] for row in plan["u"]["rows"]))

	# --- portal roles -----------------------------------------------------------

	def test_portal_admin_gets_home_plus_company_permissions(self):
		plan = _plan(
			u=_user({"HelixHR Portal Admin"}, [_active(A)], company_permissions=[B, B, A, "Deleted Co"])
		)
		self.assertEqual(
			_rows(plan, "u"), {("HelixHR Portal Admin", A): (1, 0), ("HelixHR Portal Admin", B): (1, 0)}
		)
		# Write in B is new; the global matrix and other companies' templates
		# go (security fix).
		self.assertTrue(any(B in note for note in plan["u"]["widened"]))
		self.assertIn("portal_admin_scope", {note["fix"] for note in plan["u"]["narrowed"]})

	def test_a_left_portal_admin_loses_admin_as_a_security_fix(self):
		plan = _plan(u=_user({"HelixHR Portal Admin"}, [_left(A)]))
		self.assertEqual(_rows(plan, "u"), {("HelixHR Portal Admin", A): (0, 0)})
		self.assertIn("left_portal_admin", {note["fix"] for note in plan["u"]["narrowed"]})

	def test_report_manager_maps_to_reports_with_export(self):
		plan = _plan(u=_user({"HelixHR Report Manager"}, [_active(A)]))
		self.assertEqual(_rows(plan, "u"), {("HelixHR Report Manager", A): (1, 1)})

	def test_roles_that_reach_nothing_without_an_employee_get_no_rows(self):
		plan = _plan(
			u=_user({"HelixHR Report Manager", "IT Team", "HelixHR Notification Manager"}),
		)
		self.assertEqual(plan.get("u", {}).get("rows", []), [])

	def test_it_team_and_notification_manager_get_home(self):
		plan = _plan(u=_user({"IT Team", "HelixHR Notification Manager"}, [_active(B)]))
		self.assertEqual(
			_rows(plan, "u"), {("IT Team", B): (1, 0), ("HelixHR Notification Manager", B): (1, 0)}
		)

	def test_system_manager_gets_no_rows(self):
		plan = _plan(u=_user({"System Manager", "HR Manager"}, [_active(A)]))
		self.assertNotIn("u", plan)

	def test_a_disabled_user_gets_disabled_rows(self):
		plan = _plan(u=_user({"IT Team"}, [_active(A)], enabled=False))
		self.assertEqual(_rows(plan, "u"), {("IT Team", A): (0, 0)})

	# --- Delivery Manager -------------------------------------------------------

	def test_delivery_manager_gets_home_and_every_org_with_a_membership(self):
		plan = _plan(u=_user({"HelixHR Delivery Manager"}, [_active(A)], project_orgs={B: 1}))
		self.assertEqual(
			_rows(plan, "u"),
			{("HelixHR Delivery Manager", A): (1, 0), ("HelixHR Delivery Manager", B): (1, 0)},
		)
		# B: 1 project today, 3 after; A: 0 today, 5 after.
		self.assertTrue(any("1 project" in note and B in note for note in plan["u"]["widened"]))

	def test_delivery_manager_with_no_employee_gets_membership_orgs_only(self):
		plan = _plan(u=_user({"HelixHR Delivery Manager"}, project_orgs={C: 2}))
		self.assertEqual(_rows(plan, "u"), {("HelixHR Delivery Manager", C): (1, 0)})
		self.assertIsNone(plan["u"]["employee"])

	# --- grants -------------------------------------------------------------------

	def _grant(self, level, companies, export=0, enabled=1, employee="EMP-1", home=A):
		return {
			"level": level,
			"companies": companies,
			"allow_export": export,
			"enabled": enabled,
			"employee": employee,
			"home_company": home,
			"disabled_reason": None if enabled else "turned off",
		}

	def test_all_data_grant_becomes_org_viewer(self):
		plan = _plan(u=_user({"Employee"}, [_active(A)], grant=self._grant("All data", [B, C], export=1)))
		self.assertEqual(
			_rows(plan, "u"), {("HelixHR Org Viewer", B): (1, 1), ("HelixHR Org Viewer", C): (1, 1)}
		)
		self.assertEqual(plan["u"]["widened"], [])
		self.assertEqual(plan["u"]["narrowed"], [])

	def test_reports_only_grant_merges_with_report_manager_by_or(self):
		plan = _plan(
			u=_user(
				{"HelixHR Report Manager"},
				[_active(A)],
				grant=self._grant("Reports only", [A, B], export=0),
			)
		)
		self.assertEqual(
			_rows(plan, "u"),
			{("HelixHR Report Manager", A): (1, 1), ("HelixHR Report Manager", B): (1, 0)},
		)

	def test_a_disabled_or_moved_grant_writes_disabled_rows(self):
		disabled = _plan(u=_user({"Employee"}, [_active(A)], grant=self._grant("All data", [B], enabled=0)))
		self.assertEqual(_rows(disabled, "u"), {("HelixHR Org Viewer", B): (0, 0)})
		moved = _plan(u=_user({"Employee"}, [_active(B)], grant=self._grant("All data", [C], home=A)))
		self.assertEqual(_rows(moved, "u"), {("HelixHR Org Viewer", C): (0, 0)})

	# --- the R26 promise --------------------------------------------------------------

	def test_nobody_is_narrowed_except_by_a_named_security_fix(self):
		plan = _plan(
			a=_user({"HR Manager"}, [_active(A)]),
			b=_user({"HR User"}),
			c=_user({"HelixHR Portal Admin"}, [_active(A)], company_permissions=[B]),
			d=_user({"HelixHR Delivery Manager"}, [_active(B)], project_orgs={A: 2}),
			e=_user({"Employee"}, [_active(A)], grant=self._grant("Reports only", [B])),
		)
		for answer in plan.values():
			for note in answer["narrowed"]:
				self.assertIn(note["fix"], SECURITY_FIXES)


class TestDryRun(IntegrationTestCase):
	def test_dry_run_writes_a_private_report_and_a_snapshot_and_no_rows(self):
		before = frappe.db.sql("select count(*), max(modified) from `tabHelixHR Company Access`")
		result = dry_run()
		self.assertEqual(
			frappe.db.sql("select count(*), max(modified) from `tabHelixHR Company Access`"), before
		)

		for key in ("report", "snapshot"):
			path = result[key]
			self.assertTrue(path.startswith(os.path.abspath(frappe.get_site_path("private"))), path)
			self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
			self.addCleanup(os.remove, path)

		with open(result["report"]) as handle:
			report = json.load(handle)
		self.assertEqual(set(report["summary"]), {"widened", "narrowed_by_design", "unchanged"})
		for person in report["people"]:
			self.assertTrue(person["outcomes"])
			self.assertLessEqual(set(person["outcomes"]), {"widened", "narrowed_by_design", "unchanged"})

		with open(result["snapshot"]) as handle:
			snapshot = json.load(handle)
		self.assertTrue(snapshot["users"])
		some = next(iter(snapshot["users"].values()))
		self.assertEqual(
			set(some),
			{"admin", "read", "project", "project_read", "portal_admin", "can_admin_portal", "reports"},
		)
