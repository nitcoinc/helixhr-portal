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
from helixhr.utils import company_today

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
# same-day resend for one that ticks every 15 minutes. The dated keys under
# this prefix now only stop a failure being logged on every tick
# (`_log_once`); kept through `clear-cache` and `bench migrate` by
# `hooks.persistent_cache_keys`, they expire a day and a half later.
CELEBRATION_GUARD_PREFIX = "helixhr-celebration|"
CELEBRATION_GUARD_SECONDS = 36 * 60 * 60
REMINDER_DOCTYPE = "HelixHR Celebration Reminder"
CROSS_COMPANY_LOG_TITLE = "HelixHR cross-company mailboxes"
NO_COMPANY_LOG_TITLE = "HelixHR celebrations skipped for want of a company"


def send_celebration_reminders():
	"""Every 15 minutes (`hooks.scheduler_events["cron"]`, plan 2026-10-08-001
	U2). Idle until HR enables the reminder and picks a template for the
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
	foreign, foreign_names = _foreign_address_companies()
	dropped = []
	skipped_no_company = 0
	sent = {}
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
		_log_once(f"cross-company|{getdate()}", title=CROSS_COMPANY_LOG_TITLE, message="\n".join(dropped))
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
		progress = {"emails": 0}
		try:
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
			_log_once(
				f"failed|{day}|{event}|{company}",
				f"The {event} celebration reminder for '{company}' failed -- Email Template "
				f"'{template.name}' was not sent to "
				+ ("everyone in" if progress["emails"] else "anyone in")
				+ " that company. The other companies and the other event are unaffected."
				f"\n\n{frappe.get_traceback()}",
				"HelixHR celebration reminders",
			)
		emails += progress["emails"]
		if not progress["emails"]:
			_release(reminder.name, previous)

	return {"companies": companies, "emails": emails, "failed": failed}, skipped


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
	earlier, and MariaDB's row lock makes a second worker's UPDATE wait and
	then change nothing. Returns `(claimed, previous)` -- `previous` is what
	`_release` puts back."""
	previous = frappe.db.get_value(REMINDER_DOCTYPE, name, "last_sent_on")
	frappe.db.sql(
		f"""update `tab{REMINDER_DOCTYPE}` set last_sent_on = %(day)s
		where name = %(name)s and (last_sent_on is null or last_sent_on < %(day)s)""",
		{"day": day, "name": name},
	)
	return bool(frappe.db.sql("select row_count()")[0][0]), previous


def _release(name, previous):
	"""Undo a claim that sent nothing, so the next tick can try again."""
	frappe.db.set_value(REMINDER_DOCTYPE, name, "last_sent_on", previous, update_modified=False)


def _log_once(key, message, title):
	"""`frappe.log_error`, at most once per `key`. The reminders tick every
	15 minutes, and a skip or a failure that is not claimed would otherwise
	write the same Error Log row up to 96 times a day. The key is a dated
	persistent cache key; losing it costs one repeated log line, never mail."""
	key = f"{CELEBRATION_GUARD_PREFIX}log|{key}"
	if frappe.cache.get_value(key):
		return
	frappe.cache.set_value(key, 1, expires_in_sec=CELEBRATION_GUARD_SECONDS)
	frappe.log_error(title=title, message=message)


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
	"""Every 15 minutes (`hooks.scheduler_events["cron"]`, plan 2026-10-08-001
	U2), on each company's own clock, replacing HRMS's own
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
		claimed, previous = _claim(row.name, today)
		if not claimed:
			continue

		progress = {"emails": 0}
		try:
			_send_holiday_company(row, today, foreign, foreign_names, dropped, progress=progress)
			result["companies"] += 1
		except Exception:
			result["failed"] += 1
			_log_once(
				f"failed|{today}|holiday|{company}",
				f"The holiday reminder for '{company}' failed -- Email Template "
				f"'{row.email_template}' was not sent to "
				+ ("everyone in" if progress["emails"] else "anyone in")
				+ f" that company. The other companies are unaffected.\n\n{frappe.get_traceback()}",
				"HelixHR holiday reminders",
			)
		result["emails"] += progress["emails"]
		if not progress["emails"]:
			_release(row.name, previous)

	if dropped:
		_log_once(f"cross-company|holiday|{getdate()}", title=CROSS_COMPANY_LOG_TITLE, message="\n".join(dropped))
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

# KTD13's rerun guard: one dated key per site day, kept through `clear-cache`
# and `bench migrate` by `hooks.persistent_cache_keys`, and expiring on its
# own a day and a half later so the store never accumulates them.
OVERDUE_GUARD_PREFIX = "helixhr-overdue-digest|"
OVERDUE_GUARD_SECONDS = 36 * 60 * 60
OVERDUE_ERROR_TITLE = "HelixHR overdue digests"
_HR_ROLE = "HR Manager"
# The per-doctype bound on `collect_overdue`'s reads (oldest first). The
# queries already keep only rows past their threshold, so this is a backstop
# against a site with a stuck backlog, not a page size.
_OVERDUE_FETCH = 500
_KIND_LABELS = {
	"leave": "Leave",
	"timesheet": "Timesheet",
	"attendance": "Attendance request",
	"request": "Request",
	# Plan 2026-10-04-003 U3.
	"change": "Change request",
}


def send_overdue_digests():
	"""Daily (`hooks.scheduler_events`). One `approval_overdue_digest` per
	late approver (R23) and one `hr_overdue_summary` per HR Manager (R24).

	A same-day rerun sends nothing (the dated guard key). Switching either
	event Off in Email templates is the off switch: `send_notification`
	renders nothing for it. Failure is isolated per recipient and logged.
	"""
	from helixhr.utils import send_notification

	today = getdate()
	guard = f"{OVERDUE_GUARD_PREFIX}{today}"
	if frappe.cache.get_value(guard):
		return {"skipped": True}

	items = collect_overdue(today)
	hr_users = _active_hr_managers()
	digests = summaries = 0

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
			digests += 1
		except Exception:
			frappe.log_error(frappe.get_traceback(), OVERDUE_ERROR_TITLE)

	for user in hr_users:
		try:
			owners = _summary_owners(items, user)
			if not owners:
				continue
			send_notification(
				"hr_overdue_summary",
				[user],
				{"owners": owners, "count": sum(len(owner["items"]) for owner in owners)},
			)
			summaries += 1
		except Exception:
			frappe.log_error(frappe.get_traceback(), OVERDUE_ERROR_TITLE)

	frappe.cache.set_value(guard, 1, expires_in_sec=OVERDUE_GUARD_SECONDS)
	return {"items": len(items), "digests": digests, "summaries": summaries}


def collect_overdue(today=None):
	"""Every overdue item on the site, oldest first, each with the users who
	owe it a decision (`owners`, active and mailable only) and the name the
	HR summary groups it under. Shared with U12's Overdue tab so the email
	and the tab never disagree. Thresholds are R25's, through `is_overdue`."""
	from helixhr.api import approval_overdue_days, is_overdue

	today = getdate(today)
	threshold = approval_overdue_days()
	# Overdue means pending since before this date (`is_overdue`'s age > threshold).
	cutoff = add_days(today, -threshold)
	items = []
	hr_users = None

	def hr_owned():
		nonlocal hr_users
		if hr_users is None:
			hr_users = _active_hr_managers()
		return list(hr_users), _HR_ROLE

	def add(kind, doctype, row, title, since, owners, owner_name, threshold_days, path):
		items.append(
			{
				"kind": _KIND_LABELS[kind],
				"doctype": doctype,
				"name": row.name,
				"title": title,
				"employee": row.employee,
				"employee_name": row.employee_name or row.employee,
				"age_days": (today - getdate(since)).days,
				"threshold_days": threshold_days,
				"url": get_url(f"/helixhr/approvals/{path}/{row.name}"),
				"route_kind": path,
				"owners": owners,
				"owner_name": owner_name,
			}
		)

	for row in frappe.get_all(
		"Leave Application",
		filters={"status": "Open", "docstatus": 0, PENDING_SINCE: ["<", cutoff]},
		fields=["name", "employee", "employee_name", "leave_type", "from_date", "leave_approver",
			"helixhr_stage", PENDING_SINCE],
		order_by=f"{PENDING_SINCE} asc",
		limit=_OVERDUE_FETCH,
	):
		if not is_overdue(row.get(PENDING_SINCE), today, threshold):
			continue
		if row.helixhr_stage == "HR":
			owners, owner_name = hr_owned()
		else:
			owners, owner_name = _user_owner(row.leave_approver)
		title = f"{row.leave_type}, {format_date(row.from_date, _date_format())}"
		add("leave", "Leave Application", row, title, row.get(PENDING_SINCE), owners, owner_name, threshold, "leave")

	for doctype, kind, path, manager_state, hr_state, date_field in (
		("Timesheet", "timesheet", "timesheet", "Pending Approval", "Pending HR", "start_date"),
		("Attendance Request", "attendance", "attendance", "Pending Manager", "Pending HR", "from_date"),
	):
		for row in frappe.get_all(
			doctype,
			filters={
				"docstatus": 0,
				"workflow_state": ["in", (manager_state, hr_state)],
				PENDING_SINCE: ["<", cutoff],
			},
			fields=["name", "employee", "employee_name", "workflow_state", date_field, PENDING_SINCE],
			order_by=f"{PENDING_SINCE} asc",
			limit=_OVERDUE_FETCH,
		):
			if not is_overdue(row.get(PENDING_SINCE), today, threshold):
				continue
			if row.workflow_state == hr_state:
				owners, owner_name = hr_owned()
			else:
				owners, owner_name = _manager_owner(row.employee)
			title = f"{_KIND_LABELS[kind]}, {format_date(row.get(date_field), _date_format())}"
			add(kind, doctype, row, title, row.get(PENDING_SINCE), owners, owner_name, threshold, path)

	# R25: HR Requests count from `creation` against the category's SLA; 0 is
	# no SLA, and "Waiting on Employee" is the employee's turn, never late.
	slas = {
		row.name: cint(row.sla_days)
		for row in frappe.get_all("HelixHR Request Category", fields=["name", "sla_days"])
	}
	timed = [sla for sla in slas.values() if sla > 0]
	for row in (
		frappe.get_all(
			"HR Request",
			filters={
				"status": ["in", ("Open", "In Progress")],
				"category": ["in", [name for name, sla in slas.items() if sla > 0]],
				# The shortest SLA's cutoff; the per-category check below does the rest.
				"creation": ["<", add_days(today, -min(timed))],
			},
			fields=["name", "employee", "category", "subject", "routed_to_role", "picked_up_by", "creation"],
			order_by="creation asc",
			limit=_OVERDUE_FETCH,
		)
		if timed
		else []
	):
		sla = slas.get(row.category, 0)
		if sla <= 0 or not is_overdue(row.creation, today, sla):
			continue
		row.employee_name = frappe.db.get_value("Employee", row.employee, "employee_name")
		if row.picked_up_by:
			owners, owner_name = _user_owner(row.picked_up_by)
		elif row.routed_to_role == _HR_ROLE:
			owners, owner_name = hr_owned()
		else:
			owners = _mailable(_role_holders(row.routed_to_role))
			owner_name = row.routed_to_role or "No owner"
		add("request", "HR Request", row, row.subject, row.creation, owners, owner_name, sla, "request")

	# Plan 2026-10-04-003 U3: an open change request waits on its stamped
	# approver (or HR, when it routed there) the same way every other kind
	# waits, so it joins the same digest.
	for row in frappe.get_all(
		"HelixHR Timesheet Change",
		filters={"status": "Open", PENDING_SINCE: ["<", cutoff]},
		fields=["name", "employee", "employee_name", "week_start", "approver_user", PENDING_SINCE],
		order_by=f"{PENDING_SINCE} asc",
		limit=_OVERDUE_FETCH,
	):
		if not is_overdue(row.get(PENDING_SINCE), today, threshold):
			continue
		if row.approver_user:
			owners, owner_name = _user_owner(row.approver_user)
		else:
			owners, owner_name = hr_owned()
		title = f"Change request, {format_date(row.week_start, _date_format())}"
		add("change", "HelixHR Timesheet Change", row, title, row.get(PENDING_SINCE), owners, owner_name, threshold, "change")

	items.sort(key=lambda item: -item["age_days"])
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
	status = frappe.db.get_value("Employee", {"user_id": user}, "status")
	if status and status != "Active":
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


def _role_holders(role):
	if not role:
		return []
	return frappe.get_all("Has Role", filters={"role": role, "parenttype": "User"}, pluck="parent")


def _active_hr_managers():
	"""Who receives `hr_overdue_summary` (R24)."""
	return [user for user in _mailable(_role_holders(_HR_ROLE)) if user != "Administrator"]
