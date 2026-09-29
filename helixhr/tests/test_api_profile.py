import frappe
from frappe.tests import IntegrationTestCase

from helixhr.api import update_my_profile
from helixhr.tests.utils import EMPLOYEE_USER, make_test_employee_and_manager


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
