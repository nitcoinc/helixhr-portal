# Copyright (c) 2026, HelixHR Contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.query_builder import Interval
from frappe.query_builder.functions import Now

# Resolved decision 4: an export's file is kept 7 days, its audit row a year
# (the row's retention is `default_log_clearing_doctypes` in hooks.py, which
# Log Settings passes to `clear_old_logs` as ``days``).
FILE_RETENTION_DAYS = 7


class HelixHRReportExport(Document):
	"""Audit row for one report export (plan 2026-10-04-001 KTD10). Written by
	`helixhr.api.request_export` with ignore_permissions; DocPerm is owner-only
	for every role, so a background export's attached private File stays
	downloadable by its requester alone. HR reads the log through
	`helixhr.api.get_export_log`, which returns metadata, never the file."""

	@staticmethod
	def clear_old_logs(days=365):
		table = frappe.qb.DocType("HelixHR Report Export")

		# Files first: older than FILE_RETENTION_DAYS -> file deleted, row Expired.
		stale = frappe.get_all(
			"HelixHR Report Export",
			filters={
				"file": ["is", "set"],
				"creation": ["<", frappe.utils.add_days(frappe.utils.now_datetime(), -FILE_RETENTION_DAYS)],
			},
			pluck="name",
		)
		for name in stale:
			for file_name in frappe.get_all(
				"File",
				filters={"attached_to_doctype": "HelixHR Report Export", "attached_to_name": name},
				pluck="name",
			):
				frappe.delete_doc("File", file_name, ignore_permissions=True, force=True)
			frappe.db.set_value("HelixHR Report Export", name, {"file": None, "status": "Expired"})

		frappe.db.delete(table, filters=(table.creation < (Now() - Interval(days=days))))
