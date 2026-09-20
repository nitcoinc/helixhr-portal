# Copyright (c) 2026, HelixHR Contributors
# For license information, please see license.txt

"""P8-U10. One row per celebration event (`birthday`, `work_anniversary`),
superseding the two HR Settings custom fields `reminders.py` used to read
(`helixhr_birthday_template`, `helixhr_anniversary_template`) -- carried
across by `helixhr/patches/v1_0/migrate_celebration_reminders.py`.

`event` is the row's own name (`autoname: field:event`), so `reminders.EVENTS`
stays the single source of the event vocabulary: this doctype's two Select
options are the same two keys.
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
