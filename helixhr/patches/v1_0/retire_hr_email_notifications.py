"""Delete the four HR-queue email Notification fixtures (plan 2026-10-02-001
U9 / KTD9).

HR's "waiting for you" mail is now a templated send hung on doc events
(`helixhr.events`), so these would be a second, untemplated copy. Removing
them from `fixtures/notification.json` does not delete existing rows --
fixture sync never deletes -- hence this patch. The bell (System
Notification) fixtures stay. Idempotent; also run from
`install.after_install` for fresh sites.
"""

import frappe

RETIRED_NOTIFICATIONS = (
	"HelixHR Leave Sent To HR",
	"HelixHR New Leave For HR",
	"HelixHR Timesheet Sent To HR",
	"HelixHR Attendance Request Sent To HR",
)


def execute():
	for name in RETIRED_NOTIFICATIONS:
		if frappe.db.exists("Notification", name):
			frappe.delete_doc("Notification", name, ignore_permissions=True, force=True)
