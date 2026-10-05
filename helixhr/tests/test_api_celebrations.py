"""Plan 2026-10-04-004 U4: the Email Templates page's "Celebrations &
holidays" group -- scoped endpoints for per-company celebration settings.

`test_celebration_reminder_doctype.py` covers the doctype and the save
path's doctype-level rules; `test_reminders.py` covers the senders. This
file covers the HTTP surface: who may call, for which company, and what
each endpoint answers.
"""

import frappe
from frappe.tests import IntegrationTestCase

from helixhr.tests.utils import (
	EMPLOYEE_USER,
	HR_MANAGER_EMPLOYEE_USER,
	HR_MANAGER_USER,
	NOTIFICATION_MANAGER_USER,
	ensure_baseline_company,
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
	@classmethod
	def setUpClass(cls):
		ensure_test_company()
		_company(OTHER_COMPANY, "TRCB")
		make_test_hr_manager_employee()
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

		# The anchored HR Manager's own company, configured once for the
		# class: every endpoint test below reads or tries to write it.
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		save_celebration_reminder(
			event="birthday", subject="Happy birthday {{ names }}", body="Cheers", is_enabled=1
		)

	def tearDown(self):
		frappe.set_user("Administrator")
		for name, snapshot in self.template_snapshots.items():
			doc = frappe.get_doc("Email Template", name)
			doc.update(snapshot)
			doc.save(ignore_permissions=True)

	def test_an_hr_manager_reads_and_saves_their_own_company(self):
		from helixhr.api import get_celebration_setup, save_celebration_reminder

		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		company = frappe.db.get_value("Employee", {"user_id": HR_MANAGER_EMPLOYEE_USER}, "company")

		setup = get_celebration_setup(company)
		self.assertEqual(setup["company"], company)
		self.assertEqual(setup["companies"], [company], "a scoped HR Manager sees one company")
		self.assertIn("company", setup["template_tokens"])
		self.assertEqual(set(setup["events"]), {"birthday", "work_anniversary", "holiday"})
		self.assertEqual(setup["events"]["birthday"]["subject"], "Happy birthday {{ names }}")

		result = save_celebration_reminder(
			event="birthday",
			subject="Edited subject",
			body="Cheers",
			is_enabled=1,
			company=company,
		)
		self.assertEqual(result["subject"], "Edited subject")

	def test_an_scoped_hr_manager_cannot_save_another_companys_row(self):
		from helixhr.api import save_celebration_reminder

		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		with self.assertRaises(frappe.PermissionError):
			save_celebration_reminder(
				event="birthday",
				subject="X",
				body="Y",
				is_enabled=1,
				company=OTHER_COMPANY,
			)

	def test_a_scoped_hr_manager_cannot_read_or_search_another_company(self):
		from helixhr.api import get_celebration_setup, search_celebration_recipients

		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		with self.assertRaises(frappe.PermissionError):
			get_celebration_setup(OTHER_COMPANY)
		with self.assertRaises(frappe.PermissionError):
			search_celebration_recipients(OTHER_COMPANY, query="a")

	def test_an_unscoped_caller_may_edit_any_company(self):
		from helixhr.api import get_celebration_setup, save_celebration_reminder

		# `ensure_hr_manager_user` is the Desk-only persona: no Employee
		# record, so `resolve_admin_scope` is unscoped (P3-KTD7).
		ensure_hr_manager_user()
		frappe.set_user(HR_MANAGER_USER)
		for company in (frappe.db.get_value("Company", {"name": ["!=", ""]}, "name"), OTHER_COMPANY):
			setup = get_celebration_setup(company)
			self.assertEqual(setup["company"], company)
		result = save_celebration_reminder(
			event="birthday", subject="Unscoped edit", body="Y", is_enabled=0, company=OTHER_COMPANY
		)
		self.assertEqual(result["subject"], "Unscoped edit")

	def test_an_unscoped_caller_with_no_default_company_gets_none_and_the_choice(self):
		"""KTD3 on a multi-company site: a Desk-only HR Manager with no
		Employee record has no company to default to -- the setup answers
		with `company: None` plus the companies they may configure, and the
		page's company selector takes it from there (the old throw left the
		page blank with the refusal swallowed)."""
		from helixhr.api import get_celebration_setup

		ensure_test_company()
		ensure_baseline_company()
		ensure_hr_manager_user()
		frappe.set_user(HR_MANAGER_USER)

		setup = get_celebration_setup()
		self.assertIsNone(setup["company"])
		self.assertGreaterEqual(len(setup["companies"]), 2)

	def test_a_notification_manager_without_hr_is_refused_everywhere(self):
		"""KTD2/KTD3: the celebration group is HR's; the Notification Manager
		keeps the message templates and does not see this group."""
		from helixhr.api import (
			get_celebration_setup,
			preview_celebration,
			save_celebration_reminder,
			search_celebration_recipients,
			send_test_celebration,
		)

		ensure_notification_manager_user()
		frappe.set_user(NOTIFICATION_MANAGER_USER)
		for call in (
			lambda: get_celebration_setup(ensure_test_company()),
			lambda: save_celebration_reminder(
				event="birthday", subject="x", body="y", is_enabled=1
			),
			lambda: preview_celebration(event="birthday", subject="x", body="y"),
			lambda: send_test_celebration(event="birthday", subject="x", body="y"),
			lambda: search_celebration_recipients(ensure_test_company(), query="a"),
		):
			with self.assertRaises(frappe.PermissionError):
				call()

	def test_a_plain_employee_is_refused(self):
		from helixhr.api import get_celebration_setup

		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.PermissionError):
			get_celebration_setup(ensure_test_company())

	def test_the_recipient_search_returns_only_the_companys_employees(self):
		from helixhr.api import search_celebration_recipients

		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		company = frappe.db.get_value("Employee", {"user_id": HR_MANAGER_EMPLOYEE_USER}, "company")
		rows = search_celebration_recipients(company, query="")
		self.assertTrue(rows)
		for row in rows:
			self.assertEqual(
				frappe.db.get_value("Employee", row["name"], "company"), company
			)

	def test_the_preview_renders_the_selected_companys_name(self):
		from helixhr.api import preview_celebration

		# An unscoped caller, so the preview can name a company the test
		# does not own fixtures for.
		ensure_hr_manager_user()
		frappe.set_user(HR_MANAGER_USER)
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

		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		company = frappe.db.get_value("Employee", {"user_id": HR_MANAGER_EMPLOYEE_USER}, "company")
		calls = []
		with patch("frappe.sendmail", side_effect=lambda **kwargs: calls.append(kwargs)):
			send_test_celebration(
				event="birthday", subject="Hi {{ names }}", body="Cheers", company=company
			)

		self.assertEqual(len(calls), 1)
		self.assertEqual(calls[0]["recipients"], [HR_MANAGER_EMPLOYEE_USER])

	def test_a_holiday_save_carries_the_frequency(self):
		from helixhr.api import save_celebration_reminder

		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		company = frappe.db.get_value("Employee", {"user_id": HR_MANAGER_EMPLOYEE_USER}, "company")
		result = save_celebration_reminder(
			event="holiday",
			subject="Holidays at {{ company }}",
			body="Enjoy",
			is_enabled=1,
			company=company,
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
	HelixHR sandbox render with the layout wrap, reset, and the clone patch."""

	@classmethod
	def setUpClass(cls):
		ensure_test_company()
		_company(OTHER_COMPANY, "TRCB")
		make_test_hr_manager_employee()
		ensure_hr_manager_user()
		cls.company = frappe.db.get_value("Employee", {"user_id": HR_MANAGER_EMPLOYEE_USER}, "company")

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

		frappe.set_user(HR_MANAGER_USER)
		save_celebration_reminder(event="birthday", subject="B text", body="B body", company=OTHER_COMPANY)
		save_celebration_reminder(event="birthday", subject="A text", body="A body", company=self.company)

		self.assertEqual(self._template("birthday", OTHER_COMPANY).subject, "B text")
		self.assertEqual(self._template("birthday", self.company).subject, "A text")
		row = frappe.db.get_value(
			"HelixHR Celebration Reminder", {"event": "birthday", "company": OTHER_COMPANY}, "email_template"
		)
		self.assertNotEqual(row, "HelixHR Birthday Reminder", "never the shared seeded name")

	def test_reset_restores_the_default_for_the_callers_company_only(self):
		from helixhr.api import reset_celebration_template, save_celebration_reminder
		from helixhr.reminders import CELEBRATION_DEFAULTS

		frappe.set_user(HR_MANAGER_USER)
		save_celebration_reminder(event="holiday", subject="Other kept", body="x", company=OTHER_COMPANY)
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		save_celebration_reminder(event="holiday", subject="Mine edited", body="x", company=self.company)

		result = reset_celebration_template(event="holiday", company=self.company)
		self.assertEqual(result["subject"], CELEBRATION_DEFAULTS["holiday"]["subject"])
		self.assertEqual(result["body"], CELEBRATION_DEFAULTS["holiday"]["body"])
		self.assertEqual(self._template("holiday", OTHER_COMPANY).subject, "Other kept")

	def test_reset_from_another_companys_hr_manager_is_refused(self):
		from helixhr.api import reset_celebration_template

		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		with self.assertRaises(frappe.PermissionError):
			reset_celebration_template(event="birthday", company=OTHER_COMPANY)
		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.PermissionError):
			reset_celebration_template(event="birthday", company=self.company)

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

		frappe.set_user(HR_MANAGER_USER)
		with self.assertRaises(frappe.ValidationError):
			save_celebration_reminder(
				event="birthday",
				subject="x",
				body="{{ frappe.db.get_value('Company', {}, 'name') }}",
				company=OTHER_COMPANY,
			)

	def test_a_body_only_template_is_wrapped_and_a_self_branded_one_is_not(self):
		from helixhr.patches.v1_0.seed_celebration_templates import TEMPLATES
		from helixhr.utils import render_celebration_email

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
		self.assertEqual(wrapped.count("acme-logo.png"), 1, "the layout carries the logo")
		self.assertIn("<p>Cheers</p>", wrapped)

		full = render_celebration_email("S", "<html><body>Mine</body></html>", context)["message"]
		self.assertEqual(full, "<html><body>Mine</body></html>", "a full document is never wrapped")

		# An HR edit of the old seeded fragment prints the logo itself: one <img>, not two.
		edited = TEMPLATES[0]["response_html"].replace("Do say something", "Say hello")
		message = render_celebration_email("S", edited, context)["message"]
		self.assertEqual(message.count("acme-logo.png"), 1)

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
