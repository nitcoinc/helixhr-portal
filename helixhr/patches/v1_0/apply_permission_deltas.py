"""P2-U1: apply this app's permission deltas at migrate time instead of
shipping Custom DocPerm rows as fixtures.

Why this is a patch and not a fixture
-------------------------------------
`frappe.permissions.get_valid_perms` discards **every** standard DocPerm for
a doctype that has at least one Custom DocPerm row -- it does not merge them::

	for p in perms:
		if p.parent not in doctypes_with_custom_perms:
			custom_perms.append(p)

So a fixture that ships one Custom DocPerm row for Leave Application removes
HR Manager, HR User and Leave Approver from that doctype entirely on any site
where nothing else had already copied the standard rows in. That is exactly
what a fresh CI or production install looked like before this patch, and it
is why P2-AE9 could not pass there.

Widening the fixture filters to carry every role would fix the symptom and
freeze *this dev machine's* Frappe/ERPNext/HRMS permission rows into the app
forever. `frappe.permissions.setup_custom_perms` instead copies each site's
**own** installed standard rows into Custom DocPerm before anything is
changed, so every site snapshots the version it actually runs. The app then
applies only its own deltas on top.

The deltas below are the difference between the rows this app used to ship in
`fixtures/custom_docperm.json`, `fixtures/leave_application_custom_docperm.json`
and `fixtures/timesheet_custom_docperm.json` and the standard rows for the
same role and permlevel. Nothing new is granted here.

Idempotent: every step is "make this row look like this", never "add one
more", so re-running `execute()` by hand on a site that already has it writes
nothing new. `helixhr.preflight.check_custom_docperm_coverage` is the runtime
guard afterwards -- a patch runs once, so anything that trims these rows later
is caught there, not here.
"""

import frappe
from frappe.core.doctype.custom_docperm.custom_docperm import update_custom_docperm
from frappe.permissions import add_permission, setup_custom_perms

# The Custom DocPerm rows the app shipped as fixtures up to P2-U1. Their names
# are fixed strings in the old fixture files, so a site that migrated before
# this patch still carries them -- as duplicates of the rows
# `setup_custom_perms` copies in. Removed by name first, then re-expressed as
# deltas below; on a site that never had them this is a no-op.
LEGACY_FIXTURE_ROWS = (
	# fixtures/custom_docperm.json (Employee)
	"3klcbm51qi",
	"3kll3igu0m",
	"3kl0jf32d2",
	"3kljeehq1o",
	"3klll4ae7a",
	"3klr99rjf9",
	"3klfuroh7g",
	"3klhrk5koj",
	"ocaiasbcj3",
	"ocag0rgug6",
	"oca2mfcuee",
	# fixtures/leave_application_custom_docperm.json
	"ocgrqfluq5",
	# fixtures/timesheet_custom_docperm.json
	"ocjjp6dujv",
)

# (role, permlevel, if_owner) -> the ptypes this app sets on that row.
# Order matters: Frappe refuses a permlevel > 0 rule for a role that has no
# level 0 rule (`check_level_zero_is_set`), so System Manager's level 0 row is
# created before its level 1 and 2 rows.
DELTAS = {
	# P2-R? / KTD (phase 1 U5): fixtures/property_setter.json moves every
	# Employee field an employee may not edit to permlevel 1 and the HR-only
	# ones to permlevel 2. Standard Employee DocPerms only cover permlevel 0,
	# so without these rows nobody -- not even HR -- can read or write a
	# locked field, and the employee's own seven editable fields at level 0
	# need `write`.
	"Employee": (
		(("Employee", 0, 0), {"write": 1}),
		(("Employee", 1, 0), {"read": 1, "write": 0}),
		(("HR Manager", 1, 0), {"read": 1, "write": 1}),
		(("HR Manager", 2, 0), {"read": 1, "write": 1}),
		(("HR User", 1, 0), {"read": 1, "write": 1}),
		(("HR User", 2, 0), {"read": 1, "write": 1}),
		(("System Manager", 0, 0), {"read": 1, "write": 1, "create": 1, "delete": 1}),
		(("System Manager", 1, 0), {"read": 1, "write": 1}),
		(("System Manager", 2, 0), {"read": 1, "write": 1}),
	),
	# KTD17: role Employee has no `delete` on Leave Application in the base
	# DocPerms, so withdrawing a pending request is refused. Granted through a
	# second, `if_owner` rule so it only ever applies to the caller's own
	# document -- putting `if_owner` on the *base* rule instead would move
	# read/write/report into the owner-only bucket too, and an employee would
	# stop being able to see a leave request HR filed for them.
	# P2-U1 step 7: `share` is dropped here because the portal offers no
	# sharing UI. HRMS's own Employee Self Service rule still grants it to
	# users who hold that role; removing sharing site-wide is System Settings'
	# "Disable Document Sharing", not a permission rule.
	"Leave Application": (
		(("Employee", 0, 0), {"share": 0}),
		(("Employee", 0, 1), {"read": 1, "delete": 1}),
	),
	# R17: the portal sends a week for approval through the Timesheet Approval
	# workflow, which submits the document as the employee.
	"Timesheet": ((("Employee", 0, 0), {"submit": 1}),),
}


def execute():
	for doctype, deltas in DELTAS.items():
		_drop_legacy_fixture_rows(doctype)
		# Snapshot *this site's* standard rows before touching anything, so
		# the roles Frappe is about to stop reading from `tabDocPerm` keep
		# exactly the access their installed app version gave them.
		setup_custom_perms(doctype)
		for (role, permlevel, if_owner), values in deltas:
			_apply(doctype, role, permlevel, if_owner, values)
		frappe.clear_cache(doctype=doctype)


def _drop_legacy_fixture_rows(doctype):
	rows = frappe.get_all(
		"Custom DocPerm", filters={"parent": doctype, "name": ("in", LEGACY_FIXTURE_ROWS)}, pluck="name"
	)
	for row in rows:
		frappe.delete_doc("Custom DocPerm", row, ignore_permissions=True, force=True)


def _apply(doctype, role, permlevel, if_owner, values):
	row = frappe.db.get_value(
		"Custom DocPerm",
		{"parent": doctype, "role": role, "permlevel": permlevel, "if_owner": if_owner},
	)
	if not row:
		row = _create(doctype, role, permlevel, if_owner)
	current = frappe.db.get_value("Custom DocPerm", row, list(values), as_dict=True)
	if any(frappe.utils.cint(current[ptype]) != value for ptype, value in values.items()):
		update_custom_docperm(row, values)


def _create(doctype, role, permlevel, if_owner):
	if not if_owner:
		return add_permission(doctype, role, permlevel)
	# Frappe has no public helper that creates an `if_owner` rule --
	# `add_permission` hard-codes `if_owner=0` and `update_permission_property`
	# ignores the `if_owner` argument it accepts (both in frappe/permissions.py).
	# This mirrors what `add_permission` does, with every right this app does
	# not need left off rather than defaulted on (`read` and `export` default
	# to 1 on Custom DocPerm).
	return frappe.get_doc(
		{
			"doctype": "Custom DocPerm",
			"parent": doctype,
			"parenttype": "DocType",
			"parentfield": "permissions",
			"role": role,
			"permlevel": permlevel,
			"if_owner": 1,
			"read": 1,
			"export": 0,
		}
	).insert(ignore_permissions=True).name
