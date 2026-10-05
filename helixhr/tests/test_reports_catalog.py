"""Plan 2026-10-04-001 U8-U11: catalog entries.

U8: hours by project, task and employee; missing timesheets; the wrapped
Employee Hours Utilization report. U9: attendance and shifts. U10: leave.
U11: people and lifecycle. Every wrapped report has a two-company isolation
test (resolved decision 1)."""

import re

import frappe
from frappe.tests import IntegrationTestCase

from helixhr import reports
from helixhr.api import get_report_catalog, run_report
from helixhr.tests.test_api_reports import _make_timesheet
from helixhr.tests.utils import (
	ensure_baseline_company,
	ensure_test_company,
	make_test_delivery_manager,
	make_test_employee_and_manager,
	make_test_hr_manager_employee,
	make_test_hr_user,
	make_test_project,
	make_test_user,
	set_report_access,
)

# U9-U11 entries.
NEW_KEYS = (
	"holiday_work",
	"late_early_summary",
	"leave_taken",
	"who_is_out",
	"headcount",
	"headcount_trend",
	"joiners_leavers",
	"celebrations",
	"hr_request_aging",
	"unpaid_expense_claims",
	"employee_advances",
)
AMOUNT_KEYS = ("unpaid_expense_claims", "employee_advances")


def _data(result):
	return [row for row in result["rows"] if row["_kind"] == "row"]


def _as(user, fn, *args, **kwargs):
	frappe.set_user(user)
	try:
		return fn(*args, **kwargs)
	finally:
		frappe.set_user("Administrator")


class TestNewEntriesAreSeeded(IntegrationTestCase):
	def test_every_new_entry_has_default_grants_and_preset_where_ranged(self):
		for key in ("project_timesheet", "missing_timesheets", "hours_utilization", *NEW_KEYS):
			entry = reports.get_entry(key)
			self.assertTrue(entry["default_grants"], key)
			if any(spec["name"] == "from_date" for spec in entry["filters"]):
				self.assertTrue(entry["default_preset"], key)

	def test_seed_patch_creates_missing_rows_only(self):
		from helixhr.patches.v1_0 import seed_report_access

		frappe.db.delete("HelixHR Report Access", {"report_key": "missing_timesheets"})
		set_report_access("hours_utilization", hr_user_run=0)
		seed_report_access.execute()
		self.assertEqual(frappe.db.get_value("HelixHR Report Access", "missing_timesheets", "hr_user_run"), 1)
		self.assertEqual(frappe.db.get_value("HelixHR Report Access", "hours_utilization", "hr_user_run"), 0)

	def test_amount_reports_seed_with_hr_user_off(self):
		from helixhr.patches.v1_0 import seed_report_access

		for key in AMOUNT_KEYS:
			self.assertTrue(reports.get_entry(key)["shows_amounts"], key)
			frappe.db.delete("HelixHR Report Access", {"report_key": key})
		seed_report_access.execute()
		for key in AMOUNT_KEYS:
			self.assertEqual(frappe.db.get_value("HelixHR Report Access", key, "hr_user_run"), 0, key)
		self.assertEqual(frappe.db.get_value("HelixHR Report Access", "who_is_out", "hr_user_run"), 1)

	def test_a_granted_hr_user_runs_every_new_entry(self):
		_e, hr_user = make_test_hr_user()
		frappe.db.set_single_value("HR Settings", "standard_working_hours", 8)
		for key in NEW_KEYS:
			set_report_access(key, hr_user_run=1)
			result = _as(hr_user, run_report, key)
			self.assertEqual(result["rows"][-1]["_kind"], "total", key)


class TestFilterHelp(IntegrationTestCase):
	"""Plan 2026-10-05-001 U10 (KTD9): help rides in the filter spec."""

	WITH_HELP = frozenset(
		{
			("hours_by_project", "include_pending"),
			("who_is_out", "include_pending"),
			("project_timesheet", "basis"),
			("missing_timesheets", "project_members_only"),
			("headcount", "parameter"),
			("leave_balance_summary", "date"),
		}
	)

	def test_catalog_payload_carries_help_only_where_named(self):
		access = {"can_export": False}
		seen = set()
		for entry in reports.CATALOG:
			for spec in reports.client_entry(entry, access)["filters"]:
				pair = (entry["key"], spec["name"])
				if pair in self.WITH_HELP:
					self.assertTrue(spec.get("help"), pair)
					seen.add(pair)
				else:
					self.assertNotIn("help", spec, pair)
		self.assertEqual(seen, self.WITH_HELP)


class TestHoursByProjectTaskEmployee(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.employee, _u, _, _ = make_test_employee_and_manager()
		self.hr_employee, self.hr_user = make_test_hr_manager_employee()
		self.dm_employee, self.dm_user = make_test_delivery_manager()
		set_report_access("hours_by_project", dm_run=1, dm_export=1)
		self.member = make_test_project(self.company, "_Test U8b Member", members=[self.dm_user])
		self.other = make_test_project(self.company, "_Test U8b Other")
		self.window = {"from_date": "2017-03-01", "to_date": "2017-03-31"}

	def test_delivery_manager_sees_member_projects_and_pending_toggle_marks_rows(self):
		_make_timesheet(
			self.company, self.employee, "E", [{"date": "2017-03-06", "project": self.member, "hours": 1}]
		)
		_make_timesheet(
			self.company, self.hr_employee, "H", [{"date": "2017-03-06", "project": self.other, "hours": 7}]
		)
		_make_timesheet(
			self.company,
			self.dm_employee,
			"D",
			[{"date": "2017-03-07", "project": self.member, "hours": 2}],
			state="Pending Approval",
		)
		_make_timesheet(
			self.company,
			self.dm_employee,
			"D",
			[{"date": "2017-03-14", "project": self.member, "hours": 4}],
			state="Draft",
		)
		off = _data(_as(self.dm_user, run_report, "hours_by_project", filters=self.window))
		self.assertEqual([(row["project"], row["hours"]) for row in off], [(self.member, 1)])
		on = _data(
			_as(self.dm_user, run_report, "hours_by_project", filters={**self.window, "include_pending": 1})
		)
		self.assertEqual(
			sorted((row["hours"], row["approval"]) for row in on), [(1, "Approved"), (2, "Pending")]
		)
		self.assertTrue(_data(_as(self.hr_user, run_report, "hours_by_project", filters=self.window)))


class TestMissingTimesheets(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.employee, self.employee_user, _, _ = make_test_employee_and_manager()
		self.hr_employee, self.hr_user = make_test_hr_manager_employee()
		self.project = make_test_project(self.company, "_Test U8 Missing", members=[self.employee_user])
		# Mon 2017-05-01 .. Sun 2017-05-14: two weeks.
		self.window = {"from_date": "2017-05-01", "to_date": "2017-05-14", "employee": self.employee}

	def _run(self, user=None, **filters):
		return _data(
			_as(user or self.hr_user, run_report, "missing_timesheets", filters={**self.window, **filters})
		)

	def test_weeks_without_a_timesheet_are_none_and_logged_weeks_show_state_and_hours(self):
		sheet = _make_timesheet(
			self.company, self.employee, "E", [{"date": "2017-05-02", "project": self.project, "hours": 3}]
		)
		frappe.db.set_value("Timesheet", sheet.name, "start_date", "2017-05-01")
		rows = self._run()
		self.assertEqual(
			[(str(row["week_start"]), row["state"], row["hours"]) for row in rows],
			[("2017-05-01", "approved", 3), ("2017-05-08", "none", 0)],
		)

	def test_approved_leave_days_show_alongside(self):
		frappe.get_doc(
			{
				"doctype": "Leave Type",
				"leave_type_name": "_Test U8 Leave",
				"allow_negative": 1,
			}
		).insert(ignore_permissions=True, ignore_if_duplicate=True)
		frappe.db.sql(
			"""insert into `tabLeave Application` (name, employee, leave_type, from_date, to_date,
			status, docstatus, company, total_leave_days, creation, modified, owner, modified_by)
			values ('_Test U8 LA', %s, '_Test U8 Leave', '2017-05-08', '2017-05-14', 'Approved', 1, %s, 7,
			now(), now(), 'Administrator', 'Administrator')""",
			(self.employee, self.company),
		)
		rows = self._run()
		week2 = next(row for row in rows if str(row["week_start"]) == "2017-05-08")
		self.assertEqual((week2["state"], week2["leave_days"]), ("none", 7))

	def test_project_members_toggle_and_company_scope(self):
		other_company = ensure_baseline_company()
		foreign = make_test_user("u8-foreign@helixhr.test", other_company)
		self.assertEqual(self._run(employee=foreign), [])
		non_member = make_test_user("u8-nonmember@helixhr.test", self.company)
		self.assertEqual(self._run(employee=non_member), [])
		self.assertEqual(len(self._run(employee=non_member, project_members_only=0)), 2)

	def test_restricted_hr_user_tier_runs_it(self):
		_e, hr_user = make_test_hr_user()
		set_report_access("missing_timesheets", hr_user_run=1)
		self.assertEqual(len(self._run(user=hr_user)), 2)


class TestHoursUtilization(IntegrationTestCase):
	KEY = "hours_utilization"

	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.other_company = ensure_baseline_company()
		self.employee, _u, _, _ = make_test_employee_and_manager()
		self.hr_employee, self.hr_user = make_test_hr_manager_employee()
		self.dm_employee, self.dm_user = make_test_delivery_manager()
		frappe.db.set_single_value("HR Settings", "standard_working_hours", 8)
		self.window = {"from_date": "2017-07-01", "to_date": "2017-07-31"}

	def test_hr_manager_runs_it_and_sees_only_their_company(self):
		project = make_test_project(self.company, "_Test U8 Util")
		other_project = make_test_project(self.other_company, "_Test U8 Util OtherCo")
		foreign = make_test_user("u8-util-foreign@helixhr.test", self.other_company)
		_make_timesheet(
			self.company, self.employee, "E", [{"date": "2017-07-03", "project": project, "hours": 5}]
		)
		_make_timesheet(
			self.other_company, foreign, "F", [{"date": "2017-07-03", "project": other_project, "hours": 6}]
		)
		rows = _data(_as(self.hr_user, run_report, self.KEY, filters=self.window))
		employees = {row.get("employee") for row in rows}
		self.assertIn(self.employee, employees)
		self.assertNotIn(foreign, employees)
		own = set(frappe.get_all("Employee", {"company": self.company}, pluck="name"))
		self.assertFalse(employees - own - {None})
		columns = _as(self.hr_user, run_report, self.KEY, filters=self.window)["columns"]
		self.assertFalse([c for c in columns if c.get("fieldtype") == "Currency"])

	def test_a_granted_hr_user_runs_it(self):
		_e, hr_user = make_test_hr_user()
		set_report_access(self.KEY, hr_user_run=1)
		self.assertIsInstance(_as(hr_user, run_report, self.KEY, filters=self.window)["rows"], list)

	def test_absent_from_delivery_manager_catalog_and_refused_directly(self):
		with self.assertRaises(frappe.ValidationError):
			set_report_access(self.KEY, hr_user_run=1, dm_run=1)
		# Even a row written past that validation grants nothing.
		set_report_access(self.KEY, hr_user_run=1)
		frappe.db.set_value("HelixHR Report Access", self.KEY, {"dm_run": 1, "dm_export": 1})
		keys = {entry["key"] for entry in _as(self.dm_user, get_report_catalog)}
		self.assertNotIn(self.KEY, keys)
		with self.assertRaises(frappe.PermissionError):
			_as(self.dm_user, run_report, self.KEY, filters=self.window)


def _bulk(doctype, rows):
	"""Raw rows (no controller side effects); ``rows`` are dicts with a name."""
	now = frappe.utils.now()
	fields = list(rows[0])
	frappe.db.bulk_insert(
		doctype,
		["creation", "modified", "modified_by", "owner", *fields],
		[(now, now, "Administrator", "Administrator", *[row[f] for f in fields]) for row in rows],
		ignore_duplicates=True,
	)


class _TwoCompanies(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.other_company = ensure_baseline_company()
		self.employee, self.employee_user, _, _ = make_test_employee_and_manager()
		self.hr_employee, self.hr_user = make_test_hr_manager_employee()
		self.foreign = make_test_user("u9-foreign@helixhr.test", self.other_company)

	def _run(self, key, user=None, **filters):
		return _as(user or self.hr_user, run_report, key, filters=filters)

	def _employees(self, result):
		return {row.get("employee") for row in _data(result)}


class TestAttendanceReports(_TwoCompanies):
	def _attendance(self, name, employee, company, day, docstatus=1, late=0, early=0):
		return {
			"name": name,
			"employee": employee,
			"employee_name": employee,
			"attendance_date": day,
			"status": "Present",
			"company": company,
			"docstatus": docstatus,
			"late_entry": late,
			"early_exit": early,
		}

	def test_monthly_attendance_returns_day_columns_without_a_client_company(self):
		_bulk("Attendance", [self._attendance("_T U9 MA 1", self.employee, self.company, "2016-02-03")])
		result = self._run("monthly_attendance", month="2016-02")
		fieldnames = {column["fieldname"] for column in result["columns"]}
		self.assertTrue({"01-02-2016", "29-02-2016"} <= fieldnames, fieldnames)
		row = next(r for r in _data(result) if r["employee"] == self.employee)
		self.assertEqual(row["03-02-2016"], "P")

	def test_late_and_early_counts_submitted_only_and_stays_in_company(self):
		_bulk(
			"Attendance",
			[
				self._attendance("_T U9 LE 1", self.employee, self.company, "2016-04-04", late=1),
				self._attendance("_T U9 LE 2", self.employee, self.company, "2016-04-05", late=1),
				self._attendance("_T U9 LE 3", self.employee, self.company, "2016-04-06", early=1),
				self._attendance(
					"_T U9 LE 4", self.employee, self.company, "2016-04-07", docstatus=2, late=1
				),
				self._attendance("_T U9 LE 5", self.foreign, self.other_company, "2016-04-07", late=1),
			],
		)
		rows = _data(self._run("late_early_summary", from_date="2016-04-01", to_date="2016-04-30"))
		mine = [row for row in rows if row["employee"] == self.employee]
		self.assertEqual(
			[(r["month"], r["late_entries"], r["early_exits"]) for r in mine], [("2016-04", 2, 1)]
		)
		self.assertNotIn(self.foreign, {row["employee"] for row in rows})

	def test_holiday_work_is_company_isolated(self):
		from helixhr.tests.utils import ensure_holiday_list_assignment, ensure_test_holiday

		ensure_holiday_list_assignment(self.company)
		day = str(frappe.utils.add_days(frappe.utils.get_year_start(frappe.utils.today()), 9))
		ensure_test_holiday(day)
		_bulk(
			"Attendance",
			[
				self._attendance("_T U9 HW 1", self.employee, self.company, day),
				self._attendance("_T U9 HW 2", self.foreign, self.other_company, day),
			],
		)
		result = self._run("holiday_work", from_date=day, to_date=day)
		employees = self._employees(result)
		self.assertIn(self.employee, employees)
		self.assertNotIn(self.foreign, employees)
		own = set(frappe.get_all("Employee", {"company": self.company}, pluck="name"))
		self.assertFalse(employees - own - {None})


class TestLeaveReports(_TwoCompanies):
	def setUp(self):
		super().setUp()
		frappe.get_doc(
			{"doctype": "Leave Type", "leave_type_name": "_Test U10 Leave", "allow_negative": 1}
		).insert(ignore_permissions=True, ignore_if_duplicate=True)

	def _leave(self, name, frm, to, status="Approved", docstatus=1, employee=None, company=None):
		return {
			"name": name,
			"employee": employee or self.employee,
			"employee_name": "U10",
			"leave_type": "_Test U10 Leave",
			"from_date": frm,
			"to_date": to,
			"status": status,
			"docstatus": docstatus,
			"company": company or self.company,
			"total_leave_days": 1,
		}

	def test_leave_taken_ignores_rejected_and_cancelled(self):
		_bulk(
			"Leave Application",
			[
				self._leave("_T U10 LT 1", "2016-06-06", "2016-06-06"),
				self._leave("_T U10 LT 2", "2016-06-07", "2016-06-07", status="Rejected"),
				self._leave("_T U10 LT 3", "2016-06-08", "2016-06-08", status="Cancelled", docstatus=2),
				self._leave(
					"_T U10 LT 4",
					"2016-06-09",
					"2016-06-09",
					employee=self.foreign,
					company=self.other_company,
				),
			],
		)
		rows = _data(
			self._run("leave_taken", from_date="2016-06-01", to_date="2016-06-30", employee=self.employee)
		)
		mine = [row for row in rows if row["leave_type"] == "_Test U10 Leave"]
		self.assertEqual(
			[(r["month"], r["applications"], r["leave_days"]) for r in mine], [("2016-06", 1, 1)]
		)
		# No employee filter: the other company's application is still not counted.
		rows = _data(self._run("leave_taken", from_date="2016-06-01", to_date="2016-06-30"))
		self.assertEqual(sum(r["applications"] for r in rows if r["leave_type"] == "_Test U10 Leave"), 1)

	def test_who_is_out_overlap_and_pending_toggle(self):
		_bulk(
			"Leave Application",
			[
				self._leave("_T U10 WO 1", "2016-07-28", "2016-08-02"),
				self._leave("_T U10 WO 2", "2016-07-20", "2016-07-29"),
				self._leave("_T U10 WO 3", "2016-08-03", "2016-08-03", status="Open", docstatus=0),
				self._leave(
					"_T U10 WO 4",
					"2016-08-02",
					"2016-08-02",
					employee=self.foreign,
					company=self.other_company,
				),
			],
		)
		window = {"from_date": "2016-08-01", "to_date": "2016-08-31"}
		rows = _data(self._run("who_is_out", **window))
		self.assertEqual(
			[(str(r["from_date"]), r["approval"]) for r in rows if r["leave_type"] == "_Test U10 Leave"],
			[("2016-07-28", "Approved")],
		)
		rows = _data(self._run("who_is_out", include_pending=1, **window))
		self.assertEqual(
			sorted(r["approval"] for r in rows if r["leave_type"] == "_Test U10 Leave"),
			["Approved", "Pending"],
		)
		self.assertNotIn(self.foreign, {r["employee"] for r in rows})

	def test_leave_balance_equals_hrms_own_output(self):
		filters = {"from_date": "2016-01-01", "to_date": "2016-12-31", "employee": self.employee}
		ours = [
			{k: v for k, v in row.items() if not k.startswith("_")}
			for row in _data(self._run("leave_balance", **filters))
		]
		report = frappe.get_doc("Report", "Employee Leave Balance")
		_columns, theirs = report.execute_module({**filters, "company": self.company})[:2]
		self.assertEqual(len(ours), len([row for row in theirs if isinstance(row, dict)]))
		for mine, native in zip(ours, [row for row in theirs if isinstance(row, dict)], strict=True):
			for field, value in mine.items():
				native_value = native.get(field)
				if isinstance(value, float):
					self.assertAlmostEqual(value, frappe.utils.flt(native_value, 2), places=2)
				else:
					self.assertEqual(value, native_value, field)


class TestPeopleReports(_TwoCompanies):
	def test_headcount_trend_counts_from_and_to_month_ends(self):
		from helixhr.tests.utils import make_celebration_employee

		branch = "_Test U11 Branch"
		frappe.get_doc({"doctype": "Branch", "branch": branch}).insert(
			ignore_permissions=True, ignore_if_duplicate=True
		)
		make_celebration_employee("u11-joiner", self.company, "1990-05-05", "2015-03-15", branch=branch)
		make_celebration_employee(
			"u11-leaver",
			self.company,
			"1990-05-05",
			"2014-01-01",
			branch=branch,
			status="Left",
			relieving_date="2015-03-10",
		)
		_columns, rows = reports._headcount_trend(
			{"from_date": "2015-02-01", "to_date": "2015-04-30", "branch": branch},
			{"kind": "company", "company": self.company},
		)
		self.assertEqual(
			[(r["month"], r["headcount"]) for r in rows], [("2015-02", 1), ("2015-03", 1), ("2015-04", 1)]
		)
		_columns, rows = reports._headcount_trend(
			{"from_date": "2015-02-01", "to_date": "2015-02-28", "branch": branch},
			{"kind": "company", "company": self.other_company},
		)
		self.assertEqual(rows[0]["headcount"], 0)

	def test_attrition_with_zero_start_is_not_a_division(self):
		self.assertEqual(reports.attrition_summary(0, 5, 1), "n/a")
		self.assertEqual(reports.attrition_summary(0, 0, 0), "n/a")
		self.assertEqual(reports.attrition_summary(10, 10, 1), "10.0%")
		result = self._run("joiners_leavers", from_date="1900-01-01", to_date="1900-12-31")
		summary = {item["label"]: item["value"] for item in result["extra"]["summary"]}
		self.assertEqual(summary["Attrition"], "n/a")

	def test_celebrations_never_carry_a_birth_year(self):
		from helixhr.tests.utils import make_celebration_employee

		make_celebration_employee("u11-bday", self.company, "1987-11-14", "2019-11-02")
		result = self._run("celebrations", month="November")
		rows = _data(result)
		names = {row["employee"] for row in rows}
		self.assertTrue(names)
		self.assertNotIn("date_of_birth", {c["fieldname"] for c in result["columns"]})
		for row in result["rows"]:
			self.assertNotIn("date_of_birth", row)
			self.assertFalse([v for v in row.values() if "1987" in str(v)], row)
		self.assertFalse(
			names & set(frappe.get_all("Employee", {"company": self.other_company}, pluck="name"))
		)

	def test_hr_request_aging_has_no_private_fields_and_skips_closed(self):
		_bulk(
			"HR Request",
			[
				{
					"name": f"_T U11 REQ {status}",
					"employee": employee,
					"subject": "s",
					"status": status,
					"details": "secret details",
					"hr_note": "secret note",
					"docstatus": 0,
				}
				for status, employee in (
					("Open", self.employee),
					("Done", self.employee),
					("Open", self.foreign),
				)
			],
		)
		result = self._run("hr_request_aging")
		rows = _data(result)
		requests = {row["request"] for row in rows}
		self.assertIn("_T U11 REQ Open", requests)
		self.assertNotIn("_T U11 REQ Done", requests)
		self.assertNotIn(self.foreign, {row["employee"] for row in rows})
		for row in result["rows"]:
			self.assertFalse({"details", "hr_note", "helixhr_decision_reason"} & set(row))
			self.assertFalse([k for k in row if k.startswith("correction_")])
			self.assertNotIn("secret", " ".join(str(v) for v in row.values()))

	def test_headcount_is_isolated_and_has_no_currency_or_birth_date(self):
		result = self._run("headcount", parameter="Department")
		self.assertFalse([c for c in result["columns"] if c.get("fieldtype") == "Currency"])
		self.assertNotIn("date_of_birth", {c["fieldname"] for c in result["columns"]})
		own = set(frappe.get_all("Employee", {"company": self.company}, pluck="name"))
		self.assertFalse(self._employees(result) - own - {None})

	def test_expense_claims_keep_amounts_and_stay_in_company(self):
		claims = []
		for name, employee, company in (
			("_T U11 EC own", self.employee, self.company),
			("_T U11 EC foreign", self.foreign, self.other_company),
		):
			claims.append(
				{
					"name": name,
					"employee": employee,
					"employee_name": employee,
					"posting_date": "2016-09-01",
					"company": company,
					"docstatus": 1,
					"is_paid": 0,
					"total_sanctioned_amount": 125.5,
					"total_amount_reimbursed": 0,
				}
			)
		_bulk("Expense Claim", claims)
		_bulk(
			"Payment Ledger Entry",
			[
				{
					"name": f"_T U11 PLE {claim['name']}",
					"against_voucher_type": "Expense Claim",
					"against_voucher_no": claim["name"],
					"voucher_type": "Expense Claim",
					"voucher_no": claim["name"],
					"company": claim["company"],
					"amount": 125.5,
					"delinked": 0,
					"docstatus": 1,
				}
				for claim in claims
			],
		)
		result = self._run("unpaid_expense_claims")
		rows = {row["name"]: row for row in _data(result)}
		self.assertIn("_T U11 EC own", rows)
		self.assertNotIn("_T U11 EC foreign", rows)
		self.assertEqual(rows["_T U11 EC own"]["outstanding_amt"], 125.5)
		self.assertIn("Currency", {c.get("fieldtype") for c in result["columns"]})

	def test_employee_advances_keep_amounts_and_stay_in_company(self):
		_bulk(
			"Employee Advance",
			[
				{
					"name": name,
					"employee": employee,
					"posting_date": "2016-10-03",
					"company": company,
					"docstatus": 1,
					"status": "Unpaid",
					"advance_amount": 300,
					"paid_amount": 0,
					"claimed_amount": 0,
					"return_amount": 0,
					"currency": "USD",
				}
				for name, employee, company in (
					("_T U11 ADV own", self.employee, self.company),
					("_T U11 ADV foreign", self.foreign, self.other_company),
				)
			],
		)
		result = self._run("employee_advances", from_date="2016-10-01", to_date="2016-10-31")
		rows = {row["title"]: row for row in _data(result)}
		self.assertIn("_T U11 ADV own", rows)
		self.assertNotIn("_T U11 ADV foreign", rows)
		self.assertEqual(rows["_T U11 ADV own"]["employee"], self.employee)
		self.assertEqual(rows["_T U11 ADV own"]["advance_amount"], 300)


# Plan 2026-10-05-001 U8: columns that are allowed to be blank in every row of
# the seeded data below, with the reason. Anything not listed here must carry a
# value in at least one row, or the mapping layer has lost it.
OPTIONAL_BLANK = {
	# Exit paperwork is optional in HRMS; a leaver may have none of it.
	"employee_exits": {"exit_interview", "interview_status", "employee_status", "full_and_final_statement"},
	# Only filled when the employee came in late / left early.
	"shift_attendance": {"late_entry_hrs", "early_exit_hrs"},
	# An open request nobody has picked up yet has no assignee.
	"hr_request_aging": {"picked_up_by", "picked_up_by_name"},
	# Birthdays carry no year count (never a birth year, U11).
	"celebrations": {"years"},
	# HRMS sets it on leave-application entries only, never on allocations.
	"leave_ledger": {"holiday_list"},
}


_DAY_COLUMN = re.compile(r"^\d{2}-\d{2}-\d{4}$")


class TestNoUnexplainedBlanks(_TwoCompanies):
	"""Plan 2026-10-05-001 U8 characterization: on seeded data, run every
	catalog report and list declared columns blank in every row."""

	DAY = "2015-03-04"

	def setUp(self):
		super().setUp()
		from helixhr.tests.utils import (
			_ensure_directory_masters,
			ensure_holiday_list_assignment,
			ensure_test_holiday,
			ensure_test_shift_type,
			make_celebration_employee,
		)

		frappe.db.set_single_value("HR Settings", "standard_working_hours", 8)
		designation, department = _ensure_directory_masters(self.company)
		for doctype, field, value in (
			("Branch", "branch", "_Test U8 Branch"),
			("Employment Type", "employee_type_name", "_Test U8 Type"),
		):
			frappe.get_doc({"doctype": doctype, field: value}).insert(
				ignore_permissions=True, ignore_if_duplicate=True
			)
		manager = frappe.db.get_value("Employee", self.employee, "reports_to")
		masters = {
			"department": department,
			"designation": designation,
			"branch": "_Test U8 Branch",
			"employment_type": "_Test U8 Type",
			"reports_to": manager,
		}
		self.seed = make_celebration_employee("u8-blank", self.company, "1990-03-04", "2014-03-04", **masters)
		frappe.db.set_value("Employee", self.seed, "company_email", "u8-blank@helixhr.test")
		self.leaver = make_celebration_employee(
			"u8-leaver",
			self.company,
			"1990-03-05",
			"2010-01-01",
			status="Left",
			relieving_date=self.DAY,
			**masters,
		)
		self.name = frappe.db.get_value("Employee", self.seed, "employee_name")

		customer = frappe.db.get_value("Customer", {}, "name")
		self.project = make_test_project(self.company, "_Test U8 Blank Project")
		if customer:
			frappe.db.set_value("Project", self.project, "customer", customer)
		self.task = frappe.db.get_value(
			"Task", {"project": self.project, "subject": "_Test U8 Blank Task"}
		) or (
			frappe.get_doc({"doctype": "Task", "project": self.project, "subject": "_Test U8 Blank Task"})
			.insert(ignore_permissions=True)
			.name
		)
		# Class-scoped rollback: a second method must not book the same hours again.
		if not frappe.db.exists("Timesheet", {"employee": self.seed}):
			_make_timesheet(
				self.company,
				self.seed,
				self.name,
				[
					{
						"date": self.DAY,
						"project": self.project,
						"task": self.task,
						"hours": 5,
						"is_billable": 1,
					},
					{"date": self.DAY, "project": self.project, "hours": 2, "note": "no task"},
				],
			)

		shift = ensure_test_shift_type()
		ensure_holiday_list_assignment(self.company)
		ensure_test_holiday("2015-03-05")
		attendance = []
		for index, day in enumerate((self.DAY, "2015-03-05")):
			attendance.append(
				{
					"name": f"_T U8 ATT {index}",
					"employee": self.seed,
					"employee_name": self.name,
					"attendance_date": day,
					"status": "Present",
					"company": self.company,
					"department": department,
					"docstatus": 1,
					"late_entry": 1,
					"early_exit": 0,
					"shift": shift,
					"in_time": f"{day} 09:30:00",
					"out_time": f"{day} 17:00:00",
					"working_hours": 7.5,
				}
			)
		_bulk("Attendance", attendance)
		_bulk(
			"Employee Checkin",
			[
				{
					"name": f"_T U8 CHK {index}",
					"employee": self.seed,
					"employee_name": self.name,
					"time": row["in_time"],
					"log_type": "IN",
					"attendance": row["name"],
					"shift": shift,
					"shift_start": f"{row['attendance_date']} 09:00:00",
					"shift_end": f"{row['attendance_date']} 17:00:00",
					"shift_actual_start": f"{row['attendance_date']} 09:00:00",
					"shift_actual_end": f"{row['attendance_date']} 17:00:00",
				}
				for index, row in enumerate(attendance)
			],
		)

		leave_type = "_Test U8 Leave"
		frappe.get_doc({"doctype": "Leave Type", "leave_type_name": leave_type, "allow_negative": 1}).insert(
			ignore_permissions=True, ignore_if_duplicate=True
		)
		self.leave_type = leave_type
		if not frappe.db.exists("Leave Allocation", {"employee": self.seed, "leave_type": leave_type}):
			frappe.get_doc(
				{
					"doctype": "Leave Allocation",
					"employee": self.seed,
					"leave_type": leave_type,
					"from_date": "2015-01-01",
					# Inside the run window: Leave Ledger needs both ends in it.
					"to_date": "2015-03-31",
					"new_leaves_allocated": 5,
					"company": self.company,
				}
			).insert(ignore_permissions=True).submit()
		_bulk(
			"Leave Application",
			[
				{
					"name": "_T U8 LA 1",
					"employee": self.seed,
					"employee_name": self.name,
					"leave_type": leave_type,
					"from_date": "2015-03-09",
					"to_date": "2015-03-09",
					"status": "Approved",
					"docstatus": 1,
					"company": self.company,
					"department": department,
					"total_leave_days": 1,
				}
			],
		)
		_bulk(
			"HR Request",
			[
				{
					"name": "_T U8 REQ",
					"employee": self.seed,
					"subject": "s",
					"status": "Open",
					"routed_to_role": "HR Manager",
					"category": frappe.db.get_value("HelixHR Request Category", {}, "name"),
					"docstatus": 0,
				}
			],
		)
		claim = {
			"name": "_T U8 EC",
			"employee": self.seed,
			"employee_name": self.name,
			"posting_date": self.DAY,
			"company": self.company,
			"department": department,
			"docstatus": 1,
			"is_paid": 0,
			"total_sanctioned_amount": 10,
			"total_amount_reimbursed": 0,
		}
		_bulk("Expense Claim", [claim])
		_bulk(
			"Payment Ledger Entry",
			[
				{
					"name": "_T U8 PLE",
					"against_voucher_type": "Expense Claim",
					"against_voucher_no": claim["name"],
					"voucher_type": "Expense Claim",
					"voucher_no": claim["name"],
					"company": self.company,
					"amount": 10,
					"delinked": 0,
					"docstatus": 1,
				}
			],
		)
		_bulk(
			"Employee Advance",
			[
				{
					"name": "_T U8 ADV",
					"employee": self.seed,
					"employee_name": self.name,
					"department": department,
					"posting_date": self.DAY,
					"company": self.company,
					"docstatus": 1,
					"status": "Unpaid",
					"advance_account": frappe.db.get_value("Account", {"company": self.company}, "name"),
					"advance_amount": 30,
					"paid_amount": 0,
					"claimed_amount": 0,
					"return_amount": 0,
					"currency": "USD",
				}
			],
		)

	def _filters(self, entry):
		names = {spec["name"] for spec in entry["filters"]}
		candidates = {
			"from_date": "2015-01-01",
			"to_date": "2015-03-31",
			"date": "2015-03-31",
			"month": "2015-03",
			"employee": self.seed,
			"project": self.project,
			"project_members_only": 0,
		}
		filters = {k: v for k, v in candidates.items() if k in names}
		if entry["key"] == "celebrations":
			filters["month"] = "March"
		if entry["key"] in ("employee_exits", "joiners_leavers"):
			filters.pop("employee", None)
		return filters

	def test_wrapped_reports_name_every_employee_id(self):
		"""KTD8: an Employee link in a wrapped report gets a visible name
		column next to it, filled in (the mapping layer, never HRMS)."""
		for entry in reports.CATALOG:
			if entry["engine"] != "frappe":
				continue
			result = self._run(entry["key"], **self._filters(entry))
			columns = result["columns"]
			fieldnames = [c["fieldname"] for c in columns]
			for column in columns:
				if column.get("options") != "Employee" or column.get("fieldtype") != "Link":
					continue
				field = column["fieldname"]
				name_field = "employee_name" if field == "employee" else f"{field}_name"
				self.assertIn(name_field, fieldnames, entry["key"])
				name_column = columns[fieldnames.index(name_field)]
				self.assertFalse(name_column.get("hidden"), entry["key"])
				# HRMS Leave Ledger appends a "Total Leaves (...)" row in the id column.
				rows = [
					row
					for row in _data(result)
					if row.get(field) and frappe.db.exists("Employee", row[field])
				]
				self.assertTrue(rows or entry["key"] == "monthly_attendance", (entry["key"], field))
				self.assertTrue(all(row.get(name_field) for row in rows), (entry["key"], field))

	def test_directory_names_the_manager_and_keeps_raw_blanks_empty(self):
		result = self._run("employee_directory")
		row = next(row for row in _data(result) if row["name"] == self.seed)
		manager = frappe.db.get_value("Employee", row["reports_to"], "employee_name")
		self.assertEqual(row["reports_to_name"], manager)
		self.assertTrue(
			any(row.get("reports_to_name") is None for row in _data(result) if not row["reports_to"])
		)

	def test_every_declared_column_has_a_value_somewhere(self):
		blanks = {}
		for entry in reports.CATALOG:
			result = self._run(entry["key"], **self._filters(entry))
			rows = _data(result)
			if not rows:
				blanks[entry["key"]] = "no rows"
				continue
			optional = OPTIONAL_BLANK.get(entry["key"], set())
			empty = [
				column["fieldname"]
				for column in result["columns"]
				if column["fieldname"] not in optional
				# Monthly attendance's day columns are blank on days nobody was marked.
				and not _DAY_COLUMN.match(column["fieldname"])
				and not any(row.get(column["fieldname"]) not in (None, "") for row in rows)
			]
			if empty:
				blanks[entry["key"]] = empty
		self.assertEqual(blanks, {})
