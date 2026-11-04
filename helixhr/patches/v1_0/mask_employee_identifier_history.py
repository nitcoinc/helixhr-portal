"""Mask bank and ID values already in Employee change history (plan
2026-11-03-001 U8, R19).

New Versions are masked as they are written (`events.version_before_insert`);
this walks the ones written before that. Idempotent: a masked value masks to
itself, so a re-run writes nothing. `personal_email` entries are left alone
and no Version row is deleted.
"""

import frappe

from helixhr.events import mask_employee_version_data
from helixhr.utils import PROFILE_MASKED_FIELDS


def execute():
	or_filters = [["data", "like", f"%{field}%"] for field in sorted(PROFILE_MASKED_FIELDS)]
	for name in frappe.get_all(
		"Version", filters={"ref_doctype": "Employee"}, or_filters=or_filters, pluck="name"
	):
		masked = mask_employee_version_data(frappe.db.get_value("Version", name, "data"))
		if masked is not None:
			frappe.db.set_value("Version", name, "data", masked, update_modified=False)
