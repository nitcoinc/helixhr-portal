"""Plan 2026-10-18-001 U5 (R26, R27) and plan 2026-10-19-001 U12: the pure mapping from
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

from helixhr.access_migration import SECURITY_FIXES, SNAPSHOT_KEYS, collect_state, dry_run, plan_rows
from helixhr.tests.utils import (
	create_test_company,
	ensure_baseline_company,
	ensure_test_company,
	make_test_hr_user,
	make_test_it_user,
	make_test_user,
)

SEEDED_ADMIN = "u5-portal-admin@helixhr.test"
THIRD_COMPANY = "_Test Migration C"

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
		(row["role"], "ALL" if row["all_orgs"] else row["company"]): row["allow_export"]
		for row in plan[user]["rows"]
	}


def _widened_keys(plan, user):
	return {note["key"] for note in plan[user]["widened"]}


def _fixes(plan, user):
	return {note["fix"] for note in plan[user]["narrowed"]}


class TestPlanRows(UnitTestCase):
	"""Plan 2026-10-19-001 U12: the preview matches the assignment model -- rows
	carry no enabled flag and no anchor, and every widening and narrowing is a
	structured record naming the resolver keys it moves."""

	def test_every_row_is_migrated_and_carries_no_anchor_or_enabled_flag(self):
		plan = _plan(u=_user({"HR Manager"}, [_active(A)]))
		for row in plan["u"]["rows"]:
			self.assertEqual(row["origin"], "migrated")
			self.assertNotIn("enabled", row)
		self.assertNotIn("employee", plan["u"])
		self.assertEqual(plan["u"]["own_company"], A)

	# --- HR roles -------------------------------------------------------------

	def test_hr_manager_with_employee_gets_their_company_and_no_notes(self):
		plan = _plan(u=_user({"HR Manager"}, [_active(A)]))
		self.assertEqual(_rows(plan, "u"), {("HR Manager", A): 0})
		self.assertEqual(plan["u"]["widened"], [])
		# Desk lock-down is deferred, so nothing narrows for HR.
		self.assertEqual(plan["u"]["narrowed"], [])

	def test_hr_roles_and_portal_admin_with_no_employee_get_all_orgs(self):
		plan = _plan(m=_user({"HR Manager"}), h=_user({"HR User"}), p=_user({"HelixHR Portal Admin"}))
		self.assertEqual(_rows(plan, "m"), {("HR Manager", "ALL"): 0})
		self.assertEqual(_rows(plan, "h"), {("HR User", "ALL"): 0})
		self.assertEqual(_rows(plan, "p"), {("HelixHR Portal Admin", "ALL"): 0})
		self.assertEqual(plan["m"]["widened"], [])
		self.assertEqual(_widened_keys(plan, "h"), {"hr_user_all_orgs"})
		self.assertEqual(_widened_keys(plan, "p"), {"portal_admin_all_orgs"})

	def test_a_rehired_login_maps_on_the_active_row(self):
		plan = _plan(u=_user({"HR Manager"}, [_left(B, "EMP-NEW"), _active(A, "EMP-OLD")]))
		self.assertEqual(_rows(plan, "u"), {("HR Manager", A): 0})
		self.assertEqual(plan["u"]["own_company"], A)

	def test_an_inactive_person_keeps_rows_so_reactivation_restores_reach(self):
		plan = _plan(u=_user({"HR Manager", "IT Team"}, [_left(A)]))
		self.assertEqual(_rows(plan, "u"), {("HR Manager", A): 0, ("IT Team", A): 0})
		self.assertEqual(plan["u"]["widened"], [])

	def test_a_disabled_user_keeps_rows_and_gains_nothing(self):
		plan = _plan(u=_user({"HR User", "IT Team"}, [_active(A)], enabled=False))
		self.assertEqual(_rows(plan, "u"), {("HR User", A): 0, ("IT Team", A): 0})
		self.assertEqual(plan["u"]["widened"], [])

	# --- portal roles -----------------------------------------------------------

	def test_portal_admin_gets_home_plus_company_permissions(self):
		plan = _plan(
			u=_user({"HelixHR Portal Admin"}, [_active(A)], company_permissions=[B, B, A, "Deleted Co"])
		)
		self.assertEqual(_rows(plan, "u"), {("HelixHR Portal Admin", A): 0, ("HelixHR Portal Admin", B): 0})
		self.assertEqual(_widened_keys(plan, "u"), {"portal_admin_extra_org"})
		self.assertEqual(_fixes(plan, "u"), {"global_settings"})

	def test_a_left_portal_admin_loses_admin_as_a_security_fix(self):
		plan = _plan(u=_user({"HelixHR Portal Admin"}, [_left(A)], company_permissions=[B]))
		self.assertEqual(_rows(plan, "u"), {("HelixHR Portal Admin", A): 0, ("HelixHR Portal Admin", B): 0})
		self.assertEqual(_fixes(plan, "u"), {"left_portal_admin"})
		self.assertEqual(plan["u"]["widened"], [])

	def test_report_manager_maps_to_reports_with_export(self):
		plan = _plan(u=_user({"HelixHR Report Manager"}, [_active(A)]))
		self.assertEqual(_rows(plan, "u"), {("HelixHR Report Manager", A): 1})

	def test_roles_that_reach_nothing_without_an_employee_get_no_rows(self):
		plan = _plan(u=_user({"HelixHR Report Manager", "IT Team"}))
		self.assertNotIn("u", plan)

	def test_notification_manager_is_always_all_orgs(self):
		"""Message templates are global, so the role is All-orgs only (R2)."""
		plan = _plan(
			a=_user({"HelixHR Notification Manager"}, [_active(B)]),
			b=_user({"HelixHR Notification Manager"}),
		)
		self.assertEqual(_rows(plan, "a"), {("HelixHR Notification Manager", "ALL"): 0})
		self.assertEqual(_rows(plan, "b"), {("HelixHR Notification Manager", "ALL"): 0})

	def test_it_team_gets_home(self):
		plan = _plan(u=_user({"IT Team"}, [_active(B)]))
		self.assertEqual(_rows(plan, "u"), {("IT Team", B): 0})

	# --- System Managers -------------------------------------------------------

	def test_a_system_manager_with_an_active_employee_is_a_declared_widening(self):
		plan = _plan(u=_user({"System Manager", "HR Manager"}, [_active(A)]))
		self.assertEqual(plan["u"]["rows"], [])
		self.assertEqual(_widened_keys(plan, "u"), {"system_manager_unscoped"})
		self.assertIn("admin", plan["u"]["widened"][0]["resolver_keys"])

	def test_a_system_manager_with_no_employee_is_unchanged(self):
		plan = _plan(u=_user({"System Manager", "HR Manager"}))
		self.assertNotIn("u", plan)

	# --- Delivery Manager -------------------------------------------------------

	def test_delivery_manager_gets_home_and_every_org_with_a_membership(self):
		plan = _plan(u=_user({"HelixHR Delivery Manager"}, [_active(A)], project_orgs={B: 1}))
		self.assertEqual(
			_rows(plan, "u"), {("HelixHR Delivery Manager", A): 0, ("HelixHR Delivery Manager", B): 0}
		)
		# B: 1 project today, 3 after; A: 0 today, 5 after.
		notes = plan["u"]["widened"]
		self.assertEqual({note["key"] for note in notes}, {"delivery_manager_org_projects"})
		self.assertTrue(any("1 project" in note["text"] and B in note["text"] for note in notes))
		self.assertTrue(all("project" in note["resolver_keys"] for note in notes))

	def test_delivery_manager_with_no_employee_gets_membership_orgs_only(self):
		plan = _plan(u=_user({"HelixHR Delivery Manager"}, project_orgs={C: 2}))
		self.assertEqual(_rows(plan, "u"), {("HelixHR Delivery Manager", C): 0})
		self.assertIsNone(plan["u"]["own_company"])

	def test_an_inactive_delivery_manager_keeps_rows_and_gains_nothing(self):
		plan = _plan(u=_user({"HelixHR Delivery Manager"}, [_left(A)], project_orgs={B: 1}))
		self.assertEqual(
			_rows(plan, "u"), {("HelixHR Delivery Manager", A): 0, ("HelixHR Delivery Manager", B): 0}
		)
		self.assertEqual(plan["u"]["widened"], [])

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
		self.assertEqual(_rows(plan, "u"), {("HelixHR Org Viewer", B): 1, ("HelixHR Org Viewer", C): 1})
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
			_rows(plan, "u"), {("HelixHR Report Manager", A): 1, ("HelixHR Report Manager", B): 0}
		)

	def test_a_disabled_or_moved_grant_writes_no_rows(self):
		"""Neither reaches anything today, so neither becomes access."""
		disabled = _plan(u=_user({"Employee"}, [_active(A)], grant=self._grant("All data", [B], enabled=0)))
		self.assertNotIn("u", disabled)
		moved = _plan(u=_user({"Employee"}, [_active(B)], grant=self._grant("All data", [C], home=A)))
		self.assertNotIn("u", moved)

	def test_a_grant_on_an_inactive_person_is_kept_for_reactivation(self):
		plan = _plan(u=_user({"Employee"}, [_left(A, "EMP-1")], grant=self._grant("All data", [B])))
		self.assertEqual(_rows(plan, "u"), {("HelixHR Org Viewer", B): 0})

	# --- the R25 promise --------------------------------------------------------------

	def test_every_note_is_structured_and_every_narrowing_is_a_named_fix(self):
		plan = _plan(
			a=_user({"HR Manager"}, [_active(A)]),
			b=_user({"HR User"}),
			c=_user({"HelixHR Portal Admin"}, [_active(A)], company_permissions=[B]),
			d=_user({"HelixHR Delivery Manager"}, [_active(B)], project_orgs={A: 2}),
			e=_user({"Employee"}, [_active(A)], grant=self._grant("Reports only", [B])),
			f=_user({"System Manager"}, [_active(C)]),
			g=_user({"HelixHR Portal Admin"}, [_left(A)]),
		)
		self.assertEqual(set(SECURITY_FIXES), {"left_portal_admin", "global_settings"})
		for answer in plan.values():
			for note in answer["widened"]:
				self.assertTrue(note["key"] and note["resolver_keys"] and note["text"])
			for note in answer["narrowed"]:
				self.assertIn(note["fix"], SECURITY_FIXES)
				self.assertTrue(note["resolver_keys"])


class TestCollectState(IntegrationTestCase):
	"""The site read `plan_rows` runs on, end to end, for one seeded Portal
	Admin: a Company-only User Permission is a second org; the apply-to-all
	one ERPNext makes as a side effect is ignored."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.company_a = ensure_test_company()
		self.company_b = ensure_baseline_company()
		self.company_c = create_test_company(THIRD_COMPANY, "TMC")
		make_test_user(SEEDED_ADMIN, self.company_a, create_user_permission=0)
		if "HelixHR Portal Admin" not in frappe.get_roles(SEEDED_ADMIN):
			frappe.get_doc("User", SEEDED_ADMIN).add_roles("HelixHR Portal Admin")
		for company, apply_to_all in ((self.company_b, 0), (self.company_c, 1)):
			if not frappe.db.exists("User Permission", {"user": SEEDED_ADMIN, "for_value": company}):
				frappe.get_doc(
					{
						"doctype": "User Permission",
						"user": SEEDED_ADMIN,
						"allow": "Company",
						"for_value": company,
						"apply_to_all_doctypes": apply_to_all,
						"applicable_for": None if apply_to_all else "Company",
					}
				).insert(ignore_permissions=True)

	def test_a_seeded_portal_admin_maps_to_home_plus_the_company_only_permission(self):
		state = collect_state()
		info = state["users"][SEEDED_ADMIN]
		self.assertIn("HelixHR Portal Admin", info["roles"])
		self.assertEqual(info["company_permissions"], [self.company_b])
		rows = {(row["role"], row["company"]) for row in plan_rows(state)[SEEDED_ADMIN]["rows"]}
		self.assertEqual(
			rows, {("HelixHR Portal Admin", self.company_a), ("HelixHR Portal Admin", self.company_b)}
		)

	def test_the_dry_run_lists_the_seeded_person(self):
		result = dry_run()
		for path in (result["report"], result["snapshot"]):
			self.addCleanup(os.remove, path)
		with open(result["report"]) as handle:
			people = {person["user"]: person for person in json.load(handle)["people"]}
		self.assertIn("widened", people[SEEDED_ADMIN]["outcomes"])
		with open(result["snapshot"]) as handle:
			self.assertIn(SEEDED_ADMIN, json.load(handle)["users"])


class TestSnapshotKeys(IntegrationTestCase):
	"""U12: the snapshot carries the scope answers Release B changes beyond the
	Release 2 resolvers -- IT worker scope, oversight, People & access reach,
	the bootstrap flags -- and HR and IT recipients per org."""

	def test_it_admin_and_hr_user_answers_are_recorded(self):
		from helixhr.access_migration import snapshot

		frappe.set_user("Administrator")
		company = ensure_test_company()
		_, it_user = make_test_it_user()
		_, hr_user = make_test_hr_user(company)
		users = snapshot([it_user, hr_user])["users"]
		self.assertEqual(users[it_user]["it_scope"], [company])
		self.assertEqual(users[hr_user]["oversight"], {"hr_manager": False, "companies": [company]})
		self.assertTrue(users[hr_user]["flags"]["can_oversee_timesheets"])
		self.assertIsNone(users[hr_user]["people_access_reach"])

	def test_recipients_are_recorded_per_org(self):
		from helixhr.access_migration import snapshot

		frappe.set_user("Administrator")
		company = ensure_test_company()
		_, it_user = make_test_it_user()
		recipients = snapshot([])["recipients"]
		self.assertIn(it_user, recipients[company]["IT Team"])


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
		self.assertEqual(set(some), set(SNAPSHOT_KEYS))
		# Recipients are per org, not per person (U12).
		self.assertTrue(snapshot["recipients"])
		some_org = next(iter(snapshot["recipients"].values()))
		self.assertEqual(set(some_org), {"HR Manager", "IT Team"})
