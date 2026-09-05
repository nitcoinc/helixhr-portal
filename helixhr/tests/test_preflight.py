import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from helixhr import preflight
from helixhr.tests.utils import EMPLOYEE_USER, make_test_employee_and_manager


class TestPreflight(IntegrationTestCase):
	"""The checks that guard against a lockout or a data leak must judge
	real site state, not just print. Settings are flipped and restored in
	place -- System Settings is a Single, so there is nothing to roll back
	but the one field."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.employee_name, _, _, _ = make_test_employee_and_manager()

	def _with_system_setting(self, field, value, fn):
		original = frappe.db.get_single_value("System Settings", field)
		frappe.db.set_single_value("System Settings", field, value)
		try:
			return fn()
		finally:
			frappe.db.set_single_value("System Settings", field, original)

	def test_every_check_returns_a_well_formed_result(self):
		for check in preflight.CHECKS:
			result = check()
			self.assertIn(result["status"], (preflight.PASS, preflight.WARN, preflight.FAIL), check.__name__)
			self.assertTrue(result["name"] and result["detail"], check.__name__)

	def test_disabling_password_login_without_entra_is_a_lockout(self):
		# The test site has no Social Login Key at all, which is exactly the
		# lockout shape; guard the assumption rather than mutate a key.
		self.assertFalse(preflight._entra_enabled())
		result = self._with_system_setting("disable_user_pass_login", 1, preflight.check_password_login)
		self.assertEqual(result["status"], preflight.FAIL)

	def test_password_login_on_is_fine_in_the_local_login_phase(self):
		result = self._with_system_setting("disable_user_pass_login", 0, preflight.check_password_login)
		self.assertEqual(result["status"], preflight.PASS)

	def test_strict_user_permissions_off_fails(self):
		result = self._with_system_setting(
			"apply_strict_user_permissions", 0, preflight.check_strict_user_permissions
		)
		self.assertEqual(result["status"], preflight.FAIL)

	def test_a_linked_employee_without_a_user_permission_fails(self):
		perms = frappe.get_all(
			"User Permission",
			filters={"user": EMPLOYEE_USER, "allow": "Employee", "for_value": self.employee_name},
			pluck="name",
		)
		self.assertTrue(perms, "fixture should have created the permission")
		for name in perms:
			frappe.delete_doc("User Permission", name, force=True)
		try:
			result = preflight.check_employee_user_permissions()
			self.assertEqual(result["status"], preflight.FAIL)
			self.assertIn(EMPLOYEE_USER, result["detail"])
		finally:
			frappe.get_doc(
				{
					"doctype": "User Permission",
					"user": EMPLOYEE_USER,
					"allow": "Employee",
					"for_value": self.employee_name,
				}
			).insert(ignore_permissions=True)

	def test_fixtures_are_installed_on_this_site(self):
		self.assertEqual(preflight.check_fixtures()["status"], preflight.PASS)

	def test_run_exits_non_zero_when_something_fails(self):
		def _run():
			with self.assertRaises(SystemExit):
				preflight.run()

		self._with_system_setting("apply_strict_user_permissions", 0, _run)


class TestPreflightP2U1(IntegrationTestCase):
	"""P2-U1 steps 3 and 4: the two HR Settings that carry R14 and
	self-approval natively, the legacy-row WARN, and the Custom DocPerm
	coverage trap. All four judge real site state."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()

	def _with_hr_setting(self, field, value, fn):
		original = frappe.db.get_single_value("HR Settings", field)
		frappe.db.set_single_value("HR Settings", field, value)
		try:
			return fn()
		finally:
			frappe.db.set_single_value("HR Settings", field, original)

	def test_leave_approver_mandatory_off_fails(self):
		result = self._with_hr_setting(
			"leave_approver_mandatory_in_leave_application",
			0,
			preflight.check_leave_approver_mandatory,
		)
		self.assertEqual(result["status"], preflight.FAIL)

	def test_leave_approver_mandatory_on_passes(self):
		result = self._with_hr_setting(
			"leave_approver_mandatory_in_leave_application",
			1,
			preflight.check_leave_approver_mandatory,
		)
		self.assertEqual(result["status"], preflight.PASS)

	def test_self_leave_approval_allowed_fails(self):
		result = self._with_hr_setting(
			"prevent_self_leave_approval", 0, preflight.check_self_leave_approval_blocked
		)
		self.assertEqual(result["status"], preflight.FAIL)

	def test_self_leave_approval_blocked_passes(self):
		result = self._with_hr_setting(
			"prevent_self_leave_approval", 1, preflight.check_self_leave_approval_blocked
		)
		self.assertEqual(result["status"], preflight.PASS)

	def test_a_legacy_approved_but_unsubmitted_leave_is_counted_as_a_warning(self):
		from helixhr.tests.utils import ensure_leave_allocation

		ensure_leave_allocation(self.employee_name, "Casual Leave", 5)
		leave = frappe.get_doc(
			{
				"doctype": "Leave Application",
				"employee": self.employee_name,
				"leave_type": "Casual Leave",
				"from_date": add_days(today(), 96),
				"to_date": add_days(today(), 96),
				"description": "legacy defect row",
				"leave_approver": frappe.session.user,
			}
		)
		leave.insert(ignore_permissions=True)
		# Exactly the shape the pre-P2-U1 approval path left behind.
		frappe.db.set_value("Leave Application", leave.name, "status", "Approved", update_modified=False)
		try:
			result = preflight.check_unsubmitted_approved_leave()
			self.assertEqual(result["status"], preflight.WARN)
			self.assertIn("never submitted", result["detail"])

			# And the patch that reports them finds this row.
			from helixhr.patches.v1_0.report_unsubmitted_approved_leave import FIELDS

			listed = frappe.get_all(
				"Leave Application", filters={"docstatus": 0, "status": "Approved"}, fields=FIELDS
			)
			self.assertIn(leave.name, [row.name for row in listed])
		finally:
			frappe.delete_doc("Leave Application", leave.name, force=True, ignore_permissions=True)

	def test_custom_docperm_coverage_passes_on_this_site(self):
		"""If this ever fails, something has removed another role's access to
		one of the doctypes this app customises -- see
		patches.v1_0.apply_permission_deltas."""
		result = preflight.check_custom_docperm_coverage()
		self.assertEqual(result["status"], preflight.PASS, result["detail"])


class TestPreflightP2U9(IntegrationTestCase):
	"""P2-U9 scenario 6. The go-live gate has to judge *values*: an upload
	policy that still allows SVG, a per-user bound quietly loosened in site
	config, a production site left in test mode, CSRF turned off, or an auth
	phase that contradicts itself."""

	def setUp(self):
		frappe.set_user("Administrator")

	def _with_system_setting(self, field, value, fn):
		original = frappe.db.get_single_value("System Settings", field)
		frappe.db.set_single_value("System Settings", field, value)
		try:
			return fn()
		finally:
			frappe.db.set_single_value("System Settings", field, original)

	def _with_conf(self, key, value, fn):
		missing = object()
		original = frappe.conf.get(key, missing)
		if value is None:
			frappe.conf.pop(key, None)
		else:
			frappe.conf[key] = value
		try:
			return fn()
		finally:
			if original is missing:
				frappe.conf.pop(key, None)
			else:
				frappe.conf[key] = original

	def test_allow_tests_on_a_site_is_a_fail(self):
		self.assertEqual(
			self._with_conf("allow_tests", 1, preflight.check_test_mode)["status"], preflight.FAIL
		)
		self.assertEqual(
			self._with_conf("allow_tests", 0, preflight.check_test_mode)["status"], preflight.PASS
		)

	def test_ignore_csrf_is_a_fail(self):
		self.assertEqual(self._with_conf("ignore_csrf", 1, preflight.check_csrf)["status"], preflight.FAIL)
		self.assertEqual(self._with_conf("ignore_csrf", 0, preflight.check_csrf)["status"], preflight.PASS)

	def test_an_upload_extension_outside_the_policy_fails(self):
		def _svg_allowed():
			return self._with_system_setting(
				"allowed_file_extensions", "PDF\nPNG\nSVG", preflight.check_file_settings
			)

		result = _svg_allowed()
		self.assertEqual(result["status"], preflight.FAIL)
		self.assertIn("SVG", result["detail"])

	def test_the_exact_policy_passes(self):
		def _exact():
			return self._with_system_setting(
				"allowed_file_extensions",
				"PDF\nPNG\nJPG\nJPEG\nDOCX\nXLSX",
				lambda: self._with_system_setting(
					"max_file_size",
					10,
					lambda: self._with_system_setting(
						"allow_guests_to_upload_files",
						0,
						lambda: self._with_system_setting(
							"only_allow_system_managers_to_upload_public_files",
							1,
							preflight.check_file_settings,
						),
					),
				),
			)

		self.assertEqual(_exact()["status"], preflight.PASS)

	def test_a_max_file_size_above_the_policy_fails(self):
		result = self._with_system_setting(
			"max_file_size",
			50,
			lambda: self._with_system_setting(
				"allowed_file_extensions", "PDF", preflight.check_file_settings
			),
		)
		self.assertEqual(result["status"], preflight.FAIL)
		self.assertIn("50 MB", result["detail"])

	def test_a_missing_or_loosened_rate_bound_fails(self):
		result = self._with_conf(
			"helixhr_rate_limits", {"apply_for_leave": [200, 3600]}, preflight.check_rate_limits
		)
		self.assertEqual(result["status"], preflight.FAIL)
		self.assertIn("apply_for_leave", result["detail"])

	def test_a_tightened_rate_bound_still_passes(self):
		result = self._with_conf(
			"helixhr_rate_limits", {"apply_for_leave": [5, 3600]}, preflight.check_rate_limits
		)
		self.assertEqual(result["status"], preflight.PASS)

	def test_the_entra_phase_fails_while_the_key_is_missing(self):
		self.assertFalse(preflight._entra_enabled())
		result = self._with_conf("helixhr_auth_phase", "entra", preflight.check_entra)
		self.assertEqual(result["status"], preflight.FAIL)

	def test_the_entra_phase_fails_while_password_login_is_still_on(self):
		result = self._with_conf(
			"helixhr_auth_phase",
			"entra",
			lambda: self._with_system_setting(
				"disable_user_pass_login", 0, preflight.check_password_login
			),
		)
		self.assertEqual(result["status"], preflight.FAIL)
		self.assertIn("still enabled", result["detail"])

	def test_the_https_check_warns_rather_than_passing_when_it_cannot_run(self):
		result = self._with_conf("helixhr_public_url", None, preflight.check_public_endpoint)
		self.assertEqual(result["status"], preflight.WARN)
		self.assertIn("host-only", result["detail"])

	def test_a_plain_http_public_url_fails(self):
		result = self._with_conf(
			"helixhr_public_url", "http://example.invalid/helixhr", preflight.check_public_endpoint
		)
		self.assertEqual(result["status"], preflight.FAIL)
