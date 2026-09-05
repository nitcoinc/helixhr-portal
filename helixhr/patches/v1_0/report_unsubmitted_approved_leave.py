"""P2-U1 step 4: report Leave Applications the pre-P2-U1 portal left in a
false-approved state.

Before P2-U1, `helixhr.api.act_on_approval` set `status = "Approved"` and
saved without submitting, so those rows never created a Leave Ledger Entry
and never consumed balance even though the portal called them approved
(P2-R10, P2-AE1).

This patch deliberately only *lists* them. Submitting them now would run
HRMS's balance, overlap and back-dated validations for the first time, and
any one of them may refuse -- and if it does refuse, whether to submit
anyway with a corrected allocation or to reject the request is HR's
decision, not a migration's. `helixhr.preflight` keeps counting them
(check_unsubmitted_approved_leave) until HR has resolved every one in Desk.
"""

import frappe

FIELDS = ["name", "employee", "employee_name", "leave_type", "from_date", "to_date", "leave_approver"]


def execute():
	rows = frappe.get_all(
		"Leave Application",
		filters={"docstatus": 0, "status": "Approved"},
		fields=FIELDS,
		order_by="from_date asc",
	)
	if not rows:
		return

	lines = [
		f"{row.name}\t{row.employee} ({row.employee_name})\t{row.leave_type}"
		f"\t{row.from_date} .. {row.to_date}\tapprover: {row.leave_approver or '-'}"
		for row in rows
	]
	report = (
		f"{len(rows)} Leave Application(s) have status Approved but were never submitted, so they "
		"consumed no leave balance. Resolve each one in Desk by submitting it (if the balance and "
		"dates still allow) or by rejecting it. helixhr.preflight reports a WARN until none remain.\n"
		+ "\n".join(lines)
	)
	# The migration console is this patch's report channel, not debug output:
	# the operator running `bench migrate` is the person who has to act on it.
	print(report)
	frappe.log_error(title="HelixHR: leave approved but never submitted", message=report)
