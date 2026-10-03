"""Plan 2026-10-04-001 U2: `run_report`, which replaced P7-U8's
`run_portal_report` and `get_billable_hours` (KTD13). Every security case
those two carried is ported here first: scope narrowing on the HelixHR
hours query, operator-shaped filters, ignored kwargs, run-as, forced
company, money columns, uniform refusal. Plus the wrapped HRMS reports'
two-company isolation (resolved decision 1).
"""

import hashlib
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, add_to_date, cint, get_datetime, getdate

from helixhr import preflight, reports
from helixhr.api import run_report
from helixhr.tests.utils import (
	ensure_baseline_company,
	ensure_test_company,
	make_test_delivery_manager,
	make_test_employee_and_manager,
	make_test_hr_manager_employee,
	make_test_hr_user,
	make_test_project,
	make_test_report_manager,
	make_test_user,
	set_report_access,
)
from helixhr.utils import RATE_LIMIT_POLICY


def _rows(result):
	return [row for row in result["rows"] if row["_kind"] == "row"]


def _hours(**filters):
	return _rows(run_report("hours_by_project", filters=filters))


def _ensure_task(project, subject):
	"""Idempotent, like `make_test_project` -- `IntegrationTestCase` rolls
	back once per class here, not per method, so a fixed subject reused
	across this file's test methods must find an earlier method's row
	rather than collide with it."""
	existing = frappe.db.get_value("Task", {"project": project, "subject": subject}, "name")
	if existing:
		return existing
	doc = frappe.get_doc({"doctype": "Task", "project": project, "subject": subject, "status": "Open"})
	doc.insert(ignore_permissions=True)
	return doc.name


def _make_timesheet(company, employee, employee_name, rows, state="Approved"):
	"""Insert a Timesheet directly -- not through `save_my_week`, which this
	unit is not testing -- with `rows` laid out end to end from midnight per
	day, the same non-overlap trick `_write_my_week` uses (helixhr/api.py),
	so ERPNext's own `update_billing_hours`/`update_cost` run exactly as
	they would through the real writer (the behaviour P7-U6 already
	verified: billable hours persist, money stays zero with no Activity
	Cost record)."""
	doc = frappe.new_doc("Timesheet")
	doc.employee = employee
	doc.employee_name = employee_name
	doc.company = company
	day_offset = {}
	for row in rows:
		date = str(row["date"])
		hours = row.get("hours", 1)
		start = add_to_date(get_datetime(f"{date} 00:00:00"), hours=day_offset.get(date, 0))
		day_offset[date] = day_offset.get(date, 0) + hours
		doc.append(
			"time_logs",
			{
				"project": row["project"],
				"task": row.get("task"),
				"activity_type": "General",
				"from_time": start,
				"to_time": add_to_date(start, hours=hours),
				"hours": hours,
				"is_billable": cint(row.get("is_billable", 0)),
				"description": row.get("note", "worked"),
			},
		)
	doc.insert(ignore_permissions=True)
	# The workflow is not under test: set the end state directly. "Approved"
	# is the submitted state (docstatus 1); anything else stays docstatus 0.
	docstatus = 1 if state == "Approved" else 0
	frappe.db.set_value("Timesheet", doc.name, {"workflow_state": state, "docstatus": docstatus})
	frappe.db.set_value("Timesheet Detail", {"parent": doc.name}, "docstatus", docstatus)
	return doc


# --- hours_by_project: ported from get_billable_hours (KTD3) ----------------


class TestHoursByProject(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.other_company = ensure_baseline_company()
		self.employee_name, self.employee_user, _, _ = make_test_employee_and_manager()
		self.hr_employee, self.hr_user = make_test_hr_manager_employee()
		self.dm_employee, self.dm_user = make_test_delivery_manager()
		set_report_access("hours_by_project", dm_run=1, dm_export=1)

		method_name = self.id().split(".")[-1]
		self.member_project = make_test_project(
			self.company, f"_Test U8 Member {method_name}", members=[self.dm_user]
		)
		self.other_project = make_test_project(self.company, f"_Test U8 Other {method_name}")
		self.other_company_project = make_test_project(self.other_company, f"_Test U8 OtherCo {method_name}")
		self.task = _ensure_task(self.member_project, f"_Test U8 Task {method_name}")
		self.other_task = _ensure_task(self.member_project, f"_Test U8 Other Task {method_name}")

		# A distinct day per method: ERPNext's overlap check spans every
		# Timesheet of the shared fixture employees.
		digest = int(hashlib.md5(self.id().encode()).hexdigest(), 16)
		self.today = str(add_days(frappe.utils.today(), digest % 3000))
		self.window = {"from_date": self.today, "to_date": self.today}

	def tearDown(self):
		frappe.set_user("Administrator")

	def _sheet(self, company, employee, project, hours=1, **extra):
		return _make_timesheet(
			company,
			employee,
			"Someone",
			[{"date": self.today, "project": project, "hours": hours, **extra}],
		)

	def test_hr_manager_receives_only_their_own_company_rows(self):
		self._sheet(self.company, self.employee_name, self.member_project, 2)
		self._sheet(self.other_company, self.hr_employee, self.other_company_project, 3)
		frappe.set_user(self.hr_user)
		projects = {row["project"] for row in _hours(**self.window)}
		self.assertIn(self.member_project, projects)
		self.assertNotIn(self.other_company_project, projects)

	def test_a_plain_employee_is_refused(self):
		frappe.set_user(self.employee_user)
		with self.assertRaises(frappe.PermissionError):
			run_report("hours_by_project")

	def test_delivery_manager_gets_only_member_project_rows_even_naming_another_project(self):
		self._sheet(self.company, self.dm_employee, self.member_project, 2)
		self._sheet(self.company, self.employee_name, self.other_project, 5)
		frappe.set_user(self.dm_user)
		result = run_report("hours_by_project", filters={"project": self.other_project, **self.window})
		self.assertEqual(_rows(result), [])
		self.assertEqual(result["filters_removed"], ["project"])
		projects = {row["project"] for row in _hours(**self.window)}
		self.assertIn(self.member_project, projects)
		self.assertNotIn(self.other_project, projects)

	def test_hr_manager_naming_another_companys_project_gets_nothing(self):
		self._sheet(self.company, self.employee_name, self.member_project, 2)
		self._sheet(self.other_company, self.hr_employee, self.other_company_project, 4)
		frappe.set_user(self.hr_user)
		self.assertEqual(_hours(project=self.other_company_project, **self.window), [])

	def test_filtering_by_task_returns_only_that_tasks_rows(self):
		_make_timesheet(
			self.company,
			self.employee_name,
			"Emp",
			[
				{"date": self.today, "project": self.member_project, "task": self.task, "hours": 1},
				{"date": self.today, "project": self.member_project, "task": self.other_task, "hours": 1},
			],
		)
		frappe.set_user(self.hr_user)
		rows = _hours(task=self.task, **self.window)
		self.assertTrue(rows)
		self.assertTrue(all(row["task"] == self.task for row in rows))

	def test_filtering_by_employee_returns_only_that_employees_rows(self):
		self._sheet(self.company, self.employee_name, self.member_project)
		self._sheet(self.company, self.hr_employee, self.member_project)
		frappe.set_user(self.hr_user)
		rows = _hours(employee=self.employee_name, **self.window)
		self.assertTrue(rows)
		self.assertTrue(all(row["employee"] == self.employee_name for row in rows))

	def test_returned_columns_carry_no_rate_amount_or_cost_column(self):
		self._sheet(self.company, self.employee_name, self.member_project, is_billable=1)
		frappe.set_user(self.hr_user)
		result = run_report("hours_by_project", filters={**self.window, "include_pending": 1})
		money = {"billing_rate", "billing_amount", "costing_rate", "costing_amount"}
		self.assertFalse(money & {column["fieldname"] for column in result["columns"]})
		self.assertTrue(_rows(result))
		for row in result["rows"]:
			self.assertFalse(money & set(row), row.keys())

	def test_an_unrecognised_filter_key_and_a_run_as_key_are_ignored(self):
		self._sheet(self.company, self.employee_name, self.member_project)
		frappe.set_user(self.hr_user)
		normal = _hours(**self.window)
		self.assertTrue(normal)
		self.assertEqual(normal, _hours(some_unknown_filter="x", **self.window))
		impersonating = run_report(
			"hours_by_project", filters=self.window, user="Administrator", as_user="Administrator"
		)
		self.assertEqual(normal, _rows(impersonating))
		self.assertEqual(normal, _hours(user="Administrator", **self.window))

	def test_an_operator_shaped_project_filter_is_refused(self):
		frappe.set_user(self.hr_user)
		with self.assertRaises(frappe.ValidationError):
			run_report("hours_by_project", filters={"project": ["in", [self.member_project]]})

	def test_an_empty_scope_returns_an_empty_result(self):
		for project in frappe.get_all("Project User", filters={"user": self.dm_user}, pluck="parent"):
			frappe.db.delete("Project User", {"user": self.dm_user, "parent": project})
		frappe.set_user(self.dm_user)
		self.assertEqual(_hours(**self.window), [])

	def test_a_caller_who_cannot_reach_desk_still_gets_rows(self):
		from helixhr.api import _can_open_desk

		self.assertFalse(_can_open_desk(self.dm_user))
		self._sheet(self.company, self.dm_employee, self.member_project)
		frappe.set_user(self.dm_user)
		self.assertTrue(_hours(**self.window))

	def test_approved_only_by_default_and_pending_marked_when_included(self):
		"""Resolved decision 6: pending = Pending Approval / Pending HR;
		Draft never counts."""
		self._sheet(self.company, self.employee_name, self.member_project, 1)
		_make_timesheet(
			self.company,
			self.hr_employee,
			"Pending",
			[{"date": self.today, "project": self.member_project, "hours": 2}],
			state="Pending Approval",
		)
		_make_timesheet(
			self.company,
			self.dm_employee,
			"Draft",
			[{"date": self.today, "project": self.member_project, "hours": 4}],
			state="Draft",
		)
		frappe.set_user(self.hr_user)
		self.assertEqual(sorted(row["hours"] for row in _hours(**self.window)), [1])
		rows = _hours(include_pending=1, **self.window)
		self.assertEqual(
			sorted((row["hours"], row["approval"]) for row in rows), [(1, "Approved"), (2, "Pending")]
		)

	def test_grouping_and_totals(self):
		self._sheet(self.company, self.employee_name, self.member_project, 2)
		self._sheet(self.company, self.hr_employee, self.member_project, 3)
		frappe.set_user(self.hr_user)
		result = run_report("hours_by_project", filters=self.window, group_by=["employee"])
		self.assertEqual(result["totals"]["hours"], 5)
		self.assertEqual(result["total_rows"], 2)
		self.assertEqual(sum(1 for row in result["rows"] if row["_kind"] == "subtotal"), 2)
		self.assertFalse(result["truncated"])
		with self.assertRaises(frappe.ValidationError):
			run_report("hours_by_project", filters=self.window, group_by=["billing_rate"])


# --- wrapped HRMS reports (KTD2, resolved decision 1) ------------------------


class TestRunReportWrapped(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.other_company = ensure_baseline_company()
		_, self.employee_user, _, _ = make_test_employee_and_manager()
		_, self.hr_user = make_test_hr_manager_employee()
		self.own_employee = frappe.db.get_value("Employee", {"user_id": self.employee_user}, "name")
		make_test_user("other-company-report-probe@helixhr.test", self.other_company)
		self.other_employee = frappe.db.get_value(
			"Employee", {"user_id": "other-company-report-probe@helixhr.test"}, "name"
		)

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_deny_listed_unknown_and_denied_keys_share_one_refusal(self):
		frappe.set_user(self.hr_user)
		with self.assertRaises(frappe.PermissionError) as deny_listed:
			run_report("Timesheet Billing Summary")
		with self.assertRaises(frappe.PermissionError) as unknown:
			run_report("not_a_real_report")
		frappe.set_user(self.employee_user)
		with self.assertRaises(frappe.PermissionError) as denied:
			run_report("leave_balance")
		self.assertEqual(str(deny_listed.exception), str(unknown.exception))
		self.assertEqual(str(unknown.exception), str(denied.exception))
		self.assertFalse(set(reports.wrapped_report_names()) & reports.DENY_LIST)

	def test_hr_manager_runs_leave_balance(self):
		frappe.set_user(self.hr_user)
		result = run_report("leave_balance", filters={"employee": self.own_employee})
		self.assertTrue(result["columns"])
		self.assertIn("rows", result)
		self.assertEqual(result["rows"][-1]["_kind"], "total")

	def test_an_operator_shaped_employee_filter_is_rejected_rather_than_forwarded(self):
		frappe.set_user(self.hr_user)
		with patch("frappe.core.doctype.report.report.Report.execute_module") as execute:
			with self.assertRaises(frappe.ValidationError):
				run_report(
					"leave_ledger", filters={"employee": ["in", [self.own_employee, self.other_employee]]}
				)
			execute.assert_not_called()

	def test_an_out_of_scope_employee_narrows_to_nothing_and_says_so(self):
		frappe.set_user(self.hr_user)
		with patch("frappe.core.doctype.report.report.Report.execute_module") as execute:
			result = run_report("leave_ledger", filters={"employee": self.other_employee})
			execute.assert_not_called()
		self.assertEqual(_rows(result), [])
		self.assertEqual(result["filters_removed"], ["employee"])

	def test_company_is_forced_and_desk_runner_is_never_used(self):
		frappe.set_user(self.hr_user)
		with (
			patch("frappe.core.doctype.report.report.Report.execute_module") as execute,
			patch("frappe.desk.query_report.run") as desk_run,
		):
			execute.return_value = ([], [])
			run_report(
				"leave_ledger",
				filters={"employee": self.own_employee, "company": self.other_company},
				user="Administrator",
			)
			desk_run.assert_not_called()
			forwarded = execute.call_args.args[0]
		self.assertEqual(forwarded["company"], self.company)
		self.assertEqual(frappe.session.user, self.hr_user)

	def test_every_wrapped_report_is_isolated_to_the_callers_company(self):
		"""Resolved decision 1: each wrapped report, run by a company-scoped
		tier, returns no row for another company's employee."""
		own_company_employees = set(frappe.get_all("Employee", {"company": self.company}, pluck="name"))
		frappe.set_user(self.hr_user)
		for key in [e["key"] for e in reports.CATALOG if e["engine"] == "frappe"]:
			with patch("frappe.core.doctype.report.report.Report.execute_module", autospec=True) as execute:
				execute.return_value = ([], [])
				run_report(key, filters={"company": self.other_company})
				self.assertEqual(execute.call_args.args[1]["company"], self.company, key)
			result = run_report(key)
			foreign = {row.get("employee") for row in _rows(result)} - own_company_employees - {None}
			self.assertFalse(foreign, f"{key} leaked {foreign}")

	def test_currency_columns_are_stripped_unless_the_entry_shows_amounts(self):
		columns = [
			{"fieldname": "employee", "fieldtype": "Link", "label": "Employee"},
			{"fieldname": "amount", "fieldtype": "Currency", "label": "Amount"},
		]
		data = [{"employee": self.own_employee, "amount": 12.5}]
		frappe.set_user(self.hr_user)
		with patch("frappe.core.doctype.report.report.Report.execute_module", return_value=(columns, data)):
			result = run_report("leave_ledger")
			self.assertNotIn("amount", [c["fieldname"] for c in result["columns"]])
			self.assertNotIn("amount", _rows(result)[0])
			with patch.dict(reports.get_entry("leave_ledger"), {"shows_amounts": True}):
				result = run_report("leave_ledger")
			self.assertIn("amount", [c["fieldname"] for c in result["columns"]])
			self.assertEqual(_rows(result)[0]["amount"], 12.5)

	def test_a_report_that_raises_returns_a_plain_sentence(self):
		frappe.set_user(self.hr_user)
		with patch(
			"frappe.core.doctype.report.report.Report.execute_module",
			side_effect=frappe.ValidationError("Please set a date range less than 90 days."),
		):
			with self.assertRaises(frappe.ValidationError) as raised:
				run_report("leave_ledger")
		self.assertIn("90 days", str(raised.exception))
		with patch("frappe.core.doctype.report.report.Report.execute_module", side_effect=KeyError("x")):
			with patch("frappe.log_error") as log_error:
				with self.assertRaises(frappe.ValidationError) as raised:
					run_report("leave_ledger")
				log_error.assert_called_once()
		self.assertIn("could not run", str(raised.exception))
		self.assertEqual(frappe.session.user, self.hr_user)

	def test_report_manager_without_desk_runs_monthly_attendance(self):
		employee, user = make_test_report_manager()
		date = frappe.utils.today()
		if not frappe.db.exists(
			"Attendance", {"employee": employee, "attendance_date": date, "docstatus": 1}
		):
			attendance = frappe.get_doc(
				{
					"doctype": "Attendance",
					"employee": employee,
					"attendance_date": date,
					"status": "Present",
					"company": self.company,
				}
			)
			attendance.insert(ignore_permissions=True)
			attendance.submit()
		frappe.set_user(user)
		with patch("frappe.desk.query_report.run") as desk_run:
			result = run_report("monthly_attendance", filters={"month": date[:7], "employee": employee})
			desk_run.assert_not_called()
		self.assertTrue(_rows(result))
		self.assertEqual(frappe.session.user, user)

	def test_hr_user_granted_leave_ledger_runs_it_without_report_permission(self):
		_, user = make_test_hr_user()
		set_report_access("leave_ledger", hr_user_run=1)
		frappe.set_user(user)
		result = run_report("leave_ledger")
		self.assertIn("columns", result)
		frappe.set_user("Administrator")
		set_report_access("leave_ledger")
		frappe.set_user(user)
		with self.assertRaises(frappe.PermissionError):
			run_report("leave_ledger")

	def test_employee_directory_returns_only_allowlisted_columns(self):
		frappe.set_user(self.hr_user)
		result = run_report("employee_directory", filters={"status": "Active"})
		allowed = {column[0] for column in reports._DIRECTORY_COLUMNS}
		for row in _rows(result):
			self.assertLessEqual(set(row) - {"_kind"}, allowed)
			self.assertNotEqual(frappe.db.get_value("Employee", row["name"], "company"), self.other_company)

	def test_screen_cap_returns_the_true_total(self):
		frappe.set_user(self.hr_user)
		columns = [
			{"fieldname": "employee", "fieldtype": "Data"},
			{"fieldname": "days", "fieldtype": "Float"},
		]
		data = [{"employee": f"E{i}", "days": 1} for i in range(7)]
		with (
			patch("frappe.core.doctype.report.report.Report.execute_module", return_value=(columns, data)),
			patch("helixhr.reports.cap_rows", side_effect=lambda rows: _cap(rows, 5)),
		):
			result = run_report("leave_ledger")
		self.assertTrue(result["truncated"])
		self.assertEqual(len(_rows(result)), 5)
		self.assertEqual(result["total_rows"], 7)
		self.assertEqual(result["totals"]["days"], 7)

	def test_run_report_is_rate_limited(self):
		self.assertIn("run_report", RATE_LIMIT_POLICY)
		self.assertNotIn("run_portal_report", RATE_LIMIT_POLICY)
		self.assertNotIn("get_billable_hours", RATE_LIMIT_POLICY)


_REAL_CAP = reports.cap_rows


def _cap(rows, cap):
	return _REAL_CAP(rows, cap)


class TestCatalogPreflight(IntegrationTestCase):
	def test_passes_on_this_site(self):
		self.assertIn(preflight.check_curated_reports()["status"], (preflight.PASS, preflight.WARN))

	def _with_report(self, **fields):
		real = frappe.db.get_value

		def fake(doctype, name=None, fieldname=None, *args, **kwargs):
			if doctype == "Report" and name == "Leave Ledger":
				row = real(doctype, name, fieldname, *args, **kwargs)
				if row is None or not fields:
					return None
				row.update(fields)
				return row
			return real(doctype, name, fieldname, *args, **kwargs)

		with patch.object(preflight.frappe.db, "get_value", side_effect=fake):
			return preflight.check_curated_reports()

	def test_disabled_renamed_and_prepared_reports(self):
		self.assertEqual(self._with_report(disabled=1)["status"], preflight.FAIL)
		self.assertEqual(self._with_report()["status"], preflight.FAIL)
		result = self._with_report(prepared_report=1)
		self.assertIn(result["status"], (preflight.WARN, preflight.FAIL))
		self.assertIn("prepared_report", result["detail"])

	def test_a_deny_listed_catalog_entry_fails(self):
		with patch.dict(reports.get_entry("leave_ledger"), {"report": "Timesheet Billing Summary"}):
			result = preflight.check_curated_reports()
		self.assertEqual(result["status"], preflight.FAIL)
		self.assertIn("deny-listed", result["detail"])
