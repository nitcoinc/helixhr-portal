"""Untick HRMS's `send_leave_notification` (plan 2026-10-02-001 U9 / R21).

HelixHR sends every leave email (approver on a new request, employee on a
decision and on a cancellation) through its own templates, so HRMS's
Desk-worded copies would be duplicates. `events.hr_settings_validate`
refuses re-enabling it and preflight FAILs while it is on. Idempotent; also
run from `install.after_install` for fresh sites.
"""

import frappe


def execute():
	frappe.db.set_single_value("HR Settings", "send_leave_notification", 0)
	frappe.clear_document_cache("HR Settings", "HR Settings")
