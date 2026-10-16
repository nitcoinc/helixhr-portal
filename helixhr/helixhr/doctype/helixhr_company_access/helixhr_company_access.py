# Copyright (c) 2026, HelixHR Contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class HelixHRCompanyAccess(Document):
	"""One cross-company read grant per User (plan 2026-10-14-001 KTD2).

	Who may save one is decided by `api.save_company_access`, not here: this
	keeps the row itself coherent and owns its side effects, so a System
	Manager editing or deleting it in Desk gets the same checks and the same
	clean-up as the portal."""

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
		if not self.employee or not self.home_company:
			# A grant created in Desk: anchor it the way the portal does, or
			# `active_company_grant` would never apply it.
			anchor = frappe.db.get_value(
				"Employee", {"user_id": self.user, "status": "Active"}, ["name", "company"], as_dict=True
			)
			if not anchor:
				frappe.throw(_("{0} has no Active Employee record.").format(self.user))
			self.employee, self.home_company = anchor.name, anchor.company

	def on_update(self):
		# Plan 2026-10-15-001 KTD7: when defer_side_effects is set, the
		# caller runs them after commit. skip the hook.
		if not self.flags.get("defer_side_effects"):
			_after_change(self.user)

	def after_delete(self):
		_after_change(self.user)


def _after_change(user):
	"""The holder's next request sees the change, and any Ready export file
	they could no longer request is deleted (R15)."""
	from helixhr.helixhr.doctype.helixhr_report_export.helixhr_report_export import (
		expire_unreachable_exports,
	)

	frappe.clear_cache(user=user)
	expire_unreachable_exports(user)
