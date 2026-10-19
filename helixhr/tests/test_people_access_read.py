"""People & access reads (plan 2026-10-19-001 U6, R17-R24): the Elevated
table with its R19 warnings, the All employees search and the person view.

Company A is the test company, B the baseline second company. Every login is
this module's own; rows, roles and change rows persist between methods
(rollback is per class), so `setUp` resets them.
"""

import frappe
from frappe.tests import IntegrationTestCase

from helixhr.api import (
	_access_change_lines,
	access_token,
	get_elevated_access,
	get_person_access,
	save_person_access,
	search_people_access,
)
from helixhr.tests.utils import (
	ensure_baseline_company,
	ensure_test_company,
	ensure_test_gender,
	make_assignment,
	make_test_user,
)
from helixhr.utils import (
	ACCESS_ADMIN_ROLE,
	ASSIGNABLE_ROLES,
	DELIVERY_MANAGER_ROLE,
	ORG_VIEWER_ROLE,
	PORTAL_ADMIN_ROLE,
	REPORT_MANAGER_ROLE,
	ROLE_LABELS,
	SELF_EDIT_REFUSAL,
	TARGET_LOCKED_REFUSAL,
	access_summary,
	clear_role_scope_cache,
)

SM = "par-sm@helixhr.test"
MANAGE = "par-manage@helixhr.test"
PA_A = "par-pa-a@helixhr.test"
PLAIN = "par-plain@helixhr.test"
ROLE_ONLY = "par-role-only@helixhr.test"
MISSING = "par-missing@helixhr.test"
MIGRATED = "par-migrated@helixhr.test"
MIGRATED_SAVED = "par-migrated-saved@helixhr.test"
LEFT = "par-left@helixhr.test"
MOVED = "par-moved@helixhr.test"
HR_NAMED = "par-hr-named@helixhr.test"
EXPORTER = "par-exporter@helixhr.test"
DISABLED = "par-disabled@helixhr.test"
B_ONLY = "par-b-only@helixhr.test"
# HR Manager on All orgs with no Employee: reachable by full authority only.
LEADER = "par-leader@helixhr.test"
NO_LOGIN_NUMBER = "par-no-login"

_EMPLOYEE_USERS = (
	SM,
	MANAGE,
	PA_A,
	PLAIN,
	ROLE_ONLY,
	MISSING,
	MIGRATED,
	MIGRATED_SAVED,
	LEFT,
	MOVED,
	HR_NAMED,
	EXPORTER,
	DISABLED,
)
_MODULE_USERS = (*_EMPLOYEE_USERS, B_ONLY, LEADER)
_MANAGED = (*ASSIGNABLE_ROLES, "System Manager")
IT = "IT Team"


def _as(user, fn, *args, **kwargs):
	frappe.set_user(user)
	try:
		return fn(*args, **kwargs)
	finally:
		frappe.set_user("Administrator")


def _ensure_user(user):
	if not frappe.db.exists("User", user):
		frappe.get_doc(
			{"doctype": "User", "email": user, "first_name": user.split("@")[0], "send_welcome_email": 0}
		).insert(ignore_permissions=True)


def _no_login_employee(company):
	name = frappe.db.get_value("Employee", {"employee_number": NO_LOGIN_NUMBER})
	if name:
		return name
	return (
		frappe.get_doc(
			{
				"doctype": "Employee",
				"employee_number": NO_LOGIN_NUMBER,
				"first_name": "Parnologin",
				"company": company,
				"date_of_birth": "1990-01-01",
				"date_of_joining": "2020-01-01",
				"gender": ensure_test_gender(),
				"status": "Active",
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def _keys(person):
	return {warning["key"] for warning in person["warnings"]}


def _row(role, company=None, export=0):
	return {"role": role, "all_orgs": 0 if company else 1, "company": company, "allow_export": export}


class _PeopleAccessRead(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.a = ensure_test_company()
		self.b = ensure_baseline_company()
		frappe.db.delete("HelixHR Access Assignment", {"user": ["in", list(_MODULE_USERS)]})
		frappe.db.delete("HelixHR Access Change", {"target_user": ["in", list(_MODULE_USERS)]})
		self.employees = {}
		for user in _EMPLOYEE_USERS:
			self.employees[user] = make_test_user(user, self.a, create_user_permission=0, status="Active")
		self.employees[B_ONLY] = make_test_user(B_ONLY, self.b, create_user_permission=0, status="Active")
		_ensure_user(LEADER)
		for user in _MODULE_USERS:
			frappe.db.set_value("User", user, "enabled", 1)
			frappe.db.delete("Has Role", {"parent": user, "role": ["in", list(_MANAGED)]})
			frappe.clear_cache(user=user)
		clear_role_scope_cache()

		frappe.get_doc("User", SM).add_roles("System Manager", "HR Manager")
		make_assignment(MANAGE, ACCESS_ADMIN_ROLE, "all")
		make_assignment(PA_A, PORTAL_ADMIN_ROLE, [self.a])
		# R9 both ways: a Frappe role with no row, a row with no Frappe role.
		frappe.get_doc("User", ROLE_ONLY).add_roles("HR Manager")
		make_assignment(MISSING, IT, [self.a], add_role=False)
		# Like U8's migrated "All data" grant: the row, no Frappe role.
		make_assignment(MIGRATED, ORG_VIEWER_ROLE, [self.a], origin="migrated", add_role=False)
		make_assignment(MIGRATED_SAVED, ORG_VIEWER_ROLE, [self.a], origin="migrated", add_role=False)
		frappe.get_doc(
			{
				"doctype": "HelixHR Access Change",
				"target_user": MIGRATED_SAVED,
				"changed_by": SM,
				"before": "[]",
				"after": "[]",
			}
		).insert(ignore_permissions=True)
		make_assignment(LEFT, IT, [self.a])
		frappe.db.set_value("Employee", self.employees[LEFT], "status", "Left")
		# An Org A employee whose only row is in B: "moved".
		make_assignment(MOVED, DELIVERY_MANAGER_ROLE, [self.b])
		make_assignment(HR_NAMED, "HR Manager", [self.a])
		make_assignment(EXPORTER, REPORT_MANAGER_ROLE, [self.a], export=1)
		make_assignment(DISABLED, IT, [self.a])
		frappe.db.set_value("User", DISABLED, "enabled", 0)
		make_assignment(B_ONLY, DELIVERY_MANAGER_ROLE, [self.b])
		make_assignment(LEADER, "HR Manager", "all")
		for user in _MODULE_USERS:
			frappe.clear_cache(user=user)
		clear_role_scope_cache()
		self.no_login = _no_login_employee(self.a)

	def tearDown(self):
		frappe.set_user("Administrator")
		clear_role_scope_cache()

	def _elevated(self, caller):
		return {person["user"]: person for person in _as(caller, get_elevated_access)["people"]}


class TestElevatedWarnings(_PeopleAccessRead):
	def test_each_r19_warning_lands_on_its_person(self):
		people = self._elevated(SM)
		self.assertIn("role_outside_helixhr", _keys(people[ROLE_ONLY]))
		self.assertEqual(
			people[ROLE_ONLY]["roles"],
			[
				{
					"role": "HR Manager",
					"label": "HR Manager",
					"assigned": False,
					"all_orgs": False,
					"companies": [],
					"export": False,
				}
			],
		)
		self.assertIn("role_missing_in_frappe", _keys(people[MISSING]))
		self.assertIn("employee_left", _keys(people[LEFT]))
		self.assertIn("user_disabled", _keys(people[DISABLED]))
		self.assertFalse(people[DISABLED]["has_login"])
		self.assertIn("own_org_not_covered", _keys(people[MOVED]))
		self.assertNotIn("own_org_not_covered", _keys(people[HR_NAMED]))
		self.assertIn("export_on", _keys(people[EXPORTER]))
		self.assertEqual(_keys(people[MANAGE]), {"all_orgs", "can_manage_access"})
		self.assertEqual(_keys(people[LEADER]), {"all_orgs"})

	def test_a_named_org_hr_manager_carries_the_desk_info_warning(self):
		person = self._elevated(SM)[HR_NAMED]
		self.assertEqual(person["warnings"], [{"key": "desk_hr_reach_all_orgs", "kind": "info"}])
		self.assertEqual(person["roles"][0]["companies"], [self.a])
		self.assertEqual(person["own_company"], self.a)

	def test_a_migrated_grant_row_warns_only_after_the_first_save(self):
		people = self._elevated(SM)
		self.assertEqual(people[MIGRATED]["warnings"], [])
		self.assertIn("role_missing_in_frappe", _keys(people[MIGRATED_SAVED]))
		self.assertEqual(people[MIGRATED_SAVED]["last_change"]["by"], SM)

	def test_warning_kinds_follow_r19(self):
		kinds = {
			warning["key"]: warning["kind"]
			for person in self._elevated(SM).values()
			for warning in person["warnings"]
		}
		for key in ("employee_left", "user_disabled", "role_outside_helixhr", "own_org_not_covered"):
			self.assertEqual(kinds[key], "problem")
		for key in ("desk_hr_reach_all_orgs", "all_orgs", "can_manage_access", "export_on"):
			self.assertEqual(kinds[key], "info")

	def test_system_managers_are_unscoped_with_no_drift_warning(self):
		people = self._elevated(SM)
		person = people[SM]
		self.assertTrue(person["system_manager"])
		# Holds the HR Manager Frappe role with no row: no drift for a System Manager.
		self.assertEqual(person["warnings"], [])
		self.assertEqual(person["roles"], [])
		self.assertNotIn("Administrator", people)
		self.assertNotIn("Guest", people)

	def test_counts_match_the_people(self):
		out = _as(SM, get_elevated_access)
		problems = [p for p in out["people"] if any(w["kind"] == "problem" for w in p["warnings"])]
		self.assertEqual(out["counts"]["needs_attention"], len(problems))
		for key, count in out["counts"]["by_warning"].items():
			self.assertEqual(count, sum(1 for p in out["people"] if key in _keys(p)), key)

	def test_problems_first_then_all_orgs_and_manage_access_then_name(self):
		def tier(person):
			if any(w["kind"] == "problem" for w in person["warnings"]):
				return 0
			return 1 if _keys(person) & {"all_orgs", "can_manage_access"} else 2

		people = _as(SM, get_elevated_access)["people"]
		keys = [(tier(p), p["full_name"].lower()) for p in people]
		self.assertEqual(keys, sorted(keys))
		self.assertLess([p["user"] for p in people].index(MISSING), [p["user"] for p in people].index(MANAGE))

	def test_a_fixed_number_of_queries_whatever_the_head_count(self):
		_as(SM, get_elevated_access)  # warm the caller's caches
		frappe.set_user(SM)
		try:
			with self.assertQueryCount(8):
				people = get_elevated_access()["people"]
		finally:
			frappe.set_user("Administrator")
		# A per-person query would blow the bound many times over.
		self.assertGreater(len(people), 8)


class TestElevatedReach(_PeopleAccessRead):
	def test_a_named_org_portal_admin_sees_only_people_in_its_orgs(self):
		people = self._elevated(PA_A)
		self.assertTrue({MISSING, MOVED, HR_NAMED} <= set(people))
		self.assertNotIn(B_ONLY, people)
		self.assertNotIn(LEADER, people)

	def test_full_authority_sees_everyone(self):
		for caller in (SM, MANAGE):
			people = self._elevated(caller)
			self.assertTrue({B_ONLY, LEADER, MOVED} <= set(people), caller)

	def test_a_row_in_reach_reaches_a_person_employed_elsewhere(self):
		make_assignment(B_ONLY, IT, [self.a])
		self.assertIn(B_ONLY, self._elevated(PA_A))

	def test_callers_without_people_access_reach_are_refused(self):
		for caller in (PLAIN, HR_NAMED):
			for fn, args in (
				(get_elevated_access, ()),
				(search_people_access, ()),
				(get_person_access, (MISSING,)),
			):
				with self.subTest(caller=caller, fn=fn.__name__), self.assertRaises(frappe.PermissionError):
					_as(caller, fn, *args)


class TestSearchPeopleAccess(_PeopleAccessRead):
	def _users(self, caller, **kwargs):
		return {row["employee"]: row for row in _as(caller, search_people_access, **kwargs)["rows"]}

	def test_an_employee_without_a_login_is_listed_and_found_by_id(self):
		rows = self._users(PA_A, query=self.no_login)
		self.assertEqual(
			rows[self.no_login],
			{
				"employee": self.no_login,
				"employee_name": "Parnologin",
				"company": self.a,
				"user": None,
				"has_login": False,
			},
		)
		self.assertIn(self.no_login, self._users(PA_A, query="parnolog"))

	def test_search_by_email_and_login_state(self):
		rows = self._users(SM, query="par-disabled@")
		self.assertFalse(rows[self.employees[DISABLED]]["has_login"])
		self.assertTrue(self._users(SM, query="par-plain@")[self.employees[PLAIN]]["has_login"])

	def test_reach_and_the_org_filter(self):
		self.assertNotIn(self.employees[B_ONLY], self._users(PA_A, query="par-b-only"))
		self.assertIn(self.employees[B_ONLY], self._users(SM, query="par-b-only"))
		rows = _as(PA_A, search_people_access, company=self.a, page_length=100)["rows"]
		self.assertTrue(rows)
		self.assertEqual({row["company"] for row in rows}, {self.a})
		with self.assertRaises(frappe.PermissionError):
			_as(PA_A, search_people_access, company=self.b)

	def test_only_active_employees_and_paging(self):
		self.assertNotIn(self.employees[LEFT], self._users(SM, query="par-left"))
		page = _as(SM, search_people_access, query="par-", page_length=1)
		self.assertEqual(len(page["rows"]), 1)
		self.assertTrue(page["has_more"])

	def test_a_one_character_query_does_not_filter(self):
		self.assertEqual(
			_as(PA_A, search_people_access, query="z", page_length=5),
			_as(PA_A, search_people_access, page_length=5),
		)


class TestPersonAccess(_PeopleAccessRead):
	def test_an_out_of_reach_row_is_returned_read_only(self):
		person = _as(PA_A, get_person_access, MOVED)
		self.assertEqual(person["lock"], {"reason": TARGET_LOCKED_REFUSAL, "removal_only": False})
		self.assertEqual(
			[(row["role"], row["company"], row["editable"]) for row in person["rows"]],
			[(DELIVERY_MANAGER_ROLE, self.b, False)],
		)
		self.assertEqual(person["presets"], [])

	def test_full_authority_edits_the_same_person(self):
		person = _as(SM, get_person_access, MOVED)
		self.assertIsNone(person["lock"])
		self.assertTrue(person["rows"][0]["editable"])
		self.assertEqual(person["rows"][0]["label"], "Delivery Manager")
		self.assertIn("own_org_not_covered", _keys(person))
		self.assertIn(f"Their own org, {self.a}, is not covered by these roles.", person["summary"])
		self.assertEqual(person["token"], access_token(MOVED))

	def test_a_portal_admin_assigns_its_four_roles_on_its_orgs(self):
		person = _as(PA_A, get_person_access, PLAIN)
		self.assertIsNone(person["lock"])
		self.assertEqual(
			{role["label"] for role in person["assignable"]["roles"]},
			{"Delivery Manager", "IT Admin", "Reports", "Org viewer"},
		)
		self.assertEqual(person["assignable"]["companies"], [self.a])
		self.assertFalse(person["assignable"]["all_orgs"])
		self.assertEqual(person["presets"], [])
		self.assertEqual(person["summary"], ["Employee access only."])

	def test_manage_access_gets_the_leadership_preset(self):
		person = _as(MANAGE, get_person_access, PLAIN)
		self.assertEqual(person["presets"], ["Leadership"])
		self.assertEqual(len(person["assignable"]["roles"]), len(ASSIGNABLE_ROLES))
		self.assertTrue(person["assignable"]["all_orgs"])
		self.assertIn(self.b, person["assignable"]["companies"])

	def test_self_is_read_only_with_the_self_sentence(self):
		person = _as(PA_A, get_person_access, PA_A)
		self.assertEqual(person["lock"], {"reason": SELF_EDIT_REFUSAL, "removal_only": False})
		self.assertFalse(any(row["editable"] for row in person["rows"]))

	def test_disabled_or_left_allows_removal_only(self):
		for user in (DISABLED, LEFT):
			person = _as(SM, get_person_access, user)
			self.assertTrue(person["lock"]["removal_only"], user)
			self.assertTrue(person["rows"][0]["editable"], user)
			self.assertIn("none of this access applies", person["summary"][0])

	def test_no_employee_all_orgs_person_opens_for_full_authority_only(self):
		person = _as(MANAGE, get_person_access, LEADER)
		self.assertIsNone(person["employee"])
		self.assertIn("Acts as HR Manager in all orgs, including new ones.", person["summary"])
		for user in (LEADER, B_ONLY, "nobody-here@helixhr.test", "Administrator"):
			with self.subTest(user=user), self.assertRaises(frappe.PermissionError):
				_as(PA_A, get_person_access, user)

	def test_drift_is_shown_on_the_rows(self):
		person = _as(SM, get_person_access, ROLE_ONLY)
		self.assertEqual(person["held_roles_without_rows"], [{"role": "HR Manager", "label": "HR Manager"}])
		self.assertIn("HR Manager is set outside HelixHR and gives no portal access.", person["summary"])
		self.assertTrue(_as(SM, get_person_access, MISSING)["rows"][0]["missing_in_frappe"])
		self.assertFalse(_as(SM, get_person_access, MIGRATED)["rows"][0]["missing_in_frappe"])
		self.assertTrue(_as(SM, get_person_access, MIGRATED_SAVED)["rows"][0]["missing_in_frappe"])

	def test_history_names_who_what_when_and_why(self):
		reason = "Board <b>pack</b>"
		_as(SM, save_person_access, PLAIN, [_row(IT, self.a)], access_token(PLAIN))
		_as(
			SM,
			save_person_access,
			PLAIN,
			[_row(ORG_VIEWER_ROLE, self.a, export=1)],
			access_token(PLAIN),
			reason=reason,
		)
		history = _as(MANAGE, get_person_access, PLAIN)["history"]
		self.assertEqual(len(history), 2)
		latest, first = history
		self.assertEqual(latest["gains"], [f"Org viewer · {self.a}, with export"])
		self.assertEqual(latest["losses"], [f"IT Admin · {self.a}"])
		# Plain text, stored and returned as typed; the screen renders it as text.
		self.assertEqual(latest["reason"], reason)
		self.assertEqual(latest["changed_by"], SM)
		self.assertEqual(latest["changed_by_name"], frappe.db.get_value("User", SM, "full_name"))
		self.assertEqual((first["gains"], first["losses"]), ([f"IT Admin · {self.a}"], []))

	def test_an_export_switch_reads_as_one_line(self):
		before = frappe.as_json([_row(REPORT_MANAGER_ROLE, "Org A")])
		after = frappe.as_json([_row(REPORT_MANAGER_ROLE, "Org A", export=1)])
		self.assertEqual(_access_change_lines(before, after), (["Export on Reports · Org A"], []))
		self.assertEqual(_access_change_lines(after, before), ([], ["Export on Reports · Org A"]))


class TestAccessSummary(IntegrationTestCase):
	def test_copy_table_sentences(self):
		rows = [
			_row("HR Manager", "Org A"),
			_row("HR Manager", "Org B"),
			_row(REPORT_MANAGER_ROLE, export=1),
			_row(ORG_VIEWER_ROLE, "Org A", export=1),
			_row(ORG_VIEWER_ROLE, "Org B"),
		]
		summary = access_summary(rows, {"HR Manager"}, direct_reports=3, own_company="Org A")
		self.assertEqual(
			summary,
			[
				"HR Manager in Org A and Org B.",
				"Reports in all orgs, including new ones, with export.",
				"Org viewer in Org A and Org B, with export in Org A.",
				"Desk access for HR roles is not yet limited to these orgs.",
				"Manages 3 people.",
			],
		)

	def test_leadership_and_uncovered_own_org(self):
		self.assertEqual(
			access_summary([_row("HR Manager")], {"HR Manager"}, direct_reports=1),
			["Acts as HR Manager in all orgs, including new ones.", "Manages 1 person."],
		)
		self.assertIn(
			"Their own org, Org C, is not covered by these roles.",
			access_summary([_row(IT, "Org A")], {IT}, own_company="Org C"),
		)
		self.assertIn(
			"IT Admin is missing in Frappe, so it gives no access until restored.",
			access_summary([_row(IT, "Org A")], set(), own_company="Org A"),
		)

	def test_no_raw_role_names_in_any_sentence(self):
		rows = [_row(role, "Org A") for role in ASSIGNABLE_ROLES]
		sentences = access_summary(rows, set(), own_company="Org B")
		sentences += access_summary([], set(ASSIGNABLE_ROLES))
		self.assertTrue(sentences)
		for role, label in ROLE_LABELS.items():
			if role == label:
				continue
			for sentence in sentences:
				self.assertNotIn(role, sentence)
