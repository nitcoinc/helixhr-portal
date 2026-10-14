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
			expire_export(name)

		frappe.db.delete(table, filters=(table.creation < (Now() - Interval(days=days))))


def expire_export(name):
	"""Delete one export's attached file and mark the row Expired."""
	for file_name in frappe.get_all(
		"File",
		filters={"attached_to_doctype": "HelixHR Report Export", "attached_to_name": name},
		pluck="name",
	):
		frappe.delete_doc("File", file_name, ignore_permissions=True, force=True)
	frappe.db.set_value("HelixHR Report Export", name, {"file": None, "status": "Expired"})


def expire_unreachable_exports(user):
	"""Plan 2026-10-14-001 R15: after ``user``'s access narrows, expire every
	Ready background export of theirs they could no longer request. The
	download endpoint refuses them anyway; this removes the private File
	itself, which `/private/files` would otherwise still serve its owner."""
	from helixhr.utils import resolve_report_access

	for row in frappe.get_all(
		"HelixHR Report Export",
		filters={"owner": user, "status": "Ready", "file": ["is", "set"]},
		fields=["name", "report_key", "company"],
	):
		access = resolve_report_access(user, row.report_key, row.company)
		if not access["can_export"] or access["company_removed"]:
			expire_export(row.name)
