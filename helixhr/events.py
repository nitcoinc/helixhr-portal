import frappe
from frappe import _
from frappe.model import no_value_fields
from frappe.utils import cint

from helixhr.helixhr.doctype.hr_request.hr_request import request_belongs_to_session
from helixhr.utils import UPLOAD_POLICY, get_manager_user, upload_extension, validate_portal_upload

# Timesheet workflow document event hooks (KTD7, KTD18). Two guards, both
# needed because Frappe's workflow engine only enforces "does the acting
# user have the required role" -- not "is this specific document theirs to
# act on" -- and a manager's own User Permission (scoped to their own
# Employee record) would otherwise hide their reports' timesheets entirely.


# The one workflow state that carries a share. Everything else -- Draft,
# Approved, Sent Back, Pending HR, Cancelled -- has nothing for *this*
# approver to do, so the access goes away with the decision (P2-U7 scenario
# 8). Pending HR is deliberately shareless: HR's Approve there rides HR
# Manager's native submit on Timesheet (P4-U1).
PENDING_STATE = "Pending Approval"

# P4-KTD1. The recoverable outcome on the Timesheet workflow, renamed from
# "Rejected" -- a week is never finally rejected (P4-KTD2), so this is the
# only state the `Edit` transition hangs off.
TIMESHEET_SENT_BACK = "Sent Back"

# P4-KTD7. Where a week waits after a manager hands it over. Named here
# rather than repeated in `api` because the HR queue collector, the
# already-decided gate and the notification fixture all have to agree on the
# same string.
TIMESHEET_PENDING_HR = "Pending HR"

# P4-KTD7a. Where an approver's reason lives, on both workflow kinds. A
# Comment is still added for the timeline, but a Comment dies with the
# document and a rejected attendance request is removable (P4-KTD3), so the
# reason the employee reads is a field of the record itself.
DECISION_REASON_FIELD = "helixhr_decision_reason"

# P4-KTD4. The one stage value that changes who may act on a leave request.
# `api` imports this name rather than repeating the string, so the portal's
# check and this hook's cannot drift.
LEAVE_STAGE_HR = "HR"


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
		_reconcile_pending_documents(doc.name)

	if before.user_id != doc.user_id or before.status != doc.status:
		for report in frappe.get_all("Employee", filters={"reports_to": doc.name}, pluck="name"):
			_reconcile_pending_documents(report)

	# P3-U4 step 2a / P3-KTD15 / P3-R28. Somebody who has left has no
	# purpose left for their punch coordinates to serve, so they go now
	# rather than at the end of the retention period. Inside the Employee
	# save's own transaction: the status change and the erasure commit
	# together or not at all.
	if before.status != doc.status and doc.status == "Left":
		from helixhr.tasks import scrub_employee_checkin_locations

		scrub_employee_checkin_locations(doc.name)


def _reconcile_pending_documents(employee):
	"""Point everything `employee` has waiting at whoever may act on it now
	-- pending weeks (P2-U7) and pending attendance requests (P3-R18), which
	are the same defect on two doctypes."""
	manager_user = _approver_user(employee)
	for name in frappe.get_all(
		"Timesheet",
		filters={"employee": employee, "workflow_state": PENDING_STATE, "docstatus": 0},
		pluck="name",
	):
		_reconcile_timesheet_share(name, employee, manager_user)

	for name in frappe.get_all(
		"Attendance Request",
		filters={"employee": employee, "workflow_state": REQUEST_PENDING_MANAGER, "docstatus": 0},
		pluck="name",
	):
		# submit=1 for the same reason as the on_update grant (P4-KTD5): the
		# new manager's Approve is the submit, so a reassignment that moved a
		# read/write-only share would hand them a request they cannot decide.
		_reconcile_share("Attendance Request", name, employee, manager_user, submit=1)


def timesheet_before_submit(doc, method=None):
	"""Refuses any submit (Pending Approval -> Approved, docstatus 0->1)
	unless the acting user is this timesheet's manager, HR Manager, or
	System Manager -- covers both the workflow's own Approve action and a
	raw frappe.client.submit() call that skips the workflow entirely
	(AE6). The workflow's per-transition `allow_self_approval=0` already
	stops the employee approving their own via the workflow path; this
	hook is what stops the same self-approval attempt made directly."""
	user = frappe.session.user
	# P4-R8: nobody approves their own week, on any route. The workflow's
	# per-transition `allow_self_approval=0` and `user_id != session.user`
	# condition cover `apply_workflow`; `frappe.client.submit` consults no
	# transition at all, and an HR Manager reaches it through their own
	# native submit on Timesheet -- which is the bug this closes.
	# Administrator is exempt: it is the migration and backfill account, not
	# a person with weeks of their own.
	if user != "Administrator" and frappe.db.get_value("Employee", doc.employee, "user_id") == user:
		frappe.throw(
			_("You can't approve your own timesheet. Ask your manager or another HR Manager."),
			frappe.PermissionError,
		)
	if _is_hr(user) or user == get_manager_user(doc.employee):
		return

	frappe.throw(
		_("Only {0}'s manager or HR can approve this timesheet.").format(doc.employee),
		frappe.PermissionError,
	)


def leave_application_before_submit(doc, method=None):
	"""The raw-route half of P4-R8 and P4-R8a for leave.

	Leave has no Workflow (P2-KTD17) -- HRMS's own lifecycle is the correct
	one -- so `api._may_act_on_leave` is the portal's check and this hook is
	the only thing standing on every other route. Two refusals:

	  * Nobody submits their own application. HRMS's
	    `validate_for_self_approval` refuses one too, but only while HR
	    Settings' `prevent_self_leave_approval` is on, only for status
	    Approved, and not at all if a Workflow is ever added to Leave
	    Application -- so the rule R8 actually states is asserted here
	    unconditionally. Administrator is exempt: it is the migration and
	    backfill account, not a person with leave of their own.
	  * While the *stored* stage is HR, only `_is_hr` may submit. HRMS shares
	    every application with its `leave_approver` at `submit=1`
	    (`hrms.hr.utils.share_doc_with_approver`), so the manager of an
	    escalated request keeps a Desk route to Approve that never consults
	    the portal. The stored value is what counts -- `helixhr_stage` is
	    permlevel 1, so an in-memory value from a non-HR session was reset on
	    the way in, and reading the row is also what makes a raw
	    `frappe.client.submit` answerable.
	"""
	user = frappe.session.user
	if user != "Administrator" and frappe.db.get_value("Employee", doc.employee, "user_id") == user:
		frappe.throw(
			_("You can't approve your own leave request. Ask your manager or HR."),
			frappe.PermissionError,
		)

	stored_stage = frappe.db.get_value("Leave Application", doc.name, "helixhr_stage")
	if stored_stage == LEAVE_STAGE_HR and not _is_hr(user):
		frappe.throw(
			_("This leave request is with HR now, so only HR can decide it."),
			frappe.PermissionError,
		)


def _reconcile_timesheet_share(name, employee, keep_user):
	"""The Timesheet call of `_reconcile_share`: an approver's share on a
	pending week carries `submit=1`, because the Approve transition on that
	workflow *is* the submit."""
	_reconcile_share("Timesheet", name, employee, keep_user, submit=1)


def _reconcile_share(doctype, name, employee, keep_user, submit=0):
	"""Leave exactly one approver share on this document -- `keep_user`'s
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

	Generalised from the Timesheet-only version in P3-U5: an Attendance
	Request needs the same reconcile, and since P4-KTD5 with the same
	`submit=1` -- its manager step is the submit now that approval is one
	step.
	"""
	employee_user = frappe.db.get_value("Employee", employee, "user_id")
	for share in frappe.get_all(
		"DocShare",
		filters={"share_doctype": doctype, "share_name": name},
		fields=["name", "user"],
	):
		if share.user and share.user in (keep_user, employee_user):
			continue
		frappe.db.delete("DocShare", {"name": share.name})

	if keep_user:
		# add_docshare is itself idempotent (looks up any existing DocShare
		# for this user/doc and updates it rather than duplicating).
		frappe.share.add_docshare(
			doctype,
			name,
			keep_user,
			write=1,
			submit=submit,
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


# Attendance request: the two-step approval's server-side rules (P3-U5,
# P3-KTD8, P3-KTD9, P3-R17, P3-R17a, P3-R18).
#
# Frappe validates workflow *transitions*, never a state's `allow_edit`, and
# never who a document belongs to. The fixture therefore only decides who may
# move the state; everything else -- what may change, who may delete, who may
# perform the real submit, and who can see the request while it waits -- is
# here. None of these commit: a throw rolls the transition and the share back
# together, because doc events run inside the request's own transaction.

REQUEST_DRAFT = "Draft"
REQUEST_PENDING_MANAGER = "Pending Manager"
REQUEST_PENDING_HR = "Pending HR"
REQUEST_APPROVED = "Approved"
# P4-KTD1: two states, two meanings. Sent Back is recoverable (`Edit` returns
# it to Draft); Rejected is a final no with no transition out of it.
REQUEST_SENT_BACK = "Sent Back"
REQUEST_REJECTED = "Rejected"

# The states an employee may still take their own request out of -- the same
# list the portal's withdraw offers, applied to every route (P3-R17a).
#
# Rejected is on the list for a different reason than the rest (P4-KTD3): a
# terminal row at docstatus 0 would block the same dates for ever, because
# HRMS's `validate_request_overlap` refuses a new request over any existing
# one below docstatus 2 and a Workflow cannot move a document from 0 to 2. So
# *terminal* means terminal for the row, not for the dates -- the employee
# removes it, and the approver's reason survives in the Deleted Document
# snapshot because it is a field of the record (P4-KTD7a).
REQUEST_WITHDRAWABLE = (
	REQUEST_DRAFT,
	REQUEST_PENDING_MANAGER,
	REQUEST_SENT_BACK,
	REQUEST_REJECTED,
)

# Outside Draft the state is the only thing that moves. `shift` is the one
# exception, and only while it is still empty: HRMS's `validate_shifts` fills
# it from the employee's Shift Assignment when the field has no value, and
# never once it has (P3-KTD8). Exempting it unconditionally let an employee
# (or their manager, through the pending-state DocShare) PUT any Shift Type
# onto a pending request, and HR's confirmation then wrote Attendance against
# a shift the person was never assigned -- HRMS checks neither.
#
# P4-KTD7a adds `helixhr_decision_reason`: the approver writes their reason on
# the same request they are about to move, so the freeze has to let it
# through. It is not a hole -- the field is at permlevel 1, so a save by
# anyone without HR's level-1 grant leaves it as it was whatever this rule
# says.
REQUEST_MUTABLE_FIELDS = {"workflow_state", DECISION_REASON_FIELD}

# A decision taken in Desk carries no reason of its own -- only
# `act_on_approval` requires one -- and "sent back" with nothing at all is
# worse than a sentence naming who to ask (P3-KTD9). State neutral on
# purpose: either step, and either of the two negative outcomes, can arrive
# without a reason, so naming HR would be wrong for a manager's decision.
ATTENDANCE_REQUEST_SENT_BACK_FALLBACK = (
	"No reason was given, ask your manager or HR for details"
)

_HR_ROLES = {"HR Manager", "System Manager"}


def _is_hr(user=None):
	user = user or frappe.session.user
	return user == "Administrator" or bool(set(frappe.get_roles(user)) & _HR_ROLES)


def _request_dates(from_date, to_date):
	""""3 September" for one day, "3 to 5 September" for a range. Plain
	words, no Frappe vocabulary (P3-R24)."""
	from frappe.utils import formatdate

	if not from_date:
		return ""
	if not to_date or str(from_date) == str(to_date):
		return formatdate(from_date)
	return f"{formatdate(from_date)} to {formatdate(to_date)}"


def attendance_request_subject(state, from_date, to_date, manager_name=None):
	"""The one line the employee reads in their notifications. Written and
	matched in one place, like HR_REPLY_SUBJECT_PREFIX, so the portal can ask
	for these rows without a marker field Notification Log does not have."""
	dates = _request_dates(from_date, to_date)
	if state == REQUEST_PENDING_MANAGER:
		with_whom = manager_name or "your manager"
		return f"Your attendance request for {dates} is with {with_whom}"
	if state == REQUEST_PENDING_HR:
		return f"Your attendance request for {dates} is with HR"
	if state == REQUEST_APPROVED:
		return f"Your attendance request for {dates} counts"
	if state == REQUEST_SENT_BACK:
		return f"Your attendance request for {dates} was sent back"
	if state == REQUEST_REJECTED:
		return f"Your attendance request for {dates} was rejected"
	return f"Your attendance request for {dates} changed"


def attendance_request_validate(doc, method=None):
	"""Two rules, both of which the fixture cannot express.

	The field freeze (P3-R17a): once a request has left Draft, no field of
	its own changes through any route -- `/api/resource` PUT,
	`frappe.client.set_value`, a Desk save -- unless the caller is HR. The
	diff is against `get_doc_before_save()`, the persisted row, so it holds
	however the change arrived.

	The manager requirement (P3-R14): the Submit transition is a plain field
	update, not a docstatus submit, so this is the one place every path that
	tries to send a request actually passes through. Same sentence as the
	Timesheet's, from the same rule.
	"""
	before = doc.get_doc_before_save()

	if (
		before
		and before.get("workflow_state") == REQUEST_DRAFT
		and doc.workflow_state == REQUEST_PENDING_MANAGER
		and not _approver_user(doc.employee)
	):
		frappe.throw(
			_(
				"You don't have a manager set up to approve this yet. "
				"Ask HR to set one before sending it."
			)
		)

	if not before or before.get("workflow_state") in (None, REQUEST_DRAFT) or _is_hr():
		return

	mutable = set(REQUEST_MUTABLE_FIELDS)
	if not before.get("shift"):
		mutable.add("shift")

	changed = [
		field.fieldname
		for field in doc.meta.fields
		if field.fieldname not in mutable
		and not field.is_virtual
		and field.fieldtype not in no_value_fields
		and (doc.get(field.fieldname) or None) != (before.get(field.fieldname) or None)
	]
	if changed:
		frappe.throw(
			_("This request is already with your manager, so it can't be changed. Ask HR to change it."),
			frappe.PermissionError,
		)


def attendance_request_on_update(doc, method=None):
	"""The manager's access, and the employee's notification, per state
	change.

	The share is load-bearing, not a convenience: a manager's own User
	Permission is scoped to their own Employee record and does not reach a
	report's Attendance Request at all, so without it `get_transitions`
	fails on read before the transition is even considered. It exists in
	Pending Manager and nowhere else -- every other state has nothing for
	an approver to do (P3-KTD8).

	P4-KTD5: the share now carries `submit=1`. Single-step approval means the
	manager's Approve *is* the docstatus 0 -> 1 transition, the same shape a
	timesheet approval has always had, and role Employee has no `submit` on
	Attendance Request of its own -- the share is the whole grant, and it
	exists only while the request is Pending Manager and only for the Active
	reports-to user.
	"""
	if doc.workflow_state == REQUEST_PENDING_MANAGER and doc.docstatus == 0:
		_reconcile_share(
			"Attendance Request",
			doc.name,
			doc.employee,
			_approver_user(doc.employee),
			submit=1,
		)
	else:
		_reconcile_share("Attendance Request", doc.name, doc.employee, None)

	before = doc.get_doc_before_save()
	if not before or before.get("workflow_state") == doc.workflow_state:
		return
	_notify_attendance_request(doc)


def _notify_attendance_request(doc):
	"""One Notification Log row per state change, addressed to the
	*employee's* user -- never to `owner`, which is the HR login when HR
	raised the request for somebody (P3-KTD9, P3-R17)."""
	if doc.workflow_state not in (
		REQUEST_PENDING_MANAGER,
		REQUEST_PENDING_HR,
		REQUEST_APPROVED,
		REQUEST_SENT_BACK,
		REQUEST_REJECTED,
	):
		return

	for_user = frappe.db.get_value("Employee", doc.employee, "user_id")
	if not for_user or for_user == frappe.session.user:
		# Nobody tells you what you just did -- which is exactly the
		# employee's own Draft -> Pending Manager step.
		return

	manager_name = None
	if doc.workflow_state == REQUEST_PENDING_MANAGER:
		reports_to = frappe.db.get_value("Employee", doc.employee, "reports_to")
		manager_name = frappe.db.get_value("Employee", reports_to, "first_name") if reports_to else None

	description = None
	if doc.workflow_state in (REQUEST_SENT_BACK, REQUEST_REJECTED):
		# P4-KTD7a: the field on the record, not the newest comment. The
		# comment scrape it replaces had to be scoped to the acting user to
		# avoid quoting the employee's own last remark back at them, and it
		# died with a removed request (P4-KTD3); a field does neither.
		description = (doc.get(DECISION_REASON_FIELD) or "").strip() or _(
			ATTENDANCE_REQUEST_SENT_BACK_FALLBACK
		)

	frappe.get_doc(
		{
			"doctype": "Notification Log",
			"for_user": for_user,
			"from_user": frappe.session.user,
			"type": "Alert",
			"document_type": "Attendance Request",
			"document_name": doc.name,
			"subject": attendance_request_subject(
				doc.workflow_state, doc.from_date, doc.to_date, manager_name
			),
			"description": frappe.utils.escape_html(description) if description else None,
		}
	).insert(ignore_permissions=True)


def attendance_request_before_submit(doc, method=None):
	"""Who may perform the real submit -- the one HRMS turns into Attendance
	rows -- and from where (P3-R15, P3-R17a, P4-KTD5, P4-R6, P4-R8).

	Single-step approval (P4-R6) makes the *manager's* Approve this submit,
	so the table has two rows rather than one:

	  the reports-to approver   from a stored Pending Manager
	  `_is_hr`                  from Pending Manager, Pending HR or Draft

	Anyone else is refused. HR keeps Pending Manager and Draft because HR can
	decide anything -- a request handed over, and one HR raised itself for
	somebody (P4-KTD1's Draft -> Approved edge). HR User is excluded on
	purpose; deciders need HR Manager.

	`get_doc_before_save()` rather than `doc.workflow_state`: by the time
	this runs, `apply_workflow` has already set the in-memory field to
	Approved, so the *stored* state is the only evidence of where the
	request actually came from. That is what stops a raw
	`frappe.client.submit` jumping Sent Back, Rejected or Draft straight to
	Approved.
	"""
	before = doc.get_doc_before_save()
	stored_state = before.get("workflow_state") if before else None
	user = frappe.session.user

	# P3-KTD6 / P4-R8: nobody decides their own request, on either branch.
	# It used to sit inside the HR branch alone, which was enough while only
	# HR could submit; a `reports_to` pointing at oneself now reaches the
	# manager branch with only the `_approver_user` equality in the way.
	# Administrator is exempt: it is the migration and backfill account, not
	# a person with requests of their own.
	if user != "Administrator" and frappe.db.get_value("Employee", doc.employee, "user_id") == user:
		frappe.throw(
			_("You can't decide your own attendance request. Ask your manager or HR."),
			frappe.PermissionError,
		)

	if _is_hr():
		allowed_from = (REQUEST_DRAFT, REQUEST_PENDING_MANAGER, REQUEST_PENDING_HR)
	elif user == _approver_user(doc.employee):
		allowed_from = (REQUEST_PENDING_MANAGER,)
	else:
		frappe.throw(
			_("Only {0}'s manager or HR can approve this attendance request.").format(doc.employee),
			frappe.PermissionError,
		)

	if (stored_state or REQUEST_DRAFT) not in allowed_from:
		frappe.throw(
			_("This request isn't waiting for a decision, so it can't be approved."),
			frappe.PermissionError,
		)


def attendance_request_on_trash(doc, method=None):
	"""Delete follows the same states as the portal's withdraw, whichever
	route asks (P3-R17a).

	The employee is matched by `Employee.user_id`, never by `owner`: a
	request HR raised for them is theirs to withdraw, and one they raised
	themselves that HR has already moved on is not.
	"""
	if not _is_hr():
		employee_user = frappe.db.get_value("Employee", doc.employee, "user_id")
		if employee_user != frappe.session.user:
			frappe.throw(_("That attendance request isn't yours."), frappe.PermissionError)
		if (doc.workflow_state or REQUEST_DRAFT) not in REQUEST_WITHDRAWABLE:
			frappe.throw(
				_("This one is already with HR. Ask HR to sort it out before removing it."),
			)

	# The share this app granted goes with the document. `frappe.db.delete`
	# for the reason _reconcile_share gives: a permission-checked delete of
	# DocShare would fail for the very employee whose request this is.
	frappe.db.delete("DocShare", {"share_doctype": "Attendance Request", "share_name": doc.name})
