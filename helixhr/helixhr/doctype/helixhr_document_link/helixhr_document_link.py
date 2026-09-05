# Copyright (c) 2026, HelixHR Contributors
# For license information, please see license.txt

from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.model.document import Document

# P2-R19: a document link is a policy-link catalogue entry, so the only
# schemes that can ever be right are the two a browser will follow to a
# document. `javascript:` and `data:` are the interesting ones -- both are
# valid URLs to urlparse and both execute in the reader's page if a link
# ever renders as an href.
ALLOWED_URL_SCHEMES = ("http", "https")

# Roles that work these records in Desk and are not scoped to one company.
_UNSCOPED_ROLES = {"HR Manager", "HR User", "System Manager"}


class HelixHRDocumentLink(Document):
	def validate(self):
		self.url = validate_document_url(self.url)


def document_url_problem(url):
	"""Why `url` is not a document link, or None when it is one.

	The rule, once. `validate_document_url` throws it at save time and
	`preflight.check_document_link_urls` counts the rows that already
	break it -- validation only runs on save, so a `javascript:` link
	written before P2-R19 stays in the table until somebody looks.
	"""
	value = (url or "").strip()
	if not value:
		return _("A document needs a link.")

	try:
		parsed = urlparse(value)
	except ValueError:
		return _("That link isn't a valid web address.")

	if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES:
		return _("A document link must start with http:// or https://.")
	if not parsed.hostname:
		return _("That link isn't a valid web address.")
	if parsed.username or parsed.password:
		return _("A document link can't carry a username or password.")

	return None


def validate_document_url(url):
	"""Return `url` trimmed if it is a plain HTTP(S) address, else throw
	(P2-R19). Rejected before storage rather than filtered on render, so a
	bad value cannot reach a page that forgets to escape it."""
	problem = document_url_problem(url)
	if problem:
		frappe.throw(problem)
	return (url or "").strip()


def get_permission_query_conditions(user=None, **kwargs):
	"""P2-R19 / P2-AE2: list-shaped reads see global links plus their own
	company's. Registered in hooks.py, so it applies to every generic
	route -- frappe.client.get_list, /api/resource, report view and export
	-- not only to the portal's own method.

	The `company` field carries `ignore_user_permissions` (see this
	doctype's JSON): a User Permission on Company would hide the *global*
	links too, because a document with an empty link field fails a strict
	user-permission check. Scope is therefore owned entirely here, and it
	does not depend on whether a site happens to have created a Company
	User Permission alongside the Employee one.
	"""
	user = user or frappe.session.user
	if _sees_every_company(user):
		return ""

	table = "`tabHelixHR Document Link`"
	global_only = f"({table}.company is null or {table}.company = '')"
	company = _session_company(user)
	if not company:
		return global_only
	return f"({global_only} or {table}.company = {frappe.db.escape(company)})"


def has_permission(doc, ptype=None, user=None, **kwargs):
	"""The single-document half of the same rule (P2-AE2). A controller
	hook can only deny, and returning a falsy value denies -- so this
	always returns an explicit True when the record is in scope."""
	user = user or frappe.session.user
	if _sees_every_company(user):
		return True
	if not doc.get("company"):
		return True
	return doc.get("company") == _session_company(user)


def _sees_every_company(user):
	return user == "Administrator" or bool(set(frappe.get_roles(user)) & _UNSCOPED_ROLES)


def _session_company(user):
	"""The company of the active Employee behind `user`, or None. Derived
	from the session the same way every other portal read is (KTD5) --
	never from an argument the caller controls."""
	return frappe.db.get_value("Employee", {"user_id": user, "status": "Active"}, "company")
