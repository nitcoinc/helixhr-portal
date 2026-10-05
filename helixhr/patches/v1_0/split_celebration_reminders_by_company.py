"""Plan 2026-10-04-004 U1: P8's global one-row-per-event
`HelixHR Celebration Reminder` model becomes one row per (event, company).

R14: every existing global setting is copied to *every* company, so mail
that sends today keeps sending after the change -- a company that should
not receive it is switched off afterwards, which the deploy notes say
explicitly. Recipients in "Selected employees" mode are filtered per
company: a selected person only joins the row of the company they belong
to (the sender never mailed a cross-company selected person anyway --
P8-U11's narrowing). That also means a Selected-mode global row is copied
only to the companies its recipients belong to: a company with nobody
selected receives no mail today either, so giving it an empty Selected
row would be both refused (the controller refuses an empty selection)
and meaningless.

Idempotent: skip when no global row remains, and never overwrite a
per-company row that already exists. Called from `install.after_install`
too (patches are marked done without running on a fresh install) -- where
it finds no global row and does nothing, matching the plan's decision
that a fresh install creates no rows at all: absent means disabled.

The global rows themselves are deleted once copied, so
`reminders.send_celebration_reminders` (which now reads per company) and
`preflight.check_celebration_reminders` never see a row that belongs to
no company. They are identified by name -- P8's `autoname: field:event`
named them exactly `birthday` / `work_anniversary` -- which is precise
where a "company is unset" filter would also catch a row whose company
was wiped by hand.
"""

import frappe

GLOBAL_FIELDS = ("event", "is_enabled", "email_template", "recipient_mode")
# P8's own event vocabulary: the only two names a global row could have.
GLOBAL_NAMES = ("birthday", "work_anniversary")


def execute():
	if not frappe.db.exists("DocType", "HelixHR Celebration Reminder"):
		return

	globals_ = frappe.get_all(
		"HelixHR Celebration Reminder",
		filters={"name": ["in", GLOBAL_NAMES]},
		fields=["name", *GLOBAL_FIELDS],
	)
	if not globals_:
		return

	companies = frappe.get_all("Company", pluck="name")
	for row in globals_:
		selected = _selected(row.name)
		for company in companies:
			if row.recipient_mode == "Selected employees":
				recipients = [r.employee for r in selected if _belongs(r.employee, company)]
				if not recipients:
					continue
			else:
				recipients = None
			name = f"{row.event}-{company}"
			if frappe.db.exists("HelixHR Celebration Reminder", name):
				continue
			frappe.get_doc(
				{
					"doctype": "HelixHR Celebration Reminder",
					"event": row.event,
					"company": company,
					"is_enabled": row.is_enabled,
					"email_template": row.email_template,
					"recipient_mode": row.recipient_mode,
					"recipients": (
						[{"employee": employee} for employee in recipients] if recipients else []
					),
				}
			).insert(ignore_permissions=True)
		frappe.delete_doc("HelixHR Celebration Reminder", row.name, force=True, ignore_permissions=True)


def _selected(parent):
	return frappe.get_all(
		"HelixHR Celebration Recipient",
		filters={"parent": parent, "parenttype": "HelixHR Celebration Reminder"},
		fields=["employee"],
	)


def _belongs(employee, company):
	return frappe.db.get_value("Employee", employee, "company") == company
