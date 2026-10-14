"""Plan 2026-10-14-001 U2: who may grant cross-company read access, and the
Settings endpoints that manage it. Every refusal is asserted as the caller's
own role, never as Administrator."""

import frappe
from frappe.tests import IntegrationTestCase

from helixhr.api import (
	delete_company_access,
	get_company_access,
	revoke_company_access,
	save_company_access,
	search_company_access_targets,
)
from helixhr.tests.utils import (
	GRANT_HOLDER_USER,
	ensure_baseline_company,
	ensure_test_company,
	make_test_company_grant,
	make_test_hr_manager_employee,
	make_test_portal_admin,
	make_test_user,
)
from helixhr.utils import RATE_LIMIT_POLICY, grantable_companies

SYSTEM_MANAGER_USER = "grant-system-manager@helixhr.test"


def _as(user, fn, *args, **kwargs):
	frappe.set_user(user)
	try:
		return fn(*args, **kwargs)
	finally:
		frappe.set_user("Administrator")


def _company_permission(user, company, applicable_for="Company"):
	frappe.get_doc(
		{
			"doctype": "User Permission",
			"user": user,
			"allow": "Company",
			"for_value": company,
			"apply_to_all_doctypes": 0 if applicable_for else 1,
			"applicable_for": applicable_for,
		}
	).insert(ignore_permissions=True)


class TestCompanyAccessAdmin(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.other = ensure_baseline_company()
		_, self.admin = make_test_portal_admin()
		frappe.db.delete("User Permission", {"user": self.admin, "allow": "Company"})
		make_test_user(GRANT_HOLDER_USER, self.company)
		self.foreign = "grant-admin-foreign@helixhr.test"
		make_test_user(self.foreign, self.other)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.delete("HelixHR Company Access Company", {"parent": ["like", "%@helixhr.test"]})
		frappe.db.delete("HelixHR Company Access", {"user": ["like", "%@helixhr.test"]})
		frappe.db.delete("User Permission", {"user": self.admin, "allow": "Company"})

	def _save(self, user=GRANT_HOLDER_USER, companies=None, caller=None, **kwargs):
		return _as(
			caller or self.admin,
			save_company_access,
			user,
			kwargs.pop("level", "All data"),
			companies if companies is not None else [self.other],
			**kwargs,
		)

	def test_portal_admin_reach_is_home_plus_company_scoped_user_permissions(self):
		"""Covers AE1. An apply-to-all Company row (ERPNext's side effect of
		"Create User Permission") does not count."""
		_company_permission(self.admin, self.other, applicable_for=None)
		self.assertEqual(grantable_companies(self.admin), [self.company])
		_company_permission(self.admin, self.other)
		self.assertEqual(grantable_companies(self.admin), [self.company, self.other])

		row = self._save()
		self.assertEqual(row["companies"], [self.other])
		self.assertTrue(row["enabled"] and row["can_edit"])
		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{
					"reference_doctype": "User",
					"reference_name": GRANT_HOLDER_USER,
					"content": ["like", "%company access%"],
				},
			)
		)

	def test_portal_admin_without_company_permission_grants_home_only(self):
		"""Covers AE2."""
		with self.assertRaises(frappe.PermissionError):
			self._save()
		self.assertFalse(frappe.db.exists("HelixHR Company Access", GRANT_HOLDER_USER))
		self.assertEqual(self._save(companies=[self.company])["companies"], [self.company])

	def test_grant_naming_a_company_outside_reach_is_read_only(self):
		"""Covers AE3."""
		make_test_company_grant(GRANT_HOLDER_USER, [self.other])
		modified = str(frappe.db.get_value("HelixHR Company Access", GRANT_HOLDER_USER, "modified"))
		[row] = _as(self.admin, get_company_access)["rows"]
		self.assertFalse(row["can_edit"])
		for call in (
			lambda: self._save(companies=[self.company], modified=modified),
			lambda: _as(self.admin, revoke_company_access, GRANT_HOLDER_USER, modified),
			lambda: _as(self.admin, delete_company_access, GRANT_HOLDER_USER, modified),
		):
			with self.assertRaises(frappe.PermissionError):
				call()
		self.assertEqual(
			frappe.get_all(
				"HelixHR Company Access Company", {"parent": GRANT_HOLDER_USER}, pluck="company"
			),
			[self.other],
		)

	def test_grants_in_another_company_are_not_listed(self):
		make_test_company_grant(self.foreign, [self.other])
		self.assertEqual(_as(self.admin, get_company_access)["rows"], [])

	def test_self_grant_is_refused_for_portal_admin_and_system_manager(self):
		_company_permission(self.admin, self.other)
		with self.assertRaises(frappe.PermissionError):
			self._save(user=self.admin)
		system_manager = _system_manager()
		with self.assertRaises(frappe.PermissionError):
			self._save(user=system_manager, caller=system_manager)

	def test_targets_outside_the_rules_are_refused_alike(self):
		_company_permission(self.admin, self.other)
		inactive = "grant-admin-inactive@helixhr.test"
		make_test_user(inactive, self.company, status="Inactive")
		no_employee = "grant-admin-no-employee@helixhr.test"
		if not frappe.db.exists("User", no_employee):
			frappe.get_doc(
				{"doctype": "User", "email": no_employee, "first_name": "Nobody", "send_welcome_email": 0}
			).insert(ignore_permissions=True)
		for user in (inactive, no_employee, "Administrator", "nobody@helixhr.test"):
			with self.assertRaises(frappe.PermissionError, msg=user):
				self._save(user=user)
		# A target in a company the Portal Admin cannot reach.
		frappe.db.delete("User Permission", {"user": self.admin, "allow": "Company"})
		with self.assertRaises(frappe.PermissionError):
			self._save(user=self.foreign, companies=[self.company])

	def test_hr_manager_is_refused_every_endpoint(self):
		_, hr = make_test_hr_manager_employee()
		for call in (
			lambda: get_company_access(),
			lambda: search_company_access_targets("grant"),
			lambda: save_company_access(GRANT_HOLDER_USER, "All data", [self.company]),
			lambda: revoke_company_access(GRANT_HOLDER_USER),
			lambda: delete_company_access(GRANT_HOLDER_USER),
		):
			with self.assertRaises(frappe.PermissionError):
				_as(hr, call)

	def test_stale_modified_is_refused_without_a_partial_write(self):
		_company_permission(self.admin, self.other)
		row = self._save()
		with self.assertRaises(frappe.ValidationError):
			self._save(companies=[self.company, self.other], level="Reports only")
		with self.assertRaises(frappe.ValidationError):
			self._save(companies=[self.company], modified="2001-01-01 00:00:00")
		self.assertEqual(
			frappe.db.get_value("HelixHR Company Access", GRANT_HOLDER_USER, "level"), "All data"
		)
		edited = self._save(companies=[self.company, self.other], modified=row["modified"])
		self.assertEqual(edited["companies"], [self.company, self.other])

	def test_revoke_then_save_re_enables_after_fresh_checks(self):
		_company_permission(self.admin, self.other)
		row = self._save()
		revoked = _as(self.admin, revoke_company_access, GRANT_HOLDER_USER, row["modified"])
		self.assertFalse(revoked["enabled"])
		self.assertTrue(revoked["disabled_reason"])
		again = self._save(modified=revoked["modified"])
		self.assertTrue(again["enabled"])
		self.assertIsNone(again["disabled_reason"])
		_as(self.admin, delete_company_access, GRANT_HOLDER_USER, again["modified"])
		self.assertFalse(frappe.db.exists("HelixHR Company Access", GRANT_HOLDER_USER))

	def test_system_manager_grants_any_company(self):
		system_manager = _system_manager()
		row = self._save(user=self.foreign, companies=[self.company, self.other], caller=system_manager)
		self.assertEqual(row["companies"], [self.company, self.other])

	def test_target_search_stays_in_reach_and_skips_the_caller(self):
		found = {r["user"] for r in _as(self.admin, search_company_access_targets, "grant")}
		self.assertIn(GRANT_HOLDER_USER, found)
		self.assertNotIn(self.foreign, found)
		self.assertNotIn(self.admin, found)

	def test_every_endpoint_has_a_rate_limit(self):
		for action in (
			"get_company_access",
			"search_company_access_targets",
			"save_company_access",
			"revoke_company_access",
			"delete_company_access",
		):
			self.assertIn(action, RATE_LIMIT_POLICY)


def _system_manager():
	make_test_user(SYSTEM_MANAGER_USER, ensure_test_company(), create_user_permission=0)
	user = frappe.get_doc("User", SYSTEM_MANAGER_USER)
	if "System Manager" not in [row.role for row in user.roles]:
		user.append_roles("System Manager")
		user.save(ignore_permissions=True)
		frappe.clear_cache(user=SYSTEM_MANAGER_USER)
	return SYSTEM_MANAGER_USER
