"""P4-KTD1: the workflow state that means "sent back" is now called that.

Timesheet and Attendance Request both used the shared state **Rejected** to
mean "the approver sent this back, edit it and send it again". P4 gives
*Rejected* its literal meaning -- a final no, with no way back to Draft -- and
moves the recoverable meaning to a new state, **Sent Back**. Every row a site
already carries in the old state has the old meaning, so it has to move with
it: without this patch a sent-back week or request would read "Rejected" and
the employee would be offered nothing at all, because the `Edit` transition
now hangs off Sent Back.

Only docstatus 0 rows are touched. A docstatus-1 row was never in the
send-back state on either workflow (`Approved` is the only submitted state),
and a docstatus-2 row is cancelled history.

Runs post_model_sync, which Frappe schedules *before* `sync_fixtures`
(`frappe/migrate.py`), so the "Sent Back" Workflow State row does not exist
yet when this runs. That is fine: this is a column update on the document
table, not a Link validation, and the fixture creates the state moments
later.

Idempotent: the second run matches no rows. Guarded on the Workflow existing
on this site, so a site that never installed the fixture (an app installed but
never migrated, or one where HR deactivated and deleted it) is left alone.
"""

import frappe

LEGACY_STATE = "Rejected"
SENT_BACK = "Sent Back"

# (Workflow name, document type). The guard is the Workflow, not the doctype:
# "Rejected" is a shared state and other apps' workflows use it, so a site
# without *this* app's workflow has no rows of ours to rename.
WORKFLOWS = (
	("Timesheet Approval", "Timesheet"),
	("Attendance Request Approval", "Attendance Request"),
)


def execute():
	for workflow, doctype in WORKFLOWS:
		if not frappe.db.exists("Workflow", workflow):
			continue
		frappe.db.set_value(
			doctype,
			{"workflow_state": LEGACY_STATE, "docstatus": 0},
			"workflow_state",
			SENT_BACK,
			update_modified=False,
		)
