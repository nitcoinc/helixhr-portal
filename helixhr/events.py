import frappe
from frappe import _
from frappe.utils import cint

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


# HR Request reply notifications (P2-U4, P2-KTD6, P2-R13).
#
# The reply an employee has to read is `hr_note`, and the fixture Notification
# "HelixHR Request Status Changed" cannot see it: a Notification with event
# "Value Change" watches exactly one field, and that one watches `status`. HR
# adding or revising a note without moving the status produced nothing at all,
# so "HR replied" never became an obligation and never cleared -- which is the
# whole of P2-KTD6's "the reply event is code, not a fixture".
#
# The subject prefix is the marker. Notification Log has no room for a custom
# field of ours, and the queue has to be able to ask for *reply* rows without
# also sweeping up the status-change rows the fixtures write, so the prefix is
# both written and matched in one place. helixhr.api._get_needs_you imports it.
HR_REPLY_SUBJECT_PREFIX = "HR replied about"


def hr_request_on_update(doc, method=None):
	"""One notification per new employee-visible reply, and none for
	anything else.

	Deduplication is the diff itself: `get_doc_before_save()` is the
	persisted row, so a save that did not change `hr_note` writes nothing,
	however many times HR saves the record. A *revised* note is a genuinely
	new thing to read, so it inserts a new unread row rather than reopening
	the older one -- the older reply stays read, which is what it is.
	"""
	before = doc.get_doc_before_save()
	if not before:
		# An insert. HR cannot write hr_note at creation (permlevel 1), and
		# an employee's own new request has nothing to reply to yet.
		return

	note = (doc.hr_note or "").strip()
	if not note or note == (before.hr_note or "").strip():
		return

	for_user = frappe.db.get_value("Employee", doc.employee, "user_id") or doc.owner
	if not for_user or for_user == frappe.session.user:
		# Nobody tells you what you just wrote.
		return

	frappe.get_doc(
		{
			"doctype": "Notification Log",
			"for_user": for_user,
			"from_user": frappe.session.user,
			"type": "Alert",
			"document_type": "HR Request",
			"document_name": doc.name,
			"subject": f"{HR_REPLY_SUBJECT_PREFIX} {doc.subject}",
			# Notification Log mirrors description <-> email_content in its
			# own before_insert, so one of the pair is enough.
			"description": frappe.utils.escape_html(note),
		}
	).insert(ignore_permissions=True)


def file_before_insert(doc, method=None):
	"""KTD18: Frappe lets a file's owner attach it to any document they
	can *read* (not necessarily write) -- an employee could otherwise
	attach a file to someone else's HR Request just by knowing its name.
	Only touches files attached to HR Request; every other upload in the
	app (there are none yet, but future ones too) is unaffected.

	Refuses rather than coercing a non-private upload: this hook runs
	*after* File's own before_insert (Document.hook() composes the base
	controller method first, then doc_event hooks -- confirmed while
	building this), by which point a non-private file has already been
	written to the public path and save_file()/file_url both reflect
	that. Flipping is_private=1 here alone leaves file_url pointing at
	the wrong (public) path, which then fails File's own later
	validation with a confusing "incorrect File URL" error -- refusing
	outright is both simpler and doesn't leave a real file sitting in
	the public folder even momentarily. RequestForm.vue already always
	uploads with is_private=1, so this only ever fires against a caller
	that bypasses the portal's own upload path.
	"""
	if doc.attached_to_doctype != "HR Request" or not doc.attached_to_name:
		return

	if not cint(doc.is_private):
		frappe.throw(_("Files attached to a request must be private."), frappe.PermissionError)

	if not frappe.has_permission("HR Request", "write", doc.attached_to_name):
		frappe.throw(
			_("You can't attach a file to that request."),
			frappe.PermissionError,
		)
