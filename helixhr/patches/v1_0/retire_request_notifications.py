"""Disable obsolete request Notification fixtures on existing sites."""

import frappe

RETIRED_NOTIFICATIONS = (
	"HelixHR New Request For HR",
	"HelixHR Request Status Changed",
)


def execute():
	for name in RETIRED_NOTIFICATIONS:
		if frappe.db.exists("Notification", name):
			frappe.db.set_value("Notification", name, "enabled", 0)
