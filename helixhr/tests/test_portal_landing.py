import frappe
from frappe.tests import IntegrationTestCase

from helixhr.tests.utils import (
	EMPLOYEE_USER,
	HR_MANAGER_USER,
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

	def test_an_it_team_member_is_a_website_user_and_lands_on_the_portal(self):
		from helixhr.tests.utils import make_test_it_user

		employee, user = make_test_it_user()
		self.assertTrue(employee)
		self.assertEqual(frappe.db.get_value("User", user, "user_type"), "Website User")
		self.assertEqual(portal_home_page(user), PORTAL_HOME_PAGE)

	def test_an_hr_manager_with_no_employee_record_keeps_desk(self):
		"""The other half of KTD8. An HR Manager who is not an employee has
		no portal identity of their own, so Frappe's own landing is the
		honest answer. If they open /helixhr they get the desk-only portal
		(TestDeskOnlyPortal below), not a redirect into it."""
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


class TestMicrosoftLoginLanding(IntegrationTestCase):
	"""Frappe's OAuth callback lands a System User on `get_default_path()`
	(`/desk/people`, `/apps`) without ever asking `portal_home_page`, so
	`helixhr.api.login_via_office365` corrects the redirect afterwards."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_test_employee_and_manager()
		ensure_hr_manager_user()

	def _land(self, user, location, response_type="redirect"):
		from helixhr.api import _redirect_portal_user_home

		frappe.set_user(user)
		self.addCleanup(frappe.set_user, "Administrator")
		saved = dict(frappe.local.response)
		self.addCleanup(lambda: (frappe.local.response.clear(), frappe.local.response.update(saved)))
		frappe.local.response["type"] = response_type
		frappe.local.response["location"] = location
		_redirect_portal_user_home()
		return frappe.local.response["location"]

	def test_an_employee_bound_for_desk_lands_on_the_portal(self):
		for location in ("/desk/people", "/apps", "/desk", "https://site.example/app/home", "/", ""):
			with self.subTest(location=location):
				self.assertTrue(self._land(EMPLOYEE_USER, location).endswith(f"/{PORTAL_HOME_PAGE}"))

	def test_a_portal_deep_link_is_kept(self):
		self.assertEqual(self._land(EMPLOYEE_USER, "/helixhr/leave/HR-LAP-1"), "/helixhr/leave/HR-LAP-1")

	def test_a_desk_role_user_keeps_frappes_landing(self):
		self.assertEqual(self._land(HR_MANAGER_USER, "/desk/people"), "/desk/people")

	def test_an_error_page_is_not_turned_into_a_redirect(self):
		# Frappe answers a refused login (signup disabled, expired state)
		# with a web page, not a redirect; that must reach the user as is.
		self.assertEqual(self._land(EMPLOYEE_USER, "/desk", response_type="page"), "/desk")

	def test_the_callback_override_is_registered(self):
		self.assertEqual(
			frappe.override_whitelisted_method("frappe.integrations.oauth2_logins.login_via_office365"),
			"helixhr.api.login_via_office365",
		)


class TestDeskOnlyPortal(IntegrationTestCase):
	"""An HR or System Manager with no Employee record gets the portal's
	admin pages and a way to Desk; an ordinary unlinked user still gets
	"not set up"."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_test_employee_and_manager()
		make_test_user_without_employee()
		ensure_hr_manager_user()

	def _bootstrap_as(self, user):
		from helixhr.api import get_portal_bootstrap

		frappe.set_user(user)
		self.addCleanup(frappe.set_user, "Administrator")
		return get_portal_bootstrap()

	def test_hr_without_an_employee_gets_a_desk_url(self):
		boot = self._bootstrap_as(HR_MANAGER_USER)
		self.assertFalse(boot["employee"])
		self.assertTrue(boot["desk_url"].endswith("/desk"))
		self.assertTrue(boot["can_see_people"])

	def test_an_unlinked_user_gets_no_desk_url(self):
		boot = self._bootstrap_as(ORPHAN_USER)
		self.assertFalse(boot["employee"])
		self.assertIsNone(boot["desk_url"])

	def test_an_employee_gets_no_desk_url(self):
		self.assertIsNone(self._bootstrap_as(EMPLOYEE_USER)["desk_url"])

	def _as(self, user):
		frappe.set_user(user)
		self.addCleanup(frappe.set_user, "Administrator")

	def _default_company(self):
		company = frappe.db.get_value("Employee", {"user_id": EMPLOYEE_USER}, "company")
		original = frappe.db.get_single_value("Global Defaults", "default_company")
		frappe.db.set_single_value("Global Defaults", "default_company", company)
		self.addCleanup(frappe.db.set_single_value, "Global Defaults", "default_company", original)
		return company

	def test_hr_without_an_employee_sees_the_default_companys_organisation(self):
		from helixhr.api import get_organisation_view

		company = self._default_company()
		self._as(HR_MANAGER_USER)
		view = get_organisation_view()
		self.assertEqual(view["company"], company)
		self.assertGreater(view["headcount"], 0)

	def test_hr_without_an_employee_can_search_the_directory(self):
		# The Projects member picker reads `get_directory`.
		from helixhr.api import get_directory

		company = self._default_company()
		self._as(HR_MANAGER_USER)
		page = get_directory()
		self.assertGreater(page["total"], 0)
		self.assertTrue(
			all(
				frappe.db.get_value("Employee", row["name"], "company") == company for row in page["people"]
			)
		)

	def test_an_unlinked_user_is_still_refused_the_directory(self):
		from helixhr.api import get_directory

		self._default_company()
		self._as(ORPHAN_USER)
		with self.assertRaises(frappe.PermissionError):
			get_directory()

	def test_hr_whose_employee_has_left_gets_no_default_company(self):
		"""The offboarding rule: an HR Manager whose Employee is not Active
		resolves to scope "none", never to the no-Employee fallback."""
		from helixhr.api import get_directory

		self._default_company()
		employee, user = make_test_hr_manager_employee()
		frappe.db.set_value("Employee", employee, "status", "Left")
		self.addCleanup(frappe.db.set_value, "Employee", employee, "status", "Active")
		self.addCleanup(frappe.db.set_value, "User", user, "enabled", 1)
		self._as(user)
		with self.assertRaises(frappe.PermissionError):
			get_directory()
