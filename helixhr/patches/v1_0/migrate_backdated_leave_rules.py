"""Plan 2026-10-06-001 U1 / KTD2, R4: carry the backdated leave rule's two
site-config values into the new HelixHR Leave Rules Single.

Idempotent: once the Single holds a grace value, a rerun does nothing, so a
later HR edit from the Settings Leave rules tab is never overwritten. On a
fresh install there is no site config to copy and the Single's own defaults
apply, so this is a deliberate no-op there."""

import frappe
from frappe.utils import cint

from helixhr.events import leave_rule_stored

GRACE_KEY = "helixhr_backdated_leave_grace_days"
EXEMPT_KEY = "helixhr_backdated_leave_exempt_role"


def execute():
	# Raw read: an unset Int reads as 0 through `get_single_value`, which
	# would hide the "never set" state this guard exists to detect.
	if leave_rule_stored("backdated_grace_days") is not None:
		return
	grace = frappe.conf.get(GRACE_KEY)
	role = (frappe.conf.get(EXEMPT_KEY) or "").strip()
	if grace is None and not role:
		# Nothing to carry over. Saving here would materialise the Single's
		# own default and make a later run look already-migrated.
		return
	doc = frappe.get_single("HelixHR Leave Rules")
	if grace is not None:
		doc.backdated_grace_days = cint(grace)
	if role:
		doc.backdated_exempt_role = role
	doc.flags.ignore_permissions = True
	doc.save()
