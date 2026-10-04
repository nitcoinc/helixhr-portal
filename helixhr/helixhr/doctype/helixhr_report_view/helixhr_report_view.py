# Copyright (c) 2026, HelixHR Contributors
# For license information, please see license.txt

from frappe.model.document import Document


class HelixHRReportView(Document):
	"""A saved report view (plan 2026-10-04-001 U12). Owner-only DocPerm;
	shared views are listed and deleted through `helixhr.api`, which also
	validates ``query`` against the catalog entry and keeps ``label``
	unique per owner and report."""
