"""Plan 2026-10-31-001 U7 / KTD8, KTD9: the roster request simulator.

Pure: no DB rows. `CASES` is duplicated literally in
`frontend/src/lib/rosterDraft.test.js` so the browser preview and the
server agree on every rule and sentence -- change one, change both.
"""

from datetime import date

from frappe.tests import UnitTestCase

from helixhr.roster_ops import (
	AFTER_END,
	CHANGE_ON_START,
	END_BEFORE_START,
	NO_SHIFT_ON_DATE,
	NO_SHIFT_TYPE,
	NO_WORKING_DAYS,
	OFF_DAYS_UNKNOWN,
	OVERLAP,
	RUN_OFF_DAYS_UNKNOWN,
	RUN_SPLIT,
	RUN_STOPPED_UNKNOWN,
	SAME_SHIFT,
	SKIP_SPAN,
	WEEKLY_OFFS_NOT_SET,
	affected_window,
	op_date,
	simulate,
	snapshot_key,
)

# --- Shared literal case table (keep identical to rosterDraft.test.js) -----
# coverage / expect: {employee: [[key, shift_type, start_date, end_date]]}
# problems: [[op index, message]]
# off_days (optional, default {} = every day unknown): {employee: {from, to, dates} | None}
# notes (optional, default []): [[op index, message]] -- non-blocking
# steps (optional): per op, [[action, key, shift_type, start_date, end_date]]
WEEKENDS = [
	"2026-11-01",
	"2026-11-07",
	"2026-11-08",
	"2026-11-14",
	"2026-11-15",
	"2026-11-21",
	"2026-11-22",
	"2026-11-28",
	"2026-11-29",
	"2026-12-05",
	"2026-12-06",
	"2026-12-12",
	"2026-12-13",
	"2026-12-19",
	"2026-12-20",
	"2026-12-26",
	"2026-12-27",
]
CASES = [
	{
		"name": "assign on an empty range succeeds",
		"coverage": {},
		"ops": [
			{
				"employee": "E1",
				"action": "Assign",
				"shift_type": "Day",
				"from_date": "2026-11-02",
				"to_date": "2026-11-08",
			}
		],
		"expect": {"E1": [["new-0-0", "Day", "2026-11-02", "2026-11-08"]]},
		"problems": [],
	},
	{
		"name": "assign overlapping existing coverage is refused",
		"coverage": {"E1": [["SA-1", "Day", "2026-11-01", None]]},
		"ops": [
			{
				"employee": "E1",
				"action": "Assign",
				"shift_type": "Night",
				"from_date": "2026-11-05",
				"to_date": "2026-11-06",
			}
		],
		"expect": {"E1": [["SA-1", "Day", "2026-11-01", None]]},
		"problems": [[0, OVERLAP]],
	},
	{
		"name": "change inside an assignment splits it and keeps the old end",
		"coverage": {"E1": [["SA-1", "Day", "2026-11-01", "2026-11-30"]]},
		"ops": [
			{
				"employee": "E1",
				"action": "Change",
				"shift_type": "Night",
				"from_date": "2026-11-10",
				"assignment": "SA-1",
			}
		],
		"expect": {
			"E1": [
				["SA-1", "Day", "2026-11-01", "2026-11-09"],
				["new-0-0", "Night", "2026-11-10", "2026-11-30"],
			]
		},
		"problems": [],
	},
	{
		"name": "change on the assignment's first day is refused",
		"coverage": {"E1": [["SA-1", "Day", "2026-11-01", "2026-11-30"]]},
		"ops": [
			{
				"employee": "E1",
				"action": "Change",
				"shift_type": "Night",
				"from_date": "2026-11-01",
				"assignment": "SA-1",
			}
		],
		"expect": {"E1": [["SA-1", "Day", "2026-11-01", "2026-11-30"]]},
		"problems": [[0, CHANGE_ON_START]],
	},
	{
		"name": "change after the assignment ends is refused",
		"coverage": {"E1": [["SA-1", "Day", "2026-11-01", "2026-11-10"]]},
		"ops": [
			{
				"employee": "E1",
				"action": "Change",
				"shift_type": "Night",
				"from_date": "2026-11-15",
				"assignment": "SA-1",
			}
		],
		"expect": {"E1": [["SA-1", "Day", "2026-11-01", "2026-11-10"]]},
		"problems": [[0, AFTER_END]],
	},
	{
		"name": "end before the start is refused",
		"coverage": {"E1": [["SA-1", "Day", "2026-11-05", None]]},
		"ops": [{"employee": "E1", "action": "End", "to_date": "2026-11-03", "assignment": "SA-1"}],
		"expect": {"E1": [["SA-1", "Day", "2026-11-05", None]]},
		"problems": [[0, END_BEFORE_START]],
	},
	{
		"name": "end after the current end is refused",
		"coverage": {"E1": [["SA-1", "Day", "2026-11-01", "2026-11-10"]]},
		"ops": [{"employee": "E1", "action": "End", "to_date": "2026-11-15", "assignment": "SA-1"}],
		"expect": {"E1": [["SA-1", "Day", "2026-11-01", "2026-11-10"]]},
		"problems": [[0, AFTER_END]],
	},
	{
		"name": "assign a range then change inside it combines",
		"coverage": {},
		"ops": [
			{
				"employee": "E1",
				"action": "Assign",
				"shift_type": "Day",
				"from_date": "2026-11-02",
				"to_date": "2026-11-20",
			},
			{"employee": "E1", "action": "Change", "shift_type": "Night", "from_date": "2026-11-10"},
		],
		"expect": {
			"E1": [
				["new-0-0", "Day", "2026-11-02", "2026-11-09"],
				["new-1-0", "Night", "2026-11-10", "2026-11-20"],
			]
		},
		"problems": [],
	},
	{
		"name": "an open-ended assignment followed by an end is bounded",
		"coverage": {},
		"ops": [
			{"employee": "E1", "action": "Assign", "shift_type": "Day", "from_date": "2026-11-02"},
			{"employee": "E1", "action": "End", "to_date": "2026-11-30"},
		],
		"expect": {"E1": [["new-0-0", "Day", "2026-11-02", "2026-11-30"]]},
		"problems": [],
	},
	{
		"name": "change on an open-ended assignment stays open-ended",
		"coverage": {"E1": [["SA-1", "Day", "2026-11-01", None]]},
		"ops": [
			{
				"employee": "E1",
				"action": "Change",
				"shift_type": "Night",
				"from_date": "2026-11-10",
				"assignment": "SA-1",
			}
		],
		"expect": {
			"E1": [["SA-1", "Day", "2026-11-01", "2026-11-09"], ["new-0-0", "Night", "2026-11-10", None]]
		},
		"problems": [],
	},
	{
		"name": "change with no shift on that date is refused",
		"coverage": {},
		"ops": [{"employee": "E1", "action": "Change", "shift_type": "Night", "from_date": "2026-11-10"}],
		"expect": {},
		"problems": [[0, NO_SHIFT_ON_DATE]],
	},
	{
		"name": "a change whose assignment is gone falls back to the one covering the date",
		"coverage": {"E1": [["SA-2", "Day", "2026-11-01", "2026-11-30"]]},
		"ops": [
			{
				"employee": "E1",
				"action": "Change",
				"shift_type": "Night",
				"from_date": "2026-11-10",
				"assignment": "SA-1",
			}
		],
		"expect": {
			"E1": [
				["SA-2", "Day", "2026-11-01", "2026-11-09"],
				["new-0-0", "Night", "2026-11-10", "2026-11-30"],
			]
		},
		"problems": [],
	},
	{
		"name": "assign without a shift is refused",
		"coverage": {},
		"ops": [{"employee": "E1", "action": "Assign", "from_date": "2026-11-02"}],
		"expect": {},
		"problems": [[0, NO_SHIFT_TYPE]],
	},
	{
		"name": "a refused operation is skipped and later ones still apply",
		"coverage": {"E1": [["SA-1", "Day", "2026-11-01", None]]},
		"ops": [
			{"employee": "E1", "action": "Assign", "shift_type": "Night", "from_date": "2026-11-05"},
			{
				"employee": "E2",
				"action": "Assign",
				"shift_type": "Night",
				"from_date": "2026-11-05",
				"to_date": "2026-11-05",
			},
		],
		"expect": {
			"E1": [["SA-1", "Day", "2026-11-01", None]],
			"E2": [["new-1-0", "Night", "2026-11-05", "2026-11-05"]],
		},
		"problems": [[0, OVERLAP]],
	},
	{
		"name": "skip: a four-week assign with weekends off becomes four blocks",
		"coverage": {},
		"off_days": {"E1": {"from": "2026-11-01", "to": "2026-12-31", "dates": WEEKENDS}},
		"ops": [
			{
				"employee": "E1",
				"action": "Assign",
				"shift_type": "Day",
				"from_date": "2026-11-02",
				"to_date": "2026-11-27",
				"skip_off_days": 1,
			}
		],
		"expect": {
			"E1": [
				["new-0-0", "Day", "2026-11-02", "2026-11-06"],
				["new-0-1", "Day", "2026-11-09", "2026-11-13"],
				["new-0-2", "Day", "2026-11-16", "2026-11-20"],
				["new-0-3", "Day", "2026-11-23", "2026-11-27"],
			]
		},
		"problems": [],
		"steps": [
			[
				["create", "new-0-0", "Day", "2026-11-02", "2026-11-06"],
				["create", "new-0-1", "Day", "2026-11-09", "2026-11-13"],
				["create", "new-0-2", "Day", "2026-11-16", "2026-11-20"],
				["create", "new-0-3", "Day", "2026-11-23", "2026-11-27"],
			]
		],
	},
	{
		"name": "skip: a holiday splits the block around it",
		"coverage": {},
		"off_days": {"E1": {"from": "2026-11-01", "to": "2026-12-31", "dates": [*WEEKENDS, "2026-11-12"]}},
		"ops": [
			{
				"employee": "E1",
				"action": "Assign",
				"shift_type": "Day",
				"from_date": "2026-11-02",
				"to_date": "2026-11-27",
				"skip_off_days": 1,
			}
		],
		"expect": {
			"E1": [
				["new-0-0", "Day", "2026-11-02", "2026-11-06"],
				["new-0-1", "Day", "2026-11-09", "2026-11-11"],
				["new-0-2", "Day", "2026-11-13", "2026-11-13"],
				["new-0-3", "Day", "2026-11-16", "2026-11-20"],
				["new-0-4", "Day", "2026-11-23", "2026-11-27"],
			]
		},
		"problems": [],
	},
	{
		"name": "skip: a range starting and ending on off days is trimmed",
		"coverage": {},
		"off_days": {"E1": {"from": "2026-11-01", "to": "2026-12-31", "dates": WEEKENDS}},
		"ops": [
			{
				"employee": "E1",
				"action": "Assign",
				"shift_type": "Day",
				"from_date": "2026-11-01",
				"to_date": "2026-11-08",
				"skip_off_days": 1,
			}
		],
		"expect": {"E1": [["new-0-0", "Day", "2026-11-02", "2026-11-06"]]},
		"problems": [],
	},
	{
		"name": "skip: only off days is refused",
		"coverage": {},
		"off_days": {"E1": {"from": "2026-11-01", "to": "2026-12-31", "dates": WEEKENDS}},
		"ops": [
			{
				"employee": "E1",
				"action": "Assign",
				"shift_type": "Day",
				"from_date": "2026-11-07",
				"to_date": "2026-11-08",
				"skip_off_days": 1,
			}
		],
		"expect": {},
		"problems": [[0, NO_WORKING_DAYS]],
	},
	{
		"name": "skip: unknown off days is refused, without skip one block",
		"coverage": {},
		"off_days": {"E1": None},
		"ops": [
			{
				"employee": "E1",
				"action": "Assign",
				"shift_type": "Day",
				"from_date": "2026-11-02",
				"to_date": "2026-11-27",
				"skip_off_days": 1,
			},
			{
				"employee": "E1",
				"action": "Assign",
				"shift_type": "Day",
				"from_date": "2026-11-02",
				"to_date": "2026-11-27",
			},
		],
		"expect": {"E1": [["new-1-0", "Day", "2026-11-02", "2026-11-27"]]},
		"problems": [[0, OFF_DAYS_UNKNOWN]],
	},
	{
		"name": "skip: a holiday list ending inside the range is unknown",
		"coverage": {},
		"off_days": {"E1": {"from": "2026-11-01", "to": "2026-11-20", "dates": WEEKENDS}},
		"ops": [
			{
				"employee": "E1",
				"action": "Assign",
				"shift_type": "Day",
				"from_date": "2026-11-02",
				"to_date": "2026-11-27",
				"skip_off_days": 1,
			}
		],
		"expect": {},
		"problems": [[0, OFF_DAYS_UNKNOWN]],
	},
	{
		"name": "skip: more than 92 days is refused",
		"coverage": {},
		"off_days": {"E1": {"from": "2026-11-01", "to": "2027-12-31", "dates": WEEKENDS}},
		"ops": [
			{
				"employee": "E1",
				"action": "Assign",
				"shift_type": "Day",
				"from_date": "2026-11-02",
				"to_date": "2027-02-02",
				"skip_off_days": 1,
			}
		],
		"expect": {},
		"problems": [[0, SKIP_SPAN]],
	},
	{
		"name": "skip is ignored on an open-ended assign, a change and an end",
		"coverage": {"E2": [["SA-9", "Day", "2026-11-02", "2026-11-27"]]},
		"off_days": {
			"E1": {"from": "2026-11-01", "to": "2026-12-31", "dates": WEEKENDS},
			"E2": {"from": "2026-11-01", "to": "2026-12-31", "dates": WEEKENDS},
		},
		"ops": [
			{
				"employee": "E1",
				"action": "Assign",
				"shift_type": "Day",
				"from_date": "2026-11-02",
				"skip_off_days": 1,
			},
			{
				"employee": "E2",
				"action": "Change",
				"shift_type": "Night",
				"from_date": "2026-11-10",
				"skip_off_days": 1,
			},
			{"employee": "E2", "action": "End", "to_date": "2026-11-20", "skip_off_days": 1},
		],
		"expect": {
			"E1": [["new-0-0", "Day", "2026-11-02", None]],
			"E2": [
				["SA-9", "Day", "2026-11-02", "2026-11-09"],
				["new-1-0", "Night", "2026-11-10", "2026-11-20"],
			],
		},
		"problems": [],
	},
	{
		"name": "run: change from mid-block splits it and swaps every later block",
		"coverage": {
			"E1": [
				["SA-1", "Day", "2026-11-02", "2026-11-06"],
				["SA-2", "Day", "2026-11-09", "2026-11-13"],
				["SA-3", "Day", "2026-11-16", "2026-11-20"],
				["SA-4", "Day", "2026-11-23", "2026-11-27"],
			]
		},
		"off_days": {"E1": {"from": "2026-11-01", "to": "2026-12-31", "dates": WEEKENDS}},
		"ops": [
			{
				"employee": "E1",
				"action": "Change",
				"shift_type": "Night",
				"from_date": "2026-11-18",
				"assignment": "SA-3",
			}
		],
		"expect": {
			"E1": [
				["SA-1", "Day", "2026-11-02", "2026-11-06"],
				["SA-2", "Day", "2026-11-09", "2026-11-13"],
				["SA-3", "Day", "2026-11-16", "2026-11-17"],
				["new-0-0", "Night", "2026-11-18", "2026-11-20"],
				["new-0-1", "Night", "2026-11-23", "2026-11-27"],
			]
		},
		"problems": [],
		"steps": [
			[
				["end", "SA-3", "Day", "2026-11-16", "2026-11-17"],
				["cancel", "SA-4", "Day", "2026-11-23", "2026-11-27"],
				["create", "new-0-0", "Night", "2026-11-18", "2026-11-20"],
				["create", "new-0-1", "Night", "2026-11-23", "2026-11-27"],
			]
		],
	},
	{
		"name": "run: change dated on an off-day gap acts on the next block on",
		"coverage": {
			"E1": [
				["SA-1", "Day", "2026-11-02", "2026-11-06"],
				["SA-2", "Day", "2026-11-09", "2026-11-13"],
				["SA-3", "Day", "2026-11-16", "2026-11-20"],
				["SA-4", "Day", "2026-11-23", "2026-11-27"],
			]
		},
		"off_days": {"E1": {"from": "2026-11-01", "to": "2026-12-31", "dates": WEEKENDS}},
		"ops": [{"employee": "E1", "action": "Change", "shift_type": "Night", "from_date": "2026-11-22"}],
		"expect": {
			"E1": [
				["SA-1", "Day", "2026-11-02", "2026-11-06"],
				["SA-2", "Day", "2026-11-09", "2026-11-13"],
				["SA-3", "Day", "2026-11-16", "2026-11-20"],
				["new-0-0", "Night", "2026-11-23", "2026-11-27"],
			]
		},
		"problems": [],
		"steps": [
			[
				["cancel", "SA-4", "Day", "2026-11-23", "2026-11-27"],
				["create", "new-0-0", "Night", "2026-11-23", "2026-11-27"],
			]
		],
	},
	{
		"name": "run: change on a later block's first day recreates it",
		"coverage": {
			"E1": [
				["SA-1", "Day", "2026-11-02", "2026-11-06"],
				["SA-2", "Day", "2026-11-09", "2026-11-13"],
				["SA-3", "Day", "2026-11-16", "2026-11-20"],
				["SA-4", "Day", "2026-11-23", "2026-11-27"],
			]
		},
		"off_days": {"E1": {"from": "2026-11-01", "to": "2026-12-31", "dates": WEEKENDS}},
		"ops": [
			{
				"employee": "E1",
				"action": "Change",
				"shift_type": "Night",
				"from_date": "2026-11-16",
				"assignment": "SA-3",
			}
		],
		"expect": {
			"E1": [
				["SA-1", "Day", "2026-11-02", "2026-11-06"],
				["SA-2", "Day", "2026-11-09", "2026-11-13"],
				["new-0-0", "Night", "2026-11-16", "2026-11-20"],
				["new-0-1", "Night", "2026-11-23", "2026-11-27"],
			]
		},
		"problems": [],
	},
	{
		"name": "run: change on the run's first day is still refused",
		"coverage": {
			"E1": [
				["SA-1", "Day", "2026-11-02", "2026-11-06"],
				["SA-2", "Day", "2026-11-09", "2026-11-13"],
				["SA-3", "Day", "2026-11-16", "2026-11-20"],
				["SA-4", "Day", "2026-11-23", "2026-11-27"],
			]
		},
		"off_days": {"E1": {"from": "2026-11-01", "to": "2026-12-31", "dates": WEEKENDS}},
		"ops": [
			{
				"employee": "E1",
				"action": "Change",
				"shift_type": "Night",
				"from_date": "2026-11-02",
				"assignment": "SA-1",
			}
		],
		"expect": {
			"E1": [
				["SA-1", "Day", "2026-11-02", "2026-11-06"],
				["SA-2", "Day", "2026-11-09", "2026-11-13"],
				["SA-3", "Day", "2026-11-16", "2026-11-20"],
				["SA-4", "Day", "2026-11-23", "2026-11-27"],
			]
		},
		"problems": [[0, CHANGE_ON_START]],
	},
	{
		"name": "run: end mid-block ends it and cancels every later block",
		"coverage": {
			"E1": [
				["SA-1", "Day", "2026-11-02", "2026-11-06"],
				["SA-2", "Day", "2026-11-09", "2026-11-13"],
				["SA-3", "Day", "2026-11-16", "2026-11-20"],
				["SA-4", "Day", "2026-11-23", "2026-11-27"],
			]
		},
		"off_days": {"E1": {"from": "2026-11-01", "to": "2026-12-31", "dates": WEEKENDS}},
		"ops": [{"employee": "E1", "action": "End", "to_date": "2026-11-18", "assignment": "SA-3"}],
		"expect": {
			"E1": [
				["SA-1", "Day", "2026-11-02", "2026-11-06"],
				["SA-2", "Day", "2026-11-09", "2026-11-13"],
				["SA-3", "Day", "2026-11-16", "2026-11-18"],
			]
		},
		"problems": [],
		"steps": [
			[
				["end", "SA-3", "Day", "2026-11-16", "2026-11-18"],
				["cancel", "SA-4", "Day", "2026-11-23", "2026-11-27"],
			]
		],
	},
	{
		"name": "run: end dated on a gap cancels the later blocks",
		"coverage": {
			"E1": [
				["SA-1", "Day", "2026-11-02", "2026-11-06"],
				["SA-2", "Day", "2026-11-09", "2026-11-13"],
				["SA-3", "Day", "2026-11-16", "2026-11-20"],
				["SA-4", "Day", "2026-11-23", "2026-11-27"],
			]
		},
		"off_days": {"E1": {"from": "2026-11-01", "to": "2026-12-31", "dates": WEEKENDS}},
		"ops": [{"employee": "E1", "action": "End", "to_date": "2026-11-22"}],
		"expect": {
			"E1": [
				["SA-1", "Day", "2026-11-02", "2026-11-06"],
				["SA-2", "Day", "2026-11-09", "2026-11-13"],
				["SA-3", "Day", "2026-11-16", "2026-11-20"],
			]
		},
		"problems": [],
		"steps": [[["cancel", "SA-4", "Day", "2026-11-23", "2026-11-27"]]],
	},
	{
		"name": "run: another shift type breaks the run",
		"coverage": {
			"E1": [
				["SA-1", "Day", "2026-11-02", "2026-11-06"],
				["SA-2", "Day", "2026-11-09", "2026-11-13"],
				["SA-3", "Late", "2026-11-16", "2026-11-20"],
				["SA-4", "Day", "2026-11-23", "2026-11-27"],
			]
		},
		"off_days": {"E1": {"from": "2026-11-01", "to": "2026-12-31", "dates": WEEKENDS}},
		"ops": [
			{
				"employee": "E1",
				"action": "Change",
				"shift_type": "Night",
				"from_date": "2026-11-11",
				"assignment": "SA-2",
			}
		],
		"expect": {
			"E1": [
				["SA-1", "Day", "2026-11-02", "2026-11-06"],
				["SA-2", "Day", "2026-11-09", "2026-11-10"],
				["SA-3", "Late", "2026-11-16", "2026-11-20"],
				["SA-4", "Day", "2026-11-23", "2026-11-27"],
				["new-0-0", "Night", "2026-11-11", "2026-11-13"],
			]
		},
		"problems": [],
	},
	{
		"name": "run: a working-day gap breaks the run",
		"coverage": {
			"E1": [["SA-1", "Day", "2026-11-02", "2026-11-06"], ["SA-3", "Day", "2026-11-16", "2026-11-20"]]
		},
		"off_days": {"E1": {"from": "2026-11-01", "to": "2026-12-31", "dates": WEEKENDS}},
		"ops": [{"employee": "E1", "action": "End", "to_date": "2026-11-04", "assignment": "SA-1"}],
		"expect": {
			"E1": [["SA-1", "Day", "2026-11-02", "2026-11-04"], ["SA-3", "Day", "2026-11-16", "2026-11-20"]]
		},
		"problems": [],
	},
	{
		"name": "run: unknown off days act on the covering block only",
		"coverage": {
			"E1": [
				["SA-1", "Day", "2026-11-02", "2026-11-06"],
				["SA-2", "Day", "2026-11-09", "2026-11-13"],
				["SA-3", "Day", "2026-11-16", "2026-11-20"],
				["SA-4", "Day", "2026-11-23", "2026-11-27"],
			]
		},
		"off_days": {"E1": None},
		"ops": [{"employee": "E1", "action": "End", "to_date": "2026-11-18", "assignment": "SA-3"}],
		"expect": {
			"E1": [
				["SA-1", "Day", "2026-11-02", "2026-11-06"],
				["SA-2", "Day", "2026-11-09", "2026-11-13"],
				["SA-3", "Day", "2026-11-16", "2026-11-18"],
				["SA-4", "Day", "2026-11-23", "2026-11-27"],
			]
		},
		"problems": [],
		"notes": [[0, RUN_OFF_DAYS_UNKNOWN]],
	},
	{
		"name": "run: a gap day outside the known range breaks the run",
		"coverage": {
			"E1": [
				["SA-1", "Day", "2026-11-02", "2026-11-06"],
				["SA-2", "Day", "2026-11-09", "2026-11-13"],
				["SA-3", "Day", "2026-11-16", "2026-11-20"],
				["SA-4", "Day", "2026-11-23", "2026-11-27"],
			]
		},
		"off_days": {"E1": {"from": "2026-11-01", "to": "2026-11-20", "dates": WEEKENDS}},
		"ops": [{"employee": "E1", "action": "End", "to_date": "2026-11-11", "assignment": "SA-2"}],
		"expect": {
			"E1": [
				["SA-1", "Day", "2026-11-02", "2026-11-06"],
				["SA-2", "Day", "2026-11-09", "2026-11-11"],
				["SA-4", "Day", "2026-11-23", "2026-11-27"],
			]
		},
		"problems": [],
		"notes": [[0, RUN_STOPPED_UNKNOWN]],
	},
	{
		"name": "run: a change stops where the off days run out",
		"coverage": {
			"E1": [
				["SA-1", "Day", "2026-11-02", "2026-11-06"],
				["SA-2", "Day", "2026-11-09", "2026-11-13"],
				["SA-3", "Day", "2026-11-16", "2026-11-20"],
			]
		},
		"off_days": {"E1": {"from": "2026-11-01", "to": "2026-11-13", "dates": WEEKENDS}},
		"ops": [
			{
				"employee": "E1",
				"action": "Change",
				"shift_type": "Night",
				"from_date": "2026-11-04",
				"assignment": "SA-1",
			}
		],
		"expect": {
			"E1": [
				["SA-1", "Day", "2026-11-02", "2026-11-03"],
				["SA-3", "Day", "2026-11-16", "2026-11-20"],
				["new-0-0", "Night", "2026-11-04", "2026-11-06"],
				["new-0-1", "Night", "2026-11-09", "2026-11-13"],
			]
		},
		"problems": [],
		"notes": [[0, RUN_STOPPED_UNKNOWN]],
	},
	{
		"name": "run: unknown off days and no later block, no note",
		"coverage": {"E1": [["SA-1", "Day", "2026-11-02", "2026-11-06"]]},
		"off_days": {"E1": None},
		"ops": [{"employee": "E1", "action": "End", "to_date": "2026-11-04", "assignment": "SA-1"}],
		"expect": {"E1": [["SA-1", "Day", "2026-11-02", "2026-11-04"]]},
		"problems": [],
		"notes": [],
	},
	# Plan 2026-11-05-001 A1: a holiday list with no weekly-off rows.
	{
		"name": "skip: a list without weekly offs skips only holidays, with a note",
		"coverage": {},
		"off_days": {
			"E1": {
				"from": "2026-11-01",
				"to": "2026-12-31",
				"dates": ["2026-11-12"],
				"weekly_offs_missing": 1,
			}
		},
		"ops": [
			{
				"employee": "E1",
				"action": "Assign",
				"shift_type": "Day",
				"from_date": "2026-11-02",
				"to_date": "2026-11-15",
				"skip_off_days": 1,
			}
		],
		"expect": {
			"E1": [
				["new-0-0", "Day", "2026-11-02", "2026-11-11"],
				["new-0-1", "Day", "2026-11-13", "2026-11-15"],
			]
		},
		"problems": [],
		"notes": [[0, WEEKLY_OFFS_NOT_SET]],
	},
	{
		"name": "skip off: a list without weekly offs is one block, no note",
		"coverage": {},
		"off_days": {
			"E1": {
				"from": "2026-11-01",
				"to": "2026-12-31",
				"dates": ["2026-11-12"],
				"weekly_offs_missing": 1,
			}
		},
		"ops": [
			{
				"employee": "E1",
				"action": "Assign",
				"shift_type": "Day",
				"from_date": "2026-11-02",
				"to_date": "2026-11-15",
			}
		],
		"expect": {"E1": [["new-0-0", "Day", "2026-11-02", "2026-11-15"]]},
		"problems": [],
		"notes": [],
	},
	{
		"name": "run: without weekly offs a holiday gap joins the run and a weekend gap stops it",
		"coverage": {
			"E1": [
				["SA-1", "Day", "2026-11-02", "2026-11-11"],
				["SA-2", "Day", "2026-11-13", "2026-11-13"],
				["SA-3", "Day", "2026-11-16", "2026-11-20"],
			]
		},
		"off_days": {
			"E1": {
				"from": "2026-11-01",
				"to": "2026-12-31",
				"dates": ["2026-11-12"],
				"weekly_offs_missing": 1,
			}
		},
		"ops": [
			{
				"employee": "E1",
				"action": "Change",
				"shift_type": "Night",
				"from_date": "2026-11-04",
				"assignment": "SA-1",
			}
		],
		"expect": {
			"E1": [
				["SA-1", "Day", "2026-11-02", "2026-11-03"],
				["SA-3", "Day", "2026-11-16", "2026-11-20"],
				["new-0-0", "Night", "2026-11-04", "2026-11-11"],
				["new-0-1", "Night", "2026-11-13", "2026-11-13"],
			]
		},
		"problems": [],
		"notes": [[0, RUN_STOPPED_UNKNOWN]],
	},
	# Plan 2026-11-05-001 U3 / KTD4: one-day Swap Day and Clear Day.
	{
		"name": "swap day: inside a block ends it, adds the day and the rest",
		"coverage": {"E1": [["SA-1", "Day", "2026-11-02", "2026-11-13"]]},
		"ops": [
			{
				"employee": "E1",
				"action": "Swap Day",
				"shift_type": "Night",
				"from_date": "2026-11-04",
				"assignment": "SA-1",
			}
		],
		"expect": {
			"E1": [
				["SA-1", "Day", "2026-11-02", "2026-11-03"],
				["new-0-0", "Night", "2026-11-04", "2026-11-04"],
				["new-0-1", "Day", "2026-11-05", "2026-11-13"],
			]
		},
		"problems": [],
		"steps": [
			[
				["end", "SA-1", "Day", "2026-11-02", "2026-11-03"],
				["create", "new-0-0", "Night", "2026-11-04", "2026-11-04"],
				["create", "new-0-1", "Day", "2026-11-05", "2026-11-13"],
			]
		],
	},
	{
		"name": "clear day: inside a block ends it and adds the rest",
		"coverage": {"E1": [["SA-1", "Day", "2026-11-02", "2026-11-13"]]},
		"ops": [{"employee": "E1", "action": "Clear Day", "from_date": "2026-11-04", "assignment": "SA-1"}],
		"expect": {
			"E1": [
				["SA-1", "Day", "2026-11-02", "2026-11-03"],
				["new-0-0", "Day", "2026-11-05", "2026-11-13"],
			]
		},
		"problems": [],
		"steps": [
			[
				["end", "SA-1", "Day", "2026-11-02", "2026-11-03"],
				["create", "new-0-0", "Day", "2026-11-05", "2026-11-13"],
			]
		],
	},
	{
		"name": "swap day: on the first day cancels the block before the creates",
		"coverage": {"E1": [["SA-1", "Day", "2026-11-04", "2026-11-13"]]},
		"ops": [
			{
				"employee": "E1",
				"action": "Swap Day",
				"shift_type": "Night",
				"from_date": "2026-11-04",
				"assignment": "SA-1",
			}
		],
		"expect": {
			"E1": [
				["new-0-0", "Night", "2026-11-04", "2026-11-04"],
				["new-0-1", "Day", "2026-11-05", "2026-11-13"],
			]
		},
		"problems": [],
		"steps": [
			[
				["cancel", "SA-1", "Day", "2026-11-04", "2026-11-13"],
				["create", "new-0-0", "Night", "2026-11-04", "2026-11-04"],
				["create", "new-0-1", "Day", "2026-11-05", "2026-11-13"],
			]
		],
	},
	{
		"name": "clear day: on the first day cancels the block and adds the rest",
		"coverage": {"E1": [["SA-1", "Day", "2026-11-04", "2026-11-13"]]},
		"ops": [{"employee": "E1", "action": "Clear Day", "from_date": "2026-11-04", "assignment": "SA-1"}],
		"expect": {"E1": [["new-0-0", "Day", "2026-11-05", "2026-11-13"]]},
		"problems": [],
		"steps": [
			[
				["cancel", "SA-1", "Day", "2026-11-04", "2026-11-13"],
				["create", "new-0-0", "Day", "2026-11-05", "2026-11-13"],
			]
		],
	},
	{
		"name": "swap day: on the last day ends the block and adds the day",
		"coverage": {"E1": [["SA-1", "Day", "2026-11-02", "2026-11-04"]]},
		"ops": [
			{
				"employee": "E1",
				"action": "Swap Day",
				"shift_type": "Night",
				"from_date": "2026-11-04",
				"assignment": "SA-1",
			}
		],
		"expect": {
			"E1": [
				["SA-1", "Day", "2026-11-02", "2026-11-03"],
				["new-0-0", "Night", "2026-11-04", "2026-11-04"],
			]
		},
		"problems": [],
		"steps": [
			[
				["end", "SA-1", "Day", "2026-11-02", "2026-11-03"],
				["create", "new-0-0", "Night", "2026-11-04", "2026-11-04"],
			]
		],
	},
	{
		"name": "clear day: on the last day only ends the block",
		"coverage": {"E1": [["SA-1", "Day", "2026-11-02", "2026-11-04"]]},
		"ops": [{"employee": "E1", "action": "Clear Day", "from_date": "2026-11-04", "assignment": "SA-1"}],
		"expect": {"E1": [["SA-1", "Day", "2026-11-02", "2026-11-03"]]},
		"problems": [],
		"steps": [[["end", "SA-1", "Day", "2026-11-02", "2026-11-03"]]],
	},
	{
		"name": "swap day: a one-day block is cancelled and replaced",
		"coverage": {"E1": [["SA-1", "Day", "2026-11-04", "2026-11-04"]]},
		"ops": [
			{
				"employee": "E1",
				"action": "Swap Day",
				"shift_type": "Night",
				"from_date": "2026-11-04",
				"assignment": "SA-1",
			}
		],
		"expect": {"E1": [["new-0-0", "Night", "2026-11-04", "2026-11-04"]]},
		"problems": [],
		"steps": [
			[
				["cancel", "SA-1", "Day", "2026-11-04", "2026-11-04"],
				["create", "new-0-0", "Night", "2026-11-04", "2026-11-04"],
			]
		],
	},
	{
		"name": "clear day: a one-day block is cancelled",
		"coverage": {"E1": [["SA-1", "Day", "2026-11-04", "2026-11-04"]]},
		"ops": [{"employee": "E1", "action": "Clear Day", "from_date": "2026-11-04", "assignment": "SA-1"}],
		"expect": {},
		"problems": [],
		"steps": [[["cancel", "SA-1", "Day", "2026-11-04", "2026-11-04"]]],
	},
	{
		"name": "swap day: no shift on the date is refused, even when the assignment is named",
		"coverage": {"E1": [["SA-1", "Day", "2026-11-05", "2026-11-13"]]},
		"ops": [
			{
				"employee": "E1",
				"action": "Swap Day",
				"shift_type": "Night",
				"from_date": "2026-11-04",
				"assignment": "SA-1",
			}
		],
		"expect": {"E1": [["SA-1", "Day", "2026-11-05", "2026-11-13"]]},
		"problems": [[0, NO_SHIFT_ON_DATE]],
		"steps": [[]],
	},
	{
		"name": "clear day: no shift on the date is refused",
		"coverage": {"E1": [["SA-1", "Day", "2026-11-05", "2026-11-13"]]},
		"ops": [{"employee": "E1", "action": "Clear Day", "from_date": "2026-11-04"}],
		"expect": {"E1": [["SA-1", "Day", "2026-11-05", "2026-11-13"]]},
		"problems": [[0, NO_SHIFT_ON_DATE]],
		"steps": [[]],
	},
	{
		"name": "swap day: to the shift they already have is refused",
		"coverage": {"E1": [["SA-1", "Day", "2026-11-02", "2026-11-13"]]},
		"ops": [
			{
				"employee": "E1",
				"action": "Swap Day",
				"shift_type": "Day",
				"from_date": "2026-11-04",
				"assignment": "SA-1",
			}
		],
		"expect": {"E1": [["SA-1", "Day", "2026-11-02", "2026-11-13"]]},
		"problems": [[0, SAME_SHIFT]],
		"steps": [[]],
	},
	{
		"name": "clear day: inside an open-ended block, the rest stays open-ended",
		"coverage": {"E1": [["SA-1", "Day", "2026-11-02", None]]},
		"ops": [{"employee": "E1", "action": "Clear Day", "from_date": "2026-11-04", "assignment": "SA-1"}],
		"expect": {
			"E1": [["SA-1", "Day", "2026-11-02", "2026-11-03"], ["new-0-0", "Day", "2026-11-05", None]]
		},
		"problems": [],
		"steps": [
			[
				["end", "SA-1", "Day", "2026-11-02", "2026-11-03"],
				["create", "new-0-0", "Day", "2026-11-05", None],
			]
		],
	},
	{
		"name": "one-day: a clear after a swap acts on the rest the swap created",
		"coverage": {"E1": [["SA-1", "Day", "2026-11-02", "2026-11-13"]]},
		"ops": [
			{
				"employee": "E1",
				"action": "Swap Day",
				"shift_type": "Night",
				"from_date": "2026-11-03",
				"assignment": "SA-1",
			},
			{"employee": "E1", "action": "Clear Day", "from_date": "2026-11-05", "assignment": "SA-1"},
		],
		"expect": {
			"E1": [
				["SA-1", "Day", "2026-11-02", "2026-11-02"],
				["new-0-0", "Night", "2026-11-03", "2026-11-03"],
				["new-0-1", "Day", "2026-11-04", "2026-11-04"],
				["new-1-0", "Day", "2026-11-06", "2026-11-13"],
			]
		},
		"problems": [],
		"steps": [
			[
				["end", "SA-1", "Day", "2026-11-02", "2026-11-02"],
				["create", "new-0-0", "Night", "2026-11-03", "2026-11-03"],
				["create", "new-0-1", "Day", "2026-11-04", "2026-11-13"],
			],
			[
				["end", "new-0-1", "Day", "2026-11-04", "2026-11-04"],
				["create", "new-1-0", "Day", "2026-11-06", "2026-11-13"],
			],
		],
	},
	{
		"name": "one-day: a swap on a day already swapped replaces the one-day block",
		"coverage": {"E1": [["SA-1", "Day", "2026-11-02", "2026-11-13"]]},
		"ops": [
			{
				"employee": "E1",
				"action": "Swap Day",
				"shift_type": "Night",
				"from_date": "2026-11-04",
				"assignment": "SA-1",
			},
			{
				"employee": "E1",
				"action": "Swap Day",
				"shift_type": "Late",
				"from_date": "2026-11-04",
				"assignment": "SA-1",
			},
		],
		"expect": {
			"E1": [
				["SA-1", "Day", "2026-11-02", "2026-11-03"],
				["new-0-1", "Day", "2026-11-05", "2026-11-13"],
				["new-1-0", "Late", "2026-11-04", "2026-11-04"],
			]
		},
		"problems": [],
		"steps": [
			[
				["end", "SA-1", "Day", "2026-11-02", "2026-11-03"],
				["create", "new-0-0", "Night", "2026-11-04", "2026-11-04"],
				["create", "new-0-1", "Day", "2026-11-05", "2026-11-13"],
			],
			[
				["cancel", "new-0-0", "Night", "2026-11-04", "2026-11-04"],
				["create", "new-1-0", "Late", "2026-11-04", "2026-11-04"],
			],
		],
	},
	{
		"name": "one-day: refusals change nothing and the rest still run",
		"coverage": {"E1": [["SA-1", "Day", "2026-11-02", "2026-11-13"]]},
		"ops": [
			{"employee": "E1", "action": "Clear Day", "from_date": "2026-11-20", "assignment": "SA-1"},
			{
				"employee": "E1",
				"action": "Swap Day",
				"shift_type": "Day",
				"from_date": "2026-11-04",
				"assignment": "SA-1",
			},
			{"employee": "E1", "action": "Swap Day", "from_date": "2026-11-04", "assignment": "SA-1"},
			{"employee": "E1", "action": "Clear Day", "from_date": "2026-11-04", "assignment": "SA-1"},
		],
		"expect": {
			"E1": [
				["SA-1", "Day", "2026-11-02", "2026-11-03"],
				["new-3-0", "Day", "2026-11-05", "2026-11-13"],
			]
		},
		"problems": [[0, NO_SHIFT_ON_DATE], [1, SAME_SHIFT], [2, NO_SHIFT_TYPE]],
		"steps": [
			[],
			[],
			[],
			[
				["end", "SA-1", "Day", "2026-11-02", "2026-11-03"],
				["create", "new-3-0", "Day", "2026-11-05", "2026-11-13"],
			],
		],
	},
	# Plan 2026-11-05-001 A2: a run split by a one-day change gets a note.
	{
		"name": "run split: an end before a cleared day notes the run resumes after it",
		"coverage": {"E1": [["SA-1", "Day", "2026-11-02", None]]},
		"off_days": {"E1": {"from": "2026-11-01", "to": "2026-12-31", "dates": WEEKENDS}},
		"ops": [
			{"employee": "E1", "action": "Clear Day", "from_date": "2026-11-04", "assignment": "SA-1"},
			{"employee": "E1", "action": "End", "to_date": "2026-11-02", "assignment": "SA-1"},
		],
		"expect": {
			"E1": [["SA-1", "Day", "2026-11-02", "2026-11-02"], ["new-0-0", "Day", "2026-11-05", None]]
		},
		"problems": [],
		"notes": [[1, RUN_SPLIT]],
		"steps": [
			[
				["end", "SA-1", "Day", "2026-11-02", "2026-11-03"],
				["create", "new-0-0", "Day", "2026-11-05", None],
			],
			[["end", "SA-1", "Day", "2026-11-02", "2026-11-02"]],
		],
	},
	{
		"name": "run split: a change before a swapped day notes the run resumes after it",
		"coverage": {"E1": [["SA-1", "Day", "2026-10-26", None]]},
		"off_days": {"E1": {"from": "2026-11-01", "to": "2026-12-31", "dates": WEEKENDS}},
		"ops": [
			{
				"employee": "E1",
				"action": "Swap Day",
				"shift_type": "Night",
				"from_date": "2026-11-04",
				"assignment": "SA-1",
			},
			{
				"employee": "E1",
				"action": "Change",
				"shift_type": "Late",
				"from_date": "2026-11-02",
				"assignment": "SA-1",
			},
		],
		"expect": {
			"E1": [
				["SA-1", "Day", "2026-10-26", "2026-11-01"],
				["new-0-0", "Night", "2026-11-04", "2026-11-04"],
				["new-0-1", "Day", "2026-11-05", None],
				["new-1-0", "Late", "2026-11-02", "2026-11-03"],
			]
		},
		"problems": [],
		"notes": [[1, RUN_SPLIT]],
	},
	{
		"name": "run split: one working day then a weekend still notes it",
		"coverage": {
			"E1": [["SA-1", "Day", "2026-11-02", "2026-11-05"], ["SA-2", "Day", "2026-11-09", "2026-11-13"]]
		},
		"off_days": {"E1": {"from": "2026-11-01", "to": "2026-12-31", "dates": WEEKENDS}},
		"ops": [{"employee": "E1", "action": "End", "to_date": "2026-11-03", "assignment": "SA-1"}],
		"expect": {
			"E1": [["SA-1", "Day", "2026-11-02", "2026-11-03"], ["SA-2", "Day", "2026-11-09", "2026-11-13"]]
		},
		"problems": [],
		"notes": [[0, RUN_SPLIT]],
	},
	{
		"name": "run split: a swap on the Monday after a weekend still notes it",
		"coverage": {
			"E1": [["SA-1", "Day", "2026-11-02", "2026-11-06"], ["SA-2", "Day", "2026-11-09", "2026-11-13"]]
		},
		"off_days": {"E1": {"from": "2026-11-01", "to": "2026-12-31", "dates": WEEKENDS}},
		"ops": [
			{
				"employee": "E1",
				"action": "Swap Day",
				"shift_type": "Night",
				"from_date": "2026-11-09",
				"assignment": "SA-2",
			},
			{"employee": "E1", "action": "End", "to_date": "2026-11-04", "assignment": "SA-1"},
		],
		"expect": {
			"E1": [
				["SA-1", "Day", "2026-11-02", "2026-11-04"],
				["new-0-0", "Night", "2026-11-09", "2026-11-09"],
				["new-0-1", "Day", "2026-11-10", "2026-11-13"],
			]
		},
		"problems": [],
		"notes": [[1, RUN_SPLIT]],
	},
	{
		"name": "run split: no note when the same shift resumes after two working days",
		"coverage": {
			"E1": [["SA-1", "Day", "2026-11-02", "2026-11-03"], ["SA-2", "Day", "2026-11-06", "2026-11-13"]]
		},
		"off_days": {"E1": {"from": "2026-11-01", "to": "2026-12-31", "dates": WEEKENDS}},
		"ops": [{"employee": "E1", "action": "End", "to_date": "2026-11-02", "assignment": "SA-1"}],
		"expect": {
			"E1": [["SA-1", "Day", "2026-11-02", "2026-11-02"], ["SA-2", "Day", "2026-11-06", "2026-11-13"]]
		},
		"problems": [],
		"notes": [],
	},
]


def _iso(value):
	return value.isoformat() if value else None


def _coverage_in(table):
	return {
		employee: [
			{"key": key, "shift_type": shift, "start_date": start, "end_date": end}
			for key, shift, start, end in rows
		]
		for employee, rows in table.items()
	}


def _steps_out(steps):
	return [
		[[s["action"], s["key"], s["shift_type"], s["start_date"], s["end_date"]] for s in op_steps]
		for op_steps in steps
	]


def _coverage_out(coverage):
	return {
		employee: [[a["key"], a["shift_type"], _iso(a["start_date"]), _iso(a["end_date"])] for a in rows]
		for employee, rows in coverage.items()
		if rows
	}


class TestSimulateSharedCases(UnitTestCase):
	def test_every_shared_case(self):
		for case in CASES:
			with self.subTest(case["name"]):
				notes = []
				coverage, problems, steps = simulate(
					_coverage_in(case["coverage"]), case["ops"], case.get("off_days", {}), notes
				)
				self.assertEqual(_coverage_out(coverage), case["expect"])
				self.assertEqual([[p["index"], p["message"]] for p in problems], case["problems"])
				self.assertEqual([[n["index"], n["message"]] for n in notes], case.get("notes", []))
				if "steps" in case:
					self.assertEqual(_steps_out(steps), case["steps"])

	def test_without_off_days_skip_is_ignored_and_runs_are_not_followed(self):
		# Plan 2026-11-03-001 A1: a legacy request simulates as before.
		coverage, problems, _ = simulate(
			{},
			[
				{
					"employee": "E1",
					"action": "Assign",
					"shift_type": "Day",
					"from_date": "2026-11-02",
					"to_date": "2026-11-27",
					"skip_off_days": 1,
				}
			],
		)
		self.assertEqual(problems, [])
		self.assertEqual(_coverage_out(coverage), {"E1": [["new-0-0", "Day", "2026-11-02", "2026-11-27"]]})

	def test_one_day_ops_take_the_same_steps_in_legacy_mode(self):
		# Plan 2026-11-05-001 U3: Swap Day and Clear Day never read off days.
		cases = [c for c in CASES if all(op["action"] in ("Swap Day", "Clear Day") for op in c["ops"])]
		self.assertTrue(cases)
		for case in cases:
			with self.subTest(case["name"]):
				coverage, problems, steps = simulate(_coverage_in(case["coverage"]), case["ops"])
				self.assertEqual(_coverage_out(coverage), case["expect"])
				self.assertEqual([[p["index"], p["message"]] for p in problems], case["problems"])
				self.assertEqual(_steps_out(steps), case["steps"])

	def test_input_coverage_is_not_mutated(self):
		original = _coverage_in({"E1": [["SA-1", "Day", "2026-11-01", None]]})
		simulate(
			original,
			[{"employee": "E1", "action": "End", "to_date": "2026-11-30", "assignment": "SA-1"}],
		)
		self.assertIsNone(original["E1"][0]["end_date"])

	def test_problems_name_the_employee(self):
		_, problems, _ = simulate(
			{}, [{"employee": "E9", "action": "Change", "from_date": "2026-11-10", "shift_type": "Day"}]
		)
		self.assertEqual(problems[0]["employee"], "E9")

	def test_accepts_date_objects(self):
		coverage, problems, _ = simulate(
			{"E1": [{"key": "SA-1", "shift_type": "Day", "start_date": date(2026, 11, 1), "end_date": None}]},
			[{"employee": "E1", "action": "End", "to_date": date(2026, 11, 3), "assignment": "SA-1"}],
		)
		self.assertEqual(problems, [])
		self.assertEqual(coverage["E1"][0]["end_date"], date(2026, 11, 3))


class TestAffectedWindow(UnitTestCase):
	def test_bounded_ops_give_earliest_through_latest(self):
		window = affected_window(
			[
				{
					"employee": "E1",
					"action": "Assign",
					"shift_type": "Day",
					"from_date": "2026-11-10",
					"to_date": "2026-11-20",
				},
				{"employee": "E1", "action": "End", "to_date": "2026-11-05", "assignment": "SA-1"},
				{"employee": "E2", "action": "End", "to_date": "2026-11-12", "assignment": "SA-2"},
			]
		)
		self.assertEqual(
			window,
			{"E1": (date(2026, 11, 5), date(2026, 11, 20)), "E2": (date(2026, 11, 12), date(2026, 11, 12))},
		)

	def test_open_assign_or_change_leaves_the_window_open(self):
		window = affected_window(
			[
				{
					"employee": "E1",
					"action": "Assign",
					"shift_type": "Day",
					"from_date": "2026-11-10",
					"to_date": "2026-11-20",
				},
				{"employee": "E1", "action": "Assign", "shift_type": "Day", "from_date": "2026-12-01"},
				{"employee": "E2", "action": "Change", "shift_type": "Night", "from_date": "2026-11-15"},
			]
		)
		self.assertEqual(window, {"E1": (date(2026, 11, 10), None), "E2": (date(2026, 11, 15), None)})

	def test_steps_widen_the_window_to_the_end_of_the_run(self):
		ops = [{"employee": "E1", "action": "End", "to_date": "2026-11-18", "assignment": "SA-3"}]
		coverage = _coverage_in(
			{"E1": [["SA-3", "Day", "2026-11-16", "2026-11-20"], ["SA-4", "Day", "2026-11-23", "2026-11-27"]]}
		)
		_, _, steps = simulate(
			coverage, ops, {"E1": {"from": "2026-11-01", "to": "2026-12-31", "dates": WEEKENDS}}
		)
		self.assertEqual(affected_window(ops), {"E1": (date(2026, 11, 18), date(2026, 11, 18))})
		self.assertEqual(affected_window(ops, steps), {"E1": (date(2026, 11, 18), date(2026, 11, 27))})

	def test_one_day_ops_reach_the_end_of_the_rest_they_recreate(self):
		# Plan 2026-11-05-001 U3: the op alone doesn't carry the block's end,
		# so it is open until the steps bound it.
		ops = [
			{"employee": "E1", "action": "Clear Day", "from_date": "2026-11-04", "assignment": "SA-1"},
			{"employee": "E2", "action": "Swap Day", "shift_type": "Night", "from_date": "2026-11-05"},
			{"employee": "E3", "action": "Clear Day", "from_date": "2026-11-04"},
		]
		coverage = _coverage_in(
			{
				"E1": [["SA-1", "Day", "2026-11-02", "2026-11-13"]],
				"E2": [["SA-2", "Day", "2026-11-02", None]],
				"E3": [["SA-3", "Day", "2026-11-02", "2026-11-04"]],
			}
		)
		_, _, steps = simulate(coverage, ops)
		self.assertEqual(
			affected_window(ops),
			{
				"E1": (date(2026, 11, 4), None),
				"E2": (date(2026, 11, 5), None),
				"E3": (date(2026, 11, 4), None),
			},
		)
		self.assertEqual(
			affected_window(ops, steps),
			{
				"E1": (date(2026, 11, 4), date(2026, 11, 13)),
				"E2": (date(2026, 11, 5), None),
				"E3": (date(2026, 11, 4), date(2026, 11, 4)),
			},
		)

	def test_op_date_is_the_end_date_for_end(self):
		self.assertEqual(
			op_date({"action": "End", "from_date": "2026-11-01", "to_date": "2026-11-09"}), date(2026, 11, 9)
		)
		self.assertEqual(op_date({"action": "Change", "from_date": "2026-11-01"}), date(2026, 11, 1))
		self.assertEqual(
			op_date({"action": "Clear Day", "from_date": "2026-11-04", "to_date": "2026-11-09"}),
			date(2026, 11, 4),
		)
		self.assertEqual(op_date({"action": "Swap Day", "from_date": "2026-11-04"}), date(2026, 11, 4))
		self.assertIsNone(op_date({"action": "Assign", "from_date": "not a date"}))


class TestSnapshotKey(UnitTestCase):
	def _assignment(self, name, shift, start, end, status="Active", modified="2026-10-01 10:00:00"):
		return {
			"key": name,
			"shift_type": shift,
			"start_date": start,
			"end_date": end,
			"status": status,
			"modified": modified,
		}

	def test_ignores_status_modified_and_names(self):
		before = [self._assignment("SA-1", "Day", date(2026, 11, 1), None)]
		after = [self._assignment("SA-9", "Day", date(2026, 11, 1), None, "Inactive", "2026-10-30 02:00:00")]
		window = (date(2026, 11, 5), None)
		self.assertEqual(snapshot_key(before, window), snapshot_key(after, window))

	def test_differs_when_coverage_changes_inside_the_window(self):
		window = (date(2026, 11, 5), date(2026, 11, 10))
		before = [self._assignment("SA-1", "Day", date(2026, 11, 1), None)]
		after = [
			self._assignment("SA-1", "Day", date(2026, 11, 1), date(2026, 11, 6)),
			self._assignment("SA-2", "Night", date(2026, 11, 7), None),
		]
		self.assertNotEqual(snapshot_key(before, window), snapshot_key(after, window))

	def test_only_overlapping_sorted_and_json_stable(self):
		window = (date(2026, 11, 5), date(2026, 11, 10))
		rows = [
			self._assignment("SA-3", "Night", date(2026, 11, 8), None),
			self._assignment("SA-1", "Day", date(2026, 10, 1), date(2026, 10, 31)),
			self._assignment("SA-2", "Day", date(2026, 11, 1), date(2026, 11, 7)),
			self._assignment("SA-4", "Day", date(2026, 11, 11), None),
		]
		self.assertEqual(
			snapshot_key(rows, window),
			[["Day", "2026-11-01", "2026-11-07"], ["Night", "2026-11-08", None]],
		)
