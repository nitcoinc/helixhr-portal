# Copyright (c) 2026, HelixHR Contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from helixhr.utils import PROFILE_CORRECTION_CATEGORY

# P5-R1: a category may route work only to roles deliberately equipped to
# handle the request queue. This is the server-side boundary; the Link picker
# is convenience, not authorization.
WORKER_ROLES = frozenset({"HR Manager", "IT Team"})


class HelixHRRequestCategory(Document):
	def validate(self):
		self.category_name = (self.category_name or "").strip()
		self.route_to_role = (self.route_to_role or "").strip()
		if self.route_to_role not in WORKER_ROLES:
			frappe.throw(_("Choose a role that is configured to work requests."))
		# Plan 2026-10-02-001 R31: a correction can rewrite bank details on
		# Employee, and only HR may apply it, so its route is pinned.
		if (self.name or self.category_name) == PROFILE_CORRECTION_CATEGORY and self.route_to_role != "HR Manager":
			frappe.throw(_("Profile corrections are always handled by HR, so this category can't be rerouted."))
