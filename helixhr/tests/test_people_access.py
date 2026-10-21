"""People & access: the save's mechanics (plan 2026-10-19-001 U5) -- rows
written, changed and removed, the payload refused whole, the HR Manager
self-permission rule, the change log -- and the read endpoints' basics (U6;
their warnings and reach are `test_people_access_read`). Authority is
`test_access_authority`. Every refusal is asserted as the caller's own role,
never as Administrator."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from helixhr import api
from helixhr.api import (
	access_token,
	get_elevated_access,
	get_person_access,
	save_person_access,
	search_people_access,
)
from helixhr.tests.utils import (
	clear_test_company_grants,
	ensure_baseline_company,
	ensure_test_company,
	make_assignment,
	make_test_portal_admin,
	make_test_user,
)
from helixhr.utils import (
	ALL_ORGS_SCOPE_KEY,
	ORG_VIEWER_ROLE,
	PORTAL_ADMIN_ROLE,
	RATE_LIMIT_POLICY,
	REPORT_MANAGER_ROLE,
	SELF_EDIT_REFUSAL,
)

SYSTEM_MANAGER_USER = "people-system-manager@helixhr.test"
HOME_TARGET = "people-home@helixhr.test"
FOREIGN_TARGET = "people-foreign@helixhr.test"
HOME_ONLY_ADMIN = "people-admin-home@helixhr.test"
NO_EMPLOYEE_TARGET = "people-no-employee@helixhr.test"
IT = "IT Team"
DM = "HelixHR Delivery Manager"
_TARGETS = (HOME_TARGET, FOREIGN_TARGET, NO_EMPLOYEE_TARGET)


def _as(user, fn, *args, **kwargs):
	frappe.set_user(user)
	try:
		return fn(*args, **kwargs)
	finally:
		frappe.set_user("Administrator")


def _roles(user):
	return {row.role for row in frappe.get_doc("User", user).roles}


def _set_roles(user, add=(), remove=()):
	doc = frappe.get_doc("User", user)
	doc.set("roles", [row for row in doc.roles if row.role not in remove])
	held = {row.role for row in doc.roles}
	for role in add:
		if role not in held:
			doc.append("roles", {"role": role})
	doc.save(ignore_permissions=True)
	frappe.clear_cache(user=user)


def _row(role, company=None, export=0):
	return {"role": role, "all_orgs": 0 if company else 1, "company": company, "allow_export": export}


def _rows(user):
	return api._access_snapshot(api._access_rows(user))


def _change_count(user):
	# Change rows persist between methods (rollback is per class): compare deltas.
	return frappe.db.count("HelixHR Access Change", {"target_user": user})


def _self_permission(user):
	employee = frappe.db.get_value("Employee", {"user_id": user})
	return frappe.db.exists("User Permission", {"user": user, "allow": "Employee", "for_value": employee})


class TestPeopleAccess(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.other = ensure_baseline_company()
		_, self.admin = make_test_portal_admin()
		frappe.db.delete("User Permission", {"user": self.admin, "allow": "Company"})
		# Plan 2026-10-19-001 U4: reach is the Portal Admin rows, not a Company
		# User Permission. Rows persist between methods: reset them.
		frappe.db.delete(
			"HelixHR Access Assignment",
			{"user": ["in", [self.admin, HOME_ONLY_ADMIN, *_TARGETS]]},
		)
		make_assignment(self.admin, PORTAL_ADMIN_ROLE, [self.company, self.other])
		make_test_user(SYSTEM_MANAGER_USER, self.company)
		_set_roles(SYSTEM_MANAGER_USER, add=["System Manager"])
		make_test_user(HOME_ONLY_ADMIN, self.company)
		make_assignment(HOME_ONLY_ADMIN, PORTAL_ADMIN_ROLE, [self.company])
		self.home = make_test_user(HOME_TARGET, self.company)
		self.foreign = make_test_user(FOREIGN_TARGET, self.other)
		if not frappe.db.exists("User", NO_EMPLOYEE_TARGET):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": NO_EMPLOYEE_TARGET,
					"first_name": "Nobody",
					"send_welcome_email": 0,
				}
			).insert(ignore_permissions=True)
		for user in _TARGETS:
			_set_roles(user, remove=[IT, DM, "HR User", "HR Manager", ORG_VIEWER_ROLE, REPORT_MANAGER_ROLE])
		# An earlier method's HR Manager save dropped the self permission.
		api._set_self_employee_permission(HOME_TARGET, self.home, scoped=True)
		clear_test_company_grants()

	def tearDown(self):
		frappe.set_user("Administrator")

	def _save(self, caller, user, rows, reason=None, **kwargs):
		return _as(caller, save_person_access, user, rows, access_token(user), reason=reason, **kwargs)

	# --- the save writes rows, roles and one change ------------------------

	def test_one_save_adds_changes_and_removes_rows_with_one_change_each(self):
		changes = _change_count(HOME_TARGET)
		self._save(
			self.admin,
			HOME_TARGET,
			[_row(IT, self.company), _row(REPORT_MANAGER_ROLE, self.other, 1)],
			reason="Month-end.",
		)
		self.assertTrue({IT, REPORT_MANAGER_ROLE} <= _roles(HOME_TARGET))
		out = self._save(
			self.admin,
			HOME_TARGET,
			[_row(REPORT_MANAGER_ROLE, self.other), _row(DM, self.company)],
		)
		self.assertEqual(out["rows"], [_row(DM, self.company), _row(REPORT_MANAGER_ROLE, self.other)])
		roles = _roles(HOME_TARGET)
		self.assertNotIn(IT, roles)
		self.assertTrue({DM, REPORT_MANAGER_ROLE} <= roles)
		self.assertEqual(_change_count(HOME_TARGET), changes + 2)
		change = frappe.get_doc("HelixHR Access Change", out["change"])
		self.assertEqual(
			frappe.parse_json(change.before),
			[_row(REPORT_MANAGER_ROLE, self.other, 1), _row(IT, self.company)],
		)
		self.assertEqual(frappe.parse_json(change.after), out["rows"])

	def test_an_all_orgs_row_stores_no_company(self):
		self._save(SYSTEM_MANAGER_USER, HOME_TARGET, [_row(ORG_VIEWER_ROLE)], reason="Board member.")
		row = frappe.db.get_value(
			"HelixHR Access Assignment",
			{"user": HOME_TARGET, "role": ORG_VIEWER_ROLE},
			["all_orgs", "company", "scope_key", "origin"],
			as_dict=True,
		)
		self.assertEqual(
			(row.all_orgs, row.company or None, row.scope_key, row.origin),
			(1, None, ALL_ORGS_SCOPE_KEY, "manual"),
		)

	def test_an_unchanged_save_writes_nothing(self):
		self._save(self.admin, HOME_TARGET, [_row(IT, self.company)])
		changes = _change_count(HOME_TARGET)
		token = access_token(HOME_TARGET)
		out = self._save(self.admin, HOME_TARGET, [_row(IT, self.company)])
		self.assertIsNone(out["change"])
		self.assertEqual(out["token"], token)
		self.assertEqual(_change_count(HOME_TARGET), changes)

	def test_a_bad_payload_is_refused_whole_before_any_write(self):
		good = _row(IT, self.company)
		changes = _change_count(HOME_TARGET)
		for rows in (
			"not json",
			{"role": IT},
			[good, "row"],
			[good, _row("System Manager", self.company)],
			[good, {**_row(DM, self.company), "all_orgs": 1}],
			[good, {"role": DM, "all_orgs": 0, "company": None}],
			[good, _row(IT, self.company, export=1)],
			[good, _row(DM, "No Such Company")],
			[good, _row("HelixHR Notification Manager", self.company)],
			[good, good],
			[good, {**_row(DM, self.company), "allow_export": "maybe"}],
			[good] * 201,
		):
			with self.subTest(rows=str(rows)[:80]), self.assertRaises(frappe.ValidationError):
				self._save(self.admin, HOME_TARGET, rows)
		self.assertEqual(_rows(HOME_TARGET), [])
		self.assertNotIn(IT, _roles(HOME_TARGET))
		self.assertEqual(_change_count(HOME_TARGET), changes)

	def test_flags_parse_strictly(self):
		self.assertIs(api._flag("true"), True)
		self.assertIs(api._flag("0"), False)
		self.assertIs(api._flag(1), True)
		self.assertIsNone(api._flag("yes please"))
		self.assertIsNone(api._flag(None))

	def test_rows_as_a_json_string_and_the_reach_of_a_two_org_admin(self):
		"""Plan 2026-10-19-001 U4: a Portal Admin row on the employee's org
		reaches them, whatever company the admin is in."""
		self._save(self.admin, FOREIGN_TARGET, frappe.as_json([_row(IT, self.other)]))
		self.assertEqual(_rows(FOREIGN_TARGET), [_row(IT, self.other)])
		with self.assertRaises(frappe.PermissionError):
			self._save(HOME_ONLY_ADMIN, FOREIGN_TARGET, [])

	# --- the HR Manager self-permission rule ------------------------------

	def test_hr_manager_keeps_the_self_user_permission_in_step(self):
		self.assertTrue(_self_permission(HOME_TARGET))
		self._save(
			SYSTEM_MANAGER_USER, HOME_TARGET, [_row("HR Manager", self.company)], reason="New HR lead."
		)
		self.assertIn("HR Manager", _roles(HOME_TARGET))
		self.assertFalse(_self_permission(HOME_TARGET))
		self._save(SYSTEM_MANAGER_USER, HOME_TARGET, [])
		self.assertNotIn("HR Manager", _roles(HOME_TARGET))
		self.assertTrue(_self_permission(HOME_TARGET))

	def test_granting_hr_manager_drops_only_the_self_employee_permission(self):
		colleague = make_test_user("people-colleague@helixhr.test", self.company)
		other = frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": HOME_TARGET,
				"allow": "Employee",
				"for_value": colleague,
				"apply_to_all_doctypes": 1,
			}
		).insert(ignore_permissions=True)
		self.addCleanup(frappe.db.delete, "User Permission", {"name": other.name})
		self._save(
			SYSTEM_MANAGER_USER, HOME_TARGET, [_row("HR Manager", self.company)], reason="New HR lead."
		)
		self.assertFalse(_self_permission(HOME_TARGET))
		self.assertTrue(frappe.db.exists("User Permission", other.name))

	def test_an_hr_manager_with_no_employee_gets_no_user_permission(self):
		self._save(SYSTEM_MANAGER_USER, NO_EMPLOYEE_TARGET, [_row("HR Manager")], reason="Leadership.")
		self.assertIn("HR Manager", _roles(NO_EMPLOYEE_TARGET))
		self.assertFalse(
			frappe.db.exists("User Permission", {"user": NO_EMPLOYEE_TARGET, "allow": "Employee"})
		)

	def test_a_second_hr_manager_row_leaves_the_user_permission_alone(self):
		self._save(
			SYSTEM_MANAGER_USER, HOME_TARGET, [_row("HR Manager", self.company)], reason="New HR lead."
		)
		with patch.object(api, "_set_self_employee_permission") as rescope:
			self._save(
				SYSTEM_MANAGER_USER,
				HOME_TARGET,
				[_row("HR Manager", self.company), _row("HR Manager", self.other)],
				reason="Both orgs.",
			)
		rescope.assert_not_called()

	# --- the read endpoints (plan 2026-10-19-001 U6) -----------------------

	def test_self_read_is_locked_with_the_self_sentence(self):
		person = _as(self.admin, get_person_access, self.admin)
		self.assertEqual(person["lock"], {"reason": SELF_EDIT_REFUSAL, "removal_only": False})

	def test_admin_on_home_only_reaches_home_only(self):
		with self.assertRaises(frappe.PermissionError):
			_as(HOME_ONLY_ADMIN, get_person_access, FOREIGN_TARGET)
		self.assertIsNone(_as(HOME_ONLY_ADMIN, get_person_access, HOME_TARGET)["lock"])

	def test_hr_manager_without_portal_admin_is_refused(self):
		from helixhr.tests.utils import make_test_hr_manager_employee

		_, hr = make_test_hr_manager_employee()
		for fn, args in (
			(get_person_access, (HOME_TARGET,)),
			(get_elevated_access, ()),
			(search_people_access, ()),
			(save_person_access, (HOME_TARGET, [], "x")),
		):
			with self.assertRaises(frappe.PermissionError):
				_as(hr, fn, *args)

	def test_elevated_roles_are_the_person_views_roles(self):
		self._save(self.admin, HOME_TARGET, [_row(IT, self.company)])
		_set_roles(HOME_TARGET, add=["HR User", "Accounts User"])
		self.addCleanup(_set_roles, HOME_TARGET, remove=["HR User", "Accounts User"])
		person = _as(SYSTEM_MANAGER_USER, get_person_access, HOME_TARGET)
		panel = sorted(
			[row["label"] for row in person["rows"]]
			+ [role["label"] for role in person["held_roles_without_rows"]]
		)
		people = _as(SYSTEM_MANAGER_USER, get_elevated_access)["people"]
		row = next(row for row in people if row["user"] == HOME_TARGET)
		self.assertEqual(sorted(role["label"] for role in row["roles"]), panel)
		self.assertEqual(panel, ["HR User", "IT Admin"])

	def test_search_pages_through_every_match(self):
		rows = []
		start = 0
		while True:
			page = _as(self.admin, search_people_access, query="people-", start=start, page_length=1)
			rows += page["rows"]
			if not page["has_more"]:
				break
			start += 1
		users = {row["user"] for row in rows}
		self.assertTrue({HOME_TARGET, FOREIGN_TARGET} <= users)
		self.assertEqual(len(rows), len({row["employee"] for row in rows}))

	def test_search_under_strict_permissions(self):
		frappe.db.set_single_value("System Settings", "apply_strict_user_permissions", 1)
		try:
			everyone = _as(self.admin, search_people_access, query="people-", page_length=100)["rows"]
		finally:
			frappe.db.set_single_value("System Settings", "apply_strict_user_permissions", 0)
		self.assertTrue({HOME_TARGET, FOREIGN_TARGET} <= {row["user"] for row in everyone})

	def test_every_endpoint_has_a_rate_limit(self):
		for action in (
			"get_person_access",
			"save_person_access",
			"get_elevated_access",
			"search_people_access",
		):
			self.assertIn(action, RATE_LIMIT_POLICY)
		for removed in (
			"save_company_access",
			"set_portal_role",
			"get_portal_role_holders",
			"list_people_access",
		):
			self.assertNotIn(removed, RATE_LIMIT_POLICY)
			self.assertFalse(hasattr(api, removed))
