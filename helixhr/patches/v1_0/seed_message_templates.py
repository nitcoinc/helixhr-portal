"""P5-U13 / P5-KTD11: the default wording for every message the portal sends
outside its own hardcoded copy, seeded once and never overwritten.

`helixhr.events` renders through the matching `HelixHR Message Template` when
one exists and is enabled, falling back to the hardcoded wording it always
had otherwise -- so a fresh install behaves exactly as it did before this
unit, and HR opts into editable wording by nothing more than this seed
existing. This patch never touches a row that already exists: an edited
template must survive `bench migrate` (P5-R17), and a template is plain data
this app seeds once, not a fixture re-imported on every deploy.

Bodies are plain text rendered by `helixhr.utils.render_tokens` -- a fixed
`str.replace` substitution, never Jinja (P5-KTD11) -- so the `{token}` markers
here are literal braces, not template syntax.

Idempotent, and called twice on purpose: `bench new-site --install-app` marks
every patch complete without running it, so `helixhr/install.py` calls
`execute()` too, exactly as `seed_request_categories` and
`seed_celebration_templates` already do.
"""

import frappe

TEMPLATES = (
	{
		"template_key": "request_arrival",
		"subject": "New {category} request: {subject}",
		"body": 'A new {category} request, "{subject}", is waiting for you. Open requests: {portal_url}',
	},
	{
		"template_key": "request_status_changed",
		"subject": "Your request {state}: {subject}",
		"body": "{reason}",
	},
)


def execute():
	for spec in TEMPLATES:
		if frappe.db.exists("HelixHR Message Template", spec["template_key"]):
			continue
		frappe.get_doc({"doctype": "HelixHR Message Template", **spec}).insert(ignore_permissions=True)
