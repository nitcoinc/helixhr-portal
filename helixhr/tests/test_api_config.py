"""P5-U13: the configuration API -- categories, message templates, and the
named short field set of three HRMS masters (P5-KTD12)."""

import uuid

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from helixhr.api import (
	get_portal_config,
	save_holiday_list,
	save_leave_type,
	save_message_template,
	save_request_category,
	save_shift_type,
)
from helixhr.tests.utils import (
	EMPLOYEE_USER,
	HR_MANAGER_EMPLOYEE_USER,
	make_test_employee_and_manager,
	make_test_hr_manager_employee,
)
from helixhr.utils import TEMPLATE_TOKENS, render_tokens


class TestRenderTokens(IntegrationTestCase):
	def test_a_jinja_looking_body_renders_as_literal_text(self):
		"""P5-KTD11: the security test this design exists for."""
		body = "Hello {name}, {{ frappe.get_doc('User', 'Administrator').delete() }}"
		rendered = render_tokens(body, {"name": "Priya"})
		self.assertEqual(
			rendered, "Hello Priya, {{ frappe.get_doc('User', 'Administrator').delete() }}"
		)

	def test_an_unknown_token_is_left_alone(self):
		self.assertEqual(render_tokens("Hi {name}, see {mystery}", {"name": "Priya"}), "Hi Priya, see {mystery}")

	def test_every_declared_token_is_actually_supplied_by_its_callers(self):
		"""Guarded by preflight too (`check_template_tokens`); this is the
		same assertion run directly against the fixed map."""
		for tokens in TEMPLATE_TOKENS.values():
			probe = {token: f"__{token}__" for token in tokens}
			body = " ".join(f"{{{token}}}" for token in tokens)
			rendered = render_tokens(body, probe)
			for token in tokens:
				self.assertIn(probe[token], rendered)


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
		self.assertIn("templates", config)
		self.assertIn("leave_types", config)
		leave_type = config["leave_types"][0]
		self.assertEqual(
			set(leave_type) - {"name"},
			{"leave_type_name", "max_leaves_allowed", "is_carry_forward", "is_lwp", "helixhr_hr_approves"},
		)

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

	def test_save_message_template_refuses_an_unknown_key(self):
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		with self.assertRaises(frappe.ValidationError):
			save_message_template("not_a_real_template", subject="x")

	def test_a_141_character_subject_is_refused_and_nothing_is_written(self):
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		before = frappe.db.get_value("HelixHR Message Template", "request_arrival", "subject")
		with self.assertRaises(frappe.ValidationError):
			save_message_template("request_arrival", subject="x" * 141)
		self.assertEqual(frappe.db.get_value("HelixHR Message Template", "request_arrival", "subject"), before)

	def test_saving_as_an_hr_user_not_administrator_persists_the_value(self):
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
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
		limit, _seconds = RATE_LIMIT_POLICY["save_message_template"]
		for _ in range(limit):
			save_message_template("request_arrival", subject="Rate limit probe")
		with self.assertRaises(frappe.RateLimitExceededError):
			save_message_template("request_arrival", subject="One too many")
		reset_rate_limit("save_message_template", HR_MANAGER_EMPLOYEE_USER)
