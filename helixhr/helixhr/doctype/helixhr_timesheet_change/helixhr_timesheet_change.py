# Copyright (c) 2026, HelixHR Contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

# Plan 2026-10-04-003 U3 / KTD3. A change request is its own DocType, not a
# state on the Timesheet: an approved week is docstatus 1 and immutable, so
# state on it would need allow-on-submit fields and could not keep the
# history of several requests. It has no Employee-role DocPerm at all --
# every read and write goes through the whitelisted projections in
# `helixhr.api`, matching "projections, not permissions".

# R7: the comment is the whole request, so a stub ("fix", "wrong") tells the
# approver nothing. Ten characters is one short sentence.
MIN_COMMENT_CHARS = 10

CHANGE_OPEN = "Open"
CHANGE_ACCEPTED = "Accepted"
CHANGE_DECLINED = "Declined"
CHANGE_WITHDRAWN = "Withdrawn"


def comment_problem(comment):
	"""Why `comment` cannot carry a change request, or None when it can."""
	text = (comment or "").strip()
	if len(text) < MIN_COMMENT_CHARS:
		return _("Say what should change in at least {0} characters.").format(MIN_COMMENT_CHARS)
	return None


class HelixHRTimesheetChange(Document):
	def validate(self):
		"""The same rules `helixhr.api.raise_timesheet_change` enforces,
		restated where every route has to pass -- including a Desk insert by
		an HR Manager, which the portal method never sees."""
		from helixhr.api import week_change_problem

		problem = comment_problem(self.comment)
		if problem:
			frappe.throw(problem)
		problem = week_change_problem(self.timesheet)
		if problem:
			frappe.throw(problem)
		if self.status != CHANGE_OPEN and not self.decided_on:
			self.decided_on = now_datetime()
