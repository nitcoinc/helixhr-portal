"""The "Profile correction" request category the Profile page files
corrections under (plan 2026-09-29-001, U2).

Insert-if-absent, like `seed_request_categories`: HR may rename its hint,
reroute it or retire it in Settings afterwards, and a re-run must never undo
that. New-site installs mark patches complete, so
``helixhr.install.after_install`` calls this module too.
"""

import frappe

from helixhr.utils import PROFILE_CORRECTION_CATEGORY


def execute():
	if frappe.db.exists("HelixHR Request Category", PROFILE_CORRECTION_CATEGORY):
		return
	frappe.get_doc(
		{
			"doctype": "HelixHR Request Category",
			"category_name": PROFILE_CORRECTION_CATEGORY,
			"hint": "Wrong date of birth, name, bank or ID details",
			"route_to_role": "HR Manager",
		}
	).insert(ignore_permissions=True)
