# Copyright (c) 2026, HelixHR Contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from helixhr.utils import (
	ALL_ORGS_ONLY_ROLES,
	ALL_ORGS_SCOPE_KEY,
	ASSIGNABLE_ROLES,
	EXPORT_ROLES,
)


class HelixHRAccessAssignment(Document):
	"""One (user, role, org-or-All) row (plan 2026-10-19-001 KTD1).

	Only System Manager holds a DocPerm, and that is read/report: every write
	goes through the People & access save endpoint or the migration with
	``ignore_permissions``, so Desk and REST can never bypass the role sync or
	the audit. Who may save a row is decided there; this keeps the row itself
	coherent on every route."""

	def validate(self):
		if self.user in ("Administrator", "Guest"):
			frappe.throw(_("{0} cannot hold an assignment.").format(self.user))
		if self.role not in ASSIGNABLE_ROLES:
			frappe.throw(_("{0} is not a role HelixHR assigns.").format(self.role))
		if self.all_orgs and self.company:
			frappe.throw(_("Choose All orgs or one org, not both."))
		if not self.all_orgs and not self.company:
			frappe.throw(_("Choose All orgs or an org."))
		if self.company and not frappe.db.exists("Company", self.company):
			frappe.throw(_("{0} is not a company.").format(self.company))
		if self.role in ALL_ORGS_ONLY_ROLES and not self.all_orgs:
			frappe.throw(_("{0} can only be given on All orgs.").format(self.role))
		if self.allow_export and self.role not in EXPORT_ROLES:
			frappe.throw(_("Export can only be allowed on Reports or Org viewer."))

		# MariaDB treats NULLs as distinct in a unique index, so All-orgs rows
		# need a real value for the index to refuse a second one.
		self.scope_key = ALL_ORGS_SCOPE_KEY if self.all_orgs else self.company
		duplicate = frappe.db.exists(
			"HelixHR Access Assignment",
			{"user": self.user, "role": self.role, "scope_key": self.scope_key, "name": ("!=", self.name)},
		)
		if duplicate:
			frappe.throw(_("{0} already holds {1} there.").format(self.user, self.role))


def on_doctype_update():
	# The index is the backstop for the check in `validate` (two concurrent
	# saves), and leads with `user` so `role_scope`'s one query uses it.
	frappe.db.add_unique(
		"HelixHR Access Assignment", ["user", "role", "scope_key"], constraint_name="unique_user_role_scope"
	)
