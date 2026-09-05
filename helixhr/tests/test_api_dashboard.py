import frappe
from frappe.tests import IntegrationTestCase
from hrms.api import get_leave_balance_map

from helixhr.api import get_dashboard
from helixhr.tests.utils import EMPLOYEE_USER, make_test_employee_and_manager


class TestHelixHRDashboard(IntegrationTestCase):
	def setUp(self):
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()

	def test_employee_header_and_manager_name_for_session_user(self):
		frappe.set_user(EMPLOYEE_USER)
		result = get_dashboard()

		self.assertEqual(result["employee"]["name"], self.employee_name)
		self.assertEqual(result["employee"]["reports_to"], self.manager_name)
		self.assertEqual(result["employee"]["manager_name"], frappe.db.get_value("Employee", self.manager_name, "employee_name"))

	def test_leave_balances_matches_hrms_api_with_no_allocation(self):
		frappe.set_user(EMPLOYEE_USER)
		result = get_dashboard()

		self.assertEqual(result["leave_balances"], get_leave_balance_map())
		self.assertEqual(result["leave_balances"], {})

	def test_employee_argument_is_ignored(self):
		frappe.set_user(EMPLOYEE_USER)
		result = get_dashboard(employee=self.manager_name)

		self.assertEqual(result["employee"]["name"], self.employee_name)

	def test_guest_is_refused(self):
		# A direct in-process call bypasses Frappe's guest check entirely --
		# it lives in the HTTP dispatch layer (frappe.handler.is_whitelisted),
		# not in the whitelisted function itself. That layer's real check is
		# exactly this set membership (see frappe/__init__.py's whitelist()
		# decorator and is_whitelisted()) -- assert it directly instead of
		# a real nested HTTP request. A werkzeug.test.Client(application)
		# call from inside this same process reliably leaked a DB
		# connection that then hung every later test's first insert with
		# a MariaDB lock-wait timeout -- reproduced identically on a clean
		# GitHub Actions runner, not just the dev VM. Real HTTP-level
		# guest coverage already exists in Playwright (login-dashboard.spec.ts).
		self.assertNotIn(get_dashboard, frappe.guest_methods)

	def test_pending_section_is_a_real_count_now_that_hr_request_exists(self):
		# HR Request shipped in U9 -- _get_pending_counts no longer throws,
		# so "pending" is a real dict, not the section-level null this
		# tested before the doctype existed.
		frappe.set_user(EMPLOYEE_USER)
		result = get_dashboard()

		self.assertIsInstance(result["pending"], dict)
		self.assertEqual(
			set(result["pending"]), {"my_open_leave", "my_open_requests", "approvals_waiting_for_me"}
		)

	def test_unread_notifications_is_a_count(self):
		frappe.set_user(EMPLOYEE_USER)
		result = get_dashboard()

		self.assertIsInstance(result["unread_notifications"], int)

	def test_a_broken_section_names_itself_and_leaves_the_rest_of_the_page_alone(self):
		"""P2-U4 scenario 7 / P2-R25. A null section used to be
		indistinguishable from "nothing recorded yet", so an outage read as
		an empty month. The response now says which region failed, and only
		that region."""
		from unittest.mock import patch

		frappe.set_user(EMPLOYEE_USER)
		with patch("helixhr.api._get_attendance_summary", side_effect=Exception("boom")):
			result = get_dashboard()

		self.assertEqual(result["failed_sections"], ["attendance_this_month"])
		self.assertIsNone(result["attendance_this_month"])
		self.assertEqual(result["employee"]["name"], self.employee_name)
		self.assertIsNotNone(result["week"])
		self.assertIsNotNone(result["needs_you"])

	def test_a_healthy_page_names_no_failed_section(self):
		frappe.set_user(EMPLOYEE_USER)

		self.assertEqual(get_dashboard()["failed_sections"], [])

	def tearDown(self):
		frappe.set_user("Administrator")
