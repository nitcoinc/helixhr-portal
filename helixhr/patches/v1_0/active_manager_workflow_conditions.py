"""Plan 2026-10-18-001 U2 / KTD7: the Timesheet manager transitions (Approve,
Send Back, Send to HR from Pending Approval, role Employee) look up the
caller's **Active** Employee.

The old condition looked up `{"user_id": session.user}` with no status
filter, so a manager whose login also has a newer Left or Inactive Employee
row matched that row instead, and the condition failed: the queue listed the
week and the screen offered no buttons. `or ""` keeps a caller with no Active
row from matching a week whose employee has no manager (None == None).

The fixture (`fixtures/workflow.json`) carries the new conditions and
re-syncs on every migrate; this patch is the backstop AGENTS.md requires for
a fixture change, and covers a site where the fixture sync is skipped. The
condition is copied from the fixture, so the fixture stays the single source.

Keyed by (state, action, next_state, allowed), never appended: only a
transition whose condition differs is rewritten. A rerun changes nothing.
Guarded on the Workflow existing on this site.
"""

import json

import frappe

WORKFLOW = "Timesheet Approval"
_KEY = ("state", "action", "next_state", "allowed")


def _fixture_conditions():
	path = frappe.get_app_path("helixhr", "fixtures", "workflow.json")
	with open(path) as handle:
		workflows = json.load(handle)
	workflow = next(row for row in workflows if row["name"] == WORKFLOW)
	return {
		tuple(row.get(field) for field in _KEY): row.get("condition")
		for row in workflow["transitions"]
		if row["state"] == "Pending Approval" and row["allowed"] == "Employee" and row.get("condition")
	}


def execute():
	if not frappe.db.exists("Workflow", WORKFLOW):
		return
	wanted = _fixture_conditions()
	for row in frappe.get_all(
		"Workflow Transition",
		filters={"parent": WORKFLOW, "parenttype": "Workflow"},
		fields=["name", *_KEY, "condition"],
	):
		condition = wanted.get(tuple(row.get(field) for field in _KEY))
		if condition and row.condition != condition:
			frappe.db.set_value(
				"Workflow Transition", row.name, "condition", condition, update_modified=False
			)
	frappe.clear_cache(doctype="Timesheet")
