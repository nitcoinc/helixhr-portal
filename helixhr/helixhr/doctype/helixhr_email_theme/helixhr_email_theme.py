# Copyright (c) 2026, HelixHR Contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from helixhr.utils import TemplateRejected, valid_email_header_color, validate_email_theme


class HelixHREmailTheme(Document):
	"""The one look every portal email shares (`utils.wrap_in_theme`).

	Validation lives here, not in the API, so a Desk save is held to the same
	sandbox rules as the portal's Theme tab."""

	def validate(self):
		self.brand_color = valid_email_header_color(self.brand_color) or (self.brand_color or "").strip()
		self.use_custom_code = cint(self.use_custom_code)
		try:
			validate_email_theme(self)
		except TemplateRejected as exc:
			frappe.throw(str(exc), title=_("Theme not saved"))
