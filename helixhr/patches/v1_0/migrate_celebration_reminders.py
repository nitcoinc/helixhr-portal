"""P8-U10 originally: carried HR Settings' two celebration Custom Fields
(`helixhr_birthday_template`, `helixhr_anniversary_template`) into
`HelixHR Celebration Reminder` rows, one per event.

Plan 2026-10-04-004 U1 rewrites it for the per-company model: the rows it
creates are now one per (event, company) -- R14's "copied to every
company", since the old fields were global. It only ever runs for a site
coming from before P8 (a P8-era site already has its rows in the patch
log, and `split_celebration_reminders_by_company` handles those), and it
steps aside entirely when *any* reminder row exists: rows already present
means a previous migration ran, and an HR edit or deletion made after
that must not be resurrected from the stale Custom Fields.

A fresh install has neither the Custom Fields nor any row, so this creates
nothing there -- absent means disabled, and a fresh site starts sending
nothing (R1). Idempotent, and still called from `install.after_install`
for the `install-app`-on-existing-site path.
"""

import frappe

_FIELD_TO_EVENT = {
	"helixhr_birthday_template": "birthday",
	"helixhr_anniversary_template": "work_anniversary",
}


def execute():
	if not frappe.db.exists("DocType", "HelixHR Celebration Reminder"):
		return
	# Any row at all -- global or per company -- says a previous migration
	# ran. See the docstring for the resurrected-deletion case this avoids.
	if frappe.db.count("HelixHR Celebration Reminder"):
		return

	# `frappe.db.has_column` assumes a physical table -- HR Settings is a
	# Single doctype (values live in `tabSingles`, not a table of its own),
	# so the field's existence is checked through the doctype meta instead.
	has_old_fields = frappe.get_meta("HR Settings").has_field("helixhr_birthday_template")
	if not has_old_fields:
		return

	for company in frappe.get_all("Company", pluck="name"):
		for field, event in _FIELD_TO_EVENT.items():
			template = frappe.db.get_single_value("HR Settings", field)
			if not frappe.db.exists("HelixHR Celebration Reminder", f"{event}-{company}"):
				frappe.get_doc(
					{
						"doctype": "HelixHR Celebration Reminder",
						"event": event,
						"company": company,
						"email_template": template,
						"is_enabled": 1 if template else 0,
						"recipient_mode": "All employees",
					}
				).insert(ignore_permissions=True)
