"""The shared `HelixHR Email Theme` replaces per-company email branding.

Carries forward what the old controls held, then removes the now-unused
`Company.helixhr_email_header_color` Custom Field:

- brand colour: when the theme has none and exactly one distinct valid
  colour is set across companies, it becomes the theme's (several distinct
  colours cannot be merged into one, so none is guessed);
- logo: when the theme has none and the default company has a public
  `company_logo`, it becomes the theme's. `Company.company_logo` itself is
  ERPNext's and is left alone.

Idempotent: a theme value is never overwritten, and a second run finds the
Custom Field already gone. `db.set_single_value`, so no validate runs on a
half-filled Single.
"""

import frappe

from helixhr.utils import EMAIL_THEME, valid_email_header_color, valid_theme_logo

FIELD = "helixhr_email_header_color"


def execute():
	theme = frappe.db.get_singles_dict(EMAIL_THEME)

	if not theme.get("brand_color") and frappe.db.has_column("Company", FIELD):
		colors = {
			valid_email_header_color(value)
			for value in frappe.get_all("Company", pluck=FIELD)
			if valid_email_header_color(value)
		}
		if len(colors) == 1:
			frappe.db.set_single_value(EMAIL_THEME, "brand_color", colors.pop())

	if not theme.get("logo"):
		company = frappe.defaults.get_global_default("company")
		logo = valid_theme_logo(frappe.db.get_value("Company", company, "company_logo")) if company else ""
		if logo:
			frappe.db.set_single_value(EMAIL_THEME, "logo", logo)

	if frappe.db.exists("Custom Field", f"Company-{FIELD}"):
		frappe.delete_doc("Custom Field", f"Company-{FIELD}", ignore_permissions=True, force=True)
