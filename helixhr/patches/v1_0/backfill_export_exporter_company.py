"""Plan 2026-10-14-001 U9/KTD12: backfill `exporter_company` on existing
HelixHR Report Export rows from each owner's Active Employee company.

Idempotent -- only rows with no `exporter_company` are touched, so a rerun
never overwrites a value the export itself recorded. Rows whose owner has
no Active Employee (Administrator exports, offboarded owners) keep the
export log's data-company clause as their only reach signal.
Also called from `install.py`, because `--install-app` marks patches
complete without running them.
"""

import frappe


def execute():
	if not frappe.db.table_exists("HelixHR Report Export"):
		return
	if not frappe.db.has_column("HelixHR Report Export", "exporter_company"):
		return
	for row in frappe.get_all(
		"HelixHR Report Export",
		filters={"exporter_company": ["is", "not set"]},
		fields=["name", "owner"],
	):
		company = frappe.db.get_value(
			"Employee", {"user_id": row.owner, "status": "Active"}, "company"
		)
		if company:
			frappe.db.set_value(
				"HelixHR Report Export", row.name, "exporter_company", company, update_modified=False
			)
