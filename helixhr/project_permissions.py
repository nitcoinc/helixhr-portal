# P7-U1: Project and Task have no scope of their own (KTD8).
#
# `apply_permission_deltas.DELTAS` grants `HelixHR Delivery Manager` read, write and
# create on `Project` and `Task` -- a plain DocPerm, carrying no notion of
# *which* project. ERPNext ships no `permission_query_conditions` for either
# doctype (verified on the bench; see the plan's Sources section), so that
# grant alone would let the role list, read and write every project and task
# in the system through Frappe's generic REST routes, `Task` worst of all
# since it carries no company field to fall back on.
#
# These two hooks are the actual boundary. `helixhr.utils.resolve_project_scope`
# is the single definition of the rule; both the list-route condition below
# and the single-document check answer from it, so there is exactly one place
# that says what a HelixHR Delivery Manager, an HR Manager or a System Manager may
# reach -- never two definitions that can drift apart.

import frappe

from helixhr.utils import resolve_project_scope


def _allowed_project_names(scope):
	"""The Project names ``scope`` covers, or ``None`` for "every project" --
	the same ``None``-means-unbounded contract `project_scope_filters` uses,
	kept separate from it because a SQL condition string and a
	`frappe.get_all` filter dict guard the empty case differently (an empty
	``in ()`` is a SQL error in a hand-built condition string; it is not an
	error as a `frappe.get_all` filter -- see `project_scope_filters`)."""
	if scope["kind"] == "unscoped":
		return None
	if scope["kind"] == "company":
		if not scope["company"]:
			return []
		return frappe.get_all("Project", filters={"company": scope["company"]}, pluck="name")
	if scope["kind"] == "assigned":
		return frappe.get_all("Project User", filters={"user": scope["user"]}, pluck="parent")
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
	the way the list-route condition branches on `doctype`."""
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	scope = resolve_project_scope(user)
	if scope["kind"] == "unscoped":
		return True
	if scope["kind"] == "none":
		return False
	project_name = doc.name if doc.doctype == "Project" else doc.project
	if not project_name:
		return False
	if scope["kind"] == "company":
		return frappe.db.get_value("Project", project_name, "company") == scope["company"]
	if scope["kind"] == "assigned":
		return bool(frappe.db.exists("Project User", {"parent": project_name, "user": scope["user"]}))
	return False
