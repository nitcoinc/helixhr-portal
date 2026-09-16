# Copyright (c) 2026, HelixHR Contributors
# For license information, please see license.txt

import uuid

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime
from hrms.api import get_current_employee

from helixhr.utils import _session_company

# The first worker claim and terminal outcomes. Both are read off the
# DocType's own Select options; a status added in Desk that is in neither set
# simply stamps nothing rather than guessing (P2-U8, P5-R6).
PICKED_UP_STATUSES = ("In Progress",)
CLOSED_STATUSES = ("Done", "Rejected")


class HRRequest(Document):
	def before_insert(self):
		# `employee` is set_only_once at the field level (locks it after
		# the first save), but that alone doesn't stop a caller naming a
		# different employee on the very first insert -- resolve it from
		# the session instead of trusting whatever was posted (KTD5).
		self.employee = get_current_employee()
		self.routed_to_role = frappe.db.get_value(
			"HelixHR Request Category", self.category, "route_to_role"
		)
		if not self.routed_to_role:
			frappe.throw("This request category has no worker role configured.")

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
			self.picked_up_by = frappe.session.user
		if not self.closed_on and self.status in CLOSED_STATUSES:
			self.closed_on = now_datetime()

		note = (self.hr_note or "").strip()
		if note and note != (before.hr_note or "").strip():
			# The same diff `helixhr.events.hr_request_on_update` uses to
			# decide whether there is a new reply to notify about, so the
			# stamp and the notification can never disagree.
			self.replied_on = now_datetime()

	# status and hr_note are permlevel 1 with only HR Manager and
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


_WORKER_ROLES = frozenset({"IT Team"})
_UNSCOPED_ROLES = frozenset({"HR Manager", "System Manager"})


def _company_scope_condition(company):
	"""The list-route half of an HR Manager / System Manager's company scope
	(P5-R5): every request whose employee is in that company.

	`ensure_hr_manager_user()` deliberately holds no Employee record -- the
	same Desk-only, no-company HR Manager P3-KTD7/P4-KTD7 already reach every
	Attendance Request and Timesheet through the role alone. `company` is
	`None` for that holder, and the wide-open behaviour for *that* persona is
	preserved on purpose. A portal HR Manager who does have an active
	Employee (`make_test_hr_manager_employee`) is the one this scope narrows,
	since they are the multi-company risk P5-R5 exists to close.
	"""
	if not company:
		return ""
	company_employees = frappe.get_all("Employee", filters={"company": company}, pluck="name")
	if not company_employees:
		return "1=0"
	return f"employee in ({', '.join(frappe.db.escape(name, percent=False) for name in company_employees)})"


def get_permission_query_conditions(user=None, doctype=None, **kwargs):
	"""Limit lists to a requester's records, their routed work, or (for a
	company-anchored HR Manager / System Manager) their own company."""
	user = user or frappe.session.user
	if user == "Administrator":
		return ""
	if set(frappe.get_roles(user)) & _UNSCOPED_ROLES:
		return _company_scope_condition(_session_company(user))
	employee = frappe.db.get_value("Employee", {"user_id": user, "status": "Active"}, "name")
	if not employee:
		return "1=0"
	roles = set(frappe.get_roles(user)) & _WORKER_ROLES
	if not roles:
		return f"employee = {frappe.db.escape(employee, percent=False)}"
	company_employees = frappe.get_all(
		"Employee", filters={"company": _session_company(user)}, pluck="name"
	)
	return (
		f"(employee = {frappe.db.escape(employee, percent=False)} or "
		f"(employee in ({', '.join(frappe.db.escape(name, percent=False) for name in company_employees)}) "
		f"and routed_to_role in ({', '.join(frappe.db.escape(role, percent=False) for role in roles)})))"
	)


def has_permission(doc, ptype=None, user=None, **kwargs):
	"""The single-document half of routed request visibility (P5-KTD13:
	every branch returns an explicit boolean)."""
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	roles = set(frappe.get_roles(user))
	if doc.employee == frappe.db.get_value("Employee", {"user_id": user, "status": "Active"}, "name"):
		return True
	if roles & _UNSCOPED_ROLES:
		company = _session_company(user)
		return not company or frappe.db.get_value("Employee", doc.employee, "company") == company
	return (
		doc.routed_to_role in roles
		and frappe.db.get_value("Employee", doc.employee, "company") == _session_company(user)
	)


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
	# `status = "Active"`, the same scope `_session_company` and
	# `hrms.api.get_current_employee` resolve by: a user whose Employee has
	# been set to Left or Inactive while their login is still enabled must
	# not keep passing the ownership branch of `events.file_before_insert`.
	return employee == frappe.db.get_value(
		"Employee", {"user_id": frappe.session.user, "status": "Active"}, "name"
	)
