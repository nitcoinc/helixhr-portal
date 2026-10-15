"""Plan 2026-10-15-002 U3: `send_timesheet_reminder` (R9-R13, KTD5-KTD7).

One mail through `send_notification` (patched at `frappe.sendmail`, so the
template still renders), one bell, an Info comment per timesheet, and a 24h
(approver, company) claim in Redis that any HR caller hits."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from helixhr import reminders
from helixhr.api import send_timesheet_reminder
from helixhr.tests.test_pending_by_approver import make_timesheet
from helixhr.tests.utils import (
	_make_role_user,
	ensure_baseline_company,
	ensure_test_company,
	make_test_hr_manager_employee,
	make_test_user,
)
from helixhr.utils import rate_limit_bounds, reset_rate_limit

MANAGER = "tsr-manager@helixhr.test"
REPORT = "tsr-report@helixhr.test"
HR_USER = "tsr-hr-user@helixhr.test"


def _as(user, fn, *args, **kwargs):
	frappe.set_user(user)
	try:
		return fn(*args, **kwargs)
	finally:
		frappe.set_user("Administrator")


class TestTimesheetReminder(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.manager = make_test_user(MANAGER, self.company, status="Active")
		self.report = make_test_user(REPORT, self.company, reports_to=self.manager)
		_hr, self.hr_user = _make_role_user(HR_USER, "HR User", self.company, status="Active")
		self._release()
		self.addCleanup(self._release)
		self.addCleanup(frappe.db.set_value, "Employee", self.manager, "status", "Active")

	def _release(self):
		reminders.release_reminder(MANAGER, self.company)

	def _send(self, user=None):
		with patch("frappe.sendmail") as sendmail:
			result = _as(user or self.hr_user, send_timesheet_reminder, MANAGER)
		return result, sendmail

	def _bells(self):
		return frappe.db.count("Notification Log", {"for_user": MANAGER, "document_type": "Timesheet"})

	def test_one_mail_one_bell_a_comment_each_and_the_guard(self):
		names = [make_timesheet(self, self.report, age, week_offset=index) for index, age in enumerate((4, 2))]
		modified = {name: frappe.db.get_value("Timesheet", name, "modified") for name in names}
		bells = self._bells()

		result, sendmail = self._send()

		self.assertTrue(result["sent"])
		self.assertTrue(result["next_reminder_at"])
		self.assertEqual(sendmail.call_count, 1)
		self.assertEqual(sendmail.call_args.kwargs["recipients"], [MANAGER])
		self.assertEqual(self._bells(), bells + 1)
		self.assertIsNotNone(reminders.next_reminder_at(MANAGER, self.company))
		for name in names:
			self.assertTrue(
				frappe.db.exists(
					"Comment", {"reference_doctype": "Timesheet", "reference_name": name, "comment_type": "Info"}
				)
			)
			self.assertEqual(frappe.db.get_value("Timesheet", name, "modified"), modified[name])

	def test_a_second_reminder_inside_24h_is_refused_whoever_sends_it(self):
		make_timesheet(self, self.report, 3)
		self._send()
		_hr_employee, hr_manager = make_test_hr_manager_employee()
		result, sendmail = self._send(hr_manager)
		self.assertFalse(result["sent"])
		self.assertTrue(result["next_reminder_at"])
		sendmail.assert_not_called()

	def test_once_the_claim_expires_a_reminder_is_allowed_again(self):
		make_timesheet(self, self.report, 3)
		self._send()
		self._release()  # what the 24h expiry does
		result, sendmail = self._send()
		self.assertTrue(result["sent"])
		self.assertEqual(sendmail.call_count, 1)

	def test_an_inactive_approver_is_refused_and_nothing_is_sent(self):
		make_timesheet(self, self.report, 3)
		frappe.db.set_value("Employee", self.manager, "status", "Inactive")
		with patch("frappe.sendmail") as sendmail:
			self.assertRaises(frappe.ValidationError, _as, self.hr_user, send_timesheet_reminder, MANAGER)
		sendmail.assert_not_called()
		self.assertIsNone(reminders.next_reminder_at(MANAGER, self.company))

	def test_an_approver_whose_items_are_all_pending_hr_is_refused(self):
		make_timesheet(self, self.report, 3, state="Pending HR")
		self.assertRaises(frappe.PermissionError, _as, self.hr_user, send_timesheet_reminder, MANAGER)

	def test_an_approver_user_that_owns_none_of_the_rows_is_refused(self):
		make_timesheet(self, self.report, 3)
		self.assertRaises(
			frappe.PermissionError, _as, self.hr_user, send_timesheet_reminder, "nobody@helixhr.test"
		)

	def test_a_caller_from_another_company_gets_not_found(self):
		make_timesheet(self, self.report, 3)
		_hr, outsider = _make_role_user("tsr-hr-other@helixhr.test", "HR User", ensure_baseline_company())
		self.assertRaises(frappe.PermissionError, _as, outsider, send_timesheet_reminder, MANAGER)

	def test_the_digest_guard_and_the_reminder_guard_are_independent(self):
		make_timesheet(self, self.report, 3)
		digest_guard = f"{reminders.OVERDUE_GUARD_PREFIX}{frappe.utils.getdate()}"
		had_digest = frappe.cache.get_value(digest_guard)
		frappe.cache.set_value(digest_guard, 1, expires_in_sec=60)
		if not had_digest:
			self.addCleanup(frappe.cache.delete_value, digest_guard)
		result, _sendmail = self._send()
		self.assertTrue(result["sent"])
		self.assertTrue(frappe.cache.get_value(digest_guard))

	def test_nothing_queued_releases_the_claim(self):
		make_timesheet(self, self.report, 3)
		with patch("helixhr.api.send_notification", return_value=0):
			self.assertRaises(frappe.ValidationError, _as, self.hr_user, send_timesheet_reminder, MANAGER)
		self.assertIsNone(reminders.next_reminder_at(MANAGER, self.company))

	def test_a_plain_employee_is_refused(self):
		make_timesheet(self, self.report, 3)
		self.assertRaises(frappe.PermissionError, _as, REPORT, send_timesheet_reminder, MANAGER)

	def test_the_rate_limit_policy_applies(self):
		make_timesheet(self, self.report, 3)
		limit, _seconds = rate_limit_bounds("send_timesheet_reminder")
		reset_rate_limit("send_timesheet_reminder", self.hr_user)
		frappe.flags.helixhr_enforce_rate_limits = True
		self.addCleanup(reset_rate_limit, "send_timesheet_reminder", self.hr_user)
		self.addCleanup(setattr, frappe.flags, "helixhr_enforce_rate_limits", False)
		for _ in range(limit):
			self._send()
		with patch("frappe.sendmail"):
			self.assertRaises(
				frappe.RateLimitExceededError, _as, self.hr_user, send_timesheet_reminder, MANAGER
			)
