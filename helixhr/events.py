import frappe
from frappe import _
from frappe.utils import cint

from helixhr.helixhr.doctype.hr_request.hr_request import request_belongs_to_session
from helixhr.utils import UPLOAD_POLICY, get_manager_user, upload_extension, validate_portal_upload

# Timesheet workflow document event hooks (KTD7, KTD18). Two guards, both
# needed because Frappe's workflow engine only enforces "does the acting
# user have the required role" -- not "is this specific document theirs to
# act on" -- and a manager's own User Permission (scoped to their own
# Employee record) would otherwise hide their reports' timesheets entirely.


# The one workflow state that carries a share. Everything else -- Draft,
# Approved, Rejected, Cancelled -- has nothing for an approver to do, so the
# access goes away with the decision (P2-U7 scenario 8).
PENDING_STATE = "Pending Approval"


def _approver_user(employee):
	"""The User that may hold `employee`'s pending-week share: their
	manager's login, and only while that manager is an **Active** Employee.

	Stricter than `helixhr.utils.get_manager_user`, deliberately. A share
	carries `write=1, submit=1` and is reachable from `frappe.client`,
	`/api/resource` and `apply_workflow` -- not only from the portal, which
	already refuses a manager whose Employee record is not Active. A
	manager who has left with their login still enabled must not keep the
	one grant that survives that refusal (P2-U7 step 6, from the other
	side).
	"""
	reports_to = frappe.db.get_value("Employee", employee, "reports_to")
	if not reports_to:
		return None
	manager = frappe.db.get_value("Employee", reports_to, ["user_id", "status"], as_dict=True)
	if not manager or manager.status != "Active":
		return None
	return manager.user_id


def timesheet_on_update(doc, method=None):
	manager_user = _approver_user(doc.employee)

	if doc.workflow_state == PENDING_STATE and doc.docstatus == 0:
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
		_reconcile_timesheet_share(doc.name, doc.employee, manager_user)
	else:
		# Approved, Rejected, Cancelled, or back to Draft. "Cancelled" and
		# the docstatus-2 case were missing until P2-U7: a cancelled week
		# kept its approver's write+submit share forever.
		_reconcile_timesheet_share(doc.name, doc.employee, None)


def employee_on_update(doc, method=None):
	"""P2-U7 step 6. When somebody's manager changes, every week they have
	waiting has to change hands with them.

	Without this, a Timesheet sent to Manager A stayed shared with A --
	with write *and submit* -- for as long as it sat pending, while
	`_approver_user` had already started answering B. A could still
	approve a week for somebody who no longer reported to them, and B
	could not see it at all. The reconcile runs inside the Employee save's
	own transaction, so the reassignment and the share change commit
	together or not at all.

	Widened after P2-U7: the manager's *own* record moving is the same
	defect from the other side. A rename, an Entra migration or a
	duplicate-account cleanup changes `Employee.user_id`, and leaving with
	the login still enabled changes `status` -- after either one
	`_approver_user` answers somebody else (or nobody) while the DocShare
	granting `write=1, submit=1` on every week they have pending still
	points at the old account, which `frappe.client.set_value`,
	`/api/resource` and `apply_workflow` all honour.
	"""
	before = doc.get_doc_before_save()
	if not before:
		return

	if before.reports_to != doc.reports_to:
		_reconcile_pending_weeks(doc.name)

	if before.user_id != doc.user_id or before.status != doc.status:
		for report in frappe.get_all("Employee", filters={"reports_to": doc.name}, pluck="name"):
			_reconcile_pending_weeks(report)


def _reconcile_pending_weeks(employee):
	"""Point every week `employee` has waiting at whoever may act on it now."""
	manager_user = _approver_user(employee)
	for name in frappe.get_all(
		"Timesheet",
		filters={"employee": employee, "workflow_state": PENDING_STATE, "docstatus": 0},
		pluck="name",
	):
		_reconcile_timesheet_share(name, employee, manager_user)


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


def _reconcile_timesheet_share(name, employee, keep_user):
	"""Leave exactly one approver share on this timesheet -- `keep_user`'s
	-- and none at all when `keep_user` is None.

	Written as a reconcile rather than an add/remove pair because the thing
	that goes wrong is never the share you knew about: it is the one an
	*older* manager still holds after a reassignment (P2-U7 scenario 7).
	Removing "the current manager's share" cannot remove that one, because
	by the time anybody looks, the current manager is somebody else.

	The employee's own user is never touched: it is their record, and any
	share they hold on it came from somewhere other than this app.

	`frappe.db.delete` rather than `frappe.share.remove`: the latter goes
	through `frappe.delete_doc("DocShare", ...)`, which checks the *acting*
	user's delete permission on DocShare -- which a manager (role Employee)
	does not have. This runs as part of the manager's own Approve/Reject,
	so a permission-checked delete would fail on the very share that made
	the action possible. A direct delete is safe here: it removes access
	this app granted, it never creates any.
	"""
	employee_user = frappe.db.get_value("Employee", employee, "user_id")
	for share in frappe.get_all(
		"DocShare",
		filters={"share_doctype": "Timesheet", "share_name": name},
		fields=["name", "user"],
	):
		if share.user and share.user in (keep_user, employee_user):
			continue
		frappe.db.delete("DocShare", {"name": share.name})

	if keep_user:
		# add_docshare is itself idempotent (looks up any existing DocShare
		# for this user/doc and updates it rather than duplicating).
		frappe.share.add_docshare(
			"Timesheet",
			name,
			keep_user,
			write=1,
			submit=1,
			flags={"ignore_share_permission": True},
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
	the public folder even momentarily. `helixhr.api.attach_to_my_request`
	already always writes is_private=1, so this only ever fires against a
	caller that bypasses the portal's own upload path.

	P2-U8: the ownership test used to be `has_permission("HR Request",
	"write", ...)`. Role Employee no longer has write on HR Request at all
	(creation and attachment go through the two session-scoped portal
	methods instead), so that check would now refuse the request's own
	owner. `request_belongs_to_session` asks the question this hook actually
	means -- is this the caller's own request -- and HR keeps its own write
	permission as the second branch.
	"""
	if doc.attached_to_doctype != "HR Request" or not doc.attached_to_name:
		return

	if not cint(doc.is_private):
		frappe.throw(_("Files attached to a request must be private."), frappe.PermissionError)

	if not (
		request_belongs_to_session(doc.attached_to_name)
		or frappe.has_permission("HR Request", "write", doc.attached_to_name)
	):
		frappe.throw(
			_("You can't attach a file to that request."),
			frappe.PermissionError,
		)

	_enforce_upload_policy(doc)


def _enforce_upload_policy(doc):
	"""P2-U9 step 5. The same type/size/signature policy
	`helixhr.api.attach_to_my_request` applies, applied again here so that a
	File inserted by any *other* path -- Desk, a script, a caller that found
	another way in -- cannot put an SVG, an HTML page, a macro-enabled
	document or a renamed executable behind an HR Request.

	One policy for everybody who attaches to this doctype, HR included:
	the point of the rule is what the stored bytes are, and that does not
	change with who uploaded them. `helixhr.utils.set_security_headers`
	serves everything under it as a download for the same reason.

	Content first, name second. `get_content()` answers from `doc.content`
	while the document is being created and from the saved path afterwards;
	when it can answer neither, the extension rule alone still applies
	rather than the check silently passing.
	"""
	try:
		content = doc.get_content()
	except Exception:
		content = None
	if isinstance(content, str):
		content = content.encode("utf-8", "surrogateescape")

	if isinstance(content, bytes | bytearray):
		validate_portal_upload(doc.file_name, content)
		return

	if upload_extension(doc.file_name) not in UPLOAD_POLICY:
		frappe.throw(
			_("You can attach a PDF, a PNG or JPEG image, or a Word or Excel document.")
		)
