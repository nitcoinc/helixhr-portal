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
	# P4-KTD4: `helixhr_stage` sits at permlevel 1, so the HR queue survives
	# a generic write. Role Employee has write on its own open Leave
	# Application and HRMS shares every application with its approver at
	# `submit=1`, so at permlevel 0 either of them could move a request into
	# or out of the HR queue with one `frappe.client.set_value`. HR Manager is
	# the only role given the level, and the portal writes the field with
	# `db_set` after its own authorization -- a permlevel-1 field set through
	# `save()` by anybody else is reset to the stored value (P4-R8a).
	"Leave Application": (
		(("Employee", 0, 0), {"share": 0}),
		(("Employee", 0, 1), {"read": 1, "delete": 1}),
		(("HR Manager", 1, 0), {"read": 1, "write": 1}),
	),
	# R17: the portal sends a week for approval through the Timesheet Approval
	# workflow, which submits the document as the employee.
	# P4-KTD7a: `helixhr_decision_reason` sits at permlevel 1 (the same lock
	# the Employee fields above use), and standard Timesheet DocPerms only
	# cover permlevel 0 -- so without this row nobody, HR included, can read
	# or write the approver's reason. Role Employee gets nothing at level 1:
	# an approver writes the reason through `act_on_approval`, and the
	# employee only ever reads it through a portal method.
	"Timesheet": (
		(("Employee", 0, 0), {"submit": 1}),
		(("HR Manager", 1, 0), {"read": 1, "write": 1}),
		# Plan 2026-10-04-003 KTD2 / U1: HRMS hands Employee Self Service
		# `cancel` and `amend` on Timesheet. With the Cancelled workflow
		# state installed, Desk's own Cancel button hides behind
		# `can_cancel_document`, but `frappe.client.cancel` only consults
		# the DocPerm -- without this delta an employee could still cancel
		# an approved week raw, off the workflow. Amend is the same door:
		# the accept path of a change request amends server-side as the
		# employee (KTD4), never through the Desk amend route.
		(("Employee Self Service", 0, 0), {"cancel": 0, "amend": 0}),
	),
	# P3-KTD13 / P3-R7a: HRMS ships role Employee with create, write and
	# delete on Employee Checkin, so an employee could insert a backdated
	# punch with any coordinates and edit or delete punches until the nightly
	# job links them. The portal method `punch_my_checkin` is the create rule
	# (it inserts with `ignore_permissions`), as `create_my_request` is for HR
	# Request; `read` stays so the Attendance page keeps listing punches.
	"Employee Checkin": ((("Employee", 0, 0), {"create": 0, "write": 0, "delete": 0}),),
	# P3-KTD13 / P3-R17a: with `share` an employee could grant a colleague
	# `submit` on their own Attendance Request and skip both approval steps.
	# The DocShare to the manager is written by `events._reconcile_share`
	# with `ignore_permissions`, so it does not need this right.
	# P4-KTD7a: the same permlevel-1 row as Timesheet's, for the same field.
	"Attendance Request": (
		(("Employee", 0, 0), {"share": 0}),
		(("HR Manager", 1, 0), {"read": 1, "write": 1}),
	),
	# P5-U2: IT Team works HR Requests in the portal. The level-0 row is
	# required before its level-1 row; the preflight guard names the complete
	# level-1 field inventory so a later field cannot be exposed silently.
	"HR Request": (
		(("IT Team", 0, 0), {"read": 1, "write": 1}),
		(("IT Team", 1, 0), {"read": 1, "write": 1}),
	),
	"HelixHR Request Category": (
		(("IT Team", 0, 0), {"read": 1}),
	),
	# Plan 2026-10-02-001 U7 / KTD12: the portal-only Notification Manager owns
	# message wording. HR Manager left this doctype's own JSON permissions, so
	# the snapshot `setup_custom_perms` takes below (post_model_sync, after the
	# doctype synced) carries System Manager only.
	"HelixHR Message Template": (
		(("HelixHR Notification Manager", 0, 0), {"read": 1, "write": 1, "create": 1}),
	),
	# P7-U1 originally granted HelixHR Delivery Manager a plain read/write/
	# create DocPerm on Project and Task here, paired with the scope hooks
	# in `helixhr/project_permissions.py` (KTD8). Code review found that
	# pairing insufficient: a DocPerm has no field-level notion of scope,
	# and neither Project's nor Task's costing-tab fields
	# (`estimated_costing`, `total_billable_amount`, `gross_margin`,
	# `customer`, `sales_order`, `total_costing_amount` on Task, etc.) carry
	# any permlevel restriction in stock ERPNext -- they are ordinary
	# permlevel-0 fields, the same level this grant was made at. A Delivery
	# Manager calling Frappe's generic REST route directly
	# (`/api/resource/Project/<name>`) would therefore receive the whole
	# document for any project they administer, including every costing
	# field, bypassing `helixhr.api.get_project`'s explicit field allow-list
	# entirely -- exactly the exposure R8 exists to prevent (KTD9).
	#
	# The role needs no DocPerm at all to function: every HelixHR method
	# that touches Project or Task already reads via `frappe.db.get_value`/
	# `frappe.get_all(..., ignore_permissions=True)` or writes via
	# `doc.insert(ignore_permissions=True)`/`doc.save(ignore_permissions=True)`
	# (see `_write_project_users`, `save_task` in `helixhr/api.py`) --
	# `resolve_project_scope`/`project_in_scope`, not Frappe's own DocPerm
	# system, is this app's real authorisation boundary for these two
	# doctypes (KTD8's own framing). So the grant is removed rather than
	# narrowed: Delivery Manager gets zero standing Frappe permission on
	# Project or Task, the portal's own functionality is unaffected, and
	# direct REST-route access is refused outright instead of scoped.
	#
	# The `permission_query_conditions`/`has_permission` hooks in
	# `project_permissions.py` stay registered -- they still narrow access
	# for every other role that resolves a project scope (HR Manager's
	# "company" branch), just not for a role with no base grant to reach
	# them through in the first place.
	#
	# Plan 2026-10-06-001 U3: the access matrix is Portal Admin / System
	# Manager only now, and the portal gate is `_assert_report_access_admin`.
	# HR Manager's standard DocPerm here is a second door: a direct
	# `/api/resource/HelixHR Report Access/<key>` write would change the
	# matrix off the endpoint's own check. Removed rather than narrowed, the
	# same reasoning as Delivery Manager on Project above -- nothing HR does
	# through the portal needs this perm, and Portal Admin writes with
	# `ignore_permissions` behind its own gate.
	"HelixHR Report Access": (
		(("HR Manager", 0, 0), {"read": 0, "write": 0, "create": 0, "delete": 0}),
	),
}


def execute():
	for doctype, deltas in DELTAS.items():
		_drop_legacy_fixture_rows(doctype)
		# Snapshot *this site's* standard rows before touching anything, so
		# the roles Frappe is about to stop reading from `tabDocPerm` keep
		# exactly the access their installed app version gave them.
		setup_custom_perms(doctype)
		for (role, permlevel, if_owner), values in deltas:
			_ensure_role(role)
			_apply(doctype, role, permlevel, if_owner, values)
		frappe.clear_cache(doctype=doctype)


def _ensure_role(role):
	"""Create ``role`` with the portal-only shape `fixtures/role.json` ships
	(`desk_access=0`, `is_custom=0`), if migrate's own fixture sync has not
	created it yet.

	Patches always run *before* fixtures on every `bench migrate`
	(`frappe.migrate.Migrate.run_schema_updates` then `post_schema_updates`),
	and a fresh `bench install-app` marks every patch complete without
	running it at all (`frappe.installer.install_app` calls
	`set_all_patches_as_completed` before `sync_fixtures`) -- so a role this
	app owns and a patch that grants it access can only safely land in
	different migrate cycles unless the patch also knows how to create its
	own role. Verified on the bench: `HelixHR Delivery Manager` referenced by
	this same patch, in the same migrate that first ships its fixture, threw
	`LinkValidationError` without this. A no-op for a role that already
	exists (`IT Team`, or any stock role named in `DELTAS`), and harmless
	when the fixture sync moments later reconciles the same row.
	"""
	if frappe.db.exists("Role", role):
		return
	frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 0, "is_custom": 0}).insert(
		ignore_permissions=True
	)


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
