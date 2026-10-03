# Copyright (c) 2026, HelixHR Contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class HelixHRReportAccess(Document):
	"""One row per catalog report key (plan 2026-10-04-001 KTD4). A missing
	row means deny for HR User and HelixHR Delivery Manager."""

	def validate(self):
		from helixhr.reports import get_entry

		entry = get_entry(self.report_key)
		if not entry:
			frappe.throw(_("{0} is not a report in the catalog.").format(self.report_key))
		if self.hr_user_export and not self.hr_user_run:
			frappe.throw(_("HR User cannot export a report they cannot run."))
		if self.dm_export and not self.dm_run:
			frappe.throw(_("Delivery Manager cannot export a report they cannot run."))
		# R21: Delivery Manager results are project-scoped, so only an entry
		# that supports project scope may be granted to them.
		if (self.dm_run or self.dm_export) and "project" not in entry["scopes"]:
			frappe.throw(
				_("{0} has no project scope, so it cannot be granted to Delivery Manager.").format(
					entry["label"]
				)
			)
