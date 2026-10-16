"""Plan 2026-10-17-001 U12: notification titles in people dates and plain words.

The HelixHR Notification fixture (`fixtures/notification.json`) now formats
its dates as "Mar 8" and takes its state words from the
`helixhr_state_words` Jinja method ("waiting for your manager" for Pending
Approval). The fixture re-syncs on migrate; this patch exists because
AGENTS.md requires a patch for a fixture change, and it applies the
fixture's subject and message to the existing rows even where the fixture
sync is skipped. The text is copied from the fixture, so the fixture stays
the single source of the wording.

Idempotent: a row whose subject and message already match is left alone.
A Notification missing on this site is skipped, never created.
"""

import json

import frappe

_COPIED = ("subject", "message")


def _fixture_notifications():
	path = frappe.get_app_path("helixhr", "fixtures", "notification.json")
	with open(path) as handle:
		return json.load(handle)


def execute():
	for row in _fixture_notifications():
		name = row["name"]
		if not frappe.db.exists("Notification", name):
			continue
		current = frappe.db.get_value("Notification", name, _COPIED, as_dict=True)
		changed = {field: row[field] for field in _COPIED if current.get(field) != row[field]}
		if changed:
			frappe.db.set_value("Notification", name, changed)
