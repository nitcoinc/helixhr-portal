"""Plan 2026-10-04-001 U8: time and projects reports -- hours by project,
task and employee; missing timesheets; the wrapped Employee Hours
Utilization report (company tiers only, two-company isolated)."""

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
		for key in ("project_timesheet", "missing_timesheets", "hours_utilization"):
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
		_make_timesheet(self.company, self.employee, "E", [{"date": "2017-03-06", "project": self.member, "hours": 1}])
		_make_timesheet(self.company, self.hr_employee, "H", [{"date": "2017-03-06", "project": self.other, "hours": 7}])
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
		on = _data(_as(self.dm_user, run_report, "hours_by_project", filters={**self.window, "include_pending": 1}))
		self.assertEqual(sorted((row["hours"], row["approval"]) for row in on), [(1, "Approved"), (2, "Pending")])
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
		return _data(_as(user or self.hr_user, run_report, "missing_timesheets", filters={**self.window, **filters}))

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
		_make_timesheet(self.company, self.employee, "E", [{"date": "2017-07-03", "project": project, "hours": 5}])
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
