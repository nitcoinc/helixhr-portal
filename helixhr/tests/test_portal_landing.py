import frappe
from frappe.tests import IntegrationTestCase

from helixhr.tests.utils import (
	EMPLOYEE_USER,
	MANAGER_USER,
	ORPHAN_USER,
	ensure_hr_manager_user,
	make_test_employee_and_manager,
	make_test_hr_manager_employee,
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

	def test_an_hr_manager_with_an_employee_record_lands_on_the_portal(self):
		"""P4-KTD8 inverts the assertion this test used to make.

		HR Manager kept Desk while the portal had nothing for HR to do. It
		now has an HR queue (P4-R10, P4-R11), so HR belongs in the portal and
		Desk is one click away in the shell. HR User, System Manager and
		Administrator still keep Desk -- none of them has a portal queue --
		which is why the rule stays a role set and not "holds the Employee
		role".
		"""
		employee, user = make_test_hr_manager_employee()
		self.assertTrue(employee)
		frappe.clear_cache(user=user)
		self.addCleanup(frappe.clear_cache, user=user)

		self.assertEqual(portal_home_page(user), PORTAL_HOME_PAGE)

	def test_an_hr_manager_with_no_employee_record_keeps_desk(self):
		"""The other half of KTD8. An HR Manager who is not an employee has
		no portal identity at all: every portal read starts from
		`get_current_employee` and would throw, so Frappe's own landing is
		the honest answer."""
		user = ensure_hr_manager_user()
		self.assertIsNone(portal_home_page(user))

	def test_hr_user_still_keeps_desk(self):
		"""HR User has no queue of its own in the portal (the HR queue is the
		HR Manager role, P4 scope), so the role still means "works in Desk"."""
		user = frappe.get_doc("User", EMPLOYEE_USER)
		user.append_roles("HR User")
		user.save(ignore_permissions=True)
		frappe.clear_cache(user=EMPLOYEE_USER)
		self.addCleanup(frappe.clear_cache, user=EMPLOYEE_USER)
		self.addCleanup(self._drop_role, EMPLOYEE_USER, "HR User")

		self.assertIsNone(portal_home_page(EMPLOYEE_USER))

	def _drop_role(self, user, role):
		doc = frappe.get_doc("User", user)
		doc.set("roles", [row for row in doc.roles if row.role != role])
		doc.save(ignore_permissions=True)

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
		# `set_value` runs no doc hooks, so ERPNext does not disable the login
		# here -- but any *other* suite that saves this Employee while the
		# status reads Left does, and then the fixture identity cannot sign in
		# for the rest of the run. Restoring it costs one write and makes the
		# order these suites happen to run in stop mattering.
		self.addCleanup(frappe.db.set_value, "User", EMPLOYEE_USER, "enabled", 1)

		self.assertIsNone(portal_home_page(EMPLOYEE_USER))

	def test_the_hook_is_registered(self):
		"""Without this the whole feature is dead code: Frappe only calls it
		because hooks.py names it."""
		self.assertEqual(
			frappe.get_hooks("get_website_user_home_page"),
			["helixhr.utils.portal_home_page"],
		)
