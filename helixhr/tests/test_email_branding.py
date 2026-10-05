"""Plan 2026-10-05-001 U12: one company logo brands every email.

`set_company_logo` is the upload (PNG / JPEG / WebP by signature, 2 MB,
public File, caller's in-scope company only); `send_notification` brands
each mail with the recipient's own company; celebration mail gets the logo
through the layout wrap with no template edit.
"""

import struct
import zlib
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from helixhr.tests.test_hr_request import with_uploaded_file
from helixhr.tests.utils import (
	EMPLOYEE_USER,
	HR_MANAGER_EMPLOYEE_USER,
	ensure_test_company,
	make_test_hr_manager_employee,
	make_test_user,
	suspend_chart_of_account_fixtures,
)

OTHER_COMPANY = "_Test Branding Co B"
OTHER_USER = "_test_branding_b@example.com"


def _png():
	"""A valid 1x1 PNG, built by hand so the test needs no image library."""

	def chunk(kind, data):
		return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

	header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
	return (
		b"\x89PNG\r\n\x1a\n"
		+ chunk(b"IHDR", header)
		+ chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
		+ chunk(b"IEND", b"")
	)


def _other_company():
	if not frappe.db.exists("Company", OTHER_COMPANY):
		with suspend_chart_of_account_fixtures():
			frappe.get_doc(
				{
					"doctype": "Company",
					"company_name": OTHER_COMPANY,
					"abbr": "TBCB",
					"default_currency": "USD",
					"country": "United States",
				}
			).insert(ignore_permissions=True)
	return OTHER_COMPANY


class TestEmailBranding(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		cls.company = ensure_test_company()
		_other_company()
		make_test_hr_manager_employee()
		make_test_user(OTHER_USER, OTHER_COMPANY)
		cls.hr_company = frappe.db.get_value("Employee", {"user_id": HR_MANAGER_EMPLOYEE_USER}, "company")

	def setUp(self):
		frappe.set_user("Administrator")
		self.logos = {
			name: frappe.db.get_value("Company", name, "company_logo")
			for name in (self.company, OTHER_COMPANY, self.hr_company)
		}

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.local.request = None
		for name, logo in self.logos.items():
			frappe.db.set_value("Company", name, "company_logo", logo)

	def _upload(self, file_name, content, company=None):
		from helixhr.api import set_company_logo

		frappe.local.request = with_uploaded_file(file_name, content)
		try:
			return set_company_logo(company=company)
		finally:
			frappe.local.request = None

	def test_the_upload_sets_the_callers_company_logo_as_a_public_file(self):
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		result = self._upload("logo.png", _png())

		self.assertTrue(result["logo_url"].startswith("/files/"), result["logo_url"])
		self.assertEqual(frappe.db.get_value("Company", self.hr_company, "company_logo"), result["logo_url"])
		self.assertEqual(
			frappe.db.get_value("Company", OTHER_COMPANY, "company_logo"), self.logos[OTHER_COMPANY]
		)
		self.assertEqual(frappe.db.get_value("File", {"file_url": result["logo_url"]}, "is_private"), 0)

	def test_svg_mismatched_and_oversized_uploads_are_refused(self):
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
		for file_name, content in (
			("logo.svg", svg),
			("logo.png", svg),
			("logo.jpg", _png()),
			("logo.webp", b"RIFF\x00\x00\x00\x00WAVEfmt "),
			("logo.png", _png() + b"\x00" * (2 * 1024 * 1024)),
		):
			with self.subTest(file_name=file_name), self.assertRaises(frappe.ValidationError):
				self._upload(file_name, content)
		self.assertEqual(
			frappe.db.get_value("Company", self.hr_company, "company_logo"), self.logos[self.hr_company]
		)

	def test_another_companys_hr_manager_and_an_employee_are_refused(self):
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		with self.assertRaises(frappe.PermissionError):
			self._upload("logo.png", _png(), company=OTHER_COMPANY)
		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.PermissionError):
			self._upload("logo.png", _png())

	def test_remove_clears_the_logo(self):
		from helixhr.api import set_company_logo

		frappe.db.set_value("Company", self.hr_company, "company_logo", "/files/_old_logo.png")
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		self.assertEqual(set_company_logo(remove=1)["logo_url"], "")
		self.assertFalse(frappe.db.get_value("Company", self.hr_company, "company_logo"))

	def test_a_notification_uses_the_recipients_company_logo(self):
		from helixhr.utils import send_notification

		frappe.db.set_value("Company", OTHER_COMPANY, "company_logo", "/files/_branding_b_logo.png")
		frappe.db.set_value("Company", self.company, "company_logo", "/files/_branding_a_logo.png")
		sent = []
		with (
			patch("frappe.defaults.get_global_default", return_value=self.company),
			patch("frappe.sendmail", side_effect=lambda **kwargs: sent.append(kwargs)),
		):
			send_notification("leave_submitted", [OTHER_USER], {"employee_name": "Ada"})

		self.assertEqual(len(sent), 1)
		self.assertIn("_branding_b_logo.png", sent[0]["message"])
		self.assertNotIn("_branding_a_logo.png", sent[0]["message"])

	def test_message_template_preview_carries_the_real_company_logo(self):
		"""The Email templates preview renders through helixhr_layout.html with
		the company `send_notification` would use -- not the sample's
		placeholder logo address -- and hands the editor its logo control."""
		from helixhr.api import preview_message_template

		frappe.db.set_value("Company", self.company, "company_logo", "/files/_branding_a_logo.png")
		with patch("frappe.defaults.get_global_default", return_value=self.company):
			result = preview_message_template("leave_submitted", "Leave", "<p>Hi</p>")

		self.assertEqual(result["company"], self.company)
		self.assertEqual(result["logo_url"], frappe.utils.get_url("/files/_branding_a_logo.png"))
		self.assertIn(f'<img src="{result["logo_url"]}"', result["html"])
		self.assertNotIn("hr.example.com/files/logo.png", result["html"])
		self.assertTrue(result["can_set_logo"])

	def test_no_logo_renders_no_image(self):
		from helixhr.utils import send_notification

		frappe.db.set_value("Company", OTHER_COMPANY, "company_logo", None)
		sent = []
		with patch("frappe.sendmail", side_effect=lambda **kwargs: sent.append(kwargs)):
			send_notification("leave_submitted", [OTHER_USER], {"employee_name": "Ada"})
		self.assertNotIn("<img", sent[0]["message"])
		self.assertIn(OTHER_COMPANY, sent[0]["message"])

	def test_a_celebration_send_carries_the_logo_with_no_template_edit(self):
		from helixhr.reminders import CELEBRATION_DEFAULTS, _context, _render_restricted

		frappe.db.set_value("Company", OTHER_COMPANY, "company_logo", "/files/_branding_b_logo.png")
		template = frappe._dict(
			use_html=1,
			subject=CELEBRATION_DEFAULTS["birthday"]["subject"],
			response_html=CELEBRATION_DEFAULTS["birthday"]["body"],
		)
		context = _context([{"name": "Ada Lovelace", "image": None}], OTHER_COMPANY, "birthday")
		message = _render_restricted(template, context, None)["message"]
		self.assertEqual(message.count("_branding_b_logo.png"), 1)
