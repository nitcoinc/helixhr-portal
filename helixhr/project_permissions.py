# P7-U1: Project and Task have no scope of their own (KTD8).
#
# ERPNext ships no `permission_query_conditions` for either doctype (verified
# on the bench; see the plan's Sources section), and neither doctype's
# costing-tab fields carry any permlevel restriction, so a plain DocPerm here
# would let its holder read and write the *whole* document -- costing tab
# included -- through Frappe's generic REST routes, bypassing
# `helixhr.api.get_project`'s explicit field allow-list entirely (R8, KTD9).
#
# `apply_permission_deltas` originally paired a read/write/create DocPerm for
# `HelixHR Delivery Manager` with these two hooks so REST access would be
# *scoped* to member projects. Code review found that pairing insufficient --
# a DocPerm has no field-level notion of scope, so "scoped" still meant "the
# whole document, for projects they administer." The DocPerm was removed;
# these hooks now refuse the "assigned" scope kind outright (Frappe's custom
# `has_permission` hook is independently authoritative, so removing the
# DocPerm alone does not close this off -- a hook returning `True` grants
# access even with no base permission at all, confirmed on the bench). HR
# Manager's "company" scope and System Manager's "unscoped" scope are
# unaffected; they hold no field-level exposure risk this plan is scoped to
# fix, and narrowing them further is not this unit's job.
#
# `helixhr.utils.resolve_project_scope` is still the single definition of the
# rule for HelixHR's own methods (search_projects, get_project, ...), which
# reach Project and Task via `ignore_permissions=True`/`frappe.db.get_value`
# and never go through these hooks at all.

import frappe

from helixhr.utils import resolve_project_scope


def _allowed_project_names(scope):
	"""The Project names ``scope`` covers, or ``None`` for "every project" --
	the same ``None``-means-unbounded contract `project_scope_filters` uses,
	kept separate from it because a SQL condition string and a
	`frappe.get_all` filter dict guard the empty case differently (an empty
	``in ()`` is a SQL error in a hand-built condition string; it is not an
	error as a `frappe.get_all` filter -- see `project_scope_filters`).

	"assigned" (HelixHR Delivery Manager) returns no names at all -- see the
	module docstring for why this route is refused outright rather than
	narrowed to memberships."""
	if scope["kind"] == "unscoped":
		return None
	if scope["kind"] == "company":
		if not scope["company"]:
			return []
		return frappe.get_all("Project", filters={"company": scope["company"]}, pluck="name")
	return []


def _condition(scope, field):
	names = _allowed_project_names(scope)
	if names is None:
		return ""
	if not names:
		return "1=0"
	escaped = ", ".join(frappe.db.escape(name, percent=False) for name in names)
	return f"{field} in ({escaped})"


def get_permission_query_conditions(user=None, doctype=None, **kwargs):
	"""Registered for both `Project` (field `name`) and `Task` (field
	`project`) in hooks.py -- one rule, expressed against whichever column
	names a row's project on that doctype."""
	user = user or frappe.session.user
	if user == "Administrator":
		return ""
	scope = resolve_project_scope(user)
	field = "name" if doctype == "Project" else "project"
	return _condition(scope, field)


def has_permission(doc, ptype=None, user=None, **kwargs):
	"""The single-document half, for both `Project` and `Task` -- registered
	twice in hooks.py against this same function, branching on `doc.doctype`
	the way the list-route condition branches on `doctype`.

	"assigned" (HelixHR Delivery Manager) always returns `False` -- see the
	module docstring. A custom `has_permission` hook is independently
	authoritative in Frappe regardless of whether the role holds any base
	DocPerm, so a positive answer here would still grant the whole document
	through the generic REST route even with no DocPerm at all."""
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	scope = resolve_project_scope(user)
	if scope["kind"] == "unscoped":
		return True
	if scope["kind"] in ("none", "assigned"):
		return False
	project_name = doc.name if doc.doctype == "Project" else doc.project
	if not project_name:
		return False
	if scope["kind"] == "company":
		return frappe.db.get_value("Project", project_name, "company") == scope["company"]
	return False
