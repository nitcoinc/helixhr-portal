# Copyright (c) 2026, HelixHR Contributors
# For license information, please see license.txt

import uuid

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime
from hrms.api import get_current_employee

from helixhr.utils import read_companies, resolve_admin_scope, role_scope, scope_covers

# The first worker claim and terminal outcomes. Both are read off the
# DocType's own Select options; a status added in Desk that is in neither set
# simply stamps nothing rather than guessing (P2-U8, P5-R6).
# Plan 2026-10-15-003 KTD4: Need info straight from Open is a pick-up too, so
# Waiting on Employee stamps the asker when nobody picked the request up yet.
PICKED_UP_STATUSES = ("In Progress", "Waiting on Employee")
CLOSED_STATUSES = ("Done", "Rejected")


class HRRequest(Document):
	def before_insert(self):
		# `employee` is set_only_once at the field level (locks it after
		# the first save), but that alone doesn't stop a caller naming a
		# different employee on the very first insert -- resolve it from
		# the session instead of trusting whatever was posted (KTD5).
		self.employee = get_current_employee()
		route_to_role, name_prefix = frappe.db.get_value(
			"HelixHR Request Category", self.category, ["route_to_role", "name_prefix"]
		) or (None, None)
		self.routed_to_role = route_to_role
		if not self.routed_to_role:
			frappe.throw("This request category has no worker role configured.")

		# Plan 2026-10-04-002 U1: each category numbers its own requests.
		# This runs before naming (`set_new_name` follows `before_insert`) and
		# `_validate_selects` skips the naming_series Select, so the series
		# the category picks is the one the counter uses. An empty or missing
		# prefix means the HR-REQ fallback; an existing request's name is
		# never revisited, so its ID keeps the prefix it was born with.
		self.naming_series = f"{name_prefix or 'HR-REQ'}-.YYYY.-"

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


def _company_scope_condition(companies):
	"""The list-route half of an HR Manager / System Manager's company scope
	(P5-R5): every request whose employee is in one of ``companies``.

	`ensure_hr_manager_user()` deliberately holds no Employee record -- the
	same Desk-only, no-company HR Manager P3-KTD7/P4-KTD7 already reach every
	Attendance Request and Timesheet through the role alone. That holder is
	unscoped and never reaches here (the caller returns no condition), so the
	wide-open behaviour for *that* persona is preserved on purpose. A portal
	HR Manager who does have an active Employee
	(`make_test_hr_manager_employee`) is the one this scope narrows, since
	they are the multi-company risk P5-R5 exists to close. An empty list is
	nobody.
	"""
	if not companies:
		return "1=0"
	company_employees = frappe.get_all("Employee", filters={"company": ["in", companies]}, pluck="name")
	if not company_employees:
		return "1=0"
	return f"employee in ({', '.join(frappe.db.escape(name, percent=False) for name in company_employees)})"


def _hr_routes_condition(roles):
	"""What HR scope reads, as SQL: every route for a System Manager, else
	every route but the worker roles' (plan 2026-10-19-001 U3, R3). A
	request routed to IT Team is read through IT Team's own clause -- the
	Frappe role and IT Admin rows on the employee's org -- whether or not the
	reader is also HR, so a list never shows a row its reader can't open
	(`api._assert_may_act_on`)."""
	if "System Manager" in roles:
		return ""
	workers = ", ".join(frappe.db.escape(role, percent=False) for role in sorted(_WORKER_ROLES))
	return f"ifnull(routed_to_role, '') not in ({workers})"


def get_permission_query_conditions(user=None, doctype=None, **kwargs):
	"""Limit lists to a requester's records, plus what their assignments
	reach (plan 2026-10-19-001 U3): an HR Manager / System Manager's HR
	scope (`_hr_routes_condition`: not the worker routes, for an HR
	Manager), and each routed worker role's own orgs for requests routed to
	it. A Frappe role with no assignment adds nothing (R3)."""
	user = user or frappe.session.user
	if user == "Administrator":
		return ""
	roles = set(frappe.get_roles(user))
	clauses = []
	if roles & _UNSCOPED_ROLES:
		# One answer for "which employees may this user administer"
		# (`resolve_admin_scope`, P6-R6): an HR Manager whose own Employee is
		# Left, Inactive or Suspended resolves to nobody, never to everybody.
		companies = read_companies(resolve_admin_scope(user))
		routes = _hr_routes_condition(roles)
		if companies is None:
			if not routes:
				return ""
			clauses.append(routes)
		elif companies:
			scope = _company_scope_condition(companies)
			clauses.append(f"({scope} and {routes})" if routes else scope)
	employee = frappe.db.get_value("Employee", {"user_id": user, "status": "Active"}, "name")
	if employee:
		clauses.append(f"employee = {frappe.db.escape(employee, percent=False)}")
	for role in sorted(roles & _WORKER_ROLES):
		companies = read_companies(role_scope(user, role))
		if companies is not None and not companies:
			continue
		route = f"routed_to_role = {frappe.db.escape(role, percent=False)}"
		# Unscoped: every request routed to the role. Never `employee in ()`,
		# which is a SQL error on every list this hook guards.
		clauses.append(route if companies is None else f"({_company_scope_condition(companies)} and {route})")
	if not clauses:
		return "1=0"
	return f"({' or '.join(clauses)})"


def has_permission(doc, ptype=None, user=None, **kwargs):
	"""The single-document half of routed request visibility (P5-KTD13:
	every branch returns an explicit boolean)."""
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	roles = set(frappe.get_roles(user))
	if doc.employee == frappe.db.get_value("Employee", {"user_id": user, "status": "Active"}, "name"):
		return True
	company = frappe.db.get_value("Employee", doc.employee, "company")
	if (
		roles & _UNSCOPED_ROLES
		and ("System Manager" in roles or doc.routed_to_role not in _WORKER_ROLES)
		and scope_covers(resolve_admin_scope(user), company)
	):
		return True
	# A routed worker: the stored route is a role they hold, and their
	# assignments for it cover the employee's company (U3).
	return bool(doc.routed_to_role in roles and scope_covers(role_scope(user, doc.routed_to_role), company))


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
	# `status = "Active"`, the same scope `session_company` and
	# `hrms.api.get_current_employee` resolve by: a user whose Employee has
	# been set to Left or Inactive while their login is still enabled must
	# not keep passing the ownership branch of `events.file_before_insert`.
	return employee == frappe.db.get_value(
		"Employee", {"user_id": frappe.session.user, "status": "Active"}, "name"
	)
