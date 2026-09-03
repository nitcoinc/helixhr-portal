import frappe
from frappe.client import get_value as client_get_value
from frappe.tests import IntegrationTestCase

from helixhr.tests.utils import EMPLOYEE_USER, make_test_employee_and_manager


class TestEmployeePermlevel(IntegrationTestCase):
	def setUp(self):
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()
		frappe.db.set_value("Employee", self.employee_name, "bank_ac_no", "ACCT-SECRET-123")
		frappe.db.commit()  # nosemgrep

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_locked_field_write_is_silently_reset(self):
		"""AE1: PUT department as an ESS user succeeds, but department is
		unchanged -- Frappe resets a higher-permlevel field instead of
		rejecting the request (KTD6), so the assertion is "unchanged", not
		"refused"."""
		original = frappe.db.get_value("Employee", self.employee_name, "department")

		frappe.set_user(EMPLOYEE_USER)
		doc = frappe.get_doc("Employee", self.employee_name)
		doc.department = "Some Other Department"
		doc.save()

		self.assertEqual(frappe.db.get_value("Employee", self.employee_name, "department"), original)

	def test_hr_only_field_is_unreadable(self):
		"""AE1: bank_ac_no is absent for an ESS user, not just empty --
		checked through frappe.client.get_value, the same permission-aware
		path the REST API uses, not the raw frappe.db.get_value."""
		frappe.set_user(EMPLOYEE_USER)
		result = client_get_value("Employee", "bank_ac_no", self.employee_name)

		self.assertNotIn("bank_ac_no", result)

	def test_table_field_child_row_write_is_reset(self):
		frappe.set_user("Administrator")
		before_rows = frappe.db.count("Employee Education", {"parent": self.employee_name})

		frappe.set_user(EMPLOYEE_USER)
		doc = frappe.get_doc("Employee", self.employee_name)
		doc.append("education", {"school_univ": "Sneaky University", "qualification": "Other"})
		doc.save()

		after_rows = frappe.db.count("Employee Education", {"parent": self.employee_name})
		self.assertEqual(before_rows, after_rows)

	def test_custom_field_write_is_also_reset(self):
		"""HRMS adds leave_approver, employment_type, grade, default_shift
		etc. to Employee as Custom Field records, not core DocField rows --
		a distinct doctype the earlier permlevel pass first missed. Cover
		one of them directly so a future HRMS upgrade that adds another
		custom field is at least caught here if someone copies this
		pattern, even though it can't catch a field this suite has never
		heard of."""
		from helixhr.tests.utils import MANAGER_USER

		frappe.set_user(EMPLOYEE_USER)
		doc = frappe.get_doc("Employee", self.employee_name)
		doc.leave_approver = MANAGER_USER
		doc.save()

		self.assertIsNone(frappe.db.get_value("Employee", self.employee_name, "leave_approver"))

	def test_employee_a_cannot_change_employee_b(self):
		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.PermissionError):
			doc = frappe.get_doc("Employee", self.manager_name)
			doc.cell_number = "+1-555-9999"
			doc.save()

	def test_hr_manager_can_still_edit_locked_and_hr_only_fields(self):
		frappe.set_user("Administrator")
		hr_manager_user = "hr-manager@helixhr.test"
		if not frappe.db.exists("User", hr_manager_user):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": hr_manager_user,
					"first_name": "HR",
					"last_name": "Manager",
					"send_welcome_email": 0,
					"roles": [{"doctype": "Has Role", "role": "HR Manager"}],
				}
			).insert(ignore_permissions=True)

		frappe.set_user(hr_manager_user)
		doc = frappe.get_doc("Employee", self.employee_name)
		# family_background: an ordinary permlevel-1 field, and unlike
		# employee_name it isn't recomputed by Employee.validate() from
		# first/middle/last name, so a direct write actually sticks.
		doc.family_background = "HR-set background"
		doc.bank_ac_no = "ACCT-HR-SET"
		doc.save()

		frappe.set_user("Administrator")
		self.assertEqual(
			frappe.db.get_value("Employee", self.employee_name, "family_background"), "HR-set background"
		)
		self.assertEqual(
			frappe.db.get_value("Employee", self.employee_name, "bank_ac_no"), "ACCT-HR-SET"
		)
