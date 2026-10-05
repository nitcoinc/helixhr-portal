# Copyright (c) 2026, HelixHR Contributors
# For license information, please see license.txt

"""P8-U10, per company since plan 2026-10-04-004 U1. One row per
(event, company) -- `birthday-Acme`, `holiday-Acme` -- so each company has
its own switch, template and audience, and a company with no row for an
event sends nothing for it (absent means disabled).

Supersedes the two HR Settings custom fields `reminders.py` used to read
(`helixhr_birthday_template`, `helixhr_anniversary_template`) and P8's
global one-row-per-event model (split per company by
`helixhr/patches/v1_0/split_celebration_reminders_by_company.py`).

The row's name is `format:{event}-{company}`, so `reminders.EVENTS` stays
the single source of the event vocabulary: this doctype's three Select
options are the same three keys.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class HelixHRCelebrationReminder(Document):
	def validate(self):
		# A reminder that can reach nobody is a misconfiguration, not a
		# valid state (P8-U10): `recipient_mode` naming "Selected employees"
		# is HR's own statement of intent to hand-pick a list, so an empty
		# one is refused here rather than saved and silently sending to
		# nobody the next time this event fires.
		if self.recipient_mode == "Selected employees" and not self.recipients:
			frappe.throw(_("Pick at least one employee, or switch back to 'All employees'."))

		# Plan 2026-10-04-004 U1: a selected person is mailed for *this*
		# company's celebrations, so a recipient from another company here
		# would put that other company's employee on this company's list --
		# refused rather than silently narrowed at send time.
		for row in self.get("recipients") or []:
			employee_company = frappe.db.get_value("Employee", row.employee, "company")
			if employee_company != self.company:
				frappe.throw(
					_("Employee {0} belongs to {1}, not {2}.").format(
						row.employee, employee_company or _("no company"), self.company
					)
				)

		# The holiday reminder's cadence is the row's own (U1): a holiday
		# row without one is refused, and only the two cadences the sender
		# implements are accepted. Other events ignore `frequency` -- the
		# form hides it, and a stray value would be dead data.
		if self.event == "holiday" and self.frequency not in ("Weekly", "Monthly"):
			frappe.throw(_("Pick how often the holiday reminder goes out: Weekly or Monthly."))
