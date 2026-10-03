"""Plan 2026-10-02-001 U8 / R20: `HelixHR Message Template` rows move from
single-brace `{token}` substitution to the HelixHR Jinja sandbox without
changing what they say.

Per row:
- `is_enabled=0` meant "use the default wording"; under KTD7 that is simply
  no row, so the row is deleted (Off is a new, explicit state).
- A literal `{{`, `{%` or `{#` the old engine printed as-is is escaped, so it
  still prints as-is.
- `{token}` becomes `{{ token }}` for that message's known tokens only;
  any other brace text stays literal. `request_arrival`'s `{portal_url}` was
  the requests queue, which the new catalog calls `action_url`.

`track_changes` is already on in the doctype JSON. Writes go through
`db.set_value` so the new save-time validation cannot refuse a legacy row
mid-migrate; a row the sandbox would refuse is still caught by preflight's
`check_template_tokens`. Idempotent (it is also called from `install.py`):
text that already carries this patch's own output is left alone, since
escaping it a second time would change its meaning.
"""

import re

import frappe

# The single-brace contract as it stood before U8 (frozen here on purpose:
# `helixhr.utils.TEMPLATE_TOKENS` no longer exists).
LEGACY_TOKENS = {
	"request_arrival": {"category": "category", "subject": "subject", "portal_url": "action_url"},
	"request_status_changed": {
		"category": "category",
		"subject": "subject",
		"state": "state",
		"reason": "reason",
	},
}

_JINJA_OPENERS = re.compile(r"\{[{%#]")
_ALREADY_CONVERTED = re.compile(r"\{\{ '\{[{%#]' \}\}|\{\{ (category|subject|state|reason|action_url) \}\}")


def convert(template_key, text):
	"""One field's legacy text as an equivalent sandbox template."""
	if not text or _ALREADY_CONVERTED.search(text):
		return text
	text = _JINJA_OPENERS.sub(lambda match: "{{ '" + match.group(0) + "' }}", text)
	tokens = LEGACY_TOKENS.get(template_key, {})
	if not tokens:
		return text
	pattern = re.compile(r"(?<!\{)\{(" + "|".join(map(re.escape, tokens)) + r")\}(?!\})")
	return pattern.sub(lambda match: "{{ " + tokens[match.group(1)] + " }}", text)


def execute():
	if not frappe.db.table_exists("HelixHR Message Template"):
		return
	for row in frappe.get_all(
		"HelixHR Message Template", fields=["name", "template_key", "is_enabled", "subject", "body"]
	):
		if not row.is_enabled:
			frappe.delete_doc("HelixHR Message Template", row.name, force=True, ignore_permissions=True)
			continue
		frappe.db.set_value(
			"HelixHR Message Template",
			row.name,
			{
				"subject": convert(row.template_key, row.subject),
				"body": convert(row.template_key, row.body),
			},
			update_modified=False,
		)
