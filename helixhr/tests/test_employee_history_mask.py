import frappe
from frappe.tests import IntegrationTestCase

from helixhr.tests.utils import make_test_employee_and_manager


class TestEmployeeHistoryMask(IntegrationTestCase):
	"""Plan 2026-11-03-001 U8 (R19): Employee history records that a bank or
	ID field changed, by whom and when, never the full value."""

	def setUp(self):
		self.employee_name = make_test_employee_and_manager()[0]
		frappe.db.set_value(
			"Employee", self.employee_name, {"bank_ac_no": "111122223333", "personal_email": "old@helixhr.test"}
		)
		frappe.db.delete("Version", {"ref_doctype": "Employee", "docname": self.employee_name})

	def _latest(self):
		row = frappe.get_all(
			"Version",
			filters={"ref_doctype": "Employee", "docname": self.employee_name},
			fields=["data", "owner", "creation"],
			order_by="creation desc",
			limit=1,
		)[0]
		return row, frappe.parse_json(row.data)["changed"]

	def test_a_desk_change_is_masked_with_who_and_when_kept(self):
		doc = frappe.get_doc("Employee", self.employee_name)
		doc.bank_ac_no = "987654321098"
		doc.personal_email = "new@helixhr.test"
		doc.save(ignore_version=False)
		row, changed = self._latest()
		entries = {entry[0]: entry for entry in changed}
		self.assertEqual(entries["bank_ac_no"], ["bank_ac_no", "••••3333", "••••1098"])
		# The 72h hold reads personal_email history, so it stays whole.
		self.assertEqual(entries["personal_email"], ["personal_email", "old@helixhr.test", "new@helixhr.test"])
		self.assertEqual(row.owner, "Administrator")
		self.assertTrue(row.creation)
		self.assertNotIn("987654321098", row.data)

		from helixhr.events import _personal_email_changed_recently

		self.assertTrue(_personal_email_changed_recently(self.employee_name))

	def test_a_masking_failure_blocks_the_save(self):
		from unittest.mock import patch

		doc = frappe.get_doc("Employee", self.employee_name)
		doc.bank_ac_no = "987654321098"
		with patch("helixhr.events.mask_employee_version_data", side_effect=RuntimeError("boom")):
			with self.assertRaises(RuntimeError):
				doc.save(ignore_version=False)

	def test_the_patch_masks_existing_rows_once(self):
		from helixhr.patches.v1_0 import mask_employee_identifier_history

		raw = frappe.as_json(
			{
				"changed": [
					["iban", None, "GB82WEST12345698765432"],
					["personal_email", "a@helixhr.test", "b@helixhr.test"],
				]
			}
		)
		version = frappe.get_doc(
			{"doctype": "Version", "ref_doctype": "Employee", "docname": self.employee_name, "data": raw}
		)
		version.db_insert()  # Skips the hook, as rows written before it did.
		mask_employee_identifier_history.execute()
		data = frappe.db.get_value("Version", version.name, "data")
		changed = {entry[0]: entry for entry in frappe.parse_json(data)["changed"]}
		self.assertEqual(changed["iban"], ["iban", None, "••••5432"])
		self.assertEqual(changed["personal_email"][2], "b@helixhr.test")
		modified = frappe.db.get_value("Version", version.name, "modified")
		mask_employee_identifier_history.execute()
		self.assertEqual(frappe.db.get_value("Version", version.name, "data"), data)
		self.assertEqual(frappe.db.get_value("Version", version.name, "modified"), modified)
