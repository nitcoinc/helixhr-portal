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
	ensure_hr_manager_user,
	ensure_notification_manager_user,
	ensure_test_company,
	make_test_employee_and_manager,
	make_test_hr_manager_employee,
)

OTHER_COMPANY = "_Test Reminders Co B"


def _company(name, abbr):
	if not frappe.db.exists("Company", name):
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

		# The anchored HR Manager's own company, configured once for the
		# class: every endpoint test below reads or tries to write it.
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		save_celebration_reminder(
			event="birthday", subject="Happy birthday {{ names }}", body="Cheers", is_enabled=1
		)

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
