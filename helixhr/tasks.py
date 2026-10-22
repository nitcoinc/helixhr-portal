"""Scheduled work. P3-U4 step 2a / P3-KTD15 / P3-R28.

Punch coordinates are the one thing this portal stores that nobody needs
after the punch has become Attendance: HR and every manager up the chain can
read a report's Employee Checkin rows, so a year of them is a location
history of a person's mornings. DPDP section 8(7) asks for erasure once the
purpose is served, and the purpose is served the moment the attendance is
marked -- so the coordinates have a retention period, and the period is the
only value HR and legal decide (`docs/deployment.md`).

The job is idle until `helixhr_checkin_location_retention_days` is set in the
site config, and `preflight.check_checkin_location_retention` says so while
it is. `frappe.conf` rather than a Singles row on purpose: this is an
operator decision, it must not be editable from Desk by the roles whose
punches it governs, and it belongs in the same place as the rest of the
site's operational configuration.
"""

import json

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, formatdate, get_fullname, getdate, now_datetime, strip_html

from helixhr.helixhr.doctype.helixhr_leaving.helixhr_leaving import OPEN_STATUSES
from helixhr.utils import company_today

RETENTION_KEY = "helixhr_checkin_location_retention_days"
# The three fields a punch's location lives in, and the value each one is
# erased *to*. `geolocation` is HRMS's own GeoJSON copy, written by
# `set_geolocation_from_coordinates` -- nulling the two floats and leaving it
# behind would erase nothing. Frappe's Float columns are NOT NULL, so
# "null the coordinates" is 0 for the two floats and an empty string for the
# GeoJSON text -- which is also what makes `["latitude", "is", "set"]` stop
# matching an erased row, and therefore what makes the job idempotent.
ERASED = {"latitude": 0, "longitude": 0, "geolocation": ""}
LOCATION_FIELDS = tuple(ERASED)
# One statement per batch, so a first run on a site with years of punches
# does not build a single unbounded UPDATE.
_BATCH = 500
# A first run walks a backlog; anything beyond this many batches is a sign
# the filter is not narrowing and the job should stop rather than spin.
_MAX_BATCHES = 200

_LOCATION_SET = [[field, "is", "set"] for field in LOCATION_FIELDS]


def null_stale_checkin_coordinates():
	"""Daily (`hooks.scheduler_events`). Erase the coordinates of punches
	older than the configured retention period, and the same values out of
	their Version history.

	Idempotent by construction: the filter only ever matches rows that still
	carry a coordinate, so a second run in the same day finds nothing.
	"""
	days = cint(frappe.conf.get(RETENTION_KEY))
	if days <= 0:
		return {"retention_days": days, "scrubbed": 0}

	cutoff = add_to_date(now_datetime(), days=-days)
	scrubbed = 0
	for _batch in range(_MAX_BATCHES):
		names = frappe.get_all(
			"Employee Checkin",
			filters={"time": ["<", cutoff]},
			or_filters=_LOCATION_SET,
			pluck="name",
			limit=_BATCH,
			order_by="time asc",
		)
		if not names:
			break
		scrubbed += scrub_checkin_locations(names)
		# One commit per batch. A first run over a real backlog is 200
		# batches of 500 rows plus their Version rewrites, which is well
		# past the scheduled-job timeout -- and a single transaction that
		# times out rolls the whole sweep back, so the job never makes any
		# progress at all. Committing per batch is safe precisely because
		# the sweep is idempotent: a run that dies half way leaves the
		# batches it finished erased and the next run starts from what is
		# left. Only here -- `scrub_checkin_locations` itself stays
		# commit-free, because the status-to-Left path calls it inside the
		# Employee save's own transaction (P3-KTD15).
		frappe.db.commit()
	return {"retention_days": days, "scrubbed": scrubbed}


def scrub_employee_checkin_locations(employee):
	"""Every punch this employee still has a location on. Called from
	`events.employee_on_update` the moment their status becomes Left: the
	retention period is about how long the data stays useful, and for
	somebody who has left it stopped being useful on their last day."""
	scrubbed = 0
	for _batch in range(_MAX_BATCHES):
		names = frappe.get_all(
			"Employee Checkin",
			filters={"employee": employee},
			or_filters=_LOCATION_SET,
			pluck="name",
			limit=_BATCH,
		)
		if not names:
			break
		scrubbed += scrub_checkin_locations(names)
	return scrubbed


def scrub_checkin_locations(names):
	"""Null the three location fields on these punches and take the same
	fields out of their Version rows.

	`frappe.db.set_value` rather than a document save: a save would rerun
	HRMS's `validate` (which refuses a coordinate-less punch while
	geolocation tracking is on, and would refuse to erase anything at all)
	and would rewrite `modified`, making an erasure look like an edit.
	Employee Checkin has `track_changes`, so the values also sit in
	`tabVersion` -- erasing the row and leaving the audit trail erases
	nothing.
	"""
	if not names:
		return 0
	frappe.db.set_value(
		"Employee Checkin",
		{"name": ["in", names]},
		dict(ERASED),
		update_modified=False,
	)
	_scrub_versions(names)
	return len(names)


def _scrub_versions(names):
	for row in frappe.get_all(
		"Version",
		filters={"ref_doctype": "Employee Checkin", "docname": ["in", names]},
		fields=["name", "data"],
	):
		try:
			data = json.loads(row.data or "{}")
		except (TypeError, ValueError):
			continue
		if not isinstance(data, dict):
			continue
		changed = False
		for key in ("changed", "added", "removed"):
			entries = data.get(key)
			if not isinstance(entries, list):
				continue
			kept = [
				entry
				for entry in entries
				if not (isinstance(entry, list) and entry and entry[0] in LOCATION_FIELDS)
			]
			if len(kept) != len(entries):
				data[key] = kept
				changed = True
		if changed:
			frappe.db.set_value("Version", row.name, "data", json.dumps(data), update_modified=False)


# ---------------------------------------------------------------------------
# Plan 2026-10-22-001 U3: applying a leaving scheduled from the portal.
# One function serves `api.relieve_employee` (a date on or before today),
# `api.retry_leaving` and the half-hourly job below.
# ---------------------------------------------------------------------------

LEAVING_DOCTYPE = "HelixHR Leaving"
LEAVING_LOG_TITLE = "HelixHR leaving failed"
LEAVING_APPLIED_MARK = "helixhr:leaving_applied"
_LEAVING_SAVEPOINT = "helixhr_leaving"


def leaving_manager_problem(leaver, new_manager, leaving_date):
	"""Why ``new_manager`` cannot take ``leaver``'s team on ``leaving_date``
	(R14), in plain words, or None. Asked when the leaving is scheduled and
	again when it applies: the manager may have left in between.

	Scope is the caller's question and is asked by the endpoint; here the
	manager must be someone else, Active, not leaving on or before the same
	date (a scheduled leaving writes `relieving_date` ahead), and not below
	the leaver except as a direct report -- ERPNext's tree refuses that move
	in words HR cannot act on."""
	if not new_manager:
		return None
	if new_manager == leaver:
		return _("Choose someone other than the person leaving as the new manager.")
	row = frappe.db.get_value(
		"Employee", new_manager, ["employee_name", "status", "relieving_date", "reports_to"], as_dict=True
	)
	if not row:
		return _("{0} isn't an employee here.").format(new_manager)
	if row.status != "Active":
		return _("{0} is {1}, so they can't take over the team. Choose another manager.").format(
			row.employee_name, _(row.status)
		)
	if row.relieving_date and getdate(row.relieving_date) <= getdate(leaving_date):
		return _("{0} is leaving on {1}, so they can't take over the team. Choose another manager.").format(
			row.employee_name, formatdate(row.relieving_date)
		)
	# Walk up from the new manager; the leaver two or more levels up means
	# the leaver's direct report would end up reporting to their own report.
	seen, current, depth = set(), row.reports_to, 1
	while current and current not in seen:
		if current == leaver:
			if depth > 1:
				return _(
					"{0} is further down {1}'s team. Choose one of {1}'s direct reports or someone outside the team."
				).format(row.employee_name, frappe.db.get_value("Employee", leaver, "employee_name"))
			break
		seen.add(current)
		current, depth = frappe.db.get_value("Employee", current, "reports_to"), depth + 1
	return None


def apply_leaving(name, actor=None):
	"""Apply the open leaving ``name`` in the caller's transaction (R11,
	KTD5): move every Active direct report found *now* to the new manager --
	the new manager themself, if one of them, to the leaver's own manager
	(KTD4) -- then set the leaver Left with the date and reason. ERPNext
	disables the login and HelixHR's Left hooks run in the same save.

	Raises a `frappe.ValidationError` in plain words when it cannot apply,
	with nothing written. Report moves save with `ignore_permissions`
	(KTD7): the leaver's scope gate already ran, and a report in another
	company may sit outside the caller's User Permissions; validation still
	runs. The leaver saves with the caller's own permissions (Administrator
	in the job). ``actor`` names who applied it for the Info comment."""
	from helixhr.api import _employee_note

	row = frappe.get_doc(LEAVING_DOCTYPE, name, for_update=True)
	if row.status not in OPEN_STATUSES:
		frappe.throw(_("This leaving is {0} and can't be applied.").format(_(row.status)))
	leaver = frappe.get_doc("Employee", row.employee, for_update=True)
	if leaver.status != "Active":
		frappe.throw(
			_("{0} is {1}, not Active, so the leaving can't be applied.").format(
				leaver.employee_name, _(leaver.status)
			)
		)
	problem = leaving_manager_problem(leaver.name, row.new_manager, row.leaving_date)
	if problem:
		frappe.throw(problem)

	reports = frappe.get_all(
		"Employee", filters={"reports_to": leaver.name, "status": "Active"}, pluck="name", order_by="name asc"
	)
	if reports and not row.new_manager:
		frappe.throw(
			_("{0} has {1} people reporting to them now. Choose who they report to next.").format(
				leaver.employee_name, len(reports)
			)
		)
	# The new manager first, so nobody is ever moved under someone who is
	# still below the leaver.
	reports.sort(key=lambda report: report != row.new_manager)
	for report in reports:
		doc = frappe.get_doc("Employee", report)
		doc.reports_to = leaver.reports_to if report == row.new_manager else row.new_manager
		save_employee_plainly(doc, ignore_permissions=True)

	leaver.relieving_date = row.leaving_date
	leaver.reason_for_leaving = row.reason
	leaver.status = "Left"
	save_employee_plainly(leaver)

	row.status = "Applied"
	row.applied_on = now_datetime()
	row.error = None
	row.save(ignore_permissions=True)

	moved = (
		_(" {0} direct report(s) now report to {1}.").format(len(reports), row.new_manager_name)
		if reports
		else ""
	)
	_employee_note(
		leaver.name,
		LEAVING_APPLIED_MARK,
		_("{0} relieved {1} from {2}.").format(
			actor or get_fullname(frappe.session.user), leaver.employee_name, formatdate(row.leaving_date)
		)
		+ moved,
	)
	return row


def save_employee_plainly(doc, ignore_permissions=False):
	"""`doc.save()`, with ERPNext's refusals turned into sentences HR can
	act on (R13, the plan's "wording leaks" risk). The leaving's own checks
	run first, so these are the races and the unforeseen."""
	try:
		doc.save(ignore_permissions=ignore_permissions)
	except frappe.ValidationError as exc:
		if isinstance(exc, frappe.PermissionError):
			raise
		frappe.clear_last_message()
		message = " ".join(strip_html(str(exc)).split()) or _("an unknown error")
		frappe.throw(_("{0} couldn't be saved: {1}").format(doc.employee_name, message))


def apply_due_leavings():
	"""On the hour and half hour (`hooks.scheduler_events["cron"]`, KTD6):
	apply every open leaving whose date has come on its company's own clock
	(`utils.company_today`), so each company relieves at its own midnight.

	Idempotent by filter: an applied row is no longer open, so a second run
	the same day finds nothing to do. A Failed row is retried each tick, so
	fixing the cause is enough. Each row runs in its own savepoint; a
	failure is rolled back to it, stored on the row in plain words, and
	logged to Error Log once per row per company-local day. Commits per row
	outside tests only (the reminders' `_commit` rule)."""
	result = {"applied": 0, "failed": 0}
	today = {}
	for row in frappe.get_all(
		LEAVING_DOCTYPE,
		filters={"status": ["in", OPEN_STATUSES]},
		fields=["name", "company", "leaving_date", "scheduled_by"],
		order_by="leaving_date asc",
	):
		if row.company not in today:
			today[row.company] = company_today(row.company)
		if getdate(row.leaving_date) > today[row.company]:
			continue
		frappe.db.savepoint(_LEAVING_SAVEPOINT)
		try:
			apply_leaving(row.name, actor=_scheduled_actor(row.scheduled_by))
			result["applied"] += 1
		except Exception as exc:
			frappe.db.rollback(save_point=_LEAVING_SAVEPOINT)
			frappe.clear_last_message()
			plain = isinstance(exc, frappe.ValidationError) and " ".join(strip_html(str(exc)).split())
			_mark_leaving_failed(row.name, plain or None, today[row.company])
			result["failed"] += 1
		_commit()
	return result


def _scheduled_actor(user):
	return _("HelixHR, as scheduled by {0},").format(get_fullname(user))


def _mark_leaving_failed(name, message, day):
	"""Failed, with ``message`` (None: an unexpected error, whose detail
	goes to Error Log only). The employee stays Active. Logged once per row
	per ``day``: the job retries every half hour."""
	message = message or _(
		"Something went wrong applying this leaving. Try again, or ask your administrator to check the Error Log."
	)
	row = frappe.db.get_value(LEAVING_DOCTYPE, name, ["status", "error", "failed_on"], as_dict=True)
	logged_today = row.failed_on and getdate(row.failed_on) == day
	if row.status == "Failed" and row.error == message and logged_today:
		return
	if not logged_today:
		frappe.log_error(
			title=LEAVING_LOG_TITLE,
			message=f"{message}\n\n{frappe.get_traceback()}",
			reference_doctype=LEAVING_DOCTYPE,
			reference_name=name,
		)
	frappe.db.set_value(LEAVING_DOCTYPE, name, {"status": "Failed", "error": message, "failed_on": day})


def _commit():
	"""A row's outcome commits on its own, except under tests: a test relies
	on its rollback."""
	if not frappe.flags.in_test:
		frappe.db.commit()
