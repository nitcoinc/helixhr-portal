from types import MappingProxyType, SimpleNamespace

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

	def test_it_team_role_is_portal_only_and_its_hr_request_lock_is_reviewed(self):
		self.assertEqual(preflight.check_it_team_role()["status"], preflight.PASS)

		from unittest.mock import patch

		with patch.object(preflight, "DESK_ROLES", frozenset({"IT Team"})):
			result = preflight.check_it_team_role()
		self.assertEqual(result["status"], preflight.FAIL)
		self.assertIn("DESK_ROLES", result["detail"])

		with patch.object(
			preflight.frappe,
			"get_meta",
			return_value=SimpleNamespace(
				fields=[SimpleNamespace(fieldname="unreviewed_field", permlevel=1)]
			),
		):
			result = preflight.check_it_team_role()
		self.assertEqual(result["status"], preflight.FAIL)
		self.assertIn("unreviewed_field", result["detail"])

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

	def test_a_fixture_that_never_installed_fails_and_is_named(self):
		"""P3-AE13. The check only earns its place if it fails on a site that
		missed a fixture -- and it has to say *which* one, because the fix is
		per fixture. The lookup is stubbed rather than a real row removed: a
		Workflow State is linked from live Workflows, so deleting one to make
		the check fail would break more than it proves.
		"""
		from unittest.mock import patch

		real_exists = frappe.db.exists
		# One per fixture the import can silently skip: the Workflow's state
		# and action names are Links and `sync_fixtures` runs with
		# `ignore_links`, so an absent row leaves the Workflow itself looking
		# fine (P4-U1 added "Sent Back" and the two new actions).
		for absent in (
			("Workflow State", "Pending HR"),
			("Workflow State", "Sent Back"),
			("Workflow Action Master", "Send Back"),
			("Workflow Action Master", "Send to HR"),
			# P4-U3 / P4-KTD9: HR is told a request reached its queue by
			# these four and by nothing in code, so a missing one is a queue
			# nobody is watching.
			("Notification", "HelixHR Leave Sent To HR"),
			("Notification", "HelixHR New Leave For HR"),
			("Notification", "HelixHR Timesheet Sent To HR"),
			("Notification", "HelixHR Attendance Request Sent To HR"),
		):

			def _exists(doctype, name=None, *args, _absent=absent, **kwargs):
				if (doctype, name) == _absent:
					return None
				return real_exists(doctype, name, *args, **kwargs)

			with patch.object(frappe.db, "exists", side_effect=_exists):
				result = preflight.check_fixtures()

			self.assertEqual(result["status"], preflight.FAIL, msg=absent)
			self.assertIn(absent[1], result["detail"])
			self.assertIn("bench migrate", result["detail"])

	def test_retired_request_notifications_fails_when_re_enabled(self):
		"""P5-U16: neither retired Notification -- the un-routable bell-only
		arrival one, nor the hardcoded status ladder -- may come back
		enabled, whether it exists on this site at all or not."""
		self.assertEqual(preflight.check_retired_request_notifications()["status"], preflight.PASS)

		for name in ("HelixHR New Request For HR", "HelixHR Request Status Changed"):
			if not frappe.db.exists("Notification", name):
				continue
			original = frappe.db.get_value("Notification", name, "enabled")
			frappe.db.set_value("Notification", name, "enabled", 1)
			try:
				result = preflight.check_retired_request_notifications()
				self.assertEqual(result["status"], preflight.FAIL, msg=name)
				self.assertIn(name, result["detail"])
			finally:
				frappe.db.set_value("Notification", name, "enabled", original)

	def test_hr_request_workflow_state_order_fails_when_open_is_not_first(self):
		"""P5-KTD4: `Open` not being `states[0]` throws on every insert, not
		just on read -- this is the standing guard for a regression a
		Desk edit or a later fixture patch could otherwise reintroduce
		silently."""
		self.assertEqual(preflight.check_hr_request_workflow_state_order()["status"], preflight.PASS)

		from types import SimpleNamespace
		from unittest.mock import patch

		with patch.object(
			preflight.frappe,
			"get_doc",
			return_value=SimpleNamespace(states=[SimpleNamespace(state="In Progress")]),
		):
			result = preflight.check_hr_request_workflow_state_order()
		self.assertEqual(result["status"], preflight.FAIL)
		self.assertIn("In Progress", result["detail"])

	def test_request_category_routes_warns_when_nobody_holds_the_role(self):
		"""P5-KTD8's fallback exists so this never blocks the employee, but a
		category nobody currently works should not go unnoticed at deploy
		time. `Has Role` is stubbed to answer "nobody" for `IT Team` rather
		than disabling the real fixture identity every other suite in this
		run depends on; every other lookup passes through untouched."""
		self.assertEqual(preflight.check_request_category_routes()["status"], preflight.PASS)

		category = frappe.get_doc(
			{
				"doctype": "HelixHR Request Category",
				"category_name": "_Test Unrouted Category",
				"hint": "test fixture",
				"route_to_role": "IT Team",
				"is_active": 1,
			}
		)
		category.insert(ignore_permissions=True)
		try:
			from unittest.mock import patch

			real_get_all = frappe.get_all

			def _stub(doctype, filters=None, **kwargs):
				if doctype == "Has Role" and (filters or {}).get("role") == "IT Team":
					return []
				return real_get_all(doctype, filters=filters, **kwargs)

			with patch.object(preflight.frappe, "get_all", side_effect=_stub):
				result = preflight.check_request_category_routes()
			self.assertEqual(result["status"], preflight.WARN)
			self.assertIn(category.name, result["detail"])
		finally:
			frappe.delete_doc("HelixHR Request Category", category.name, force=True, ignore_permissions=True)

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

	def test_a_javascript_document_link_written_before_the_rule_is_a_fail(self):
		"""P2-R19: the doctype validates on save and nothing revalidates a
		row that is never saved again, so a link written before that rule
		still renders into an `:href`. Inserted here past validation, the
		way it got into the table in the first place."""
		doc = frappe.get_doc(
			{
				"doctype": "HelixHR Document Link",
				"title": "_Test legacy link",
				"url": "https://example.test/policy.pdf",
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value(
			"HelixHR Document Link", doc.name, "url", "javascript:alert(1)", update_modified=False
		)
		try:
			result = preflight.check_document_link_urls()
			self.assertEqual(result["status"], preflight.FAIL)
			self.assertIn(doc.name, result["detail"])
		finally:
			frappe.delete_doc("HelixHR Document Link", doc.name, force=True, ignore_permissions=True)

		self.assertEqual(preflight.check_document_link_urls()["status"], preflight.PASS)

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


class TestPreflightP3U1(IntegrationTestCase):
	"""P3-U1 step 6 / P3-R26 / P3-AE13: the check-in prerequisites, judged as
	values. The `Permissions-Policy` check reads a fetched response, so the
	fetch is stubbed; everything else flips real site state and restores it."""

	def setUp(self):
		frappe.set_user("Administrator")

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

	def _with_hr_setting(self, field, value, fn):
		original = frappe.db.get_single_value("HR Settings", field)
		frappe.db.set_single_value("HR Settings", field, value)
		try:
			return fn()
		finally:
			frappe.db.set_single_value("HR Settings", field, original)

	# -- Permissions-Policy value (P3-KTD12) ---------------------------------

	_GOOD_HEADERS = MappingProxyType(
		{
			"Strict-Transport-Security": "max-age=63072000",
			"Content-Security-Policy": "frame-ancestors 'none'",
			"X-Content-Type-Options": "nosniff",
			"Referrer-Policy": "strict-origin-when-cross-origin",
			"Set-Cookie": "sid=abc; Secure; HttpOnly; SameSite=Lax",
		}
	)

	def _check_with_response_headers(self, headers):
		from unittest.mock import patch

		class _Response:
			def __init__(self, headers):
				self.headers = headers
				self.raw = None

		with patch("requests.get", return_value=_Response(headers)):
			return self._with_conf(
				"helixhr_public_url", "https://portal.example.invalid/helixhr", preflight.check_public_endpoint
			)

	def test_a_permissions_policy_that_denies_geolocation_fails(self):
		"""P3-AE13. `geolocation=()` -- the app's own value before P3, and what
		a reverse proxy template commonly sets -- makes every punch read as a
		denial by the user."""
		result = self._check_with_response_headers(
			{**self._GOOD_HEADERS, "Permissions-Policy": "camera=(), geolocation=(), usb=()"}
		)
		self.assertEqual(result["status"], preflight.FAIL)
		self.assertIn("geolocation", result["detail"])

	def test_a_second_geolocation_directive_appended_by_a_proxy_fails(self):
		"""A substring match passed this: the app's own `geolocation=(self)`
		is still in the header, and the browser denies anyway because the
		proxy's `geolocation=()` is there too (P3-KTD12)."""
		result = self._check_with_response_headers(
			{
				**self._GOOD_HEADERS,
				"Permissions-Policy": "geolocation=(self), camera=(), geolocation=()",
			}
		)
		self.assertEqual(result["status"], preflight.FAIL)
		self.assertIn("geolocation", result["detail"])

	def test_a_missing_permissions_policy_still_fails(self):
		result = self._check_with_response_headers(dict(self._GOOD_HEADERS))
		self.assertEqual(result["status"], preflight.FAIL)
		self.assertIn("no Permissions-Policy", result["detail"])

	def test_the_apps_own_header_value_passes(self):
		from helixhr.utils import SECURITY_HEADERS

		result = self._check_with_response_headers(
			{**self._GOOD_HEADERS, "Permissions-Policy": SECURITY_HEADERS["Permissions-Policy"]}
		)
		self.assertEqual(result["status"], preflight.PASS, result["detail"])

	# -- HR Settings flags (P3-R8, P3-KTD4) ---------------------------------

	def test_mobile_checkin_off_warns_and_on_passes(self):
		off = self._with_hr_setting(
			"allow_employee_checkin_from_mobile_app", 0, preflight.check_checkin_settings
		)
		self.assertEqual(off["status"], preflight.WARN)
		self.assertIn("never shows the check-in button", off["detail"])

		on = self._with_hr_setting("allow_employee_checkin_from_mobile_app", 1, preflight.check_checkin_settings)
		self.assertEqual(on["status"], preflight.PASS)

	def test_geolocation_tracking_is_reported_either_way_without_changing_the_verdict(self):
		def _with_mobile_on(fn):
			return self._with_hr_setting("allow_employee_checkin_from_mobile_app", 1, fn)

		for value, phrase in ((0, "tracking off"), (1, "tracking on")):
			result = _with_mobile_on(
				lambda: self._with_hr_setting("allow_geolocation_tracking", value, preflight.check_checkin_settings)
			)
			self.assertEqual(result["status"], preflight.PASS, result["detail"])
			self.assertIn(phrase, result["detail"])

	# -- Shift Types (P3-KTD5) ----------------------------------------------

	def _shift_type(self, **fields):
		name = "_Test P3-U1 Preflight Shift"
		if not frappe.db.exists("Shift Type", name):
			frappe.get_doc(
				{"doctype": "Shift Type", "__newname": name, "start_time": "09:00:00", "end_time": "17:00:00"}
			).insert(ignore_permissions=True)
		frappe.db.set_value("Shift Type", name, fields, update_modified=False)
		self.addCleanup(frappe.delete_doc, "Shift Type", name, force=True, ignore_permissions=True)
		return name

	def test_no_auto_attendance_shift_type_warns(self):
		"""The baseline is not assumed empty: every auto-attendance shift on
		the site is switched off for the duration and switched back."""
		enabled = frappe.get_all("Shift Type", filters={"enable_auto_attendance": 1}, pluck="name")
		for name in enabled:
			frappe.db.set_value("Shift Type", name, "enable_auto_attendance", 0, update_modified=False)
		try:
			result = preflight.check_shift_types()
		finally:
			for name in enabled:
				frappe.db.set_value("Shift Type", name, "enable_auto_attendance", 1, update_modified=False)
		self.assertEqual(result["status"], preflight.WARN)
		self.assertIn("no Shift Type", result["detail"])

	def test_an_auto_attendance_shift_that_cannot_mark_attendance_warns(self):
		name = self._shift_type(
			enable_auto_attendance=1,
			process_attendance_after=None,
			auto_update_last_sync=0,
			last_sync_of_checkin=None,
		)
		result = preflight.check_shift_types()
		self.assertEqual(result["status"], preflight.WARN)
		self.assertIn(f"{name}: Process Attendance After is empty", result["detail"])
		self.assertIn(f"{name}: Last Sync of Checkin is empty", result["detail"])

	def test_a_stale_last_sync_warns_and_a_fresh_one_does_not(self):
		name = self._shift_type(
			enable_auto_attendance=1,
			process_attendance_after=add_days(today(), -30),
			auto_update_last_sync=0,
			last_sync_of_checkin=frappe.utils.add_days(frappe.utils.now_datetime(), -5),
		)
		stale = preflight.check_shift_types()
		self.assertEqual(stale["status"], preflight.WARN)
		self.assertIn(f"{name}: Last Sync of Checkin is", stale["detail"])
		self.assertIn("older than 2 days", stale["detail"])

		frappe.db.set_value(
			"Shift Type", name, "last_sync_of_checkin", frappe.utils.now_datetime(), update_modified=False
		)
		fresh = preflight.check_shift_types()
		self.assertNotIn(name, fresh["detail"])

	def test_an_auto_updated_shift_needs_no_last_sync(self):
		name = self._shift_type(
			enable_auto_attendance=1,
			process_attendance_after=add_days(today(), -30),
			auto_update_last_sync=1,
			last_sync_of_checkin=None,
		)
		result = preflight.check_shift_types()
		self.assertNotIn(name, result["detail"])

	# -- retention key (P3-KTD15) -------------------------------------------

	def test_the_retention_key_warns_while_unset_and_passes_when_set(self):
		unset = self._with_conf(
			"helixhr_checkin_location_retention_days", None, preflight.check_checkin_location_retention
		)
		self.assertEqual(unset["status"], preflight.WARN)
		self.assertIn("helixhr_checkin_location_retention_days", unset["detail"])

		result = self._with_conf(
			"helixhr_checkin_location_retention_days", 90, preflight.check_checkin_location_retention
		)
		self.assertEqual(result["status"], preflight.PASS)
		self.assertIn("90 days", result["detail"])

	# -- delta coverage (P3-KTD13) ------------------------------------------

	def test_a_delta_doctype_with_no_custom_docperm_row_fails(self):
		"""A doctype named in the patch's delta table with no Custom DocPerm
		row at all means the delta never ran on this site. Asserted through a
		doctype that has none, rather than by deleting real rows."""
		from unittest.mock import patch

		customised = set(frappe.get_all("Custom DocPerm", distinct=True, pluck="parent"))
		untouched = next(
			(
				dt
				for dt in frappe.get_all(
					"DocType", filters={"istable": 0, "issingle": 0, "custom": 0}, pluck="name", order_by="name"
				)
				if dt not in customised
			),
			None,
		)
		self.assertIsNotNone(untouched, "every doctype on this site has Custom DocPerm rows?")

		with patch.object(preflight, "DELTAS", {untouched: ()}):
			result = preflight.check_custom_docperm_coverage()
		self.assertEqual(result["status"], preflight.FAIL)
		self.assertIn(untouched, result["detail"])
		self.assertIn("never ran", result["detail"])

	def test_a_delta_flipped_back_on_the_site_fails_and_names_the_doctype(self):
		"""Presence was not the question. A site where somebody handed role
		Employee its `create` on Employee Checkin back in the Role
		Permissions Manager still has Custom DocPerm rows for the doctype,
		and the delta P3-KTD13 applies is gone."""
		row = frappe.db.get_value(
			"Custom DocPerm",
			{"parent": "Employee Checkin", "role": "Employee", "permlevel": 0, "if_owner": 0},
			"name",
		)
		self.assertIsNotNone(row, "the Employee Checkin delta has not run on this site")
		self.assertEqual(preflight.check_custom_docperm_coverage()["status"], preflight.PASS)

		frappe.db.set_value("Custom DocPerm", row, "create", 1)
		try:
			result = preflight.check_custom_docperm_coverage()
			self.assertEqual(result["status"], preflight.FAIL)
			self.assertIn("Employee Checkin", result["detail"])
			self.assertIn("create", result["detail"])
		finally:
			frappe.db.set_value("Custom DocPerm", row, "create", 0)
			frappe.clear_cache(doctype="Employee Checkin")
		self.assertEqual(preflight.check_custom_docperm_coverage()["status"], preflight.PASS)

	def test_the_delta_doctypes_are_all_covered_on_this_site(self):
		"""Employee Checkin and Attendance Request joined the table in P3; a
		site that ran the dated re-run line in patches.txt carries both."""
		from helixhr.patches.v1_0.apply_permission_deltas import DELTAS

		self.assertIn("Employee Checkin", DELTAS)
		self.assertIn("Attendance Request", DELTAS)
		result = preflight.check_custom_docperm_coverage()
		self.assertEqual(result["status"], preflight.PASS, result["detail"])

	def test_every_new_rate_limit_action_has_bounds(self):
		"""P3-U1 scenario 4: every method P3 adds is bounded before it exists."""
		from helixhr.utils import rate_limit_bounds

		for action in (
			"punch_my_checkin",
			"create_my_attendance_request",
			"send_my_attendance_request",
			"withdraw_my_attendance_request",
			"get_attendance_request_preview",
			"download_my_payslip",
			"get_directory",
			"get_my_team_week",
		):
			limit, seconds = rate_limit_bounds(action)
			self.assertGreater(limit, 0, action)
			self.assertGreater(seconds, 0, action)
		self.assertEqual(preflight.check_rate_limits()["status"], preflight.PASS)

class TestPreflightHolidayCoverage(IntegrationTestCase):
	"""P3-R26: the holiday-list check, judged against real assignments.

	It is the quietest missing setting in the phase -- the Holidays page says
	it cannot tell, the attendance calendar cannot name working days, and a
	Fix a day preview cannot separate a holiday from a working day -- so it
	fails rather than warns.
	"""

	def setUp(self):
		frappe.set_user("Administrator")

	def test_it_passes_once_every_active_employee_resolves_a_list(self):
		from helixhr.preflight import PASS, check_holiday_list_coverage
		from helixhr.tests.utils import ensure_holiday_list_assignment, ensure_test_company

		ensure_holiday_list_assignment(ensure_test_company())
		# Anyone left over from another suite without an assignment would
		# fail this legitimately, so the assertion is about our own company's
		# employees resolving, not about the whole site being tidy.
		uncovered = self._uncovered()
		if uncovered:
			self.skipTest(f"site carries employees outside the fixture company: {uncovered[:3]}")

		self.assertEqual(check_holiday_list_coverage()["status"], PASS)

	def test_it_fails_and_names_the_people_when_no_list_resolves(self):
		from helixhr.preflight import FAIL, check_holiday_list_coverage
		from helixhr.tests.utils import ensure_test_company, ensure_test_gender

		employee = frappe.get_doc(
			{
				"doctype": "Employee",
				"employee_number": "P3-PREFLIGHT-NO-HOLIDAYS",
				"first_name": "Holidayless Person",
				"date_of_birth": "1990-01-01",
				"date_of_joining": "2020-01-01",
				"gender": ensure_test_gender(),
				"company": self._company_without_a_holiday_list(),
				"status": "Active",
			}
		).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "Employee", employee.name, force=True)

		result = check_holiday_list_coverage()

		self.assertEqual(result["status"], FAIL)
		self.assertIn("Holidayless Person", result["detail"])
		self.assertIn("Holiday List Assignment", result["detail"])

	def _company_without_a_holiday_list(self):
		"""A company of its own, so the fixture company's assignment cannot
		cover this employee and the failure is the one being asserted."""
		name = "_Test Holidayless Company"
		if not frappe.db.exists("Company", name):
			frappe.get_doc(
				{
					"doctype": "Company",
					"company_name": name,
					"abbr": "THC",
					"default_currency": "USD",
					"country": "United States",
				}
			).insert(ignore_permissions=True)
		return name

	def _uncovered(self):
		from hrms.utils.holiday_list import get_holiday_list_for_employee

		names = []
		for row in frappe.get_all("Employee", filters={"status": "Active"}, pluck="name"):
			try:
				if not get_holiday_list_for_employee(row, raise_exception=False):
					names.append(row)
			except Exception:
				names.append(row)
		return names

class TestPreflightPdfGenerator(IntegrationTestCase):
	"""P3-R2: a payslip download is only as available as the binary that
	renders it, and its absence looks like a server fault rather than a
	setup gap -- which is how it first surfaced, as a 500 in CI."""

	def setUp(self):
		frappe.set_user("Administrator")

	def test_it_passes_when_the_generator_is_on_the_path(self):
		"""Mocked, not read off this host: the CI job that runs the Python
		suite has no PDF generator (only the e2e job installs one), so
		asserting against the real PATH tests the runner, not the check."""
		from unittest.mock import patch

		from helixhr.preflight import PASS, check_pdf_generator

		with patch("shutil.which", return_value="/usr/local/bin/wkhtmltopdf"):
			result = check_pdf_generator()

		self.assertEqual(result["status"], PASS)
		self.assertIn("wkhtmltopdf", result["detail"])
		self.assertIn("/usr/local/bin/wkhtmltopdf", result["detail"])

	def test_it_fails_and_names_the_missing_binary(self):
		from unittest.mock import patch

		from helixhr.preflight import FAIL, check_pdf_generator

		with patch("shutil.which", return_value=None):
			result = check_pdf_generator()

		self.assertEqual(result["status"], FAIL)
		self.assertIn("wkhtmltopdf", result["detail"])
		self.assertIn("500", result["detail"])


class TestPreflightCelebrationReminders(IntegrationTestCase):
	"""P4-R18 / P4-KTD10: the double-send guard, after the fact.

	`events.hr_settings_validate` refuses the contradiction at the moment HR
	creates it; these are the routes that never reach `validate` -- a fixture
	import, a raw `db_set`, a restored site.
	"""

	FIELDS = (
		"helixhr_birthday_template",
		"helixhr_anniversary_template",
		"send_birthday_reminders",
		"send_work_anniversary_reminders",
	)
	TEMPLATE = "_Test HelixHR Birthday"

	def setUp(self):
		frappe.set_user("Administrator")
		from helixhr.tests.test_reminders import _template

		_template(self.TEMPLATE, "BDAYMARK {{ names }}")
		self.original = {
			field: frappe.db.get_single_value("HR Settings", field) for field in self.FIELDS
		}
		self._set(dict.fromkeys(self.FIELDS, None))

	def tearDown(self):
		self._set(self.original)

	def _set(self, values):
		for field, value in values.items():
			frappe.db.set_single_value("HR Settings", field, value)
		frappe.clear_document_cache("HR Settings", "HR Settings")

	def _check(self):
		from helixhr.preflight import check_celebration_reminders

		return check_celebration_reminders()

	def test_both_senders_on_for_one_event_fails_and_names_both_fields(self):
		from helixhr.preflight import FAIL

		self._set({"send_birthday_reminders": 1, "helixhr_birthday_template": self.TEMPLATE})
		result = self._check()

		self.assertEqual(result["status"], FAIL)
		self.assertIn("Birthday", result["detail"])
		self.assertIn("Birthdays", result["detail"])
		self.assertIn("HelixHR Birthday Template", result["detail"])

	def test_a_picked_template_that_does_not_exist_fails(self):
		from helixhr.preflight import FAIL

		self._set({"helixhr_anniversary_template": "_Test Template That Went Away"})
		result = self._check()

		self.assertEqual(result["status"], FAIL)
		self.assertIn("_Test Template That Went Away", result["detail"])

	def test_neither_sender_on_warns_rather_than_failing(self):
		from helixhr.preflight import WARN

		result = self._check()

		self.assertEqual(result["status"], WARN)
		self.assertIn("nobody sends", result["detail"])

	def test_helixhr_only_and_hrms_only_both_pass_and_name_the_sender(self):
		from helixhr.preflight import PASS

		self._set({"helixhr_birthday_template": self.TEMPLATE, "send_work_anniversary_reminders": 1})
		result = self._check()

		self.assertEqual(result["status"], PASS)
		self.assertIn(f"HelixHR sends '{self.TEMPLATE}'", result["detail"])
		self.assertIn("HRMS sends its own", result["detail"])


class TestPreflightMailAndHRQueue(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")

	def test_a_site_with_a_default_outgoing_account_passes(self):
		from helixhr.preflight import PASS, check_outgoing_email
		from helixhr.tests.utils import ensure_test_email_account

		account = ensure_test_email_account()
		result = check_outgoing_email()

		self.assertEqual(result["status"], PASS)
		self.assertIn(account, result["detail"])

	def test_a_site_with_no_default_outgoing_account_fails(self):
		"""FAIL, not WARN: the HR-queue notifications send from inside the
		save that escalates a request, so with no account Send to HR is
		refused outright, and so is applying for an HR-approves leave type.
		The detail has to name both refusals, not just the missing account.
		"""
		from unittest.mock import patch

		from helixhr.preflight import FAIL, check_outgoing_email

		with patch("frappe.db.get_value", return_value=None):
			result = check_outgoing_email()

		self.assertEqual(result["status"], FAIL)
		self.assertIn("Email Account", result["detail"])
		self.assertIn("Send to HR", result["detail"])
		self.assertIn("HR-approves leave type", result["detail"])

	def test_an_hr_manager_scoped_to_their_own_employee_is_named(self):
		"""P4-R11 carry-forward: a User Permission on Employee beats HR
		Manager's own read permission on the three approval doctypes, so the
		HR queue is silently empty -- `get_transitions` throws on every row
		that is not theirs. Reported, not judged."""
		from helixhr.preflight import PASS, WARN, check_hr_manager_self_scope
		from helixhr.tests.utils import make_test_hr_manager_employee

		employee, user = make_test_hr_manager_employee()
		self.assertEqual(check_hr_manager_self_scope()["status"], PASS)

		permission = frappe.get_doc(
			{"doctype": "User Permission", "user": user, "allow": "Employee", "for_value": employee}
		).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "User Permission", permission.name, force=True)

		result = check_hr_manager_self_scope()

		self.assertEqual(result["status"], WARN)
		self.assertIn(user, result["detail"])
