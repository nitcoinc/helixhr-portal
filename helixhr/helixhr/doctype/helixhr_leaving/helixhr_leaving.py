# Copyright (c) 2026, HelixHR Contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

# Pending and Failed are open: HR can still change, cancel or retry them.
OPEN_STATUSES = ("Pending", "Failed")


class HelixHRLeaving(Document):
	"""One leaving scheduled from the portal (plan 2026-10-22-001 KTD2).

	Like the access change log, only System Manager holds a DocPerm, and
	read-only: `helixhr.api.relieve_employee`, `cancel_leaving`,
	`retry_leaving` and `helixhr.tasks.apply_due_leavings` write it with
	``ignore_permissions`` after their own scope gate. The rules below hold
	for every route, a Desk insert included."""

	def validate(self):
		before = self.get_doc_before_save()
		if before and before.status not in OPEN_STATUSES:
			# R12: an applied leaving is not undone from here.
			frappe.throw(_("This leaving is {0} and can't be changed.").format(_(before.status)))
		if self.status in OPEN_STATUSES and frappe.db.exists(
			"HelixHR Leaving",
			{"employee": self.employee, "status": ["in", OPEN_STATUSES], "name": ["!=", self.name]},
		):
			frappe.throw(_("{0} already has a leaving scheduled.").format(self.employee_name or self.employee))
