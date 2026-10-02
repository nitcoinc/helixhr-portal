# Copyright (c) 2026, HelixHR Contributors
# For license information, please see license.txt

"""Fresh-install completion.

`bench new-site --install-app helixhr` (and `bench install-app helixhr` on an
existing site) marks every entry in `patches.txt` as already applied --
`frappe.installer.install_app` calls `set_all_patches_as_completed`, which
inserts a Patch Log row for each patch *without running it* -- on the
assumption that a fresh install's doctype JSON already reflects the schema a
patch would have produced. That assumption holds for a schema-migration
patch; it does not hold for `patches/v1_0/apply_permission_deltas`,
`patches/v1_0/report_unsubmitted_approved_leave`,
`patches/v1_0/seed_celebration_templates`,
`patches/v1_0/seed_request_categories`,
`patches/v1_0/route_it_asset_requests` and
`patches/v1_0/migrate_celebration_reminders`, which insert or mutate data
rather than schema. A site created with `--install-app` therefore silently
skips them and ships with unpatched permissions -- this is what broke CI's
first real run, and it would equally break every fresh production install:
nothing in that flow ever calls `bench migrate`.

`bench migrate` on an existing site is unaffected by this file: each patch
still runs exactly once, tracked by its own Patch Log row, exactly as before.
This hook exists only for the one path migrate does not cover.
"""

import frappe


def after_install():
	from helixhr.patches.v1_0 import (
		apply_permission_deltas,
		migrate_celebration_reminders,
		migrate_message_templates_to_jinja,
		report_unsubmitted_approved_leave,
		retire_request_notifications,
		route_it_asset_requests,
		seed_celebration_templates,
		seed_message_templates,
		seed_profile_correction_category,
		seed_request_categories,
		turn_off_hrms_celebration_senders,
	)

	report_unsubmitted_approved_leave.execute()
	seed_celebration_templates.execute()
	seed_message_templates.execute()
	# U8: the seed still writes the legacy {token} wording; convert it.
	migrate_message_templates_to_jinja.execute()
	seed_request_categories.execute()
	seed_profile_correction_category.execute()
	route_it_asset_requests.execute()
	retire_request_notifications.execute()
	apply_permission_deltas.execute()
	# P8-U10: after seed_celebration_templates, not before -- reads nothing
	# from it, but keeps the two celebration-related patches in the order
	# a reader would expect.
	migrate_celebration_reminders.execute()
	turn_off_hrms_celebration_senders.execute()
	frappe.db.commit()  # nosemgrep
