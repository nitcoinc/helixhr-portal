import frappe
from frappe import _

from helixhr.utils import get_manager_user

# Timesheet workflow document event hooks (KTD7, KTD18). Two guards, both
# needed because Frappe's workflow engine only enforces "does the acting
# user have the required role" -- not "is this specific document theirs to
# act on" -- and a manager's own User Permission (scoped to their own
# Employee record) would otherwise hide their reports' timesheets entirely.


def timesheet_on_update(doc, method=None):
	manager_user = get_manager_user(doc.employee)

	if doc.workflow_state == "Pending Approval":
		if not manager_user:
			# The Submit transition (Draft -> Pending Approval) is a plain
			# field update, not a real docstatus submit, so it lands here
			# rather than in before_submit -- this is the one place every
			# path that tries to move a timesheet to Pending Approval
			# actually passes through. Raising here rolls back the whole
			# request (KTD10's "Ask HR" refusal).
			frappe.throw(
				_(
					"You don't have a manager set up to approve your timesheet yet. "
					"Ask HR to set one before submitting."
				)
			)
		_share_with_approver(doc, manager_user)
	elif doc.workflow_state in ("Approved", "Rejected") and manager_user:
		_unshare_from_approver(doc, manager_user)


def timesheet_before_submit(doc, method=None):
	"""Refuses any submit (Pending Approval -> Approved, docstatus 0->1)
	unless the acting user is this timesheet's manager, HR Manager, or
	System Manager -- covers both the workflow's own Approve action and a
	raw frappe.client.submit() call that skips the workflow entirely
	(AE6). The workflow's per-transition `allow_self_approval=0` already
	stops the employee approving their own via the workflow path; this
	hook is what stops the same self-approval attempt made directly."""
	user = frappe.session.user
	if user == "Administrator":
		return
	if set(frappe.get_roles(user)) & {"HR Manager", "System Manager"}:
		return
	if user == get_manager_user(doc.employee):
		return

	frappe.throw(
		_("Only {0}'s manager or HR can approve this timesheet.").format(doc.employee),
		frappe.PermissionError,
	)


def _share_with_approver(doc, manager_user):
	# add_docshare is itself idempotent (looks up any existing DocShare
	# for this user/doc and updates it rather than duplicating).
	frappe.share.add_docshare(
		doc.doctype,
		doc.name,
		manager_user,
		write=1,
		submit=1,
		flags={"ignore_share_permission": True},
	)


def _unshare_from_approver(doc, manager_user):
	# frappe.share.remove() -> frappe.delete_doc("DocShare", ...) checks
	# the *acting* user's delete permission on DocShare itself -- which
	# the manager (role Employee) doesn't have. This runs as part of the
	# manager's own Approve/Reject action, so a plain permission-checked
	# delete would fail on the very share that made the read/write access
	# possible in the first place. A direct db.delete is safe here: it's
	# cleaning up a share this app created, not exposing new access.
	frappe.db.delete(
		"DocShare", {"share_doctype": doc.doctype, "share_name": doc.name, "user": manager_user}
	)
