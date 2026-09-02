import frappe
from frappe.tests import IntegrationTestCase
from werkzeug.test import Client


class TestHelixHRInstall(IntegrationTestCase):
	"""
	U2 verification: the app installs cleanly and /helixhr serves the built
	shell. Uses a real WSGI request (via frappe.app.application) rather than
	calling helixhr.www.helixhr.get_context() directly, because CSRF token
	generation needs frappe.local.session_obj, which only exists once
	init_request() has run -- a bare function call or `bench execute` does
	not set that up. See docs/plans/.../U2 for the same finding.

	The Guest-redirect-to-login scenario named in the plan for this unit is
	client-side (Vue router guard, built in U3) and is not observable from a
	raw HTTP request -- it is covered by a Playwright spec in U3 instead.
	"""

	def test_page_route_serves_built_shell(self):
		from frappe.app import application

		client = Client(application)
		response = client.get("/helixhr", headers={"Host": frappe.local.site})

		self.assertEqual(response.status_code, 200)
		body = response.get_data(as_text=True)
		self.assertIn('id="app"', body)
		self.assertIn("window.csrf_token", body)
		self.assertIn("/assets/helixhr/helixhr/assets/", body)

	def test_website_route_rule_maps_subpaths_to_the_spa(self):
		from frappe.app import application

		client = Client(application)
		# Any /helixhr/<path> should hit the same www page (helixhr/hooks.py
		# website_route_rules), so client-side Vue Router can own subroutes.
		response = client.get("/helixhr/leave", headers={"Host": frappe.local.site})

		self.assertEqual(response.status_code, 200)
		self.assertIn('id="app"', response.get_data(as_text=True))
