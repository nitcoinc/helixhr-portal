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
		clone_celebration_templates_per_company,
		migrate_backdated_leave_rules,
		migrate_celebration_reminders,
		migrate_message_templates_to_jinja,
		report_unsubmitted_approved_leave,
		retire_hr_email_notifications,
		retire_request_notifications,
		route_it_asset_requests,
		seed_celebration_templates,
		seed_message_templates,
		seed_profile_correction_category,
		seed_report_access,
		seed_request_categories,
		seed_request_category_prefixes,
		split_celebration_reminders_by_company,
		turn_off_hrms_celebration_senders,
		turn_off_hrms_leave_notification,
	)

	report_unsubmitted_approved_leave.execute()
	seed_celebration_templates.execute()
	seed_message_templates.execute()
	# U8: the seed still writes the legacy {token} wording; convert it.
	migrate_message_templates_to_jinja.execute()
	seed_request_categories.execute()
	seed_profile_correction_category.execute()
	# Plan 2026-10-04-002 U1: after the categories exist, before anything
	# else might read a prefix.
	seed_request_category_prefixes.execute()
	route_it_asset_requests.execute()
	retire_request_notifications.execute()
	retire_hr_email_notifications.execute()
	apply_permission_deltas.execute()
	# P8-U10: after seed_celebration_templates, not before -- reads nothing
	# from it, but keeps the two celebration-related patches in the order
	# a reader would expect.
	migrate_celebration_reminders.execute()
	# Plan 2026-10-04-004 U1: after migrate_celebration_reminders, which it
	# supersedes -- a fresh install has no rows for either to create, so
	# both no-op here; on `install-app` over an old site they carry the
	# legacy settings forward in order.
	split_celebration_reminders_by_company.execute()
	# Plan 2026-10-05-001 U11: after the split, which creates the rows it
	# repoints -- a fresh install has none, so this no-ops there.
	clone_celebration_templates_per_company.execute()
	turn_off_hrms_celebration_senders.execute()
	turn_off_hrms_leave_notification.execute()
	seed_report_access.execute()
	# Plan 2026-10-06-001 U1: carries any `helixhr_backdated_leave_*` site
	# config into the HelixHR Leave Rules Single. A no-op on a fresh site --
	# there is no config to copy -- and the only path that carries it over on
	# `install-app` over an existing site, where patches are marked complete.
	migrate_backdated_leave_rules.execute()
	frappe.db.commit()  # nosemgrep
