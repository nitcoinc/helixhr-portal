"""Documents: give every HelixHR Document Link written before the category and
date fields existed a category of General and a `published_on` taken from its
creation date, so the Documents page can sort and bucket them.

Idempotent: only rows still missing a value are touched, and a direct
`db` update (no `modified` bump) so re-running is a no-op. Not in
`install.after_install`: a fresh site has no rows that predate the fields.
"""

import frappe


def execute():
	table = "`tabHelixHR Document Link`"
	frappe.db.sql(f"update {table} set category = 'General' where ifnull(category, '') = ''")
	frappe.db.sql(f"update {table} set published_on = date(creation) where published_on is null")
