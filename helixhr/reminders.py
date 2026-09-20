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
`HelixHR Celebration Reminder` per event -- its `email_template` (a Link to
Email Template), `is_enabled`, `recipient_mode` ("All employees" or
"Selected employees") and, for the latter, its `recipients` child table.
Disabled, or no template picked, means HelixHR sends nothing for that event.
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

HR authors the Email Template from the portal (P8-U12), which makes its body
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
from frappe.utils import comma_sep, format_date, get_url, getdate
from hrms.controllers.employee_reminders import (
	get_all_employee_emails,
	get_employee_email,
	get_employees_having_an_event_today,
	get_sender_email,
)

# One row per event: the words HR reads on the celebrations settings tab,
# and the HRMS checkbox that would send the stock email for the same event.
# `events.hr_settings_validate` and `preflight.check_celebration_reminders`
# both quote from here, so the refusal and the preflight line name the same
# event the form does. The DocType name itself (not listed per-event here)
# is `HelixHR Celebration Reminder`, autonamed on this dict's own keys.
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
}


def send_celebration_reminders():
	"""Daily (`hooks.scheduler_events`). Idle until HR enables the reminder
	and picks a template for the event (P4-R17), so an install ships
	sending nothing.

	No commit of its own: `frappe.sendmail` commits the Email Queue row it
	writes, and nothing else here writes anything.

	NOT idempotent, unlike `tasks.null_stale_checkin_coordinates`, and once a
	day is the assumption: there is no per-(event, company, date) marker, so
	a second run on the same day sends every celebration email a second time.
	The scheduler runs it once, and `docs/deployment.md`'s U6 steps have an
	operator run it by hand with `bench execute` -- doing that on a live site
	after the scheduler has already been round mails the whole company twice.
	A marker would need either a new DocType or a Custom Field written from a
	job, which is more machinery than a once-a-day sender is worth; the
	pruning of Email Queue rules out reading the queue back as one. HRMS's own
	celebration job makes exactly the same assumption.

	Failure is isolated per event and, inside `_send_event`, per company: one
	template that raises while rendering must not cost the other event its
	mail (P4-KTD13 hands HR the template, so a template that raises is HR's
	edit away). Every swallowed error goes to the scheduler log via
	`frappe.log_error` -- it must surface somewhere, not vanish.
	"""
	sent = {}
	for event in EVENTS:
		if not frappe.db.exists("HelixHR Celebration Reminder", event):
			# Only a site that has not yet run
			# `migrate_celebration_reminders` (or been freshly installed
			# before this patch shipped) -- treated the same as "disabled",
			# not as an error worth logging.
			continue
		reminder = frappe.get_doc("HelixHR Celebration Reminder", event)
		if not reminder.is_enabled or not reminder.email_template:
			continue
		try:
			sent[event] = _send_event(event, reminder)
		except Exception:
			frappe.log_error(
				f"The {event} celebration reminder failed before it reached any company "
				f"(Email Template '{reminder.email_template}') -- nothing was sent for this event.\n\n"
				f"{frappe.get_traceback()}",
				"HelixHR celebration reminders",
			)
			sent[event] = {"companies": 0, "emails": 0, "failed": 1}
	return sent


def _send_event(event, reminder):
	"""Every company with somebody celebrating `event` today.

	Recipients are HRMS's own set arithmetic when `recipient_mode` is "All
	employees" (P4-R16): every active employee in that company, minus the
	people celebrating. In "Selected employees" mode the same subtraction
	runs against `reminder.recipients` narrowed to the celebrating company
	instead of the whole company (P8-U11) -- a selected person in a
	*different* company is never mailed for this company's celebration.
	When two or more share the day, each of them also gets one email about
	the others -- from the same template, so HR words that mail once too,
	in either mode.

	One company at a time, each inside its own try/except: HR edits this
	template (P4-KTD13), so a render that raises -- an undefined filter, an
	attribute the context does not carry -- is a realistic morning, and it
	must cost that company its mail and nothing else. The error goes to the
	scheduler log, the same way the missing-template case above does.
	"""
	template_name = reminder.email_template
	if not frappe.db.exists("Email Template", template_name):
		# `preflight.check_celebration_reminders` FAILs on this. The job says
		# so in the scheduler log and carries on with the other event rather
		# than dying half way through the morning.
		frappe.log_error(
			f"The {event} reminder picks Email Template '{template_name}', "
			"but no such template exists -- nothing was sent for this event.",
			"HelixHR celebration reminders",
		)
		return {"companies": 0, "emails": 0, "failed": 1}

	template = frappe.get_doc("Email Template", template_name)
	sender = get_sender_email()
	grouped = get_employees_having_an_event_today(event) or {}
	selected = (
		[row.employee for row in reminder.recipients]
		if reminder.recipient_mode == "Selected employees"
		else None
	)

	emails = 0
	failed = 0
	for company, persons in grouped.items():
		try:
			emails += _send_company(template, sender, persons, company, event, selected)
		except Exception:
			failed += 1
			frappe.log_error(
				f"The {event} celebration reminder for '{company}' failed -- Email Template "
				f"'{template.name}' was not sent to that company. The other companies and the "
				f"other event are unaffected.\n\n{frappe.get_traceback()}",
				"HelixHR celebration reminders",
			)

	return {"companies": len(grouped), "emails": emails, "failed": failed}


def _send_company(template, sender, persons, company, event, selected):
	"""`selected` is `None` for "All employees" mode -- every active
	employee's own address, HRMS's own helper (P4-R16) -- or the
	`recipients` child table's employee ids for "Selected employees" mode,
	narrowed to `company` before resolving addresses (P8-U11) so a person
	selected in another company is never mailed for this one."""
	emails = 0
	celebrating = {get_employee_email(person) for person in persons}

	if selected is None:
		pool = get_all_employee_emails(company)
	else:
		company_selected = frappe.get_all(
			"Employee",
			filters={"name": ["in", selected], "company": company, "status": "Active"},
			pluck="name",
		)
		pool = get_employee_emails(company_selected)

	recipients = sorted(set(pool) - celebrating)
	if recipients:
		emails += _send(template, sender, recipients, persons, company, event)

	# The shared-day email is between celebrants about each other -- who
	# hears about *them* (the pool above) is what `recipient_mode` scopes,
	# not this. Unconditional on mode, exactly as before P8-U11.
	if len(persons) > 1:
		for person in persons:
			own = get_employee_email(person)
			others = [other for other in persons if other is not person]
			if own:
				emails += _send(template, sender, [own], others, company, event)
	return emails


def _send(template, sender, recipients, persons, company, event):
	rendered = _render_restricted(template, _context(persons, company, event), sender)
	frappe.sendmail(
		sender=sender,
		recipients=recipients,
		subject=rendered["subject"],
		message=rendered["message"],
		reference_doctype="Employee",
	)
	return 1


def _render_restricted(template, context, sender):
	"""`Email Template.get_formatted_email`'s own logic, replicated with
	`restrict_globals=True` (P8-KTD7): HR authors this body from the portal
	now (P8-U12), so it is server-side Jinja under HR's control, not this
	app's own. `frappe.db.get_value` is exposed, unrestricted, under both
	global sets -- rendering read-restricted is not what this buys (see
	the module docstring). What it drops is every side-effecting call:
	`frappe.db.set_value`, `new_doc`/`delete_doc`, `frappe.call`,
	`sendmail`, `db.commit`. A template using only the documented context
	(`_context`, below) renders identically either way -- only a template
	reaching for a write behaves differently, which is the point.
	"""
	context = dict(context)
	if template.use_html:
		context = template.inject_email_account(context, sender=sender)
	body_field = template.response_html if template.use_html else template.response
	return {
		"subject": frappe.render_template(template.subject, context, restrict_globals=True),
		"message": frappe.render_template(body_field, context, restrict_globals=True),
	}


def _context(persons, company, event):
	"""The documented contract HR writes the template against (P4-KTD13).

	`persons` (each `name`, `first_name`, `image_url`, plus `years` on an
	anniversary), `names`, `count`, `company`, `logo_url`, `date`,
	`portal_url` -- and nothing else. `docs/deployment.md` carries the same
	list; a template that reads anything outside it is reading something this
	job does not promise to keep.

	`getdate()` is the site's time zone, the clock HRMS's own job uses, so
	both jobs agree on which day it is.
	"""
	today = getdate()
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
	"""Absolute, because an email is read outside the site. Empty for a
	Company with no logo -- the default templates guard on it, and so should
	HR's."""
	logo = frappe.db.get_value("Company", company, "company_logo")
	return get_url(logo) if logo else ""


def _date_format():
	return frappe.db.get_single_value("System Settings", "date_format") or "yyyy-mm-dd"
