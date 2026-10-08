"""Plan 2026-10-08-001 U2: carry today's sends into `last_sent_on`.

Before this patch the once-a-day guard was a Redis key per (system date,
event, company). The reminders now claim `last_sent_on` on the row instead,
so a company the old daily job already mailed today would be mailed again on
the first 15-minute tick after deploy. This copies today's keys onto the
rows. A company whose own date is already ahead of the system date has no
matching key, and its new day's mail is correctly due. Idempotent.
"""

import frappe
from frappe.utils import getdate

from helixhr.reminders import CELEBRATION_GUARD_PREFIX, REMINDER_DOCTYPE


def execute():
	today = getdate()
	for row in frappe.get_all(REMINDER_DOCTYPE, fields=["name", "event", "company", "last_sent_on"]):
		if row.last_sent_on and getdate(row.last_sent_on) >= today:
			continue
		if frappe.cache.get_value(f"{CELEBRATION_GUARD_PREFIX}{today}|{row.event}|{row.company}"):
			frappe.db.set_value(REMINDER_DOCTYPE, row.name, "last_sent_on", today, update_modified=False)
