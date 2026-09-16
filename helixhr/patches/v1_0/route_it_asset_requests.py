"""P5-U2: send the built-in IT category to the portal-only IT Team role."""

import frappe

CATEGORY = "IT / Asset"
INITIAL_ROLE = "HR Manager"
IT_TEAM = "IT Team"


def execute():
	"""Move only the untouched seed route; never overwrite an HR choice.

	Post-model patches run before fixtures sync, so this ensures the fixture's
	literal role exists before the permission patch validates its Link. Fixture
	sync remains the declarative source of truth; this is only the migration
	ordering bridge. The raw route write is safe because this patch owns the
	literal initial route, and preflight guards the installed role thereafter.
	"""
	if not frappe.db.exists("Role", IT_TEAM):
		frappe.get_doc(
			{
				"doctype": "Role",
				"name": IT_TEAM,
				"role_name": IT_TEAM,
				"desk_access": 0,
				"is_custom": 0,
			}
		).insert(ignore_permissions=True)
	if frappe.db.get_value("HelixHR Request Category", CATEGORY, "route_to_role") == INITIAL_ROLE:
		frappe.db.set_value("HelixHR Request Category", CATEGORY, "route_to_role", IT_TEAM)
