# Copyright (c) 2026, HelixHR Contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class HelixHRAccessChange(Document):
	"""One access change: who changed whose access, why, and the target's
	assignment rows before and after as JSON (plan 2026-10-19-001 KTD8).

	Insert-only. History and the migration's re-run guard read these rows,
	so an edited one would rewrite the record. Like the assignment, only
	System Manager holds a DocPerm, read/report; the save endpoint and the
	migration insert with ``ignore_permissions``."""

	def validate(self):
		if not self.is_new():
			frappe.throw(_("An access change is a record and cannot be edited."))
