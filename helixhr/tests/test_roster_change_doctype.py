"""Plan 2026-10-31-001 U7 / KTD7: the HelixHR Roster Change controller's
guard. Kept apart from `test_roster_ops` so the pure simulator suite runs
before the DocType is migrated."""

import frappe
from frappe.tests import IntegrationTestCase

from helixhr.tests.utils import make_test_employee_and_manager

DOCTYPE = "HelixHR Roster Change"


class TestRosterChangeGuard(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.report, _, cls.manager, cls.manager_user = make_test_employee_and_manager()

	def _draft(self, **fields):
		doc = frappe.get_doc({"doctype": DOCTYPE, "employee": self.manager, **fields})
		doc.insert(ignore_permissions=True)
		return doc

	def test_draft_to_approved_without_the_api_flag_is_refused(self):
		doc = self._draft()
		doc.status = "Approved"
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_draft_to_pending_without_the_api_flag_is_refused(self):
		doc = self._draft()
		doc.status = "Pending"
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_a_desk_insert_must_start_as_a_draft(self):
		with self.assertRaises(frappe.ValidationError):
			self._draft(status="Pending")

	def test_the_api_flag_still_follows_the_lifecycle(self):
		doc = self._draft()
		doc.flags.helixhr_roster_api = True
		doc.status = "Approved"
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)
		# The refused save already stamped a new `modified` in memory.
		doc.reload()
		doc.flags.helixhr_roster_api = True
		doc.status = "Pending"
		doc.save(ignore_permissions=True)
		self.assertEqual(frappe.db.get_value(DOCTYPE, doc.name, "status"), "Pending")

	def test_decision_fields_need_the_api_flag(self):
		doc = self._draft()
		doc.decision_note = "Looks fine"
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_the_note_stays_editable(self):
		doc = self._draft()
		doc.note = "Cover for the holiday week"
		doc.save(ignore_permissions=True)

	def test_a_manager_cannot_request_their_own_shifts(self):
		with self.assertRaises(frappe.ValidationError):
			self._draft(
				rows=[{"employee": self.manager, "action": "End", "to_date": "2099-01-31"}],
			)
