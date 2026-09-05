import frappe
from frappe.tests import IntegrationTestCase

from helixhr.tests.utils import (
	EMPLOYEE_USER,
	MANAGER_USER,
	ORPHAN_USER,
	make_test_employee_and_manager,
	make_test_user_without_employee,
)
from helixhr.utils import PORTAL_HOME_PAGE, portal_home_page


class TestPortalLanding(IntegrationTestCase):
	"""P2: where a user lands after signing in.

	Registered as `get_website_user_home_page`, which Frappe consults before
	`role_home_page` and before Website Settings. The rule cannot be "holds
	the Employee role" -- HR staff are employees too -- so it is "has an
	active Employee record and does not work in Desk"."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_test_employee_and_manager()
		make_test_user_without_employee()

	def test_an_employee_lands_on_the_portal(self):
		self.assertEqual(portal_home_page(EMPLOYEE_USER), PORTAL_HOME_PAGE)

	def test_a_manager_is_an_employee_too_and_lands_on_the_portal(self):
		self.assertEqual(portal_home_page(MANAGER_USER), PORTAL_HOME_PAGE)

	def test_hr_keeps_desk_even_though_they_are_also_an_employee(self):
		"""The case `role_home_page` cannot express: it matches the first
		entry in frappe.get_roles(), whose order is undefined, so an HR
		Manager who also holds Employee would land on the portal on some
		sites and on Desk on others."""
		user = frappe.get_doc("User", EMPLOYEE_USER)
		user.append_roles("HR Manager")
		user.save(ignore_permissions=True)
		frappe.clear_cache(user=EMPLOYEE_USER)
		self.addCleanup(frappe.clear_cache, user=EMPLOYEE_USER)

		self.assertIsNone(portal_home_page(EMPLOYEE_USER))

	def test_a_user_with_no_employee_record_is_left_alone(self):
		"""They get Frappe's own landing, and the portal's own not-linked
		state if they navigate to it -- not a redirect loop into a portal
		that has nothing to show them."""
		self.assertIsNone(portal_home_page(ORPHAN_USER))

	def test_guest_and_administrator_are_left_alone(self):
		self.assertIsNone(portal_home_page("Guest"))
		self.assertIsNone(portal_home_page("Administrator"))

	def test_a_left_employee_is_left_alone(self):
		"""Status, not merely a user_id link: someone who has left keeps
		their login until IT disables it, and must not be sent to a portal
		that will refuse every read."""
		employee = frappe.db.get_value("Employee", {"user_id": EMPLOYEE_USER}, "name")
		frappe.db.set_value("Employee", employee, "status", "Left")
		self.addCleanup(frappe.db.set_value, "Employee", employee, "status", "Active")

		self.assertIsNone(portal_home_page(EMPLOYEE_USER))

	def test_the_hook_is_registered(self):
		"""Without this the whole feature is dead code: Frappe only calls it
		because hooks.py names it."""
		self.assertEqual(
			frappe.get_hooks("get_website_user_home_page"),
			["helixhr.utils.portal_home_page"],
		)
