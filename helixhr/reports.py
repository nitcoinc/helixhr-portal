"""Report catalog, engines and shaper (plan 2026-10-04-001 U2, KTD4/KTD7/KTD12).

Endpoints stay in `helixhr/api.py`, which is the single authorization
boundary; nothing here is whitelisted. `api.run_report` resolves access with
`helixhr.utils.resolve_report_access` and hands the resolved scope to `run`.

Two engines:

- ``frappe``: an HRMS Script Report's own `execute`, called through
  `Report.execute_module` (never `frappe.desk.query_report.run`, never
  `execute_script_report`'s prepared-report watcher). It runs inside
  `utils.as_administrator()` -- for exactly the module call, never
  `frappe.set_user`, which signs the caller out -- only after
  access passed, company was forced and every entity filter was validated
  (resolved decision 1: no read DocPerm grants for the portal roles).
- ``helixhr``: a named-column query here that receives the resolved scope.

One shaper (`shape`) feeds the screen and, later, every export.
"""

import itertools
import re

import frappe
from frappe import _
from frappe.utils import cint, flt, get_first_day, get_last_day, getdate, today

from helixhr.utils import (
	admin_scope_employee_filters,
	as_administrator,
	employee_in_admin_scope,
	project_in_scope,
	project_scope_filters,
)

# KTD14. Screen cap, and the export caps by format. Inline exports stream
# from the request; between the two caps is U13's background queue; above
# the background cap the request is refused with "narrow the filters".
SCREEN_ROW_CAP = 2000
EXPORT_FORMATS = ("csv", "xlsx", "pdf")
INLINE_EXPORT_CAP = {"csv": 10000, "xlsx": 10000, "pdf": 1500}
BACKGROUND_EXPORT_CAP = {"csv": 100000, "xlsx": 100000, "pdf": 10000}

# KTD3: never offered, and preflight FAILs if one enters the catalog.
DENY_LIST = frozenset({"Timesheet Billing Summary", "Project Profitability", "Project-wise Stock Tracking"})

# Timesheet workflow states that count as "awaiting approval" (resolved
# decision 6) -- not every docstatus 0 row; Draft and Sent Back are not pending.
PENDING_TIMESHEET_STATES = ("Pending Approval", "Pending HR")

NUMERIC_FIELDTYPES = frozenset({"Int", "Float", "Currency", "Duration"})
_ROUNDED_FIELDTYPES = frozenset({"Float", "Currency"})
_TEXT_FIELDTYPES = frozenset({"Data", "Link", "Dynamic Link", "Select", "Small Text", "Text"})
_ENTITY_TYPES = frozenset({"employee", "project", "task", "department"})
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


# --- filter specs -----------------------------------------------------------


def _this_month_start():
	return str(get_first_day(today()))


def _this_month_end():
	return str(get_last_day(today()))


def _this_month():
	return today()[:7]


def _f(name, type, label, maps_to=None, reqd=0, default=None, options=None):
	return {
		"name": name,
		"type": type,
		"label": label,
		"maps_to": maps_to or name,
		"reqd": reqd,
		"default": default,
		"options": options,
	}


_EMPLOYEE = _f("employee", "employee", "Employee")
_DEPARTMENT = _f("department", "department", "Department")
_FROM = _f("from_date", "date", "From", reqd=1, default=_this_month_start)
_TO = _f("to_date", "date", "To", reqd=1, default=_this_month_end)


def _monthly_attendance_adapt(engine_filters):
	"""The portal's single month picker -> HRMS's month/year pair."""
	year, month = engine_filters.pop("month").split("-")
	engine_filters.update({"filter_based_on": "Month", "month": cint(month), "year": cint(year)})


# --- HelixHR queries --------------------------------------------------------

# KTD3: the named column list. No rate, amount or cost column is ever named,
# so none can be returned.
_HOURS_FIELDS = (
	"date(td.from_time) as `date`",
	"ts.employee as employee",
	"ts.employee_name as employee_name",
	"td.project as project",
	"prj.project_name as project_name",
	"td.task as task",
	# Plan 2026-10-05-001 U8: same label the flagship uses for a taskless row.
	"coalesce(tsk.subject, %(no_task)s) as task_subject",
	"td.hours as hours",
	"td.billing_hours as billing_hours",
)


def _hours_by_project(filters, scope):
	"""Hours logged against projects and tasks. Approved (submitted) rows
	only, unless ``include_pending`` adds rows awaiting approval, marked in
	an ``approval`` column."""
	include_pending = cint(filters.get("include_pending"))
	columns = [
		{"fieldname": "date", "label": "Date", "fieldtype": "Date"},
		{"fieldname": "employee", "label": "Employee", "fieldtype": "Link", "options": "Employee"},
		{"fieldname": "employee_name", "label": "Employee name", "fieldtype": "Data"},
		{"fieldname": "project", "label": "Project", "fieldtype": "Link", "options": "Project"},
		{"fieldname": "project_name", "label": "Project name", "fieldtype": "Data"},
		{"fieldname": "task", "label": "Task", "fieldtype": "Link", "options": "Task"},
		{"fieldname": "task_subject", "label": "Task subject", "fieldtype": "Data"},
		{"fieldname": "hours", "label": "Hours", "fieldtype": "Float"},
		{"fieldname": "billing_hours", "label": "Billable hours", "fieldtype": "Float"},
	]
	fields = list(_HOURS_FIELDS)
	values = {"pending_states": PENDING_TIMESHEET_STATES, "no_task": NO_TASK}
	if include_pending:
		conditions = ["(ts.docstatus = 1 or (ts.docstatus = 0 and ts.workflow_state in %(pending_states)s))"]
		fields.append("if(ts.docstatus = 1, 'Approved', 'Pending') as approval")
		columns.append({"fieldname": "approval", "label": "Approval", "fieldtype": "Data"})
	else:
		conditions = ["ts.docstatus = 1"]

	if scope["kind"] == "company":
		conditions.append("ts.company = %(scope_company)s")
		values["scope_company"] = scope["company"]
	elif scope["kind"] == "assigned":
		scope_filters = project_scope_filters(scope)
		if scope_filters is None:
			return columns, []
		conditions.append("td.project in %(scope_projects)s")
		values["scope_projects"] = tuple(scope_filters["name"][1])
	elif scope["kind"] != "unscoped":
		return columns, []

	for key, condition in (
		("employee", "ts.employee = %(employee)s"),
		("project", "td.project = %(project)s"),
		("task", "td.task = %(task)s"),
		("from_date", "date(td.from_time) >= %(from_date)s"),
		("to_date", "date(td.from_time) <= %(to_date)s"),
	):
		if filters.get(key):
			conditions.append(condition)
			values[key] = filters[key]

	rows = frappe.db.sql(
		f"""
		select {", ".join(fields)}
		from `tabTimesheet Detail` td
		inner join `tabTimesheet` ts on ts.name = td.parent
		left join `tabTask` tsk on tsk.name = td.task
		left join `tabProject` prj on prj.name = td.project
		where {" and ".join(conditions)}
		order by `date` desc, ts.employee asc, td.idx asc
		""",
		values,
		as_dict=True,
	)
	return columns, rows


def _timesheet_scope_conditions(scope, values):
	"""SQL conditions narrowing ``tabTimesheet ts`` / ``tabTimesheet Detail
	td`` to ``scope``, or None when nothing is in scope."""
	if scope["kind"] == "company":
		values["scope_company"] = scope["company"]
		return ["ts.company = %(scope_company)s"]
	if scope["kind"] == "assigned":
		scope_filters = project_scope_filters(scope)
		if scope_filters is None:
			return None
		values["scope_projects"] = tuple(scope_filters["name"][1])
		return ["td.project in %(scope_projects)s"]
	if scope["kind"] == "unscoped":
		return []
	return None


# --- U7: monthly project timesheet (flagship) --------------------------------

NO_TASK = "(No task)"
HOURS_BASIS = ("All hours", "Billable hours")

_PROJECT_TIMESHEET_COLUMNS = [
	{"fieldname": "date", "label": "Date", "fieldtype": "Date"},
	{"fieldname": "employee_name", "label": "Employee", "fieldtype": "Data"},
	{"fieldname": "task_subject", "label": "Task", "fieldtype": "Data"},
	{"fieldname": "note", "label": "Note", "fieldtype": "Data"},
	{"fieldname": "hours", "label": "Hours", "fieldtype": "Float"},
	{"fieldname": "billing_hours", "label": "Billable hours", "fieldtype": "Float"},
]


def _month_bounds(month):
	start = getdate(f"{month}-01")
	return start, getdate(get_last_day(start))


def _project_timesheet(filters, scope):
	"""Approved (submitted) hours on one project in one month, one row per
	time log (KTD3: named columns only, no rate or amount)."""
	columns = [dict(column) for column in _PROJECT_TIMESHEET_COLUMNS]
	if not filters.get("project") or not filters.get("month"):
		return columns, []
	values = {}
	conditions = _timesheet_scope_conditions(scope, values)
	if conditions is None:
		return columns, []
	start, end = _month_bounds(filters["month"])
	values.update({"project": filters["project"], "start": start, "end": end})
	rows = frappe.db.sql(
		f"""
		select date(td.from_time) as `date`, ts.employee_name as employee_name,
			coalesce(nullif(tsk.subject, ''), td.task, %(no_task)s) as task_subject,
			td.description as note, td.hours as hours, td.billing_hours as billing_hours
		from `tabTimesheet Detail` td
		inner join `tabTimesheet` ts on ts.name = td.parent
		left join `tabTask` tsk on tsk.name = td.task
		where ts.docstatus = 1 and td.project = %(project)s
			and date(td.from_time) between %(start)s and %(end)s
			{"".join(f" and {condition}" for condition in conditions)}
		order by `date` asc, ts.employee_name asc, td.idx asc
		""",
		{**values, "no_task": NO_TASK},
		as_dict=True,
	)
	return columns, rows


def _pending_project_hours(filters, scope):
	"""Hours on the same project and month still awaiting approval
	(resolved decision 6: workflow states, not every draft)."""
	values = {}
	conditions = _timesheet_scope_conditions(scope, values)
	if conditions is None or not filters.get("project") or not filters.get("month"):
		return 0
	start, end = _month_bounds(filters["month"])
	values.update(
		{"project": filters["project"], "start": start, "end": end, "states": PENDING_TIMESHEET_STATES}
	)
	total = frappe.db.sql(
		f"""
		select coalesce(sum(td.hours), 0)
		from `tabTimesheet Detail` td
		inner join `tabTimesheet` ts on ts.name = td.parent
		where ts.docstatus = 0 and ts.workflow_state in %(states)s and td.project = %(project)s
			and date(td.from_time) between %(start)s and %(end)s
			{"".join(f" and {condition}" for condition in conditions)}
		""",
		values,
	)[0][0]
	return flt(total, 2)


def _company_holidays(company, start, end):
	"""``{date: description}`` for the company's holiday list in the month,
	weekly offs left out (weekends are shaded separately). The list is the
	company's Holiday List Assignment as of month end, else its default."""
	from hrms.utils.holiday_list import get_assigned_holiday_list

	holiday_list = get_assigned_holiday_list(company, end) or frappe.db.get_value(
		"Company", company, "default_holiday_list"
	)
	if not holiday_list:
		return {}
	rows = frappe.get_all(
		"Holiday",
		filters={"parent": holiday_list, "holiday_date": ["between", [start, end]], "weekly_off": 0},
		fields=["holiday_date", "description"],
	)
	return {
		getdate(row.holiday_date): frappe.utils.strip_html(row.description or "") or _("Holiday")
		for row in rows
	}


def project_timesheet_grid(filters, rows, holidays=None):
	"""The task x day pivot of the detail rows (KTD7: one structure feeds
	screen, Excel and PDF). Values are Σ hours (or billable hours, by the
	"Hours basis" filter), rounded per row first exactly as `shape` rounds,
	so the grid's grand total equals the detail's."""
	start, end = _month_bounds(filters["month"])
	field = "billing_hours" if filters.get("basis") == "Billable hours" else "hours"
	holidays = holidays or {}
	days = []
	for offset in range((end - start).days + 1):
		day = frappe.utils.add_days(start, offset)
		days.append(
			{
				"day": day.day,
				"date": str(day),
				"weekday": day.strftime("%a")[:2],
				"weekend": day.weekday() >= 5,
				"holiday": holidays.get(day),
			}
		)

	cells = {}
	for row in rows:
		task = row.get("task_subject") or NO_TASK
		key = (task, getdate(row["date"]).day)
		cells[key] = cells.get(key, 0) + flt(row.get(field), 2)
	tasks = sorted({task for task, _day in cells}, key=lambda task: (task == NO_TASK, task.lower()))

	grid_rows = []
	for task in tasks:
		values = [flt(cells[(task, d["day"])], 2) if (task, d["day"]) in cells else None for d in days]
		grid_rows.append({"task": task, "cells": values, "total": flt(sum(v or 0 for v in values), 2)})
	day_totals = [
		flt(sum(row["cells"][index] or 0 for row in grid_rows), 2) if grid_rows else None
		for index in range(len(days))
	]
	return {
		"field": field,
		"days": days,
		"rows": grid_rows,
		"day_totals": [total or None for total in day_totals],
		"grand_total": flt(sum(row["total"] for row in grid_rows), 2),
		"holidays": [{"date": str(day), "description": text} for day, text in sorted(holidays.items())],
	}


def _project_timesheet_extra(clean, scope, rows):
	"""Flagship extras next to the shaped detail: the grid, the pending
	footnote figure and the project header (name, customer)."""
	if not clean.get("project") or not clean.get("month"):
		return {}
	project = frappe.db.get_value(
		"Project", clean["project"], ["project_name", "customer", "company"], as_dict=True
	)
	start, end = _month_bounds(clean["month"])
	holidays = _company_holidays(project.company, start, end) if project and project.company else {}
	return {
		"grid": project_timesheet_grid(clean, rows, holidays),
		"pending_hours": _pending_project_hours(clean, scope),
		"project_name": project.project_name if project else clean["project"],
		"customer": project.customer if project else None,
	}


def _grid_columns_and_rows(grid):
	"""The grid as a (columns, shaped rows) pair for the Excel Grid sheet."""
	columns = [{"fieldname": "task", "label": "Task", "fieldtype": "Data"}]
	columns += [
		{"fieldname": f"d{d['day']}", "label": str(d["day"]), "fieldtype": "Float"} for d in grid["days"]
	]
	columns.append({"fieldname": "total", "label": "Total", "fieldtype": "Float"})
	rows = [
		{
			"task": row["task"],
			**{f"d{d['day']}": value for d, value in zip(grid["days"], row["cells"], strict=True)},
			"total": row["total"],
			"_kind": "row",
		}
		for row in grid["rows"]
	]
	rows.append(
		{
			**{f"d{d['day']}": value for d, value in zip(grid["days"], grid["day_totals"], strict=True)},
			"total": grid["grand_total"],
			"_kind": "total",
		}
	)
	return columns, rows


def _project_timesheet_sheets(result, columns):
	extra = result.get("extra") or {}
	sheets = []
	if extra.get("grid"):
		grid_columns, grid_rows = _grid_columns_and_rows(extra["grid"])
		sheets.append(("Grid", grid_columns, grid_rows))
	sheets.append(("Detail", columns, result["shaped"]["rows"]))
	return sheets


# --- U8: missing timesheets ---------------------------------------------------


def _week_starts(from_date, to_date):
	"""Mondays of every Monday-Sunday week touching the period (KTD10 of
	plan 2026-09-02-001: one week = one Timesheet)."""
	start, end = getdate(from_date), getdate(to_date)
	monday = frappe.utils.add_days(start, -start.weekday())
	weeks = []
	while monday <= end:
		weeks.append(monday)
		monday = frappe.utils.add_days(monday, 7)
	return weeks


def _missing_timesheets(filters, scope):
	"""One row per Active in-scope employee per week: hours logged, the
	week's timesheet state (none / draft / pending / approved) and approved
	leave days that week."""
	columns = [
		{"fieldname": "employee", "label": "Employee", "fieldtype": "Link", "options": "Employee"},
		{"fieldname": "employee_name", "label": "Employee name", "fieldtype": "Data"},
		{"fieldname": "department", "label": "Department", "fieldtype": "Link", "options": "Department"},
		{"fieldname": "week_start", "label": "Week of", "fieldtype": "Date"},
		{"fieldname": "state", "label": "Timesheet", "fieldtype": "Data"},
		{"fieldname": "hours", "label": "Hours", "fieldtype": "Float"},
		{"fieldname": "leave_days", "label": "Leave days", "fieldtype": "Float"},
	]
	if scope["kind"] not in ("company", "unscoped") or not filters.get("from_date"):
		return columns, []
	if getdate(filters["to_date"]) < getdate(filters["from_date"]):
		frappe.throw(_("From must be on or before To."))
	weeks = _week_starts(filters["from_date"], filters["to_date"])
	if len(weeks) > 27:
		frappe.throw(_("Choose a period of six months or less."))

	query = {"status": "Active"}
	if scope["kind"] == "company":
		query["company"] = scope["company"]
	for key, field in (("employee", "name"), ("department", "department")):
		if filters.get(key):
			query[field] = filters[key]
	if filters.get("project_members_only"):
		members = frappe.get_all("Project User", distinct=True, pluck="user")
		query["user_id"] = ["in", members or [""]]
	employees = frappe.get_all(
		"Employee",
		filters=query,
		fields=["name", "employee_name", "department"],
		order_by="employee_name asc",
		ignore_permissions=True,
	)
	if not employees:
		return columns, []
	names = [employee.name for employee in employees]
	first, last = weeks[0], frappe.utils.add_days(weeks[-1], 6)

	sheets = frappe.db.sql(
		"""
		select employee, start_date, docstatus, workflow_state, total_hours
		from `tabTimesheet`
		where employee in %(names)s and docstatus < 2 and start_date between %(first)s and %(last)s
		""",
		{"names": names, "first": first, "last": last},
		as_dict=True,
	)
	rank = {"none": 0, "draft": 1, "pending": 2, "approved": 3}
	logged = {}
	for sheet in sheets:
		start = getdate(sheet.start_date)
		key = (sheet.employee, frappe.utils.add_days(start, -start.weekday()))
		state = (
			"approved"
			if sheet.docstatus == 1
			else "pending"
			if sheet.workflow_state in PENDING_TIMESHEET_STATES
			else "draft"
		)
		hours, best = logged.get(key, (0, "none"))
		logged[key] = (hours + flt(sheet.total_hours), state if rank[state] > rank[best] else best)

	leaves = frappe.get_all(
		"Leave Application",
		filters={
			"employee": ["in", names],
			"docstatus": 1,
			"status": "Approved",
			"from_date": ["<=", last],
			"to_date": [">=", first],
		},
		fields=["employee", "from_date", "to_date", "half_day", "half_day_date"],
		ignore_permissions=True,
	)
	leave_days = {}
	for leave in leaves:
		day, end = getdate(leave.from_date), getdate(leave.to_date)
		while day <= end:
			if first <= day <= last:
				key = (leave.employee, frappe.utils.add_days(day, -day.weekday()))
				portion = (
					0.5 if leave.half_day and getdate(leave.half_day_date or leave.from_date) == day else 1
				)
				leave_days[key] = leave_days.get(key, 0) + portion
			day = frappe.utils.add_days(day, 1)

	rows = []
	for employee in employees:
		for week in weeks:
			hours, state = logged.get((employee.name, week), (0, "none"))
			rows.append(
				{
					"employee": employee.name,
					"employee_name": employee.employee_name,
					"department": employee.department,
					"week_start": week,
					"state": state,
					"hours": flt(hours, 2),
					"leave_days": leave_days.get((employee.name, week), 0),
				}
			)
	return columns, rows


# --- U9-U11: shared helpers ---------------------------------------------------


def _company_condition(scope, column, values):
	"""``[condition]`` narrowing ``column`` to the scope's company, ``[]`` for
	an unscoped caller, None when nothing is in scope (these reports have
	company tiers only)."""
	if scope["kind"] == "company":
		values["scope_company"] = scope["company"]
		return [f"{column} = %(scope_company)s"]
	if scope["kind"] == "unscoped":
		return []
	return None


def _range(filters, max_days=None, message=None):
	start, end = getdate(filters["from_date"]), getdate(filters["to_date"])
	if end < start:
		frappe.throw(_("From must be on or before To."))
	if max_days and (end - start).days > max_days:
		frappe.throw(message)
	return start, end


def _where(conditions):
	return " and ".join(conditions) if conditions else "1 = 1"


# --- U9: late and early summary -----------------------------------------------


def _late_early_summary(filters, scope):
	"""Per employee per month: submitted Attendance flagged ``late_entry`` /
	``early_exit``. Cancelled (docstatus 2) and draft rows never count."""
	columns = [
		{"fieldname": "employee", "label": "Employee", "fieldtype": "Link", "options": "Employee"},
		{"fieldname": "employee_name", "label": "Employee name", "fieldtype": "Data"},
		{"fieldname": "department", "label": "Department", "fieldtype": "Link", "options": "Department"},
		{"fieldname": "month", "label": "Month", "fieldtype": "Data"},
		{"fieldname": "late_entries", "label": "Late entries", "fieldtype": "Int"},
		{"fieldname": "early_exits", "label": "Early exits", "fieldtype": "Int"},
	]
	values = {}
	conditions = _company_condition(scope, "a.company", values)
	if conditions is None or not filters.get("from_date"):
		return columns, []
	start, end = _range(filters, 366, _("Choose a period of one year or less."))
	values.update({"start": start, "end": end})
	conditions += ["a.docstatus = 1", "a.attendance_date between %(start)s and %(end)s"]
	for key, condition in (
		("employee", "a.employee = %(employee)s"),
		("department", "a.department = %(department)s"),
	):
		if filters.get(key):
			conditions.append(condition)
			values[key] = filters[key]
	rows = frappe.db.sql(
		f"""
		select a.employee, a.employee_name, a.department,
			date_format(a.attendance_date, '%%Y-%%m') as `month`,
			sum(a.late_entry) as late_entries, sum(a.early_exit) as early_exits
		from `tabAttendance` a
		where {_where(conditions)} and (a.late_entry = 1 or a.early_exit = 1)
		group by a.employee, a.employee_name, a.department, `month`
		order by `month` asc, a.employee_name asc
		""",
		values,
		as_dict=True,
	)
	return columns, rows


# --- U10: leave taken, who is out ---------------------------------------------


def _leave_taken(filters, scope):
	"""Approved, submitted Leave Applications by leave type and the month of
	``from_date`` (an application spanning two months counts in the first;
	the catalog question says so)."""
	columns = [
		{"fieldname": "leave_type", "label": "Leave type", "fieldtype": "Link", "options": "Leave Type"},
		{"fieldname": "month", "label": "Month", "fieldtype": "Data"},
		{"fieldname": "applications", "label": "Applications", "fieldtype": "Int"},
		{"fieldname": "leave_days", "label": "Leave days", "fieldtype": "Float"},
	]
	values = {}
	conditions = _company_condition(scope, "la.company", values)
	if conditions is None or not filters.get("from_date"):
		return columns, []
	start, end = _range(filters, 731, _("Choose a period of two years or less."))
	values.update({"start": start, "end": end})
	conditions += ["la.docstatus = 1", "la.status = 'Approved'", "la.from_date between %(start)s and %(end)s"]
	for key, condition in (
		("employee", "la.employee = %(employee)s"),
		("department", "la.department = %(department)s"),
	):
		if filters.get(key):
			conditions.append(condition)
			values[key] = filters[key]
	rows = frappe.db.sql(
		f"""
		select la.leave_type, date_format(la.from_date, '%%Y-%%m') as `month`,
			count(*) as applications, sum(la.total_leave_days) as leave_days
		from `tabLeave Application` la
		where {_where(conditions)}
		group by la.leave_type, `month`
		order by `month` asc, la.leave_type asc
		""",
		values,
		as_dict=True,
	)
	return columns, rows


def _who_is_out(filters, scope):
	"""Leave overlapping the range, one row per person per application.
	Approved and submitted only, unless ``include_pending`` adds Open drafts,
	marked in ``approval``."""
	columns = [
		{"fieldname": "employee", "label": "Employee", "fieldtype": "Link", "options": "Employee"},
		{"fieldname": "employee_name", "label": "Employee name", "fieldtype": "Data"},
		{"fieldname": "department", "label": "Department", "fieldtype": "Link", "options": "Department"},
		{"fieldname": "leave_type", "label": "Leave type", "fieldtype": "Link", "options": "Leave Type"},
		{"fieldname": "from_date", "label": "From", "fieldtype": "Date"},
		{"fieldname": "to_date", "label": "To", "fieldtype": "Date"},
		{"fieldname": "total_leave_days", "label": "Days", "fieldtype": "Float"},
		{"fieldname": "approval", "label": "Approval", "fieldtype": "Data"},
	]
	values = {}
	conditions = _company_condition(scope, "la.company", values)
	if conditions is None or not filters.get("from_date"):
		return columns, []
	start, end = _range(filters, 366, _("Choose a period of one year or less."))
	values.update({"start": start, "end": end})
	conditions += ["la.from_date <= %(end)s", "la.to_date >= %(start)s"]
	if cint(filters.get("include_pending")):
		conditions.append(
			"((la.docstatus = 1 and la.status = 'Approved') or (la.docstatus = 0 and la.status = 'Open'))"
		)
	else:
		conditions.append("la.docstatus = 1 and la.status = 'Approved'")
	for key, condition in (
		("employee", "la.employee = %(employee)s"),
		("department", "la.department = %(department)s"),
	):
		if filters.get(key):
			conditions.append(condition)
			values[key] = filters[key]
	rows = frappe.db.sql(
		f"""
		select la.employee, la.employee_name, la.department, la.leave_type, la.from_date, la.to_date,
			la.total_leave_days, if(la.docstatus = 1, 'Approved', 'Pending') as approval
		from `tabLeave Application` la
		where {_where(conditions)}
		order by la.from_date asc, la.employee_name asc
		""",
		values,
		as_dict=True,
	)
	return columns, rows


# --- U11: people and lifecycle ------------------------------------------------


def _employee_scope(scope, filters):
	"""(sql conditions on ``tabEmployee e``, values) or (None, None)."""
	values = {}
	conditions = _company_condition(scope, "e.company", values)
	if conditions is None:
		return None, None
	for key in ("department", "designation", "branch", "employment_type"):
		if filters.get(key):
			conditions.append(f"e.{key} = %({key})s")
			values[key] = filters[key]
	return conditions, values


def _headcount_on(day, conditions, values):
	"""Employees on the books at end of ``day``: joined on or before it, and
	no relieving date or one after it."""
	return frappe.db.sql(
		f"""
		select count(*) from `tabEmployee` e
		where {_where(conditions)} and e.date_of_joining <= %(day)s
			and (e.relieving_date is null or e.relieving_date > %(day)s)
		""",
		{**values, "day": day},
	)[0][0]


def _headcount_trend(filters, scope):
	columns = [
		{"fieldname": "month", "label": "Month", "fieldtype": "Data"},
		{"fieldname": "month_end", "label": "Month end", "fieldtype": "Date"},
		{"fieldname": "headcount", "label": "Headcount", "fieldtype": "Int"},
	]
	conditions, values = _employee_scope(scope, filters)
	if conditions is None or not filters.get("from_date"):
		return columns, []
	start, end = _range(filters)
	month_end = getdate(get_last_day(start))
	rows = []
	while month_end <= getdate(get_last_day(end)):
		if len(rows) >= 36:
			frappe.throw(_("Choose a period of three years or less."))
		rows.append(
			{
				"month": str(month_end)[:7],
				"month_end": month_end,
				"headcount": _headcount_on(month_end, conditions, values),
			}
		)
		month_end = getdate(get_last_day(frappe.utils.add_days(month_end, 1)))
	return columns, rows


def _joiners_and_leavers(filters, scope):
	"""One row per join or leave event in the period."""
	columns = [
		{"fieldname": "event", "label": "Event", "fieldtype": "Data"},
		{"fieldname": "event_date", "label": "Date", "fieldtype": "Date"},
		{"fieldname": "employee", "label": "Employee", "fieldtype": "Link", "options": "Employee"},
		{"fieldname": "employee_name", "label": "Employee name", "fieldtype": "Data"},
		{"fieldname": "department", "label": "Department", "fieldtype": "Link", "options": "Department"},
		{"fieldname": "designation", "label": "Designation", "fieldtype": "Link", "options": "Designation"},
	]
	conditions, values = _employee_scope(scope, filters)
	if conditions is None or not filters.get("from_date"):
		return columns, []
	start, end = _range(filters)
	values = {**values, "start": start, "end": end}
	rows = frappe.db.sql(
		f"""
		select 'Joined' as event, e.date_of_joining as event_date, e.name as employee,
			e.employee_name, e.department, e.designation
		from `tabEmployee` e
		where {_where(conditions)} and e.date_of_joining between %(start)s and %(end)s
		union all
		select 'Left', e.relieving_date, e.name, e.employee_name, e.department, e.designation
		from `tabEmployee` e
		where {_where(conditions)} and e.relieving_date between %(start)s and %(end)s
		order by event_date asc, employee_name asc
		""",
		values,
		as_dict=True,
	)
	return columns, rows


def attrition_summary(start_headcount, end_headcount, leavers):
	"""Attrition = leavers / average of start and end headcount, as a
	percentage string; "n/a" when the starting headcount is 0."""
	average = (start_headcount + end_headcount) / 2
	if not start_headcount or not average:
		return "n/a"
	return f"{flt(leavers / average * 100, 1)}%"


def _joiners_and_leavers_extra(clean, scope, rows):
	conditions, values = _employee_scope(scope, clean)
	if conditions is None or not clean.get("from_date"):
		return {}
	start, end = getdate(clean["from_date"]), getdate(clean["to_date"])
	start_headcount = _headcount_on(frappe.utils.add_days(start, -1), conditions, values)
	end_headcount = _headcount_on(end, conditions, values)
	joiners = sum(1 for row in rows if row["event"] == "Joined")
	leavers = sum(1 for row in rows if row["event"] == "Left")
	return {
		"summary": [
			{"label": "Joiners", "value": joiners},
			{"label": "Leavers", "value": leavers},
			{"label": "Headcount at start", "value": start_headcount},
			{"label": "Headcount at end", "value": end_headcount},
			{"label": "Attrition", "value": attrition_summary(start_headcount, end_headcount, leavers)},
		]
	}


MONTHS = (
	"January",
	"February",
	"March",
	"April",
	"May",
	"June",
	"July",
	"August",
	"September",
	"October",
	"November",
	"December",
)


def _this_month_name():
	return MONTHS[getdate(today()).month - 1]


def _celebrations(filters, scope):
	"""Birthdays (day and month only -- never a year or an age) and work
	anniversaries (with years of service) of Active employees in one month."""
	columns = [
		{"fieldname": "occasion", "label": "Occasion", "fieldtype": "Data"},
		{"fieldname": "day", "label": "Day", "fieldtype": "Int"},
		{"fieldname": "employee", "label": "Employee", "fieldtype": "Link", "options": "Employee"},
		{"fieldname": "employee_name", "label": "Employee name", "fieldtype": "Data"},
		{"fieldname": "department", "label": "Department", "fieldtype": "Link", "options": "Department"},
		{"fieldname": "years", "label": "Years of service", "fieldtype": "Data"},
	]
	conditions, values = _employee_scope(scope, filters)
	if conditions is None or filters.get("month") not in MONTHS:
		return columns, []
	month = MONTHS.index(filters["month"]) + 1
	year = getdate(today()).year
	# Only day-of-month and month leave SQL; the birth year is never selected.
	employees = frappe.db.sql(
		f"""
		select e.name, e.employee_name, e.department,
			month(e.date_of_birth) as birth_month, dayofmonth(e.date_of_birth) as birth_day,
			e.date_of_joining
		from `tabEmployee` e
		where {_where(conditions)} and e.status = 'Active'
			and (month(e.date_of_birth) = %(month)s or month(e.date_of_joining) = %(month)s)
		""",
		{**values, "month": month},
		as_dict=True,
	)
	rows = []
	for e in employees:
		base = {"employee": e.name, "employee_name": e.employee_name, "department": e.department}
		if e.birth_month == month:
			rows.append({**base, "occasion": "Birthday", "day": e.birth_day, "years": None})
		joined = getdate(e.date_of_joining) if e.date_of_joining else None
		if joined and joined.month == month and year - joined.year >= 1:
			rows.append(
				{**base, "occasion": "Work anniversary", "day": joined.day, "years": str(year - joined.year)}
			)
	rows.sort(key=lambda row: (row["day"], row["employee_name"] or ""))
	return columns, rows


HR_REQUEST_AGE_BUCKETS = ((2, "0-2 days"), (7, "3-7 days"), (14, "8-14 days"), (30, "15-30 days"))
_HR_REQUEST_CLOSED = ("Done", "Rejected")


def _age_bucket(days):
	return next((label for limit, label in HR_REQUEST_AGE_BUCKETS if days <= limit), "Over 30 days")


def _hr_request_aging(filters, scope):
	"""Open HR Requests with their age. Named columns only: never
	``details``, ``hr_note``, ``helixhr_decision_reason`` or any
	``correction_*`` field."""
	columns = [
		{"fieldname": "request", "label": "Request", "fieldtype": "Link", "options": "HR Request"},
		{"fieldname": "employee", "label": "Employee", "fieldtype": "Link", "options": "Employee"},
		{"fieldname": "employee_name", "label": "Employee name", "fieldtype": "Data"},
		{
			"fieldname": "category",
			"label": "Category",
			"fieldtype": "Link",
			"options": "HelixHR Request Category",
		},
		{"fieldname": "status", "label": "Status", "fieldtype": "Data"},
		{"fieldname": "routed_to_role", "label": "Routed to", "fieldtype": "Link", "options": "Role"},
		{"fieldname": "picked_up_by", "label": "Assignee", "fieldtype": "Link", "options": "User"},
		{"fieldname": "picked_up_by_name", "label": "Assignee name", "fieldtype": "Data"},
		{"fieldname": "opened_on", "label": "Opened", "fieldtype": "Date"},
		{"fieldname": "age_days", "label": "Age (days)", "fieldtype": "Int"},
		{"fieldname": "age_bucket", "label": "Age", "fieldtype": "Data"},
	]
	values = {"closed": _HR_REQUEST_CLOSED}
	conditions = _company_condition(scope, "e.company", values)
	if conditions is None:
		return columns, []
	conditions.append("r.status not in %(closed)s")
	conditions.append("r.docstatus < 2")
	for key in ("employee", "department"):
		if filters.get(key):
			conditions.append(f"e.{'name' if key == 'employee' else key} = %({key})s")
			values[key] = filters[key]
	rows = frappe.db.sql(
		f"""
		select r.name as request, r.employee, e.employee_name, r.category, r.status,
			r.routed_to_role, r.picked_up_by, u.full_name as picked_up_by_name,
			date(r.creation) as opened_on,
			datediff(%(today)s, date(r.creation)) as age_days
		from `tabHR Request` r
		inner join `tabEmployee` e on e.name = r.employee
		left join `tabUser` u on u.name = r.picked_up_by
		where {_where(conditions)}
		order by age_days desc, r.name asc
		""",
		{**values, "today": today()},
		as_dict=True,
	)
	for row in rows:
		row["age_bucket"] = _age_bucket(cint(row["age_days"]))
	return columns, rows


# --- U11: wrapped-report post-processing --------------------------------------


def _headcount_post(columns, rows):
	"""Employee Analytics: drop Date of Birth (never shown), and call its
	"Name" column what it is."""
	columns = [dict(c) for c in columns if c["fieldname"] != "date_of_birth"]
	for column in columns:
		if column["fieldname"] == "name":
			column.update({"fieldname": "employee_name", "label": "Employee name"})
	rows = [
		{("employee_name" if k == "name" else k): v for k, v in row.items() if k != "date_of_birth"}
		for row in rows
	]
	return columns, rows


def _advance_post(columns, rows):
	"""Employee Advance Summary joins ``"<id>: <name>"`` into one column;
	split it back so the employee column holds the Employee id."""
	columns = [dict(c) for c in columns]
	for column in columns:
		if column["fieldname"] == "employee":
			column.update({"fieldtype": "Link", "options": "Employee"})
	index = next((i for i, c in enumerate(columns) if c["fieldname"] == "employee"), None)
	if index is not None:
		columns.insert(
			index + 1, {"fieldname": "employee_name", "label": "Employee name", "fieldtype": "Data"}
		)
	out = []
	for row in rows:
		row = dict(row)
		employee, _sep, name = str(row.get("employee") or "").partition(": ")
		row["employee"], row["employee_name"] = employee or None, name or None
		out.append(row)
	return columns, out


def _employee_names(columns, rows):
	"""Plan 2026-10-05-001 U8 (KTD8), wrapped reports only: every Employee
	link column gets a visible name column right after it. An HRMS name
	column that is already there (``employee_name``, ``reports_to_name``) is
	un-hidden; a missing one is added and filled from Employee."""
	columns = [dict(column) for column in columns]
	fieldnames = {column["fieldname"] for column in columns}
	for column in list(columns):
		if column.get("fieldtype") != "Link" or column.get("options") != "Employee":
			continue
		field = column["fieldname"]
		name_field = "employee_name" if field == "employee" else f"{field}_name"
		if name_field in fieldnames:
			next(c for c in columns if c["fieldname"] == name_field).pop("hidden", None)
			continue
		ids = list({row.get(field) for row in rows if row.get(field)})
		names = (
			dict(
				frappe.get_all(
					"Employee", filters={"name": ["in", ids]}, fields=["name", "employee_name"], as_list=True
				)
			)
			if ids
			else {}
		)
		columns.insert(
			columns.index(column) + 1,
			{
				"fieldname": name_field,
				"label": f"{column.get('label') or frappe.unscrub(field)} name",
				"fieldtype": "Data",
			},
		)
		fieldnames.add(name_field)
		rows = [{**row, name_field: names.get(row.get(field))} for row in rows]
	return columns, rows


def _list_adapt(*fields):
	"""HRMS filters applied with ``isin``: wrap the portal's single value."""

	def adapt(engine_filters):
		for field in fields:
			if engine_filters.get(field):
				engine_filters[field] = [engine_filters[field]]

	return adapt


# Employee Information's twelve columns plus the name, as a fixed allowlist
# (the client cannot ask for another column).
_DIRECTORY_COLUMNS = (
	("name", "Employee", "Link", "Employee"),
	("employee_name", "Employee name", "Data", None),
	("employee_number", "Employee number", "Data", None),
	("date_of_joining", "Date of joining", "Date", None),
	("branch", "Branch", "Link", "Branch"),
	("department", "Department", "Link", "Department"),
	("designation", "Designation", "Link", "Designation"),
	("gender", "Gender", "Link", "Gender"),
	("status", "Status", "Data", None),
	("company", "Company", "Link", "Company"),
	("employment_type", "Employment type", "Link", "Employment Type"),
	("reports_to", "Reports to", "Link", "Employee"),
	("company_email", "Company email", "Data", None),
)


def _employee_directory(filters, scope):
	columns = [
		{"fieldname": f, "label": label, "fieldtype": ft, **({"options": opt} if opt else {})}
		for f, label, ft, opt in _DIRECTORY_COLUMNS
	]
	# Plan 2026-10-05-001 U8 (KTD8): the manager's name next to the id.
	at = next(i for i, column in enumerate(columns) if column["fieldname"] == "reports_to") + 1
	columns.insert(at, {"fieldname": "reports_to_name", "label": "Reports to name", "fieldtype": "Data"})
	query = {}
	if scope["kind"] == "company":
		query["company"] = scope["company"]
	elif scope["kind"] != "unscoped":
		return columns, []
	for key in ("department", "designation", "branch", "employment_type", "status"):
		if filters.get(key):
			query[key] = filters[key]
	rows = frappe.get_all(
		"Employee",
		filters=query,
		fields=[column[0] for column in _DIRECTORY_COLUMNS],
		order_by="employee_name asc",
		ignore_permissions=True,
	)
	managers = {row.reports_to for row in rows if row.reports_to}
	names = (
		dict(
			frappe.get_all(
				"Employee",
				filters={"name": ["in", list(managers)]},
				fields=["name", "employee_name"],
				as_list=True,
			)
		)
		if managers
		else {}
	)
	for row in rows:
		row["reports_to_name"] = names.get(row.reports_to)
	return columns, rows


# --- catalog ----------------------------------------------------------------
#
# Entry shape (later units add entries in the same shape):
#   key, family, label, question, engine ("frappe" | "helixhr"),
#   report (frappe engine: the HRMS Report name) | query (helixhr engine:
#     callable(filters, scope) -> (columns, rows)),
#   filters: [_f(...)], adapt: optional callable(engine_filters) mutating in place,
#   group_by: fieldnames allowed as group dimensions (max two at a time),
#   totals: fieldnames to sum, or None for every numeric column,
#   scopes: ("company",) or ("company", "project"), orientation,
#   shows_amounts (keep Currency columns), default_grants (seed patch),
#   default_preset (U3: a lib/datePresets.js id for from/to entries).
#
# Families: "time", "attendance", "leave", "people".


def _entry(key, family, label, question, engine, filters, **extra):
	entry = {
		"key": key,
		"family": family,
		"label": label,
		"question": question,
		"engine": engine,
		"filters": filters,
		"report": None,
		"query": None,
		"adapt": None,
		"group_by": (),
		"totals": None,
		"scopes": ("company",),
		"orientation": "portrait",
		"shows_amounts": False,
		"default_grants": {},
		# U3: the date preset a fresh open starts on (lib/datePresets.js ids);
		# None for entries without a from/to range.
		"default_preset": None,
		# U5 export hooks (U7's flagship uses both): a template under
		# helixhr/templates/reports/ for the PDF, and a callable
		# ``(result, columns) -> [(sheet_name, columns, shaped_rows), ...]``
		# for a multi-sheet workbook. None = the generic one.
		"pdf_template": None,
		"xlsx_sheets": None,
		# U7: callable ``(clean filters, scope, rows) -> dict`` stored as
		# ``result["extra"]`` (the flagship grid and footnote); a fixed
		# grouping the client cannot change; CSV of data rows only.
		"extra": None,
		"fixed_group_by": None,
		"csv_detail_only": False,
		# U11: frappe engine only, ``(columns, rows) -> (columns, rows)`` run on
		# the wrapped output before the Currency strip (drop or reshape columns).
		"post": None,
	}
	entry.update(extra)
	return entry


_HR_USER_RUN = {"hr_user_run": 1}

CATALOG = (
	_entry(
		"hours_by_project",
		"time",
		"Hours by project, task and employee",
		"How many approved hours went into each project and task, and by whom?",
		"helixhr",
		[
			_EMPLOYEE,
			_f("project", "project", "Project"),
			_f("task", "task", "Task"),
			_FROM,
			_TO,
			_f("include_pending", "toggle", "Include pending approval", default=0),
		],
		query=_hours_by_project,
		default_preset="last_month",
		group_by=("employee", "project", "task", "date"),
		totals=("hours", "billing_hours"),
		scopes=("company", "project"),
		default_grants={"dm_run": 1, "dm_export": 1},
	),
	_entry(
		"project_timesheet",
		"time",
		"Monthly project timesheet",
		"What did the team log on one project this month, task by task and day by day?",
		"helixhr",
		[
			_f("project", "project", "Project", reqd=1),
			_f("month", "month", "Month", reqd=1, default=_this_month),
			_f("basis", "select", "Hours basis", default="All hours", options=HOURS_BASIS),
		],
		query=_project_timesheet,
		extra=_project_timesheet_extra,
		fixed_group_by=("date",),
		csv_detail_only=True,
		totals=("hours", "billing_hours"),
		scopes=("company", "project"),
		orientation="landscape",
		pdf_template="project_timesheet.html",
		xlsx_sheets=_project_timesheet_sheets,
		default_grants={"dm_run": 1, "dm_export": 1},
	),
	_entry(
		"missing_timesheets",
		"time",
		"Missing timesheets",
		"Who has not logged a timesheet for a week, and were they on leave?",
		"helixhr",
		[
			_FROM,
			_TO,
			_EMPLOYEE,
			_DEPARTMENT,
			_f("project_members_only", "toggle", "Project members only", default=1),
		],
		query=_missing_timesheets,
		default_preset="last_month",
		group_by=("employee", "department", "week_start", "state"),
		totals=("hours", "leave_days"),
		default_grants=_HR_USER_RUN,
	),
	_entry(
		"hours_utilization",
		"time",
		"Employee hours utilization",
		"How much of each person's standard working time was logged, and how much was billable?",
		"frappe",
		[_FROM, _TO, _EMPLOYEE, _DEPARTMENT, _f("project", "project", "Project")],
		report="Employee Hours Utilization Based On Timesheet",
		default_preset="last_month",
		group_by=("department",),
		default_grants=_HR_USER_RUN,
	),
	_entry(
		"monthly_attendance",
		"attendance",
		"Monthly attendance",
		"What was everyone's attendance, day by day, in a month?",
		"frappe",
		[_f("month", "month", "Month", reqd=1, default=_this_month), _EMPLOYEE, _DEPARTMENT],
		report="Monthly Attendance Sheet",
		adapt=_monthly_attendance_adapt,
		orientation="landscape",
		default_grants=_HR_USER_RUN,
	),
	_entry(
		"shift_attendance",
		"attendance",
		"Shift attendance",
		"Who worked which shift, and who came in late or left early?",
		"frappe",
		[_FROM, _TO, _EMPLOYEE, _DEPARTMENT],
		report="Shift Attendance",
		default_preset="this_month",
		group_by=("employee", "shift", "department"),
		default_grants=_HR_USER_RUN,
	),
	_entry(
		"leave_balance",
		"leave",
		"Leave balance",
		"How much leave does someone have left, by type?",
		"frappe",
		[_FROM, _TO, _EMPLOYEE, _DEPARTMENT],
		report="Employee Leave Balance",
		default_preset="this_month",
		group_by=("leave_type",),
		default_grants=_HR_USER_RUN,
	),
	_entry(
		"leave_balance_summary",
		"leave",
		"Leave balance summary",
		"What are leave balances across the company, one row per person?",
		"frappe",
		[_f("date", "date", "As of", reqd=1, default=today), _EMPLOYEE, _DEPARTMENT],
		report="Employee Leave Balance Summary",
		group_by=("department",),
		default_grants=_HR_USER_RUN,
	),
	_entry(
		"leave_ledger",
		"leave",
		"Leave ledger",
		"Which leave transactions moved a balance?",
		"frappe",
		[_FROM, _TO, _EMPLOYEE, _DEPARTMENT],
		report="Leave Ledger",
		default_preset="this_month",
		group_by=("employee", "leave_type", "transaction_type"),
		default_grants=_HR_USER_RUN,
	),
	_entry(
		"employee_exits",
		"people",
		"Employee exits",
		"Who has left, and how did their exit interview go?",
		"frappe",
		[_FROM, _TO, _EMPLOYEE, _DEPARTMENT],
		report="Employee Exits",
		default_preset="this_month",
		group_by=("department", "designation"),
		default_grants=_HR_USER_RUN,
	),
	_entry(
		"employee_directory",
		"people",
		"Employee directory",
		"Who works here, in which department, branch and role?",
		"helixhr",
		[
			_DEPARTMENT,
			_f("designation", "select_link", "Designation"),
			_f("branch", "select_link", "Branch"),
			_f("employment_type", "select_link", "Employment type"),
			_f(
				"status",
				"select",
				"Status",
				default="Active",
				options=("Active", "Inactive", "Suspended", "Left"),
			),
		],
		query=_employee_directory,
		group_by=("department", "designation", "branch", "employment_type", "status"),
		totals=(),
		default_grants=_HR_USER_RUN,
	),
	# --- U9: attendance and shifts ---
	_entry(
		"holiday_work",
		"attendance",
		"Employees working on a holiday",
		"Who was marked present on a company holiday?",
		"frappe",
		[_FROM, _TO, _DEPARTMENT],
		report="Employees Working on a Holiday",
		default_preset="last_month",
		group_by=("employee", "holiday"),
		totals=(),
		default_grants=_HR_USER_RUN,
	),
	_entry(
		"late_early_summary",
		"attendance",
		"Late and early summary",
		"How often did each person come in late or leave early, month by month?",
		"helixhr",
		[_FROM, _TO, _EMPLOYEE, _DEPARTMENT],
		query=_late_early_summary,
		default_preset="last_month",
		group_by=("department", "month", "employee"),
		totals=("late_entries", "early_exits"),
		default_grants=_HR_USER_RUN,
	),
	# --- U10: leave ---
	_entry(
		"leave_taken",
		"leave",
		"Leave taken by type and month",
		"How much approved leave was taken, by type and month? "
		"(Each application counts in the month it starts.)",
		"helixhr",
		[_FROM, _TO, _EMPLOYEE, _DEPARTMENT],
		query=_leave_taken,
		default_preset="this_year",
		group_by=("leave_type", "month"),
		totals=("applications", "leave_days"),
		default_grants=_HR_USER_RUN,
	),
	_entry(
		"who_is_out",
		"leave",
		"Who is out",
		"Who is on leave during these dates?",
		"helixhr",
		[
			_FROM,
			_TO,
			_EMPLOYEE,
			_DEPARTMENT,
			_f("include_pending", "toggle", "Include pending approval", default=0),
		],
		query=_who_is_out,
		default_preset="this_month",
		group_by=("department", "leave_type", "employee", "approval"),
		totals=("total_leave_days",),
		default_grants=_HR_USER_RUN,
	),
	# --- U11: people and lifecycle ---
	_entry(
		"headcount",
		"people",
		"Headcount",
		"How many active employees are there, by department, designation or branch?",
		"frappe",
		[
			_f(
				"parameter",
				"select",
				"Counted by",
				reqd=1,
				default="Department",
				options=("Department", "Designation", "Branch", "Employment Type", "Grade"),
			)
		],
		report="Employee Analytics",
		post=_headcount_post,
		group_by=("department", "designation", "branch"),
		totals=(),
		default_grants=_HR_USER_RUN,
	),
	_entry(
		"headcount_trend",
		"people",
		"Headcount trend",
		"How many people were on the books at each month end?",
		"helixhr",
		[_FROM, _TO, _DEPARTMENT],
		query=_headcount_trend,
		default_preset="this_year",
		totals=(),
		default_grants=_HR_USER_RUN,
	),
	_entry(
		"joiners_leavers",
		"people",
		"Joiners and leavers",
		"Who joined and who left in a period, and what was the attrition?",
		"helixhr",
		[_FROM, _TO, _DEPARTMENT],
		query=_joiners_and_leavers,
		extra=_joiners_and_leavers_extra,
		default_preset="this_year",
		group_by=("event", "department", "designation"),
		totals=(),
		default_grants=_HR_USER_RUN,
	),
	_entry(
		"celebrations",
		"people",
		"Celebrations by month",
		"Whose birthday or work anniversary falls in a month?",
		"helixhr",
		[
			_f("month", "select", "Month", reqd=1, default=_this_month_name, options=MONTHS),
			_DEPARTMENT,
		],
		query=_celebrations,
		group_by=("occasion", "department"),
		totals=(),
		default_grants=_HR_USER_RUN,
	),
	_entry(
		"hr_request_aging",
		"people",
		"HR request aging",
		"Which HR requests are still open, with whom, and for how long?",
		"helixhr",
		[_EMPLOYEE, _DEPARTMENT],
		query=_hr_request_aging,
		group_by=("category", "status", "routed_to_role", "picked_up_by", "age_bucket"),
		totals=(),
		default_grants=_HR_USER_RUN,
	),
	_entry(
		"unpaid_expense_claims",
		"people",
		"Unpaid expense claims",
		"Which approved expense claims are still waiting to be paid?",
		"frappe",
		[_EMPLOYEE, _DEPARTMENT, _f("branch", "select_link", "Branch")],
		report="Unpaid Expense Claim",
		shows_amounts=True,
		group_by=("employee", "department", "branch"),
		# R3: amounts -- HR User is off until HR grants it.
		default_grants={"hr_user_run": 0},
	),
	_entry(
		"employee_advances",
		"people",
		"Employee advance summary",
		"Which employee advances are outstanding, paid or claimed?",
		"frappe",
		[
			_FROM,
			_TO,
			_EMPLOYEE,
			_DEPARTMENT,
			_f(
				"status",
				"select",
				"Status",
				options=("Draft", "Paid", "Partially Paid", "Unpaid", "Claimed", "Cancelled"),
			),
		],
		report="Employee Advance Summary",
		adapt=_list_adapt("department"),
		post=_advance_post,
		shows_amounts=True,
		default_preset="this_year",
		group_by=("employee", "department", "status"),
		default_grants={"hr_user_run": 0},
	),
)

_BY_KEY = {entry["key"]: entry for entry in CATALOG}


def get_entry(report_key):
	"""The catalog entry for ``report_key``, or None. Non-strings are None."""
	if not isinstance(report_key, str):
		return None
	return _BY_KEY.get(report_key)


def wrapped_report_names():
	"""The HRMS Report names the ``frappe`` engine runs (preflight, and
	`get_report_link`'s Desk hand-off)."""
	return tuple(entry["report"] for entry in CATALOG if entry["engine"] == "frappe")


# --- filters ----------------------------------------------------------------


def _entity_in_scope(kind, value, scope):
	if kind == "employee":
		if scope["kind"] == "assigned":
			# The hours query intersects with member projects itself; an
			# employee filter can only narrow that further.
			return bool(frappe.db.exists("Employee", value))
		return employee_in_admin_scope(value, scope)
	if kind == "project":
		return project_in_scope(value, scope)
	if kind == "task":
		project = frappe.db.get_value("Task", value, "project")
		return bool(project) and project_in_scope(project, scope)
	if kind == "department":
		if scope["kind"] == "unscoped":
			return bool(frappe.db.exists("Department", value))
		if scope["kind"] == "company":
			return bool(frappe.db.exists("Department", {"name": value, "company": scope["company"]}))
		return False
	return False


def resolve_filters(entry, raw, scope):
	"""Allowlist ``raw`` against the entry's filter specs.

	Returns ``(clean, filters_removed)``. Keys the entry does not declare are
	dropped. A non-string entity value (an operator-shaped list, a dict) is
	refused with a ValidationError before anything runs. A plain string
	outside the caller's scope is reported in ``filters_removed`` -- the
	caller then returns an empty result, never a widened one (resolved
	decision 8).
	"""
	if isinstance(raw, str):
		raw = frappe.parse_json(raw) if raw else {}
	if raw is None:
		raw = {}
	if not isinstance(raw, dict):
		frappe.throw(_("Invalid filters."))

	clean, removed = {}, []
	for spec in entry["filters"]:
		name, kind = spec["name"], spec["type"]
		value = raw.get(name)
		if value in (None, ""):
			default = spec["default"]
			value = default() if callable(default) else default
			if value in (None, ""):
				if spec["reqd"]:
					frappe.throw(_("Choose {0}.").format(_(spec["label"])))
				continue
		if kind == "toggle":
			if not isinstance(value, bool | int | str):
				frappe.throw(_("Invalid {0} filter.").format(_(spec["label"])))
			clean[name] = 1 if cint(value) else 0
			continue
		if not isinstance(value, str):
			frappe.throw(_("Invalid {0} filter.").format(_(spec["label"])))
		if kind in _ENTITY_TYPES:
			if not _entity_in_scope(kind, value, scope):
				removed.append(name)
				continue
		elif kind == "date":
			try:
				value = str(getdate(value))
			except Exception:
				frappe.throw(_("Invalid {0} filter.").format(_(spec["label"])))
		elif kind == "month":
			if not _MONTH_RE.match(value):
				frappe.throw(_("Invalid {0} filter.").format(_(spec["label"])))
		elif kind == "select":
			if value not in spec["options"]:
				frappe.throw(_("Invalid {0} filter.").format(_(spec["label"])))
		elif kind == "select_link":
			# Global masters (no company field): existence is the whole check.
			if not frappe.db.exists(_SELECT_LINK_DOCTYPES[name], value):
				removed.append(name)
				continue
		clean[name] = value
	return clean, removed


# --- filter options (U3) ----------------------------------------------------
#
# `search_options` serves the EntityPicker typeahead. It answers only for
# filter types the entry declares, and only inside the scope
# `resolve_report_access` granted for THIS report -- never a wider one.
# Designation, Branch and Employment Type are global masters with no company
# field, so they are served unscoped: their names are not personal data and
# the report itself is still company-scoped.

OPTIONS_LIMIT = 20
# U7: what "active" means per picker doctype. Browse lists only these; a
# typed query lists them first, then the inactive matches.
_ACTIVE_FILTERS = {
	"Project": {"status": "Open"},
	"Task": {"status": ["not in", ("Completed", "Cancelled")]},
	"Department": {"disabled": 0},
}
_INACTIVE_FILTERS = {
	"Project": {"status": ["!=", "Open"]},
	"Task": {"status": ["in", ("Completed", "Cancelled")]},
	"Department": {"disabled": 1},
}
_OPTIONS_QUERY_MAX = 60
_SELECT_LINK_DOCTYPES = {
	"designation": "Designation",
	"branch": "Branch",
	"employment_type": "Employment Type",
}


def _scope_projects(scope):
	"""Project names in scope, or None for "every project" (unscoped)."""
	filters = project_scope_filters(scope)
	if filters is None:
		return []
	if not filters:
		return None
	if "name" in filters:
		return list(filters["name"][1])
	return frappe.get_all("Project", filters=filters, pluck="name")


def _option_source(spec, scope, context):
	"""(doctype, scope filters | None, label field, description field,
	search fields) for one filter spec. None filters = nothing in scope."""
	kind = spec["type"]
	if kind == "employee":
		if scope["kind"] == "assigned":
			projects = _scope_projects(scope)
			users = (
				frappe.get_all("Project User", filters={"parent": ["in", projects]}, pluck="user")
				if projects
				else []
			)
			filters = {"user_id": ["in", users]} if users else None
		else:
			filters = admin_scope_employee_filters(scope)
		return (
			"Employee",
			filters,
			"employee_name",
			"designation",
			("name", "employee_name", "employee_number"),
		)
	if kind == "project":
		return "Project", project_scope_filters(scope), "project_name", "status", ("name", "project_name")
	if kind == "task":
		projects = _scope_projects(scope)
		filters = None if projects == [] else ({} if projects is None else {"project": ["in", projects]})
		wanted = context.get("project") if isinstance(context, dict) else None
		if filters is not None and isinstance(wanted, str) and wanted:
			filters = {"project": wanted} if project_in_scope(wanted, scope) else None
		return "Task", filters, "subject", "project", ("name", "subject")
	if kind == "department":
		filters = admin_scope_employee_filters(scope) if scope["kind"] != "assigned" else None
		return "Department", filters, "department_name", "company", ("name", "department_name")
	doctype = _SELECT_LINK_DOCTYPES[spec["name"]]
	return doctype, {}, "name", None, ("name",)


def search_options(entry, filter_name, scope, query=None, value=None, context=None):
	"""Up to `OPTIONS_LIMIT` ``{value, label, description}`` for one picker.

	An empty ``query`` browses (plan 2026-10-05-001 U7, KTD7): active rows
	only (`_ACTIVE_FILTERS`), most recently modified first. A typed ``query``
	also matches inactive rows (a Completed project), listed after the
	active matches. ``value``
	(instead of ``query``) resolves the label of one already-chosen value --
	the URL-load case -- and returns ``[]`` when it is out of scope.
	A filter the entry does not declare, or one with no picker, is refused.
	"""
	spec = next((s for s in entry["filters"] if s["name"] == filter_name), None)
	if not spec or spec["type"] not in _ENTITY_TYPES | {"select_link"}:
		frappe.throw(_("That filter has no options here."))
	if isinstance(context, str):
		context = frappe.parse_json(context) if context else {}

	doctype, filters, label_field, description_field, search_fields = _option_source(spec, scope, context)
	if filters is None:
		return []

	fields = list(dict.fromkeys(["name", label_field] + ([description_field] if description_field else [])))

	def fetch(row_filters, or_filters=None, order_by=f"{label_field} asc", limit=OPTIONS_LIMIT):
		return frappe.get_all(
			doctype,
			filters=row_filters,
			or_filters=or_filters,
			fields=fields,
			order_by=order_by,
			limit=limit,
			ignore_permissions=True,
		)

	if value not in (None, ""):
		if not isinstance(value, str):
			return []
		rows = fetch({**filters, "name": value})
	else:
		needle = query.strip()[:_OPTIONS_QUERY_MAX] if isinstance(query, str) else ""
		if doctype == "Employee":
			filters = {**filters, "status": "Active"}
		active = {**filters, **_ACTIVE_FILTERS.get(doctype, {})}
		if not needle:
			rows = fetch(active, order_by="modified desc")
		else:
			or_filters = [[field, "like", f"%{needle}%"] for field in search_fields]
			rows = fetch(active, or_filters)
			if doctype in _INACTIVE_FILTERS and len(rows) < OPTIONS_LIMIT:
				rows += fetch(
					{**filters, **_INACTIVE_FILTERS[doctype]}, or_filters, limit=OPTIONS_LIMIT - len(rows)
				)
	return [
		{
			"value": row.name,
			"label": row.get(label_field) or row.name,
			"description": (row.get(description_field) if description_field else None) or None,
		}
		for row in rows
	]


# --- catalog for the client (U4) --------------------------------------------


def _client_default(spec):
	default = spec["default"]
	return default() if callable(default) else default


def client_entry(entry, access):
	"""The JSON-safe slice of one catalog entry the Reports page needs.
	The page never decides access; `access` came from the server."""
	return {
		"key": entry["key"],
		"family": entry["family"],
		"label": entry["label"],
		"question": entry["question"],
		"engine": entry["engine"],
		# The HRMS Report name, for `get_report_link`'s Desk hand-off only.
		"report": entry["report"],
		"default_preset": entry["default_preset"],
		"filters": [
			{
				"name": spec["name"],
				"type": spec["type"],
				"label": spec["label"],
				"reqd": spec["reqd"],
				"default": _client_default(spec),
				"options": list(spec["options"]) if spec["options"] else None,
			}
			for spec in entry["filters"]
		],
		"group_by": [{"field": field, "label": frappe.unscrub(field)} for field in entry["group_by"]],
		"can_export": access["can_export"],
	}


def _scope_company(scope, raw):
	"""The company a wrapped report runs for: always the scope's company;
	only an unscoped caller (Administrator, anchorless HR Manager) may name
	one, and otherwise gets the site default."""
	if scope["kind"] == "company":
		return scope["company"]
	requested = raw.get("company") if isinstance(raw, dict) else None
	if isinstance(requested, str) and frappe.db.exists("Company", requested):
		return requested
	return frappe.defaults.get_global_default("company") or frappe.db.get_value("Company", {}, "name")


# --- engines ----------------------------------------------------------------


def _run_frappe_report(entry, clean, scope, raw):
	from frappe.core.utils import ljust_list
	from frappe.desk.query_report import get_column_as_dict, normalize_result

	if scope["kind"] not in ("company", "unscoped"):
		# Wrapped reports have no project scope (KTD3); the resolver never
		# grants one, and this keeps it true if that ever changes.
		return [], []

	engine_filters = {
		spec["maps_to"]: clean[spec["name"]] for spec in entry["filters"] if spec["name"] in clean
	}
	if entry["adapt"]:
		entry["adapt"](engine_filters)
	# Forced last, after `adapt`, so nothing can override it.
	engine_filters["company"] = _scope_company(scope, raw)

	report = frappe.get_doc("Report", entry["report"])
	with as_administrator():
		res = report.execute_module(engine_filters)

	columns, result = ljust_list(list(res or []), 6)[:2]
	columns = [get_column_as_dict(column) for column in (columns or [])]
	rows = normalize_result(result or [], columns)
	rows = [row for row in rows if isinstance(row, dict)]
	if entry["post"]:
		columns, rows = entry["post"](columns, rows)
	columns, rows = _employee_names(columns, rows)
	if not entry["shows_amounts"]:
		# R3: Currency columns leave wrapped output unless the entry is
		# declared to show expense amounts.
		money = {column["fieldname"] for column in columns if column.get("fieldtype") == "Currency"}
		if money:
			columns = [column for column in columns if column["fieldname"] not in money]
			rows = [{k: v for k, v in row.items() if k not in money} for row in rows]
	return columns, rows


def _execute(entry, clean, scope, raw):
	"""Run the engine; turn an engine failure into one plain sentence and a
	logged error rather than a 500."""
	try:
		if entry["engine"] == "frappe":
			return _run_frappe_report(entry, clean, scope, raw)
		return entry["query"](clean, scope)
	except frappe.ValidationError as error:
		frappe.clear_messages()
		message = frappe.utils.strip_html(str(error)) or _("That report could not run with these filters.")
		frappe.throw(message, title=_("Report"))
	except Exception:
		frappe.log_error(title=f"HelixHR report failed: {entry['key']}")
		frappe.throw(_("That report could not run. Try different filters, or try again later."))


# --- shaper -----------------------------------------------------------------


def _sort_key(value):
	if value is None or value == "":
		return (1, 0, "")
	if isinstance(value, int | float):
		return (0, 0, value)
	return (0, 1, str(value).lower())


def _sums(rows, fields):
	return {field: flt(sum(flt(row.get(field)) for row in rows), 2) for field in fields}


def shape(columns, rows, group_by=(), sort=None, totals=None):
	"""KTD7: one shaper for screen and every export.

	Rounds each Float/Currency value to two places once, per row, before
	anything is summed (R10), so subtotals and the grand total always equal
	the sum of the rows shown. Sorts (``sort`` = ``{"field", "order"}``),
	then groups by up to two fields with a subtotal row after each group.

	Returns ``{"rows", "totals", "total_rows"}``; every row carries
	``_kind`` = ``row`` / ``subtotal`` / ``total``, and the last row is the
	grand total.
	"""
	fieldnames = [column["fieldname"] for column in columns]
	rounded = [c["fieldname"] for c in columns if c.get("fieldtype") in _ROUNDED_FIELDTYPES]
	if totals is None:
		totals = [c["fieldname"] for c in columns if c.get("fieldtype") in NUMERIC_FIELDTYPES]
	totals = [field for field in totals if field in fieldnames]
	# Plan 2026-10-05-001 U8 (R14): a subtotal row says so in the first text
	# column that is not a group field (else the group columns label it).
	label_field = next(
		(
			c["fieldname"]
			for c in columns
			if c["fieldname"] not in group_by and c.get("fieldtype") in _TEXT_FIELDTYPES
		),
		None,
	)

	data = []
	for row in rows:
		row = dict(row)
		for field in rounded:
			if row.get(field) not in (None, ""):
				row[field] = flt(row[field], 2)
		data.append(row)

	if sort and sort.get("field") in fieldnames:
		data.sort(key=lambda row: _sort_key(row.get(sort["field"])), reverse=sort.get("order") == "desc")
	for field in reversed(group_by):
		data.sort(key=lambda row, field=field: _sort_key(row.get(field)))

	out = []

	def emit(group_rows, fields, level, parents):
		if not fields:
			out.extend({**row, "_kind": "row"} for row in group_rows)
			return
		field = fields[0]
		for value, members in itertools.groupby(group_rows, key=lambda row: row.get(field)):
			members = list(members)
			emit(members, fields[1:], level + 1, {**parents, field: value})
			out.append(
				{
					**parents,
					field: value,
					**_sums(members, totals),
					**({label_field: _("Subtotal")} if label_field else {}),
					"_kind": "subtotal",
					"_level": level,
					"_group_field": field,
					"_count": len(members),
				}
			)

	emit(data, list(group_by), 0, {})
	grand = _sums(data, totals)
	out.append({**grand, "_kind": "total", "_count": len(data)})
	return {"rows": out, "totals": grand, "total_rows": len(data)}


def cap_rows(shaped_rows, cap=SCREEN_ROW_CAP):
	"""Cut shaped output to at most ``cap`` data rows for the screen (resolved
	decision 7): at the last top-level group boundary that fits, else the
	next level down, else a plain cut. The grand total row is always kept,
	since totals cover every row. Returns ``(rows, truncated)``."""
	body, total = shaped_rows[:-1], shaped_rows[-1:]
	if sum(1 for row in body if row["_kind"] == "row") <= cap:
		return shaped_rows, False

	best = {}  # level -> index after the last fitting subtotal at that level
	count = 0
	for index, row in enumerate(body):
		if row["_kind"] == "row":
			count += 1
			if count > cap:
				break
		elif row["_kind"] == "subtotal" and count <= cap:
			best[row["_level"]] = index + 1
	if best:
		return body[: best[min(best)]] + total, True

	cut, count = [], 0
	for row in body:
		if row["_kind"] == "row":
			count += 1
			if count > cap:
				break
			cut.append(row)
	return cut + total, True


# --- runner -----------------------------------------------------------------


def _parse(value, default):
	if isinstance(value, str):
		value = frappe.parse_json(value) if value else default
	return default if value is None else value


def run(report_key, scope, filters=None, group_by=None, sort=None):
	"""Run one catalog report for an ALREADY-AUTHORISED caller and shape it.
	`api.run_report` is the only caller; it resolved access first."""
	entry = get_entry(report_key)
	raw = _parse(filters, {})
	group_by = _parse(group_by, [])
	sort = _parse(sort, None)

	if not isinstance(group_by, list) or len(group_by) > 2 or len(set(map(str, group_by))) != len(group_by):
		frappe.throw(_("Group by at most two different fields."))
	for field in group_by:
		if field not in entry["group_by"]:
			frappe.throw(_("This report cannot be grouped that way."))
	if sort is not None and not (
		isinstance(sort, dict) and isinstance(sort.get("field"), str) and sort.get("order") in ("asc", "desc")
	):
		frappe.throw(_("Invalid sort."))

	if entry["fixed_group_by"] is not None:
		group_by = list(entry["fixed_group_by"])

	clean, removed = resolve_filters(entry, raw, scope)
	if removed:
		columns, rows = [], []
		if entry["engine"] == "helixhr":
			# Columns stay stable for the screen; no query runs.
			columns, _rows = entry["query"]({}, {"kind": "none"})
	else:
		columns, rows = _execute(entry, clean, scope, raw)

	shaped = shape(columns, rows, group_by, sort, entry["totals"])
	return {
		"columns": columns,
		"shaped": shaped,
		"groups_applied": group_by,
		"filters_removed": removed,
		"filters": clean,
		"extra": entry["extra"](clean, scope, rows) if entry["extra"] and not removed else None,
	}


# --- exporters (U5, KTD8/KTD9) ----------------------------------------------
#
# Every export renders `run`'s shaped output -- the same rows and totals the
# screen shows (KTD7). Callers resolved access with ``export_scope`` first.

_CONTENT_TYPES = {
	"csv": "text/csv; charset=utf-8",
	"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
	"pdf": "application/pdf",
}
_PDF_LANDSCAPE_COLUMNS = 6
_UNSAFE_LETTER_HEAD_TAGS = ("script", "iframe", "object", "embed", "link", "meta", "base", "form")


def export_mode(fmt, total_rows):
	"""``inline`` / ``background`` / ``refused`` for one format and row count."""
	if total_rows <= INLINE_EXPORT_CAP[fmt]:
		return "inline"
	if total_rows <= BACKGROUND_EXPORT_CAP[fmt]:
		return "background"
	return "refused"


def visible_columns(columns, hidden):
	"""Columns minus the ones hidden on screen (resolved decision 10)."""
	hidden = set(hidden) if isinstance(hidden, list | tuple) else set()
	return [column for column in columns if column["fieldname"] not in hidden]


def _period(filters):
	if filters.get("from_date") and filters.get("to_date"):
		return f"{filters['from_date']}_{filters['to_date']}"
	return filters.get("month") or filters.get("date") or ""


def export_filename(entry, filters, fmt, at=None):
	"""``helixhr_<key>_<period>_<YYYYMMDDTHHMM>.<ext>``: ASCII, no person names
	(the key and period are the only inputs)."""
	at = at or frappe.utils.now_datetime()
	parts = ["helixhr", entry["key"], _period(filters), at.strftime("%Y%m%dT%H%M")]
	name = "_".join(part for part in parts if part)
	return re.sub(r"[^A-Za-z0-9_.-]", "-", name) + f".{fmt}"


def _filter_lines(entry, filters):
	lines = []
	for spec in entry["filters"]:
		if spec["name"] not in filters:
			continue
		value = filters[spec["name"]]
		if spec["type"] == "toggle":
			value = _("Yes") if value else _("No")
		lines.append((_(spec["label"]), str(value)))
	return lines


def export_meta(entry, result, scope, raw=None):
	"""Title block shared by Excel and PDF: report, company, period, filters,
	who generated it and when (with the site's time zone)."""
	from frappe.utils import get_system_timezone

	company = _scope_company(scope, raw or {}) if scope["kind"] != "assigned" else None
	return {
		"title": _(entry["label"]),
		"company": company,
		"period": _period(result["filters"]).replace("_", " to "),
		"filters": _filter_lines(entry, result["filters"]),
		"group_by": [frappe.unscrub(field) for field in result["groups_applied"]],
		"generated_by": frappe.utils.get_fullname(frappe.session.user),
		"generated_at": f"{frappe.utils.now_datetime().strftime('%Y-%m-%d %H:%M')} {get_system_timezone()}",
	}


def export_rows(columns, shaped_rows):
	"""Shaped rows -> ``[(kind, [cell, ...])]`` for the visible columns.
	Subtotal and total rows carry their label in the first column unless that
	column itself holds a total."""
	fields = [column["fieldname"] for column in columns]
	out = []
	for row in shaped_rows:
		kind = row["_kind"]
		cells = [row.get(field) for field in fields]
		if kind != "row" and fields:
			label = (
				_("Total")
				if kind == "total"
				else _("Subtotal: {0}").format(row.get(row["_group_field"]) or _("(none)"))
			)
			if (
				cells[0] in (None, "")
				or fields[0] == row.get("_group_field")
				or (kind == "subtotal" and cells[0] == _("Subtotal"))
			):
				cells[0] = label
		out.append((kind, cells))
	return out


def to_csv(columns, shaped_rows):
	"""Header plus every shaped row; formula-like strings escaped, numbers
	left numeric, a UTF-8 BOM so Excel reads the encoding right."""
	from csv import QUOTE_MINIMAL

	from frappe.desk.utils import get_csv_bytes
	from frappe.utils.csvutils import escape_formula_injection

	data = [[_(column["label"]) for column in columns]]
	for _kind, cells in export_rows(columns, shaped_rows):
		data.append(
			[
				escape_formula_injection(cell)
				if isinstance(cell, str)
				else ("" if cell is None else cell if isinstance(cell, int | float) else str(cell))
				for cell in cells
			]
		)
	return b"\xef\xbb\xbf" + get_csv_bytes(data, {"delimiter": ",", "quoting": QUOTE_MINIMAL})


def _xlsx_cell(value, fieldtype):
	if value in (None, ""):
		return None
	if fieldtype == "Date":
		try:
			return getdate(value)
		except Exception:
			return str(value)
	if isinstance(value, int | float | str):
		return value
	return str(value)


def _xlsx_sheet(wb, sheet_name, meta, columns, shaped_rows):
	from frappe.utils.xlsxutils import make_xlsx

	title = [[meta["title"]]]
	if meta["company"]:
		title.append([_("Company"), meta["company"]])
	if meta["period"]:
		title.append([_("Period"), meta["period"]])
	title.extend([[label, value] for label, value in meta["filters"]])
	if meta["group_by"]:
		title.append([_("Grouped by"), ", ".join(meta["group_by"])])
	title.append([_("Generated"), f"{meta['generated_at']} by {meta['generated_by']}"])
	title.append([])

	data = [*title, [_(column["label"]) for column in columns]]
	header_index = len(data) - 1
	bold_rows = [0, header_index]
	types = [column.get("fieldtype") for column in columns]
	for kind, cells in export_rows(columns, shaped_rows):
		if kind != "row":
			bold_rows.append(len(data))
		data.append([_xlsx_cell(cell, fieldtype) for cell, fieldtype in zip(cells, types, strict=True)])

	styles = {"styles": [{"bold": True}], "row_styles": {index: (0,) for index in bold_rows}}
	make_xlsx(data, sheet_name, wb=wb, styles=styles)


def to_xlsx(entry, meta, columns, result):
	"""One workbook: title rows above the header, bold subtotal/total rows,
	typed numbers and dates. ``entry["xlsx_sheets"]`` may supply several
	sheets (U7's Grid + Detail)."""
	from io import BytesIO

	from frappe.utils.xlsxutils import xlsxwriter

	sheets = (
		entry["xlsx_sheets"](result, columns)
		if entry["xlsx_sheets"]
		else [(entry["label"], columns, result["shaped"]["rows"])]
	)
	buffer = BytesIO()
	wb = xlsxwriter.Workbook(buffer, {"constant_memory": True, "default_date_format": "yyyy-mm-dd"})
	for sheet_name, sheet_columns, sheet_rows in sheets:
		_xlsx_sheet(wb, sheet_name, meta, sheet_columns, sheet_rows)
	wb.close()
	return buffer.getvalue()


def _file_data_uri(src):
	"""A site File's content as a data URI, or None. Letter Head images are
	shared branding, so this reads them without the viewer's permission --
	only for File rows that already back a Letter Head or Company logo src."""
	import base64
	import mimetypes
	from urllib.parse import urlparse

	path = urlparse(src or "").path
	mime = mimetypes.guess_type(path)[0]
	if not path or not mime or not mime.startswith("image/"):
		return None
	name = frappe.db.get_value("File", {"file_url": path}, "name")
	if not name:
		return None
	try:
		content = frappe.get_doc("File", name).get_content()
	except Exception:
		return None
	if isinstance(content, str):
		content = content.encode()
	return f"data:{mime};base64,{base64.b64encode(content).decode()}"


def _static_html(html):
	"""KTD9: Letter Head HTML as inert markup. Never evaluated as Jinja;
	scripts and other active tags dropped; ``on*`` attributes removed; every
	image inlined as a data URI (or dropped), so the PDF makes no fetches."""
	from bs4 import BeautifulSoup
	from markupsafe import Markup

	if not html:
		return None
	soup = BeautifulSoup(html, "html.parser")
	for tag in soup.find_all(_UNSAFE_LETTER_HEAD_TAGS):
		tag.decompose()
	for tag in soup.find_all(True):
		for attr in [a for a in tag.attrs if a.lower().startswith("on")]:
			del tag[attr]
	for img in soup.find_all("img"):
		src = img.get("src") or ""
		inlined = src if src.startswith("data:image/") else _file_data_uri(src)
		if inlined:
			img["src"] = inlined
		else:
			img.decompose()
	# Sanitised above, and inserted as a value -- never rendered as a template.
	return Markup(str(soup))


def resolve_letter_head(company):
	"""KTD9 order: the company's default Letter Head, then the site default,
	then the company logo plus name, then the name alone.
	Returns ``{"header", "footer", "logo", "company"}``."""
	name = (company and frappe.db.get_value("Company", company, "default_letter_head")) or None
	if not name:
		name = frappe.db.get_value("Letter Head", {"is_default": 1, "disabled": 0}, "name")
	if name:
		lh = frappe.db.get_value(
			"Letter Head", name, ["source", "content", "image", "footer", "disabled"], as_dict=True
		)
		if lh and not lh.disabled:
			header = (
				f'<img src="{frappe.utils.escape_html(lh.image)}" style="max-height:80px">'
				if lh.source == "Image" and lh.image
				else lh.content
			)
			# Jinja in a Letter Head is never evaluated here (KTD9); rather
			# than print it raw, that part falls back -- the header to logo
			# plus company name, the footer to nothing.
			footer = None if _has_jinja(lh.footer) else _static_html(lh.footer)
			if not _has_jinja(header):
				return {
					"header": _static_html(header),
					"footer": footer,
					"logo": None,
					"company": company,
				}
			return _logo_letter_head(company, footer)
	return _logo_letter_head(company, None)


def _has_jinja(html):
	return bool(html) and ("{%" in html or "{{" in html)


def _logo_letter_head(company, footer):
	logo = company and frappe.db.get_value("Company", company, "company_logo")
	return {
		"header": None,
		"footer": footer,
		"logo": _file_data_uri(logo) if logo else None,
		"company": company,
	}


def _pdf_env():
	import os

	from jinja2 import Environment, FileSystemLoader, select_autoescape

	# Own autoescaping environment, not frappe.render_template: report cells
	# are untrusted data, and nothing here should reach Frappe's globals.
	return Environment(
		loader=FileSystemLoader(os.path.join(frappe.get_app_path("helixhr"), "templates", "reports")),
		autoescape=select_autoescape(default=True),
	)


def render_pdf_html(entry, meta, columns, shaped_rows, letter_head=None, extra=None):
	"""The PDF's HTML, from ``entry["pdf_template"]`` or ``report.html``.
	``extra`` is the run's ``result["extra"]`` (U7's grid and footnote)."""
	letter_head = letter_head if letter_head is not None else resolve_letter_head(meta["company"])
	template = _pdf_env().get_template(entry["pdf_template"] or "report.html")
	return template.render(
		meta=meta,
		letter_head=letter_head,
		columns=[
			{
				"label": _(column["label"]),
				"numeric": column.get("fieldtype") in NUMERIC_FIELDTYPES,
			}
			for column in columns
		],
		rows=[
			{"kind": kind, "cells": ["" if cell is None else cell for cell in cells]}
			for kind, cells in export_rows(columns, shaped_rows)
		],
		extra=extra or {},
	)


def pdf_options(entry, meta, columns, has_header):
	landscape = entry["orientation"] == "landscape" or len(columns) > _PDF_LANDSCAPE_COLUMNS
	options = {
		"orientation": "Landscape" if landscape else "Portrait",
		"page-size": "A4",
		"margin-bottom": "18mm",
		"footer-font-size": "8",
		"footer-spacing": "5",
		"footer-left": _("Generated by {0}, {1}").format(meta["generated_by"], meta["generated_at"]),
		# wkhtmltopdf substitutes [page]/[topage]; footer JS is disabled (KTD8).
		"footer-right": _("Page [page] of [topage]"),
	}
	if has_header:
		options.update({"margin-top": "38mm", "header-spacing": "4"})
	return options


def to_pdf(entry, meta, columns, shaped_rows, extra=None):
	"""KTD8: server-rendered HTML through wkhtmltopdf. A missing or failing
	generator is one plain sentence for the user and a logged error."""
	from frappe.utils.pdf import get_pdf

	letter_head = resolve_letter_head(meta["company"])
	html = render_pdf_html(entry, meta, columns, shaped_rows, letter_head, extra)
	try:
		return get_pdf(html, pdf_options(entry, meta, columns, bool(letter_head["header"])))
	except Exception:
		frappe.log_error(title=f"HelixHR report PDF failed: {entry['key']}")
		frappe.throw(
			_("PDF export isn't available right now. Try Excel or CSV, or ask HR to check the site.")
		)


def build_export(entry, result, fmt, scope, raw=None, hidden=None):
	"""``(content bytes, filename, content type)`` for one run `result`."""
	columns = visible_columns(result["columns"], hidden)
	if fmt == "csv":
		rows = result["shaped"]["rows"]
		if entry["csv_detail_only"]:
			rows = [row for row in rows if row["_kind"] == "row"]
		content = to_csv(columns, rows)
	else:
		meta = export_meta(entry, result, scope, raw)
		if fmt == "xlsx":
			content = to_xlsx(entry, meta, columns, result)
		else:
			content = to_pdf(entry, meta, columns, result["shaped"]["rows"], result.get("extra"))
	return content, export_filename(entry, result["filters"], fmt), _CONTENT_TYPES[fmt]


# --- background export job (U13) -------------------------------------------


class _ExportRefused(Exception):
	"""A background export that may no longer run; the message is for the user."""


def run_background_export(export, filters=None, group_by=None, sort=None, hidden=None):
	"""Build one queued export (enqueued by `api.request_export`).

	Runs as the requester and re-resolves their access now -- a grant revoked
	since the request ends the export Failed with no file. On success the
	file is a private File attached to the owner-only export row, which is
	what keeps it downloadable by the requester alone. Either way the
	requester gets a Notification Log routed to Reports."""
	from helixhr.utils import resolve_report_access

	row = frappe.db.get_value(
		"HelixHR Report Export", export, ["name", "owner", "report_key", "format", "status"], as_dict=True
	)
	if not row or row.status != "Queued":
		return
	original_user = frappe.session.user
	frappe.set_user(row.owner)
	try:
		frappe.db.set_value("HelixHR Report Export", export, "status", "Running")
		frappe.db.commit()  # nosemgrep -- the "My exports" panel shows Running
		try:
			access = resolve_report_access(row.owner, row.report_key)
			if not access["can_export"]:
				raise _ExportRefused(_("You no longer have access to export this report."))
			entry = get_entry(row.report_key)
			scope = access["export_scope"]
			result = run(row.report_key, scope, filters, group_by, sort)
			total_rows = result["shaped"]["total_rows"]
			if export_mode(row.format, total_rows) == "refused":
				raise _ExportRefused(_("This export is too large. Narrow the filters and try again."))
			content, filename, _content_type = build_export(
				entry, result, row.format, scope, _parse(filters, {}), hidden
			)
			file = frappe.get_doc(
				{
					"doctype": "File",
					"file_name": filename,
					"is_private": 1,
					"content": content,
					"attached_to_doctype": "HelixHR Report Export",
					"attached_to_name": export,
				}
			).insert(ignore_permissions=True)
			frappe.db.set_value(
				"HelixHR Report Export",
				export,
				{"status": "Ready", "file": file.file_url, "file_name": filename, "row_count": total_rows},
			)
			_notify_export(row, entry["label"], ready=True)
			frappe.db.commit()  # nosemgrep -- background job owns its transaction
		except Exception as failure:
			frappe.db.rollback()
			if isinstance(failure, _ExportRefused | frappe.ValidationError):
				message = str(failure)
			else:
				frappe.log_error(f"HelixHR report export {export} failed")
				message = _("The export could not be prepared. Try again.")
			frappe.db.set_value("HelixHR Report Export", export, {"status": "Failed", "error": message})
			entry = get_entry(row.report_key)
			_notify_export(row, entry["label"] if entry else row.report_key, ready=False)
			frappe.db.commit()  # nosemgrep
	finally:
		frappe.set_user(original_user)


def _notify_export(row, label, ready):
	frappe.get_doc(
		{
			"doctype": "Notification Log",
			"for_user": row.owner,
			"type": "Alert",
			"document_type": "HelixHR Report Export",
			"document_name": row.name,
			"subject": (
				_("Your export of {0} is ready to download.") if ready else _("Your export of {0} failed.")
			).format(frappe.utils.escape_html(label)),
		}
	).insert(ignore_permissions=True)
