"""P2-U9: the hardening controls, tested as behaviour rather than as
settings.

Three things live here because they are one decision each and none of them
belongs to a single flow:

  * the portal upload policy (P2-U9 step 5) -- what an employee may attach,
    judged by content and not only by name;
  * the per-user write limits (step 6) -- proved with the limiter forced on,
    because the suites run with it off;
  * the response headers and download disposition (steps 5 and 8).
"""

import base64
import io
import uuid
import zipfile
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from werkzeug.wrappers import Response

from helixhr import utils
from helixhr.tests.test_hr_request import SAFE_PDF_BASE64, with_uploaded_file
from helixhr.tests.utils import EMPLOYEE_USER, make_test_employee_and_manager

# Real files, not just the right first bytes: Frappe's own File controller
# parses images through Pillow and PDFs through pypdf, so a fixture that only
# satisfies this app's signature check would fail one layer later for an
# unrelated reason and prove nothing about the policy.
PDF = base64.b64decode(SAFE_PDF_BASE64)
PNG = base64.b64decode(
	"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC"
)
JPEG = base64.b64decode(
	"/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD3+iiigD//2Q=="
)
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
HTML = b"<!doctype html><script>alert(1)</script>"


def _ooxml(main_part, extra=None):
	"""A minimal but structurally real OOXML container."""
	buffer = io.BytesIO()
	with zipfile.ZipFile(buffer, "w") as archive:
		archive.writestr("[Content_Types].xml", "<Types/>")
		archive.writestr(main_part, "<document/>")
		for name, content in (extra or {}).items():
			archive.writestr(name, content)
	return buffer.getvalue()


DOCX = _ooxml("word/document.xml")
XLSX = _ooxml("xl/workbook.xml")
# A .docm renamed to .docx: same container, plus the macro project.
MACRO_DOCX = _ooxml("word/document.xml", {"word/vbaProject.bin": "MACRO"})
# Starts with PK, is not a zip.
MALFORMED_DOCX = b"PK\x03\x04" + b"not really a zip"


class TestPortalUploadPolicy(IntegrationTestCase):
	"""P2-U9 scenario 7. One safe file of each allowed type is stored
	privately; every named unsafe shape is refused and leaves nothing
	behind."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.employee_name, _, _, _ = make_test_employee_and_manager()
		frappe.set_user(EMPLOYEE_USER)
		self.request = self._create_request()

	def tearDown(self):
		frappe.local.request = None
		frappe.set_user("Administrator")

	def _create_request(self):
		from helixhr.api import create_my_request

		return create_my_request(
			category="HR Letter", subject="Upload policy", operation_key=str(uuid.uuid4())
		)["name"]

	def _attach(self, file_name, content):
		from helixhr.api import attach_to_my_request

		frappe.local.request = with_uploaded_file(file_name, content)
		return attach_to_my_request(self.request)

	def _attachment_count(self):
		return frappe.db.count(
			"File", {"attached_to_doctype": "HR Request", "attached_to_name": self.request}
		)

	def test_one_safe_file_of_each_allowed_type_is_stored_privately(self):
		for file_name, content in (
			("payslip.pdf", PDF),
			("badge.png", PNG),
			("scan.jpg", JPEG),
			("form.docx", DOCX),
			("claim.xlsx", XLSX),
		):
			with self.subTest(file_name):
				attached = self._attach(file_name, content)
				self.assertTrue(attached["created"], file_name)
				self.assertEqual(
					frappe.db.get_value("File", {"file_url": attached["file_url"]}, "is_private"),
					1,
					file_name,
				)
				self.assertTrue(attached["file_url"].startswith("/private/files/"), file_name)
		self.assertEqual(self._attachment_count(), 5)

	def test_every_unsafe_shape_is_refused_and_nothing_is_stored(self):
		cases = {
			"oversized": ("big.pdf", PDF + b"x" * utils.UPLOAD_MAX_BYTES),
			"svg": ("logo.svg", SVG),
			"html": ("page.html", HTML),
			"scriptable": ("payload.exe", b"MZ\x90\x00"),
			"legacy word": ("old.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
			"legacy excel": ("old.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
			"macro-enabled by name": ("macros.docm", MACRO_DOCX),
			"macro-enabled renamed": ("macros.docx", MACRO_DOCX),
			"malformed ooxml": ("broken.docx", MALFORMED_DOCX),
			"wrong ooxml part": ("wrong.xlsx", DOCX),
			"svg renamed to png": ("logo.png", SVG),
			"html renamed to pdf": ("page.pdf", HTML),
			"empty": ("empty.pdf", b""),
			"no extension": ("README", PDF),
			"double extension": ("report.pdf.svg", SVG),
		}
		for label, (file_name, content) in cases.items():
			with self.subTest(label):
				with self.assertRaises(frappe.ValidationError, msg=label):
					self._attach(file_name, content)
		self.assertEqual(self._attachment_count(), 0)

	def test_a_direct_file_insert_gets_the_same_answer_as_the_portal_method(self):
		"""The policy is not only in `attach_to_my_request`: a File written
		by any other path passes through `file_before_insert` too."""
		for file_name, content in (("logo.svg", SVG), ("page.html", HTML), ("macros.docx", MACRO_DOCX)):
			with self.subTest(file_name):
				with self.assertRaises(frappe.ValidationError):
					frappe.get_doc(
						{
							"doctype": "File",
							"file_name": file_name,
							"content": base64.b64encode(content).decode(),
							"decode": 1,
							"attached_to_doctype": "HR Request",
							"attached_to_name": self.request,
							"is_private": 1,
						}
					).insert()
		self.assertEqual(self._attachment_count(), 0)

	def test_the_policy_names_exactly_the_five_agreed_types(self):
		self.assertEqual(
			set(utils.ALLOWED_UPLOAD_EXTENSIONS), {".pdf", ".png", ".jpg", ".jpeg", ".docx", ".xlsx"}
		)
		self.assertEqual(utils.UPLOAD_MAX_BYTES, 10 * 1024 * 1024)


def _photo(fmt, size=(64, 32), exif=None):
	"""A real image built with Pillow, so the re-encode has pixels to read."""
	from PIL import Image

	buffer = io.BytesIO()
	image = Image.new("RGB", size, (200, 30, 30))
	params = {"exif": exif} if exif is not None else {}
	image.save(buffer, fmt, **params)
	return buffer.getvalue()


def _gps_exif(orientation=None):
	from PIL import Image

	exif = Image.Exif()
	exif[0x010F] = "PhoneMaker"  # Make
	if orientation:
		exif[0x0112] = orientation
	exif[0x8825] = {1: "N", 2: (12.0, 34.0, 56.0), 3: "E", 4: (65.0, 43.0, 21.0)}  # GPSInfo
	return exif.tobytes()


class TestProfilePhotoPolicy(IntegrationTestCase):
	"""Plan 2026-09-30-001 U1 (R1, R2): PNG/JPEG, 5 MB, re-encoded with no
	EXIF and a longest side of 512 px."""

	def _decode(self, content):
		from PIL import Image

		return Image.open(io.BytesIO(content))

	def test_a_jpeg_with_gps_exif_is_stripped_rotated_and_bounded(self):
		# Orientation 6 = rotate 90: a 2000x1000 landscape is a portrait.
		original = _photo("JPEG", size=(2000, 1000), exif=_gps_exif(orientation=6))
		self.assertTrue(self._decode(original).getexif())

		content, extension, content_type = utils.prepare_profile_photo("me.jpg", original)

		self.assertEqual((extension, content_type), (".jpg", "image/jpeg"))
		image = self._decode(content)
		self.assertEqual(image.format, "JPEG")
		self.assertLessEqual(max(image.size), utils.PHOTO_MAX_SIDE)
		self.assertGreater(image.height, image.width, "orientation was applied")
		self.assertFalse(image.getexif(), "no EXIF survives")
		self.assertNotIn("exif", image.info)
		self.assertNotIn(b"PhoneMaker", content)

	def test_a_png_under_the_cap_is_accepted(self):
		content, extension, content_type = utils.prepare_profile_photo("me.png", _photo("PNG"))
		self.assertEqual((extension, content_type), (".png", "image/png"))
		self.assertEqual(self._decode(content).format, "PNG")

	def test_every_unsafe_shape_is_refused_with_one_sentence(self):
		jpeg = _photo("JPEG", size=(300, 300))
		cases = {
			"pdf renamed png": ("me.png", PDF),
			"oversized": ("me.png", PNG + b"x" * utils.PHOTO_MAX_BYTES),
			"gif": ("me.gif", _photo("GIF")),
			"svg": ("me.svg", SVG),
			"svg renamed png": ("me.png", SVG),
			"webp": ("me.webp", _photo("WEBP")),
			"truncated jpeg": ("me.jpg", jpeg[: len(jpeg) // 2]),
			"corrupt jpeg": ("me.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 200),
			"empty": ("me.png", b""),
		}
		for label, (file_name, content) in cases.items():
			with self.subTest(label):
				with self.assertRaises(frappe.ValidationError, msg=label):
					utils.prepare_profile_photo(file_name, content)

	def test_a_decompression_bomb_is_refused_before_decoding(self):
		from PIL import Image

		# Scaled down rather than a real 50,000 px canvas: the same checks
		# trip on a small image once the bounds are below its pixel count.
		photo = _photo("PNG", size=(64, 32))
		with self.subTest("our own header bound"), patch.object(utils, "PHOTO_MAX_PIXELS", 100):
			with self.assertRaises(frappe.ValidationError):
				utils.prepare_profile_photo("me.png", photo)
		for limit, label in ((1000, "Pillow bomb warning"), (100, "Pillow bomb error")):
			with self.subTest(label), patch.object(Image, "MAX_IMAGE_PIXELS", limit):
				with self.assertRaises(frappe.ValidationError):
					utils.prepare_profile_photo("me.png", photo)

	def test_the_attachment_policy_is_unchanged(self):
		self.assertEqual(utils.validate_portal_upload("x.pdf", PDF), "application/pdf")
		with self.assertRaises(frappe.ValidationError):
			utils.validate_portal_upload("x.gif", _photo("GIF"))


class TestPerUserRateLimits(IntegrationTestCase):
	"""P2-U9 step 6. The limiter is off on a site with `allow_tests` -- the
	Python suite creates far more than ten HR Requests as one user, and a
	second Playwright pass inside the same minute re-trips the timesheet
	bound. This proves the bound is real anyway, by forcing the limiter on
	for the length of one test, and preflight (`check_test_mode`) is what
	stops that bypass from ever existing on a production site.
	"""

	def setUp(self):
		frappe.set_user("Administrator")
		self.employee_name, _, _, _ = make_test_employee_and_manager()
		frappe.set_user(EMPLOYEE_USER)
		frappe.flags.helixhr_enforce_rate_limits = True
		for action in utils.RATE_LIMIT_POLICY:
			utils.reset_rate_limit(action)

	def tearDown(self):
		frappe.flags.helixhr_enforce_rate_limits = False
		for action in utils.RATE_LIMIT_POLICY:
			utils.reset_rate_limit(action)
		frappe.set_user("Administrator")

	def test_the_key_is_site_and_user_scoped(self):
		self.assertEqual(
			utils._rate_limit_key("create_my_request", EMPLOYEE_USER),
			f"helixhr:rate-limit:{frappe.local.site}:create_my_request:{EMPLOYEE_USER}",
		)

	def test_the_suite_runs_with_the_limiter_off_and_that_needs_allow_tests(self):
		frappe.flags.helixhr_enforce_rate_limits = False
		self.assertTrue(frappe.conf.get("allow_tests"), "this suite only runs on a test site")
		self.assertFalse(utils.rate_limits_enforced())

	def test_creating_an_eleventh_request_in_an_hour_is_refused(self):
		from helixhr.api import create_my_request

		limit, _ = utils.rate_limit_bounds("create_my_request")
		self.assertEqual(limit, 10)
		for index in range(limit):
			create_my_request(
				category="HR Letter", subject=f"Rate limit {index}", operation_key=str(uuid.uuid4())
			)
		with self.assertRaises(frappe.RateLimitExceededError):
			create_my_request(
				category="HR Letter", subject="One too many", operation_key=str(uuid.uuid4())
			)

	def test_every_named_write_has_a_bound_and_none_is_looser_than_policy(self):
		from helixhr import preflight

		expected = {
			"update_my_profile": (20, 60),
			"save_my_week": (30, 60),
			"act_on_approval": (30, 60),
			"get_overdue_approvals": (60, 60),
			"apply_for_leave": (20, 3600),
			"withdraw_my_leave": (20, 3600),
			"create_my_request": (10, 3600),
			"attach_to_my_request": (20, 3600),
			"mark_notifications_read": (60, 60),
			# P3-U1 step 5 / P3-R25.
			"punch_my_checkin": (12, 60),
			"create_my_attendance_request": (10, 60),
			"send_my_attendance_request": (10, 60),
			"withdraw_my_attendance_request": (10, 60),
			"get_attendance_request_preview": (30, 60),
			"download_my_payslip": (10, 60),
			"get_directory": (60, 60),
			"get_my_team_week": (60, 60),
			"search_people": (60, 60),
			"get_person": (60, 60),
			"get_report_link": (60, 60),
			"search_projects": (60, 60),
			"get_project": (60, 60),
			"create_project": (20, 3600),
			"save_task": (30, 3600),
			"set_project_members": (20, 3600),
			"run_report": (30, 60),
			# Plan 2026-10-04-001 U3-U13: catalog, export, access matrix, views.
			"search_report_options": (60, 60),
			"get_report_catalog": (60, 60),
			"request_export": (10, 60),
			"download_export": (20, 60),
			"get_export_log": (60, 60),
			"list_my_exports": (60, 60),
			"list_report_views": (60, 60),
			"save_report_view": (60, 3600),
			"delete_report_view": (60, 3600),
			"get_report_access": (60, 60),
			"save_report_access": (30, 3600),
			"get_portal_role_holders": (60, 60),
			"set_portal_role": (30, 3600),
			"get_dashboard": (60, 60),
			"get_my_approvals": (60, 60),
			# Plan 2026-10-04-002 U3: the "To work on" tab polls like the queue.
			"get_request_work": (60, 60),
			"get_approval_detail": (60, 60),
			"get_leave_day_count": (60, 60),
			"get_my_leave_detail": (60, 60),
			"get_my_request": (60, 60),
			"get_my_attendance_request": (60, 60),
			"get_request_categories": (60, 60),
			"reply_to_my_request": (20, 3600),
			"attach_to_request_reply": (20, 3600),
			"get_organisation_view": (60, 60),
			"get_portal_config": (60, 60),
			"save_request_category": (30, 3600),
			"save_message_template": (30, 3600),
			"get_notification_setup": (60, 60),
			"preview_message_template": (60, 60),
			"reset_message_template": (30, 3600),
			"send_test_message": (5, 600),
			# Plan 2026-10-02-001 U13.
			"reveal_correction_value": (20, 3600),
			"save_leave_type": (30, 3600),
			"save_holiday_list": (30, 3600),
			"save_shift_type": (30, 3600),
			# P8-U7/U8/U9.
			"get_person_form_options": (60, 60),
			"save_person": (30, 3600),
			# P8-U12.
			"save_celebration_reminder": (30, 3600),
			# Plan 2026-09-29-001 U3.
			"get_my_profile": (60, 60),
			# Plan 2026-09-30-001 U2. The photo GET is unlimited on purpose.
			"upload_my_photo": (20, 3600),
			"remove_my_photo": (20, 3600),
			# Plan 2026-09-30-001 U7.
			"get_roster_week": (60, 60),
			# U8.
			"assign_shift": (30, 3600),
			"end_shift_assignment": (30, 3600),
			"change_shift_assignment": (30, 3600),
			"cancel_shift_assignment": (30, 3600),
		}
		self.assertEqual(utils.RATE_LIMIT_POLICY, expected)
		self.assertEqual(preflight.check_rate_limits()["status"], preflight.PASS)

	def test_preflight_fails_on_a_loosened_bound(self):
		from helixhr import preflight

		original = frappe.conf.get("helixhr_rate_limits")
		frappe.conf["helixhr_rate_limits"] = {"create_my_request": [500, 3600]}
		try:
			result = preflight.check_rate_limits()
			self.assertEqual(result["status"], preflight.FAIL)
			self.assertIn("create_my_request", result["detail"])
		finally:
			if original is None:
				frappe.conf.pop("helixhr_rate_limits", None)
			else:
				frappe.conf["helixhr_rate_limits"] = original


class TestResponseHardening(IntegrationTestCase):
	"""P2-U9 steps 5 and 8: what `helixhr.utils.set_security_headers` adds to
	every response, and what it adds to a portal attachment."""

	class _Request:
		def __init__(self, path, scheme="http"):
			self.path = path
			self.scheme = scheme

	def test_security_headers_are_set_on_every_response(self):
		response = Response("ok")
		utils.set_security_headers(response=response, request=self._Request("/helixhr"))
		self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
		self.assertEqual(response.headers["Content-Security-Policy"], "frame-ancestors 'none'")
		self.assertIn("Referrer-Policy", response.headers)
		# P3-KTD12: the exact value. `geolocation=()` made the browser report
		# a denial the check-in sheet could not tell from the user's choice.
		self.assertEqual(
			response.headers["Permissions-Policy"],
			"camera=(), microphone=(), geolocation=(self), payment=(), usb=()",
		)

	def test_hsts_only_over_https(self):
		plain = Response("ok")
		utils.set_security_headers(response=plain, request=self._Request("/helixhr", scheme="http"))
		self.assertNotIn("Strict-Transport-Security", plain.headers)

		secure = Response("ok")
		utils.set_security_headers(response=secure, request=self._Request("/helixhr", scheme="https"))
		self.assertIn("max-age=", secure.headers["Strict-Transport-Security"])

	def test_a_proxy_value_is_never_overwritten(self):
		response = Response("ok")
		response.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
		utils.set_security_headers(response=response, request=self._Request("/helixhr"))
		self.assertEqual(response.headers["Content-Security-Policy"], "frame-ancestors 'self'")

	def test_a_portal_attachment_is_served_as_a_download(self):
		frappe.set_user("Administrator")
		make_test_employee_and_manager()
		frappe.set_user(EMPLOYEE_USER)
		from helixhr.api import attach_to_my_request, create_my_request

		created = create_my_request(
			category="HR Letter", subject="Disposition", operation_key=str(uuid.uuid4())
		)
		frappe.local.request = with_uploaded_file("payslip.pdf", PDF)
		attached = attach_to_my_request(created["name"])
		frappe.local.request = None
		frappe.set_user("Administrator")

		response = Response("ok")
		utils.set_security_headers(response=response, request=self._Request(attached["file_url"]))
		self.assertIn("attachment", response.headers["Content-Disposition"])

	def test_a_file_url_shared_with_another_doctype_is_still_a_download(self):
		"""Frappe reuses one `file_url` across File rows with identical
		content, so a single-row lookup can answer with whichever row it
		happens to find first. Any row saying HR Request forces the
		download."""
		frappe.set_user("Administrator")
		make_test_employee_and_manager()
		frappe.set_user(EMPLOYEE_USER)
		from helixhr.api import attach_to_my_request, create_my_request

		created = create_my_request(
			category="HR Letter", subject="Shared content", operation_key=str(uuid.uuid4())
		)
		frappe.local.request = with_uploaded_file("shared.pdf", PDF)
		attached = attach_to_my_request(created["name"])
		frappe.local.request = None

		# The second row on the same URL: a To Do, which is not an HR
		# Request and must not be the answer.
		frappe.set_user("Administrator")
		todo = frappe.get_doc({"doctype": "ToDo", "description": "_Test shared file"}).insert(
			ignore_permissions=True
		)
		other = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "shared.pdf",
				"file_url": attached["file_url"],
				"is_private": 1,
				"attached_to_doctype": "ToDo",
				"attached_to_name": todo.name,
			}
		).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "File", other.name, force=True, ignore_permissions=True)

		response = Response("ok")
		shared_url = attached["file_url"]
		utils.set_security_headers(response=response, request=self._Request(shared_url))
		self.assertIn("attachment", response.headers["Content-Disposition"])

	def test_an_unrelated_private_file_keeps_its_own_disposition(self):
		response = Response("ok")
		utils.set_security_headers(
			response=response, request=self._Request("/private/files/not-a-portal-file.png")
		)
		self.assertNotIn("Content-Disposition", response.headers)
