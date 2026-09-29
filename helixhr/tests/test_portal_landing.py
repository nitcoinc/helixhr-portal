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
	"""Where a user lands after signing in: /helixhr, for every signed-in
	user (2026-09-29). Desk roles reach Desk from the shell's Open Desk
	button; the portal decides what each caller sees once they are there.

	Registered as `get_website_user_home_page`, which Frappe consults before
	`role_home_page` and before Website Settings."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_test_employee_and_manager()
		make_test_user_without_employee()

	def test_an_employee_lands_on_the_portal(self):
		self.assertEqual(portal_home_page(EMPLOYEE_USER), PORTAL_HOME_PAGE)

	def test_a_manager_lands_on_the_portal(self):
		self.assertEqual(portal_home_page(MANAGER_USER), PORTAL_HOME_PAGE)

	def test_an_hr_manager_with_an_employee_record_lands_on_the_portal(self):
		employee, user = make_test_hr_manager_employee()
		self.assertTrue(employee)
		self.assertEqual(portal_home_page(user), PORTAL_HOME_PAGE)

	def test_an_it_team_member_is_a_website_user_and_lands_on_the_portal(self):
		from helixhr.tests.utils import make_test_it_user

		employee, user = make_test_it_user()
		self.assertTrue(employee)
		self.assertEqual(frappe.db.get_value("User", user, "user_type"), "Website User")
		self.assertEqual(portal_home_page(user), PORTAL_HOME_PAGE)

	def test_desk_roles_land_on_the_portal_too(self):
		"""Inverts the old rule, which kept HR User, System Manager and
		Administrator on Desk: one landing page for everyone, Desk one click
		away. An HR Manager with no Employee record gets the desk-only portal
		there (TestDeskOnlyPortal)."""
		self.assertEqual(portal_home_page(ensure_hr_manager_user()), PORTAL_HOME_PAGE)
		self.assertEqual(portal_home_page("Administrator"), PORTAL_HOME_PAGE)

		user = frappe.get_doc("User", EMPLOYEE_USER)
		user.append_roles("HR User", "System Manager")
		user.save(ignore_permissions=True)
		self.addCleanup(self._drop_roles, EMPLOYEE_USER, {"HR User", "System Manager"})
		self.assertEqual(portal_home_page(EMPLOYEE_USER), PORTAL_HOME_PAGE)

	def _drop_roles(self, user, roles):
		doc = frappe.get_doc("User", user)
		doc.set("roles", [row for row in doc.roles if row.role not in roles])
		doc.save(ignore_permissions=True)

	def test_a_user_with_no_employee_record_lands_on_the_portal(self):
		"""Where the portal shows them "not set up" and the HR contact --
		the one page that tells them what to do."""
		self.assertEqual(portal_home_page(ORPHAN_USER), PORTAL_HOME_PAGE)

	def test_guest_has_no_landing(self):
		self.assertIsNone(portal_home_page("Guest"))

	def test_the_hook_is_registered(self):
		"""Without this the whole feature is dead code: Frappe only calls it
		because hooks.py names it."""
		self.assertEqual(
			frappe.get_hooks("get_website_user_home_page"),
			["helixhr.utils.portal_home_page"],
		)


class TestMicrosoftLoginLanding(IntegrationTestCase):
	"""Frappe's OAuth callback lands a System User on `get_default_path()`
	(the User's Default App, `/desk/people`, `/apps`) without ever asking
	`portal_home_page`, so `helixhr.api.login_via_office365` corrects the
	redirect afterwards -- unless the login was asked to go somewhere."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_test_employee_and_manager()
		ensure_hr_manager_user()

	def _sign_in(self, user, frappe_location, requested="", response_type="redirect"):
		"""Run the override with Frappe's own callback stubbed to what it
		would do: sign `user` in and answer with `frappe_location`."""
		from unittest.mock import patch

		from frappe.utils.oauth import create_oauth_state

		from helixhr.api import login_via_office365

		state = create_oauth_state(requested)
		saved = dict(frappe.local.response)
		self.addCleanup(lambda: (frappe.local.response.clear(), frappe.local.response.update(saved)))
		self.addCleanup(frappe.set_user, "Administrator")

		def frappe_callback(code, state):
			from frappe.utils.oauth import consume_oauth_state

			consume_oauth_state(state)
			frappe.set_user(user)
			frappe.local.response["type"] = response_type
			frappe.local.response["location"] = frappe_location

		with patch("frappe.integrations.oauth2_logins.login_via_office365", side_effect=frappe_callback):
			login_via_office365("code", state)
		return frappe.local.response["location"]

	def test_frappes_default_landing_becomes_the_portal(self):
		for user in (EMPLOYEE_USER, HR_MANAGER_USER):
			for location in ("https://site.example/desk/people", "/apps", "/desk", "/"):
				with self.subTest(user=user, location=location):
					self.assertTrue(self._sign_in(user, location).endswith(f"/{PORTAL_HOME_PAGE}"))

	def test_a_requested_destination_is_honoured(self):
		# A Desk record followed from an email, or a portal deep link.
		for requested in ("https://site.example/desk/leave-application/X", "https://site.example/helixhr/leave/X"):
			with self.subTest(requested=requested):
				self.assertEqual(self._sign_in(HR_MANAGER_USER, requested, requested=requested), requested)

	def test_an_error_page_is_not_turned_into_a_redirect(self):
		# Frappe answers a refused login (signup disabled, expired state)
		# with a web page, not a redirect; that must reach the user as is.
		self.assertEqual(self._sign_in(EMPLOYEE_USER, "/desk", response_type="page"), "/desk")

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
