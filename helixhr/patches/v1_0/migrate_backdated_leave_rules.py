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
	# Raw reads: an unset Int reads as 0 through `get_single_value`, which
	# would hide the "never set" state this guard exists to detect. Guard on
	# both fields, so a config that carried only the exempt role is not
	# re-applied over a later HR edit on the next run.
	grace_set = leave_rule_stored("backdated_grace_days") is not None
	role_set = bool((leave_rule_stored("backdated_exempt_role") or "").strip())
	if grace_set or role_set:
		return
	grace = frappe.conf.get(GRACE_KEY)
	role = (frappe.conf.get(EXEMPT_KEY) or "").strip()
	if grace is None and not role:
		# Nothing to carry over -- do not materialise the Single's default.
		return
	values = {}
	if grace is not None:
		# Clamp: the old reader tolerated any value (a negative one read as 0,
		# and it had no upper bound), so migrate must not fail on one.
		values["backdated_grace_days"] = min(max(cint(grace), 0), 365)
	if role and frappe.db.exists("Role", role):
		values["backdated_exempt_role"] = role
	if not values:
		return
	# `set_single_value`, not `doc.save()`: a legacy role that was deleted, or
	# an out-of-range grace, would otherwise abort `bench migrate` on Link or
	# validation. Preflight still reports a missing exempt role.
	frappe.db.set_single_value("HelixHR Leave Rules", values)
