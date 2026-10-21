"""Plan 2026-10-19-001 U8 (R25): today's access becomes HelixHR Access
Assignment rows, origin "migrated" -- `helixhr.access_migration.migrate`.

Idempotent: a person who already has a row, or a HelixHR Access Change,
is skipped, so a re-run writes nothing and a person whose rows were all
removed through People & access is not recreated. Never touches the legacy
grants or User Permissions, expires no export and sends no mail.

Runs after the doctypes sync but before the fixtures do, so it creates the
two new roles itself (`apply_permission_deltas._ensure_role`). Not called
from `install.py`: a fresh install has no legacy access to migrate.
"""

from helixhr.access_migration import migrate
from helixhr.patches.v1_0.apply_permission_deltas import _ensure_role


def execute():
	for role in ("HelixHR Org Viewer", "HelixHR Access Admin"):
		_ensure_role(role)
	migrate()
