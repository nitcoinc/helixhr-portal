"""Plan 2026-10-16-001 U2 / KTD3: Mark done and Decline from Waiting on Employee,
two transitions each for HR Manager and IT Team on the "HR Request Handling" workflow.

The fixture (`fixtures/workflow.json`) carries all four and re-syncs on migrate;
this patch exists because AGENTS.md requires a patch for a fixture change,
and it makes the four rows present even where the fixture sync is skipped or
the workflow was edited in Desk. The rows are copied from the fixture, so
the fixture stays the single source of their conditions.

Idempotent: a transition already present (same state, action, next state
and role) is left alone. Guarded on the Workflow existing on this site.
"""

import json

import frappe

WORKFLOW = "HR Request Handling"
_KEY = ("state", "action", "next_state", "allowed")
_COPIED = (*_KEY, "condition", "allow_self_approval")


def _fixture_transitions():
	path = frappe.get_app_path("helixhr", "fixtures", "workflow.json")
	with open(path) as handle:
		workflows = json.load(handle)
	workflow = next(row for row in workflows if row["name"] == WORKFLOW)
	return [
		row
		for row in workflow["transitions"]
		if row["state"] == "Waiting on Employee" and row["action"] in ("Done", "Reject")
	]


def execute():
	if not frappe.db.exists("Workflow", WORKFLOW):
		return
	doc = frappe.get_doc("Workflow", WORKFLOW)
	present = {tuple(row.get(field) for field in _KEY) for row in doc.transitions}
	missing = [row for row in _fixture_transitions() if tuple(row.get(field) for field in _KEY) not in present]
	if not missing:
		return
	for row in missing:
		doc.append("transitions", {field: row.get(field) for field in _COPIED})
	# The fixture's states ("Open", "In Progress", ...) are not Workflow
	# State records on every site; the fixture import skips link checks too.
	doc.flags.ignore_links = True
	doc.save(ignore_permissions=True)
