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

	def test_pending_section_is_null_before_hr_request_doctype_exists(self):
		# HR Request lands in U9. Until then, _get_pending_counts throws
		# (doctype doesn't exist) and the whole "pending" card is null --
		# this is the section-level failure isolation the method is built
		# for, not a bug. Update this test once U9 ships HR Request.
		frappe.set_user(EMPLOYEE_USER)
		result = get_dashboard()

		self.assertIsNone(result["pending"])

	def test_unread_notifications_is_a_count(self):
		frappe.set_user(EMPLOYEE_USER)
		result = get_dashboard()

		self.assertIsInstance(result["unread_notifications"], int)

	def tearDown(self):
		frappe.set_user("Administrator")
