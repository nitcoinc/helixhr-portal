# Copyright (c) 2026, HelixHR Contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class HelixHRLeaveRules(Document):
	"""The backdated leave rule (plan 2026-10-06-001 U1): how far back an
	employee may start leave, editable from portal Settings' Leave rules tab
	instead of site config.

	Validation lives here, not in the API, so a Desk save is held to the same
	bounds as the portal's own form."""

	def validate(self):
		grace = cint(1 if self.backdated_grace_days is None else self.backdated_grace_days)
		if grace < 0 or grace > 365:
			frappe.throw(_("Grace days must be between 0 and 365."))
		self.backdated_grace_days = grace
		self.backdated_exempt_role = (self.backdated_exempt_role or "").strip()
