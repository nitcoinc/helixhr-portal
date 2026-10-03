"""Plan 2026-10-02-001 U4 / KTD13: `helixhr_pending_since` for rows already
pending when the field arrived. `modified` is the best value on hand; from
here on `events.stamp_pending_since` writes it. Only empty values are filled,
so a rerun never moves a real stamp."""

import frappe

from helixhr.events import (
	PENDING_STATE,
	REQUEST_PENDING_HR,
	REQUEST_PENDING_MANAGER,
	TIMESHEET_PENDING_HR,
)

_PENDING = {
	"Leave Application": {"docstatus": 0, "status": "Open"},
	"Timesheet": {"docstatus": 0, "workflow_state": ["in", [PENDING_STATE, TIMESHEET_PENDING_HR]]},
	"Attendance Request": {
		"docstatus": 0,
		"workflow_state": ["in", [REQUEST_PENDING_MANAGER, REQUEST_PENDING_HR]],
	},
}


def execute():
	for doctype, filters in _PENDING.items():
		if not frappe.db.has_column(doctype, "helixhr_pending_since"):
			continue
		for row in frappe.get_all(
			doctype,
			filters={**filters, "helixhr_pending_since": ["is", "not set"]},
			fields=["name", "modified"],
		):
			frappe.db.set_value(
				doctype, row.name, "helixhr_pending_since", row.modified, update_modified=False
			)
