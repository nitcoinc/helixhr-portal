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
		self.header_colors = {
			name: frappe.db.get_value("Company", name, "helixhr_email_header_color")
			for name in (self.company, OTHER_COMPANY, self.hr_company)
		}

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.local.request = None
		for name, logo in self.logos.items():
			frappe.db.set_value("Company", name, "company_logo", logo)
		for name, color in self.header_colors.items():
			frappe.db.set_value("Company", name, "helixhr_email_header_color", color)

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

	# --- the per-template "Include company logo" opt-out ----------------------

	def _send_leave_submitted(self):
		from helixhr.utils import send_notification

		sent = []
		with patch("frappe.sendmail", side_effect=lambda **kwargs: sent.append(kwargs)):
			send_notification("leave_submitted", [OTHER_USER], {"employee_name": "Ada"})
		return sent[0]["message"]

	def test_a_message_template_opted_out_of_the_logo_prints_the_company_name(self):
		from helixhr.api import get_notification_setup, reset_message_template, save_message_template

		frappe.db.set_value("Company", OTHER_COMPANY, "company_logo", "/files/_branding_b_logo.png")
		frappe.db.delete("HelixHR Message Template", {"template_key": "leave_submitted"})
		self.assertIn("_branding_b_logo.png", self._send_leave_submitted())

		# Opting out on the default wording keeps a wording-less row: still Default.
		save_message_template("leave_submitted", hide_logo=1)
		html = self._send_leave_submitted()
		self.assertNotIn("<img", html)
		self.assertIn(f'<strong style="font-size:16px;color:#1f2328;">{OTHER_COMPANY}</strong>', html)
		event = next(e for e in get_notification_setup()["events"] if e["key"] == "leave_submitted")
		self.assertEqual(event["state"], "Default")
		self.assertTrue(event["hide_logo"])

		# Reset to default wording keeps the opt-out; ticking it back restores the logo.
		reset_message_template("leave_submitted")
		self.assertTrue(frappe.db.get_value("HelixHR Message Template", "leave_submitted", "hide_logo"))
		save_message_template("leave_submitted", hide_logo=0)
		self.assertIn("_branding_b_logo.png", self._send_leave_submitted())
		frappe.db.delete("HelixHR Message Template", {"template_key": "leave_submitted"})

	def test_the_message_preview_follows_the_unsaved_checkbox(self):
		from helixhr.api import preview_message_template
		from helixhr.utils import NOTIFICATION_EVENTS

		frappe.db.set_value("Company", self.company, "company_logo", "/files/_branding_a_logo.png")
		event = NOTIFICATION_EVENTS["leave_submitted"]
		with patch("frappe.defaults.get_global_default", return_value=self.company):
			shown = preview_message_template("leave_submitted", event["subject"], event["body"], hide_logo=0)
			hidden = preview_message_template("leave_submitted", event["subject"], event["body"], hide_logo=1)
		self.assertIn("<img", shown["html"])
		self.assertNotIn("<img", hidden["html"])

	def test_a_celebration_opted_out_of_the_logo_prints_the_company_name(self):
		from helixhr.reminders import CELEBRATION_DEFAULTS, _context, _render_restricted

		frappe.db.set_value("Company", OTHER_COMPANY, "company_logo", "/files/_branding_b_logo.png")
		for body in (CELEBRATION_DEFAULTS["birthday"]["body"], "<p>Happy birthday, {{ names }}!</p>"):
			template = frappe._dict(
				use_html=1, subject=CELEBRATION_DEFAULTS["birthday"]["subject"], response_html=body
			)
			context = _context([{"name": "Ada Lovelace", "image": None}], OTHER_COMPANY, "birthday")
			with_logo = _render_restricted(template, context, None)["message"]
			without = _render_restricted(template, context, None, include_logo=False)["message"]
			self.assertIn("_branding_b_logo.png", with_logo)
			self.assertNotIn("_branding_b_logo.png", without)
			self.assertNotIn("<img src=", without.split("Ada Lovelace")[0])

	# --- the per-company email header colour ----------------------------------

	def test_header_color_accepts_hex_or_empty_and_refuses_anything_else(self):
		from helixhr.api import set_email_header_color

		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		self.assertEqual(set_email_header_color(color="#0B2545")["header_color"], "#0b2545")
		self.assertEqual(
			frappe.db.get_value("Company", self.hr_company, "helixhr_email_header_color"), "#0b2545"
		)
		for bad in ("0B2545", "#0B25", "#0B25456", "#GGGGGG", "red", "#000;background:url(x)", "#fff"):
			with self.subTest(bad=bad), self.assertRaises(frappe.ValidationError):
				set_email_header_color(color=bad)
		self.assertEqual(
			frappe.db.get_value("Company", self.hr_company, "helixhr_email_header_color"), "#0b2545"
		)
		self.assertEqual(set_email_header_color(color="")["header_color"], "#ffffff")
		self.assertFalse(frappe.db.get_value("Company", self.hr_company, "helixhr_email_header_color"))

	def test_header_color_for_another_company_or_by_an_employee_is_refused(self):
		from helixhr.api import set_email_header_color

		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		with self.assertRaises(frappe.PermissionError):
			set_email_header_color(company=OTHER_COMPANY, color="#0B2545")
		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.PermissionError):
			set_email_header_color(color="#0B2545")
		frappe.set_user("Administrator")
		self.assertEqual(
			frappe.db.get_value("Company", OTHER_COMPANY, "helixhr_email_header_color"),
			self.header_colors[OTHER_COMPANY],
		)

	def test_foreground_follows_wcag_luminance(self):
		from helixhr.utils import email_header_colors

		for bg, fg in (
			("#FFFFFF", "#1f2328"),
			("#0B2545", "#ffffff"),
			("#2B2D33", "#ffffff"),
			("#0F4C5C", "#ffffff"),
			("#6D1A36", "#ffffff"),
			("#3E4C59", "#ffffff"),
			("#F5D76E", "#1f2328"),
			("#999999", "#1f2328"),
		):
			with self.subTest(bg=bg):
				self.assertEqual(email_header_colors(bg)["header_fg"], fg)
		# Empty or invalid falls back to white with dark text.
		for raw in ("", None, "javascript:1", "#000;x"):
			self.assertEqual(email_header_colors(raw), {"header_bg": "#ffffff", "header_fg": "#1f2328"})

	def test_layout_paints_the_header_and_never_injects_a_raw_value(self):
		frappe.db.set_value("Company", OTHER_COMPANY, "company_logo", None)
		frappe.db.set_value("Company", OTHER_COMPANY, "helixhr_email_header_color", "#0b2545")
		html = self._send_leave_submitted()
		self.assertIn("background:#0b2545;color:#ffffff;", html)
		self.assertIn(f'<strong style="font-size:16px;color:#ffffff;">{OTHER_COMPANY}</strong>', html)

		# A value written past the endpoint is ignored at render.
		frappe.db.set_value(
			"Company", OTHER_COMPANY, "helixhr_email_header_color", '#000;"><script>x</script>'
		)
		html = self._send_leave_submitted()
		self.assertNotIn("<script>x", html)
		self.assertIn("background:#ffffff;color:#1f2328;", html)

	def test_a_celebration_carries_the_header_color(self):
		from helixhr.reminders import _context, _render_restricted

		frappe.db.set_value("Company", OTHER_COMPANY, "helixhr_email_header_color", "#6d1a36")
		template = frappe._dict(use_html=1, subject="Hi", response_html="<p>Happy birthday, {{ names }}!</p>")
		context = _context([{"name": "Ada Lovelace", "image": None}], OTHER_COMPANY, "birthday")
		message = _render_restricted(template, context, None, include_logo=False)["message"]
		self.assertIn("background:#6d1a36;color:#ffffff;", message)
		self.assertIn(f'color:#ffffff;">{OTHER_COMPANY}</strong>', message)
