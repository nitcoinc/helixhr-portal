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
			"apply_for_leave": (20, 3600),
			"withdraw_my_leave": (20, 3600),
			"create_my_request": (10, 3600),
			"attach_to_my_request": (20, 3600),
			"mark_notifications_read": (60, 60),
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
		self.assertIn("Permissions-Policy", response.headers)

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
