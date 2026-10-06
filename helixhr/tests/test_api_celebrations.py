"""Plan 2026-10-04-004 U4: the Email Templates page's "Celebrations &
holidays" group -- Portal Admin / System Manager endpoints for per-company
celebration settings.

`test_celebration_reminder_doctype.py` covers the doctype and the save
path's doctype-level rules; `test_reminders.py` covers the senders. This
file covers the HTTP surface: who may call, for which company, and what
each endpoint answers.
"""

import frappe
from frappe.tests import IntegrationTestCase

from helixhr.tests.utils import (
	EMAIL_ADMIN_USER,
	EMPLOYEE_USER,
	HR_MANAGER_EMPLOYEE_USER,
	HR_MANAGER_USER,
	NOTIFICATION_MANAGER_USER,
	ensure_baseline_company,
	ensure_email_admin_user,
	ensure_hr_manager_user,
	ensure_notification_manager_user,
	ensure_test_company,
	make_test_employee_and_manager,
	make_test_hr_manager_employee,
	suspend_chart_of_account_fixtures,
)

OTHER_COMPANY = "_Test Reminders Co B"


def _company(name, abbr):
	if not frappe.db.exists("Company", name):
		with suspend_chart_of_account_fixtures():
			frappe.get_doc(
				{
					"doctype": "Company",
					"company_name": name,
					"abbr": abbr,
					"default_currency": "USD",
					"country": "United States",
				}
			).insert(ignore_permissions=True)
	return name


class TestCelebrationEndpoints(IntegrationTestCase):
	"""Portal Admin / System Manager edit any company's celebration mail; HR
	roles and the Notification Manager are refused."""

	@classmethod
	def setUpClass(cls):
		cls.company = ensure_test_company()
		_company(OTHER_COMPANY, "TRCB")
		make_test_hr_manager_employee()
		ensure_hr_manager_user()
		ensure_notification_manager_user()
		ensure_email_admin_user()
		cls.employee, *_ = make_test_employee_and_manager()

	def setUp(self):
		frappe.set_user("Administrator")
		from helixhr.api import save_celebration_reminder

		# A save edits the *seeded* Email Template by name (P8-U12's rule, one
		# template per event, never a second) -- so whatever the tests write
		# into the shipped defaults is snapshot-and-restored, the same shape
		# `test_celebration_reminder_doctype.py` takes.
		self.template_snapshots = {}
		for name in (
			"HelixHR Birthday Reminder",
			"HelixHR Work Anniversary Reminder",
			"HelixHR Holiday Reminder",
		):
			if frappe.db.exists("Email Template", name):
				doc = frappe.get_doc("Email Template", name)
				self.template_snapshots[name] = {
					field: doc.get(field) for field in ("subject", "response_html", "response", "use_html")
				}

		frappe.set_user(EMAIL_ADMIN_USER)
		save_celebration_reminder(
			event="birthday",
			subject="Happy birthday {{ names }}",
			body="Cheers",
			is_enabled=1,
			company=self.company,
		)

	def tearDown(self):
		frappe.set_user("Administrator")
		for name, snapshot in self.template_snapshots.items():
			doc = frappe.get_doc("Email Template", name)
			doc.update(snapshot)
			doc.save(ignore_permissions=True)

	def test_a_portal_admin_reads_and_saves_any_company(self):
		"""No DocPerm at all, yet the save lands: the gate is the role check
		and the writes run with `ignore_permissions`."""
		from helixhr.api import get_celebration_setup, save_celebration_reminder

		frappe.set_user(EMAIL_ADMIN_USER)
		setup = get_celebration_setup(self.company)
		self.assertEqual(setup["company"], self.company)
		self.assertIn(OTHER_COMPANY, setup["companies"])
		self.assertIn("company", setup["template_tokens"])
		self.assertNotIn("logo_url", setup)
		self.assertEqual(set(setup["events"]), {"birthday", "work_anniversary", "holiday"})
		self.assertEqual(setup["events"]["birthday"]["subject"], "Happy birthday {{ names }}")

		for company in (self.company, OTHER_COMPANY):
			result = save_celebration_reminder(
				event="birthday", subject="Edited subject", body="Cheers", is_enabled=0, company=company
			)
			self.assertEqual(result["subject"], "Edited subject")

	def test_a_portal_admin_with_no_default_company_gets_none_and_the_choice(self):
		"""On a multi-company site an editor with no Employee record has no
		company to default to -- the setup answers with `company: None` plus
		the companies, and the page's company selector takes it from there."""
		from helixhr.api import get_celebration_setup

		ensure_baseline_company()
		frappe.set_user(EMAIL_ADMIN_USER)
		setup = get_celebration_setup()
		self.assertIsNone(setup["company"])
		self.assertGreaterEqual(len(setup["companies"]), 2)

	def test_hr_roles_and_the_notification_manager_are_refused_everywhere(self):
		from helixhr.api import (
			get_celebration_setup,
			preview_celebration,
			reset_celebration_template,
			save_celebration_reminder,
			search_celebration_recipients,
			send_test_celebration,
		)

		company = self.company
		calls = (
			lambda: get_celebration_setup(company),
			lambda: save_celebration_reminder(
				event="birthday", subject="x", body="y", is_enabled=1, company=company
			),
			lambda: preview_celebration(event="birthday", subject="x", body="y", company=company),
			lambda: send_test_celebration(event="birthday", subject="x", body="y", company=company),
			lambda: search_celebration_recipients(company, query="a"),
			lambda: reset_celebration_template(event="birthday", company=company),
		)
		for user in (HR_MANAGER_EMPLOYEE_USER, HR_MANAGER_USER, NOTIFICATION_MANAGER_USER, EMPLOYEE_USER):
			frappe.set_user(user)
			for call in calls:
				with self.subTest(user=user), self.assertRaises(frappe.PermissionError):
					call()

	def test_an_unknown_company_is_refused(self):
		from helixhr.api import save_celebration_reminder

		frappe.set_user(EMAIL_ADMIN_USER)
		with self.assertRaises(frappe.ValidationError):
			save_celebration_reminder(
				event="birthday", subject="X", body="Y", is_enabled=1, company="_No Such Company"
			)

	def test_the_recipient_search_returns_only_the_companys_employees(self):
		from helixhr.api import search_celebration_recipients

		frappe.set_user(EMAIL_ADMIN_USER)
		rows = search_celebration_recipients(self.company, query="")
		self.assertTrue(rows)
		for row in rows:
			self.assertEqual(frappe.db.get_value("Employee", row["name"], "company"), self.company)

	def test_the_preview_renders_the_selected_companys_name(self):
		from helixhr.api import preview_celebration

		frappe.set_user(EMAIL_ADMIN_USER)
		rendered = preview_celebration(
			event="birthday",
			subject="Hello {{ company }}",
			body="Welcome to {{ company }}",
			company=OTHER_COMPANY,
		)
		self.assertEqual(rendered["subject"], f"Hello {OTHER_COMPANY}")
		self.assertIn(OTHER_COMPANY, rendered["html"])

	def test_the_test_send_mails_only_the_caller(self):
		from unittest.mock import patch

		from helixhr.api import send_test_celebration

		frappe.set_user(EMAIL_ADMIN_USER)
		calls = []
		with patch("frappe.sendmail", side_effect=lambda **kwargs: calls.append(kwargs)):
			send_test_celebration(
				event="birthday", subject="Hi {{ names }}", body="Cheers", company=self.company
			)

		self.assertEqual(len(calls), 1)
		self.assertEqual(calls[0]["recipients"], [EMAIL_ADMIN_USER])

	def test_a_holiday_save_carries_the_frequency(self):
		from helixhr.api import save_celebration_reminder

		frappe.set_user(EMAIL_ADMIN_USER)
		result = save_celebration_reminder(
			event="holiday",
			subject="Holidays at {{ company }}",
			body="Enjoy",
			is_enabled=1,
			company=self.company,
			frequency="Monthly",
		)
		self.assertEqual(result["frequency"], "Monthly")

	def test_the_write_is_rate_limited(self):
		from helixhr.utils import RATE_LIMIT_POLICY

		for action in (
			"save_celebration_reminder",
			"get_celebration_setup",
			"preview_celebration",
			"send_test_celebration",
			"search_celebration_recipients",
		):
			self.assertIn(action, RATE_LIMIT_POLICY)


class TestPerCompanyCelebrationTemplates(IntegrationTestCase):
	"""Plan 2026-10-05-001 U11: one Email Template per (event, company), the
	HelixHR sandbox render with the theme wrap, reset, and the clone patch."""

	@classmethod
	def setUpClass(cls):
		cls.company = ensure_test_company()
		_company(OTHER_COMPANY, "TRCB")
		ensure_email_admin_user()

	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.set_user("Administrator")

	def _template(self, event, company):
		from helixhr.reminders import celebration_template_name

		return frappe.db.get_value(
			"Email Template", celebration_template_name(event, company), ["subject", "response_html"], as_dict=True
		)

	def test_saving_one_companys_template_leaves_the_other_unchanged(self):
		from helixhr.api import save_celebration_reminder

		frappe.set_user(EMAIL_ADMIN_USER)
		save_celebration_reminder(event="birthday", subject="B text", body="B body", company=OTHER_COMPANY)
		save_celebration_reminder(event="birthday", subject="A text", body="A body", company=self.company)

		self.assertEqual(self._template("birthday", OTHER_COMPANY).subject, "B text")
		self.assertEqual(self._template("birthday", self.company).subject, "A text")
		row = frappe.db.get_value(
			"HelixHR Celebration Reminder", {"event": "birthday", "company": OTHER_COMPANY}, "email_template"
		)
		self.assertNotEqual(row, "HelixHR Birthday Reminder", "never the shared seeded name")

	def test_reset_restores_the_default_for_that_company_only(self):
		from helixhr.api import reset_celebration_template, save_celebration_reminder
		from helixhr.reminders import CELEBRATION_DEFAULTS

		frappe.set_user(EMAIL_ADMIN_USER)
		save_celebration_reminder(event="holiday", subject="Other kept", body="x", company=OTHER_COMPANY)
		save_celebration_reminder(event="holiday", subject="Mine edited", body="x", company=self.company)

		result = reset_celebration_template(event="holiday", company=self.company)
		self.assertEqual(result["subject"], CELEBRATION_DEFAULTS["holiday"]["subject"])
		self.assertEqual(result["body"], CELEBRATION_DEFAULTS["holiday"]["body"])
		self.assertEqual(self._template("holiday", OTHER_COMPANY).subject, "Other kept")

	def test_a_company_with_no_row_shows_the_default_text(self):
		from helixhr.api import _celebration_reminder_projection
		from helixhr.reminders import CELEBRATION_DEFAULTS

		company = _company("_Test Celebration Defaults Co", "TCDC")
		projection = _celebration_reminder_projection("work_anniversary", company)
		self.assertEqual(projection["subject"], CELEBRATION_DEFAULTS["work_anniversary"]["subject"])
		self.assertEqual(projection["body"], CELEBRATION_DEFAULTS["work_anniversary"]["body"])

	def test_the_sandbox_refuses_frappe_and_record_reads(self):
		from helixhr.utils import render_celebration_email

		for body in (
			"{{ frappe.get_doc('Company', 'X').name }}",
			"{{ frappe.db.get_value('Company', {}, 'name') }}",
		):
			with self.assertRaises(Exception):
				render_celebration_email("S", body, {"company": "A"})

		from helixhr.api import save_celebration_reminder

		frappe.set_user(EMAIL_ADMIN_USER)
		with self.assertRaises(frappe.ValidationError):
			save_celebration_reminder(
				event="birthday",
				subject="x",
				body="{{ frappe.db.get_value('Company', {}, 'name') }}",
				company=OTHER_COMPANY,
			)

	def test_a_body_only_template_is_wrapped_and_a_self_branded_one_is_not(self):
		from helixhr.patches.v1_0.seed_celebration_templates import TEMPLATES
		from helixhr.utils import EMAIL_THEME, render_celebration_email

		frappe.db.set_single_value(EMAIL_THEME, "logo", "/files/acme-logo.png")
		self.addCleanup(frappe.db.set_single_value, EMAIL_THEME, "logo", "")
		context = {
			"company": "Acme",
			"logo_url": "https://hr.example.com/files/acme-logo.png",
			"portal_url": "https://hr.example.com/helixhr",
			"persons": [{"name": "Ada Lovelace", "first_name": "Ada", "image_url": ""}],
			"names": "Ada Lovelace",
			"count": 1,
			"date": "2026-10-05",
		}
		wrapped = render_celebration_email("Hi {{ names }}", "<p>Cheers</p>", context)["message"]
		self.assertEqual(wrapped.count("acme-logo.png"), 1, "the theme carries the logo")
		self.assertIn('embed="/files/acme-logo.png"', wrapped)
		self.assertIn("<p>Cheers</p>", wrapped)
		preview = render_celebration_email("Hi", "<p>Cheers</p>", context, embed_logo=False)["message"]
		self.assertIn('src="/files/acme-logo.png"', preview)
		self.assertNotIn("embed=", preview)

		full = render_celebration_email("S", "<html><body>Mine</body></html>", context)["message"]
		self.assertEqual(full, "<html><body>Mine</body></html>", "a full document is never wrapped")

		# An HR edit of the old seeded fragment prints the logo itself: one
		# <img>, not two -- and on a send it is embedded, not linked.
		edited = TEMPLATES[0]["response_html"].replace("Do say something", "Say hello")
		message = render_celebration_email("S", edited, context)["message"]
		self.assertEqual(message.count("acme-logo.png"), 1)
		self.assertIn('embed="/files/acme-logo.png"', message)

	def test_the_clone_patch_copies_per_company_preserving_custom_text_and_is_idempotent(self):
		from helixhr.patches.v1_0 import clone_celebration_templates_per_company as patch
		from helixhr.patches.v1_0.seed_celebration_templates import TEMPLATES
		from helixhr.reminders import CELEBRATION_DEFAULTS, celebration_template_name

		shared_custom = "_Test Shared Custom Birthday"
		if not frappe.db.exists("Email Template", shared_custom):
			frappe.get_doc(
				{
					"doctype": "Email Template",
					"name": shared_custom,
					"use_html": 1,
					"subject": "Custom {{ names }}",
					"response_html": "<p>Custom body</p>",
				}
			).insert(ignore_permissions=True)
		seeded_holiday = next(spec for spec in TEMPLATES if spec["name"] == "HelixHR Holiday Reminder")
		frappe.db.set_value(
			"Email Template",
			"HelixHR Holiday Reminder",
			{"subject": seeded_holiday["subject"], "response_html": seeded_holiday["response_html"], "use_html": 1},
		)

		companies = (self.company, OTHER_COMPANY)
		for company in companies:
			for event, template in (("birthday", shared_custom), ("holiday", "HelixHR Holiday Reminder")):
				target = celebration_template_name(event, company)
				if frappe.db.exists("Email Template", target):
					frappe.delete_doc("Email Template", target, force=True, ignore_permissions=True)
				row = frappe.db.get_value(
					"HelixHR Celebration Reminder", {"event": event, "company": company}, "name"
				)
				if not row:
					frappe.get_doc(
						{
							"doctype": "HelixHR Celebration Reminder",
							"event": event,
							"company": company,
							"is_enabled": 0,
							"email_template": template,
							"recipient_mode": "All employees",
							"frequency": "Weekly" if event == "holiday" else None,
						}
					).insert(ignore_permissions=True)
				else:
					frappe.db.set_value("HelixHR Celebration Reminder", row, "email_template", template)

		patch.execute()
		for company in companies:
			birthday = self._template("birthday", company)
			self.assertEqual(birthday.subject, "Custom {{ names }}", "customised text survives")
			self.assertEqual(birthday.response_html, "<p>Custom body</p>")
			holiday = self._template("holiday", company)
			self.assertEqual(holiday.response_html, CELEBRATION_DEFAULTS["holiday"]["body"], "unedited seed upgraded")
			self.assertEqual(
				frappe.db.get_value(
					"HelixHR Celebration Reminder", {"event": "birthday", "company": company}, "email_template"
				),
				celebration_template_name("birthday", company),
			)

		before = frappe.db.count("Email Template")
		frappe.db.set_value(
			"Email Template", celebration_template_name("birthday", self.company), "subject", "Edited after"
		)
		patch.execute()
		self.assertEqual(frappe.db.count("Email Template"), before, "a second run creates nothing")
		self.assertEqual(self._template("birthday", self.company).subject, "Edited after")

	def _clone_row(self, event, template, is_enabled=0):
		"""Point (event, self.company)'s row at `template`, per-company clone removed."""
		from helixhr.reminders import celebration_template_name

		target = celebration_template_name(event, self.company)
		if frappe.db.exists("Email Template", target):
			frappe.delete_doc("Email Template", target, force=True, ignore_permissions=True)
		row = frappe.db.get_value("HelixHR Celebration Reminder", {"event": event, "company": self.company}, "name")
		if not row:
			row = (
				frappe.get_doc(
					{
						"doctype": "HelixHR Celebration Reminder",
						"event": event,
						"company": self.company,
						"recipient_mode": "All employees",
						"frequency": "Weekly" if event == "holiday" else None,
					}
				)
				.insert(ignore_permissions=True)
				.name
			)
		frappe.db.set_value(
			"HelixHR Celebration Reminder", row, {"email_template": template, "is_enabled": is_enabled}
		)
		return row

	def test_the_clone_patch_leaves_a_row_with_no_template_idle(self):
		from helixhr.patches.v1_0 import clone_celebration_templates_per_company as patch
		from helixhr.reminders import celebration_template_name

		row = self._clone_row("birthday", None, is_enabled=1)
		patch.execute()
		self.assertFalse(frappe.db.get_value("HelixHR Celebration Reminder", row, "email_template"))
		self.assertFalse(frappe.db.exists("Email Template", celebration_template_name("birthday", self.company)))

	def test_the_clone_patch_replaces_a_source_the_sandbox_refuses_with_the_default(self):
		from helixhr.patches.v1_0 import clone_celebration_templates_per_company as patch
		from helixhr.reminders import CELEBRATION_DEFAULTS

		unsafe = "_Test Unsafe Shared Birthday"
		if not frappe.db.exists("Email Template", unsafe):
			frappe.get_doc(
				{
					"doctype": "Email Template",
					"name": unsafe,
					"use_html": 1,
					"subject": "Hi {{ names }}",
					"response_html": "<p>{{ frappe.get_doc('User', 'Administrator').email }}</p>",
				}
			).insert(ignore_permissions=True)
		self._clone_row("birthday", unsafe)
		frappe.db.delete("Error Log", {"method": ["like", f"%{unsafe}%"]})
		patch.execute()
		clone = self._template("birthday", self.company)
		self.assertEqual(clone.subject, CELEBRATION_DEFAULTS["birthday"]["subject"])
		self.assertEqual(clone.response_html, CELEBRATION_DEFAULTS["birthday"]["body"])
		self.assertTrue(frappe.db.exists("Error Log", {"method": ["like", f"%{unsafe}%"]}))
