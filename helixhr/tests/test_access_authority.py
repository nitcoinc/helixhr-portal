"""Plan 2026-10-19-001 U5: who may change whose access (R11-R14), the reason
a high-risk save needs (R15), the Frappe roles a save keeps in step (R8-R10)
and the change log (R23). Every refusal is asserted as the caller's own
login, never as Administrator.

Company A is the test company and B the baseline second company. Every
login is this module's own.
"""

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.handler import execute_cmd
from frappe.tests import IntegrationTestCase

from helixhr import api
from helixhr.api import access_token, save_person_access
from helixhr.tests.utils import (
	clear_test_company_grants,
	ensure_baseline_company,
	ensure_test_company,
	make_assignment,
	make_test_company_grant,
	make_test_user,
)
from helixhr.utils import (
	ACCESS_ADMIN_ROLE,
	ASSIGNABLE_ROLES,
	DELIVERY_MANAGER_ROLE,
	IT_TEAM_ROLE,
	NOTIFICATION_MANAGER_ROLE,
	ORG_VIEWER_ROLE,
	PORTAL_ADMIN_ROLE,
	REPORT_MANAGER_ROLE,
	SELF_EDIT_REFUSAL,
	TARGET_LOCKED_REFUSAL,
	can_manage_access,
	clear_role_scope_cache,
	may_edit_target,
)

SYSTEM_MANAGER = "auth-sm@helixhr.test"
HOLDER_X = "auth-ma-x@helixhr.test"
HOLDER_Y = "auth-ma-y@helixhr.test"
HOLDER_D = "auth-ma-d@helixhr.test"
ADMIN_A = "auth-pa-a@helixhr.test"
ADMIN_ALL = "auth-pa-all@helixhr.test"
TARGET = "auth-target@helixhr.test"
TARGET_B = "auth-target-b@helixhr.test"
PROFILE_TARGET = "auth-profile@helixhr.test"
PROFILE = "_Test Auth Profile"

_MODULE_USERS = (
	SYSTEM_MANAGER,
	HOLDER_X,
	HOLDER_Y,
	HOLDER_D,
	ADMIN_A,
	ADMIN_ALL,
	TARGET,
	TARGET_B,
	PROFILE_TARGET,
)
_MANAGED = (*ASSIGNABLE_ROLES, "System Manager")

HIGH_RISK_REFUSAL = "high-risk"
R14_REFUSAL = "nobody who can manage access"


def _as(user, fn, *args, **kwargs):
	frappe.set_user(user)
	try:
		return fn(*args, **kwargs)
	finally:
		frappe.set_user("Administrator")


def _row(role, company=None, export=0):
	"""One payload row; no company means All orgs."""
	return {"role": role, "all_orgs": 0 if company else 1, "company": company, "allow_export": export}


def _rows(user):
	return api._access_snapshot(api._access_rows(user))


def _roles(user):
	return {row.role for row in frappe.get_doc("User", user).roles}


def _changes(user):
	"""Change rows persist between methods (rollback is per class): compare
	counts, or read one by name."""
	return frappe.get_all(
		"HelixHR Access Change",
		filters={"target_user": user},
		fields=["name", "changed_by", "reason", "before", "after"],
		order_by="creation desc",
	)


def _bells(change):
	return frappe.get_all(
		"Notification Log",
		filters={"document_type": "HelixHR Access Change", "document_name": change},
		fields=["for_user", "subject"],
	)


class _Authority(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.a = ensure_test_company()
		self.b = ensure_baseline_company()

		# Rows, roles and the enabled flag persist between methods (rollback
		# is per class): reset them.
		frappe.db.delete("HelixHR Access Assignment", {"user": ["in", list(_MODULE_USERS)]})
		for user in _MODULE_USERS:
			make_test_user(user, self.b if user == TARGET_B else self.a, status="Active")
			frappe.db.set_value("User", user, "enabled", 1)
			frappe.db.delete("Has Role", {"parent": user, "role": ["in", list(_MANAGED)]})
			frappe.clear_cache(user=user)
		clear_role_scope_cache()

		frappe.get_doc("User", SYSTEM_MANAGER).add_roles("System Manager")
		for holder in (HOLDER_X, HOLDER_Y, HOLDER_D):
			make_assignment(holder, ACCESS_ADMIN_ROLE, "all")
		make_assignment(ADMIN_A, PORTAL_ADMIN_ROLE, [self.a])
		make_assignment(ADMIN_ALL, PORTAL_ADMIN_ROLE, "all")

	def tearDown(self):
		frappe.set_user("Administrator")
		clear_role_scope_cache()

	def _save(self, caller, user, rows, reason=None, token=None, sync_roles=None):
		return _as(
			caller,
			save_person_access,
			user,
			rows,
			token or access_token(user),
			reason=reason,
			sync_roles=sync_roles,
		)

	def _refused(self, exc, message, caller, user, rows, **kwargs):
		"""Refused with ``message``, and nothing written."""
		before = (_rows(user), _roles(user), len(_changes(user)))
		with self.assertRaises(exc) as caught:
			self._save(caller, user, rows, **kwargs)
		self.assertIn(message, str(caught.exception))
		self.assertEqual((_rows(user), _roles(user), len(_changes(user))), before)


class TestAuthorityMatrix(_Authority):
	"""R12 and R7 across caller tiers, roles and org shapes."""

	def test_portal_admin_gives_a_portal_role_in_its_own_org(self):
		out = self._save(ADMIN_A, TARGET, [_row(DELIVERY_MANAGER_ROLE, self.a)])
		self.assertEqual(out["rows"], [_row(DELIVERY_MANAGER_ROLE, self.a)])
		self.assertIn(DELIVERY_MANAGER_ROLE, _roles(TARGET))
		change = frappe.get_doc("HelixHR Access Change", out["change"])
		self.assertEqual(change.changed_by, ADMIN_A)
		self.assertEqual(frappe.parse_json(change.before), [])
		self.assertEqual(frappe.parse_json(change.after), out["rows"])
		# Not high-risk: no reason needed, nobody notified.
		self.assertEqual(_bells(change.name), [])
		self.assertEqual(out["token"], access_token(TARGET))

	def test_portal_admin_is_refused_outside_its_orgs_roles_and_all_orgs(self):
		for row, message in (
			(_row(DELIVERY_MANAGER_ROLE, self.b), f"in {self.b}"),
			(_row("HR User", self.a), "Only Manage access can give HR roles."),
			(_row("HR Manager", self.a), "Only Manage access can give HR roles."),
			(_row(NOTIFICATION_MANAGER_ROLE), "on All orgs"),
			(_row(ORG_VIEWER_ROLE), "on All orgs"),
			(_row(PORTAL_ADMIN_ROLE, self.a), "Portal Admin"),
			(_row(ACCESS_ADMIN_ROLE), "on All orgs"),
		):
			with self.subTest(row=row):
				self._refused(frappe.PermissionError, message, ADMIN_A, TARGET, [row], reason="Cover.")

	def test_an_all_orgs_portal_admin_names_any_org_but_never_all_orgs(self):
		self._save(ADMIN_ALL, TARGET, [_row(ORG_VIEWER_ROLE, self.b)])
		self.assertEqual(_rows(TARGET), [_row(ORG_VIEWER_ROLE, self.b)])
		self._refused(
			frappe.PermissionError,
			"on All orgs",
			ADMIN_ALL,
			TARGET,
			[_row(ORG_VIEWER_ROLE, self.b), _row(IT_TEAM_ROLE)],
		)

	def test_export_is_high_risk_even_for_a_portal_admin(self):
		self._refused(
			frappe.ValidationError, HIGH_RISK_REFUSAL, ADMIN_A, TARGET, [_row(REPORT_MANAGER_ROLE, self.a, 1)]
		)
		out = self._save(ADMIN_A, TARGET, [_row(REPORT_MANAGER_ROLE, self.a, 1)], reason="Month-end close.")
		self.assertTrue(_bells(out["change"]))
		# Turning export on later is high-risk too; turning it off is not.
		self._save(ADMIN_A, TARGET, [_row(REPORT_MANAGER_ROLE, self.a)])
		self._refused(
			frappe.ValidationError, HIGH_RISK_REFUSAL, ADMIN_A, TARGET, [_row(REPORT_MANAGER_ROLE, self.a, 1)]
		)

	def test_leadership_needs_a_reason_then_saves_syncs_and_notifies(self):
		leadership = [_row("HR Manager")]
		self._refused(frappe.ValidationError, HIGH_RISK_REFUSAL, HOLDER_X, TARGET, leadership)
		self._refused(frappe.ValidationError, "500", HOLDER_X, TARGET, leadership, reason="x" * 501)

		out = self._save(HOLDER_X, TARGET, leadership, reason="Acting head of HR.")
		self.assertEqual(_rows(TARGET), leadership)
		self.assertIn("HR Manager", _roles(TARGET))
		employee = frappe.db.get_value("Employee", {"user_id": TARGET})
		self.assertFalse(
			frappe.db.exists("User Permission", {"user": TARGET, "allow": "Employee", "for_value": employee})
		)
		change = frappe.get_doc("HelixHR Access Change", out["change"])
		self.assertEqual(change.reason, "Acting head of HR.")
		notified = {bell.for_user for bell in _bells(out["change"])}
		# Every System Manager and Manage access holder but the caller.
		self.assertTrue({SYSTEM_MANAGER, HOLDER_Y, HOLDER_D} <= notified)
		self.assertNotIn(HOLDER_X, notified)

	def test_system_manager_and_manage_access_give_everything(self):
		for caller in (SYSTEM_MANAGER, HOLDER_X):
			with self.subTest(caller=caller):
				rows = [
					_row(NOTIFICATION_MANAGER_ROLE),
					_row(PORTAL_ADMIN_ROLE, self.b),
					_row("HR User", self.a),
				]
				self._save(caller, TARGET, rows, reason="Covering.")
				self.assertEqual(len(_rows(TARGET)), 3)
				self._save(caller, TARGET, [])
				self.assertEqual(_rows(TARGET), [])


class TestWhoMayBeEdited(_Authority):
	"""R11 and R13, and the reach the editor opens in."""

	def test_self_edit_is_refused_for_every_tier(self):
		for caller in (ADMIN_A, HOLDER_X, SYSTEM_MANAGER):
			with self.subTest(caller=caller):
				self._refused(frappe.PermissionError, SELF_EDIT_REFUSAL, caller, caller, [])

	def test_a_target_holding_a_role_the_caller_cannot_assign_is_read_only(self):
		make_assignment(TARGET, DELIVERY_MANAGER_ROLE, [self.b])
		self.assertEqual(
			_as(ADMIN_A, may_edit_target, ADMIN_A, TARGET, api._access_rows(TARGET), _roles(TARGET)),
			TARGET_LOCKED_REFUSAL,
		)
		# Even a change to a row it could give: the whole person is locked.
		self._refused(
			frappe.PermissionError,
			TARGET_LOCKED_REFUSAL,
			ADMIN_A,
			TARGET,
			[_row(DELIVERY_MANAGER_ROLE, self.b), _row(IT_TEAM_ROLE, self.a)],
		)
		self.assertIsNone(may_edit_target(HOLDER_X, TARGET, api._access_rows(TARGET), _roles(TARGET)))

	def test_a_frappe_role_without_a_row_locks_the_target_too(self):
		frappe.get_doc("User", TARGET).add_roles("HR Manager")
		self._refused(
			frappe.PermissionError, TARGET_LOCKED_REFUSAL, ADMIN_A, TARGET, [_row(IT_TEAM_ROLE, self.a)]
		)
		# A portal role set outside HelixHR does not: the Portal Admin may
		# assign it.
		frappe.db.delete("Has Role", {"parent": TARGET, "role": "HR Manager"})
		frappe.get_doc("User", TARGET).add_roles(IT_TEAM_ROLE)
		self._save(ADMIN_A, TARGET, [_row(IT_TEAM_ROLE, self.a)])
		self.assertEqual(_rows(TARGET), [_row(IT_TEAM_ROLE, self.a)])

	def test_a_case_variant_of_the_callers_own_id_is_still_self_edit(self):
		"""The User table's collation matches any case, so the identity
		checks run on the canonical name: an upper-cased copy of the caller's
		own login is refused as self-edit (R11), never saved, and the
		editor opens it locked."""
		shout = HOLDER_X.upper()
		self.assertNotEqual(shout, HOLDER_X)
		self._refused(
			frappe.PermissionError,
			SELF_EDIT_REFUSAL,
			HOLDER_X,
			shout,
			[_row(ACCESS_ADMIN_ROLE), _row("HR Manager")],
			reason="Promote myself.",
			token=access_token(HOLDER_X),
		)
		self.assertNotIn("HR Manager", _roles(HOLDER_X))
		person = _as(HOLDER_X, api.get_person_access, shout)
		self.assertEqual(person["user"], HOLDER_X)
		self.assertEqual(person["lock"]["reason"], SELF_EDIT_REFUSAL)

	def test_administrator_in_any_case_is_refused_like_a_missing_person(self):
		for user in ("ADMINISTRATOR", "administrator", "GUEST"):
			with self.subTest(user=user):
				with self.assertRaises(frappe.PermissionError) as caught:
					self._save(HOLDER_X, user, [_row(IT_TEAM_ROLE, self.a)], token="x")
				self.assertIn("not authorised", str(caught.exception))
				with self.assertRaises(frappe.PermissionError):
					_as(HOLDER_X, api.get_person_access, user)
		self.assertFalse(frappe.db.exists("HelixHR Access Assignment", {"user": "Administrator"}))

	def test_a_system_manager_target_needs_full_authority(self):
		self._refused(
			frappe.PermissionError,
			TARGET_LOCKED_REFUSAL,
			ADMIN_A,
			SYSTEM_MANAGER,
			[_row(IT_TEAM_ROLE, self.a)],
		)
		self._save(HOLDER_X, SYSTEM_MANAGER, [_row(IT_TEAM_ROLE, self.a)])

	def test_a_person_outside_reach_is_refused_like_a_missing_one(self):
		for user in (TARGET_B, "nobody@helixhr.test", "Administrator"):
			with self.subTest(user=user), self.assertRaises(frappe.PermissionError) as caught:
				self._save(ADMIN_A, user, [_row(IT_TEAM_ROLE, self.a)], token="x")
			self.assertIn("not authorised", str(caught.exception))
		# A row on a reachable org brings the person into reach.
		make_assignment(TARGET_B, DELIVERY_MANAGER_ROLE, [self.a])
		self._save(ADMIN_A, TARGET_B, [])
		self.assertEqual(_rows(TARGET_B), [])

	def test_a_caller_with_no_admin_scope_is_refused(self):
		with self.assertRaises(frappe.PermissionError):
			self._save(TARGET_B, TARGET, [_row(IT_TEAM_ROLE, self.a)])

	def test_a_disabled_or_left_target_can_only_lose_access(self):
		make_assignment(TARGET, DELIVERY_MANAGER_ROLE, [self.a])
		frappe.db.set_value("User", TARGET, "enabled", 0)
		self._refused(
			frappe.ValidationError,
			"can only be removed",
			ADMIN_A,
			TARGET,
			[_row(DELIVERY_MANAGER_ROLE, self.a), _row(IT_TEAM_ROLE, self.a)],
		)
		self._save(ADMIN_A, TARGET, [])
		self.assertEqual(_rows(TARGET), [])


class TestLastManageAccessHolder(_Authority):
	"""R14. Other suites' fixtures may hold Manage access too; this class
	counts only its own holders (the deletes roll back with the class)."""

	def setUp(self):
		super().setUp()
		frappe.db.delete(
			"HelixHR Access Assignment",
			{"role": ACCESS_ADMIN_ROLE, "user": ["not in", [HOLDER_X, HOLDER_Y, HOLDER_D]]},
		)

	def test_a_holder_may_remove_another_while_one_remains(self):
		frappe.db.set_value("User", HOLDER_D, "enabled", 0)
		self._save(HOLDER_Y, HOLDER_X, [])
		self.assertEqual(_rows(HOLDER_X), [])
		self.assertNotIn(ACCESS_ADMIN_ROLE, _roles(HOLDER_X))

	def test_two_holders_removing_each_other_leaves_one(self):
		"""X removes Y while Y removes X. Y's request authorised itself before
		X's commit (its cached scope says Manage access); the lock and fresh
		count then see Y's own row gone and refuse."""
		frappe.db.delete("HelixHR Access Assignment", {"user": HOLDER_D})
		self.assertTrue(_as(HOLDER_Y, can_manage_access, HOLDER_Y))
		token = access_token(HOLDER_X)
		# X's save, committed by the other request.
		frappe.db.delete("HelixHR Access Assignment", {"user": HOLDER_Y, "role": ACCESS_ADMIN_ROLE})
		frappe.set_user(HOLDER_Y)
		try:
			with self.assertRaises(frappe.ValidationError) as caught:
				save_person_access(HOLDER_X, [], token)
		finally:
			frappe.set_user("Administrator")
		self.assertIn(R14_REFUSAL, str(caught.exception))
		self.assertEqual(_rows(HOLDER_X), [_row(ACCESS_ADMIN_ROLE)])

	def test_a_disabled_holder_counts_as_zero(self):
		frappe.db.set_value("User", HOLDER_D, "enabled", 0)
		self.assertEqual(api._manage_access_left(HOLDER_X), 1)  # Y
		frappe.db.delete("HelixHR Access Assignment", {"user": HOLDER_Y})
		self.assertEqual(api._manage_access_left(HOLDER_X), 0)
		frappe.db.set_value("User", HOLDER_D, "enabled", 1)
		self.assertEqual(api._manage_access_left(HOLDER_X), 1)

	def test_a_left_holder_counts_as_zero_and_a_system_manager_may_remove_the_last(self):
		frappe.db.delete("HelixHR Access Assignment", {"user": HOLDER_Y})
		employee = frappe.db.get_value("Employee", {"user_id": HOLDER_D})
		frappe.db.set_value("Employee", employee, "status", "Left")
		self.addCleanup(frappe.db.set_value, "Employee", employee, "status", "Active")
		self.assertEqual(api._manage_access_left(HOLDER_X), 0)
		self._save(SYSTEM_MANAGER, HOLDER_X, [])
		self.assertEqual(_rows(HOLDER_X), [])


class TestStaleToken(_Authority):
	def test_removing_a_row_that_is_not_the_newest_makes_the_token_stale(self):
		make_assignment(TARGET, DELIVERY_MANAGER_ROLE, [self.a])
		make_assignment(TARGET, IT_TEAM_ROLE, [self.a])
		token = access_token(TARGET)
		oldest = frappe.get_all(
			"HelixHR Access Assignment",
			filters={"user": TARGET},
			order_by="modified asc",
			limit=1,
			pluck="name",
		)[0]
		frappe.db.delete("HelixHR Access Assignment", oldest)
		self._refused(
			frappe.TimestampMismatchError,
			"Someone changed this person's access. Reload and re-apply.",
			ADMIN_A,
			TARGET,
			[_row(IT_TEAM_ROLE, self.a), _row(ORG_VIEWER_ROLE, self.a)],
			token=token,
		)

	def test_a_has_role_change_makes_it_stale_too(self):
		token = access_token(TARGET)
		frappe.get_doc("User", TARGET).add_roles(IT_TEAM_ROLE)
		self.assertNotEqual(access_token(TARGET), token)

	def test_a_save_that_commits_after_the_token_check_is_caught_under_the_lock(self):
		"""Two saves load one token. The other's commit lands after this
		save's unlocked check: the locked re-check inside the savepoint
		refuses, and nothing of this save is written."""
		token = access_token(TARGET)
		real = api._desired_access_rows

		def racing(rows):
			# The other request's save, committed after our first check.
			make_assignment(TARGET, DELIVERY_MANAGER_ROLE, [self.a], add_role=False)
			return real(rows)

		with (
			patch.object(api, "_desired_access_rows", side_effect=racing),
			self.assertRaises(frappe.TimestampMismatchError) as caught,
		):
			self._save(ADMIN_A, TARGET, [_row(IT_TEAM_ROLE, self.a)], token=token)
		self.assertIn("Someone changed this person's access", str(caught.exception))
		self.assertEqual(_rows(TARGET), [_row(DELIVERY_MANAGER_ROLE, self.a)])
		self.assertNotIn(IT_TEAM_ROLE, _roles(TARGET))
		self.assertEqual(_changes(TARGET), [])


class TestRoleSync(_Authority):
	"""R8-R10 and KTD4: the save writes Has Role for the roles it touched."""

	def test_removing_the_last_hr_manager_row_removes_the_role_and_rescopes(self):
		self._save(
			SYSTEM_MANAGER,
			TARGET,
			[_row("HR Manager", self.a), _row("HR Manager", self.b)],
			reason="Two orgs.",
		)
		employee = frappe.db.get_value("Employee", {"user_id": TARGET})
		self._save(SYSTEM_MANAGER, TARGET, [_row("HR Manager", self.b)])
		self.assertIn("HR Manager", _roles(TARGET))
		self._save(SYSTEM_MANAGER, TARGET, [])
		self.assertNotIn("HR Manager", _roles(TARGET))
		self.assertTrue(
			frappe.db.exists("User Permission", {"user": TARGET, "allow": "Employee", "for_value": employee})
		)

	def test_an_untouched_drifted_role_stays_either_way(self):
		# Set outside HelixHR: the Frappe role with no row.
		frappe.get_doc("User", TARGET).add_roles(IT_TEAM_ROLE)
		# Missing in Frappe: a row whose role was removed.
		make_assignment(TARGET, ORG_VIEWER_ROLE, [self.a], add_role=False)
		self._save(ADMIN_A, TARGET, [_row(ORG_VIEWER_ROLE, self.a), _row(DELIVERY_MANAGER_ROLE, self.a)])
		roles = _roles(TARGET)
		self.assertIn(IT_TEAM_ROLE, roles)
		self.assertNotIn(ORG_VIEWER_ROLE, roles)
		self.assertIn(DELIVERY_MANAGER_ROLE, roles)

	def test_sync_roles_removes_a_drifted_role_and_restores_a_missing_one(self):
		frappe.get_doc("User", TARGET).add_roles(IT_TEAM_ROLE)
		make_assignment(TARGET, ORG_VIEWER_ROLE, [self.a], add_role=False)
		rows = [_row(ORG_VIEWER_ROLE, self.a)]
		out = self._save(ADMIN_A, TARGET, rows, sync_roles=[IT_TEAM_ROLE, ORG_VIEWER_ROLE])
		roles = _roles(TARGET)
		self.assertNotIn(IT_TEAM_ROLE, roles)
		self.assertIn(ORG_VIEWER_ROLE, roles)
		self.assertTrue(out["change"])
		# A role the caller may not assign cannot be synced.
		frappe.get_doc("User", TARGET).add_roles("HR User")
		with self.assertRaises(frappe.PermissionError):
			self._save(ADMIN_A, TARGET, rows, sync_roles=["HR User"])

	def test_restoring_an_hr_role_is_high_risk(self):
		"""KTD12: "Restore role" on an HR row gives HRMS Desk reach back, so
		it needs a reason and notifies, like adding the row."""
		make_assignment(TARGET, "HR Manager", [self.a], add_role=False)
		rows = [_row("HR Manager", self.a)]
		self._refused(
			frappe.ValidationError, HIGH_RISK_REFUSAL, HOLDER_X, TARGET, rows, sync_roles=["HR Manager"]
		)
		self.assertNotIn("HR Manager", _roles(TARGET))
		out = self._save(HOLDER_X, TARGET, rows, reason="Back from leave.", sync_roles=["HR Manager"])
		self.assertIn("HR Manager", _roles(TARGET))
		bells = _bells(out["change"])
		self.assertTrue(bells)
		self.assertTrue(all("HR Manager" in bell.subject for bell in bells))

	def test_a_role_profile_target_is_refused_with_nothing_written(self):
		if not frappe.db.exists("Role Profile", PROFILE):
			frappe.get_doc(
				{"doctype": "Role Profile", "role_profile": PROFILE, "roles": [{"role": "Employee"}]}
			).insert(ignore_permissions=True)
		user = frappe.get_doc("User", PROFILE_TARGET)
		user.append("role_profiles", {"role_profile": PROFILE})
		user.save(ignore_permissions=True)
		self.addCleanup(_drop_role_profiles, PROFILE_TARGET)
		self._refused(
			frappe.ValidationError,
			"This person's roles come from a role profile. Change it in Desk.",
			ADMIN_A,
			PROFILE_TARGET,
			[_row(DELIVERY_MANAGER_ROLE, self.a)],
		)


def _drop_role_profiles(user):
	doc = frappe.get_doc("User", user)
	doc.set("role_profiles", [])
	doc.role_profile_name = None
	doc.save(ignore_permissions=True)


class TestAuditAndRollback(_Authority):
	def test_a_reason_with_markup_is_stored_and_sent_as_text(self):
		reason = "<b>Cover</b> for <script>alert(1)</script> & co"
		out = self._save(HOLDER_X, TARGET, [_row("HR Manager", self.a)], reason=reason)
		change = frappe.get_doc("HelixHR Access Change", out["change"])
		self.assertEqual(change.reason, reason)
		bells = _bells(out["change"])
		self.assertTrue(bells)
		for bell in bells:
			self.assertNotIn("<script", bell.subject)
			self.assertIn("&lt;script&gt;", bell.subject)

	def test_a_failed_save_leaves_no_rows_roles_log_or_notification(self):
		real = api._notify_high_risk_access
		bells_before = frappe.db.count("Notification Log", {"document_type": "HelixHR Access Change"})

		def failing(*args, **kwargs):
			real(*args, **kwargs)
			frappe.throw("boom")

		with (
			patch.object(api, "_notify_high_risk_access", side_effect=failing),
			patch.object(api, "expire_unreachable_exports") as expire,
		):
			self._refused(
				frappe.ValidationError,
				"boom",
				HOLDER_X,
				TARGET,
				[_row("HR Manager", self.a)],
				reason="Cover.",
			)
		expire.assert_not_called()
		self.assertEqual(
			frappe.db.count("Notification Log", {"document_type": "HelixHR Access Change"}), bells_before
		)
		employee = frappe.db.get_value("Employee", {"user_id": TARGET})
		self.assertTrue(
			frappe.db.exists("User Permission", {"user": TARGET, "allow": "Employee", "for_value": employee})
		)

	def test_side_effects_wait_for_commit(self):
		with patch.object(api, "expire_unreachable_exports") as expire:
			self._save(ADMIN_A, TARGET, [_row(DELIVERY_MANAGER_ROLE, self.a)])
		expire.assert_not_called()

	def test_removing_a_migrated_org_viewer_row_disables_the_legacy_grant(self):
		self.addCleanup(clear_test_company_grants)
		make_test_company_grant(TARGET, [self.a], level="All data")
		self.assertEqual(_rows(TARGET), [_row(ORG_VIEWER_ROLE, self.a)])
		self._save(ADMIN_A, TARGET, [])
		grant = frappe.db.get_value(
			"HelixHR Company Access", TARGET, ["enabled", "disabled_reason"], as_dict=True
		)
		self.assertEqual(grant.enabled, 0)
		self.assertTrue(grant.disabled_reason)

	def test_a_manual_row_removal_leaves_a_legacy_grant_alone(self):
		self.addCleanup(clear_test_company_grants)
		make_test_company_grant(TARGET, [self.a], level="All data")
		make_assignment(TARGET, DELIVERY_MANAGER_ROLE, [self.a])
		self._save(ADMIN_A, TARGET, [_row(ORG_VIEWER_ROLE, self.a)])
		self.assertEqual(frappe.db.get_value("HelixHR Company Access", TARGET, "enabled"), 1)

	def test_removing_a_migrated_row_the_grant_did_not_give_leaves_the_grant_on(self):
		"""Only the grant a row came from is switched off: an "All data"
		grant on A gives Org viewer on A -- not Reports on A (the Report
		Manager role's), nor Org viewer on another org."""
		self.addCleanup(clear_test_company_grants)
		make_test_company_grant(TARGET, [self.a], level="All data", allow_export=1)
		make_assignment(TARGET, REPORT_MANAGER_ROLE, [self.a], export=1, origin="migrated", add_role=False)
		make_assignment(TARGET, ORG_VIEWER_ROLE, [self.b], origin="migrated")
		self._save(HOLDER_X, TARGET, [_row(ORG_VIEWER_ROLE, self.a, export=1)])
		self.assertEqual(frappe.db.get_value("HelixHR Company Access", TARGET, "enabled"), 1)
		self.assertEqual(frappe.db.get_value("HelixHR Company Access", TARGET, "allow_export"), 1)

	def test_turning_export_off_on_a_migrated_row_turns_the_grants_export_off(self):
		"""Rollback safety: under the grant code the grant's export would
		come back. The grant itself stays on: the row still reads."""
		self.addCleanup(clear_test_company_grants)
		make_test_company_grant(TARGET, [self.a], level="All data", allow_export=1)
		self.assertEqual(_rows(TARGET), [_row(ORG_VIEWER_ROLE, self.a, export=1)])
		self._save(ADMIN_A, TARGET, [_row(ORG_VIEWER_ROLE, self.a)])
		grant = frappe.db.get_value("HelixHR Company Access", TARGET, ["enabled", "allow_export"], as_dict=True)
		self.assertEqual((grant.enabled, grant.allow_export), (1, 0))
		# Export turned off on a row the grant did not give leaves it alone.
		frappe.db.set_value("HelixHR Company Access", TARGET, "allow_export", 1)
		make_assignment(TARGET, REPORT_MANAGER_ROLE, [self.a], export=1, origin="migrated", add_role=False)
		self._save(
			HOLDER_X,
			TARGET,
			[_row(ORG_VIEWER_ROLE, self.a), _row(REPORT_MANAGER_ROLE, self.a)],
		)
		self.assertEqual(frappe.db.get_value("HelixHR Company Access", TARGET, "allow_export"), 1)


class TestRestCall(_Authority):
	"""R16: a call that skips the screen meets the same checks."""

	def _rest(self, caller, http_method, **form):
		frappe.set_user(caller)
		saved = (getattr(frappe.local, "request", None), frappe.local.form_dict)
		frappe.local.request = SimpleNamespace(method=http_method)
		frappe.local.form_dict = frappe._dict(form)
		try:
			return execute_cmd("helixhr.api.save_person_access")
		finally:
			frappe.local.request, frappe.local.form_dict = saved
			frappe.set_user("Administrator")

	def test_a_crafted_payload_is_refused_like_the_screen(self):
		form = {
			"user": TARGET,
			"rows": frappe.as_json([_row("HR Manager")]),
			"token": access_token(TARGET),
			"reason": "Promote.",
		}
		for http_method in ("POST", "GET"):
			with self.subTest(http_method=http_method), self.assertRaises(frappe.PermissionError):
				self._rest(ADMIN_A, http_method, **form)
		self.assertEqual(_rows(TARGET), [])
		self.assertNotIn("HR Manager", _roles(TARGET))
		self.assertEqual(_changes(TARGET), [])
		out = self._rest(
			ADMIN_A,
			"POST",
			user=TARGET,
			rows=frappe.as_json([_row(IT_TEAM_ROLE, self.a)]),
			token=access_token(TARGET),
		)
		self.assertEqual(out["rows"], [_row(IT_TEAM_ROLE, self.a)])
