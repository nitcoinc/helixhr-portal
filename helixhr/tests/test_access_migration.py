"""Plan 2026-10-18-001 U5 (R26, R27) and plan 2026-10-19-001 U12 and U8: the pure
mapping from today's access to assignment rows, the read-only dry-run, the
resolver snapshot, the migration that writes the rows and the parity check
that proves the switch.

`plan_rows` takes a plain state dict, so the mapping table is tested without
a database. The dry-run, snapshot, migration and parity check are tested on
the site.
"""

import json
import os
import stat
import tempfile
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

from helixhr import api
from helixhr import utils as helixhr_utils
from helixhr.access_migration import (
	DECLARED_RECIPIENT_CHANGES,
	SECURITY_FIXES,
	SNAPSHOT_KEYS,
	collect_state,
	dry_run,
	migrate,
	parity_check,
	plan_rows,
	snapshot,
)
from helixhr.reports import CATALOG
from helixhr.tests.utils import (
	create_test_company,
	ensure_baseline_company,
	ensure_test_company,
	make_test_company_grant,
	make_test_hr_manager_employee,
	make_test_hr_user,
	make_test_it_user,
	make_test_portal_admin,
	make_test_project,
	make_test_user,
)
from helixhr.utils import ALL_ORGS_SCOPE_KEY, clear_role_scope_cache

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
		self.assertEqual(_fixes(plan, "u"), {"person_gate"})

	def test_a_disabled_user_keeps_rows_and_gains_nothing(self):
		plan = _plan(u=_user({"HR User", "IT Team"}, [_active(A)], enabled=False))
		self.assertEqual(_rows(plan, "u"), {("HR User", A): 0, ("IT Team", A): 0})
		self.assertEqual(plan["u"]["widened"], [])
		self.assertEqual(_fixes(plan, "u"), {"person_gate"})

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
		self.assertEqual(_fixes(plan, "u"), {"person_gate"})
		self.assertEqual(plan["u"]["widened"], [])

	def test_report_manager_maps_to_reports_with_export(self):
		plan = _plan(u=_user({"HelixHR Report Manager"}, [_active(A)]))
		self.assertEqual(_rows(plan, "u"), {("HelixHR Report Manager", A): 1})

	def test_roles_that_reach_nothing_without_an_employee_get_no_rows(self):
		plan = _plan(u=_user({"HelixHR Report Manager", "IT Team"}))
		self.assertEqual(plan["u"]["rows"], [])
		self.assertNotIn("r", _plan(r=_user({"HelixHR Report Manager"})))

	def test_an_unscoped_hr_manager_leaving_it_mail_is_a_declared_recipient_change(self):
		from helixhr.access_migration import _recipient_rule

		info = _user({"HR Manager", "IT Team"})
		self.assertEqual(_recipient_rule("removed", "u", A, info, "IT Team"), "unscoped_hr_not_it")
		self.assertIsNone(_recipient_rule("removed", "u", A, info, "HR Manager"))
		self.assertIsNone(_recipient_rule("added", "u", A, info, "IT Team"))

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

	def test_a_reports_only_grant_declares_every_catalog_report(self):
		"""U8: today's grant tier runs company-scoped entries only."""
		plan = _plan(u=_user({"Employee"}, [_active(A)], grant=self._grant("Reports only", [B])))
		self.assertEqual(_widened_keys(plan, "u"), {"reports_grant_all_catalog"})
		self.assertEqual(plan["u"]["widened"][0]["resolver_keys"], ["reports"])
		viewer = _plan(u=_user({"Employee"}, [_active(A)], grant=self._grant("All data", [B])))
		self.assertEqual(viewer["u"]["widened"], [])

	def test_a_delivery_manager_whose_count_is_unchanged_still_declares_the_shape(self):
		"""Today's scope is membership ("assigned"), after it is the orgs."""
		plan = plan_rows(
			{
				"companies": COMPANIES,
				"org_projects": {A: 1},
				"users": {"u": _user({"HelixHR Delivery Manager"}, [_active(A)], project_orgs={A: 1})},
			}
		)
		self.assertEqual(_widened_keys(plan, "u"), {"delivery_manager_org_projects"})

	def test_a_portal_admins_extra_org_declares_people_access_reach(self):
		plan = _plan(u=_user({"HelixHR Portal Admin"}, [_active(A)], company_permissions=[B]))
		self.assertIn("people_access_reach", plan["u"]["widened"][0]["resolver_keys"])

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
		self.assertEqual(set(SECURITY_FIXES), {"person_gate", "global_settings", "empty_it_queue"})
		for answer in plan.values():
			for note in answer["widened"]:
				self.assertTrue(note["key"] and note["resolver_keys"] and note["text"])
				# A typo here would hide a real change from the parity check.
				self.assertLessEqual(set(note["resolver_keys"]), set(SNAPSHOT_KEYS))
			for note in answer["narrowed"]:
				self.assertIn(note["fix"], SECURITY_FIXES)
				self.assertTrue(note["resolver_keys"])
				self.assertLessEqual(set(note["resolver_keys"]), set(SNAPSHOT_KEYS))

	def test_a_gated_system_manager_or_portal_admin_declares_the_gate(self):
		"""Today's code never gated a System Manager; R4 does: declared, so
		the parity check can tell it from a bug. Nothing is gained."""
		plan = _plan(
			left=_user({"System Manager"}, [_left(A)]),
			off=_user({"System Manager", "IT Team"}, [_active(A)], enabled=False),
			off_none=_user({"System Manager"}, enabled=False),
			pa=_user({"HelixHR Portal Admin"}, [_active(A)], company_permissions=[B], enabled=False),
		)
		for user in ("left", "off", "off_none", "pa"):
			with self.subTest(user=user):
				self.assertEqual(plan[user]["widened"], [])
				self.assertEqual(_fixes(plan, user), {"person_gate"})
		# R5: a System Manager is never assigned; the Portal Admin's rows are
		# kept for reactivation.
		self.assertEqual(plan["off"]["rows"], [])
		self.assertTrue(plan["pa"]["rows"])

	def test_a_system_manager_with_no_employee_who_holds_it_team_widens_the_it_queue(self):
		"""Today their IT queue is the Active Employee's company: none."""
		plan = _plan(u=_user({"System Manager", "IT Team"}))
		self.assertEqual(plan["u"]["rows"], [])
		self.assertEqual(_widened_keys(plan, "u"), {"system_manager_it_all_orgs"})
		self.assertEqual(plan["u"]["widened"][0]["resolver_keys"], ["it_scope"])
		self.assertEqual(plan["u"]["narrowed"], [])

	def test_an_it_admin_with_no_employee_declares_the_empty_queue(self):
		plan = _plan(u=_user({"IT Team"}))
		self.assertEqual(plan["u"]["rows"], [])
		self.assertEqual(_fixes(plan, "u"), {"empty_it_queue"})

	def test_a_grant_on_a_person_with_no_employee_writes_nothing(self):
		"""Today a grant needs its saved Employee to apply."""
		plan = _plan(u=_user({"Employee"}, grant=self._grant("All data", [B])))
		self.assertNotIn("u", plan)


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
		self.assertFalse(users[hr_user]["flags"]["can_see_all_pending"])
		self.assertIsNone(users[hr_user]["people_access_reach"])
		self.assertEqual(users[hr_user]["it_scope"], [])

	def test_a_portal_admins_reach_and_an_hr_managers_flags_are_recorded(self):
		from helixhr.access_migration import snapshot

		frappe.set_user("Administrator")
		company = ensure_test_company()
		_, admin = make_test_portal_admin(company)
		_, hr_manager = make_test_hr_manager_employee()
		users = snapshot([admin, hr_manager])["users"]
		self.assertEqual(users[admin]["people_access_reach"], sorted(users[admin]["people_access_reach"]))
		self.assertIn(company, users[admin]["people_access_reach"])
		self.assertTrue(users[hr_manager]["flags"]["can_configure"])
		self.assertTrue(users[hr_manager]["flags"]["can_see_all_pending"])

	def test_recipients_are_recorded_per_org(self):
		from helixhr.access_migration import snapshot

		frappe.set_user("Administrator")
		company = ensure_test_company()
		_, it_user = make_test_it_user()
		_, hr_manager = make_test_hr_manager_employee()
		recipients = snapshot([])["recipients"]
		self.assertIn(it_user, recipients[company]["IT Team"])
		self.assertIn(hr_manager, recipients[company]["HR Manager"])


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
			{
				"admin",
				"read",
				"project",
				"project_read",
				"portal_admin",
				"can_admin_portal",
				"reports",
				"it_scope",
				"oversight",
				"people_access_reach",
				"flags",
			},
		)
		self.assertEqual(set(SNAPSHOT_KEYS), set(some))
		# Recipients are per org, not per person (U12).
		self.assertTrue(snapshot["recipients"])
		some_org = next(iter(snapshot["recipients"].values()))
		self.assertEqual(set(some_org), {"HR Manager", "IT Team"})


# --- U8: the migration and the parity check --------------------------------------

U8_IT = "u8-it@helixhr.test"
U8_HR_ALL = "u8-hr-all@helixhr.test"
U8_SM = "u8-sm@helixhr.test"
U8_DM = "u8-dm@helixhr.test"
U8_GRANT = "u8-grant@helixhr.test"
U8_REPORTS = "u8-reports@helixhr.test"
U8_SM_OFF = "u8-sm-off@helixhr.test"
U8_SM_IT = "u8-sm-it@helixhr.test"
U8_HR_OFF = "u8-hr-off@helixhr.test"
U8_PROJECT = "_Test U8 Delivery Project"
_U8_USERS = (U8_IT, U8_HR_ALL, U8_SM, U8_DM, U8_GRANT, U8_REPORTS, U8_SM_OFF, U8_SM_IT, U8_HR_OFF)
_U8_SEEDED = (U8_IT, U8_HR_ALL, U8_SM, U8_DM, U8_SM_OFF, U8_SM_IT, U8_HR_OFF)
_FLAGS = (
	"can_configure",
	"can_handle_requests",
	"can_edit_email_templates",
	"can_run_reports",
	"can_oversee_timesheets",
	"can_see_all_pending",
	"can_manage_documents",
)


def _set_legacy_roles(user, roles):
	"""``user`` holding exactly ``roles`` beyond Employee, as Desk would set
	them before Release B: Frappe roles, no assignment rows, no change log."""
	doc = frappe.get_doc("User", user)
	managed = {*roles, "IT Team", "HR Manager", "HR User", "System Manager"}
	managed |= {"HelixHR Delivery Manager", "HelixHR Report Manager", "HelixHR Org Viewer"}
	doc.set("roles", [row for row in doc.roles if row.role not in managed])
	for role in roles:
		doc.append("roles", {"role": role})
	doc.save(ignore_permissions=True)
	frappe.db.delete("HelixHR Access Assignment", {"user": user})
	frappe.db.delete("HelixHR Access Change", {"target_user": user})
	frappe.clear_cache(user=user)
	clear_role_scope_cache(user)


def _ensure_login(user):
	if not frappe.db.exists("User", user):
		frappe.get_doc(
			{"doctype": "User", "email": user, "first_name": user.split("@")[0], "send_welcome_email": 0}
		).insert(ignore_permissions=True)


def _seed_legacy(company_a, company_b):
	"""Seven people as the pre-switch site holds them:

	- an IT Admin with an Employee in A;
	- an HR Manager with no Employee (the Desk-only persona);
	- a System Manager with an Employee in A who also holds IT Team;
	- a Delivery Manager with an Employee in A, member of one project in B;
	- a disabled System Manager with an Employee in A;
	- a System Manager with no Employee who holds IT Team;
	- a disabled HR Manager with an Employee in A.
	"""
	make_test_user(U8_IT, company_a)
	_set_legacy_roles(U8_IT, ["IT Team"])
	_ensure_login(U8_HR_ALL)
	frappe.db.delete("Employee", {"user_id": U8_HR_ALL})
	_set_legacy_roles(U8_HR_ALL, ["HR Manager"])
	make_test_user(U8_SM, company_a)
	_set_legacy_roles(U8_SM, ["System Manager", "IT Team"])
	make_test_user(U8_DM, company_a)
	_set_legacy_roles(U8_DM, ["HelixHR Delivery Manager"])
	make_test_project(company_b, U8_PROJECT, members=[U8_DM])
	make_test_user(U8_SM_OFF, company_a)
	_set_legacy_roles(U8_SM_OFF, ["System Manager"])
	_ensure_login(U8_SM_IT)
	frappe.db.delete("Employee", {"user_id": U8_SM_IT})
	_set_legacy_roles(U8_SM_IT, ["System Manager", "IT Team"])
	make_test_user(U8_HR_OFF, company_a)
	_set_legacy_roles(U8_HR_OFF, ["HR Manager"])
	for user in (U8_SM_OFF, U8_HR_OFF):
		# Not `User.save`: its hooks would expire exports, which is not the
		# point here. The gate re-reads `enabled`.
		frappe.db.set_value("User", user, "enabled", 0)
		clear_role_scope_cache(user)


def _none():
	return {"kind": "none", "company": None}


def _flags(**on):
	return {flag: flag in on for flag in _FLAGS}


def _reports(companies):
	return {entry["key"]: [[company, True] for company in companies] for entry in CATALOG}


def _old_answers(company_a, companies):
	"""What the Release A resolvers answered for `_seed_legacy`'s people,
	written out by hand from the old rules rather than run: the parity check
	must prove the new code against the old answers, not against itself.

	The Delivery Manager's report answers are the seeded report matrix's
	(`seed_report_access`): Delivery Manager runs and exports the two project
	reports, and the old "assigned" tier ran them for the home company."""
	unscoped = {"kind": "unscoped", "company": None}
	home = {"kind": "company", "company": company_a}
	# Today's code never read User.enabled: a disabled login answered like
	# an enabled one.
	anchored_sm = {
		"admin": home,
		"read": home,
		"project": home,
		"project_read": home,
		"portal_admin": home,
		"can_admin_portal": True,
		"reports": _reports([company_a]),
		"it_scope": [],
		"oversight": {"hr_manager": True, "companies": [company_a]},
		"people_access_reach": companies,
		"flags": _flags(**dict.fromkeys(_FLAGS, 1)),
	}
	hr_flags = _flags(
		can_configure=1,
		can_handle_requests=1,
		can_run_reports=1,
		can_oversee_timesheets=1,
		can_see_all_pending=1,
		can_manage_documents=1,
	)
	return {
		U8_SM_OFF: anchored_sm,
		# No Employee: unscoped, and an empty IT queue (no session company).
		U8_SM_IT: {
			"admin": unscoped,
			"read": unscoped,
			"project": unscoped,
			"project_read": unscoped,
			"portal_admin": unscoped,
			"can_admin_portal": True,
			"reports": _reports(companies),
			"it_scope": [],
			"oversight": {"hr_manager": True, "companies": None},
			"people_access_reach": companies,
			"flags": _flags(**dict.fromkeys(_FLAGS, 1)),
		},
		U8_HR_OFF: {
			"admin": home,
			"read": home,
			"project": home,
			"project_read": home,
			# Answered for an HR Manager too; compared behind can_admin_portal.
			"portal_admin": home,
			"can_admin_portal": False,
			"reports": _reports([company_a]),
			"it_scope": [],
			"oversight": {"hr_manager": True, "companies": [company_a]},
			"people_access_reach": None,
			"flags": hr_flags,
		},
		U8_IT: {
			"admin": _none(),
			"read": _none(),
			"project": {"kind": "none"},
			"project_read": {"kind": "none"},
			"portal_admin": _none(),
			"can_admin_portal": False,
			"reports": {},
			"it_scope": [company_a],
			"oversight": None,
			"people_access_reach": None,
			"flags": _flags(can_handle_requests=1),
		},
		# Desk-only HR Manager: unscoped by the role alone.
		U8_HR_ALL: {
			"admin": unscoped,
			"read": unscoped,
			"project": unscoped,
			"project_read": unscoped,
			"portal_admin": unscoped,
			"can_admin_portal": False,
			"reports": _reports(companies),
			"it_scope": [],
			"oversight": {"hr_manager": True, "companies": None},
			"people_access_reach": None,
			"flags": hr_flags,
		},
		# Anchored System Manager: every HR answer limited to their company.
		U8_SM: {
			"admin": home,
			"read": home,
			"project": home,
			"project_read": home,
			"portal_admin": home,
			"can_admin_portal": True,
			"reports": _reports([company_a]),
			"it_scope": [company_a],
			"oversight": {"hr_manager": True, "companies": [company_a]},
			"people_access_reach": companies,
			"flags": _flags(**dict.fromkeys(_FLAGS, 1)),
		},
		U8_DM: {
			"admin": _none(),
			"read": _none(),
			"project": {"kind": "assigned", "user": U8_DM},
			"project_read": {"kind": "assigned", "user": U8_DM},
			"portal_admin": _none(),
			"can_admin_portal": False,
			"reports": {key: [[company_a, True]] for key in ("hours_by_project", "project_timesheet")},
			"it_scope": [],
			"oversight": None,
			"people_access_reach": None,
			"flags": _flags(can_run_reports=1),
		},
	}


class _MigrationCase(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company_a = ensure_test_company()
		self.company_b = ensure_baseline_company()
		self.companies = frappe.get_all("Company", pluck="name", order_by="name asc")

	def tearDown(self):
		frappe.set_user("Administrator")
		clear_role_scope_cache()

	def _rows(self, user):
		return {
			(row.role, ALL_ORGS_SCOPE_KEY if row.all_orgs else row.company, row.allow_export, row.origin)
			for row in frappe.get_all(
				"HelixHR Access Assignment",
				filters={"user": user},
				fields=["role", "all_orgs", "company", "allow_export", "origin"],
			)
		}

	def _has_role(self, user, role):
		return bool(frappe.db.exists("Has Role", {"parenttype": "User", "parent": user, "role": role}))


class TestMigrate(_MigrationCase):
	"""U8: the patch writes the planned rows once, adds only the Org viewer
	Frappe role, and leaves alone anyone the save has touched."""

	def test_a_no_employee_hr_manager_gets_an_all_orgs_row(self):
		_seed_legacy(self.company_a, self.company_b)
		result = migrate()
		self.assertIn(U8_HR_ALL, result["migrated"])
		self.assertEqual(self._rows(U8_HR_ALL), {("HR Manager", ALL_ORGS_SCOPE_KEY, 0, "migrated")})
		self.assertFalse(frappe.db.get_value("HelixHR Access Assignment", {"user": U8_HR_ALL}, "company"))
		self.assertEqual(self._rows(U8_IT), {("IT Team", self.company_a, 0, "migrated")})
		self.assertEqual(
			self._rows(U8_DM),
			{
				("HelixHR Delivery Manager", self.company_a, 0, "migrated"),
				("HelixHR Delivery Manager", self.company_b, 0, "migrated"),
			},
		)
		# R5: a System Manager is never assigned.
		self.assertEqual(self._rows(U8_SM), set())

	def test_a_rerun_changes_nothing(self):
		_seed_legacy(self.company_a, self.company_b)
		migrate()
		rows = frappe.get_all("HelixHR Access Assignment", fields=["name", "modified"], order_by="name")
		roles = frappe.db.count("Has Role", {"parenttype": "User"})
		again = migrate()
		self.assertEqual(again["migrated"], [])
		self.assertIn(U8_IT, again["skipped"])
		self.assertEqual(
			frappe.get_all("HelixHR Access Assignment", fields=["name", "modified"], order_by="name"), rows
		)
		self.assertEqual(frappe.db.count("Has Role", {"parenttype": "User"}), roles)

	def test_a_person_cleared_through_the_save_is_not_recreated(self):
		_seed_legacy(self.company_a, self.company_b)
		migrate()
		api.save_person_access(U8_IT, [], api.access_token(U8_IT))
		self.assertEqual(self._rows(U8_IT), set())
		# Desk gives the Frappe role back afterwards: drift, not a migration.
		frappe.get_doc("User", U8_IT).add_roles("IT Team")
		frappe.clear_cache(user=U8_IT)
		self.assertIn(U8_IT, plan_rows(collect_state()))
		result = migrate()
		self.assertNotIn(U8_IT, result["migrated"])
		self.assertIn(U8_IT, result["skipped"])
		self.assertEqual(self._rows(U8_IT), set())

	def test_an_all_data_grant_becomes_org_viewer_rows_and_the_org_viewer_role(self):
		make_test_user(U8_GRANT, self.company_a)
		_set_legacy_roles(U8_GRANT, [])
		grant = make_test_company_grant(
			U8_GRANT, [self.company_a, self.company_b], level="All data", allow_export=1
		)
		# The fixture also writes the rows; take the site back to before the switch.
		frappe.db.delete("HelixHR Access Assignment", {"user": U8_GRANT})
		frappe.db.delete("Has Role", {"parent": U8_GRANT, "role": "HelixHR Org Viewer"})
		frappe.clear_cache(user=U8_GRANT)
		before = frappe.db.get_value("HelixHR Company Access", grant, ["enabled", "modified"])
		permissions = frappe.get_all("User Permission", filters={"user": U8_GRANT}, pluck="name")

		migrate()
		self.assertEqual(
			self._rows(U8_GRANT),
			{
				("HelixHR Org Viewer", self.company_a, 1, "migrated"),
				("HelixHR Org Viewer", self.company_b, 1, "migrated"),
			},
		)
		self.assertTrue(self._has_role(U8_GRANT, "HelixHR Org Viewer"))
		# Never the legacy grant or a User Permission.
		self.assertEqual(
			frappe.db.get_value("HelixHR Company Access", grant, ["enabled", "modified"]), before
		)
		self.assertEqual(
			frappe.get_all("User Permission", filters={"user": U8_GRANT}, pluck="name"), permissions
		)

	def test_a_reports_only_grant_merges_with_report_manager_by_or(self):
		make_test_user(U8_REPORTS, self.company_a)
		_set_legacy_roles(U8_REPORTS, ["HelixHR Report Manager"])
		make_test_company_grant(
			U8_REPORTS, [self.company_a, self.company_b], level="Reports only", allow_export=0
		)
		frappe.db.delete("HelixHR Access Assignment", {"user": U8_REPORTS})
		roles = {row.role for row in frappe.get_doc("User", U8_REPORTS).roles}

		migrate()
		self.assertEqual(
			self._rows(U8_REPORTS),
			{
				("HelixHR Report Manager", self.company_a, 1, "migrated"),
				("HelixHR Report Manager", self.company_b, 0, "migrated"),
			},
		)
		# Only Org viewer is ever added as a Frappe role.
		self.assertEqual({row.role for row in frappe.get_doc("User", U8_REPORTS).roles}, roles)

	def test_the_patch_ensures_both_new_roles_then_migrates(self):
		"""The patch runs before fixtures sync, so it creates the two roles
		itself; then it writes the rows."""
		from helixhr.patches.v1_0 import migrate_access_assignments as module

		_seed_legacy(self.company_a, self.company_b)
		with patch.object(module, "_ensure_role", wraps=module._ensure_role) as ensure:
			module.execute()
		self.assertEqual(
			[call.args[0] for call in ensure.call_args_list], ["HelixHR Org Viewer", "HelixHR Access Admin"]
		)
		for role in ("HelixHR Org Viewer", "HelixHR Access Admin"):
			self.assertTrue(frappe.db.exists("Role", role), role)
		self.assertEqual(self._rows(U8_HR_ALL), {("HR Manager", ALL_ORGS_SCOPE_KEY, 0, "migrated")})
		self.assertEqual(self._rows(U8_IT), {("IT Team", self.company_a, 0, "migrated")})

	def test_migrate_expires_no_export(self):
		_seed_legacy(self.company_a, self.company_b)
		ready = frappe.db.count("HelixHR Report Export", {"status": "Ready"})
		module = "helixhr.helixhr.doctype.helixhr_report_export.helixhr_report_export"
		with patch(f"{module}.expire_export") as expire:
			migrate()
		expire.assert_not_called()
		self.assertEqual(frappe.db.count("HelixHR Report Export", {"status": "Ready"}), ready)


class TestParityCheck(_MigrationCase):
	"""U8 (R26, KTD9): after the switch, every answer that moved from the
	stored snapshot is declared by a structured note, a named fix or a
	recipient rule -- or reported as undeclared."""

	def setUp(self):
		super().setUp()
		_seed_legacy(self.company_a, self.company_b)
		migrate()
		self.old = _old_answers(self.company_a, self.companies)

	def _check(self, users, recipients=None):
		handle, path = tempfile.mkstemp(suffix=".json")
		with os.fdopen(handle, "w") as out:
			json.dump(
				{"users": {user: self.old[user] for user in users}, "recipients": recipients or {}}, out
			)
		self.addCleanup(os.remove, path)
		result = parity_check(path)
		self.addCleanup(os.remove, result["report"])
		with open(result["report"]) as report:
			return result, json.load(report)

	def _labels(self, report, user):
		return {item["key"]: item["label"] for item in report["people"].get(user, [])}

	def test_seeded_people_show_zero_undeclared_differences(self):
		result, report = self._check(list(_U8_SEEDED))
		self.assertEqual(result["undeclared"], 0, report)
		self.assertEqual(report["counts"]["undeclared"], 0)
		# Nothing moved for the IT Admin or the Desk-only HR Manager.
		self.assertNotIn(U8_IT, report["people"])
		self.assertNotIn(U8_HR_ALL, report["people"])
		self.assertEqual(stat.S_IMODE(os.stat(result["report"]).st_mode), 0o600)

	def test_a_system_manager_with_an_employee_is_declared(self):
		_result, report = self._check([U8_SM])
		labels = self._labels(report, U8_SM)
		self.assertEqual(labels["admin"], "declared widening")
		self.assertEqual(labels["it_scope"], "declared widening")
		self.assertEqual(set(labels.values()), {"declared widening"})
		self.assertTrue(all(item["notes"] == ["system_manager_unscoped"] for item in report["people"][U8_SM]))

	def test_a_gated_system_manager_or_hr_manager_narrows_as_a_security_fix(self):
		"""Today's code ignored a disabled login; R4 zeroes it (person_gate).
		Every move is a narrowing, so every one is the declared fix."""
		_result, report = self._check([U8_SM_OFF, U8_HR_OFF])
		for user in (U8_SM_OFF, U8_HR_OFF):
			with self.subTest(user=user):
				items = report["people"][user]
				self.assertEqual({item["label"] for item in items}, {"security fix"}, items)
				self.assertTrue(all(item["notes"] == ["person_gate"] for item in items))
				self.assertEqual(self._labels(report, user)["admin"], "security fix")

	def test_a_system_manager_with_no_employee_widens_only_the_it_queue(self):
		_result, report = self._check([U8_SM_IT])
		self.assertEqual(
			report["people"][U8_SM_IT],
			[
				{
					"key": "it_scope",
					"label": "declared widening",
					"direction": "wider",
					"notes": ["system_manager_it_all_orgs"],
					"before": [],
					"after": self.companies,
				}
			],
		)

	def test_a_narrowing_under_a_widening_note_is_undeclared(self):
		"""The Delivery Manager's note declares ``project`` wider. Had today's
		answer been every project, the org scope after is narrower: that is
		a regression, not the declared widening."""
		self.old[U8_DM] = {**self.old[U8_DM], "project": {"kind": "unscoped", "company": None}}
		result, report = self._check([U8_DM])
		self.assertEqual(self._labels(report, U8_DM)["project"], "undeclared")
		self.assertEqual(result["undeclared"], 1)
		# The other keys still widen, and stay declared.
		self.assertEqual(self._labels(report, U8_DM)["project_read"], "declared widening")

	def test_a_flag_is_judged_on_its_own(self):
		"""The System Manager's note names ``flags`` for widening: a flag it
		gains is declared, one it loses beside it is not."""
		self.old[U8_SM] = {**self.old[U8_SM], "flags": {**self.old[U8_SM]["flags"], "can_configure": False}}
		with patch.object(api, "_can_manage_documents", return_value=False):
			_result, report = self._check([U8_SM])
		labels = self._labels(report, U8_SM)
		self.assertEqual(labels["flags.can_configure"], "declared widening")
		self.assertEqual(labels["flags.can_manage_documents"], "undeclared")
		self.assertNotIn("flags", labels)

	def test_a_delivery_managers_widening_is_declared(self):
		_result, report = self._check([U8_DM])
		labels = self._labels(report, U8_DM)
		self.assertEqual(labels["project"], "declared widening")
		self.assertEqual(labels["project_read"], "declared widening")
		# The B project's reports now run for B too.
		self.assertEqual(labels["reports"], "declared widening")
		self.assertEqual(set(labels.values()), {"declared widening"})

	def test_a_resolver_that_ignores_all_orgs_rows_is_undeclared(self):
		read = helixhr_utils._read_role_scope_entry

		def ignoring_all_orgs(user):
			entry = read(user)
			for key in ("rows", "export"):
				entry[key] = {
					role: scopes - {ALL_ORGS_SCOPE_KEY}
					for role, scopes in entry[key].items()
					if scopes - {ALL_ORGS_SCOPE_KEY}
				}
			return entry

		clear_role_scope_cache()
		with patch.object(helixhr_utils, "_read_role_scope_entry", ignoring_all_orgs):
			result, report = self._check([U8_IT, U8_HR_ALL])
		self.assertGreater(result["undeclared"], 0)
		self.assertEqual(self._labels(report, U8_HR_ALL)["admin"], "undeclared")
		self.assertNotIn(U8_IT, report["people"])

	def test_a_row_that_does_not_match_the_plan_is_undeclared(self):
		frappe.db.delete(
			"HelixHR Access Assignment",
			{"user": U8_DM, "role": "HelixHR Delivery Manager", "company": self.company_b},
		)
		clear_role_scope_cache(U8_DM)
		result, report = self._check([U8_IT])
		self.assertGreater(result["undeclared"], 0)
		self.assertEqual(self._labels(report, U8_DM)["rows"], "undeclared")

	def test_recipient_moves_are_declared_only_by_a_rule(self):
		self.assertEqual(set(DECLARED_RECIPIENT_CHANGES), {"system_manager_elsewhere", "unscoped_hr_not_it"})
		now = snapshot([])["recipients"][self.company_b]["IT Team"]
		# A System Manager anchored in A is no longer mailed for B: declared.
		_result, report = self._check([], {self.company_b: {"IT Team": sorted({*now, U8_SM})}})
		self.assertEqual(
			[(item["user"], item["change"], item["label"]) for item in report["recipients"]],
			[(U8_SM, "removed", "declared recipient change")],
		)
		self.assertEqual(report["recipients"][0]["notes"], ["system_manager_elsewhere"])
		# The same move for an IT Admin who is not a System Manager is not.
		result, report = self._check([], {self.company_b: {"IT Team": sorted({*now, U8_IT})}})
		self.assertEqual(result["undeclared"], 1)
		self.assertEqual(report["recipients"][0]["label"], "undeclared")
