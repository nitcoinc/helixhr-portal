# Copyright (c) 2026, HelixHR Contributors
# For license information, please see license.txt

"""Plan 2026-10-31-001 U7 / KTD8, KTD9: the roster request rule engine.

`simulate`, `affected_window`, `op_date` and `snapshot_key` are pure: no
DB, no session. The same rules run in the browser
(`frontend/src/lib/rosterDraft.js`, the draft preview) and on the server
(save, submit, approve). `read_coverage` is the one DB reader, kept apart.

Coverage is `{employee: [assignment]}`; an assignment is a dict with
`key` (the Shift Assignment name, or `new-<op index>` for one an operation
creates), `shift_type`, `start_date` and `end_date` (None = open-ended).

An operation is a dict shaped like a `HelixHR Roster Change Row`:
- Assign: `shift_type` from `from_date`, through `to_date` or open-ended.
- Change: `shift_type` from `from_date`; the covering assignment ends the
  day before and the new one keeps its end (`change_shift_assignment`).
- End: the covering assignment ends on `to_date` (`end_shift_assignment`).
Change and End act on `assignment` when it names one in the coverage,
otherwise on the assignment covering the date as earlier operations left
it -- so "assign a range, then change inside it" works.
"""

import re
from datetime import date, datetime, timedelta

import frappe
from frappe.utils import getdate

ASSIGN = "Assign"
CHANGE = "Change"
END = "End"
ACTIONS = (ASSIGN, CHANGE, END)

# The HR endpoints' sentences. Copies, not imports: `helixhr.api` will
# import this module (U9), so the reverse import would cycle.
# `test_roster_ops.TestMessagesMatchTheHrEndpoints` pins them together.
OVERLAP = "This person already has a shift on some of those dates. End or change that one first."
END_BEFORE_START = "The end date can't be before the shift starts."
CHANGE_ON_START = "That is the day this shift starts. Cancel it and assign the new shift instead."
AFTER_END = "That date is after this shift ends."
NO_SHIFT_TYPE = "Choose a shift."
BAD_DATE = "Give a valid date."
# New to roster requests: an HR cell always names its assignment.
NO_SHIFT_ON_DATE = "This person has no shift on that date to change or end."
BAD_ACTION = "Choose assign, change or end."


def _date(value):
	"""A `date` from a date or a `YYYY-MM-DD` string (the only string form
	the browser mirror accepts), or None when blank or invalid."""
	if not value:
		return None
	if isinstance(value, datetime):
		return value.date()
	if isinstance(value, date):
		return value
	if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)):
		return None
	try:
		return date.fromisoformat(str(value))
	except ValueError:
		return None


def op_date(op):
	"""The date an operation acts on: `to_date` for End, `from_date` for
	Assign and Change. None when missing or invalid. R13's "tomorrow or
	later" applies to this date."""
	return _date(op.get("to_date") if op.get("action") == END else op.get("from_date"))


def _overlaps(assignment, start, end):
	return (end is None or assignment["start_date"] <= end) and (
		assignment["end_date"] is None or assignment["end_date"] >= start
	)


def _target(rows, key, day):
	"""The assignment `key` names, else the first one covering `day`."""
	if key:
		return next((a for a in rows if a["key"] == key), None)
	return next((a for a in rows if _overlaps(a, day, day)), None)


def _apply(rows, op, index):
	"""Apply `op` to one employee's `rows` in place, or return why not."""
	action = op.get("action")
	if action not in ACTIONS:
		return BAD_ACTION
	if action != END and not op.get("shift_type"):
		return NO_SHIFT_TYPE
	day = op_date(op)
	if not day:
		return BAD_DATE

	if action == ASSIGN:
		end = _date(op.get("to_date"))
		if op.get("to_date") and not end:
			return BAD_DATE
		if end and end < day:
			return END_BEFORE_START
		if any(_overlaps(a, day, end) for a in rows):
			return OVERLAP
		rows.append(
			{"key": f"new-{index}", "shift_type": op["shift_type"], "start_date": day, "end_date": end}
		)
		return None

	target = _target(rows, op.get("assignment"), day)
	if not target:
		return NO_SHIFT_ON_DATE
	if action == CHANGE:
		if day <= target["start_date"]:
			return CHANGE_ON_START
		if target["end_date"] and day > target["end_date"]:
			return AFTER_END
		old_end = target["end_date"]
		target["end_date"] = day - timedelta(days=1)
		rows.append(
			{"key": f"new-{index}", "shift_type": op["shift_type"], "start_date": day, "end_date": old_end}
		)
		return None
	if day < target["start_date"]:
		return END_BEFORE_START
	if target["end_date"] and day > target["end_date"]:
		return AFTER_END
	target["end_date"] = day
	return None


def simulate(coverage, ops):
	"""Apply `ops` in order to a copy of `coverage`. Returns
	`(new_coverage, problems)`; each problem is `{index, employee, message}`
	with an untranslated plain sentence (callers wrap it in `_()`). A
	refused operation changes nothing and the rest still run."""
	result = {
		employee: [
			{
				"key": a["key"],
				"shift_type": a["shift_type"],
				"start_date": _date(a["start_date"]),
				"end_date": _date(a["end_date"]),
			}
			for a in rows
		]
		for employee, rows in (coverage or {}).items()
	}
	problems = []
	for index, op in enumerate(ops):
		employee = op.get("employee")
		message = _apply(result.setdefault(employee, []), op, index)
		if message:
			problems.append({"index": index, "employee": employee, "message": message})
	return result, problems


def affected_window(ops):
	"""`{employee: (start, end)}`: the earliest operation date through the
	latest date any operation writes, `end` None when one may leave an open
	end. A Change counts as open: its new half keeps the old assignment's
	end, which the operation alone does not carry. Wider is safe -- a false
	conflict costs a resubmit, a missed one applies over HR's edit."""
	windows = {}
	for op in ops:
		day = op_date(op)
		if not day:
			continue
		if op.get("action") == ASSIGN:
			last = _date(op.get("to_date"))
		elif op.get("action") == CHANGE:
			last = None
		else:
			last = day
		employee = op.get("employee")
		if employee not in windows:
			windows[employee] = (day, last)
			continue
		start, end = windows[employee]
		windows[employee] = (min(start, day), None if end is None or last is None else max(end, last))
	return windows


def snapshot_key(assignments, window):
	"""KTD8's conflict token for one employee: `[shift_type, start, end]`
	(ISO strings, end None when open) of every assignment overlapping
	`window`, sorted. Names, `status` and `modified` are left out -- HRMS's
	nightly job rewrites `status` and an HR change mints new names. Lists,
	not tuples, so a JSON round trip compares equal."""
	start, end = window
	rows = [
		[a["shift_type"], _date(a["start_date"]), _date(a["end_date"])]
		for a in assignments
		if _overlaps({"start_date": _date(a["start_date"]), "end_date": _date(a["end_date"])}, start, end)
	]
	rows.sort(key=lambda row: (row[1], row[2] or date.max, row[0]))
	return [[shift, first.isoformat(), last.isoformat() if last else None] for shift, first, last in rows]


def read_coverage(employees, window_start):
	"""Live coverage for `employees`: every submitted (docstatus 1) Shift
	Assignment still running on or after `window_start`, whatever its
	`status` (the roster's own read, runbook "Roster"). Unpermissioned:
	callers have already scoped `employees`."""
	employees = list(employees or [])
	coverage = {employee: [] for employee in employees}
	if not employees:
		return coverage
	rows = frappe.get_all(
		"Shift Assignment",
		filters={"employee": ["in", employees], "docstatus": 1},
		or_filters=[["end_date", "is", "not set"], ["end_date", ">=", str(getdate(window_start))]],
		fields=["name", "employee", "shift_type", "start_date", "end_date"],
		order_by="start_date asc, name asc",
		ignore_permissions=True,
	)
	for row in rows:
		coverage[row.employee].append(
			{
				"key": row.name,
				"shift_type": row.shift_type,
				"start_date": getdate(row.start_date),
				"end_date": getdate(row.end_date) if row.end_date else None,
			}
		)
	return coverage
