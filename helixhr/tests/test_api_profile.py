import uuid
from typing import ClassVar

import frappe
from frappe.tests import IntegrationTestCase

from helixhr.api import update_my_profile
from helixhr.tests.utils import EMPLOYEE_USER, MANAGER_USER, TEST_PASSWORD, make_test_employee_and_manager


class TestUpdateMyProfile(IntegrationTestCase):
	def setUp(self):
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_editable_field_is_saved_and_versioned(self):
		frappe.set_user(EMPLOYEE_USER)
		version_count_before = frappe.db.count("Version", {"ref_doctype": "Employee", "docname": self.employee_name})

		# Document.save() skips versioning under frappe.in_test unless told
		# otherwise (frappe/model/document.py) -- flip it off for this one
		# assertion so it exercises the same `save()` codepath production
		# actually uses instead of testing framework behavior.
		frappe.in_test = False
		try:
			result = update_my_profile(cell_number="+1-555-0100")
		finally:
			frappe.in_test = True

		self.assertEqual(result["cell_number"], "+1-555-0100")
		self.assertEqual(frappe.db.get_value("Employee", self.employee_name, "cell_number"), "+1-555-0100")
		version_count_after = frappe.db.count("Version", {"ref_doctype": "Employee", "docname": self.employee_name})
		self.assertGreater(version_count_after, version_count_before)

	def test_locked_field_is_dropped_before_reaching_the_document(self):
		frappe.set_user(EMPLOYEE_USER)
		original = frappe.db.get_value("Employee", self.employee_name, "department")

		result = update_my_profile(department="Somewhere Else", cell_number="+1-555-0101")

		self.assertNotIn("department", result)
		self.assertEqual(frappe.db.get_value("Employee", self.employee_name, "department"), original)
		self.assertEqual(frappe.db.get_value("Employee", self.employee_name, "cell_number"), "+1-555-0101")

	def test_invalid_email_is_rejected_with_a_plain_message(self):
		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.ValidationError):
			update_my_profile(personal_email="not-an-email")

	def test_employee_a_cannot_update_employee_b_by_any_argument(self):
		frappe.set_user(EMPLOYEE_USER)
		before = frappe.db.get_value("Employee", self.manager_name, "cell_number")

		update_my_profile(employee=self.manager_name, name=self.manager_name, cell_number="+1-555-0102")

		self.assertEqual(frappe.db.get_value("Employee", self.manager_name, "cell_number"), before)
		self.assertEqual(
			frappe.db.get_value("Employee", self.employee_name, "cell_number"), "+1-555-0102"
		)

	def test_rate_limit_triggers_per_user_not_globally(self):
		"""P2-U9 step 6: the bound comes from `RATE_LIMIT_POLICY`, and the
		limiter is off on a site with `allow_tests` -- so this forces it on
		for the length of the test rather than asserting the bypass."""
		from helixhr.tests.utils import MANAGER_USER
		from helixhr.utils import rate_limit_bounds, rate_limit_per_user, reset_rate_limit

		limit, _ = rate_limit_bounds("update_my_profile")
		frappe.flags.helixhr_enforce_rate_limits = True
		try:
			frappe.set_user(EMPLOYEE_USER)
			reset_rate_limit("update_my_profile")
			with self.assertRaises(frappe.RateLimitExceededError):
				for _ in range(limit + 5):
					rate_limit_per_user("update_my_profile")

			# A different user's bucket is untouched.
			frappe.set_user(MANAGER_USER)
			reset_rate_limit("update_my_profile")
			rate_limit_per_user("update_my_profile")
		finally:
			frappe.flags.helixhr_enforce_rate_limits = False
			for user in (EMPLOYEE_USER, MANAGER_USER):
				reset_rate_limit("update_my_profile", user=user)


class TestGetMyProfile(IntegrationTestCase):
	"""Plan 2026-09-29-001 U3: the employee's own record, by Profile tab --
	allow-listed, masked on the server, and never salary."""

	def setUp(self):
		from helixhr.patches.v1_0.seed_profile_correction_category import execute

		execute()
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Employee", self.employee_name)
		doc.date_of_birth = "1991-04-17"
		doc.bank_ac_no = "004512345678"
		doc.passport_number = "Z1234567"
		doc.ctc = 1234567
		doc.set("education", [{"school_univ": "State University", "qualification": "BSc", "level": "Graduate"}])
		doc.set(
			"external_work_history",
			[{"company_name": "Previous Co", "designation": "Engineer", "salary": 98765}],
		)
		doc.save(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")

	def _profile(self, user=EMPLOYEE_USER, **kwargs):
		from helixhr.api import get_my_profile

		frappe.set_user(user)
		return get_my_profile(**kwargs)

	@staticmethod
	def _field(profile, section, fieldname):
		return next(f for f in profile["sections"][section]["fields"] if f["fieldname"] == fieldname)

	def test_the_employee_sees_their_own_date_of_birth_joining_and_education(self):
		profile = self._profile()
		self.assertEqual(profile["employee"], self.employee_name)
		self.assertEqual(str(self._field(profile, "personal", "date_of_birth")["value"]), "1991-04-17")
		self.assertIsNotNone(self._field(profile, "personal", "date_of_joining")["value"])
		education = next(t for t in profile["sections"]["history"]["tables"] if t["fieldname"] == "education")
		self.assertEqual(education["rows"][0]["school_univ"], "State University")
		self.assertEqual(profile["failed_sections"], [])

	def test_labels_are_sentence_case_with_acronyms_kept(self):
		from helixhr.api import _sentence_case

		self.assertEqual(_sentence_case("Date Of Retirement"), "Date of retirement")
		self.assertEqual(_sentence_case("IBAN"), "IBAN")
		self.assertEqual(_sentence_case("PAN Number"), "PAN number")
		self.assertEqual(self._field(self._profile(), "personal", "date_of_birth")["label"], "Date of birth")

	def test_people_are_shown_by_name_never_by_id_or_login(self):
		from helixhr.tests.utils import MANAGER_USER

		frappe.db.set_value("Employee", self.employee_name, "leave_approver", MANAGER_USER)
		manager_name = frappe.db.get_value("Employee", self.manager_name, "employee_name")
		profile = self._profile()
		self.assertEqual(self._field(profile, "job", "reports_to")["value"], manager_name)
		self.assertEqual(self._field(profile, "job", "leave_approver")["value"], manager_name)
		self.assertNotIn(MANAGER_USER, frappe.as_json(profile["sections"]["job"]))

	def test_mask_identifier_edges(self):
		from helixhr.utils import mask_identifier

		self.assertIsNone(mask_identifier(None))
		self.assertIsNone(mask_identifier(""))
		self.assertIsNone(mask_identifier("   "))
		self.assertEqual(mask_identifier("ab12"), "••••")
		self.assertEqual(mask_identifier("abcde"), "••••bcde")
		self.assertEqual(mask_identifier("  004512345678 "), "••••5678")

	def test_another_employees_id_as_an_argument_is_ignored(self):
		profile = self._profile(employee=self.manager_name, name=self.manager_name)
		self.assertEqual(profile["employee"], self.employee_name)

	def test_identifiers_leave_the_server_masked_to_their_last_four(self):
		profile = self._profile()
		rendered = frappe.as_json(profile)
		self.assertNotIn("004512345678", rendered)
		self.assertNotIn("Z1234567", rendered)
		account = self._field(profile, "bank", "bank_ac_no")
		self.assertEqual(account["value"], "••••5678")
		self.assertTrue(account["masked"])
		self.assertEqual(self._field(profile, "bank", "passport_number")["value"], "••••4567")

	def test_no_salary_or_payroll_key_appears_anywhere(self):
		rendered = frappe.as_json(self._profile())
		for key in ("ctc", "salary_currency", "payroll_cost_center", "employee_advance_account", "health_details", "salary"):
			self.assertNotIn(f'"{key}"', rendered, key)
		self.assertNotIn("98765", rendered)
		self.assertNotIn("1234567", rendered.replace("••••4567", ""))

	def test_a_field_hr_never_filled_comes_back_empty_not_missing(self):
		frappe.db.set_value("Employee", self.employee_name, "blood_group", "")
		field = self._field(self._profile(), "personal", "blood_group")
		self.assertFalse(field["value"])

	def test_a_failing_section_is_named_and_the_rest_still_answer(self):
		from unittest.mock import patch

		import helixhr.api as api

		real = api._profile_section

		def flaky(employee, name, meta):
			if name == "history":
				raise RuntimeError("boom")
			return real(employee, name, meta)

		with patch.object(api, "_profile_section", side_effect=flaky):
			profile = self._profile()
		self.assertEqual(profile["failed_sections"], ["history"])
		self.assertIsNone(profile["sections"]["history"])
		self.assertIsNotNone(profile["sections"]["personal"])

	def test_desk_url_is_for_hr_roles_only(self):
		from helixhr.tests.utils import make_test_hr_manager_employee

		self.assertIsNone(self._profile()["desk_url"])
		_, hr_user = make_test_hr_manager_employee()
		frappe.set_user("Administrator")
		desk_url = self._profile(user=hr_user)["desk_url"]
		self.assertIn("/employee/", desk_url)

	def test_correction_category_goes_null_when_hr_retires_it(self):
		from helixhr.utils import PROFILE_CORRECTION_CATEGORY

		self.assertEqual(self._profile()["correction_category"], PROFILE_CORRECTION_CATEGORY)
		frappe.set_user("Administrator")
		frappe.db.set_value("HelixHR Request Category", PROFILE_CORRECTION_CATEGORY, "is_active", 0)
		self.addCleanup(frappe.db.set_value, "HelixHR Request Category", PROFILE_CORRECTION_CATEGORY, "is_active", 1)
		self.assertIsNone(self._profile()["correction_category"])

	def test_a_user_with_no_active_employee_is_refused(self):
		from helixhr.tests.utils import ORPHAN_USER, make_test_user_without_employee

		make_test_user_without_employee()
		with self.assertRaises(frappe.PermissionError):
			self._profile(user=ORPHAN_USER)

	def test_the_read_is_rate_limited(self):
		from helixhr.utils import rate_limit_bounds, rate_limit_per_user, reset_rate_limit

		limit, _ = rate_limit_bounds("get_my_profile")
		frappe.flags.helixhr_enforce_rate_limits = True
		try:
			frappe.set_user(EMPLOYEE_USER)
			reset_rate_limit("get_my_profile")
			with self.assertRaises(frappe.RateLimitExceededError):
				for _ in range(limit + 5):
					rate_limit_per_user("get_my_profile")
		finally:
			frappe.flags.helixhr_enforce_rate_limits = False
			reset_rate_limit("get_my_profile", user=EMPLOYEE_USER)


class TestGetMyProfileRegionalField(IntegrationTestCase):
	"""Kept apart from TestGetMyProfile on purpose: adding a Custom Field is
	DDL, which MariaDB commits implicitly -- so this class writes nothing
	before it that a commit would make permanent."""

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_a_field_the_site_lacks_is_left_out_and_a_present_one_is_masked(self):
		from helixhr.api import get_my_profile
		from helixhr.tests.test_employee_permlevel import _drop_custom_field, _import_fixture_setters

		employee_name, _, _, _ = make_test_employee_and_manager()
		frappe.set_user(EMPLOYEE_USER)
		bank = get_my_profile()["sections"]["bank"]["fields"]
		had_pan = any(f["fieldname"] == "pan_number" for f in bank)
		if not had_pan:
			from frappe.custom.doctype.custom_field.custom_field import create_custom_field

			frappe.set_user("Administrator")
			create_custom_field(
				"Employee", {"fieldname": "pan_number", "label": "PAN", "fieldtype": "Data", "insert_after": "bank_ac_no"}
			)
			self.addCleanup(_drop_custom_field, "Employee-pan_number")
			_import_fixture_setters(["Employee-pan_number-permlevel"])
			frappe.clear_cache(doctype="Employee")
		frappe.set_user("Administrator")
		frappe.db.set_value("Employee", employee_name, "pan_number", "ABCDE1234F")
		frappe.set_user(EMPLOYEE_USER)

		profile = get_my_profile()
		pan = next(f for f in profile["sections"]["bank"]["fields"] if f["fieldname"] == "pan_number")
		self.assertEqual(pan["value"], "••••234F")
		self.assertNotIn("ABCDE1234F", frappe.as_json(profile))


# --- Profile photo (plan 2026-09-30-001, U2 and U3) ------------------------

PHOTO_LEFT_USER = "photo-left@helixhr.test"


def _jpeg(size=(40, 30), color=(10, 120, 200)):
	import io

	from PIL import Image

	buffer = io.BytesIO()
	Image.new("RGB", size, color).save(buffer, "JPEG")
	return buffer.getvalue()


class _PhotoTestCase(IntegrationTestCase):
	def setUp(self):
		from helixhr.tests.test_api_people import OTHER_COMPANY_USER, _ensure_other_company
		from helixhr.tests.utils import (
			TEST_COMPANY,
			ensure_hr_manager_user,
			make_test_hr_manager_employee,
			make_test_user,
			make_test_user_without_employee,
		)

		frappe.set_user("Administrator")
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()
		self.hr_employee, self.hr_user = make_test_hr_manager_employee()
		self.hr_unscoped = ensure_hr_manager_user()
		self.orphan = make_test_user_without_employee()
		self.other_user = OTHER_COMPANY_USER
		self.other_employee = make_test_user(OTHER_COMPANY_USER, _ensure_other_company())
		self.left_employee = make_test_user(PHOTO_LEFT_USER, TEST_COMPANY)
		# No rollback between methods on this bench (runbook, U6): start clean.
		for user in (EMPLOYEE_USER, OTHER_COMPANY_USER, PHOTO_LEFT_USER):
			self._as(user, self._remove)

	def tearDown(self):
		frappe.local.request = None
		frappe.set_user("Administrator")

	def _as(self, user, fn, *args):
		frappe.set_user(user)
		try:
			return fn(*args)
		finally:
			frappe.local.request = None
			frappe.set_user("Administrator")

	def _upload(self, content=None, file_name="me.jpg", **kwargs):
		from helixhr.api import upload_my_photo
		from helixhr.tests.test_hr_request import with_uploaded_file

		frappe.local.request = with_uploaded_file(file_name, content or _jpeg())
		return upload_my_photo(**kwargs)

	def _remove(self):
		from helixhr.api import remove_my_photo

		return remove_my_photo()

	def _photo_files(self, employee):
		return frappe.get_all(
			"File",
			filters={"attached_to_doctype": "Employee", "attached_to_name": employee, "attached_to_field": "image"},
			fields=["name", "file_url", "is_private"],
		)

	def _user_files(self, user, file_url):
		return frappe.db.count(
			"File", {"attached_to_doctype": "User", "attached_to_name": user, "file_url": file_url}
		)


class TestMyPhoto(_PhotoTestCase):
	"""U2 (R1, R6): the caller sets, replaces and removes their own photo."""

	def test_upload_stores_one_private_file_and_never_touches_the_user(self):
		user_image_before = frappe.db.get_value("User", EMPLOYEE_USER, "user_image")

		result = self._as(EMPLOYEE_USER, self._upload)

		files = self._photo_files(self.employee_name)
		self.assertEqual(len(files), 1)
		self.assertEqual(files[0].is_private, 1)
		self.assertTrue(files[0].file_url.startswith("/private/files/"))
		self.assertEqual(frappe.db.get_value("Employee", self.employee_name, "image"), files[0].file_url)
		self.assertIn("helixhr.api.get_employee_photo", result["photo_url"])
		self.assertIn(f"employee={self.employee_name}", result["photo_url"])
		self.assertEqual(frappe.db.get_value("User", EMPLOYEE_USER, "user_image"), user_image_before)
		self.assertEqual(self._user_files(EMPLOYEE_USER, files[0].file_url), 0)

	def test_a_later_full_employee_save_still_keeps_the_photo_off_the_user(self):
		"""P0 from doc review: `Employee.update_user` copies `image` into
		`User.user_image` and attaches a File to the User on every save."""
		from helixhr.api import save_person

		user_image_before = frappe.db.get_value("User", EMPLOYEE_USER, "user_image")
		self._as(EMPLOYEE_USER, self._upload)
		file_url = frappe.db.get_value("Employee", self.employee_name, "image")

		self._as(self.hr_unscoped, save_person, self.employee_name)
		self._as(EMPLOYEE_USER, lambda: update_my_profile(cell_number="+1-555-0190"))
		# Desk, with versioning on (skipped under in_test otherwise): the
		# hide-and-restore must not record the photo as removed.
		frappe.in_test = False
		try:
			desk = frappe.get_doc("Employee", self.employee_name)
			desk.cell_number = "+1-555-0191"
			desk.save(ignore_permissions=True)
		finally:
			frappe.in_test = True
		self.assertEqual(desk.image, file_url)
		latest = frappe.get_all(
			"Version",
			filters={"ref_doctype": "Employee", "docname": self.employee_name},
			fields=["data"],
			order_by="creation desc",
			limit=1,
		)
		self.assertNotIn('"image"', latest[0].data)

		self.assertEqual(frappe.db.get_value("Employee", self.employee_name, "image"), file_url)
		self.assertEqual(frappe.db.get_value("User", EMPLOYEE_USER, "user_image"), user_image_before)
		self.assertEqual(self._user_files(EMPLOYEE_USER, file_url), 0)
		self.assertEqual(frappe.db.count("File", {"file_url": file_url, "attached_to_doctype": "User"}), 0)

	def test_clearing_the_photo_in_desk_sticks(self):
		"""Review fix: the before_save stash must never write back a photo
		the save itself cleared -- including when a failed earlier save on
		the same document object left the stash flag behind."""
		self._as(EMPLOYEE_USER, self._upload)
		desk = frappe.get_doc("Employee", self.employee_name)
		desk.flags.helixhr_photo = desk.image  # stale stash from an aborted save
		desk.image = None
		desk.save(ignore_permissions=True)

		self.assertFalse(desk.image)
		self.assertFalse(frappe.db.get_value("Employee", self.employee_name, "image"))

	def test_replacing_the_photo_in_desk_with_a_plain_url_sticks(self):
		self._as(EMPLOYEE_USER, self._upload)
		desk = frappe.get_doc("Employee", self.employee_name)
		desk.image = "https://example.invalid/me.png"
		desk.save(ignore_permissions=True)

		self.assertEqual(
			frappe.db.get_value("Employee", self.employee_name, "image"), "https://example.invalid/me.png"
		)
		frappe.db.set_value("Employee", self.employee_name, "image", None)

	def test_replacing_leaves_exactly_one_photo_and_the_old_one_is_gone(self):
		first = self._as(EMPLOYEE_USER, self._upload, _jpeg(color=(1, 2, 3)))
		old_url = frappe.db.get_value("Employee", self.employee_name, "image")
		second = self._as(EMPLOYEE_USER, self._upload, _jpeg(color=(250, 200, 10)))

		files = self._photo_files(self.employee_name)
		self.assertEqual(len(files), 1)
		self.assertNotEqual(files[0].file_url, old_url)
		self.assertNotEqual(first["photo_url"], second["photo_url"], "a replace changes the URL (R5)")
		self.assertFalse(frappe.db.exists("File", {"file_url": old_url}))

	def test_remove_clears_the_field_and_a_second_remove_is_a_no_op(self):
		self._as(EMPLOYEE_USER, self._upload)

		self.assertEqual(self._as(EMPLOYEE_USER, self._remove), {"photo_url": None})
		self.assertFalse(frappe.db.get_value("Employee", self.employee_name, "image"))
		self.assertEqual(self._photo_files(self.employee_name), [])
		self.assertEqual(self._as(EMPLOYEE_USER, self._remove), {"photo_url": None})

	def test_a_forged_employee_argument_is_ignored(self):
		self._as(EMPLOYEE_USER, lambda: self._upload(employee=self.manager_name))

		self.assertEqual(len(self._photo_files(self.employee_name)), 1)
		self.assertEqual(self._photo_files(self.manager_name), [])

	def test_a_wrong_type_is_refused_and_nothing_is_stored(self):
		with self.assertRaises(frappe.ValidationError):
			self._as(EMPLOYEE_USER, self._upload, b"<svg xmlns='http://www.w3.org/2000/svg'/>", "me.png")
		self.assertEqual(self._photo_files(self.employee_name), [])

	def test_a_caller_with_no_employee_gets_the_not_linked_refusal(self):
		with self.assertRaises(frappe.PermissionError):
			self._as(self.orphan, self._upload)
		with self.assertRaises(frappe.PermissionError):
			self._as(self.orphan, self._remove)

	def test_a_public_photo_file_inserted_directly_is_refused(self):
		import base64

		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(
				{
					"doctype": "File",
					"file_name": "desk.jpg",
					"content": base64.b64encode(_jpeg()).decode(),
					"decode": 1,
					"attached_to_doctype": "Employee",
					"attached_to_name": self.employee_name,
					"attached_to_field": "image",
					"is_private": 0,
				}
			).insert()
		self.assertEqual(self._photo_files(self.employee_name), [])

	def test_a_private_non_image_photo_file_inserted_directly_is_refused(self):
		import base64

		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "File",
					"file_name": "desk.png",
					"content": base64.b64encode(b"%PDF-1.4 not an image").decode(),
					"decode": 1,
					"attached_to_doctype": "Employee",
					"attached_to_name": self.employee_name,
					"attached_to_field": "image",
					"is_private": 1,
				}
			).insert()


class TestEmployeePhoto(_PhotoTestCase):
	"""U3 (R3, R5, KTD1, KTD2, KTD5): who gets the bytes, and how."""

	def _get(self, user, employee):
		from helixhr.api import get_employee_photo

		frappe.local.response_headers = {}
		for key in ("type", "filecontent", "filename", "content_type", "display_content_as"):
			frappe.response.pop(key, None)
		self._as(user, get_employee_photo, employee)
		return frappe.response

	def _assert_refused(self, user, employee):
		with self.assertRaises(frappe.DoesNotExistError):
			self._get(user, employee)
		self.assertNotIn("filecontent", frappe.response)

	def test_the_owner_gets_inline_jpeg_bytes_with_a_private_cache(self):
		from frappe.utils.response import build_response

		self._as(EMPLOYEE_USER, self._upload)
		response = self._get(EMPLOYEE_USER, self.employee_name)

		self.assertEqual(response.type, "download")
		self.assertEqual(response.display_content_as, "inline")
		self.assertEqual(response.content_type, "image/jpeg")
		self.assertTrue(response.filecontent.startswith(b"\xff\xd8\xff"))
		cache = frappe.local.response_headers["Cache-Control"]
		self.assertEqual(cache, "private, max-age=300")
		self.assertEqual(frappe.local.response_headers["X-Content-Type-Options"], "nosniff")

		served = build_response()
		self.assertEqual(served.mimetype, "image/jpeg")
		self.assertTrue(served.headers["Content-Disposition"].startswith("inline"))

	def test_the_portal_attachment_hook_leaves_the_photo_inline(self):
		"""KTD5: `_force_download_portal_attachment` is scoped to
		`/private/files`; the photo is served from the method path."""
		from frappe.utils.response import build_response

		from helixhr import utils

		self._as(EMPLOYEE_USER, self._upload)
		self._get(EMPLOYEE_USER, self.employee_name)
		served = build_response()

		class _Request:
			scheme = "http"
			path = "/api/method/helixhr.api.get_employee_photo"

		utils.set_security_headers(served, _Request())
		self.assertTrue(served.headers["Content-Disposition"].startswith("inline"))

	def test_a_same_company_colleague_is_allowed(self):
		self._as(EMPLOYEE_USER, self._upload)
		self.assertTrue(self._get(MANAGER_USER, self.employee_name).filecontent)

	def test_another_company_and_no_employee_are_refused(self):
		self._as(EMPLOYEE_USER, self._upload)
		self._assert_refused(self.other_user, self.employee_name)
		self._assert_refused(self.orphan, self.employee_name)

	def test_hr_in_scope_is_allowed_and_hr_out_of_scope_is_refused(self):
		self._as(EMPLOYEE_USER, self._upload)
		self._as(self.other_user, self._upload)

		self.assertTrue(self._get(self.hr_user, self.employee_name).filecontent)
		self._assert_refused(self.hr_user, self.other_employee)
		self.assertTrue(self._get(self.hr_unscoped, self.other_employee).filecontent)

	def test_a_left_employee_is_hidden_from_colleagues_but_not_from_hr(self):
		self._as(PHOTO_LEFT_USER, self._upload)
		frappe.db.set_value("Employee", self.left_employee, "status", "Left")
		try:
			self._assert_refused(EMPLOYEE_USER, self.left_employee)
			self.assertTrue(self._get(self.hr_user, self.left_employee).filecontent)
		finally:
			frappe.db.set_value("Employee", self.left_employee, "status", "Active")

	def test_no_photo_is_not_found_and_never_someone_elses(self):
		self._as(EMPLOYEE_USER, self._upload)
		self._assert_refused(EMPLOYEE_USER, self.manager_name)
		self._assert_refused(EMPLOYEE_USER, "HR-EMP-DOES-NOT-EXIST")

	def test_a_legacy_desk_photo_is_served_only_when_it_really_is_an_image(self):
		"""Open question resolved: a public `/files` photo set in Desk
		before this feature is served through this method only when its
		bytes are PNG/JPEG. Anything else (an SVG could carry script) is
		treated as no photo rather than rendered inline."""
		import base64

		for label, file_name, content, served in (
			("jpeg", "legacy.jpg", _jpeg(color=(5, 5, 5)), True),
			("svg", "legacy.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', False),
		):
			with self.subTest(label):
				doc = frappe.get_doc(
					{
						"doctype": "File",
						"file_name": file_name,
						"content": base64.b64encode(content).decode(),
						"decode": 1,
						"attached_to_doctype": "Employee",
						"attached_to_name": self.manager_name,
						"is_private": 0,
					}
				).insert(ignore_permissions=True)
				# The shape a pre-portal Desk upload left behind.
				frappe.db.set_value("File", doc.name, "attached_to_field", "image")
				frappe.db.set_value("Employee", self.manager_name, "image", doc.file_url)
				try:
					if served:
						self.assertTrue(self._get(EMPLOYEE_USER, self.manager_name).filecontent)
					else:
						self._assert_refused(EMPLOYEE_USER, self.manager_name)
				finally:
					frappe.db.set_value("Employee", self.manager_name, "image", None)
					frappe.delete_doc("File", doc.name, ignore_permissions=True, force=True)

	def test_guest_cannot_reach_the_method_and_it_is_not_rate_limited(self):
		"""Guest by `frappe.guest_methods`, never a nested werkzeug request
		(runbook, U4). The GET has no RATE_LIMIT_POLICY entry on purpose."""
		from helixhr import utils
		from helixhr.api import get_employee_photo, remove_my_photo, upload_my_photo

		self.assertIn(get_employee_photo, frappe.whitelisted)
		for method in (get_employee_photo, upload_my_photo, remove_my_photo):
			self.assertNotIn(method, frappe.guest_methods)
		self.assertNotIn("get_employee_photo", utils.RATE_LIMIT_POLICY)

	def test_frappes_own_private_file_check_still_refuses_a_colleague(self):
		"""We did not widen Frappe's check: a colleague (not the manager,
		whose nested User Permission reaches reports) cannot read the File by
		its `/private/files` URL, while this method serves them the bytes."""
		self._as(EMPLOYEE_USER, self._upload)
		name = self._photo_files(self.employee_name)[0].name
		self.assertTrue(self._get(PHOTO_LEFT_USER, self.employee_name).filecontent)
		frappe.set_user(PHOTO_LEFT_USER)
		try:
			self.assertFalse(frappe.get_doc("File", name).is_downloadable())
		finally:
			frappe.set_user("Administrator")


class TestPhotoInProjections(_PhotoTestCase):
	"""U4 (R4): every avatar projection carries `photo_url`, batched."""

	def test_the_directory_pays_one_extra_query_not_one_per_row(self):
		from unittest.mock import patch

		from helixhr import api

		self._as(EMPLOYEE_USER, self._upload)

		def count_queries(photo_urls=api._photo_urls):
			with patch.object(frappe.db, "sql", wraps=frappe.db.sql) as sql, patch.object(api, "_photo_urls", photo_urls):
				frappe.set_user(EMPLOYEE_USER)
				try:
					people = api.get_directory()["people"]
				finally:
					frappe.set_user("Administrator")
			return sql.call_count, people

		with_photos, people = count_queries()
		without_photos, _people = count_queries(lambda employees: {})
		self.assertGreater(len(people), 1)
		self.assertEqual(with_photos - without_photos, 1)

		by_name = {person["name"]: person for person in people}
		self.assertIn("v=", by_name[self.employee_name]["photo_url"])
		self.assertNotIn("/private/files", by_name[self.employee_name]["photo_url"])
		self.assertIsNone(by_name[self.manager_name]["photo_url"])
		manager = by_name[self.manager_name]
		self.assertEqual(manager["initials"], api._initials(manager["employee_name"]))

	def test_profile_and_bootstrap_carry_the_callers_own_photo(self):
		from helixhr.api import get_my_profile, get_portal_bootstrap

		self.assertIsNone(self._as(EMPLOYEE_USER, get_my_profile)["photo_url"])
		uploaded = self._as(EMPLOYEE_USER, self._upload)["photo_url"]
		self.assertEqual(self._as(EMPLOYEE_USER, get_my_profile)["photo_url"], uploaded)
		self.assertEqual(self._as(EMPLOYEE_USER, get_portal_bootstrap)["employee"]["photo_url"], uploaded)

	def test_an_approval_queue_row_carries_the_requesters_versioned_photo(self):
		# Plan 2026-10-16-001 KTD1: requests left Approvals, so the queue row
		# under test is a decision -- the requester's pending leave.
		import uuid

		from frappe.utils import add_days, today

		from helixhr.api import get_my_approvals
		from helixhr.tests.utils import ensure_leave_allocation

		uploaded = self._as(EMPLOYEE_USER, self._upload)["photo_url"]
		ensure_leave_allocation(self.employee_name, "Casual Leave", 5)
		leave_date = str(add_days(today(), uuid.uuid4().int % 100))
		name = self._as(
			EMPLOYEE_USER,
			lambda: frappe.get_doc(
				{
					"doctype": "Leave Application",
					"employee": self.employee_name,
					"leave_type": "Casual Leave",
					"from_date": leave_date,
					"to_date": leave_date,
					"description": "Photo in queue",
					"leave_approver": MANAGER_USER,
				}
			)
			.insert()
			.name,
		)
		pending = self._as(MANAGER_USER, get_my_approvals)["pending"]
		row = next(row for row in pending if row["name"] == name)
		self.assertEqual(row["photo_url"], uploaded)
		self.assertIn("v=", row["photo_url"])


class TestSubmitMyProfileChanges(IntegrationTestCase):
	"""Plan 2026-11-03-001 U6: one call fills blank Bank & IDs groups now and
	files the rest to HR as one row request, in one transaction."""

	ACCOUNT = "987654321098"
	BLANK: ClassVar[dict] = {
		"salary_mode": None,
		"bank_name": None,
		"bank_ac_no": None,
		"iban": None,
		"passport_number": None,
		"date_of_issue": None,
		"valid_upto": None,
		"place_of_issue": None,
		"health_insurance_provider": None,
		"health_insurance_no": None,
	}

	def setUp(self):
		from helixhr.patches.v1_0 import seed_profile_correction_category
		from helixhr.tests.utils import ensure_test_email_account
		from helixhr.utils import PROFILE_CORRECTION_CATEGORY

		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()
		ensure_test_email_account()
		seed_profile_correction_category.execute()
		frappe.db.set_value(
			"HelixHR Request Category",
			PROFILE_CORRECTION_CATEGORY,
			{"is_active": 1, "route_to_role": "HR Manager"},
		)
		frappe.db.set_single_value("System Settings", "disable_user_pass_login", 0)
		frappe.db.set_value(
			"Employee",
			self.employee_name,
			{
				**self.BLANK,
				"status": "Active",
				"company_email": "employee-company@helixhr.test",
				"personal_email": "employee-personal@helixhr.test",
			},
		)
		# Close anything an earlier test left open, so R17 starts clean.
		for name in frappe.get_all(
			"HR Request", filters={"employee": self.employee_name, "status": ["not in", ("Done", "Rejected")]}, pluck="name"
		):
			frappe.db.set_value("HR Request", name, "status", "Rejected")
		frappe.db.delete("Version", {"ref_doctype": "Employee", "docname": self.employee_name})
		self.addCleanup(setattr, frappe.local, "request", None)
		self._mail_before = set(frappe.get_all("Email Queue", pluck="name"))

	def tearDown(self):
		frappe.set_user("Administrator")

	def _submit(self, changes, seen, proof=False, password=TEST_PASSWORD, key=None, confirm=None):
		from helixhr.api import submit_my_profile_changes
		from helixhr.tests.test_hr_request import _Request, with_uploaded_file

		frappe.local.request = with_uploaded_file("bank-letter.pdf") if proof else _Request({})
		frappe.set_user(EMPLOYEE_USER)
		try:
			return submit_my_profile_changes(
				changes=frappe.as_json(
					[{"field": f, "value": v, "confirm": (confirm or {}).get(f, v)} for f, v in changes.items()]
				),
				seen=frappe.as_json(seen),
				operation_key=key or str(uuid.uuid4()),
				password=password,
			)
		finally:
			frappe.set_user("Administrator")

	def _live(self, *fields):
		return frappe.db.get_value("Employee", self.employee_name, list(fields), as_dict=True)

	def _mails_to(self, address):
		new = set(frappe.get_all("Email Queue", pluck="name")) - self._mail_before
		return frappe.get_all(
			"Email Queue Recipient", filters={"parent": ["in", list(new) or [""]], "recipient": address}, pluck="parent"
		)

	def _requests(self):
		return frappe.get_all("HR Request", filters={"employee": self.employee_name, "status": "Open"}, pluck="name")

	def _errors(self, result):
		self.assertEqual(result["status"], "refused", result)
		return result["errors"]

	BANK: ClassVar[dict] = {"bank_name": "New Bank", "bank_ac_no": ACCOUNT}

	# --- what the edit mode reads (U9) -------------------------------------

	def test_get_my_profile_describes_each_editable_field_and_the_locked_ones(self):
		from helixhr.api import get_my_profile

		frappe.db.set_value("Employee", self.employee_name, {"bank_name": "Old Bank", "bank_ac_no": "111122223333"})
		result = self._submit({"bank_ac_no": self.ACCOUNT}, {"bank": "filled"}, proof=True)
		self.assertEqual(result["status"], "requested", result)
		frappe.set_user(EMPLOYEE_USER)
		try:
			profile = get_my_profile()
		finally:
			frappe.set_user("Administrator")
		fields = {f["fieldname"]: f for f in profile["sections"]["bank"]["fields"]}
		self.assertEqual(fields["bank_ac_no"]["change"], {"group": "bank", "type": "Data", "options": None})
		self.assertEqual(fields["salary_mode"]["change"]["type"], "Select")
		self.assertIn("Bank", fields["salary_mode"]["change"]["options"])
		self.assertEqual(fields["date_of_issue"]["change"]["group"], "passport")
		self.assertEqual(fields["date_of_issue"]["change"]["type"], "Date")
		self.assertIn("bank_ac_no", profile["pending_fields"])
		self.assertNotIn(self.ACCOUNT, frappe.as_json(profile))
		job = profile["sections"]["job"]["fields"]
		self.assertFalse(any("change" in f for f in job))

	# --- first-time fill ---------------------------------------------------

	def test_a_blank_bank_group_fills_now_with_one_notice_mark_and_hr_alert(self):
		result = self._submit(self.BANK, {"bank": "blank"})
		self.assertEqual(result["status"], "applied")
		self.assertIsNone(result["request"])
		self.assertEqual(self._live("bank_ac_no").bank_ac_no, self.ACCOUNT)
		self.assertFalse(self._requests())
		self.assertNotIn(self.ACCOUNT, frappe.as_json(result))
		self.assertEqual(len(self._mails_to("employee-company@helixhr.test")), 1)
		self.assertEqual(len(self._mails_to("employee-personal@helixhr.test")), 1)
		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{"reference_doctype": "Employee", "reference_name": self.employee_name, "subject": "helixhr:self_filled_unverified"},
			)
		)
		self.assertTrue(
			frappe.db.exists("Notification Log", {"document_type": "Employee", "document_name": self.employee_name})
		)

	def test_a_fill_retried_after_it_landed_reports_applied(self):
		self._submit(self.BANK, {"bank": "blank"})
		again = self._submit(self.BANK, {"bank": "blank"})
		self.assertEqual(again["status"], "applied")
		self.assertEqual({row["field"] for row in again["applied"]}, set(self.BANK))

	def test_a_partial_fill_must_carry_the_core_fields(self):
		errors = self._errors(self._submit({"bank_name": "New Bank"}, {"bank": "blank"}))
		self.assertEqual(errors[0]["group"], "bank")
		self.assertIsNone(self._live("bank_name").bank_name)
		errors = self._errors(self._submit({"passport_number": "Z7654321"}, {"passport": "blank"}))
		self.assertEqual(errors[0]["group"], "passport")

	def test_a_fill_needs_the_password(self):
		for password in (None, "wrong-password"):
			errors = self._errors(self._submit(self.BANK, {"bank": "blank"}, password=password))
			self.assertEqual(errors[0]["field"], "password")
		self.assertIsNone(self._live("bank_ac_no").bank_ac_no)

	def test_a_fill_skips_the_password_on_an_sso_only_site(self):
		frappe.db.set_single_value("System Settings", "disable_user_pass_login", 1)
		self.addCleanup(frappe.db.set_single_value, "System Settings", "disable_user_pass_login", 0)
		from helixhr.api import _password_login_disabled

		self.assertTrue(_password_login_disabled())
		result = self._submit(self.BANK, {"bank": "blank"}, password=None)
		self.assertEqual(result["status"], "applied")

	def test_a_fill_is_refused_for_an_inactive_employee(self):
		frappe.db.set_value("Employee", self.employee_name, "status", "Suspended")
		# An inactive record has no current employee at all, so the call
		# never reaches the fill; the endpoint's own Active check backs it.
		with self.assertRaises(Exception):
			self._submit(self.BANK, {"bank": "blank"})
		self.assertIsNone(self._live("bank_ac_no").bank_ac_no)

	def test_a_fill_needs_an_out_of_band_address(self):
		frappe.db.set_value("Employee", self.employee_name, {"company_email": None, "personal_email": None})
		self._errors(self._submit(self.BANK, {"bank": "blank"}))

	def test_a_recent_personal_email_change_holds_fills_but_not_filing(self):
		frappe.db.set_value("Employee", self.employee_name, "iban", "DE89370400440532013000")
		doc = frappe.get_doc("Employee", self.employee_name)
		doc.personal_email = "new-personal@helixhr.test"
		doc.save(ignore_permissions=True, ignore_version=False)
		errors = self._errors(self._submit({"passport_number": "Z7654321", "date_of_issue": "2020-01-01", "valid_upto": "2030-01-01", "place_of_issue": "Pune"}, {"passport": "blank"}))
		self.assertIn("72 hours", errors[0]["message"])
		result = self._submit({"bank_ac_no": self.ACCOUNT}, {"bank": "filled"}, proof=True)
		self.assertEqual(result["status"], "requested")

	def test_fill_notice_also_reaches_the_previous_personal_email(self):
		doc = frappe.get_doc("Employee", self.employee_name)
		doc.personal_email = "new-personal@helixhr.test"
		doc.save(ignore_permissions=True, ignore_version=False)
		frappe.db.delete("Version", {"ref_doctype": "Employee", "docname": self.employee_name, "creation": [">", frappe.utils.add_to_date(None, days=-30)]})
		frappe.get_doc(
			{
				"doctype": "Version",
				"ref_doctype": "Employee",
				"docname": self.employee_name,
				"data": frappe.as_json({"changed": [["personal_email", "employee-personal@helixhr.test", "new-personal@helixhr.test"]]}),
			}
		).db_insert()
		frappe.db.set_value("Version", {"ref_doctype": "Employee", "docname": self.employee_name}, "creation", frappe.utils.add_to_date(None, days=-5))
		self._submit(self.BANK, {"bank": "blank"})
		self.assertTrue(self._mails_to("employee-personal@helixhr.test"))
		self.assertTrue(self._mails_to("new-personal@helixhr.test"))

	# --- routing to HR -----------------------------------------------------

	def test_a_group_with_any_value_on_file_goes_to_hr_whole(self):
		frappe.db.set_value("Employee", self.employee_name, "bank_ac_no", "111122223333")
		result = self._submit(self.BANK, {"bank": "filled"}, proof=True)
		self.assertEqual(result["status"], "requested")
		self.assertEqual(self._live("bank_ac_no").bank_ac_no, "111122223333")
		rows = frappe.get_all("HelixHR Profile Change Row", filters={"parent": result["request"]["name"]}, pluck="field")
		self.assertEqual(set(rows), set(self.BANK))
		self.assertEqual(len(self._mails_to("employee-personal@helixhr.test")), 1)

	def test_mixed_fills_and_files_in_one_call(self):
		frappe.db.set_value("Employee", self.employee_name, "bank_ac_no", "111122223333")
		passport = {"passport_number": "Z7654321", "date_of_issue": "2020-01-01", "valid_upto": "2030-01-01", "place_of_issue": "Pune"}
		result = self._submit({**passport, "bank_ac_no": self.ACCOUNT}, {"bank": "filled", "passport": "blank"}, proof=True)
		self.assertEqual(result["status"], "applied_and_requested")
		self.assertEqual(self._live("passport_number").passport_number, "Z7654321")
		self.assertEqual(len(self._requests()), 1)
		self.assertEqual(len(self._mails_to("employee-company@helixhr.test")), 1)

	def test_hr_bound_rows_without_proof_refuse_the_fill_too(self):
		frappe.db.set_value("Employee", self.employee_name, "bank_ac_no", "111122223333")
		passport = {"passport_number": "Z7654321", "date_of_issue": "2020-01-01", "valid_upto": "2030-01-01", "place_of_issue": "Pune"}
		errors = self._errors(self._submit({**passport, "bank_ac_no": self.ACCOUNT}, {"bank": "filled", "passport": "blank"}))
		self.assertEqual(errors[0]["field"], "file")
		self.assertIsNone(self._live("passport_number").passport_number)

	def test_a_failed_request_insert_rolls_the_fill_back(self):
		from unittest.mock import patch

		frappe.db.set_value("Employee", self.employee_name, "bank_ac_no", "111122223333")
		passport = {"passport_number": "Z7654321", "date_of_issue": "2020-01-01", "valid_upto": "2030-01-01", "place_of_issue": "Pune"}
		with patch("helixhr.events._validate_new_profile_rows", side_effect=frappe.ValidationError("boom")):
			with self.assertRaises(frappe.ValidationError):
				self._submit({**passport, "bank_ac_no": self.ACCOUNT}, {"bank": "filled", "passport": "blank"}, proof=True)
		self.assertIsNone(self._live("passport_number").passport_number)

	def test_double_entry_mismatch_is_refused_without_echo(self):
		frappe.db.set_value("Employee", self.employee_name, "iban", "DE89370400440532013000")
		result = self._submit({"iban": "GB82WEST12345698765432"}, {"bank": "filled"}, proof=True, confirm={"iban": "GB82WEST12345698765433"})
		self.assertEqual(self._errors(result)[0]["field"], "iban")
		self.assertNotIn("GB82WEST12345698765432", frappe.as_json(result))

	def test_seen_blank_but_hr_filled_it_is_refused(self):
		frappe.db.set_value("Employee", self.employee_name, "bank_name", "HR Bank")
		errors = self._errors(self._submit(self.BANK, {"bank": "blank"}))
		self.assertIn("updated since you opened this", errors[0]["message"])

	def test_a_field_in_an_open_request_is_refused_others_pass(self):
		frappe.db.set_value("Employee", self.employee_name, {"bank_ac_no": "111122223333", "health_insurance_no": "H-1"})
		self._submit({"bank_ac_no": self.ACCOUNT}, {"bank": "filled"}, proof=True)
		errors = self._errors(self._submit({"bank_ac_no": "555566667777"}, {"bank": "filled"}, proof=True))
		self.assertEqual(errors[0]["field"], "bank_ac_no")
		result = self._submit({"health_insurance_no": "H-2"}, {"insurance": "filled"}, proof=True)
		self.assertEqual(result["status"], "requested")

	def test_a_legacy_open_correction_counts_as_open(self):
		frappe.db.set_value("Employee", self.employee_name, "bank_ac_no", "111122223333")
		frappe.local.request = None
		from helixhr.tests.test_hr_request import with_uploaded_file

		frappe.local.request = with_uploaded_file("bank-letter.pdf")
		frappe.set_user(EMPLOYEE_USER)
		try:
			from helixhr.api import create_my_request

			create_my_request(
				category="Profile correction",
				subject="Old style",
				operation_key=str(uuid.uuid4()),
				correction_field="bank_ac_no",
				correction_value=self.ACCOUNT,
				correction_confirm=self.ACCOUNT,
			)
		finally:
			frappe.set_user("Administrator")
		errors = self._errors(self._submit({"bank_ac_no": "555566667777"}, {"bank": "filled"}, proof=True))
		self.assertEqual(errors[0]["field"], "bank_ac_no")

	def test_the_same_key_twice_files_once(self):
		frappe.db.set_value("Employee", self.employee_name, "bank_ac_no", "111122223333")
		key = str(uuid.uuid4())
		first = self._submit({"bank_ac_no": self.ACCOUNT}, {"bank": "filled"}, proof=True, key=key)
		second = self._submit({"bank_ac_no": self.ACCOUNT}, {"bank": "filled"}, proof=True, key=key)
		self.assertEqual(first["request"]["name"], second["request"]["name"])
		self.assertFalse(second["created"])
		self.assertEqual(len(self._requests()), 1)

	def test_bad_values_are_refused_before_any_write(self):
		cases = (
			({"salary_mode": "Crypto", "bank_name": "B", "bank_ac_no": self.ACCOUNT}, {"bank": "blank"}, "salary_mode"),
			({"health_insurance_provider": "No Such Insurer", "health_insurance_no": "H-1"}, {"insurance": "blank"}, "health_insurance_provider"),
		)
		for changes, seen, field in cases:
			self.assertEqual(self._errors(self._submit(changes, seen))[0]["field"], field)
		passport = {"passport_number": "Z7654321", "date_of_issue": "2030-01-01", "valid_upto": "2020-01-01", "place_of_issue": "Pune"}
		self.assertEqual(self._errors(self._submit(passport, {"passport": "blank"}))[0]["field"], "valid_upto")
		self.assertIsNone(self._live("passport_number").passport_number)

	def test_a_field_missing_on_the_site_or_off_the_list_is_refused(self):
		for field in ("pan_number", "date_of_birth") if not frappe.get_meta("Employee").has_field("pan_number") else ("date_of_birth", "ctc"):
			self.assertEqual(self._errors(self._submit({field: "X"}, {}))[0]["field"], field)

	def test_blank_value_is_refused(self):
		self._errors(self._submit({"bank_name": ""}, {"bank": "blank"}))

	def test_update_my_profile_still_cannot_write_bank_fields(self):
		frappe.set_user(EMPLOYEE_USER)
		try:
			update_my_profile(bank_ac_no=self.ACCOUNT)
		except Exception:
			pass
		finally:
			frappe.set_user("Administrator")
		self.assertIsNone(self._live("bank_ac_no").bank_ac_no)
