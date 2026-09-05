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
