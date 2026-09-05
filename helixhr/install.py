# Copyright (c) 2026, HelixHR Contributors
# For license information, please see license.txt

"""Fresh-install completion.

`bench new-site --install-app helixhr` (and `bench install-app helixhr` on an
existing site) marks every entry in `patches.txt` as already applied --
`frappe.installer.install_app` calls `set_all_patches_as_completed`, which
inserts a Patch Log row for each patch *without running it* -- on the
assumption that a fresh install's doctype JSON already reflects the schema a
patch would have produced. That assumption holds for a schema-migration
patch; it does not hold for `patches/v1_0/apply_permission_deltas` and
`patches/v1_0/report_unsubmitted_approved_leave`, which mutate data rather
than schema. A site created with `--install-app` therefore silently skips
both and ships with unpatched permissions -- this is what broke CI's first
real run, and it would equally break every fresh production install: nothing
in that flow ever calls `bench migrate`.

`bench migrate` on an existing site is unaffected by this file: each patch
still runs exactly once, tracked by its own Patch Log row, exactly as before.
This hook exists only for the one path migrate does not cover.
"""

import frappe


def after_install():
	from helixhr.patches.v1_0 import apply_permission_deltas, report_unsubmitted_approved_leave

	apply_permission_deltas.execute()
	report_unsubmitted_approved_leave.execute()
	frappe.db.commit()  # nosemgrep
