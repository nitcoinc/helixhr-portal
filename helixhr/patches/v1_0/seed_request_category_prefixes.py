"""Plan 2026-10-04-002 U1: give every existing request category an ID prefix.

The category's `name_prefix` field arrives after the categories themselves,
so a migrated site has rows with an empty prefix -- which would fall back to
`HR-REQ` at naming time anyway. Seeding makes the intent explicit: IT / Asset
is numbered `IT-REQ`, everything else keeps the `HR-REQ` counter it has
always shared. Insert-if-absent semantics: only an *empty* (NULL or '')
prefix is ever filled, never an HR-edited one. Also called from
`helixhr.install.after_install`, because `--install-app` marks patches
complete without running them.
"""

import frappe

IT_CATEGORY = "IT / Asset"
IT_PREFIX = "IT-REQ"
DEFAULT_PREFIX = "HR-REQ"


def execute():
	for row in frappe.get_all("HelixHR Request Category", fields=["name", "name_prefix"]):
		if row.name_prefix:
			continue
		prefix = IT_PREFIX if row.name == IT_CATEGORY else DEFAULT_PREFIX
		frappe.db.set_value("HelixHR Request Category", row.name, "name_prefix", prefix)
