"""Plan 2026-10-15-001 U5: one person's roles and grant, read and saved in one
place (R9-R16). Every refusal is asserted as the caller's own role, never as
Administrator."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from helixhr import api
from helixhr.api import get_person_access, list_people_access, save_person_access
from helixhr.tests.utils import (
	ensure_baseline_company,
	ensure_test_company,
	make_test_portal_admin,
	make_test_user,
)
from helixhr.utils import RATE_LIMIT_POLICY

SYSTEM_MANAGER_USER = "people-system-manager@helixhr.test"
HOME_TARGET = "people-home@helixhr.test"
FOREIGN_TARGET = "people-foreign@helixhr.test"
HOME_ONLY_ADMIN = "people-admin-home@helixhr.test"
IT = "IT Team"
DM = "HelixHR Delivery Manager"


def _as(user, fn, *args, **kwargs):
	frappe.set_user(user)
	try:
		return fn(*args, **kwargs)
	finally:
		frappe.set_user("Administrator")


def _roles(user):
	return set(frappe.get_roles(user))


def _set_roles(user, add=(), remove=()):
	doc = frappe.get_doc("User", user)
	doc.set("roles", [row for row in doc.roles if row.role not in remove])
	held = {row.role for row in doc.roles}
	for role in add:
		if role not in held:
			doc.append("roles", {"role": role})
	doc.save(ignore_permissions=True)
	frappe.clear_cache(user=user)


def _comments(user):
	return frappe.db.count(
		"Comment",
		{"reference_doctype": "User", "reference_name": user, "comment_type": "Info", "content": ["like", "%HelixHR portal%"]},
	)


class TestPeopleAccess(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.other = ensure_baseline_company()
		_, self.admin = make_test_portal_admin()
		frappe.db.delete("User Permission", {"user": self.admin, "allow": "Company"})
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": self.admin,
				"allow": "Company",
				"for_value": self.other,
				"apply_to_all_doctypes": 0,
				"applicable_for": "Company",
			}
		).insert(ignore_permissions=True)
		make_test_user(SYSTEM_MANAGER_USER, self.company)
		_set_roles(SYSTEM_MANAGER_USER, add=["System Manager"])
		make_test_user(HOME_ONLY_ADMIN, self.company)
		_set_roles(HOME_ONLY_ADMIN, add=["HelixHR Portal Admin"])
		self.home = make_test_user(HOME_TARGET, self.company)
		self.foreign = make_test_user(FOREIGN_TARGET, self.other)
		for user in (HOME_TARGET, FOREIGN_TARGET):
			_set_roles(user, remove=[IT, DM, "HR User", "HR Manager"])
		frappe.db.delete("HelixHR Company Access Company", {"parent": ["like", "%@helixhr.test"]})
		frappe.db.delete("HelixHR Company Access", {"user": ["like", "%@helixhr.test"]})

	def tearDown(self):
		frappe.set_user("Administrator")

	def _save(self, caller, employee, **changes):
		person = _as(caller, get_person_access, employee)
		return _as(
			caller,
			save_person_access,
			employee,
			user_modified=changes.pop("user_modified", person["user_modified"]),
			grant_modified=changes.pop("grant_modified", person["grant_modified"]),
			**changes,
		)

	# --- happy paths ---------------------------------------------------------

	def test_admin_saves_roles_and_grant_for_a_home_employee_with_one_comment(self):
		before = _comments(HOME_TARGET)
		person = self._save(
			self.admin,
			self.home,
			managed_roles={IT: True, DM: True},
			grant={"level": "All data", "companies": [self.company, self.other], "allow_export": 1},
		)
		self.assertTrue({IT, DM} <= _roles(HOME_TARGET))
		self.assertEqual(person["grant"]["companies"], [self.company, self.other])
		self.assertTrue(person["grant"]["allow_export"])
		self.assertEqual(_comments(HOME_TARGET), before + 1)
		self.assertIn("IT Team", person["summary"])
		self.assertIn(self.other, person["summary"])

	def test_admin_writes_only_the_grant_for_an_outsourcing_employee(self):
		person = _as(self.admin, get_person_access, self.foreign)
		self.assertFalse(person["can_edit_roles"])
		self.assertTrue(person["can_edit_grant"])
		self._save(self.admin, self.foreign, grant={"level": "Reports only", "companies": [self.other]})
		self.assertEqual(frappe.db.get_value("HelixHR Company Access", FOREIGN_TARGET, "level"), "Reports only")
		with self.assertRaises(frappe.PermissionError):
			self._save(self.admin, self.foreign, managed_roles={IT: True})
		self.assertNotIn(IT, _roles(FOREIGN_TARGET))
		# An unchanged echo of the roles is not a change, so it is accepted.
		self._save(self.admin, self.foreign, managed_roles={IT: False})

	# --- R11: the HR boundary ------------------------------------------------

	def test_admin_may_not_touch_hr_roles_and_an_existing_one_survives(self):
		with self.assertRaises(frappe.PermissionError):
			self._save(self.admin, self.home, hr_roles={"HR Manager": True})
		self.assertNotIn("HR Manager", _roles(HOME_TARGET))
		_set_roles(HOME_TARGET, add=["HR Manager"])
		self.assertFalse(_as(self.admin, get_person_access, self.home)["can_edit_hr_roles"])
		self._save(self.admin, self.home, managed_roles={IT: True})
		self.assertTrue({"HR Manager", IT} <= _roles(HOME_TARGET))

	def test_a_role_outside_the_managed_set_is_refused(self):
		with self.assertRaises(frappe.PermissionError):
			self._save(self.admin, self.home, managed_roles={"System Manager": True})
		with self.assertRaises(frappe.PermissionError):
			self._save(SYSTEM_MANAGER_USER, self.home, hr_roles={"HelixHR Portal Admin": True})

	def test_system_manager_adds_hr_user(self):
		self.assertTrue(_as(SYSTEM_MANAGER_USER, get_person_access, self.home)["can_edit_hr_roles"])
		self._save(SYSTEM_MANAGER_USER, self.home, hr_roles={"HR User": True})
		self.assertIn("HR User", _roles(HOME_TARGET))

	def test_hr_manager_toggle_keeps_the_self_user_permission_in_step(self):
		"""R13."""

		def scoped():
			return frappe.db.exists("User Permission", {"user": HOME_TARGET, "allow": "Employee"})

		self.assertTrue(scoped())
		self._save(SYSTEM_MANAGER_USER, self.home, hr_roles={"HR Manager": True})
		self.assertIn("HR Manager", _roles(HOME_TARGET))
		self.assertFalse(scoped())
		self._save(SYSTEM_MANAGER_USER, self.home, hr_roles={"HR Manager": False})
		self.assertNotIn("HR Manager", _roles(HOME_TARGET))
		self.assertEqual(
			frappe.db.get_value("User Permission", {"user": HOME_TARGET, "allow": "Employee"}, "for_value"),
			self.home,
		)

	def test_granting_hr_manager_drops_only_the_self_employee_permission(self):
		"""R13 touches the user's own Employee row only; another Employee
		User Permission (here: on a colleague) survives."""
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
		self._save(SYSTEM_MANAGER_USER, self.home, hr_roles={"HR Manager": True})
		self.assertFalse(
			frappe.db.exists("User Permission", {"user": HOME_TARGET, "allow": "Employee", "for_value": self.home})
		)
		self.assertTrue(frappe.db.exists("User Permission", other.name))
		self._save(SYSTEM_MANAGER_USER, self.home, hr_roles={"HR Manager": False})
		self.assertTrue(
			frappe.db.exists("User Permission", {"user": HOME_TARGET, "allow": "Employee", "for_value": self.home})
		)

	def test_flags_parse_strictly_and_timestamps_compare_as_datetimes(self):
		self.assertIs(api._flag("true"), True)
		self.assertIs(api._flag("0"), False)
		self.assertIs(api._flag(1), True)
		self.assertIsNone(api._flag("yes please"))
		self.assertIsNone(api._flag(None))
		self.assertTrue(api._same_timestamp("2026-10-15 10:00:00", "2026-10-15 10:00:00.000000"))
		self.assertTrue(api._same_timestamp(None, ""))
		self.assertFalse(api._same_timestamp("2026-10-15 10:00:00", None))
		self.assertFalse(api._same_timestamp("2026-10-15 10:00:00", "not a time"))
		# A garbage toggle value is neither on nor off: nothing changes.
		self._save(self.admin, self.home, managed_roles={IT: "maybe"})
		self.assertNotIn(IT, _roles(HOME_TARGET))

	def test_an_unchanged_hr_manager_toggle_leaves_the_user_permission_alone(self):
		self._save(SYSTEM_MANAGER_USER, self.home, hr_roles={"HR Manager": False}, managed_roles={IT: True})
		self.assertTrue(frappe.db.exists("User Permission", {"user": HOME_TARGET, "allow": "Employee"}))

	# --- R12: atomic, side effects after commit, stale refusals ------------

	def test_a_failure_after_the_grant_rolls_everything_back_without_side_effects(self):
		real_apply_grant = api._apply_grant

		def failing(*args, **kwargs):
			real_apply_grant(*args, **kwargs)
			frappe.throw("boom")

		access_module = "helixhr.helixhr.doctype.helixhr_company_access.helixhr_company_access"
		with (
			patch.object(api, "_apply_grant", side_effect=failing),
			patch(f"{access_module}._after_change") as after_change,
			self.assertRaises(frappe.ValidationError),
		):
			self._save(
				self.admin,
				self.home,
				managed_roles={IT: True},
				grant={"level": "All data", "companies": [self.other]},
			)
		after_change.assert_not_called()
		self.assertNotIn(IT, _roles(HOME_TARGET))
		self.assertFalse(frappe.db.exists("HelixHR Company Access", HOME_TARGET))

	def test_grant_side_effects_wait_for_commit(self):
		access_module = "helixhr.helixhr.doctype.helixhr_company_access.helixhr_company_access"
		with patch(f"{access_module}._after_change") as after_change:
			self._save(self.admin, self.home, grant={"level": "All data", "companies": [self.other]})
		after_change.assert_not_called()

	def test_stale_tokens_are_refused_with_nothing_written(self):
		with self.assertRaises(frappe.TimestampMismatchError):
			self._save(self.admin, self.home, managed_roles={IT: True}, user_modified="2000-01-01 00:00:00")
		self.assertNotIn(IT, _roles(HOME_TARGET))
		self._save(self.admin, self.home, grant={"level": "All data", "companies": [self.other]})
		with self.assertRaises(frappe.TimestampMismatchError):
			self._save(
				self.admin,
				self.home,
				managed_roles={IT: True},
				grant={"level": "Reports only", "companies": [self.other]},
				grant_modified=None,
			)
		self.assertNotIn(IT, _roles(HOME_TARGET))
		self.assertEqual(frappe.db.get_value("HelixHR Company Access", HOME_TARGET, "level"), "All data")

	def test_a_grant_company_outside_reach_refuses_the_whole_save(self):
		from helixhr.tests.utils import COMPANY_ACCESS_THIRD_COMPANY, create_test_company

		third = create_test_company(COMPANY_ACCESS_THIRD_COMPANY, "TCAC")
		with self.assertRaises(frappe.PermissionError):
			self._save(
				self.admin, self.home, managed_roles={IT: True}, grant={"level": "All data", "companies": [third]}
			)
		self.assertNotIn(IT, _roles(HOME_TARGET))
		self.assertFalse(frappe.db.exists("HelixHR Company Access", HOME_TARGET))

	def test_an_empty_grant_never_revokes_and_revoke_needs_explicit_intent(self):
		self._save(self.admin, self.home, grant={"level": "All data", "companies": [self.other]})
		self._save(self.admin, self.home, grant={})
		self.assertEqual(frappe.db.get_value("HelixHR Company Access", HOME_TARGET, "enabled"), 1)
		person = self._save(self.admin, self.home, grant={"revoke": True})
		self.assertEqual(frappe.db.get_value("HelixHR Company Access", HOME_TARGET, "enabled"), 0)
		self.assertFalse(person["grant"]["enabled"])

	# --- R14 and reach -------------------------------------------------------

	def test_self_edit_is_refused(self):
		admin_employee = frappe.db.get_value("Employee", {"user_id": self.admin})
		with self.assertRaises(frappe.PermissionError):
			_as(self.admin, get_person_access, admin_employee)
		with self.assertRaises(frappe.PermissionError):
			_as(self.admin, save_person_access, admin_employee, managed_roles={IT: True}, user_modified="x")

	def test_admin_without_company_permissions_reaches_home_only(self):
		with self.assertRaises(frappe.PermissionError):
			_as(HOME_ONLY_ADMIN, get_person_access, self.foreign)
		self.assertTrue(_as(HOME_ONLY_ADMIN, get_person_access, self.home)["can_edit_roles"])

	def test_hr_manager_without_portal_admin_is_refused(self):
		from helixhr.tests.utils import make_test_hr_manager_employee

		_, hr = make_test_hr_manager_employee()
		for fn, args in ((get_person_access, (self.home,)), (list_people_access, ())):
			with self.assertRaises(frappe.PermissionError):
				_as(hr, fn, *args)

	# --- R15: who has what -------------------------------------------------

	def test_list_roles_are_the_panels_held_roles(self):
		"""R15: the list chip and the panel match profiles over the same
		role set -- every managed and HR role held, nothing else."""
		_set_roles(HOME_TARGET, add=[IT, "HR User", "Accounts User"])
		self.addCleanup(_set_roles, HOME_TARGET, remove=[IT, "HR User", "Accounts User"])
		person = _as(SYSTEM_MANAGER_USER, get_person_access, self.home)
		panel = sorted(
			role for toggles in (person["managed_roles"], person["hr_roles"]) for role, on in toggles.items() if on
		)
		rows = _as(SYSTEM_MANAGER_USER, list_people_access, query=self.home)["rows"]
		row = next(row for row in rows if row["employee"] == self.home)
		self.assertEqual(row["roles"], panel)
		self.assertEqual(panel, sorted([IT, "HR User"]))

	def test_role_filter_runs_before_paging(self):
		_set_roles(HOME_TARGET, add=[IT])
		_set_roles(FOREIGN_TARGET, add=[IT])
		rows = []
		start = 0
		while True:
			page = _as(self.admin, list_people_access, roles=[IT], start=start, page_length=1)
			rows += page["rows"]
			if not page["has_more"]:
				break
			start += 1
		users = {row["user"] for row in rows}
		self.assertTrue({HOME_TARGET, FOREIGN_TARGET} <= users)
		self.assertTrue(all(IT in row["roles"] for row in rows))

	def test_grant_level_filter_and_strict_permissions(self):
		self._save(self.admin, self.foreign, grant={"level": "Reports only", "companies": [self.other]})
		frappe.db.set_single_value("System Settings", "apply_strict_user_permissions", 1)
		try:
			rows = _as(self.admin, list_people_access, grant_level="Reports only")["rows"]
			everyone = _as(self.admin, list_people_access, page_length=100)["rows"]
		finally:
			frappe.db.set_single_value("System Settings", "apply_strict_user_permissions", 0)
		self.assertEqual([row["user"] for row in rows], [FOREIGN_TARGET])
		self.assertTrue({HOME_TARGET, FOREIGN_TARGET} <= {row["user"] for row in everyone})

	def test_every_endpoint_has_a_rate_limit(self):
		for action in ("get_person_access", "save_person_access", "list_people_access"):
			self.assertIn(action, RATE_LIMIT_POLICY)

	# --- KTD6: the old endpoints still behave -------------------------------

	def test_set_portal_role_still_refuses_hr_roles_and_writes_managed_ones(self):
		from helixhr.api import set_portal_role

		with self.assertRaises(frappe.PermissionError):
			_as(self.admin, set_portal_role, self.home, "HR Manager", 1)
		_as(self.admin, set_portal_role, self.home, IT, 1)
		self.assertIn(IT, _roles(HOME_TARGET))
