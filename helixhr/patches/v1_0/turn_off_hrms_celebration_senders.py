"""Untick HRMS's own birthday and work-anniversary reminder checkboxes.

HelixHR owns both emails (P8-U10), and P8-KTD8 made the two HRMS checkboxes
read-only in Desk. HRMS ships `send_work_anniversary_reminders` on, so a site
that never saved the reminder in the portal kept getting HRMS's stock email
with no way for HR to stop it: disabling on Settings > Celebrations only
turns off HelixHR's sender. After this, the portal toggle is the only switch.

Idempotent; also run from `install.after_install` for fresh sites.
"""

import frappe


def execute():
	from helixhr.reminders import EVENTS

	for spec in EVENTS.values():
		frappe.db.set_single_value("HR Settings", spec["hrms_field"], 0)
	frappe.clear_document_cache("HR Settings", "HR Settings")
