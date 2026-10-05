"""The shared email theme (`HelixHR Email Theme`): one logo, brand colour,
footer -- or sandboxed theme code -- around every portal, celebration and
holiday email.

The logo is sent as an inline (cid:) attachment through Frappe's own
`<img embed="...">` handling, so a mail client never has to reach the site;
the in-portal preview links it instead. Every theme endpoint is Portal Admin
or System Manager only.
"""

import struct
import zlib
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from helixhr.tests.test_hr_request import with_uploaded_file
from helixhr.tests.utils import (
	EMAIL_ADMIN_USER,
	EMPLOYEE_USER,
	HR_MANAGER_EMPLOYEE_USER,
	NOTIFICATION_MANAGER_USER,
	ensure_email_admin_user,
	ensure_notification_manager_user,
	ensure_test_company,
	make_test_hr_manager_employee,
	make_test_user,
	suspend_chart_of_account_fixtures,
)
from helixhr.utils import EMAIL_THEME

OTHER_COMPANY = "_Test Branding Co B"
OTHER_USER = "_test_branding_b@example.com"
THEME_FIELDS = ("logo", "brand_color", "footer_text", "use_custom_code", "theme_code")


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


class TestEmailTheme(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		cls.company = ensure_test_company()
		_other_company()
		make_test_hr_manager_employee()
		make_test_user(OTHER_USER, OTHER_COMPANY)
		ensure_email_admin_user()
		ensure_notification_manager_user()

	def setUp(self):
		frappe.set_user("Administrator")
		saved = frappe.db.get_singles_dict(EMAIL_THEME)
		self.snapshot = {field: saved.get(field) for field in THEME_FIELDS}
		for field in THEME_FIELDS:
			frappe.db.set_single_value(EMAIL_THEME, field, None)
		frappe.db.delete("HelixHR Message Template", {"template_key": "leave_submitted"})

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.local.request = None
		for field, value in self.snapshot.items():
			frappe.db.set_single_value(EMAIL_THEME, field, value)
		frappe.db.delete("HelixHR Message Template", {"template_key": "leave_submitted"})

	def _upload(self, file_name, content):
		from helixhr.api import upload_email_theme_logo

		frappe.local.request = with_uploaded_file(file_name, content)
		try:
			return upload_email_theme_logo()
		finally:
			frappe.local.request = None

	def _send_leave_submitted(self):
		from helixhr.utils import send_notification

		sent = []
		with patch("frappe.sendmail", side_effect=lambda **kwargs: sent.append(kwargs)):
			send_notification("leave_submitted", [OTHER_USER], {"employee_name": "Ada"})
		return sent[0]["message"]

	# --- the logo ------------------------------------------------------------

	def test_a_portal_admin_uploads_a_public_logo_and_removes_it(self):
		frappe.set_user(EMAIL_ADMIN_USER)
		result = self._upload("logo.png", _png())
		self.assertTrue(result["logo"].startswith("/files/"), result["logo"])
		self.assertEqual(frappe.db.get_single_value(EMAIL_THEME, "logo"), result["logo"])
		self.assertEqual(frappe.db.get_value("File", {"file_url": result["logo"]}, "is_private"), 0)
		self.assertIn(f'src="{result["logo"]}"', result["preview_html"], "the preview links the logo")

		from helixhr.api import upload_email_theme_logo

		self.assertEqual(upload_email_theme_logo(remove=1)["logo"], "")
		self.assertFalse(frappe.db.get_single_value(EMAIL_THEME, "logo"))

	def test_svg_mismatched_and_oversized_uploads_are_refused(self):
		frappe.set_user(EMAIL_ADMIN_USER)
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
		self.assertFalse(frappe.db.get_single_value(EMAIL_THEME, "logo"))

	def test_a_sent_notification_embeds_the_logo_as_an_inline_attachment(self):
		"""The fix for the unreachable `http://<site>/files/...` logo: the
		queued mail carries the image itself (Content-ID, `cid:`)."""
		frappe.set_user(EMAIL_ADMIN_USER)
		logo = self._upload("logo.png", _png())["logo"]
		frappe.set_user("Administrator")

		html = self._send_leave_submitted()
		self.assertIn(f'<img embed="{logo}"', html)
		self.assertNotIn(frappe.utils.get_url(logo), html.split("<h1")[0])

		frappe.sendmail(recipients=[OTHER_USER], subject="Theme embed probe", message=html)
		queued = frappe.get_last_doc("Email Queue")
		self.assertIn("Content-ID", queued.message)
		# Quoted-printable: `src=3D"cid:..."`.
		self.assertIn('"cid:', queued.message)
		self.assertNotIn("embed=", queued.message)

	def test_the_test_send_embeds_the_logo_too(self):
		frappe.set_user(EMAIL_ADMIN_USER)
		logo = self._upload("logo.png", _png())["logo"]
		from helixhr.api import send_email_theme_test

		sent = []
		with patch("frappe.sendmail", side_effect=lambda **kwargs: sent.append(kwargs)):
			result = send_email_theme_test(brand_color="#0B2545")
		self.assertEqual(result["sent_to"], EMAIL_ADMIN_USER)
		self.assertEqual(sent[0]["recipients"], [EMAIL_ADMIN_USER])
		self.assertIn(f'embed="{logo}"', sent[0]["message"])
		self.assertIn("background:#0b2545;color:#ffffff;", sent[0]["message"])

	def test_the_message_preview_links_the_logo_and_follows_the_unsaved_checkbox(self):
		from helixhr.api import preview_message_template
		from helixhr.utils import NOTIFICATION_EVENTS

		frappe.db.set_single_value(EMAIL_THEME, "logo", "/files/_theme_logo.png")
		frappe.set_user(EMAIL_ADMIN_USER)
		event = NOTIFICATION_EVENTS["leave_submitted"]
		shown = preview_message_template("leave_submitted", event["subject"], event["body"], hide_logo=0)
		hidden = preview_message_template("leave_submitted", event["subject"], event["body"], hide_logo=1)
		self.assertIn('<img src="/files/_theme_logo.png"', shown["html"])
		self.assertNotIn("embed=", shown["html"])
		self.assertNotIn("<img", hidden["html"])

	def test_no_logo_renders_the_company_name(self):
		html = self._send_leave_submitted()
		self.assertNotIn("<img", html)
		self.assertIn(f'<strong style="font-size:16px;color:#1f2328;">{OTHER_COMPANY}</strong>', html)

	def test_a_template_opted_out_of_the_logo_prints_the_company_name(self):
		from helixhr.api import get_notification_setup, reset_message_template, save_message_template

		frappe.db.set_single_value(EMAIL_THEME, "logo", "/files/_theme_logo.png")
		self.assertIn("_theme_logo.png", self._send_leave_submitted())

		frappe.set_user(EMAIL_ADMIN_USER)
		save_message_template("leave_submitted", hide_logo=1)
		html = self._send_leave_submitted()
		self.assertNotIn("<img", html)
		self.assertIn(f'<strong style="font-size:16px;color:#1f2328;">{OTHER_COMPANY}</strong>', html)
		event = next(e for e in get_notification_setup()["events"] if e["key"] == "leave_submitted")
		self.assertEqual(event["state"], "Default")
		self.assertTrue(event["hide_logo"])

		# Custom theme code: `{{ logo }}` prints the company name too.
		frappe.db.set_single_value(EMAIL_THEME, "use_custom_code", 1)
		frappe.db.set_single_value(EMAIL_THEME, "theme_code", "<div>{{ logo }}</div>{{ content }}")
		self.assertIn("<div><strong", self._send_leave_submitted())

		reset_message_template("leave_submitted")
		save_message_template("leave_submitted", hide_logo=0)
		self.assertIn('embed="/files/_theme_logo.png"', self._send_leave_submitted())

	def test_a_celebration_send_carries_the_theme_logo_with_no_template_edit(self):
		from helixhr.reminders import CELEBRATION_DEFAULTS, _context, _render_restricted

		frappe.db.set_single_value(EMAIL_THEME, "logo", "/files/_theme_logo.png")
		for body in (CELEBRATION_DEFAULTS["birthday"]["body"], "<p>Happy birthday, {{ names }}!</p>"):
			template = frappe._dict(
				use_html=1, subject=CELEBRATION_DEFAULTS["birthday"]["subject"], response_html=body
			)
			context = _context([{"name": "Ada Lovelace", "image": None}], OTHER_COMPANY, "birthday")
			with_logo = _render_restricted(template, context, None)["message"]
			without = _render_restricted(template, context, None, include_logo=False)["message"]
			self.assertEqual(with_logo.count("_theme_logo.png"), 1)
			self.assertIn('embed="/files/_theme_logo.png"', with_logo)
			self.assertNotIn("_theme_logo.png", without)

	# --- colour, footer, custom code -----------------------------------------

	def test_brand_color_accepts_hex_or_empty_and_refuses_anything_else(self):
		from helixhr.api import save_email_theme

		frappe.set_user(EMAIL_ADMIN_USER)
		self.assertEqual(save_email_theme(brand_color="#0B2545")["brand_color"], "#0B2545")
		self.assertEqual(frappe.db.get_single_value(EMAIL_THEME, "brand_color"), "#0b2545")
		for bad in ("0B2545", "#0B25", "#0B25456", "#GGGGGG", "red", "#000;background:url(x)", "#fff"):
			with self.subTest(bad=bad), self.assertRaises(frappe.ValidationError):
				save_email_theme(brand_color=bad)
		self.assertEqual(frappe.db.get_single_value(EMAIL_THEME, "brand_color"), "#0b2545")
		save_email_theme(brand_color="")
		self.assertFalse(frappe.db.get_single_value(EMAIL_THEME, "brand_color"))

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
		for raw in ("", None, "javascript:1", "#000;x"):
			self.assertEqual(email_header_colors(raw), {"header_bg": "#ffffff", "header_fg": "#1f2328"})

	def test_the_theme_paints_the_header_and_never_injects_a_raw_value(self):
		frappe.db.set_single_value(EMAIL_THEME, "brand_color", "#0b2545")
		html = self._send_leave_submitted()
		self.assertIn("background:#0b2545;color:#ffffff;", html)
		self.assertIn(f'<strong style="font-size:16px;color:#ffffff;">{OTHER_COMPANY}</strong>', html)

		# A value written past the endpoint is ignored at render.
		frappe.db.set_single_value(EMAIL_THEME, "brand_color", '#000;"><script>x</script>')
		html = self._send_leave_submitted()
		self.assertNotIn("<script>x", html)
		self.assertIn("background:#ffffff;color:#1f2328;", html)

	def test_the_footer_takes_company_and_portal_url_only(self):
		from helixhr.api import save_email_theme

		frappe.set_user(EMAIL_ADMIN_USER)
		save_email_theme(footer_text="Sent by {{ company }} via {{ portal_url }}")
		html = self._send_leave_submitted()
		self.assertIn(f"Sent by {OTHER_COMPANY} via {frappe.utils.get_url('/helixhr')}", html)
		self.assertNotIn("through the <a", html)
		for bad in ("{{ subject }}", "{{ frappe.session.user }}", "{% set x = 1 %}"):
			with self.subTest(bad=bad), self.assertRaises(frappe.ValidationError):
				save_email_theme(footer_text=bad)

	def test_theme_code_must_contain_content_and_stays_in_the_sandbox(self):
		from helixhr.api import save_email_theme

		frappe.set_user(EMAIL_ADMIN_USER)
		for bad in (
			"<div>{{ logo }}</div>",
			"{{ content }}{{ frappe.session.user }}",
			"{{ content }}{{ company.__class__ }}",
			"{{ content }}{{ lipsum() }}",
			"{{ content }}{% include 'x.html' %}",
			"{{ content }}{{ recipient_first_name }}",
			"{{ content }",
		):
			with self.subTest(bad=bad), self.assertRaises(frappe.ValidationError):
				save_email_theme(use_custom_code=1, theme_code=bad)
		self.assertFalse(frappe.db.get_single_value(EMAIL_THEME, "use_custom_code"))
		with self.assertRaises(frappe.ValidationError):
			save_email_theme(use_custom_code=1, theme_code="")

	def test_valid_theme_code_wraps_every_email(self):
		from helixhr.api import save_email_theme

		frappe.set_user(EMAIL_ADMIN_USER)
		code = (
			'<table data-theme="custom" style="border-top:4px solid {{ brand_color }}">'
			"<tr><td>{{ logo }} {{ company }}: {{ subject }}</td></tr>"
			"<tr><td>{{ content }}</td></tr><tr><td>{{ portal_url }}</td></tr></table>"
		)
		result = save_email_theme(brand_color="#6D1A36", use_custom_code=1, theme_code=code)
		self.assertIn('data-theme="custom"', result["preview_html"])
		html = self._send_leave_submitted()
		self.assertIn('data-theme="custom" style="border-top:4px solid #6d1a36"', html)
		self.assertIn(OTHER_COMPANY, html)
		self.assertNotIn("background:#f4f5f7", html, "the default theme is not used")

	def test_theme_code_that_fails_at_send_falls_back_to_the_default_and_logs(self):
		# Written past validation (the code was valid when saved, say).
		frappe.db.set_single_value(EMAIL_THEME, "use_custom_code", 1)
		frappe.db.set_single_value(EMAIL_THEME, "theme_code", "{{ content }}{{ company.missing }}")
		before = frappe.db.count("Error Log", {"method": "HelixHR email theme failed"})
		html = self._send_leave_submitted()
		self.assertIn("background:#f4f5f7", html, "the default theme went out")
		self.assertIn("Ada", html)
		self.assertEqual(frappe.db.count("Error Log", {"method": "HelixHR email theme failed"}), before + 1)

	def test_reset_returns_to_the_default_and_keeps_the_logo(self):
		from helixhr.api import reset_email_theme, save_email_theme

		frappe.db.set_single_value(EMAIL_THEME, "logo", "/files/_theme_logo.png")
		frappe.set_user(EMAIL_ADMIN_USER)
		save_email_theme(brand_color="#0B2545", footer_text="Hi", use_custom_code=1, theme_code="{{ content }}")
		result = reset_email_theme()
		self.assertEqual(
			{key: result[key] for key in THEME_FIELDS},
			{"logo": "/files/_theme_logo.png", "brand_color": "", "footer_text": "", "use_custom_code": 0, "theme_code": ""},
		)

	# --- permissions ---------------------------------------------------------

	def test_every_theme_endpoint_refuses_hr_the_notification_manager_and_an_employee(self):
		from helixhr import api

		calls = (
			lambda: api.get_email_theme(),
			lambda: api.save_email_theme(brand_color="#0B2545"),
			lambda: api.preview_email_theme(brand_color="#0B2545"),
			lambda: api.reset_email_theme(),
			lambda: api.upload_email_theme_logo(remove=1),
			lambda: api.send_email_theme_test(),
		)
		for user in (HR_MANAGER_EMPLOYEE_USER, NOTIFICATION_MANAGER_USER, EMPLOYEE_USER):
			frappe.set_user(user)
			for call in calls:
				with self.subTest(user=user), self.assertRaises(frappe.PermissionError):
					call()
		frappe.set_user("Administrator")
		self.assertFalse(frappe.db.get_single_value(EMAIL_THEME, "brand_color"))

	def test_the_portal_admin_holds_no_docperm_on_the_theme(self):
		frappe.set_user(EMAIL_ADMIN_USER)
		self.assertFalse(frappe.has_permission(EMAIL_THEME, "write"))
		from helixhr.api import get_email_theme

		self.assertIn("preview_html", get_email_theme())


class TestMigrateEmailThemePatch(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		saved = frappe.db.get_singles_dict(EMAIL_THEME)
		self.snapshot = {field: saved.get(field) for field in THEME_FIELDS}
		for field in THEME_FIELDS:
			frappe.db.set_single_value(EMAIL_THEME, field, None)
		self.company = ensure_test_company()
		self.logo = frappe.db.get_value("Company", self.company, "company_logo")

	def tearDown(self):
		for field, value in self.snapshot.items():
			frappe.db.set_single_value(EMAIL_THEME, field, value)
		frappe.db.set_value("Company", self.company, "company_logo", self.logo)

	def _run(self, colors):
		from helixhr.patches.v1_0 import migrate_email_theme

		real_get_all = frappe.get_all

		def get_all(doctype, *args, **kwargs):
			if doctype == "Company" and kwargs.get("pluck") == migrate_email_theme.FIELD:
				return colors
			return real_get_all(doctype, *args, **kwargs)

		with (
			patch.object(frappe.db, "has_column", return_value=True),
			patch("frappe.get_all", side_effect=get_all),
			patch("frappe.defaults.get_global_default", return_value=self.company),
		):
			migrate_email_theme.execute()

	def test_one_distinct_colour_and_the_default_logo_carry_over_once(self):
		frappe.db.set_value("Company", self.company, "company_logo", "/files/_old_company_logo.png")
		self._run(["#0B2545", "", None, "#0b2545", "not-a-colour"])
		self.assertEqual(frappe.db.get_single_value(EMAIL_THEME, "brand_color"), "#0b2545")
		self.assertEqual(frappe.db.get_single_value(EMAIL_THEME, "logo"), "/files/_old_company_logo.png")
		self.assertFalse(frappe.db.exists("Custom Field", "Company-helixhr_email_header_color"))

		# Idempotent, and never overwrites what the theme already holds.
		frappe.db.set_value("Company", self.company, "company_logo", "/files/_newer.png")
		self._run(["#6d1a36"])
		self.assertEqual(frappe.db.get_single_value(EMAIL_THEME, "brand_color"), "#0b2545")
		self.assertEqual(frappe.db.get_single_value(EMAIL_THEME, "logo"), "/files/_old_company_logo.png")

	def test_several_distinct_colours_or_a_private_logo_are_not_guessed(self):
		frappe.db.set_value("Company", self.company, "company_logo", "/private/files/_secret.png")
		self._run(["#0b2545", "#6d1a36"])
		self.assertFalse(frappe.db.get_single_value(EMAIL_THEME, "brand_color"))
		self.assertFalse(frappe.db.get_single_value(EMAIL_THEME, "logo"))
