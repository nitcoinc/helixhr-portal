# Copyright (c) 2026, HelixHR Contributors
# For license information, please see license.txt

"""Plan 2026-10-31-001 U7 / KTD8, KTD9: the roster request rule engine.

`simulate`, `affected_window`, `op_date` and `snapshot_key` are pure: no
DB, no session. The same rules run in the browser
(`frontend/src/lib/rosterDraft.js`, the draft preview) and on the server
(save, submit, approve). `read_coverage` is the one DB reader, kept apart.

Coverage is `{employee: [assignment]}`; an assignment is a dict with
`key` (the Shift Assignment name, or `new-<op index>-<n>` for the blocks
an operation creates), `shift_type`, `start_date` and `end_date` (None = open-ended).

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
from itertools import count

import frappe
from frappe.utils import getdate

ASSIGN = "Assign"
CHANGE = "Change"
END = "End"
ACTIONS = (ASSIGN, CHANGE, END)

# The HR endpoints' sentences, single source: `helixhr.api` imports them.
OVERLAP = "This person already has a shift on some of those dates. End or change that one first."
END_BEFORE_START = "The end date can't be before the shift starts."
CHANGE_ON_START = "That is the day this shift starts. Cancel it and assign the new shift instead."
AFTER_END = "That date is after this shift ends."
NO_SHIFT_TYPE = "Choose a shift."
BAD_DATE = "Give a valid date."
# New to roster requests: an HR cell always names its assignment.
NO_SHIFT_ON_DATE = "This person has no shift on that date to change or end."
BAD_ACTION = "Choose assign, change or end."
# Plan 2026-11-03-001 U1: skipping a person's weekly offs and holidays.
SKIP_MAX_DAYS = 92
NO_WORKING_DAYS = "No working days in that range."
OFF_DAYS_UNKNOWN = (
	"Their weekly offs and holidays can't be worked out for those dates. "
	"Turn off skipping weekly offs and holidays, or ask HR to set up their holiday list."
)
SKIP_SPAN = "Skipping weekly offs and holidays works on at most 92 days at a time."


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
	"""The assignment `key` names while it is still in `rows`, else the first
	one covering `day` -- as `api._roster_op_target` does at apply time, so a
	key HR's edit has since replaced falls back rather than refusing."""
	named = next((a for a in rows if a["key"] == key), None) if key else None
	return named or next((a for a in rows if _overlaps(a, day, day)), None)


def _off_days(value):
	"""`{from, to, dates}` with dates parsed, or None (cannot tell). A day
	outside `from`..`to` is unknown too."""
	if not value:
		return None
	start, end = _date(value.get("from")), _date(value.get("to"))
	if not start or not end:
		return None
	return {"from": start, "to": end, "dates": {day for day in map(_date, value.get("dates") or ()) if day}}


def _is_off(off, day):
	"""True / False, or None when `off` cannot tell for `day`."""
	if off is None or not off["from"] <= day <= off["to"]:
		return None
	return day in off["dates"]


def _flag(value):
	return value in (1, True, "1")


def _next_in_run(rows, block, off):
	"""The block right after `block` in its run (KTD3): the next one by date,
	same shift type, with only known off days between. None at a break."""
	if off is None or block["end_date"] is None:
		return None
	later = [a for a in rows if a["start_date"] > block["end_date"]]
	if not later:
		return None
	following = min(later, key=lambda a: a["start_date"])
	if following["shift_type"] != block["shift_type"]:
		return None
	day = block["end_date"] + timedelta(days=1)
	while day < following["start_date"]:
		if _is_off(off, day) is not True:
			return None
		day += timedelta(days=1)
	return following


def _has_previous_in_run(rows, block, off):
	return any(_next_in_run(rows, a, off) is block for a in rows if a is not block)


def _resolve(rows, key, day, off):
	"""Run-aware target: `(block, via_gap)`. A date on an off-day gap inside
	a run acts on the block after the gap."""
	named = next((a for a in rows if a["key"] == key), None) if key else None
	if named and _overlaps(named, day, day):
		return named, False
	covering = next((a for a in rows if _overlaps(a, day, day)), None)
	if covering:
		return covering, False
	for block in rows:
		if block["end_date"] is not None and block["end_date"] < day:
			following = _next_in_run(rows, block, off)
			if following and following["start_date"] > day:
				return following, True
	return named, False


def _working_spans(day, end, off):
	"""The contiguous runs of working days in `day`..`end`."""
	spans, start = [], None
	while day <= end:
		if day in off["dates"]:
			if start:
				spans.append((start, day - timedelta(days=1)))
				start = None
		elif not start:
			start = day
		day += timedelta(days=1)
	if start:
		spans.append((start, end))
	return spans


def _step(action, employee, block, end_date=None):
	end = end_date if action == "end" else block["end_date"]
	return {
		"action": action,
		"employee": employee,
		"key": block["key"],
		"shift_type": block["shift_type"],
		"start_date": block["start_date"].isoformat(),
		"end_date": end.isoformat() if end else None,
	}


def _apply(rows, op, index, off, run_aware):
	"""Apply `op` to one employee's `rows` in place. Returns `(message,
	steps)`: why not (rows untouched), or the primitive steps it took."""
	action = op.get("action")
	employee = op.get("employee")
	if action not in ACTIONS:
		return BAD_ACTION, []
	if action != END and not op.get("shift_type"):
		return NO_SHIFT_TYPE, []
	day = op_date(op)
	if not day:
		return BAD_DATE, []
	counter = count()

	def new_block(shift_type, start, end):
		block = {
			"key": f"new-{index}-{next(counter)}",
			"shift_type": shift_type,
			"start_date": start,
			"end_date": end,
		}
		rows.append(block)
		return _step("create", employee, block)

	if action == ASSIGN:
		end = _date(op.get("to_date"))
		if op.get("to_date") and not end:
			return BAD_DATE, []
		if end and end < day:
			return END_BEFORE_START, []
		spans = [(day, end)]
		if run_aware and end and _flag(op.get("skip_off_days")):
			if (end - day).days + 1 > SKIP_MAX_DAYS:
				return SKIP_SPAN, []
			if off is None or not (off["from"] <= day and end <= off["to"]):
				return OFF_DAYS_UNKNOWN, []
			spans = _working_spans(day, end, off)
			if not spans:
				return NO_WORKING_DAYS, []
		if any(_overlaps(a, start, last) for a in rows for start, last in spans):
			return OVERLAP, []
		return None, [new_block(op["shift_type"], start, last) for start, last in spans]

	if off is None:
		target, via_gap = _target(rows, op.get("assignment"), day), False
	else:
		target, via_gap = _resolve(rows, op.get("assignment"), day, off)
	if not target:
		return NO_SHIFT_ON_DATE, []
	later = []
	following = _next_in_run(rows, target, off)
	while following:
		later.append(following)
		following = _next_in_run(rows, following, off)

	steps = []
	if action == CHANGE:
		recreate, old_end = list(later), None
		if via_gap or (day == target["start_date"] and _has_previous_in_run(rows, target, off)):
			# A4: the whole block moves to the new shift.
			recreate.insert(0, target)
		elif day <= target["start_date"]:
			return CHANGE_ON_START, []
		elif target["end_date"] and day > target["end_date"]:
			return AFTER_END, []
		else:
			old_end = target["end_date"]
			target["end_date"] = day - timedelta(days=1)
			steps.append(_step("end", employee, target, target["end_date"]))
		for block in recreate:
			rows.remove(block)
			steps.append(_step("cancel", employee, block))
		if target not in recreate:
			steps.append(new_block(op["shift_type"], day, old_end))
		for block in recreate:
			steps.append(new_block(op["shift_type"], block["start_date"], block["end_date"]))
		return None, steps

	if via_gap:
		later.insert(0, target)
	else:
		if day < target["start_date"]:
			return END_BEFORE_START, []
		if target["end_date"] and day > target["end_date"]:
			return AFTER_END, []
		target["end_date"] = day
		steps.append(_step("end", employee, target, day))
	for block in later:
		rows.remove(block)
		steps.append(_step("cancel", employee, block))
	return None, steps


def simulate(coverage, ops, off_days=None):
	"""Apply `ops` in order to a copy of `coverage`. Returns
	`(new_coverage, problems, steps)`; each problem is `{index, employee,
	message}` with an untranslated plain sentence (callers wrap it in `_()`),
	and `steps[index]` is the ordered create / end / cancel steps operation
	`index` took (empty when refused) -- what approval executes (plan
	2026-11-03-001 KTD2). A refused operation changes nothing and the rest
	still run.

	`off_days` is `{employee: {from, to, dates} | None}` (plan 2026-11-03-001
	KTD3, KTD4): it turns on skip expansion and run-aware Change / End. A
	missing or None entry, or a day outside `from`..`to`, is "cannot tell":
	a skip is refused and a run stops there. `off_days=None` is the legacy
	mode a request sent before the skip feature keeps (A1): skip ignored,
	Change / End on the one covering block."""
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
	run_aware = off_days is not None
	offs = {}
	problems, steps = [], []
	for index, op in enumerate(ops):
		employee = op.get("employee")
		if employee not in offs:
			offs[employee] = _off_days((off_days or {}).get(employee)) if run_aware else None
		message, taken = _apply(result.setdefault(employee, []), op, index, offs[employee], run_aware)
		steps.append(taken)
		if message:
			problems.append({"index": index, "employee": employee, "message": message})
	return result, problems, steps


def affected_window(ops, steps=None):
	"""`{employee: (start, end)}`: the earliest operation date through the
	latest date any operation writes, `end` None when one may leave an open
	end. A Change counts as open: its new half keeps the old assignment's
	end, which the operation alone does not carry. `steps` (from `simulate`)
	widen `end` to the last block a run-aware Change or End reaches (plan
	2026-11-03-001 U1). Wider is safe -- a false conflict costs a resubmit, a
	missed one applies over HR's edit."""
	windows = {}

	def widen(employee, day, last):
		if employee not in windows:
			windows[employee] = (day, last)
			return
		start, end = windows[employee]
		windows[employee] = (min(start, day), None if end is None or last is None else max(end, last))

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
		widen(op.get("employee"), day, last)
	for op_steps in steps or ():
		for step in op_steps:
			if step["employee"] in windows:
				start = windows[step["employee"]][0]
				widen(step["employee"], start, _date(step["end_date"]))
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


def read_coverage(employees, window_start, for_update=False):
	"""Live coverage for `employees`: every submitted (docstatus 1) Shift
	Assignment still running on or after `window_start`, whatever its
	`status` (the roster's own read, runbook "Roster"). Unpermissioned:
	callers have already scoped `employees`. `for_update` makes it a locking
	read of the latest committed rows, not the transaction's older snapshot
	-- approval's re-check, run under the Employee locks."""
	employees = list(employees or [])
	coverage = {employee: [] for employee in employees}
	if not employees:
		return coverage
	# `frappe.qb.get_query`, not `get_all`: only the former takes `for_update`.
	rows = frappe.qb.get_query(
		"Shift Assignment",
		filters={"employee": ["in", employees], "docstatus": 1},
		or_filters=[["end_date", "is", "not set"], ["end_date", ">=", str(getdate(window_start))]],
		fields=["name", "employee", "shift_type", "start_date", "end_date"],
		order_by="start_date asc, name asc",
		for_update=for_update,
	).run(as_dict=True)
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
