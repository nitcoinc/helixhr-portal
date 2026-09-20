"""P8-U10: creates the two `HelixHR Celebration Reminder` rows (`birthday`,
`work_anniversary`) every site needs, carrying forward whatever HR Settings'
two now-superseded Custom Fields (`helixhr_birthday_template`,
`helixhr_anniversary_template`) already held.

Runs unconditionally, not only when a site had those fields set: both event
rows must exist for `reminders.send_celebration_reminders`,
`events.hr_settings_validate` and `preflight.check_celebration_reminders` to
have something to read, on every site, including a fresh install that never
touched the two old fields at all.

Idempotent, and called twice for the same reason `seed_celebration_templates`
and the rest of `install.py`'s own list are: `bench new-site --install-app`
marks every `patches.txt` entry as already applied *without running it*
(`frappe.installer.install_app` -> `set_all_patches_as_completed`), on the
assumption a fresh install's doctype JSON already reflects the schema a
patch would have produced -- true for schema, not for the two rows this
patch inserts. `helixhr/install.py`'s `after_install` calls `execute()` too,
so a fresh install still gets both event rows.

The two Custom Fields are left in place, unused, after this runs. Removing
them is a separate, later patch (P8's own Scope Boundaries) -- this one
must be safely re-runnable against a site that has not had them removed yet,
and against one that already has.
"""

import frappe


def execute():
	if not frappe.db.exists("DocType", "HelixHR Celebration Reminder"):
		# A site migrating from before this patch's own doctype shipped
		# would otherwise hit an unhelpful "doctype not found" -- post_model_sync
		# patches run after the doctype sync that creates it, so this should
		# never actually fire, but a defensive check costs nothing here.
		return

	# `frappe.db.has_column` assumes a physical table -- HR Settings is a
	# Single doctype (values live in `tabSingles`, not a table of its own),
	# so the field's existence is checked through the doctype meta instead.
	has_old_fields = frappe.get_meta("HR Settings").has_field("helixhr_birthday_template")

	for event, field in (
		("birthday", "helixhr_birthday_template"),
		("work_anniversary", "helixhr_anniversary_template"),
	):
		if frappe.db.exists("HelixHR Celebration Reminder", event):
			continue

		template = frappe.db.get_single_value("HR Settings", field) if has_old_fields else None
		frappe.get_doc(
			{
				"doctype": "HelixHR Celebration Reminder",
				"event": event,
				"email_template": template,
				"is_enabled": 1 if template else 0,
				"recipient_mode": "All employees",
			}
		).insert(ignore_permissions=True)
