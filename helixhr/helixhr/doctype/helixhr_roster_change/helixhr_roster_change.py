# Copyright (c) 2026, HelixHR Contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from helixhr.roster_ops import op_date, read_coverage, simulate

# Plan 2026-10-31-001 U7 / KTD7. A manager's batch of shift operations for
# their direct reports, decided by HR. Modelled on HelixHR Timesheet Change:
# no Employee DocPerm, HR Manager read-only, every write through the
# whitelisted methods in `helixhr.api`, which set `flags.helixhr_roster_api`.

ROSTER_DRAFT = "Draft"
ROSTER_PENDING = "Pending"
ROSTER_APPROVED = "Approved"
ROSTER_DECLINED = "Declined"
ROSTER_RETURNED = "Returned"
ROSTER_WITHDRAWN = "Withdrawn"

# The lifecycle diagram in the plan. `None` is a new record: the API may
# create one straight into Pending (the second company of a split batch).
TRANSITIONS = {
	None: {ROSTER_DRAFT, ROSTER_PENDING},
	ROSTER_DRAFT: {ROSTER_PENDING},
	ROSTER_PENDING: {ROSTER_APPROVED, ROSTER_DECLINED, ROSTER_RETURNED, ROSTER_WITHDRAWN},
	ROSTER_RETURNED: {ROSTER_DRAFT},
	ROSTER_WITHDRAWN: {ROSTER_DRAFT},
}
# Written only by the request flow: the decision and the conflict token.
API_ONLY_FIELDS = ("snapshot", "decided_by", "decided_on", "decision_note", "return_reasons")


def pending_requests(employees):
	"""`{employee: request name}` for every one of `employees` named in a
	Pending request (R12: at most one each). One join, unpermissioned:
	callers have already scoped `employees`."""
	employees = list(employees or [])
	if not employees:
		return {}
	request = frappe.qb.DocType("HelixHR Roster Change")
	row = frappe.qb.DocType("HelixHR Roster Change Row")
	found = (
		frappe.qb.from_(row)
		.join(request)
		.on(row.parent == request.name)
		.select(row.employee, request.name)
		.where(row.parenttype == "HelixHR Roster Change")
		.where(request.status == ROSTER_PENDING)
		.where(row.employee.isin(employees))
		.orderby(request.creation)
		.run(as_dict=True)
	)
	return {item.employee: item.name for item in reversed(found)}


class HelixHRRosterChange(Document):
	def validate(self):
		self._guard_transition()
		if self.status == ROSTER_DRAFT and not self.flags.helixhr_roster_api:
			self._check_rows()

	def _guard_transition(self):
		"""Without the API flag a record can be created as a Draft and its
		note edited, nothing more: a Desk "Approved" would apply nothing.
		With it, status still moves only along the lifecycle."""
		before = self.get_doc_before_save()
		old_status = before.status if before else None
		if self.flags.helixhr_roster_api:
			allowed = TRANSITIONS.get(old_status, set())
		else:
			allowed = set() if before else {ROSTER_DRAFT}
			# `has_value_changed` is True for every field of a new record.
			changed = self.has_value_changed if before else self.get
			if any(changed(field) for field in API_ONLY_FIELDS):
				frappe.throw(_("Only the roster request flow can record a decision."))
		if old_status != self.status and self.status not in allowed:
			frappe.throw(
				_("A roster request can't move from {0} to {1} here.").format(
					_(old_status or "new"), _(self.status)
				)
			)

	def _check_rows(self):
		"""The simulator against live coverage, for a Desk insert the portal
		never sees. R13's tomorrow-or-later check needs the company time zone
		and stays with the portal methods."""
		ops = [row.as_dict() for row in self.rows]
		for row in self.rows:
			if row.employee == self.employee:
				frappe.throw(
					_("Row {0}: a manager can't request changes to their own shifts.").format(row.idx)
				)
		dates = [day for day in map(op_date, ops) if day]
		coverage = read_coverage({op.employee for op in ops}, min(dates)) if dates else {}
		problems = simulate(coverage, ops)[1]
		if problems:
			first = problems[0]
			frappe.throw(_("Row {0}: {1}").format(first["index"] + 1, _(first["message"])))
