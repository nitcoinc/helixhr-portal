"""Plan 2026-10-05-001 U11 (KTD12): every (event, company) celebration row
gets its own Email Template, so one company's edit stops reaching every
other company's mail.

Before this, `save_celebration_reminder` wrote into the one shared seeded
template per event (`seed_celebration_templates`), and every company's row
pointed at it. For each row not already on its per-company name
(`reminders.celebration_template_name`):

- the per-company template is created from whatever the row sends today --
  except that text still byte-for-byte equal to the old seeded default is
  replaced by the new body-only default (`reminders.CELEBRATION_DEFAULTS`),
  so unedited sites get the new wording and customised text survives;
- the row is repointed at it.

The shared templates are left in place (nothing reads them afterwards, and
deleting site data a patch did not create is not this patch's call).

A row with no template yet (an idle, never-configured row) is left alone.
A customised source that no longer renders in the new sandbox (e.g. it
calls `frappe.*`) is cloned as the default instead, with an Error Log
naming it, so the company keeps receiving mail.

Idempotent: a row already on its per-company name is skipped, and an
existing per-company template is never overwritten. Called from
`install.after_install` too, where there are no rows and it does nothing.
"""

import frappe


def execute():
	if not frappe.db.exists("DocType", "HelixHR Celebration Reminder"):
		return

	from helixhr.patches.v1_0.seed_celebration_templates import TEMPLATES as SEEDED
	from helixhr.reminders import CELEBRATION_DEFAULTS, celebration_template_name

	seeded = {spec["name"]: spec for spec in SEEDED}
	rows = frappe.get_all(
		"HelixHR Celebration Reminder", fields=["name", "event", "company", "email_template"]
	)
	for row in rows:
		if row.event not in CELEBRATION_DEFAULTS or not row.company or not row.email_template:
			continue
		target = celebration_template_name(row.event, row.company)
		if row.email_template == target:
			continue
		if not frappe.db.exists("Email Template", target):
			_create(target, row, seeded, CELEBRATION_DEFAULTS[row.event])
		frappe.db.set_value(
			"HelixHR Celebration Reminder", row.name, "email_template", target, update_modified=False
		)


def _create(target, row, seeded, default):
	values = {"subject": default["subject"], "use_html": 1, "response_html": default["body"]}
	if row.email_template and frappe.db.exists("Email Template", row.email_template):
		source = frappe.get_doc("Email Template", row.email_template)
		spec = seeded.get(source.name)
		unedited = (
			spec is not None
			and source.use_html
			and source.subject == spec["subject"]
			and source.response_html == spec["response_html"]
		)
		if not unedited and _renders(source, row):
			values = {
				"subject": source.subject,
				"use_html": source.use_html,
				"response_html": source.response_html,
				"response": source.response,
			}
	frappe.get_doc({"doctype": "Email Template", "name": target, **values}).insert(ignore_permissions=True)


def _renders(source, row):
	"""Dry-render a customised source through the U11 sandbox with the
	event's sample context. False (and an Error Log) when it fails."""
	from helixhr.api import _celebration_sample_context
	from helixhr.utils import render_celebration_email

	body = source.response_html if source.use_html else source.response
	try:
		render_celebration_email(source.subject, body, _celebration_sample_context(row.event, row.company))
	except Exception:
		frappe.log_error(
			title=f"Celebration template {source.name} replaced by the default",
			message=frappe.get_traceback(),
		)
		return False
	return True
