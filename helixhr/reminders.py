# Copyright (c) 2026, HelixHR Contributors
# For license information, please see license.txt

"""The birthday and work-anniversary emails HR words themselves. P4-U6 /
P4-KTD10..KTD13 / P4-R15..R20.

HRMS already sends both, from a hardcoded subject, header and Jinja file, and
the only switch is a checkbox. There is no Email Template record to edit, so
the copy cannot be changed without editing HRMS -- which the next
`bench update` overwrites (P4-R20 is that no HRMS file is touched).

So this job runs *beside* HRMS's, in the same daily slot, and reads two
Custom Fields on HR Settings -- `helixhr_birthday_template` and
`helixhr_anniversary_template`, each a Link to Email Template. Empty means
HelixHR sends nothing for that event; picking a template is how HR switches
the branded email on. HRMS's two checkboxes keep working exactly as before:
Frappe merges `scheduler_events` across apps and offers no way to remove
another app's job, so both senders being on at once is a real hazard --
`events.hr_settings_validate` refuses the save that would create it and
`preflight.check_celebration_reminders` is the post-deploy backstop
(P4-KTD10).

Nothing here is monkeypatched and no HRMS template file is shadowed. Both
were considered and declined: the subject and header stay hardcoded in HRMS
Python either way, and neither is HR-editable.

Who is celebrating, and who hears about it, is HRMS's answer (P4-KTD12): the
four helpers below are imported, never re-implemented, so eligibility cannot
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
from frappe.utils import comma_sep, format_date, get_url, getdate
from hrms.controllers.employee_reminders import (
	get_all_employee_emails,
	get_employee_email,
	get_employees_having_an_event_today,
	get_sender_email,
)

# One row per event: the HelixHR template picker, the HRMS checkbox that
# would send the stock email for the same event, and the words HR reads on
# the HR Settings form. `events.hr_settings_validate` and
# `preflight.check_celebration_reminders` both quote from here, so the
# refusal and the preflight line name the same two fields as the form.
EVENTS = {
	"birthday": {
		"label": "Birthday",
		"template_field": "helixhr_birthday_template",
		"template_label": "HelixHR Birthday Template",
		"hrms_field": "send_birthday_reminders",
		"hrms_label": "Birthdays",
	},
	"work_anniversary": {
		"label": "Work anniversary",
		"template_field": "helixhr_anniversary_template",
		"template_label": "HelixHR Work Anniversary Template",
		"hrms_field": "send_work_anniversary_reminders",
		"hrms_label": "Work Anniversaries",
	},
}


def send_celebration_reminders():
	"""Daily (`hooks.scheduler_events`). Idle until HR picks a template on
	HR Settings for the event (P4-R17), so an install ships sending nothing.

	No commit of its own: `frappe.sendmail` commits the Email Queue row it
	writes, and nothing else here writes anything.
	"""
	sent = {}
	for event, spec in EVENTS.items():
		template = frappe.db.get_single_value("HR Settings", spec["template_field"])
		if not template:
			continue
		sent[event] = _send_event(event, template)
	return sent


def _send_event(event, template_name):
	"""Every company with somebody celebrating `event` today.

	Recipients are HRMS's own set arithmetic (P4-R16): every active employee
	in that company, minus the people celebrating. When two or more share the
	day, each of them also gets one email about the others -- from the same
	template, so HR words that mail once too.
	"""
	if not frappe.db.exists("Email Template", template_name):
		# `preflight.check_celebration_reminders` FAILs on this. The job says
		# so in the scheduler log and carries on with the other event rather
		# than dying half way through the morning.
		frappe.log_error(
			f"HR Settings picks Email Template '{template_name}' for the {event} reminder, "
			"but no such template exists -- nothing was sent for this event.",
			"HelixHR celebration reminders",
		)
		return {"companies": 0, "emails": 0}

	template = frappe.get_doc("Email Template", template_name)
	sender = get_sender_email()
	grouped = get_employees_having_an_event_today(event) or {}

	emails = 0
	for company, persons in grouped.items():
		celebrating = {get_employee_email(person) for person in persons}
		recipients = sorted(set(get_all_employee_emails(company)) - celebrating)
		if recipients:
			emails += _send(template, sender, recipients, persons, company, event)

		if len(persons) > 1:
			for person in persons:
				own = get_employee_email(person)
				others = [other for other in persons if other is not person]
				if own:
					emails += _send(template, sender, [own], others, company, event)

	return {"companies": len(grouped), "emails": emails}


def _send(template, sender, recipients, persons, company, event):
	rendered = template.get_formatted_email(_context(persons, company, event))
	frappe.sendmail(
		sender=sender,
		recipients=recipients,
		subject=rendered["subject"],
		message=rendered["message"],
		reference_doctype="Employee",
	)
	return 1


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
