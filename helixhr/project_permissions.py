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
#
# Plan 2026-10-19-001 KTD10: since a Delivery Manager's project scope became
# whole orgs (U4), these hooks build their scope from `resolve_admin_scope`
# only -- never from Delivery Manager rows -- so a Delivery Manager stays
# refused here on any project, on named orgs or All orgs alike.

import frappe

from helixhr.utils import read_companies, resolve_admin_scope, scope_covers


def _allowed_project_names(scope):
	"""The Project names ``scope`` covers, or ``None`` for "every project" --
	the same ``None``-means-unbounded contract `project_scope_filters` uses,
	kept separate from it because a SQL condition string and a
	`frappe.get_all` filter dict guard the empty case differently (an empty
	``in ()`` is a SQL error in a hand-built condition string; it is not an
	error as a `frappe.get_all` filter -- see `project_scope_filters`)."""
	companies = read_companies(scope)
	if companies is None:
		return None
	if not companies:
		return []
	return frappe.get_all("Project", filters={"company": ["in", companies]}, pluck="name")


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
	scope = resolve_admin_scope(user)
	field = "name" if doctype == "Project" else "project"
	return _condition(scope, field)


def has_permission(doc, ptype=None, user=None, **kwargs):
	"""The single-document half, for both `Project` and `Task` -- registered
	twice in hooks.py against this same function, branching on `doc.doctype`
	the way the list-route condition branches on `doctype`.

	A Delivery Manager always gets `False` (KTD10) -- see the module
	docstring. A custom `has_permission` hook is independently authoritative
	in Frappe regardless of whether the role holds any base DocPerm, so a
	positive answer here would still grant the whole document through the
	generic REST route even with no DocPerm at all."""
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	scope = resolve_admin_scope(user)
	if scope["kind"] == "unscoped":
		return True
	if scope["kind"] == "none":
		return False
	project_name = doc.name if doc.doctype == "Project" else doc.project
	if not project_name:
		return False
	return scope_covers(scope, frappe.db.get_value("Project", project_name, "company"))
