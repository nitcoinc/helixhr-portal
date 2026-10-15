"""Plan 2026-10-15-002 U2: `get_pending_by_approver`, its collector and the
approver resolver (R6, R8, R11, R14; KTD2, KTD3, KTD4).

Timesheets are raw inserts, as `TestHrOverdueTab` does: the collector reads
columns only. Each test owns its own reporting lines (``pba-*`` users), so a
long-lived site's other pending rows never decide an assertion -- every check
is narrowed to this test's own records or groups."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, add_to_date, now_datetime, today

from helixhr import reminders
from helixhr.api import get_overdue_approvals, get_pending_by_approver
from helixhr.events import PENDING_SINCE_FIELD
from helixhr.tests.utils import (
	EMPLOYEE_USER,
	_make_role_user,
	ensure_baseline_company,
	ensure_test_company,
	make_test_employee_and_manager,
	make_test_hr_manager_employee,
	make_test_user,
)

MANAGER_A = "pba-manager-a@helixhr.test"
MANAGER_B = "pba-manager-b@helixhr.test"
REPORT_A = "pba-report-a@helixhr.test"
REPORT_B = "pba-report-b@helixhr.test"
HR_USER = "pba-hr-user@helixhr.test"


def _as(user, fn, *args, **kwargs):
	frappe.set_user(user)
	try:
		return fn(*args, **kwargs)
	finally:
		frappe.set_user("Administrator")


def make_timesheet(test, employee, age_days, state="Pending Approval", company=None, week_offset=0):
	"""One raw pending timesheet, `age_days` old (None: no pending-since)."""
	doc = frappe.get_doc(
		{
			"doctype": "Timesheet",
			"employee": employee,
			"employee_name": frappe.db.get_value("Employee", employee, "employee_name"),
			"company": company or frappe.db.get_value("Employee", employee, "company"),
			"start_date": add_days(today(), -7 * (week_offset + 1)),
			"end_date": add_days(today(), -7 * week_offset - 1),
			"workflow_state": state,
			PENDING_SINCE_FIELD: None if age_days is None else add_to_date(now_datetime(), days=-age_days),
		}
	)
	doc.set_new_name()
	doc.db_insert()
	test.addCleanup(frappe.db.delete, "Timesheet", {"name": doc.name})
	return doc.name


def groups_by_key(result):
	return {group["key"]: group for group in result["groups"]}


def mine(group, names):
	return [row for row in group["items"] if row["name"] in names]


class TestPendingByApprover(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.manager_a = make_test_user(MANAGER_A, self.company, status="Active")
		self.manager_b = make_test_user(MANAGER_B, self.company, status="Active")
		self.report_a = make_test_user(REPORT_A, self.company, reports_to=self.manager_a)
		self.report_b = make_test_user(REPORT_B, self.company, reports_to=self.manager_b)
		# Strict: the HR User keeps the self-scoping User Permission (R14).
		self.hr_employee, self.hr_user = _make_role_user(HR_USER, "HR User", self.company, status="Active")
		self.addCleanup(frappe.db.set_value, "Employee", self.manager_a, "status", "Active")

	def _timesheets(self, employee, *ages, **kwargs):
		return [make_timesheet(self, employee, age, week_offset=index, **kwargs) for index, age in enumerate(ages)]

	def test_two_managers_give_two_groups_ordered_by_oldest_wait(self):
		names_a = self._timesheets(self.report_a, 400, 5, 2)
		names_b = self._timesheets(self.report_b, 3)
		result = _as(self.hr_user, get_pending_by_approver, kind="timesheet")
		groups = groups_by_key(result)
		group_a, group_b = groups[f"user:{MANAGER_A}"], groups[f"user:{MANAGER_B}"]

		self.assertEqual(len(mine(group_a, names_a)), 3)
		self.assertEqual(len(mine(group_b, names_b)), 1)
		self.assertEqual(group_a["oldest_days"], 400)
		self.assertEqual(sorted(row["age_days"] for row in mine(group_a, names_a)), [2, 5, 400])
		keys = [group["key"] for group in result["groups"]]
		self.assertLess(keys.index(group_a["key"]), keys.index(group_b["key"]))
		self.assertTrue(group_a["remindable"])
		self.assertEqual(group_a["reason"], reminders.APPROVER_OK)

	def test_a_row_with_no_pending_since_is_since_unknown_not_zero(self):
		[name] = self._timesheets(self.report_a, None)
		group = groups_by_key(_as(self.hr_user, get_pending_by_approver, kind="timesheet"))[f"user:{MANAGER_A}"]
		[row] = mine(group, [name])
		self.assertTrue(row["since_unknown"])
		self.assertIsNone(row["age_days"])

	def test_two_approvers_with_the_same_name_stay_separate(self):
		frappe.db.set_value("Employee", self.manager_a, "employee_name", "Same Name")
		frappe.db.set_value("Employee", self.manager_b, "employee_name", "Same Name")
		self._timesheets(self.report_a, 3)
		self._timesheets(self.report_b, 3)
		groups = groups_by_key(_as(self.hr_user, get_pending_by_approver, kind="timesheet"))
		self.assertIn(f"user:{MANAGER_A}", groups)
		self.assertIn(f"user:{MANAGER_B}", groups)

	def test_an_approver_who_left_is_flagged_inactive_and_not_remindable(self):
		self._timesheets(self.report_a, 3)
		frappe.db.set_value("Employee", self.manager_a, "status", "Inactive")
		group = groups_by_key(_as(self.hr_user, get_pending_by_approver, kind="timesheet"))[f"user:{MANAGER_A}"]
		self.assertEqual(group["reason"], reminders.APPROVER_INACTIVE)
		self.assertEqual(group["approver_user"], MANAGER_A)
		self.assertFalse(group["remindable"])

	def test_a_missing_reports_to_is_no_approver(self):
		frappe.db.set_value("Employee", self.report_b, "reports_to", None)
		self.addCleanup(frappe.db.set_value, "Employee", self.report_b, "reports_to", self.manager_b)
		[name] = self._timesheets(self.report_b, 3)
		groups = _as(self.hr_user, get_pending_by_approver, kind="timesheet")["groups"]
		[group] = [group for group in groups if mine(group, [name])]
		self.assertEqual(group["reason"], reminders.APPROVER_NO_APPROVER)
		self.assertFalse(group["remindable"])

	def test_resolver_precedence_and_one_read_each(self):
		"""KTD3: batched -- the item count does not change the read count."""
		frappe.db.set_value("Employee", self.manager_a, "status", "Inactive")
		items = [{"approver": {"reports_to_of": self.report_a}} for _ in range(5)]
		items.append({"approver": {"user": MANAGER_B}})
		items.append({"approver": {"role": "HR Manager"}})
		items.append({"approver": {}})
		with patch("frappe.get_all", wraps=frappe.get_all) as get_all:
			reminders.resolve_approvers(items)
		self.assertEqual([call.args[0] for call in get_all.call_args_list], ["User"])
		self.assertEqual(items[0]["reason"], reminders.APPROVER_INACTIVE)
		self.assertEqual(items[5]["reason"], reminders.APPROVER_OK)
		self.assertEqual(items[6]["approver_role"], "HR Manager")
		self.assertEqual(items[7]["reason"], reminders.APPROVER_NO_APPROVER)

	def test_pending_hr_rows_group_under_the_role_with_no_reminder(self):
		[name] = self._timesheets(self.report_a, 3, state="Pending HR")
		groups = groups_by_key(_as(self.hr_user, get_pending_by_approver, kind="timesheet"))
		group = groups["role:HR Manager"]
		self.assertTrue(mine(group, [name]))
		self.assertFalse(group["remindable"])

	def test_another_companys_timesheet_is_not_listed(self):
		other_company = ensure_baseline_company()
		outsider = make_test_user("pba-outsider@helixhr.test", other_company, reports_to=self.manager_a)
		[name] = self._timesheets(outsider, 3, company=other_company)
		result = _as(self.hr_user, get_pending_by_approver, kind="timesheet")
		self.assertFalse([group for group in result["groups"] if mine(group, [name])])

	def test_the_company_filter_runs_before_the_bound(self):
		other_company = ensure_baseline_company()
		outsider = make_test_user("pba-outsider@helixhr.test", other_company, reports_to=self.manager_a)
		_other_hr, other_hr_user = _make_role_user("pba-hr-other@helixhr.test", "HR User", other_company)
		self._timesheets(self.report_a, 50, 40, 30)
		[theirs] = self._timesheets(outsider, 1, company=other_company)
		with patch.object(reminders, "_OVERDUE_FETCH", 2):
			result = _as(other_hr_user, get_pending_by_approver, kind="timesheet")
		self.assertTrue([group for group in result["groups"] if mine(group, [theirs])])

	def test_a_capped_read_says_so(self):
		self._timesheets(self.report_a, 5, 4)
		with patch.object(reminders, "_OVERDUE_FETCH", 1):
			self.assertTrue(_as(self.hr_user, get_pending_by_approver, kind="timesheet")["total_is_capped"])

	def test_overdue_only_matches_the_old_overdue_tab_for_other_kinds(self):
		_hr_employee, hr_manager = make_test_hr_manager_employee()
		frappe.local.conf["helixhr_approval_overdue_days"] = 2
		self.addCleanup(frappe.local.conf.pop, "helixhr_approval_overdue_days", None)
		pending = _as(hr_manager, get_pending_by_approver, overdue_only=1)
		overdue = _as(hr_manager, get_overdue_approvals)

		def names(result, skip_timesheets):
			return {
				row["name"]
				for group in result["groups"]
				for row in group["items"]
				if not (skip_timesheets and row["route_kind"] == "timesheet")
			}

		self.assertEqual(names(pending, False), names(overdue, True))
		self.assertTrue(all(row["overdue"] for group in pending["groups"] for row in group["items"]))
		self.assertIn("timesheet_count", pending)

	def test_hr_user_asking_for_another_kind_is_refused(self):
		self.assertRaises(frappe.PermissionError, _as, self.hr_user, get_pending_by_approver, kind="leave")
		self.assertRaises(frappe.PermissionError, _as, self.hr_user, get_pending_by_approver)

	def test_a_plain_employee_is_refused(self):
		make_test_employee_and_manager()
		self.assertRaises(frappe.PermissionError, _as, EMPLOYEE_USER, get_pending_by_approver, kind="timesheet")

	def test_an_hr_user_with_no_active_employee_is_refused(self):
		_make_role_user("pba-hr-inactive@helixhr.test", "HR User", self.company, status="Inactive")
		self.assertRaises(
			frappe.PermissionError, _as, "pba-hr-inactive@helixhr.test", get_pending_by_approver, kind="timesheet"
		)

	def test_an_hr_manager_whose_employee_left_is_refused_not_widened(self):
		hr_employee, hr_manager = make_test_hr_manager_employee()
		frappe.db.set_value("Employee", hr_employee, "status", "Inactive")
		self.addCleanup(frappe.db.set_value, "Employee", hr_employee, "status", "Active")
		self.assertRaises(frappe.PermissionError, _as, hr_manager, get_pending_by_approver, kind="timesheet")

	def test_strict_hr_user_still_sees_every_pending_timesheet_in_the_company(self):
		self.assertTrue(
			frappe.db.exists("User Permission", {"user": self.hr_user, "allow": "Employee"}),
			"fixture must keep the self-scoping User Permission",
		)
		names = self._timesheets(self.report_a, 3) + self._timesheets(self.report_b, 3)
		result = _as(self.hr_user, get_pending_by_approver, kind="timesheet")
		listed = {row["name"] for group in result["groups"] for row in group["items"]}
		self.assertTrue(set(names) <= listed)
