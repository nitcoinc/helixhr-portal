import frappe
from frappe.client import get_value as client_get_value
from frappe.tests import IntegrationTestCase

from helixhr.tests.utils import EMPLOYEE_USER, make_test_employee_and_manager


class TestEmployeePermlevel(IntegrationTestCase):
	def setUp(self):
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()
		# No commit() here -- see test_leave_flow.py's setUp for why a real
		# commit() inside a test breaks IntegrationTestCase's per-test
		# rollback isolation. Same-connection visibility doesn't need it.
		frappe.db.set_value("Employee", self.employee_name, "bank_ac_no", "ACCT-SECRET-123")

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

		# Known starting value, not an assumed-empty default: this suite's
		# fixtures are not guaranteed a clean slate between test *files*
		# within one `bench run-tests` invocation (confirmed while
		# building U6 -- state committed by an earlier test file's setUp
		# was still visible here).
		original = frappe.db.get_value("Employee", self.employee_name, "leave_approver")

		frappe.set_user(EMPLOYEE_USER)
		doc = frappe.get_doc("Employee", self.employee_name)
		doc.leave_approver = MANAGER_USER
		doc.save()

		self.assertEqual(frappe.db.get_value("Employee", self.employee_name, "leave_approver"), original)

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

	def test_regional_payroll_fields_are_hr_only(self):
		"""HRMS's India setup adds PAN, IFSC, MICR and PF account as Custom
		Fields at permlevel 0 -- employee-writable -- whenever an Indian
		company is created, which can be long after install. The fixture
		Property Setters lock them to level 2 whenever they exist. A fresh CI
		site has no Indian company, so the fields are created here."""
		from frappe.custom.doctype.custom_field.custom_field import create_custom_field

		regional = ("pan_number", "ifsc_code", "micr_code", "provident_fund_account")
		for fieldname in regional:
			if frappe.get_meta("Employee").has_field(fieldname):
				continue
			create_custom_field(
				"Employee", {"fieldname": fieldname, "label": fieldname, "fieldtype": "Data", "insert_after": "bank_ac_no"}
			)
			self.addCleanup(_drop_custom_field, f"Employee-{fieldname}")
		# Deleting a Custom Field also deletes its Property Setters
		# (`CustomField.on_trash`), so an earlier run's cleanup may have
		# removed the fixture rows: put them back exactly as migrate does.
		_import_fixture_setters(f"Employee-{fieldname}-permlevel" for fieldname in regional)
		frappe.clear_cache(doctype="Employee")
		self.addCleanup(frappe.clear_cache, doctype="Employee")

		meta = frappe.get_meta("Employee")
		for fieldname in regional:
			self.assertEqual(meta.get_field(fieldname).permlevel, 2, fieldname)

		frappe.db.set_value("Employee", self.employee_name, "pan_number", "ABCDE1234F")
		frappe.set_user(EMPLOYEE_USER)
		doc = frappe.get_doc("Employee", self.employee_name)
		doc.pan_number = "ZZZZZ9999Z"
		doc.save()
		self.assertEqual(frappe.db.get_value("Employee", self.employee_name, "pan_number"), "ABCDE1234F")


def _drop_custom_field(name):
	"""Undo a test-created Custom Field for good. Adding the field ran DDL,
	which MariaDB commits implicitly, so the per-test rollback would restore
	the Custom Field row but not undo the column -- delete and commit
	instead, leaving the site as it was."""
	frappe.set_user("Administrator")
	frappe.delete_doc("Custom Field", name, ignore_permissions=True)
	# The delete took the field's fixture Property Setter with it; restore it
	# so the site keeps what migrate installed.
	_import_fixture_setters([f"{name}-permlevel"])
	frappe.db.commit()  # nosemgrep
	frappe.clear_cache(doctype="Employee")


def _import_fixture_setters(names):
	"""Insert the named Property Setters from helixhr's fixture file if the
	site lacks them -- what `bench migrate`'s fixture sync would do."""
	import json

	wanted = set(names)
	path = frappe.get_app_path("helixhr", "fixtures", "property_setter.json")
	with open(path) as handle:
		rows = [row for row in json.load(handle) if row["name"] in wanted]
	for row in rows:
		if not frappe.db.exists("Property Setter", row["name"]):
			frappe.get_doc(row).insert(ignore_permissions=True)
