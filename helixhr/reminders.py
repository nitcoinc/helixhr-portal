# Copyright (c) 2026, HelixHR Contributors
# For license information, please see license.txt

"""The birthday and work-anniversary emails HR words themselves. P4-U6 /
P4-KTD10..KTD13 / P4-R15..R20, rewired onto `HelixHR Celebration Reminder`
in P8-U10/U11 (KTD6, KTD7).

HRMS already sends both, from a hardcoded subject, header and Jinja file, and
the only switch is a checkbox. There is no Email Template record to edit, so
the copy cannot be changed without editing HRMS -- which the next
`bench update` overwrites (P4-R20 is that no HRMS file is touched).

So this job runs *beside* HRMS's, in the same daily slot, and reads one
`HelixHR Celebration Reminder` row per (event, company) -- its
`email_template` (a Link to Email Template), `is_enabled`, `recipient_mode`
("All employees" or "Selected employees") and, for the latter, its
`recipients` child table. No row for that company, disabled, or no template
picked, means HelixHR sends nothing for that event there (R1).
HRMS's two checkboxes keep working exactly as before: Frappe merges
`scheduler_events` across apps and offers no way to remove another app's job,
so both senders being on at once is a real hazard -- `events.hr_settings_validate`
refuses the save that would create it, `preflight.check_celebration_reminders`
is the post-deploy backstop (P4-KTD10), and the two HR Settings checkboxes
themselves are Desk-read-only fixtures (a Property Setter, P8-KTD8) so the
affordance for that hazard is never offered in the first place.

Nothing here is monkeypatched and no HRMS template file is shadowed. Both
were considered and declined: the subject and header stay hardcoded in HRMS
Python either way, and neither is HR-editable.

Superseded by plan 2026-10-05-001 U11: the render below now goes through
HelixHR's own sandbox (`utils.render_celebration_email`) -- the history that
follows is why. HR authors the Email Template from the portal (P8-U12), which makes its body
server-side Jinja HR controls -- rendered with `restrict_globals=True`
(P8-KTD7) rather than `Email Template.get_formatted_email`'s own unrestricted
render. `frappe.db.get_value` is a permission-free raw read available under
*both* global sets -- restricting rendering does not touch it -- so this is
not a defence against a template reading other records; verified against
Frappe's own `safe_exec.py` rather than assumed. What the restricted set
actually drops is everything with a *side effect*: `frappe.db.set_value`,
`frappe.new_doc`/`delete_doc`, `frappe.call` (arbitrary whitelisted method
invocation), `frappe.sendmail`, `db.commit`/`rollback` -- all present under
the default globals `get_formatted_email` would have rendered with, none of
them under `render_safe_globals()`. That is the actual boundary HR's own
template body is held to. The upgrade path, if HR's edit surface ever needs
to be narrower still, is the portal's other template engine: `HelixHR
Message Template` plus `helixhr.utils.render_tokens`, which is plain
substitution and executes nothing at all (P5-KTD11).

Who is celebrating, and who hears about it, is HRMS's answer (P4-KTD12): the
helpers below are imported, never re-implemented, so eligibility cannot
drift from the rule Home's own celebrations card follows. If HRMS renames one
of them this module fails to import -- loudly, in the scheduler log and in
`tests/test_reminders.py` on the next upgrade -- which is the trade P4-KTD12
makes on purpose over a quiet second implementation. Two consequences ride
along with it, both documented in `docs/deployment.md`: HRMS falls back to
`personal_email` for an employee with no User and no company email, so a
branded company email can reach a personal inbox; and the celebrating
person's own address is resolved with `get_employee_email` on both sides of
the set arithmetic (HRMS itself uses two different fallback orders -- one for
the exclusion, another for the shared-day email -- and this job uses one).
"""

import frappe
from erpnext.setup.doctype.employee.employee import get_employee_emails
from frappe.utils import add_days, add_months, cint, comma_sep, format_date, get_url, getdate
from hrms.controllers.employee_reminders import (
	get_all_employee_emails,
	get_employee_email,
	get_sender_email,
)

from helixhr.events import PENDING_SINCE_FIELD as PENDING_SINCE
from helixhr.utils import _company_role_holders, active_employee_for_user, company_today

# One row per (event, company) since plan 2026-10-04-004 U1 -- named
# `{event}-{company}` by the doctype's own autoname: the words HR reads on
# the Email Templates page's celebrations group, and the HRMS checkbox that
# would send the stock email for the same event.
# `events.hr_settings_validate` and `preflight.check_celebration_reminders`
# both quote from here, so the refusal and the preflight line name the same
# event the form does. A company with no row for an event sends nothing for
# it -- absent means disabled (R1).
EVENTS = {
	"birthday": {
		"label": "Birthday",
		"hrms_field": "send_birthday_reminders",
		"hrms_label": "Birthdays",
	},
	"work_anniversary": {
		"label": "Work anniversary",
		"hrms_field": "send_work_anniversary_reminders",
		"hrms_label": "Work Anniversaries",
	},
	# Plan 2026-10-04-004 U3: HelixHR takes over HRMS's holiday reminder
	# (`send_reminders_in_advance_weekly/monthly`), which was hardcoded
	# wording, global on/off, and per-employee sends. The row's own
	# `frequency` (Weekly / Monthly) is the cadence now.
	"holiday": {
		"label": "Holiday",
		"hrms_field": "send_holiday_reminders",
		"hrms_label": "Holidays",
	},
}

# Plan 2026-10-05-001 U11 (KTD12): the shipped default wording, body-only --
# the sender wraps it in the branded layout, which already carries the logo,
# the subject as a heading and the "Open HelixHR" button. Used by reset and
# by `patches/v1_0/clone_celebration_templates_per_company`; the applied
# `seed_celebration_templates` keeps its own (older) copy untouched.
CELEBRATION_DEFAULTS = {
	"birthday": {
		"template": "HelixHR Birthday Reminder",
		"subject": "{% if count > 1 %}Birthdays today: {{ names }}{% else %}"
		"Today is {{ names }}'s birthday{% endif %}",
		"body": "<p>{% if count > 1 %}{{ count }} of us are celebrating today: {{ names }}."
		"{% else %}{{ persons[0].first_name }} is celebrating today.{% endif %} "
		"A quick message goes a long way.</p>\n"
		"{% for person in persons %}<p style=\"margin:0 0 8px\">"
		"{% if person.image_url %}<img src=\"{{ person.image_url }}\" alt=\"\" width=\"32\" height=\"32\" "
		"style=\"border-radius:16px;vertical-align:middle;margin-right:8px\">{% endif %}"
		"{{ person.name }}</p>{% endfor %}",
	},
	"work_anniversary": {
		"template": "HelixHR Work Anniversary Reminder",
		"subject": "{% if count > 1 %}Work anniversaries today{% else %}"
		"{{ names }}: {{ persons[0].years }} years today{% endif %}",
		"body": "<p>{% if count > 1 %}Several of us mark another year at {{ company }} today."
		"{% else %}{{ persons[0].first_name }} marks {{ persons[0].years }} "
		"year{% if persons[0].years != 1 %}s{% endif %} at {{ company }} today.{% endif %} "
		"Thank you for the time you have given us.</p>\n"
		"{% for person in persons %}<p style=\"margin:0 0 8px\">{{ person.name }} &middot; "
		"{{ person.years }} year{% if person.years != 1 %}s{% endif %}</p>{% endfor %}",
	},
	"holiday": {
		"template": "HelixHR Holiday Reminder",
		"subject": "Holidays ahead at {{ company }}",
		"body": "<p>Hi {{ employee_name }}, here is what is coming up:</p>\n"
		"<ul style=\"margin:0 0 16px;padding-left:20px\">{% for holiday in holidays %}"
		"<li>{{ holiday.date }} &middot; {{ holiday.description }}</li>{% endfor %}</ul>\n"
		"<p>Plan ahead and enjoy the time off.</p>",
	},
}


def celebration_template_name(event, company):
	"""The Email Template one (event, company) row sends from (KTD12): one
	per company, so one company's edit never reaches another's mail."""
	return f"{CELEBRATION_DEFAULTS[event]['template']} - {company}"


# Plan 2026-10-08-001 U2: the once-per-(event, company, day) rerun guard is
# the reminder row's own `last_sent_on`, claimed with one conditional UPDATE
# before anything is sent (`_claim`). It used to be a Redis key, which a
# Redis restart or eviction drops -- harmless for a once-a-day job, a
# same-day resend for one that ticks every half hour. The dated keys under
# this prefix now only stop a failure being logged on every tick
# (`_log_once`); kept through `clear-cache` and `bench migrate` by
# `hooks.persistent_cache_keys`, they expire a day and a half later.
CELEBRATION_GUARD_PREFIX = "helixhr-celebration|"
CELEBRATION_GUARD_SECONDS = 36 * 60 * 60
REMINDER_DOCTYPE = "HelixHR Celebration Reminder"
CROSS_COMPANY_LOG_TITLE = "HelixHR cross-company mailboxes"
NO_COMPANY_LOG_TITLE = "HelixHR celebrations skipped for want of a company"


def send_celebration_reminders():
	"""On the hour and half hour (`hooks.scheduler_events["cron"]`, plan
	2026-10-08-001 U2). A tick with no company due -- nearly all of them --
	is one small query and returns (`_due`). Idle until HR enables the reminder and picks a template for the
	event, per company (P4-R17), so an install ships sending nothing.

	Each company is mailed on its *own* clock (`utils.company_today`): the
	first tick after midnight in its time zone sends about the people
	celebrating on its local date. A company with no time zone set follows
	the system zone, exactly as the old daily job did.

	Idempotent per (event, company, local day): the row's `last_sent_on` is
	claimed before sending (`_claim`), so a second tick -- or a hand-run, or
	a second worker -- sends nothing a company already received. A claim is
	released when nothing at all went out, so a company whose template was
	broken this morning and fixed this afternoon is still mailed today; once
	some mail went out it stands, because a retry would mail those people
	again.

	Failure is isolated per event and, inside `_send_event`, per company: one
	template that raises while rendering must not cost the other event its
	mail (P4-KTD13 hands HR the template, so a template that raises is HR's
	edit away). Every swallowed error goes to the scheduler log via
	`frappe.log_error` -- it must surface somewhere, not vanish.
	"""
	sent = {event: {"companies": 0, "emails": 0, "failed": 0} for event in EVENTS}
	if not _due([event for event in EVENTS if event != "holiday"]):
		return sent
	foreign, foreign_names = _foreign_address_companies()
	dropped = []
	skipped_no_company = 0
	for event in EVENTS:
		try:
			result, skipped = _send_event(event, foreign, foreign_names, dropped)
			skipped_no_company += skipped
			sent[event] = result
		except Exception:
			frappe.log_error(
				f"The {event} celebration reminder failed before it reached any company\n\n"
				f"{frappe.get_traceback()}",
				"HelixHR celebration reminders",
			)
			sent[event] = {"companies": 0, "emails": 0, "failed": 1}

	if dropped:
		# R3: once per run, naming both Employee records -- never the mail
		# body, which is nobody else's to read.
		# Keyword arguments on purpose: `log_error`'s positional hack
		# swaps a single-line first argument into the title, and one
		# dropped address is a single line.
		# Keyed by what was dropped, not the system date: companies on other
		# clocks send at other hours and drop other addresses.
		message = "\n".join(dropped)
		_log_once(f"cross-company|{_digest(message)}", title=CROSS_COMPANY_LOG_TITLE, message=message)
	if skipped_no_company:
		# R4: an employee with no company is never celebrated and never
		# mailed; the day's first tick says how many were skipped.
		_log_once(
			f"no-company|{getdate()}",
			title=NO_COMPANY_LOG_TITLE,
			message=f"{skipped_no_company} active employee(s) with no company were not celebrated "
			"and received no celebration mail -- give them a company on their Employee record.",
		)
	return sent


def _send_event(event, foreign, foreign_names, dropped):
	"""Every company with an enabled row for `event` and somebody
	celebrating on its own local date, each sent only that company's own
	setting (R1): no enabled row, no mail.

	Recipients are HRMS's own set arithmetic when `recipient_mode` is "All
	employees" (P4-R16): every active employee in that company, minus the
	people celebrating and minus any address another company's active
	employee also resolves to (R3, the guard). In "Selected employees" mode
	the same subtractions run against `reminder.recipients` narrowed to the
	celebrating company (P8-U11) -- a selected person in a *different*
	company is never mailed for this company's celebration. When two or
	more share the day, each of them also gets one email about the others
	-- from the same template, so HR words that mail once too, in either
	mode.

	One company at a time, each inside its own try/except: HR edits this
	template (P4-KTD13), so a render that raises -- an undefined filter, an
	attribute the context does not carry -- is a realistic morning, and it
	must cost that company its mail and nothing else. The error goes to the
	scheduler log once per (company, day), the same way the missing-template
	case below does.

	Returns `(result, skipped)` -- the per-event counts plus how many
	celebrants have no company (R4), counted once per run by the caller.
	"""
	skipped = len(_celebrants(event, None, getdate()))

	emails = 0
	failed = 0
	companies = 0
	for reminder in frappe.get_all(
		REMINDER_DOCTYPE,
		filters={"event": event, "is_enabled": 1, "email_template": ["is", "set"]},
		fields=["name", "company", "email_template", "recipient_mode", "hide_logo", "last_sent_on"],
	):
		company = reminder.company
		day = company_today(company)
		# Cheap pre-check; `_claim` below is the one that decides.
		if reminder.last_sent_on and getdate(reminder.last_sent_on) >= day:
			continue
		persons = _celebrants(event, company, day)
		if not persons:
			continue
		if not frappe.db.exists("Email Template", reminder.email_template):
			# `preflight.check_celebration_reminders` FAILs on this. The job says
			# so in the scheduler log and carries on with the other companies rather
			# than dying half way through the morning.
			_log_once(
				f"missing-template|{day}|{event}|{company}",
				title="HelixHR celebration reminders",
				message=f"The {event} reminder for '{company}' picks Email Template "
				f"'{reminder.email_template}', but no such template exists -- nothing "
				"was sent for that company.",
			)
			failed += 1
			continue
		progress = {"emails": 0}
		claimed, previous = False, None
		try:
			claimed, previous = _claim(reminder.name, day)
			if not claimed:
				continue
			companies += 1
			template = frappe.get_doc("Email Template", reminder.email_template)
			sender = get_sender_email()
			selected = (
				[
					row.employee
					for row in frappe.get_all(
						"HelixHR Celebration Recipient",
						filters={"parent": reminder.name, "parenttype": REMINDER_DOCTYPE},
						fields=["employee"],
					)
				]
				if reminder.recipient_mode == "Selected employees"
				else None
			)
			_send_company(
				template,
				sender,
				persons,
				company,
				event,
				selected,
				foreign,
				foreign_names,
				dropped,
				include_logo=not reminder.hide_logo,
				day=day,
				progress=progress,
			)
		except Exception:
			failed += 1
			if claimed and not progress["emails"]:
				_release(reminder.name, previous)
			_log_once(
				f"failed|{day}|{event}|{company}",
				f"The {event} celebration reminder for '{company}' failed -- Email Template "
				f"'{reminder.email_template}' was not sent to "
				+ ("everyone in" if progress["emails"] else "anyone in")
				+ " that company. The other companies and the other event are unaffected."
				f"\n\n{frappe.get_traceback()}",
				"HelixHR celebration reminders",
			)
		emails += progress["emails"]

	return {"companies": companies, "emails": emails, "failed": failed}, skipped


def _due(events):
	"""Whether any enabled reminder row for `events` has not yet sent for
	its company's current date (and, for a holiday row, today is its send
	day). One read of the reminder rows -- a handful -- so the ticks between
	midnights cost almost nothing; only a due tick goes on to read every
	employee for the cross-company guard."""
	today = {}
	for row in frappe.get_all(
		REMINDER_DOCTYPE,
		filters={"event": ["in", events], "is_enabled": 1, "email_template": ["is", "set"]},
		fields=["event", "company", "frequency", "last_sent_on"],
	):
		if row.company not in today:
			today[row.company] = company_today(row.company)
		day = today[row.company]
		if row.last_sent_on and getdate(row.last_sent_on) >= day:
			continue
		if row.event == "holiday" and not _is_holiday_send_day(row.frequency, day):
			continue
		return True
	return False


def _celebrants(event, company, day):
	"""Active employees of `company` (None: those with no company) whose
	birthday or joining anniversary falls on `day` -- HRMS's own rule from
	`get_employees_having_an_event_today` (same day and month, an earlier
	year), with the date as a parameter: HRMS's helper reads the system
	date, which is not this company's date when it keeps its own time zone.
	Same row shape as HRMS's, so the send path below is unchanged."""
	column = {"birthday": "date_of_birth", "work_anniversary": "date_of_joining"}.get(event)
	if not column:
		# `holiday` shares the reminder doctype but has its own sender.
		return []
	return frappe.db.sql(
		f"""select personal_email, company, company_email, user_id,
			employee_name as name, image, date_of_joining
		from `tabEmployee`
		where status = 'Active'
			and day(`{column}`) = %(day)s and month(`{column}`) = %(month)s
			and year(`{column}`) < %(year)s
			and {"company = %(company)s" if company else "ifnull(company, '') = ''"}
		order by employee_name asc""",
		{"day": day.day, "month": day.month, "year": day.year, "company": company},
		as_dict=True,
	)


def _claim(name, day):
	"""Claim (reminder row, local day) before sending. One conditional
	UPDATE: it changes the row only while `last_sent_on` is empty or
	earlier, so of two workers racing for the row exactly one changes it.
	Committed at once: `frappe.sendmail` queues without committing, so an
	uncommitted claim would hold the row lock across every company's send
	and make an overlapping run time out instead of skipping. The cost is
	deliberate -- a worker killed after the claim leaves that company
	unmailed for the day rather than mailed twice. Returns
	`(claimed, previous)` -- `previous` is what `_release` puts back."""
	previous = frappe.db.get_value(REMINDER_DOCTYPE, name, "last_sent_on")
	frappe.db.sql(
		f"""update `tab{REMINDER_DOCTYPE}` set last_sent_on = %(day)s
		where name = %(name)s and (last_sent_on is null or last_sent_on < %(day)s)""",
		{"day": day, "name": name},
	)
	claimed = bool(frappe.db.sql("select row_count()")[0][0])
	_commit()
	return claimed, previous


def _digest(text):
	import hashlib

	return hashlib.sha1(text.encode()).hexdigest()


def _release(name, previous):
	"""Undo a claim whose send raised before any mail went out, so the next
	tick can try again. A run that simply had nobody to mail keeps its claim:
	there is nothing more to send today."""
	frappe.db.set_value(REMINDER_DOCTYPE, name, "last_sent_on", previous, update_modified=False)
	_commit()


def _commit():
	"""Commit the claim, except under tests: a test relies on its rollback,
	and the row lock this releases only matters between real workers."""
	if not frappe.flags.in_test:
		frappe.db.commit()


def _log_once(key, message, title):
	"""`frappe.log_error`, at most once per `key`. The reminders tick every
	half hour, and a skip or a failure that is not claimed would otherwise
	write the same Error Log row up to 48 times a day. The key is a dated
	persistent cache key; losing it costs one repeated log line, never mail."""
	key = f"{CELEBRATION_GUARD_PREFIX}log|{key}"
	if frappe.cache.get_value(key):
		return
	frappe.log_error(title=title, message=message)
	# After the log, not before: a key set ahead of a log that never lands
	# would silence the failure for the rest of the day.
	frappe.cache.set_value(key, 1, expires_in_sec=CELEBRATION_GUARD_SECONDS)


def _foreign_address_companies():
	"""KTD4's guard data, one query per run: for every active employee the
	address HRMS's own pool helpers would resolve for them (`user_id` ->
	`company_email` -> `personal_email`), mapped to the set of companies an
	active employee resolves to it from, and to the Employee records behind
	each company so the R3 log can name both sides.

	An address is in company C's pool exactly when some active C employee
	resolves to it -- `get_all_employee_emails` appends one address per
	employee, in this same order -- so a set subtraction against this map
	is the whole guard.
	"""
	rows = frappe.get_all(
		"Employee",
		filters={"status": "Active"},
		fields=["name", "employee_name", "company", "user_id", "company_email", "personal_email"],
	)
	companies = {}
	names = {}
	for row in rows:
		address = row.user_id or row.company_email or row.personal_email
		if not address:
			continue
		companies.setdefault(address, set()).add(row.company)
		names.setdefault(address, {}).setdefault(row.company, []).append(row)
	return companies, names


def _drop_foreign(recipients, company, foreign, foreign_names, dropped):
	"""R3: any address that also resolves for an active employee of another
	company is dropped from `company`'s pool, and the drop is recorded (once
	per run, in the caller's `dropped` list) naming the Employee records on
	both sides. Returns the narrowed pool."""
	kept = []
	for address in recipients:
		others = foreign.get(address, set()) - {company}
		if not others:
			kept.append(address)
			continue
		for other in sorted(others):
			mine = _names(foreign_names, address, company) or _pool_employee(recipients, address, company)
			theirs = _names(foreign_names, address, other)
			dropped.append(
				f"{address}: dropped from {company}'s recipients -- it also resolves for "
				f"{', '.join(theirs)} in {other}"
				+ (f" (alongside {', '.join(mine)} in {company})" if mine else "")
			)
	return kept


def _names(foreign_names, address, company):
	rows = foreign_names.get(address, {}).get(company) or []
	return [f"Employee {row.name} ({row.employee_name})" for row in rows]


def _pool_employee(recipients, address, company):
	"""The Employee record the address was pooled for, when the foreign map
	already has the other side only -- `get_all_employee_emails` returns
	bare addresses, so the one it came from is looked up here."""
	rows = frappe.get_all(
		"Employee",
		filters={"status": "Active", "company": company, "user_id": address},
		fields=["name", "employee_name"],
	)
	if not rows:
		rows = frappe.get_all(
			"Employee",
			filters={
				"status": "Active",
				"company": company,
				"user_id": ["is", "not set"],
				**{"company_email": address},
			},
			fields=["name", "employee_name"],
		)
	return [f"Employee {row.name} ({row.employee_name})" for row in rows]


def _send_company(
	template,
	sender,
	persons,
	company,
	event,
	selected,
	foreign,
	foreign_names,
	dropped,
	include_logo=True,
	day=None,
	progress=None,
):
	"""`selected` is `None` for "All employees" mode -- every active
	employee's own address, HRMS's own helper (P4-R16) -- or the
	`recipients` child table's employee ids for "Selected employees" mode,
	narrowed to `company` before resolving addresses (P8-U11) so a person
	selected in another company is never mailed for this one.

	`day` is the company's own date (plan 2026-10-08-001 U2). `progress`,
	when passed, counts mail as it goes out, so a caller whose send raised
	half way still knows whether anything was sent."""
	progress = progress if progress is not None else {"emails": 0}
	emails = 0
	celebrating = _celebrating_addresses(persons)

	if selected is None:
		pool = get_all_employee_emails(company)
	else:
		company_selected = frappe.get_all(
			"Employee",
			filters={"name": ["in", selected], "company": company, "status": "Active"},
			pluck="name",
		)
		pool = get_employee_emails(company_selected)

	recipients = _drop_foreign(
		sorted(set(pool) - celebrating), company, foreign, foreign_names, dropped
	)
	if recipients:
		emails += _send(template, sender, recipients, persons, company, event, include_logo, day)
		progress["emails"] += 1

	# The shared-day email is between celebrants about each other -- who
	# hears about *them* (the pool above) is what `recipient_mode` scopes,
	# not this. Unconditional on mode, exactly as before P8-U11 -- but the
	# cross-company guard (R3) covers this address too: a celebrant whose
	# mailbox also resolves for another company's active employee receives
	# their own event's mail there no longer (R3's reported shape), dropped
	# and logged like any pool address.
	if len(persons) > 1:
		for person in persons:
			own = _drop_foreign(
				[address for address in [get_employee_email(person)] if address],
				company,
				foreign,
				foreign_names,
				dropped,
			)
			others = [other for other in persons if other is not person]
			if own:
				emails += _send(template, sender, own, others, company, event, include_logo, day)
				progress["emails"] += 1
	return emails


def _celebrating_addresses(persons):
	"""KTD5: every address the celebrant could receive their own
	announcement on, under either of HRMS's two fallback orders
	(`user_id` -> personal -> company, and `user_id` -> company ->
	personal) -- which is the union of all three fields. Subtracting the
	union, not `get_employee_email`'s single pick, is what fixes the small
	existing bug where a celebrant with no User could be mailed about
	themselves."""
	addresses = set()
	for person in persons:
		for field in ("user_id", "company_email", "personal_email"):
			if person.get(field):
				addresses.add(person[field])
	return addresses


def _send(template, sender, recipients, persons, company, event, include_logo=True, day=None):
	rendered = _render_restricted(template, _context(persons, company, event, day), sender, include_logo)
	frappe.sendmail(
		sender=sender,
		recipients=recipients,
		subject=rendered["subject"],
		message=rendered["message"],
		reference_doctype="Employee",
	)
	return 1


def _render_restricted(template, context, sender, include_logo=True):
	"""Render one Email Template through HelixHR's own Jinja sandbox
	(plan 2026-10-05-001 U11, `utils.render_celebration_email`), not
	`frappe.render_template`: Frappe's `restrict_globals=True` still exposes
	`frappe.db.get_value`, so a template could read any record. The sandbox
	has empty globals and a plain-data context -- a template using only the
	documented context (`_context`, below) renders; one reaching for
	`frappe.*` raises, and the caller logs and skips that company. A
	body-only template gets the branded layout (KTD11). `sender` is kept for
	the call sites; Email Template's `inject_email_account` context is no
	longer merged, since it is not part of the documented contract."""
	from helixhr.utils import render_celebration_email

	body = template.response_html if template.use_html else template.response
	return render_celebration_email(template.subject, body, context, include_logo=include_logo)


def _context(persons, company, event, day=None):
	"""The documented contract HR writes the template against (P4-KTD13).

	`persons` (each `name`, `first_name`, `image_url`, plus `years` on an
	anniversary), `names`, `count`, `company`, `logo_url`, `date`,
	`portal_url` -- and nothing else. `docs/deployment.md` carries the same
	list; a template that reads anything outside it is reading something this
	job does not promise to keep.

	`day` is the company's own date (plan 2026-10-08-001 U2); without one,
	the system date.
	"""
	today = getdate(day) if day else getdate()
	people = []
	for person in persons:
		entry = {
			"name": person.get("name"),
			# HRMS's projection carries `employee_name` (aliased to `name`)
			# and no `first_name`, and asking the Employee table again would
			# mean matching people by display name. The first word of the
			# name is what a greeting needs.
			"first_name": (person.get("name") or "").split(" ")[0],
			"image_url": get_url(person["image"]) if person.get("image") else "",
		}
		if event == "work_anniversary":
			joining = person.get("date_of_joining")
			entry["years"] = today.year - getdate(joining).year if joining else 0
		people.append(entry)

	return {
		"persons": people,
		"names": comma_sep([entry["name"] for entry in people], "{0} & {1}", False),
		"count": len(people),
		"company": company,
		"logo_url": _logo_url(company),
		# The site's date format, named rather than resolved from the
		# session: `format_date` with no format string asks
		# `frappe.locale.get_locale_value`, which raises UnboundLocalError
		# whenever `frappe.local.lang` is empty -- true of a `bench console`
		# session, and one bad session away from being true of a job. There
		# is no user to have a format of their own here anyway; the mail goes
		# to a whole company.
		"date": format_date(today, _date_format()),
		"portal_url": get_url("/helixhr"),
	}


def _logo_url(company):
	"""The shared email theme's logo, absolute (`utils.theme_logo_url`) --
	the same for every company since the theme replaced per-company logos.
	Empty when the theme has none -- the default templates guard on it, and
	so should HR's. A self-branded body's `<img src>` of it is turned into an
	inline attachment on send (`utils.render_celebration_email`)."""
	from helixhr.utils import theme_logo_url

	return theme_logo_url()


def _date_format():
	return frappe.db.get_single_value("System Settings", "date_format") or "yyyy-mm-dd"


# --- holiday reminders (plan 2026-10-04-004 U3, R7/R8, KTD6/KTD7) ----------


def send_holiday_reminders():
	"""On the hour and half hour (`hooks.scheduler_events["cron"]`, plan
	2026-10-08-001 U2), on each company's own clock, replacing HRMS's own
	`send_reminders_in_advance_weekly` / `..._monthly` (R8): each company
	with an enabled `holiday` row gets the reminder on its own cadence --
	Weekly rows send on Monday for Monday..Sunday, Monthly rows on the 1st
	for that month (exclusive upper bounds, so a boundary day is never
	listed by two consecutive mails the way HRMS's inclusive window lists
	the next Monday).

	The mail is *to* the employee, about their own upcoming non-weekly
	holidays from their own holiday list (resolved through HRMS's own
	helpers -- P4-KTD12). Employees whose list has nothing ahead in the
	window -- or no list at all -- get nothing.

	Recipients with the same holiday set share one render and one send
	(KTD6): the common case is a whole company on one list, and Monday's
	volume must not be one Email Queue row per employee. The template's
	`employee_name` is therefore the group's names, comma-separated -- for
	the usual single-person group it reads exactly as HR writes it.

	The same claimed `last_sent_on` as the celebrations (`_claim`), and the
	same cross-company address guard (R3): an address that also resolves for
	an active employee of another company is dropped and logged.
	"""
	if not _due(["holiday"]):
		return {"companies": 0, "emails": 0, "failed": 0}
	foreign, foreign_names = _foreign_address_companies()
	dropped = []

	result = {"companies": 0, "emails": 0, "failed": 0}
	for row in frappe.get_all(
		"HelixHR Celebration Reminder",
		filters={"event": "holiday", "is_enabled": 1},
		fields=[
			"name",
			"company",
			"email_template",
			"frequency",
			"recipient_mode",
			"hide_logo",
			"last_sent_on",
		],
	):
		company = row.company
		today = company_today(company)
		if not _is_holiday_send_day(row.frequency, today):
			continue
		if row.last_sent_on and getdate(row.last_sent_on) >= today:
			continue
		if not frappe.db.exists("Email Template", row.email_template):
			_log_once(
				f"missing-template|{today}|holiday|{company}",
				title="HelixHR holiday reminders",
				message=f"The holiday reminder for '{company}' picks Email Template "
				f"'{row.email_template}', but no such template exists -- nothing was sent.",
			)
			result["failed"] += 1
			continue
		progress = {"emails": 0}
		claimed, previous = False, None
		try:
			claimed, previous = _claim(row.name, today)
			if not claimed:
				continue
			_send_holiday_company(row, today, foreign, foreign_names, dropped, progress=progress)
			result["companies"] += 1
		except Exception:
			result["failed"] += 1
			if claimed and not progress["emails"]:
				_release(row.name, previous)
			_log_once(
				f"failed|{today}|holiday|{company}",
				f"The holiday reminder for '{company}' failed -- Email Template "
				f"'{row.email_template}' was not sent to "
				+ ("everyone in" if progress["emails"] else "anyone in")
				+ f" that company. The other companies are unaffected.\n\n{frappe.get_traceback()}",
				"HelixHR holiday reminders",
			)
		result["emails"] += progress["emails"]

	if dropped:
		message = "\n".join(dropped)
		_log_once(f"cross-company|{_digest(message)}", title=CROSS_COMPANY_LOG_TITLE, message=message)
	return result


def _is_holiday_send_day(frequency, today):
	"""The row's cadence decides whether *today* is a send day at all:
	Weekly on Monday, Monthly on the 1st. Anything else (no frequency --
	the controller refuses a holiday row without one, but a legacy write
	could still produce it) is never a send day."""
	if frequency == "Weekly":
		return today.weekday() == 0
	if frequency == "Monthly":
		return today.day == 1
	return False


def _holiday_window(frequency, today):
	"""Exclusive bounds: what the window *covers*, not what the last
	email's last day was. Weekly covers Monday..Sunday -- seven days, so
	the following Monday is left to the next mail. Monthly covers the
	calendar month -- the 1st of next month is left to next month's mail.
	HRMS's inclusive `[today, today+7]` / `[1st, next 1st]` listed a
	boundary day twice."""
	if frequency == "Weekly":
		return today, add_days(today, 6)
	return today, add_days(add_months(today, 1), -1)


def _mail_address(employee):
	"""The address the guard map records for `employee` -- its own order
	(`user_id` -> company -> personal), not `get_employee_email`'s
	(`user_id` -> personal -> company): an address resolved differently
	than the map records it would never be dropped -- and `_pool_employee`'s
	log would name nobody."""
	doc = frappe.get_cached_doc("Employee", employee)
	for field in ("user_id", "company_email", "personal_email"):
		if doc.get(field):
			return doc.get(field)
	return None


def _assigned_holiday_lists(employees, company, today):
	"""One employee's holiday list, resolved for the whole company in one
	query, from HRMS's own helpers (P4-KTD12). HRMS renamed the batch
	helpers over version-16 -- 16.17 ships
	`get_assigned_holiday_lists_to_employee_and_company`, the version-16
	tip moved to `get_holiday_list_assignments` plus
	`resolve_holiday_list_assignment` -- so both shapes are supported,
	with the employee's assignment keeping precedence over the company's,
	as `get_holiday_list_for_employee` resolves it."""
	from hrms.utils import holiday_list as hl

	batch = getattr(hl, "get_assigned_holiday_lists_to_employee_and_company", None)
	if batch:
		assigned = batch([*employees, company], today, today)
		resolved = {}
		for employee in employees:
			ranges = assigned.get(employee) or assigned.get(company)
			resolved[employee] = ranges[0]["holiday_list"] if ranges else None
		return resolved
	assignments = hl.get_holiday_list_assignments([*employees, company])
	company_assignments = assignments.get(company, [])
	resolved = {}
	for employee in employees:
		assignment = hl.resolve_holiday_list_assignment(
			assignments.get(employee, []), company_assignments, today
		)
		resolved[employee] = assignment.holiday_list if assignment else None
	return resolved


def _send_holiday_company(row, today, foreign, foreign_names, dropped, progress=None):
	"""One company's holiday reminder: every active employee's own list,
	grouped by the holidays ahead in the window.

	The list comes from HRMS -- `get_assigned_holiday_lists_to_employee_and_company`,
	imported, not re-implemented (P4-KTD12) -- resolved for the whole
	company in ONE query instead of HRMS's per-employee pair of lookups,
	with the employee's assignment keeping precedence over the company's,
	exactly as `get_holiday_list_for_employee` resolves it. The holiday
	rows are queried here, not through `get_holidays_for_employee`, on
	purpose: that helper adds `filters["weekly_off"] = False`, and under
	this Frappe a bare False in a filter matches nothing -- HRMS's own
	weekly/monthly senders have been mailing nobody for a while, which is
	one more reason the takeover is happening. The explicit `weekly_off: 0`
	is the same intent, stated so it actually runs."""
	start, end = _holiday_window(row.frequency, today)
	employees = frappe.get_all(
		"Employee", filters={"status": "Active", "company": row.company}, pluck="name"
	)
	if row.recipient_mode == "Selected employees":
		# The audience is the row's own (R9): a Selected holiday row mails
		# only the people HR picked -- they are already company-validated by
		# the doctype controller, and the Active/company narrowing below
		# still applies.
		picked = set(
			frappe.get_all(
				"HelixHR Celebration Recipient", filters={"parent": row.name}, pluck="employee"
			)
		)
		employees = [employee for employee in employees if employee in picked]
	assigned = _assigned_holiday_lists(employees, row.company, today)
	group_holidays = {}
	groups = {}
	for employee in employees:
		holiday_list = assigned.get(employee)
		if not holiday_list:
			continue
		if holiday_list not in group_holidays:
			# One query per distinct list, not per employee: the common
			# case is a whole company sharing one list.
			group_holidays[holiday_list] = frappe.get_all(
				"Holiday",
				filters={
					"parent": holiday_list,
					"parenttype": "Holiday List",
					"weekly_off": 0,
					"holiday_date": ["between", [start, end]],
				},
				fields=["holiday_date", "description"],
				order_by="holiday_date asc",
			)
		holidays = group_holidays[holiday_list]
		if not holidays:
			continue
		key = tuple((holiday["holiday_date"], holiday["description"]) for holiday in holidays)
		groups.setdefault(key, []).append(employee)

	emails = 0
	for key, employees in groups.items():
		holidays = [
			{"date": format_date(holiday_date, _date_format()), "description": description}
			for holiday_date, description in key
		]
		recipients = _drop_foreign(
			[address for employee in employees if (address := _mail_address(employee))],
			row.company,
			foreign,
			foreign_names,
			dropped,
		)
		if not recipients:
			continue
		names = comma_sep(
			frappe.db.get_all(
				"Employee",
				filters={"name": ["in", employees]},
				fields=["employee_name"],
				order_by="employee_name asc",
				pluck="employee_name",
			),
			"{0} & {1}",
			False,
		)
		template = frappe.get_doc("Email Template", row.email_template)
		context = {
			"employee_name": names,
			"holidays": holidays,
			"company": row.company,
			"logo_url": _logo_url(row.company),
			"portal_url": get_url("/helixhr"),
			"date": format_date(today, _date_format()),
			"frequency": row.frequency,
		}
		rendered = _render_restricted(
			template, context, get_sender_email(), include_logo=not row.get("hide_logo")
		)
		frappe.sendmail(
			sender=get_sender_email(),
			recipients=recipients,
			subject=rendered["subject"],
			message=rendered["message"],
			reference_doctype="Employee",
		)
		emails += 1
		if progress is not None:
			progress["emails"] += 1
	return emails


# --- overdue digests (plan 2026-10-02-001 U11, R23..R25, KTD13) -------------

# KTD13's rerun guard: one dated key per (company, company-local day) since
# 2026-10-29 (`overdue_guard_key`), kept through `clear-cache` and `bench
# migrate` by `hooks.persistent_cache_keys`, and expiring on its own a day and
# a half later so the store never accumulates them.
OVERDUE_GUARD_PREFIX = "helixhr-overdue-digest|"
OVERDUE_GUARD_SECONDS = 36 * 60 * 60
OVERDUE_ERROR_TITLE = "HelixHR overdue digests"
_HR_ROLE = "HR Manager"
# The per-doctype bound on `collect_overdue`'s reads (oldest first). The
# queries already keep only rows past their threshold, so this is a backstop
# against a site with a stuck backlog, not a page size.
_OVERDUE_FETCH = 500
# Plan 2026-10-16-001 R13: kind labels match R13 vocabulary.
_KIND_LABELS = {
	"leave": "Leave",
	"timesheet": "Timesheet",
	"attendance": "Attendance",
	"request": "HR request",
	"change": "Timesheet change",
}


def overdue_guard_key(company, day):
	"""The digest's rerun guard for one company's local `day`."""
	return f"{OVERDUE_GUARD_PREFIX}{company}|{day}"


def send_overdue_digests():
	"""On the hour and half hour (`hooks.scheduler_events["cron"]`). Each
	company's digest goes out on the first tick after midnight in *its own*
	time zone (`utils.company_today`), with its items aged against that date
	(since 2026-10-29). Per company: one `approval_overdue_digest` per late
	approver (R23) and one `hr_overdue_summary` per HR Manager whose scope
	reaches the company (R24) -- someone covering two companies gets one of
	each per company, each at that company's midnight. A tick with every
	company already sent is one Company read and a cache lookup per company.

	A same-day rerun sends that company nothing (its dated guard). Switching
	either event Off in Email templates is the off switch: `send_notification`
	renders nothing for it. Failure is isolated per recipient and logged; a
	company whose collector raises is logged once and retried next tick.
	"""
	from helixhr.utils import send_notification

	result = {"companies": 0, "items": 0, "digests": 0, "summaries": 0}
	due = {}
	for company in frappe.get_all("Company", pluck="name", order_by="name asc"):
		day = company_today(company)
		# The pre-2026-10-29 site-wide key: the deploy day's old daily run
		# already mailed this date. Expires within 36 h; drop next release.
		if frappe.cache.get_value(overdue_guard_key(company, day)) or frappe.cache.get_value(
			f"{OVERDUE_GUARD_PREFIX}{day}"
		):
			continue
		due[company] = day
	if not due:
		return result

	hr_users = _active_hr_managers()
	for company, day in due.items():
		try:
			items = collect_overdue(day, companies=[company])
		except Exception:
			_log_once(
				f"overdue|{company}|{day}",
				f"The overdue digest for '{company}' could not be collected; it retries on the next tick."
				f"\n\n{frappe.get_traceback()}",
				OVERDUE_ERROR_TITLE,
			)
			continue

		by_owner = {}
		for item in items:
			for user in item["owners"]:
				by_owner.setdefault(user, []).append(item)
		for user, owned in by_owner.items():
			# An HR Manager reads their own items in the summary, marked (U11).
			if user in hr_users:
				continue
			try:
				send_notification(
					"approval_overdue_digest",
					[user],
					{"items": [_public(item) for item in owned], "count": len(owned)},
				)
				result["digests"] += 1
			except Exception:
				frappe.log_error(frappe.get_traceback(), OVERDUE_ERROR_TITLE)

		for user in hr_users if items else ():
			try:
				owners = _summary_owners(items, user)
				if not owners:
					continue
				send_notification(
					"hr_overdue_summary",
					[user],
					{"owners": owners, "count": sum(len(owner["items"]) for owner in owners)},
				)
				result["summaries"] += 1
			except Exception:
				frappe.log_error(frappe.get_traceback(), OVERDUE_ERROR_TITLE)

		frappe.cache.set_value(overdue_guard_key(company, day), 1, expires_in_sec=OVERDUE_GUARD_SECONDS)
		result["companies"] += 1
		result["items"] += len(items)
	return result


# Plan 2026-10-15-002 KTD2: `collect_overdue(cutoff=ALL_PENDING)` reads every
# pending item, not only the late ones -- the All pending tab and the
# timesheet oversight list share the digest's collector.
ALL_PENDING = "all"
# The kinds `collect_overdue` reads, by their `route_kind`.
PENDING_KINDS = ("leave", "timesheet", "attendance", "request", "change")


def collect_overdue(today=None, cutoff=None, companies=None, kinds=None, resolve_owners=True, stats=None):
	"""Every overdue item on the site, oldest first, each with the users who
	owe it a decision (`owners`, active and mailable only) and the name the
	HR summary groups it under. Shared with U12's Overdue tab so the email
	and the tab never disagree. Thresholds are R25's, through `is_overdue`.

	Plan 2026-10-15-002 KTD2, all optional so the digest keeps its defaults:

	- ``cutoff``: ``ALL_PENDING`` reads every pending item (a row with no
	  ``helixhr_pending_since`` included, its age ``None``); a date reads
	  items pending since before it. Each item says whether it is ``overdue``.
	- ``companies``: a list narrows every read to employees of those
	  companies in SQL, before the per-doctype bound, so one company's backlog cannot crowd
	  another's rows out. ``None`` is every company.
	- ``kinds``: the `route_kind`s to read; default all.
	- ``resolve_owners=False`` skips the per-row `owners` lookups; callers
	  batch-resolve the raw ``approver`` instead (`resolve_approvers`).
	- ``stats``: a dict that gets ``capped`` = True when a read hit the bound.
	"""
	from helixhr.api import approval_overdue_days, is_overdue

	today = getdate(today)
	threshold = approval_overdue_days()
	every = cutoff == ALL_PENDING
	late_only = cutoff is None
	# Overdue means pending since before this date (`is_overdue`'s age > threshold).
	since_filter = {} if every else {PENDING_SINCE: ["<", cutoff or add_days(today, -threshold)]}
	kinds = set(kinds or PENDING_KINDS)
	# Scope follows the employee's company, as `admin_scope_employee_filters`
	# does everywhere else; it is an SQL filter, so it runs before the bound.
	company_filter = (
		{
			"employee": [
				"in",
				frappe.get_all("Employee", filters={"company": ["in", list(companies)]}, pluck="name") or [""],
			]
		}
		if companies is not None
		else {}
	)
	items = []
	# Plan 2026-10-19-001 U3: who owes an HR- or IT-routed item is that
	# role's holders in the item's company -- one read per (role, company).
	holders = {}
	if stats is not None:
		stats["capped"] = False

	def read(doctype, **kwargs):
		rows = frappe.get_all(doctype, limit=_OVERDUE_FETCH, **kwargs)
		if stats is not None and len(rows) >= _OVERDUE_FETCH:
			stats["capped"] = True
		return rows

	def role_owners(role, company):
		if (role, company) not in holders:
			holders[role, company] = _mailable(_company_role_holders(role, company))
		return list(holders[role, company])

	def hr_owned(company):
		return role_owners(_HR_ROLE, company), _HR_ROLE

	def owned(resolve, *args):
		return resolve(*args) if resolve_owners else ([], None)

	def add(kind, doctype, row, title, since, owners, owner_name, threshold_days, path, approver, date=None):
		kind_label = (
			("IT request" if row.get("routed_to_role") == "IT Team" else "HR request")
			if kind == "request"
			else _KIND_LABELS[kind]
		)
		items.append(
			{
				"kind": kind_label,
				"doctype": doctype,
				"name": row.name,
				"title": title,
				"employee": row.employee,
				"employee_name": row.employee_name or row.employee,
				"company": row.get("company"),
				"age_days": (today - getdate(since)).days if since else None,
				"threshold_days": threshold_days,
				"overdue": is_overdue(since, today, threshold_days),
				"pending_since": str(since) if since else None,
				"date": str(date) if date else None,
				"url": (
					get_url(f"/helixhr/requests/{row.name}?tab=work")
					if path == "request"
					else get_url(f"/helixhr/approvals/{path}/{row.name}")
				),
				"route_kind": path,
				"owners": owners,
				"owner_name": owner_name,
				# KTD3: who owes the decision, unresolved -- {"user": ...},
				# {"reports_to_of": employee} or {"role": ...}.
				"approver": approver,
			}
		)

	if "leave" in kinds:
		for row in read(
			"Leave Application",
			filters={"status": "Open", "docstatus": 0, **since_filter, **company_filter},
			fields=["name", "employee", "employee_name", "company", "leave_type", "from_date", "leave_approver",
				"helixhr_stage", PENDING_SINCE],
			order_by=f"{PENDING_SINCE} asc",
		):
			if late_only and not is_overdue(row.get(PENDING_SINCE), today, threshold):
				continue
			if row.helixhr_stage == "HR":
				owners, owner_name = owned(hr_owned, row.company)
				approver = {"role": _HR_ROLE}
			else:
				owners, owner_name = owned(_user_owner, row.leave_approver)
				approver = {"user": row.leave_approver}
			title = f"{row.leave_type}, {format_date(row.from_date, _date_format())}"
			add("leave", "Leave Application", row, title, row.get(PENDING_SINCE), owners, owner_name, threshold,
				"leave", approver, row.from_date)

	for doctype, kind, path, manager_state, hr_state, date_field in (
		("Timesheet", "timesheet", "timesheet", "Pending Approval", "Pending HR", "start_date"),
		("Attendance Request", "attendance", "attendance", "Pending Manager", "Pending HR", "from_date"),
	):
		if kind not in kinds:
			continue
		for row in read(
			doctype,
			filters={
				"docstatus": 0,
				"workflow_state": ["in", (manager_state, hr_state)],
				**since_filter,
				**company_filter,
			},
			fields=["name", "employee", "employee_name", "company", "workflow_state", date_field, PENDING_SINCE],
			order_by=f"{PENDING_SINCE} asc",
		):
			if late_only and not is_overdue(row.get(PENDING_SINCE), today, threshold):
				continue
			if row.workflow_state == hr_state:
				owners, owner_name = owned(hr_owned, row.company)
				approver = {"role": _HR_ROLE}
			else:
				owners, owner_name = owned(_manager_owner, row.employee)
				approver = {"reports_to_of": row.employee}
			title = f"{_KIND_LABELS[kind]}, {format_date(row.get(date_field), _date_format())}"
			add(kind, doctype, row, title, row.get(PENDING_SINCE), owners, owner_name, threshold, path, approver,
				row.get(date_field))

	# R25: HR Requests count from `creation` against the category's SLA; 0 is
	# no SLA, and "Waiting on Employee" is the employee's turn, never late.
	slas = {
		row.name: cint(row.sla_days)
		for row in frappe.get_all("HelixHR Request Category", fields=["name", "sla_days"])
	} if "request" in kinds else {}
	timed = [sla for sla in slas.values() if sla > 0]
	request_filters = {"status": ["in", ("Open", "In Progress")]}
	if not every:
		request_filters["category"] = ["in", [name for name, sla in slas.items() if sla > 0]]
		if timed:
			# The shortest SLA's cutoff; the per-category check below does the rest.
			request_filters["creation"] = ["<", add_days(today, -min(timed))]
	request_filters.update(company_filter)
	for row in (
		read(
			"HR Request",
			filters=request_filters,
			fields=["name", "employee", "category", "subject", "routed_to_role", "picked_up_by", "creation"],
			order_by="creation asc",
		)
		if "request" in kinds and (every or timed)
		else []
	):
		sla = slas.get(row.category, 0)
		if late_only and (sla <= 0 or not is_overdue(row.creation, today, sla)):
			continue
		row.employee_name, company = frappe.db.get_value(
			"Employee", row.employee, ["employee_name", "company"]
		) or (None, None)
		if row.picked_up_by:
			owners, owner_name = owned(_user_owner, row.picked_up_by)
			approver = {"user": row.picked_up_by}
		elif row.routed_to_role == _HR_ROLE:
			owners, owner_name = owned(hr_owned, company)
			approver = {"role": _HR_ROLE}
		else:
			owners = role_owners(row.routed_to_role, company) if resolve_owners else []
			owner_name = row.routed_to_role or "No owner"
			approver = {"role": row.routed_to_role} if row.routed_to_role else {}
		add("request", "HR Request", row, row.subject, row.creation, owners, owner_name, sla, "request", approver)
		if sla <= 0:
			items[-1]["overdue"] = False

	# Plan 2026-10-04-003 U3: an open change request waits on its stamped
	# approver (or HR, when it routed there) the same way every other kind
	# waits, so it joins the same digest.
	if "change" in kinds:
		for row in read(
			"HelixHR Timesheet Change",
			filters={"status": "Open", **since_filter, **company_filter},
			fields=["name", "employee", "employee_name", "company", "week_start", "approver_user", PENDING_SINCE],
			order_by=f"{PENDING_SINCE} asc",
		):
			if late_only and not is_overdue(row.get(PENDING_SINCE), today, threshold):
				continue
			if row.approver_user:
				owners, owner_name = owned(_user_owner, row.approver_user)
				approver = {"user": row.approver_user}
			else:
				owners, owner_name = owned(hr_owned, row.company)
				approver = {"role": _HR_ROLE}
			title = f"Change request, {format_date(row.week_start, _date_format())}"
			add("change", "HelixHR Timesheet Change", row, title, row.get(PENDING_SINCE), owners, owner_name,
				threshold, "change", approver, row.week_start)

	# Oldest first; an item whose wait is unknown sorts last.
	items.sort(key=lambda item: -(item["age_days"] if item["age_days"] is not None else -1))
	return items


def _public(item):
	"""The template's documented item fields (`approval_overdue_digest`)."""
	return {key: item[key] for key in ("kind", "title", "employee_name", "age_days", "url")}


def _summary_owners(items, hr_user, project=None):
	"""`hr_overdue_summary`'s `owners`, narrowed to `hr_user`'s admin scope
	(P6-R6). U12's Overdue tab reads the same groups with a wider `project`
	(row fields), so the tab's counts and the summary's always agree. Items with nobody active to act go under their owner's name,
	flagged `inactive` (R24). The HR user's own group is marked "(you)" in
	its name rather than by a new key, so saved templates keep rendering."""
	from helixhr.utils import admin_scope_employee_filters, resolve_admin_scope

	filters = admin_scope_employee_filters(resolve_admin_scope(hr_user))
	if filters is None:
		return []
	in_scope = None
	if filters:
		employees = {item["employee"] for item in items}
		in_scope = set(
			frappe.get_all("Employee", filters={**filters, "name": ["in", list(employees)]}, pluck="name")
		) if employees else set()

	groups = {}
	for item in items:
		if in_scope is not None and item["employee"] not in in_scope:
			continue
		inactive = not item["owners"]
		group = groups.setdefault(
			(item["owner_name"], inactive),
			{"owner_name": item["owner_name"], "inactive": inactive, "items": []},
		)
		if hr_user in item["owners"] and item["owner_name"] != _HR_ROLE:
			group["owner_name"] = f"{item['owner_name']} (you)"
		group["items"].append((project or _public)(item))
	return list(groups.values())


def _user_owner(user):
	"""`([user], name)` while the user can be mailed and is still with the
	company, else `([], name)` -- the HR summary's "no active owner"."""
	if not user:
		return [], "No approver"
	name = frappe.utils.get_fullname(user)
	if active_employee_for_user(user).state == "inactive":
		return [], name
	return _mailable([user]), name


def _manager_owner(employee):
	"""The timesheet / attendance approver: the employee's `reports_to`, the
	same person `events._approver_user` resolves, kept by name even when
	inactive so HR can see whose queue it is stuck in."""
	reports_to = frappe.db.get_value("Employee", employee, "reports_to")
	if not reports_to:
		return [], "No approver"
	manager = frappe.db.get_value(
		"Employee", reports_to, ["user_id", "status", "employee_name"], as_dict=True
	)
	if not manager or not manager.user_id:
		return [], (manager and manager.employee_name) or reports_to
	if manager.status != "Active":
		return [], manager.employee_name
	return _mailable([manager.user_id]), manager.employee_name


def _mailable(users):
	"""Enabled users with an email address -- anyone else cannot receive a
	digest, so their items surface in the HR summary instead."""
	users = [user for user in users if user]
	if not users:
		return []
	return frappe.get_all(
		"User",
		filters={"name": ["in", users], "enabled": 1, "email": ["is", "set"]},
		pluck="name",
		order_by="name asc",
	)


def _active_hr_managers():
	"""Who receives `hr_overdue_summary` (R24): mailable HR Managers whose
	HR scope reaches some org (plan 2026-10-19-001 U3). `_summary_owners`
	narrows each one's summary to that scope, so Leadership reads every
	org's items and an HR Manager on [A] only A's."""
	from helixhr.utils import resolve_admin_scope

	holders = frappe.get_all("Has Role", filters={"role": _HR_ROLE, "parenttype": "User"}, pluck="parent")
	return [
		user
		for user in _mailable(holders)
		if user != "Administrator" and resolve_admin_scope(user)["kind"] != "none"
	]



# --- Plan 2026-10-15-002: approver resolver and manual reminders -----------
#
# KTD3: a resolver beside the digest's `_manager_owner`, which throws away the
# user id and the reason. Reasons, in precedence order: no_approver (nobody
# set, or the set Employee is gone), inactive (the approver has left -- still
# returned, so HR sees whose queue it is stuck in), no_login (no User, or a
# disabled one), unmailable (no email). "none" means a reminder can go.

APPROVER_OK = "none"
APPROVER_NO_APPROVER = "no_approver"
APPROVER_INACTIVE = "inactive"
APPROVER_NO_LOGIN = "no_login"
APPROVER_UNMAILABLE = "unmailable"

# KTD6: one claim per (approver, company) per 24 hours, whoever sends it.
REMINDER_GUARD_PREFIX = "helixhr-timesheet-reminder|"
REMINDER_GUARD_SECONDS = 24 * 60 * 60


def resolve_approvers(items):
	"""Set ``approver_user``, ``approver_employee``, ``approver_name``,
	``approver_role`` and ``reason`` on each of `collect_overdue`'s items,
	from its raw ``approver``. Batched: one Employee read (subjects joined to
	their `reports_to`, plus user approvers' own records) and one User read
	per call, whatever the item count."""
	subjects = {item["approver"]["reports_to_of"] for item in items if item["approver"].get("reports_to_of")}
	users = {item["approver"]["user"] for item in items if item["approver"].get("user")}

	by_subject, by_user = {}, {}
	if subjects or users:
		employee = frappe.qb.DocType("Employee")
		manager = employee.as_("manager")
		condition = None
		if subjects:
			condition = employee.name.isin(list(subjects))
		if users:
			by_login = employee.user_id.isin(list(users))
			condition = by_login if condition is None else condition | by_login
		for row in (
			frappe.qb.from_(employee)
			.left_join(manager)
			.on(manager.name == employee.reports_to)
			.select(
				employee.name,
				employee.user_id,
				employee.status,
				employee.employee_name,
				employee.reports_to,
				manager.name.as_("manager_name"),
				manager.user_id.as_("manager_user"),
				manager.status.as_("manager_status"),
				manager.employee_name.as_("manager_employee_name"),
			)
			.where(condition)
			.run(as_dict=True)
		):
			if row.name in subjects:
				by_subject[row.name] = row
			if row.user_id in users:
				by_user[row.user_id] = row

	logins = users | {row.manager_user for row in by_subject.values() if row.manager_user}
	accounts = {
		row.name: row
		for row in (
			frappe.get_all(
				"User",
				filters={"name": ["in", list(logins)]},
				fields=["name", "enabled", "email", "full_name"],
			)
			if logins
			else []
		)
	}

	def login_reason(user):
		account = accounts.get(user)
		if not account or not cint(account.enabled):
			return APPROVER_NO_LOGIN
		return APPROVER_OK if account.email else APPROVER_UNMAILABLE

	for item in items:
		raw = item["approver"]
		answer = {"approver_user": None, "approver_employee": None, "approver_name": None,
			"approver_role": None, "reason": APPROVER_NO_APPROVER}
		if raw.get("role"):
			answer.update(approver_name=raw["role"], approver_role=raw["role"], reason=APPROVER_OK)
		elif raw.get("reports_to_of"):
			row = by_subject.get(raw["reports_to_of"])
			if row and row.reports_to:
				answer["approver_employee"] = row.reports_to
			if row and row.manager_name:
				answer.update(
					approver_user=row.manager_user,
					approver_name=row.manager_employee_name,
				)
				if row.manager_status != "Active":
					answer["reason"] = APPROVER_INACTIVE
				elif not row.manager_user:
					answer["reason"] = APPROVER_NO_LOGIN
				else:
					answer["reason"] = login_reason(row.manager_user)
		elif raw.get("user"):
			user = raw["user"]
			row = by_user.get(user)
			account = accounts.get(user)
			answer.update(
				approver_user=user,
				approver_employee=row.name if row else None,
				approver_name=(row and row.employee_name) or (account and account.full_name) or user,
			)
			answer["reason"] = APPROVER_INACTIVE if row and row.status != "Active" else login_reason(user)
		item.update(answer)
	return items


def reminder_guard_key(approver_user, company):
	"""The KTD6 claim key, namespaced to the site like `frappe.cache`'s own."""
	return frappe.cache.make_key(f"{REMINDER_GUARD_PREFIX}{company}|{approver_user}")


def claim_reminder(approver_user, company):
	"""Atomically claim the 24h reminder slot (Redis ``SET NX``). True when
	this caller won it; the stored value is the claim time."""
	return bool(
		frappe.cache.set(
			reminder_guard_key(approver_user, company),
			frappe.utils.now_datetime().isoformat(),
			ex=REMINDER_GUARD_SECONDS,
			nx=True,
		)
	)


def release_reminder(approver_user, company):
	"""Give the slot back -- nothing was queued, so nothing was sent."""
	frappe.cache.delete(reminder_guard_key(approver_user, company))


def next_reminder_at(approver_user, company):
	"""When the next reminder may go (claim time + 24h), or None if now."""
	claimed = frappe.cache.get(reminder_guard_key(approver_user, company))
	if not claimed:
		return None
	claimed = claimed.decode() if isinstance(claimed, bytes) else str(claimed)
	return frappe.utils.add_to_date(frappe.utils.get_datetime(claimed), seconds=REMINDER_GUARD_SECONDS)
