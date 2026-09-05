# Copyright (c) 2026, Nitco and contributors
# For license information, please see license.txt

import uuid

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime
from hrms.api import get_current_employee

# The statuses that mean HR has taken the request off the pile, and the ones
# that mean it is finished. Both are read off the DocType's own Select
# options; a status added in Desk that is in neither set simply stamps
# nothing rather than guessing (P2-U8).
PICKED_UP_STATUSES = ("In Progress", "Done", "Rejected")
CLOSED_STATUSES = ("Done", "Rejected")


class HRRequest(Document):
	def before_insert(self):
		# `employee` is set_only_once at the field level (locks it after
		# the first save), but that alone doesn't stop a caller naming a
		# different employee on the very first insert -- resolve it from
		# the session instead of trusting whatever was posted (KTD5).
		self.employee = get_current_employee()

		# P2-U8 step 2. The idempotency key is a *unique* column, and a
		# unique column with several empty strings in it is not unique in
		# MariaDB's eyes for `''` the way it is for NULL. Every request
		# therefore carries a key, whether it came from the portal (which
		# supplies its own, generated once per user attempt) or from HR
		# creating one in Desk. The field is read-only and hidden, so this
		# is the only place a value is ever assigned.
		if not self.client_operation_key:
			self.client_operation_key = str(uuid.uuid4())

	def before_save(self):
		"""Stamp the three lifecycle moments the employee is shown.

		They are properties of this record, so they are written inside the
		save that causes them rather than reconstructed from Version rows
		later -- Version stores a JSON diff the Employee role cannot read,
		and parsing it per row is the N+1 P2-R22 exists to prevent.

		Only ever set, never cleared: a request HR reopens keeps the date it
		was first picked up, because that is when it was first picked up.
		A request created before this shipped has no stamps, and the screen
		omits the steps it does not know rather than inventing them.
		"""
		before = self.get_doc_before_save()
		if not before:
			return

		if not self.picked_up_on and self.status in PICKED_UP_STATUSES:
			self.picked_up_on = now_datetime()
		if not self.closed_on and self.status in CLOSED_STATUSES:
			self.closed_on = now_datetime()

		note = (self.hr_note or "").strip()
		if note and note != (before.hr_note or "").strip():
			# The same diff `helixhr.events.hr_request_on_update` uses to
			# decide whether there is a new reply to notify about, so the
			# stamp and the notification can never disagree.
			self.replied_on = now_datetime()

	# status and hr_note are permlevel 1 with only HR Manager/HR User/
	# System Manager granted write there (see this doctype's own
	# permissions, not a fixture) -- Frappe resets an ESS write to either
	# field the same way it does for Employee's locked fields (KTD6), so
	# R22's "Only HR can change status and note" needs no extra code here.
	#
	# P2-U8 goes one step further: role Employee no longer has `create` or
	# `write` on this DocType at all. An employee's own request is made by
	# `helixhr.api.create_my_request` and attachments by
	# `helixhr.api.attach_to_my_request`, both field-allow-listed and
	# session-scoped (P2-R27), so there is no generic Frappe route left that
	# writes an HR Request as an employee.


def request_belongs_to_session(name):
	"""Whether `name` is the session user's own HR Request.

	Shared by the portal methods and by `helixhr.events.file_before_insert`,
	because "may I attach a file to this request" and "may I read this
	request" have to answer from the same rule. Not `frappe.has_permission`
	with `write`: the Employee role deliberately has no write on this
	DocType any more, and an owner check that depended on it would refuse
	the owner.
	"""
	if not name:
		return False
	employee = frappe.db.get_value("HR Request", name, "employee")
	if not employee:
		return False
	return employee == frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "name")
