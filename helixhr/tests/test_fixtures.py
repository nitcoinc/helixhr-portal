import frappe
from frappe.tests import IntegrationTestCase

from helixhr.tests.utils import make_test_employee_and_manager


class TestHelixHRTestFixtures(IntegrationTestCase):
	"""The employee/manager fixture used by every later unit's tests and
	by the Playwright auth setup (U3). Prove it here once so a broken
	fixture fails loudly and close to the cause, not as a mystery
	failure three units later."""

	def test_creates_employee_reporting_to_manager_with_user_permissions(self):
		employee_name, employee_user, manager_name, manager_user = make_test_employee_and_manager()

		employee = frappe.get_doc("Employee", employee_name)
		self.assertEqual(employee.user_id, employee_user)
		self.assertEqual(employee.reports_to, manager_name)
		self.assertEqual(employee.status, "Active")

		manager = frappe.get_doc("Employee", manager_name)
		self.assertEqual(manager.user_id, manager_user)

	def test_is_idempotent(self):
		first = make_test_employee_and_manager()
		second = make_test_employee_and_manager()

		self.assertEqual(first, second)
