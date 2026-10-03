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

from helixhr.utils import as_administrator, employee_in_admin_scope, project_in_scope, project_scope_filters

# KTD14. Screen cap; the export caps land with U5/U13.
SCREEN_ROW_CAP = 2000

# KTD3: never offered, and preflight FAILs if one enters the catalog.
DENY_LIST = frozenset({"Timesheet Billing Summary", "Project Profitability", "Project-wise Stock Tracking"})

# Timesheet workflow states that count as "awaiting approval" (resolved
# decision 6) -- not every docstatus 0 row; Draft and Sent Back are not pending.
PENDING_TIMESHEET_STATES = ("Pending Approval", "Pending HR")

NUMERIC_FIELDTYPES = frozenset({"Int", "Float", "Currency", "Duration"})
_ROUNDED_FIELDTYPES = frozenset({"Float", "Currency"})
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
	"td.task as task",
	"tsk.subject as task_subject",
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
		{"fieldname": "task", "label": "Task", "fieldtype": "Link", "options": "Task"},
		{"fieldname": "task_subject", "label": "Task subject", "fieldtype": "Data"},
		{"fieldname": "hours", "label": "Hours", "fieldtype": "Float"},
		{"fieldname": "billing_hours", "label": "Billable hours", "fieldtype": "Float"},
	]
	fields = list(_HOURS_FIELDS)
	values = {"pending_states": PENDING_TIMESHEET_STATES}
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
		where {" and ".join(conditions)}
		order by `date` desc, ts.employee asc, td.idx asc
		""",
		values,
		as_dict=True,
	)
	return columns, rows


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
#   shows_amounts (keep Currency columns), default_grants (seed patch).
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
	}
	entry.update(extra)
	return entry


_HR_USER_RUN = {"hr_user_run": 1}

CATALOG = (
	_entry(
		"hours_by_project",
		"time",
		"Hours by project",
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
		group_by=("employee", "project", "task", "date"),
		totals=("hours", "billing_hours"),
		scopes=("company", "project"),
		default_grants={"dm_run": 1, "dm_export": 1},
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
		clean[name] = value
	return clean, removed


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
	}
