"""P5-U13: the configuration API -- categories, message templates, and the
named short field set of three HRMS masters (P5-KTD12)."""

import uuid

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from helixhr.api import (
	get_notification_setup,
	get_portal_bootstrap,
	get_portal_config,
	preview_message_template,
	reset_message_template,
	save_holiday_list,
	save_leave_type,
	save_message_template,
	save_request_category,
	save_shift_type,
	send_test_message,
)
from helixhr.tests.utils import (
	EMPLOYEE_USER,
	HR_MANAGER_EMPLOYEE_USER,
	ensure_holiday_list_assignment,
	ensure_leave_allocation,
	ensure_notification_manager_user,
	make_test_employee_and_manager,
	make_test_hr_manager_employee,
)


class TestMessageTemplateSeed(IntegrationTestCase):
	def test_seed_is_idempotent_and_never_overwrites_an_edit(self):
		from helixhr.patches.v1_0.seed_message_templates import execute

		execute()
		frappe.set_user("Administrator")
		doc = frappe.get_doc("HelixHR Message Template", "request_arrival")
		doc.subject = "Edited subject, mine to keep"
		doc.save(ignore_permissions=True)

		execute()
		doc.reload()
		self.assertEqual(doc.subject, "Edited subject, mine to keep")

	def test_an_edited_template_survives_two_migrate_equivalent_seed_runs(self):
		"""Stands in for `bench migrate` twice (the plan's own verification):
		the seed patch is what migrate re-invokes, and it is exactly what
		must not clobber HR's edit."""
		from helixhr.patches.v1_0.seed_message_templates import execute

		frappe.set_user("Administrator")
		doc = frappe.get_doc("HelixHR Message Template", "request_status_changed")
		doc.body = "A survives-migrate edit"
		doc.save(ignore_permissions=True)

		execute()
		execute()
		doc.reload()
		self.assertEqual(doc.body, "A survives-migrate edit")


class TestProfileCorrectionCategorySeed(IntegrationTestCase):
	"""Plan 2026-09-29-001 U2: the category Profile files corrections under."""

	def test_seed_creates_an_active_category_routed_to_hr_manager(self):
		from helixhr.patches.v1_0.seed_profile_correction_category import execute
		from helixhr.utils import PROFILE_CORRECTION_CATEGORY

		execute()
		row = frappe.db.get_value(
			"HelixHR Request Category", PROFILE_CORRECTION_CATEGORY, ["is_active", "route_to_role"], as_dict=True
		)
		self.assertEqual(row.route_to_role, "HR Manager")
		self.assertTrue(row.is_active)

	def test_seed_never_overwrites_hrs_edit(self):
		from helixhr.patches.v1_0.seed_profile_correction_category import execute
		from helixhr.utils import PROFILE_CORRECTION_CATEGORY

		execute()
		frappe.set_user("Administrator")
		doc = frappe.get_doc("HelixHR Request Category", PROFILE_CORRECTION_CATEGORY)
		original = doc.hint
		doc.hint = "HR's own wording"
		doc.save(ignore_permissions=True)
		self.addCleanup(frappe.db.set_value, "HelixHR Request Category", PROFILE_CORRECTION_CATEGORY, "hint", original)

		execute()
		doc.reload()
		self.assertEqual(doc.hint, "HR's own wording")

	def test_preflight_warns_when_the_category_is_retired(self):
		from helixhr import preflight
		from helixhr.patches.v1_0.seed_profile_correction_category import execute
		from helixhr.utils import PROFILE_CORRECTION_CATEGORY

		execute()
		self.assertEqual(preflight.check_profile_correction_category()["status"], preflight.PASS)
		frappe.db.set_value("HelixHR Request Category", PROFILE_CORRECTION_CATEGORY, "is_active", 0)
		self.addCleanup(frappe.db.set_value, "HelixHR Request Category", PROFILE_CORRECTION_CATEGORY, "is_active", 1)
		result = preflight.check_profile_correction_category()
		self.assertEqual(result["status"], preflight.WARN)
		self.assertIn("inactive", result["detail"])

	def test_an_employee_can_file_a_request_in_it(self):
		from helixhr.api import create_my_request
		from helixhr.patches.v1_0.seed_profile_correction_category import execute
		from helixhr.utils import PROFILE_CORRECTION_CATEGORY

		execute()
		make_test_employee_and_manager()
		frappe.set_user(EMPLOYEE_USER)
		self.addCleanup(frappe.set_user, "Administrator")
		created = create_my_request(
			category=PROFILE_CORRECTION_CATEGORY,
			subject="Correct my date of birth",
			details="Date of birth is shown as 1 Jan 1990.",
			operation_key=str(uuid.uuid4()),
		)
		self.assertEqual(frappe.db.get_value("HR Request", created["name"], "category"), PROFILE_CORRECTION_CATEGORY)


class TestConfigApi(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		make_test_employee_and_manager()
		make_test_hr_manager_employee()
		from helixhr.patches.v1_0.seed_message_templates import execute

		execute()

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_get_portal_config_refuses_a_non_hr_caller(self):
		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.PermissionError):
			get_portal_config()

	def test_get_portal_config_returns_the_short_field_sets_only(self):
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		config = get_portal_config()
		self.assertIn("categories", config)
		# Plan 2026-10-02-001 U10: message wording left Settings.
		self.assertNotIn("templates", config)
		self.assertIn("leave_types", config)
		leave_type = config["leave_types"][0]
		self.assertEqual(
			set(leave_type) - {"name"},
			{
				"leave_type_name",
				"max_leaves_allowed",
				"max_continuous_days_allowed",
				"allow_negative",
				"is_carry_forward",
				"is_lwp",
				"helixhr_hr_approves",
			},
		)

	# --- P8-U6: a Desk list-view link per section ---------------------------

	def test_desk_urls_covers_every_settings_section_for_a_caller_who_can_open_desk(self):
		from frappe.utils import get_url_to_list

		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		desk_urls = get_portal_config()["desk_urls"]
		self.assertEqual(
			set(desk_urls),
			{"categories", "leave_types", "holiday_lists", "shift_types", "celebrations"},
		)
		self.assertEqual(desk_urls["categories"], get_url_to_list("HelixHR Request Category"))
		self.assertEqual(desk_urls["leave_types"], get_url_to_list("Leave Type"))
		self.assertEqual(desk_urls["holiday_lists"], get_url_to_list("Holiday List"))
		self.assertEqual(desk_urls["shift_types"], get_url_to_list("Shift Type"))
		# P8-U12: the Email Template list, not HelixHR Celebration Reminder.
		self.assertEqual(desk_urls["celebrations"], get_url_to_list("Email Template"))

	def test_desk_urls_is_none_for_a_caller_who_cannot_open_desk(self):
		"""`_can_open_desk` is exhaustively tested on its own in
		test_api_people.py (`TestDeskLinks`); this only checks that
		`get_portal_config` actually consults it rather than handing out
		every URL unconditionally -- mocking the gate keeps this test from
		needing a role combination Frappe's own auto-promotion of
		desk-access roles to System User makes impossible to construct for
		real (every role `_is_hr` accepts also grants Desk access)."""
		from unittest.mock import patch

		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		with patch("helixhr.api._can_open_desk", return_value=False):
			self.assertIsNone(get_portal_config()["desk_urls"])

	def test_get_portal_config_returns_a_celebrations_projection_per_event(self):
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		config = get_portal_config()
		self.assertEqual(set(config["celebrations"]), {"birthday", "work_anniversary"})
		for event in ("birthday", "work_anniversary"):
			row = config["celebrations"][event]
			self.assertEqual(
				set(row),
				{"event", "label", "is_enabled", "recipient_mode", "subject", "body", "use_html", "recipients"},
			)
		self.assertIn("company", config["celebration_template_tokens"])
		self.assertIn("portal_url", config["celebration_template_tokens"])

	def test_save_request_category_refuses_a_caller_without_write(self):
		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.PermissionError):
			save_request_category("HR Letter", hint="new hint")

	def test_save_request_category_ignores_a_field_outside_its_named_set(self):
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		save_request_category("Other", hint="Anything else", category_name="Renamed")
		doc = frappe.get_doc("HelixHR Request Category", "Other")
		self.assertEqual(doc.category_name, "Other")
		self.assertEqual(doc.hint, "Anything else")

	def test_deactivating_a_category_in_use_does_not_orphan_stored_requests(self):
		from helixhr.api import create_my_request

		frappe.set_user(EMPLOYEE_USER)
		created = create_my_request(
			category="Other", subject="Needs an answer", operation_key=str(uuid.uuid4())
		)

		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		save_request_category("Other", is_active=0)

		frappe.set_user(EMPLOYEE_USER)
		doc = frappe.get_doc("HR Request", created["name"])
		self.assertEqual(doc.category, "Other")

	def test_save_message_template_refuses_a_caller_without_write(self):
		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.PermissionError):
			save_message_template("request_arrival", subject="hacked")

	def test_save_message_template_refuses_an_hr_manager(self):
		"""Plan 2026-10-02-001 U7 / R14: HR Manager no longer edits templates."""
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		with self.assertRaises(frappe.PermissionError):
			save_message_template("request_arrival", subject="hr edit")

	def test_a_notification_manager_bootstrap_opens_templates_not_settings(self):
		"""U7: no Employee record, no Desk -- the flag the router lands on."""
		frappe.set_user(ensure_notification_manager_user())
		boot = get_portal_bootstrap()
		self.assertTrue(boot["can_manage_notifications"])
		self.assertFalse(boot["can_configure"])
		self.assertFalse(boot["can_open_desk"])
		self.assertIsNone(boot["desk_url"])
		self.assertFalse(boot["employee"] and boot["employee"].get("name"))

		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		self.assertFalse(get_portal_bootstrap()["can_manage_notifications"])
		frappe.set_user("Administrator")
		self.assertTrue(get_portal_bootstrap()["can_manage_notifications"])

	def test_save_message_template_refuses_an_unknown_key(self):
		frappe.set_user(ensure_notification_manager_user())
		with self.assertRaises(frappe.ValidationError):
			save_message_template("not_a_real_template", subject="x")

	def test_a_141_character_subject_is_refused_and_nothing_is_written(self):
		frappe.set_user(ensure_notification_manager_user())
		before = frappe.db.get_value("HelixHR Message Template", "request_arrival", "subject")
		with self.assertRaises(frappe.ValidationError):
			save_message_template("request_arrival", subject="x" * 141)
		self.assertEqual(frappe.db.get_value("HelixHR Message Template", "request_arrival", "subject"), before)

	def test_saving_as_a_notification_manager_not_administrator_persists_the_value(self):
		frappe.set_user(ensure_notification_manager_user())
		result = save_message_template("request_arrival", subject="A new arrival subject {subject}")
		self.assertEqual(result["subject"], "A new arrival subject {subject}")
		frappe.set_user("Administrator")
		self.assertEqual(
			frappe.db.get_value("HelixHR Message Template", "request_arrival", "subject"),
			"A new arrival subject {subject}",
		)

	def test_save_leave_type_still_runs_hrms_validate(self):
		"""HRMS refuses `is_lwp` while an active Leave Allocation exists for
		the type -- proving `doc.save()` runs HRMS's own `validate()`, not
		just this API's allow-list (P5-KTD15)."""
		frappe.set_user("Administrator")
		leave_type = frappe.get_doc(
			{"doctype": "Leave Type", "leave_type_name": "P5-U13 config test leave", "is_lwp": 0}
		).insert(ignore_permissions=True)
		employee, _, _, _ = make_test_employee_and_manager()
		frappe.get_doc(
			{
				"doctype": "Leave Allocation",
				"employee": employee,
				"leave_type": leave_type.name,
				"from_date": add_days(today(), -1),
				"to_date": add_days(today(), 30),
				"new_leaves_allocated": 5,
			}
		).insert(ignore_permissions=True)

		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		with self.assertRaises(frappe.ValidationError):
			save_leave_type(leave_type.name, is_lwp=1)

	def test_save_leave_type_ignores_a_field_outside_its_named_set(self):
		frappe.set_user("Administrator")
		leave_type = frappe.get_doc(
			{"doctype": "Leave Type", "leave_type_name": "P5-U13 allow-list test", "is_lwp": 0}
		).insert(ignore_permissions=True)

		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		save_leave_type(leave_type.name, max_leaves_allowed=12, is_optional_leave=1)
		leave_type.reload()
		self.assertEqual(leave_type.max_leaves_allowed, 12)
		self.assertEqual(leave_type.is_optional_leave, 0)

	def test_save_leave_type_refuses_a_caller_without_write(self):
		frappe.set_user("Administrator")
		leave_type = frappe.get_doc(
			{"doctype": "Leave Type", "leave_type_name": "P5-U13 permission test", "is_lwp": 0}
		).insert(ignore_permissions=True)

		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.PermissionError):
			save_leave_type(leave_type.name, max_leaves_allowed=1)

	def test_save_holiday_list_creates_and_updates_the_named_fields(self):
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		result = save_holiday_list(
			"P5-U13 test holiday list",
			from_date=today(),
			to_date=add_days(today(), 365),
			weekly_off="Sunday",
			holidays=[{"holiday_date": add_days(today(), 5), "description": "Test holiday"}],
		)
		self.assertEqual(result["weekly_off"], "Sunday")
		self.assertEqual(len(result["holidays"]), 1)

		result = save_holiday_list("P5-U13 test holiday list", weekly_off="Saturday")
		self.assertEqual(result["weekly_off"], "Saturday")
		# The identifying field is not rewritten on update.
		self.assertEqual(result["name"], "P5-U13 test holiday list")

	def test_save_shift_type_creates_and_updates_the_named_fields(self):
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		result = save_shift_type(
			"P5-U13 test shift",
			start_time="09:00:00",
			end_time="18:00:00",
			begin_check_in_before_shift_start_time=60,
		)
		self.assertEqual(result["begin_check_in_before_shift_start_time"], 60)

		result = save_shift_type("P5-U13 test shift", end_time="19:00:00")
		self.assertEqual(str(result["end_time"]), "19:00:00")

	def test_every_new_write_is_rate_limited(self):
		from helixhr.tests.utils import ensure_test_company
		from helixhr.utils import RATE_LIMIT_POLICY, reset_rate_limit

		ensure_test_company()
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		for action in (
			"get_portal_config",
			"save_request_category",
			"save_message_template",
			"save_leave_type",
			"save_holiday_list",
			"save_shift_type",
		):
			self.assertIn(action, RATE_LIMIT_POLICY)
			reset_rate_limit(action, HR_MANAGER_EMPLOYEE_USER)

		frappe.flags.helixhr_enforce_rate_limits = True
		self.addCleanup(lambda: frappe.flags.pop("helixhr_enforce_rate_limits", None))
		# U7: templates belong to the Notification Manager now, not HR.
		notification_manager = ensure_notification_manager_user()
		reset_rate_limit("save_message_template", notification_manager)
		frappe.set_user(notification_manager)
		limit, _seconds = RATE_LIMIT_POLICY["save_message_template"]
		for _ in range(limit):
			save_message_template("request_arrival", subject="Rate limit probe")
		with self.assertRaises(frappe.RateLimitExceededError):
			save_message_template("request_arrival", subject="One too many")
		reset_rate_limit("save_message_template", notification_manager)


class TestLeaveTypeLimits(IntegrationTestCase):
	"""U2 (R4): the two fields HRMS really enforces, saved from the portal."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.employee_name, _, _, _ = make_test_employee_and_manager()
		make_test_hr_manager_employee()
		frappe.db.set_value("Employee", self.employee_name, "leave_approver", "Administrator")
		ensure_holiday_list_assignment(frappe.db.get_value("Employee", self.employee_name, "company"))

	def tearDown(self):
		frappe.set_user("Administrator")

	def _type(self, suffix, leaves):
		name = f"_Test U2 {suffix}"
		if not frappe.db.exists("Leave Type", name):
			frappe.get_doc(
				{"doctype": "Leave Type", "leave_type_name": name, "include_holiday": 1}
			).insert(ignore_permissions=True)
		for row in frappe.get_all(
			"Leave Application", filters={"employee": self.employee_name, "leave_type": name}, pluck="name"
		):
			frappe.delete_doc("Leave Application", row, force=True, ignore_permissions=True)
		ensure_leave_allocation(self.employee_name, name, leaves)
		return name

	def _apply(self, leave_type, offset, days):
		frappe.set_user(EMPLOYEE_USER)
		try:
			return frappe.get_doc(
				{
					"doctype": "Leave Application",
					"employee": self.employee_name,
					"leave_type": leave_type,
					"from_date": add_days(today(), offset),
					"to_date": add_days(today(), offset + days - 1),
					"leave_approver": "Administrator",
				}
			).insert()
		finally:
			frappe.set_user("Administrator")

	def test_a_saved_consecutive_limit_refuses_a_longer_request(self):
		leave_type = self._type("Limit", 10)
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		saved = save_leave_type(leave_type, max_continuous_days_allowed=3)
		self.assertEqual(saved["max_continuous_days_allowed"], 3)

		with self.assertRaises(frappe.ValidationError) as caught:
			self._apply(leave_type, 160, days=4)
		# The HRMS sentence U2's errorMap pattern maps to the plain one.
		self.assertIn("cannot be longer than 3", str(caught.exception))
		self.assertEqual(self._apply(leave_type, 170, days=3).status, "Open")

	def test_allow_negative_saved_on_turns_an_overdraw_into_a_warning(self):
		leave_type = self._type("Negative", 1)
		with self.assertRaises(frappe.ValidationError):
			self._apply(leave_type, 175, days=3)

		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		save_leave_type(leave_type, allow_negative=1)
		frappe.clear_messages()
		doc = self._apply(leave_type, 175, days=3)
		self.assertEqual(doc.total_leave_days, 3)
		self.assertTrue(any("Warning" in str(m) for m in frappe.get_message_log()))

	def test_an_unknown_field_is_still_dropped_beside_the_new_ones(self):
		leave_type = self._type("Allowlist", 1)
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		save_leave_type(leave_type, max_continuous_days_allowed=5, is_optional_leave=1)
		self.assertEqual(frappe.db.get_value("Leave Type", leave_type, "max_continuous_days_allowed"), 5)
		self.assertEqual(frappe.db.get_value("Leave Type", leave_type, "is_optional_leave"), 0)

	def test_a_non_hr_caller_cannot_save_the_new_fields(self):
		leave_type = self._type("Perm", 1)
		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.PermissionError):
			save_leave_type(leave_type, allow_negative=1)
		self.assertEqual(frappe.db.get_value("Leave Type", leave_type, "allow_negative"), 0)


class TestSaveMessageTemplateValidation(IntegrationTestCase):
	"""Plan 2026-10-02-001 U8 / R17: the API refuses what the sandbox refuses,
	through the doctype's own validate, and nothing is written."""

	def setUp(self):
		frappe.set_user(ensure_notification_manager_user())

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_an_unknown_variable_is_refused_by_name(self):
		before = frappe.db.get_value("HelixHR Message Template", "request_arrival", "body")
		with self.assertRaises(frappe.ValidationError) as caught:
			save_message_template("request_arrival", subject="New request", body="Hi {{ employe_name }}")
		self.assertIn("employe_name", str(caught.exception))
		self.assertEqual(frappe.db.get_value("HelixHR Message Template", "request_arrival", "body"), before)

	def test_frappe_globals_are_refused(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			save_message_template("request_arrival", subject="x", body="{{ frappe.session.user }}")
		self.assertIn("frappe", str(caught.exception))

	def test_a_syntax_error_is_refused_with_its_line(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			save_message_template(
				"request_arrival", subject="x", body="<p>ok</p>\n{% if category %}never closed"
			)
		self.assertIn("line 2", str(caught.exception))

	def test_a_valid_template_saves(self):
		result = save_message_template(
			"request_arrival",
			subject="{{ category }}: {{ subject }}",
			body="<p>{{ employee_name }}</p>",
			is_enabled=1,
		)
		self.assertEqual(result["subject"], "{{ category }}: {{ subject }}")


class TestNotificationSetupApi(IntegrationTestCase):
	"""Plan 2026-10-02-001 U10: the Email templates page's methods, each behind
	`_assert_can_manage_notifications`."""

	def setUp(self):
		frappe.set_user("Administrator")
		make_test_employee_and_manager()
		make_test_hr_manager_employee()
		frappe.delete_doc_if_exists("HelixHR Message Template", "leave_approved", force=True)
		frappe.set_user(ensure_notification_manager_user())

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_every_setup_method_refuses_an_employee_and_an_hr_manager(self):
		calls = (
			lambda: get_notification_setup(),
			lambda: save_message_template("leave_approved", subject="x", body="<p>x</p>"),
			lambda: reset_message_template("leave_approved"),
			lambda: preview_message_template("leave_approved", subject="x", body="<p>x</p>"),
			lambda: send_test_message("leave_approved", subject="x", body="<p>x</p>"),
		)
		for user in (EMPLOYEE_USER, HR_MANAGER_EMPLOYEE_USER):
			frappe.set_user(user)
			for call in calls:
				with self.subTest(user=user), self.assertRaises(frappe.PermissionError):
					call()

	def test_setup_lists_every_event_with_state_and_variables(self):
		from helixhr.utils import NOTIFICATION_EVENTS

		events = {event["key"]: event for event in get_notification_setup()["events"]}
		self.assertEqual(set(events), set(NOTIFICATION_EVENTS))
		approved = events["leave_approved"]
		self.assertEqual(approved["state"], "Default")
		self.assertEqual(approved["audience"], "Employee")
		names = {variable["name"]: variable for variable in approved["variables"]}
		self.assertIn("approver_name", names)
		self.assertIn("recipient_first_name", names)
		self.assertTrue(names["approver_name"]["description"])
		self.assertTrue(events["bank_change_applied"]["locked"])

	def test_save_then_reset_moves_custom_to_default_with_an_info_comment(self):
		save_message_template(
			"leave_approved", subject="Approved: {{ leave_type }}", body="<p>Yes {{ days }}</p>", is_enabled=1
		)
		events = {event["key"]: event for event in get_notification_setup()["events"]}
		self.assertEqual(events["leave_approved"]["state"], "Custom")
		self.assertEqual(events["leave_approved"]["subject"], "Approved: {{ leave_type }}")

		reset_message_template("leave_approved")
		self.assertFalse(frappe.db.exists("HelixHR Message Template", "leave_approved"))
		events = {event["key"]: event for event in get_notification_setup()["events"]}
		self.assertEqual(events["leave_approved"]["state"], "Default")
		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{
					"comment_type": "Info",
					"reference_doctype": "HelixHR Message Template",
					"reference_name": "leave_approved",
					"content": ("like", "%reset this email template%"),
				},
			)
		)

	def test_preview_renders_samples_in_the_layout_and_refuses_an_unknown_variable(self):
		result = preview_message_template(
			"leave_approved", subject="Hi {{ recipient_first_name }}", body="<p>{{ approver_name }}</p>"
		)
		self.assertEqual(result["subject"], "Hi Priya")
		self.assertIn("Meera Shah", result["html"])
		with self.assertRaises(frappe.ValidationError) as caught:
			preview_message_template("leave_approved", subject="x", body="{{ aprover_name }}")
		self.assertIn("aprover_name", str(caught.exception))

	def test_a_test_send_goes_only_to_the_caller(self):
		from unittest.mock import patch

		with patch("frappe.sendmail") as sendmail:
			result = send_test_message(
				"leave_approved", subject="{{ leave_type }}", body="<p>{{ days }}</p>"
			)
		self.assertEqual(sendmail.call_args.kwargs["recipients"], [frappe.session.user])
		self.assertEqual(result["sent_to"], frappe.session.user)

	def test_a_burst_of_test_sends_is_rate_limited(self):
		from unittest.mock import patch

		from helixhr.utils import _rate_limit_key

		frappe.cache.delete(_rate_limit_key("send_test_message", frappe.session.user))
		frappe.flags.helixhr_enforce_rate_limits = True
		try:
			with patch("frappe.sendmail"), self.assertRaises(frappe.RateLimitExceededError):
				for _attempt in range(10):
					send_test_message("leave_approved", subject="x", body="<p>x</p>")
		finally:
			frappe.flags.helixhr_enforce_rate_limits = False
			frappe.cache.delete(_rate_limit_key("send_test_message", frappe.session.user))
