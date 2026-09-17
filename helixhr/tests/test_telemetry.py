from unittest.mock import patch

import frappe
from frappe.installer import update_site_config
from frappe.tests import IntegrationTestCase

from helixhr import telemetry


class TestTelemetry(IntegrationTestCase):
	def tearDown(self):
		for key in (telemetry.ENABLED_KEY, telemetry.URL_KEY, telemetry.INSTALL_ID_KEY):
			if key in frappe.conf:
				update_site_config(key, None)

	def test_no_ping_when_unconfigured(self):
		update_site_config(telemetry.ENABLED_KEY, None)
		update_site_config(telemetry.URL_KEY, None)
		with patch("requests.post") as post:
			telemetry.send_ping()
		post.assert_not_called()

	def test_no_ping_when_enabled_but_no_url(self):
		update_site_config(telemetry.ENABLED_KEY, True)
		update_site_config(telemetry.URL_KEY, None)
		with patch("requests.post") as post:
			telemetry.send_ping()
		post.assert_not_called()

	def test_no_ping_when_url_set_but_not_enabled(self):
		update_site_config(telemetry.ENABLED_KEY, False)
		update_site_config(telemetry.URL_KEY, "https://example.invalid/ping")
		with patch("requests.post") as post:
			telemetry.send_ping()
		post.assert_not_called()

	def test_the_ping_carries_exactly_an_install_id_and_two_versions(self):
		update_site_config(telemetry.ENABLED_KEY, True)
		update_site_config(telemetry.URL_KEY, "https://example.invalid/ping")

		with patch("requests.post") as post:
			telemetry.send_ping()

		post.assert_called_once()
		_, kwargs = post.call_args
		self.assertEqual(kwargs["json"].keys(), {"install_id", "app_version", "frappe_version"})
		self.assertIsInstance(kwargs["json"]["install_id"], str)
		self.assertTrue(kwargs["json"]["install_id"])

	def test_the_install_id_is_generated_once_and_reused(self):
		update_site_config(telemetry.ENABLED_KEY, True)
		update_site_config(telemetry.URL_KEY, "https://example.invalid/ping")

		with patch("requests.post"):
			telemetry.send_ping()
		first_id = frappe.conf.get(telemetry.INSTALL_ID_KEY)

		with patch("requests.post"):
			telemetry.send_ping()
		second_id = frappe.conf.get(telemetry.INSTALL_ID_KEY)

		self.assertEqual(first_id, second_id)

	def test_a_failed_request_is_swallowed_not_raised(self):
		update_site_config(telemetry.ENABLED_KEY, True)
		update_site_config(telemetry.URL_KEY, "https://example.invalid/ping")

		with patch("requests.post", side_effect=Exception("network down")):
			telemetry.send_ping()  # must not raise

	def test_no_employee_company_or_user_data_ever_appears_in_the_payload(self):
		update_site_config(telemetry.ENABLED_KEY, True)
		update_site_config(telemetry.URL_KEY, "https://example.invalid/ping")

		with patch("requests.post") as post:
			telemetry.send_ping()

		rendered = frappe.as_json(post.call_args.kwargs["json"])
		self.assertNotIn("employee", rendered.lower())
		self.assertNotIn("company", rendered.lower())
		self.assertNotIn(frappe.session.user, rendered)
