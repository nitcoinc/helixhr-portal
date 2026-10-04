"""Default HelixHR Report Access rows (plan 2026-10-04-001 U2).

Creates a row only for a catalog key that has `default_grants` and no row
yet -- never overwrites HR's edits, never recreates a row HR deleted on a
re-run of the same patch line. A later unit that adds catalog entries adds a
dated re-run line in patches.txt. New-site installs mark patches complete, so
``helixhr.install.after_install`` calls this module too.
"""

import frappe

from helixhr.reports import CATALOG


def execute():
	if not frappe.db.table_exists("HelixHR Report Access"):
		return
	for entry in CATALOG:
		if not entry["default_grants"] or frappe.db.exists("HelixHR Report Access", entry["key"]):
			continue
		frappe.get_doc(
			{"doctype": "HelixHR Report Access", "report_key": entry["key"], **entry["default_grants"]}
		).insert(ignore_permissions=True)
