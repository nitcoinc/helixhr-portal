"""Plan 2026-10-22-001 U3 (R9-R15, R17): relieve an employee now or on a
future date, with their direct reports moved, from the portal.

Every person here is a throwaway created by the test: a Left status disables
the linked User outside the test's rollback (docs/runbook.md), so no shared
fixture identity is ever relieved. The dev bench runs no scheduler, so the
job is called directly; it commits per row only outside tests."""

import hashlib

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, add_to_date, getdate, now_datetime

from helixhr import preflight
from helixhr.api import cancel_leaving, get_person, relieve_employee, retry_leaving, save_person
from helixhr.tasks import LEAVING_DOCTYPE, LEAVING_LOG_TITLE, apply_due_leavings
from helixhr.tests.test_api_people import _ensure_other_company
from helixhr.tests.utils import (
	EMPLOYEE_USER,
	clear_open_leave,
	ensure_holiday_list_assignment,
	ensure_leave_allocation,
	ensure_test_company,
	ensure_test_gender,
	make_test_employee_and_manager,
	make_test_hr_manager_employee,
)
from helixhr.utils import RATE_LIMIT_POLICY, company_today, reset_rate_limit

_ENDPOINTS = ("relieve_employee", "cancel_leaving", "retry_leaving")


def _person(company, login=False, reports_to=None, first="Leaving", **fields):
	"""A fresh Active Employee, with its own throwaway User when ``login``."""
	tag = frappe.generate_hash(length=8)
	user = None
	if login:
		user = f"u3-{tag}@helixhr.test"
		frappe.get_doc(
			{
				"doctype": "User",
				"email": user,
				"first_name": first,
				"send_welcome_email": 0,
				"roles": [{"role": "Employee"}],
			}
		).insert(ignore_permissions=True)
	doc = frappe.get_doc(
		{
			"doctype": "Employee",
			"employee_number": f"U3-{tag}",
			"first_name": first,
			"last_name": f"Probe {tag}",
			"company": company,
			"date_of_birth": "1990-01-01",
			"date_of_joining": "2020-01-01",
			"gender": ensure_test_gender(),
			"status": "Active",
			"user_id": user,
			"reports_to": reports_to,
			**fields,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def _emp(name, *fields):
	return frappe.db.get_value("Employee", name, list(fields) or ["*"], as_dict=True)


def _open_row(employee):
	name = frappe.db.get_value(
		LEAVING_DOCTYPE, {"employee": employee, "status": ["in", ("Pending", "Failed")]}
	)
	return frappe.get_doc(LEAVING_DOCTYPE, name) if name else None


def _notes(employee, mark):
	return frappe.get_all(
		"Comment",
		filters={"reference_doctype": "Employee", "reference_name": employee, "comment_type": "Info", "subject": mark},
		pluck="content",
	)


def _error_logs(row):
	return frappe.db.count(
		"Error Log", {"method": LEAVING_LOG_TITLE, "reference_doctype": LEAVING_DOCTYPE, "reference_name": row}
	)


class LeavingTestCase(IntegrationTestCase):
	def setUp(self):
		# Midday on the company's date, whatever hour CI runs at (IST midnight
		# crossings): `company_today` then cannot roll over mid-test.
		self.enterContext(self.freeze_time(f"{getdate()} 12:00:00", tick=True))
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.hr_employee, self.hr_user = make_test_hr_manager_employee()
		self.today = company_today(self.company)
		for action in _ENDPOINTS:
			reset_rate_limit(action, self.hr_user)

	def tearDown(self):
		frappe.set_user("Administrator")

	def _as_hr(self, fn, *args, **kwargs):
		frappe.set_user(self.hr_user)
		try:
			return fn(*args, **kwargs)
		finally:
			frappe.set_user("Administrator")

	def _relieve(self, employee, leaving_date, reason="Resigned to study.", new_manager=None, **kwargs):
		return self._as_hr(
			relieve_employee,
			employee,
			str(leaving_date),
			reason=reason,
			new_manager=new_manager,
			**kwargs,
		)

	def _team(self, reports=2):
		"""A boss, a leaver (with a login) reporting to them, the leaver's
		reports, and a new manager with a login."""
		boss = _person(self.company, login=True, first="Boss")
		leaver = _person(self.company, login=True, reports_to=boss, first="Leaver")
		team = [_person(self.company, reports_to=leaver, first="Report") for _ in range(reports)]
		new_manager = _person(self.company, login=True, first="Next")
		return boss, leaver, team, new_manager


class TestRelieveToday(LeavingTestCase):
	def test_leaving_today_moves_the_reports_and_sets_the_leaver_left(self):
		_boss, leaver, team, new_manager = self._team()
		leaver_user = _emp(leaver, "user_id").user_id
		manager_user = _emp(new_manager, "user_id").user_id

		result = self._relieve(leaver, self.today, new_manager=new_manager)

		self.assertTrue(result["applied"])
		self.assertEqual(result["leaving"]["status"], "Applied")
		for report in team:
			row = _emp(report, "reports_to", "leave_approver")
			self.assertEqual(row.reports_to, new_manager)
			self.assertEqual(row.leave_approver, manager_user)
		row = _emp(leaver, "status", "relieving_date", "reason_for_leaving")
		self.assertEqual(row.status, "Left")
		self.assertEqual(getdate(row.relieving_date), self.today)
		self.assertEqual(row.reason_for_leaving, "Resigned to study.")
		self.assertEqual(frappe.db.get_value("User", leaver_user, "enabled"), 0)

		stored = frappe.get_doc(LEAVING_DOCTYPE, result["leaving"]["name"])
		self.assertEqual(stored.status, "Applied")
		self.assertTrue(stored.applied_on)
		self.assertEqual(stored.scheduled_by, self.hr_user)
		notes = _notes(leaver, "helixhr:leaving_applied")
		self.assertEqual(len(notes), 1)
		self.assertIn(frappe.utils.get_fullname(self.hr_user), notes[0])

	def test_the_new_manager_among_the_reports_moves_to_the_leavers_own_manager(self):
		boss, leaver, (first, second), _next = self._team()

		self._relieve(leaver, self.today, new_manager=first)

		self.assertEqual(_emp(first, "reports_to").reports_to, boss)
		self.assertEqual(_emp(second, "reports_to").reports_to, first)
		self.assertEqual(_emp(leaver, "status").status, "Left")

	def test_a_report_in_a_company_outside_the_callers_scope_moves_too(self):
		"""KTD7: the leaver's scope gate decides; the report is moved for them."""
		_boss, leaver, _team, new_manager = self._team(reports=0)
		abroad = _person(_ensure_other_company(), reports_to=leaver, first="Abroad")

		self._relieve(leaver, self.today, new_manager=new_manager)

		self.assertEqual(_emp(abroad, "reports_to").reports_to, new_manager)

	def test_no_reports_and_no_new_manager_applies_cleanly(self):
		leaver = _person(self.company)
		result = self._relieve(leaver, self.today)
		self.assertTrue(result["applied"])
		self.assertEqual(_emp(leaver, "status").status, "Left")

	def test_an_applied_leaving_cannot_be_cancelled_or_changed(self):
		leaver = _person(self.company)
		self._relieve(leaver, self.today)
		with self.assertRaises(frappe.ValidationError):
			self._as_hr(cancel_leaving, leaver)
		with self.assertRaises(frappe.ValidationError):
			self._relieve(leaver, add_days(self.today, 3))


class TestScheduledLeaving(LeavingTestCase):
	def test_a_future_date_is_scheduled_and_the_job_applies_it_on_the_day(self):
		_boss, leaver, team, new_manager = self._team(reports=1)
		day = add_days(self.today, 3)

		result = self._relieve(leaver, day, new_manager=new_manager)

		self.assertFalse(result["applied"])
		self.assertEqual(result["leaving"]["status"], "Pending")
		row = _emp(leaver, "status", "relieving_date", "reason_for_leaving")
		self.assertEqual(row.status, "Active")
		self.assertEqual(getdate(row.relieving_date), getdate(day))
		self.assertEqual(row.reason_for_leaving, "Resigned to study.")
		self.assertEqual(len(_notes(leaver, "helixhr:leaving_scheduled")), 1)

		self.assertEqual(apply_due_leavings()["applied"], 0)
		self.assertEqual(_emp(leaver, "status").status, "Active")
		self.assertEqual(_emp(team[0], "reports_to").reports_to, leaver)

		with self.freeze_time(f"{day} 12:00:00", tick=True):
			self.assertEqual(apply_due_leavings()["applied"], 1)
		self.assertEqual(_emp(leaver, "status").status, "Left")
		self.assertEqual(_emp(team[0], "reports_to").reports_to, new_manager)
		self.assertEqual(frappe.db.get_value(LEAVING_DOCTYPE, result["leaving"]["name"], "status"), "Applied")
		notes = _notes(leaver, "helixhr:leaving_applied")
		self.assertEqual(len(notes), 1)
		self.assertIn(frappe.utils.get_fullname(self.hr_user), notes[0])

	def test_a_report_added_after_scheduling_moves_on_the_day(self):
		_boss, leaver, _team, new_manager = self._team(reports=1)
		day = add_days(self.today, 2)
		self._relieve(leaver, day, new_manager=new_manager)
		late = _person(self.company, reports_to=leaver, first="Late")

		with self.freeze_time(f"{day} 12:00:00", tick=True):
			apply_due_leavings()

		self.assertEqual(_emp(late, "reports_to").reports_to, new_manager)
		self.assertEqual(_emp(leaver, "status").status, "Left")

	def test_a_company_on_another_clock_applies_at_its_own_midnight(self):
		before = frappe.db.get_value("Company", self.company, "helixhr_time_zone")
		self.addCleanup(
			frappe.db.set_value, "Company", self.company, "helixhr_time_zone", before, update_modified=False
		)
		frappe.db.set_value("Company", self.company, "helixhr_time_zone", "America/Chicago", update_modified=False)
		leaver = _person(self.company)
		day = add_days(getdate(), 5)
		# The server's zone is Asia/Kolkata on this bench and in CI: 08:00
		# there is still the evening before in Chicago, 11:00 is past its
		# midnight.
		with self.freeze_time(f"{add_days(day, -1)} 12:00:00", tick=True):
			self._relieve(leaver, day)
		with self.freeze_time(f"{day} 08:00:00", tick=True):
			self.assertEqual(company_today(self.company), add_days(day, -1))
			self.assertEqual(apply_due_leavings()["applied"], 0)
		self.assertEqual(_emp(leaver, "status").status, "Active")
		with self.freeze_time(f"{day} 11:00:00", tick=True):
			self.assertEqual(apply_due_leavings()["applied"], 1)
		self.assertEqual(_emp(leaver, "status").status, "Left")

	def test_the_job_run_twice_on_the_same_day_touches_nothing_the_second_time(self):
		leaver = _person(self.company)
		day = add_days(self.today, 1)
		result = self._relieve(leaver, day)
		with self.freeze_time(f"{day} 12:00:00", tick=True):
			self.assertEqual(apply_due_leavings()["applied"], 1)
			modified = frappe.db.get_value(LEAVING_DOCTYPE, result["leaving"]["name"], "modified")
			employee_modified = _emp(leaver, "modified").modified
			self.assertEqual(apply_due_leavings(), {"applied": 0, "failed": 0})
		self.assertEqual(frappe.db.get_value(LEAVING_DOCTYPE, result["leaving"]["name"], "modified"), modified)
		self.assertEqual(_emp(leaver, "modified").modified, employee_modified)
		self.assertEqual(len(_notes(leaver, "helixhr:leaving_applied")), 1)

	def test_a_change_updates_the_same_row_and_cancel_clears_the_dates(self):
		leaver = _person(self.company)
		first = self._relieve(leaver, add_days(self.today, 5))
		second = self._relieve(
			leaver,
			add_days(self.today, 7),
			reason="Moving abroad.",
			expected_modified=first["leaving"]["modified"],
		)

		self.assertEqual(second["leaving"]["name"], first["leaving"]["name"])
		self.assertEqual(getdate(second["leaving"]["leaving_date"]), add_days(self.today, 7))
		row = _emp(leaver, "relieving_date", "reason_for_leaving")
		self.assertEqual(getdate(row.relieving_date), add_days(self.today, 7))
		self.assertEqual(row.reason_for_leaving, "Moving abroad.")
		self.assertEqual(frappe.db.count(LEAVING_DOCTYPE, {"employee": leaver}), 1)
		self.assertEqual(len(_notes(leaver, "helixhr:leaving_changed")), 1)

		result = self._as_hr(cancel_leaving, leaver, expected_modified=second["leaving"]["modified"])

		self.assertIsNone(result["leaving"])
		self.assertEqual(frappe.db.get_value(LEAVING_DOCTYPE, first["leaving"]["name"], "status"), "Cancelled")
		row = _emp(leaver, "status", "relieving_date", "reason_for_leaving")
		self.assertEqual(row.status, "Active")
		self.assertIsNone(row.relieving_date)
		self.assertFalse(row.reason_for_leaving)
		self.assertEqual(len(_notes(leaver, "helixhr:leaving_cancelled")), 1)

	def test_a_stale_form_is_refused(self):
		leaver = _person(self.company)
		first = self._relieve(leaver, add_days(self.today, 5))
		# A form that saw no leaving, after one was scheduled.
		with self.assertRaises(frappe.ValidationError):
			self._relieve(leaver, add_days(self.today, 6))
		with self.assertRaises(frappe.ValidationError):
			self._relieve(leaver, add_days(self.today, 6), expected_modified="2001-01-01 00:00:00")
		with self.assertRaises(frappe.ValidationError):
			self._as_hr(cancel_leaving, leaver, expected_modified="2001-01-01 00:00:00")
		self.assertEqual(
			getdate(frappe.db.get_value(LEAVING_DOCTYPE, first["leaving"]["name"], "leaving_date")),
			add_days(self.today, 5),
		)

	def test_only_one_open_leaving_per_employee(self):
		leaver = _person(self.company)
		self._relieve(leaver, add_days(self.today, 5))
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": LEAVING_DOCTYPE,
					"employee": leaver,
					"company": self.company,
					"leaving_date": add_days(self.today, 9),
					"reason": "Twice.",
					"status": "Pending",
					"scheduled_by": "Administrator",
				}
			).insert(ignore_permissions=True)

	def test_retry_before_the_date_is_refused(self):
		leaver = _person(self.company)
		result = self._relieve(leaver, add_days(self.today, 5))
		with self.assertRaises(frappe.ValidationError) as caught:
			self._as_hr(retry_leaving, leaver, expected_modified=result["leaving"]["modified"])
		self.assertIn("applies on that day", str(caught.exception))
		# Without the token, a retry is refused as stale.
		with self.assertRaises(frappe.ValidationError) as caught:
			self._as_hr(retry_leaving, leaver)
		self.assertIn("Reload", str(caught.exception))
		self.assertEqual(_emp(leaver, "status").status, "Active")


class TestRelieveRefusals(LeavingTestCase):
	def test_hr_cannot_relieve_themselves(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			self._relieve(self.hr_employee, add_days(self.today, 3))
		self.assertIn("yourself", str(caught.exception))
		self.assertIsNone(_open_row(self.hr_employee))

	def test_a_new_manager_who_has_left_is_refused(self):
		_boss, leaver, _team, new_manager = self._team(reports=1)
		frappe.db.set_value("Employee", new_manager, {"status": "Left", "relieving_date": self.today})
		with self.assertRaises(frappe.ValidationError):
			self._relieve(leaver, self.today, new_manager=new_manager)
		self.assertEqual(_emp(leaver, "status").status, "Active")

	def test_a_new_manager_leaving_on_or_before_the_same_date_is_refused(self):
		_boss, leaver, _team, new_manager = self._team(reports=1)
		day = add_days(self.today, 6)
		self._relieve(new_manager, day)
		for leaver_day in (day, add_days(day, 1)):
			with self.assertRaises(frappe.ValidationError):
				self._relieve(leaver, leaver_day, new_manager=new_manager)
		# Leaving later than this leaver is fine.
		self._relieve(leaver, add_days(day, -1), new_manager=new_manager)

	def test_the_leaver_cannot_be_their_own_new_manager(self):
		_boss, leaver, _team, _next = self._team(reports=1)
		with self.assertRaises(frappe.ValidationError):
			self._relieve(leaver, self.today, new_manager=leaver)

	def test_a_new_manager_further_down_the_leavers_team_is_refused(self):
		_boss, leaver, (report,), _next = self._team(reports=1)
		below = _person(self.company, reports_to=report, first="Below")
		with self.assertRaises(frappe.ValidationError):
			self._relieve(leaver, self.today, new_manager=below)
		self.assertEqual(_emp(leaver, "status").status, "Active")

	def test_a_new_manager_outside_the_callers_scope_is_refused(self):
		_boss, leaver, _team, _next = self._team(reports=1)
		outsider = _person(_ensure_other_company())
		with self.assertRaises(frappe.PermissionError):
			self._relieve(leaver, self.today, new_manager=outsider)

	def test_reports_without_a_new_manager_are_refused(self):
		_boss, leaver, _team, _next = self._team(reports=1)
		with self.assertRaises(frappe.ValidationError) as caught:
			self._relieve(leaver, self.today)
		self.assertIn("report", str(caught.exception))
		self.assertEqual(_emp(leaver, "status").status, "Active")

	def test_an_employee_outside_scope_or_an_employee_caller_is_refused(self):
		outsider = _person(_ensure_other_company())
		with self.assertRaises(frappe.PermissionError):
			self._relieve(outsider, self.today)
		make_test_employee_and_manager()
		leaver = _person(self.company)
		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.PermissionError):
			relieve_employee(leaver, str(self.today), reason="No.")
		frappe.set_user("Administrator")
		self.assertEqual(_emp(leaver, "status").status, "Active")

	def test_a_missing_reason_or_date_and_a_date_before_joining_are_refused_in_plain_words(self):
		leaver = _person(self.company)
		with self.assertRaises(frappe.ValidationError):
			self._relieve(leaver, self.today, reason="  ")
		with self.assertRaises(frappe.ValidationError):
			self._relieve(leaver, "")
		with self.assertRaises(frappe.ValidationError) as caught:
			self._relieve(leaver, "2019-12-31")
		self.assertIn("joined", str(caught.exception))
		self.assertEqual(_emp(leaver, "status").status, "Active")

	def test_someone_not_active_is_refused(self):
		leaver = _person(self.company)
		frappe.db.set_value("Employee", leaver, "status", "Inactive")
		with self.assertRaises(frappe.ValidationError):
			self._relieve(leaver, self.today)


class TestFailedLeaving(LeavingTestCase):
	def test_a_new_manager_who_left_in_between_fails_the_row_once_then_retry_applies(self):
		_boss, leaver, (report,), new_manager = self._team(reports=1)
		day = add_days(self.today, 3)
		scheduled = self._relieve(leaver, day, new_manager=new_manager)
		row = scheduled["leaving"]["name"]
		# Left in Desk between scheduling and the date. `db.set_value`, so the
		# throwaway login is not disabled through ERPNext's hook.
		frappe.db.set_value("Employee", new_manager, {"status": "Left", "relieving_date": self.today})

		with self.freeze_time(f"{day} 12:00:00", tick=True):
			self.assertEqual(apply_due_leavings(), {"applied": 0, "failed": 1})
			stored = frappe.get_doc(LEAVING_DOCTYPE, row)
			self.assertEqual(stored.status, "Failed")
			self.assertIn(_emp(new_manager, "employee_name").employee_name, stored.error)
			self.assertNotIn("<", stored.error)
			self.assertEqual(_emp(leaver, "status").status, "Active")
			self.assertEqual(_emp(report, "reports_to").reports_to, leaver)
			self.assertEqual(_error_logs(row), 1)

			apply_due_leavings()
			self.assertEqual(_error_logs(row), 1)

			person = self._as_hr(get_person, leaver)
			self.assertEqual(person["leaving"]["status"], "Failed")
			self.assertTrue(person["leaving"]["error"])

			frappe.db.set_value("Employee", new_manager, {"status": "Active", "relieving_date": None})
			result = self._as_hr(retry_leaving, leaver, expected_modified=person["leaving"]["modified"])

		self.assertEqual(result["leaving"]["status"], "Applied")
		self.assertFalse(result["leaving"]["error"])
		self.assertEqual(_emp(leaver, "status").status, "Left")
		self.assertEqual(_emp(report, "reports_to").reports_to, new_manager)

	def test_preflight_fails_on_a_failed_leaving_and_warns_on_an_overdue_one(self):
		self.assertIn(preflight.check_leavings, preflight.CHECKS)
		leaver = _person(self.company)
		result = self._relieve(leaver, add_days(self.today, 2))
		self.assertEqual(preflight.check_leavings()["status"], preflight.PASS)

		frappe.db.set_value(LEAVING_DOCTYPE, result["leaving"]["name"], "leaving_date", add_days(self.today, -1))
		self.assertEqual(preflight.check_leavings()["status"], preflight.WARN)

		frappe.db.set_value(
			LEAVING_DOCTYPE, result["leaving"]["name"], {"status": "Failed", "error": "Something broke."}
		)
		check = preflight.check_leavings()
		self.assertEqual(check["status"], preflight.FAIL)
		self.assertIn(leaver, check["detail"])


class TestLeavingIntegration(LeavingTestCase):
	def test_a_moved_reports_pending_leave_follows_the_new_manager(self):
		ensure_holiday_list_assignment(self.company)
		_boss, leaver, (report,), new_manager = self._team(reports=1)
		leaver_user = _emp(leaver, "user_id").user_id
		manager_user = _emp(new_manager, "user_id").user_id
		clear_open_leave(report)
		ensure_leave_allocation(report, "Casual Leave", 5)
		offset = 10 + int(hashlib.md5(self.id().encode()).hexdigest(), 16) % 100
		leave = frappe.get_doc(
			{
				"doctype": "Leave Application",
				"employee": report,
				"leave_type": "Casual Leave",
				"from_date": add_days(self.today, offset),
				"to_date": add_days(self.today, offset),
				"description": "u3",
				"leave_approver": leaver_user,
			}
		).insert(ignore_permissions=True)

		self._relieve(leaver, self.today, new_manager=new_manager)

		self.assertEqual(frappe.db.get_value("Leave Application", leave.name, "leave_approver"), manager_user)

	def test_the_leavers_coordinates_are_scrubbed_and_open_changes_withdrawn(self):
		leaver = _person(self.company)
		punch = frappe.get_doc(
			{
				"doctype": "Employee Checkin",
				"employee": leaver,
				"time": add_to_date(now_datetime(), days=-1),
				"log_type": "IN",
				"latitude": 12.9716,
				"longitude": 77.5946,
			}
		)
		punch.flags.ignore_validate = True
		punch.insert(ignore_permissions=True)
		change = frappe.generate_hash(length=10)
		frappe.db.bulk_insert(
			"HelixHR Timesheet Change",
			["name", "employee", "company", "timesheet", "week_start", "status", "comment"],
			[(change, leaver, self.company, "TS-U3", self.today, "Open", "Fix Tuesday please.")],
		)

		self._relieve(leaver, self.today)

		self.assertFalse(frappe.db.get_value("Employee Checkin", punch.name, "latitude"))
		self.assertEqual(frappe.db.get_value("HelixHR Timesheet Change", change, "status"), "Withdrawn")

	def test_get_person_returns_the_open_leaving_and_the_direct_report_count(self):
		_boss, leaver, _team, new_manager = self._team(reports=2)
		person = self._as_hr(get_person, leaver)
		self.assertIsNone(person["leaving"])
		self.assertEqual(person["direct_reports"], 2)

		self._relieve(leaver, add_days(self.today, 4), new_manager=new_manager)
		leaving = self._as_hr(get_person, leaver)["leaving"]
		self.assertEqual(leaving["status"], "Pending")
		self.assertEqual(getdate(leaving["leaving_date"]), add_days(self.today, 4))
		self.assertEqual(leaving["new_manager"], new_manager)
		self.assertEqual(leaving["new_manager_name"], _emp(new_manager, "employee_name").employee_name)
		self.assertEqual(leaving["reason"], "Resigned to study.")
		self.assertTrue(leaving["modified"])

	def test_get_person_shows_the_applied_leaving_of_someone_who_left(self):
		leaver = _person(self.company)
		self._relieve(leaver, self.today)
		leaving = self._as_hr(get_person, leaver)["leaving"]
		self.assertEqual(leaving["status"], "Applied")
		self.assertEqual(leaving["reason"], "Resigned to study.")

	def test_save_person_refuses_left_and_still_saves_inactive(self):
		person = _person(self.company)
		with self.assertRaises(frappe.ValidationError) as caught:
			self._as_hr(save_person, person, status="Left", relieving_date=str(self.today))
		self.assertIn("Relieve", str(caught.exception))
		self.assertEqual(_emp(person, "status").status, "Active")

		self._as_hr(save_person, person, status="Inactive")
		self.assertEqual(_emp(person, "status").status, "Inactive")

	def test_the_job_is_on_the_half_hourly_cron(self):
		cron = frappe.get_hooks("scheduler_events").get("cron", {})
		self.assertIn("helixhr.tasks.apply_due_leavings", cron.get("0,30 * * * *", []))

	def test_each_endpoint_has_a_rate_limit_and_it_is_enforced(self):
		for action in _ENDPOINTS:
			self.assertIn(action, RATE_LIMIT_POLICY)
		limit, _seconds = RATE_LIMIT_POLICY["cancel_leaving"]
		leaver = _person(self.company)
		frappe.flags.helixhr_enforce_rate_limits = True
		self.addCleanup(lambda: frappe.flags.pop("helixhr_enforce_rate_limits", None))
		self.addCleanup(reset_rate_limit, "cancel_leaving", self.hr_user)
		frappe.set_user(self.hr_user)
		# Nothing is scheduled, so each call is refused -- after counting.
		for _ in range(limit):
			with self.assertRaises(frappe.ValidationError):
				cancel_leaving(leaver)
		with self.assertRaises(frappe.RateLimitExceededError):
			cancel_leaving(leaver)
		frappe.set_user("Administrator")
