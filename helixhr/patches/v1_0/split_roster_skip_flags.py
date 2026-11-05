"""Split the one roster skip into Skip weekends and Skip holidays (plan
2026-11-06-002 U1, KTD5, R8).

A row saved before the split with `skip_off_days = 1` meant both skips, so
it gets both new flags. Idempotent: only rows with both new flags still 0
are written, and `skip_off_days` itself is left as it was.
"""

import frappe


def execute():
	frappe.db.set_value(
		"HelixHR Roster Change Row",
		{"skip_off_days": 1, "skip_weekends": 0, "skip_holidays": 0},
		{"skip_weekends": 1, "skip_holidays": 1},
		update_modified=False,
	)
