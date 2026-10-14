# Copyright (c) 2026, HelixHR Contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class HelixHRCompanyAccess(Document):
	"""One cross-company read grant per User (plan 2026-10-14-001 KTD2).

	Who may save one is decided by `api.save_company_access`, not here: this
	only keeps the row itself coherent, so a System Manager editing it in
	Desk gets the same shape checks."""

	def validate(self):
		companies = [row.company for row in self.companies]
		if not companies:
			frappe.throw(_("Choose at least one company."))
		if len(set(companies)) != len(companies):
			frappe.throw(_("Each company can be named only once."))
		for company in companies:
			if not frappe.db.exists("Company", company):
				frappe.throw(_("{0} is not a company.").format(company))
		if self.enabled:
			self.disabled_reason = None
