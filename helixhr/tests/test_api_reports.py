"""P7-U8: the report runner (`run_portal_report`) and HelixHR's own scoped
billable-hours query (`get_billable_hours`) -- two different things wearing
the word "report" (KTD3a), tested separately because they carry different
authorization mechanisms.

`get_billable_hours` is never a Frappe Report and never reaches Frappe's
report engine (KTD3) -- its tests are almost entirely about scope narrowing,
per the plan's own Execution note that a method taking caller-supplied
filters and reaching a query is exactly the shape that becomes a
data-exfiltration path when narrowing is got wrong. `run_portal_report`
renders one of the existing curated HR reports through that engine (KTD4);
its own authorization is Frappe's report-permission model, already proven on
the bench for the plan's Sources and Research, so this file's coverage of it
is the uniform-refusal test the plan names explicitly, plus the rate-limit
membership every P7 write and fan-out read carries.
"""

import hashlib

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, add_to_date, cint, get_datetime

from helixhr.api import get_billable_hours, run_portal_report
from helixhr.tests.utils import (
	ensure_baseline_company,
	ensure_test_company,
	make_test_delivery_manager,
	make_test_employee_and_manager,
	make_test_hr_manager_employee,
	make_test_project,
)
from helixhr.utils import RATE_LIMIT_POLICY


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


def _make_timesheet(company, employee, employee_name, rows):
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
	return doc


# --- get_billable_hours (KTD3, R8, R13, R15) --------------------------------


class TestGetBillableHours(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.other_company = ensure_baseline_company()
		self.employee_name, self.employee_user, _, _ = make_test_employee_and_manager()
		self.hr_employee, self.hr_user = make_test_hr_manager_employee()
		self.dm_employee, self.dm_user = make_test_delivery_manager()

		# One set of projects/tasks per test method (named off the method,
		# the same trick test_api_timesheet.py and test_api_projects.py
		# both use) so one method's Timesheet rows never leak into another
		# method's assertions under the per-class rollback.
		method_name = self.id().split(".")[-1]
		self.member_project = make_test_project(
			self.company, f"_Test U8 Member {method_name}", members=[self.dm_user]
		)
		self.other_project = make_test_project(self.company, f"_Test U8 Other {method_name}")
		self.other_company_project = make_test_project(
			self.other_company, f"_Test U8 OtherCo {method_name}"
		)
		self.task = _ensure_task(self.member_project, f"_Test U8 Task {method_name}")
		self.other_task = _ensure_task(self.member_project, f"_Test U8 Other Task {method_name}")

		# A distinct day per test method (the same hashed-offset trick
		# test_api_timesheet.py uses for its weeks) -- the fixture employees
		# are shared across this class's methods, and ERPNext's own
		# Timesheet overlap check compares a new row against *every*
		# existing Timesheet for that employee, not just rows within the
		# same document, so two methods writing to the same employee on the
		# same literal "today" collide with each other's fixture data.
		digest = int(hashlib.md5(self.id().encode()).hexdigest(), 16)
		self.today = str(add_days(frappe.utils.today(), digest % 3000))

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_hr_manager_receives_only_their_own_company_rows(self):
		_make_timesheet(
			self.company,
			self.employee_name,
			"Own Co Employee",
			[{"date": self.today, "project": self.member_project, "hours": 2}],
		)
		_make_timesheet(
			self.other_company,
			self.hr_employee,
			"Other Co Employee",
			[{"date": self.today, "project": self.other_company_project, "hours": 3}],
		)

		frappe.set_user(self.hr_user)
		projects = {row["project"] for row in get_billable_hours()["rows"]}
		self.assertIn(self.member_project, projects)
		self.assertNotIn(self.other_company_project, projects)

	def test_a_plain_employee_is_refused(self):
		frappe.set_user(self.employee_user)
		with self.assertRaises(frappe.PermissionError):
			get_billable_hours()

	def test_delivery_manager_gets_only_member_project_rows_even_naming_another_project(self):
		_make_timesheet(
			self.company,
			self.dm_employee,
			"DM",
			[{"date": self.today, "project": self.member_project, "hours": 2}],
		)
		_make_timesheet(
			self.company,
			self.employee_name,
			"Other",
			[{"date": self.today, "project": self.other_project, "hours": 5}],
		)

		frappe.set_user(self.dm_user)
		# Naming a project they do not administer narrows to nothing --
		# never the named project's own rows.
		self.assertEqual(get_billable_hours(project=self.other_project)["rows"], [])

		# The shared Delivery Manager fixture is a member of other test
		# methods' own projects too (per-class rollback), so this asserts
		# membership rather than an exact set: their own project's hours are
		# present, and the project they were just refused by name is not.
		projects = {row["project"] for row in get_billable_hours()["rows"]}
		self.assertIn(self.member_project, projects)
		self.assertNotIn(self.other_project, projects)

	def test_hr_manager_naming_another_companys_filter_gets_their_own_company_rows(self):
		_make_timesheet(
			self.company,
			self.employee_name,
			"Own",
			[{"date": self.today, "project": self.member_project, "hours": 2}],
		)
		_make_timesheet(
			self.other_company,
			self.hr_employee,
			"Other",
			[{"date": self.today, "project": self.other_company_project, "hours": 4}],
		)

		frappe.set_user(self.hr_user)
		self.assertEqual(get_billable_hours(project=self.other_company_project)["rows"], [])

	def test_filtering_by_task_returns_only_that_tasks_rows(self):
		_make_timesheet(
			self.company,
			self.employee_name,
			"Emp",
			[
				{"date": self.today, "project": self.member_project, "task": self.task, "hours": 1},
				{
					"date": self.today,
					"project": self.member_project,
					"task": self.other_task,
					"hours": 1,
				},
			],
		)

		frappe.set_user(self.hr_user)
		rows = get_billable_hours(task=self.task)["rows"]
		self.assertTrue(rows)
		self.assertTrue(all(row["task"] == self.task for row in rows))

	def test_filtering_by_employee_returns_only_that_employees_rows(self):
		_make_timesheet(
			self.company,
			self.employee_name,
			"Emp",
			[{"date": self.today, "project": self.member_project, "hours": 1}],
		)
		_make_timesheet(
			self.company,
			self.hr_employee,
			"HR",
			[{"date": self.today, "project": self.member_project, "hours": 1}],
		)

		frappe.set_user(self.hr_user)
		rows = get_billable_hours(employee=self.employee_name)["rows"]
		self.assertTrue(rows)
		self.assertTrue(all(row["employee"] == self.employee_name for row in rows))

	def test_returned_columns_carry_no_rate_amount_or_cost_column(self):
		_make_timesheet(
			self.company,
			self.employee_name,
			"Emp",
			[{"date": self.today, "project": self.member_project, "hours": 1, "is_billable": 1}],
		)

		frappe.set_user(self.hr_user)
		rows = get_billable_hours()["rows"]
		self.assertTrue(rows)
		money_keys = {"billing_rate", "billing_amount", "costing_rate", "costing_amount"}
		for row in rows:
			self.assertFalse(money_keys & set(row.keys()), row.keys())

	def test_an_unrecognised_filter_key_is_ignored(self):
		_make_timesheet(
			self.company,
			self.employee_name,
			"Emp",
			[{"date": self.today, "project": self.member_project, "hours": 1}],
		)

		frappe.set_user(self.hr_user)
		named = get_billable_hours()["rows"]
		with_unknown_key = get_billable_hours(some_unknown_filter="whatever else")["rows"]
		self.assertEqual(named, with_unknown_key)

	def test_naming_a_different_user_to_run_as_has_no_effect(self):
		_make_timesheet(
			self.company,
			self.employee_name,
			"Emp",
			[{"date": self.today, "project": self.member_project, "hours": 1}],
		)

		frappe.set_user(self.hr_user)
		normal = get_billable_hours()["rows"]
		# `user` / `as_user` are not filter keys this method reads -- they
		# land in `**kwargs` and are never consulted, so whose rows come
		# back is unaffected by naming somebody else here.
		impersonating = get_billable_hours(user="Administrator", as_user="Administrator")["rows"]
		self.assertEqual(normal, impersonating)

	def test_an_empty_scope_returns_an_empty_result(self):
		# The shared Delivery Manager fixture accumulates Project User
		# memberships across this class's other test methods (the rollback
		# here is per class, not per method -- see the module docstring's
		# pattern in test_api_projects.py). Strip every membership first so
		# this exercises the genuine zero-membership case rather than
		# passing only because an earlier method's project is still theirs.
		for project in frappe.get_all("Project User", filters={"user": self.dm_user}, pluck="parent"):
			frappe.db.delete("Project User", {"user": self.dm_user, "parent": project})

		frappe.set_user(self.dm_user)
		self.assertEqual(get_billable_hours()["rows"], [])

	def test_a_caller_who_cannot_reach_desk_still_gets_rows(self):
		from helixhr.api import _can_open_desk

		self.assertFalse(_can_open_desk(self.dm_user))
		_make_timesheet(
			self.company,
			self.dm_employee,
			"DM",
			[{"date": self.today, "project": self.member_project, "hours": 1}],
		)

		frappe.set_user(self.dm_user)
		self.assertTrue(get_billable_hours()["rows"])


# --- run_portal_report (KTD4, R12, R14) -------------------------------------


class TestRunPortalReport(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		ensure_test_company()
		_, self.employee_user, _, _ = make_test_employee_and_manager()
		_, self.hr_user = make_test_hr_manager_employee()

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_refuses_an_uncurated_report_with_the_same_message_as_nonexistent(self):
		frappe.set_user(self.hr_user)
		with self.assertRaises(frappe.PermissionError) as real_but_uncurated:
			# A real, installed report -- deliberately not on the curated
			# list (KTD3: it carries `billing_amount`).
			run_portal_report("Timesheet Billing Summary")
		with self.assertRaises(frappe.PermissionError) as does_not_exist:
			run_portal_report("Not A Real Report At All")
		self.assertEqual(str(real_but_uncurated.exception), str(does_not_exist.exception))

	def test_a_curated_report_runs_for_an_hr_manager(self):
		frappe.set_user(self.hr_user)
		result = run_portal_report("Employee Information")
		self.assertIn("columns", result)
		self.assertIn("result", result)

	def test_a_plain_employee_is_refused(self):
		frappe.set_user(self.employee_user)
		with self.assertRaises(frappe.PermissionError):
			run_portal_report("Employee Information")

	def test_both_report_methods_appear_in_the_rate_limit_policy(self):
		self.assertIn("get_billable_hours", RATE_LIMIT_POLICY)
		self.assertIn("run_portal_report", RATE_LIMIT_POLICY)
