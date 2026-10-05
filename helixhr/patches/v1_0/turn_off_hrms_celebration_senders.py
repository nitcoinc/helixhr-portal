"""Untick HRMS's own reminder checkboxes -- birthday, work anniversary
(P8-U10 / P8-KTD8) and, since plan 2026-10-04-004 U3, the holiday reminder
(KTD7): HelixHR owns all three emails now, and the HR Settings checkboxes
are read-only in Desk. HRMS ships `send_work_anniversary_reminders` and
`send_holiday_reminders` on, so a site that never configured the reminder
kept getting HRMS's stock email with no way for HR to stop it: disabling in
the portal only turns off HelixHR's sender. After this, the portal toggle
is the only switch. This one patch covers the takeover because it loops
`reminders.EVENTS`, which the holiday event joins; a separate
`turn_off_hrms_holiday_reminders` patch would be the same loop twice.

Idempotent; also run from `install.after_install` for fresh sites.
"""

import frappe


def execute():
	from helixhr.reminders import EVENTS

	for spec in EVENTS.values():
		frappe.db.set_single_value("HR Settings", spec["hrms_field"], 0)
	frappe.clear_document_cache("HR Settings", "HR Settings")
