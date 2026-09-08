"""Scheduled work. P3-U4 step 2a / P3-KTD15 / P3-R28.

Punch coordinates are the one thing this portal stores that nobody needs
after the punch has become Attendance: HR and every manager up the chain can
read a report's Employee Checkin rows, so a year of them is a location
history of a person's mornings. DPDP section 8(7) asks for erasure once the
purpose is served, and the purpose is served the moment the attendance is
marked -- so the coordinates have a retention period, and the period is the
only value HR and legal decide (`docs/deployment.md`).

The job is idle until `helixhr_checkin_location_retention_days` is set in the
site config, and `preflight.check_checkin_location_retention` says so while
it is. `frappe.conf` rather than a Singles row on purpose: this is an
operator decision, it must not be editable from Desk by the roles whose
punches it governs, and it belongs in the same place as the rest of the
site's operational configuration.
"""

import json

import frappe
from frappe.utils import add_to_date, cint, now_datetime

RETENTION_KEY = "helixhr_checkin_location_retention_days"
# The three fields a punch's location lives in, and the value each one is
# erased *to*. `geolocation` is HRMS's own GeoJSON copy, written by
# `set_geolocation_from_coordinates` -- nulling the two floats and leaving it
# behind would erase nothing. Frappe's Float columns are NOT NULL, so
# "null the coordinates" is 0 for the two floats and an empty string for the
# GeoJSON text -- which is also what makes `["latitude", "is", "set"]` stop
# matching an erased row, and therefore what makes the job idempotent.
ERASED = {"latitude": 0, "longitude": 0, "geolocation": ""}
LOCATION_FIELDS = tuple(ERASED)
# One statement per batch, so a first run on a site with years of punches
# does not build a single unbounded UPDATE.
_BATCH = 500
# A first run walks a backlog; anything beyond this many batches is a sign
# the filter is not narrowing and the job should stop rather than spin.
_MAX_BATCHES = 200

_LOCATION_SET = [[field, "is", "set"] for field in LOCATION_FIELDS]


def null_stale_checkin_coordinates():
	"""Daily (`hooks.scheduler_events`). Erase the coordinates of punches
	older than the configured retention period, and the same values out of
	their Version history.

	Idempotent by construction: the filter only ever matches rows that still
	carry a coordinate, so a second run in the same day finds nothing.
	"""
	days = cint(frappe.conf.get(RETENTION_KEY))
	if days <= 0:
		return {"retention_days": days, "scrubbed": 0}

	cutoff = add_to_date(now_datetime(), days=-days)
	scrubbed = 0
	for _batch in range(_MAX_BATCHES):
		names = frappe.get_all(
			"Employee Checkin",
			filters={"time": ["<", cutoff]},
			or_filters=_LOCATION_SET,
			pluck="name",
			limit=_BATCH,
			order_by="time asc",
		)
		if not names:
			break
		scrubbed += scrub_checkin_locations(names)
		# One commit per batch. A first run over a real backlog is 200
		# batches of 500 rows plus their Version rewrites, which is well
		# past the scheduled-job timeout -- and a single transaction that
		# times out rolls the whole sweep back, so the job never makes any
		# progress at all. Committing per batch is safe precisely because
		# the sweep is idempotent: a run that dies half way leaves the
		# batches it finished erased and the next run starts from what is
		# left. Only here -- `scrub_checkin_locations` itself stays
		# commit-free, because the status-to-Left path calls it inside the
		# Employee save's own transaction (P3-KTD15).
		frappe.db.commit()
	return {"retention_days": days, "scrubbed": scrubbed}


def scrub_employee_checkin_locations(employee):
	"""Every punch this employee still has a location on. Called from
	`events.employee_on_update` the moment their status becomes Left: the
	retention period is about how long the data stays useful, and for
	somebody who has left it stopped being useful on their last day."""
	scrubbed = 0
	for _batch in range(_MAX_BATCHES):
		names = frappe.get_all(
			"Employee Checkin",
			filters={"employee": employee},
			or_filters=_LOCATION_SET,
			pluck="name",
			limit=_BATCH,
		)
		if not names:
			break
		scrubbed += scrub_checkin_locations(names)
	return scrubbed


def scrub_checkin_locations(names):
	"""Null the three location fields on these punches and take the same
	fields out of their Version rows.

	`frappe.db.set_value` rather than a document save: a save would rerun
	HRMS's `validate` (which refuses a coordinate-less punch while
	geolocation tracking is on, and would refuse to erase anything at all)
	and would rewrite `modified`, making an erasure look like an edit.
	Employee Checkin has `track_changes`, so the values also sit in
	`tabVersion` -- erasing the row and leaving the audit trail erases
	nothing.
	"""
	if not names:
		return 0
	frappe.db.set_value(
		"Employee Checkin",
		{"name": ["in", names]},
		dict(ERASED),
		update_modified=False,
	)
	_scrub_versions(names)
	return len(names)


def _scrub_versions(names):
	for row in frappe.get_all(
		"Version",
		filters={"ref_doctype": "Employee Checkin", "docname": ["in", names]},
		fields=["name", "data"],
	):
		try:
			data = json.loads(row.data or "{}")
		except (TypeError, ValueError):
			continue
		if not isinstance(data, dict):
			continue
		changed = False
		for key in ("changed", "added", "removed"):
			entries = data.get(key)
			if not isinstance(entries, list):
				continue
			kept = [
				entry
				for entry in entries
				if not (isinstance(entry, list) and entry and entry[0] in LOCATION_FIELDS)
			]
			if len(kept) != len(entries):
				data[key] = kept
				changed = True
		if changed:
			frappe.db.set_value("Version", row.name, "data", json.dumps(data), update_modified=False)
