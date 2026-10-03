# Copyright (c) 2026, HelixHR Contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from helixhr.utils import NOTIFICATION_EVENTS, TemplateRejected, validate_message_template


class HelixHRMessageTemplate(Document):
	"""A customised (`is_enabled=1`) or switched-off (`is_enabled=0`) portal
	email; no row means the registry default (plan 2026-10-02-001 KTD7).

	Validation lives here, not in the API, so a Desk save is held to the same
	rule as the portal (R17)."""

	def validate(self):
		event = NOTIFICATION_EVENTS.get(self.template_key)
		if not event:
			frappe.throw(_("Not a valid message."))
		if event.get("locked"):
			if not cint(self.is_enabled):
				frappe.throw(_("This security notice cannot be switched off."))
		elif cint(self.is_enabled) and not ((self.subject or "").strip() and (self.body or "").strip()):
			frappe.throw(_("Give the message a subject and a body."))
		try:
			validate_message_template(self.template_key, self.subject, self.body)
		except TemplateRejected as exc:
			frappe.throw(str(exc), title=_("Template not saved"))

	def on_update(self):
		# R18a: `track_changes` writes the Version; this names the actor.
		self.add_comment("Info", _("{0} saved this email template").format(frappe.session.user))
