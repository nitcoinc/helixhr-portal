from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from helixhr.api import _team_holiday_dates, get_my_team_week, get_roster_week
from helixhr.tests.test_api_people import _ensure_other_company
from helixhr.tests.utils import (
	EMPLOYEE_USER,
	HR_MANAGER_EMPLOYEE_USER,
	MANAGER_USER,
	PORTAL_SHIFT_TYPE,
	TEST_COMPANY,
	ensure_test_gender,
	ensure_test_shift_type,
	make_test_employee_and_manager,
	make_test_hr_manager_employee,
)
from helixhr.utils import get_week_bounds

# Plan 2026-09-30-001 U7/U8. Every row asserted on exactly is a throwaway
# Employee with no login, and every week asserted on exactly is in 2019, so
# nothing another suite seeds (the fixture employee's open-ended portal
# shift, this week's leave) can decide an answer here -- the runbook's
# "unique date windows per test" rule.
SECOND_SHIFT_TYPE = "_Test Roster Late Shift"
WED_WEEK = "2019-03-04"  # Mondays, all of them
SPLIT_WEEK = "2019-04-01"
PAST_WEEK = "2019-05-06"
EMPTY_WEEK = "2019-06-03"


def roster_employee(key, company=TEST_COMPANY, **fields):
	"""An Active Employee with no User, reused across runs by its number.
	`fields` are rewritten on reuse with `db.set_value` (no hooks)."""
	number = f"_test-roster-{key}"
	name = frappe.db.get_value("Employee", {"employee_number": number})
	if name:
		frappe.db.set_value("Employee", name, {"status": "Active", **fields})
		return name
	return (
		frappe.get_doc(
			{
				"doctype": "Employee",
				"first_name": f"Roster {key}",
				"employee_number": number,
				"company": company,
				"gender": ensure_test_gender(),
				"date_of_birth": "1990-01-01",
				"date_of_joining": "2015-01-01",
				"status": "Active",
				**fields,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def seed_assignment(employee, start_date, end_date=None, shift_type=PORTAL_SHIFT_TYPE):
	"""A submitted assignment through the HRMS controller, as Administrator."""
	user = frappe.session.user
	frappe.set_user("Administrator")
	try:
		doc = frappe.get_doc(
			{
				"doctype": "Shift Assignment",
				"employee": employee,
				"shift_type": shift_type,
				"company": frappe.db.get_value("Employee", employee, "company"),
				"start_date": start_date,
				"end_date": end_date,
				"status": "Active",
			}
		)
		doc.insert(ignore_permissions=True)
		doc.submit()
		return doc
	finally:
		frappe.set_user(user)


def cell_shifts(row):
	return [cell["shift_type"] for cell in row["cells"]]


def row_for(payload, employee):
	return next(row for row in payload["rows"] if row["employee"] == employee)


class RosterTestCase(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()
		frappe.db.set_value("Employee", self.employee_name, "reports_to", self.manager_name)
		self.hr_employee, _ = make_test_hr_manager_employee()
		ensure_test_shift_type()
		ensure_test_shift_type(SECOND_SHIFT_TYPE, start_time="14:00:00", end_time="22:00:00")

	def tearDown(self):
		frappe.set_user("Administrator")

	def fresh(self, key, **fields):
		"""A throwaway employee with every assignment of theirs removed."""
		name = roster_employee(key, **fields)
		frappe.db.delete("Shift Assignment", {"employee": name})
		return name


class TestRosterRead(RosterTestCase):
	"""U7 / R7, R8."""

	def test_an_employee_sees_only_their_own_row_and_is_refused_wider_modes(self):
		frappe.set_user(EMPLOYEE_USER)
		payload = get_roster_week(WED_WEEK)
		self.assertEqual(payload["mode"], "mine")
		self.assertEqual([row["employee"] for row in payload["rows"]], [self.employee_name])
		self.assertFalse(payload["can_edit"])
		self.assertEqual(payload["shift_types"], [])
		self.assertNotIn("assignment", payload["rows"][0]["cells"][0])

		# Refused, not downgraded: the page's `forbidden` state is the answer.
		with self.assertRaises(frappe.PermissionError):
			get_roster_week(WED_WEEK, mode="hr")
		if not frappe.db.count("Employee", {"reports_to": self.employee_name, "status": "Active"}):
			with self.assertRaises(frappe.PermissionError):
				get_roster_week(WED_WEEK, mode="team")

	def test_an_unknown_mode_is_refused(self):
		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.ValidationError):
			get_roster_week(WED_WEEK, mode="everyone")

	def test_a_manager_sees_themselves_and_direct_reports_only(self):
		report = self.fresh("direct", reports_to=self.manager_name)
		nested = self.fresh("nested", reports_to=report)
		frappe.set_user(MANAGER_USER)
		employees = {row["employee"] for row in get_roster_week(WED_WEEK, mode="team")["rows"]}
		self.assertIn(self.manager_name, employees)
		self.assertIn(self.employee_name, employees)
		self.assertIn(report, employees)
		self.assertNotIn(nested, employees)

	def test_a_company_scoped_hr_manager_sees_only_their_company(self):
		mine = self.fresh("scope-a")
		theirs = roster_employee("scope-b", company=_ensure_other_company())
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		payload = get_roster_week(WED_WEEK, mode="hr", search="Roster scope")
		employees = {row["employee"] for row in payload["rows"]}
		self.assertIn(mine, employees)
		self.assertNotIn(theirs, employees)
		self.assertTrue(payload["can_edit"])
		self.assertIn(PORTAL_SHIFT_TYPE, [shift["name"] for shift in payload["shift_types"]])
		self.assertIn("assignment", payload["rows"][0]["cells"][0])

	def test_an_open_ended_assignment_from_wednesday_fills_wednesday_to_sunday(self):
		employee = self.fresh("wednesday")
		doc = seed_assignment(employee, "2019-03-06")
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		row = row_for(get_roster_week(WED_WEEK, mode="hr", search="Roster wednesday"), employee)
		self.assertEqual(cell_shifts(row), [None, None] + [PORTAL_SHIFT_TYPE] * 5)
		wednesday = row["cells"][2]
		self.assertEqual(wednesday["date"], "2019-03-06")
		self.assertEqual(wednesday["assignment"], doc.name)
		self.assertEqual(wednesday["assignment_start"], "2019-03-06")
		self.assertIsNone(wednesday["assignment_end"])
		self.assertEqual((wednesday["start_time"], wednesday["end_time"]), ("00:00", "23:59"))
		self.assertIsNone(row["cells"][0]["assignment"])

	def test_back_to_back_assignments_each_show_on_their_own_days(self):
		employee = self.fresh("split")
		seed_assignment(employee, "2019-04-01", "2019-04-03")
		seed_assignment(employee, "2019-04-04", shift_type=SECOND_SHIFT_TYPE)
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		row = row_for(get_roster_week(SPLIT_WEEK, mode="hr", search="Roster split"), employee)
		self.assertEqual(cell_shifts(row), [PORTAL_SHIFT_TYPE] * 3 + [SECOND_SHIFT_TYPE] * 4)
		self.assertEqual((row["cells"][3]["start_time"], row["cells"][3]["end_time"]), ("14:00", "22:00"))

	def test_a_past_inactive_assignment_still_shows_and_a_cancelled_one_does_not(self):
		# HRMS's nightly job flips an ended assignment to Inactive; a past
		# week must still show who worked it.
		employee = self.fresh("past")
		ended = seed_assignment(employee, "2019-05-06", "2019-05-08")
		frappe.db.set_value("Shift Assignment", ended.name, "status", "Inactive")
		cancelled = seed_assignment(employee, "2019-05-09", "2019-05-10")
		cancelled.cancel()
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		row = row_for(get_roster_week(PAST_WEEK, mode="hr", search="Roster past"), employee)
		self.assertEqual(cell_shifts(row), [PORTAL_SHIFT_TYPE] * 3 + [None] * 4)

	def test_an_empty_cell_carries_the_default_shift_hint_on_the_row(self):
		employee = self.fresh("default", default_shift=PORTAL_SHIFT_TYPE)
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		row = row_for(get_roster_week(EMPTY_WEEK, mode="hr", search="Roster default"), employee)
		self.assertEqual(cell_shifts(row), [None] * 7)
		self.assertEqual(
			row["default_shift"],
			{"shift_type": PORTAL_SHIFT_TYPE, "start_time": "00:00", "end_time": "23:59"},
		)
		self.assertIn("photo_url", row)
		self.assertEqual(row["initials"], "RD")

	def test_leave_and_holiday_markers_match_the_team_week(self):
		frappe.set_user(MANAGER_USER)
		team = next(r for r in get_my_team_week()["reports"] if r["employee"] == self.employee_name)
		roster = row_for(get_roster_week(mode="team"), self.employee_name)
		self.assertEqual(roster["leaves"], team["leaves"])
		self.assertEqual(roster["holidays"], team["holidays"])

	def test_hr_rows_are_capped_at_fifty_with_the_true_total_and_page_on(self):
		for index in range(52):
			roster_employee(f"cap-{index:02d}")
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		first = get_roster_week(EMPTY_WEEK, mode="hr", search="Roster cap-")
		self.assertEqual(len(first["rows"]), 50)
		self.assertEqual(first["total"], 52)
		rest = get_roster_week(EMPTY_WEEK, mode="hr", search="Roster cap-", start=50)
		self.assertEqual(len(rest["rows"]), 2)
		self.assertEqual(rest["start"], 50)

	def test_query_count_does_not_grow_per_row_beyond_holiday_resolution(self):
		for index in range(10):
			roster_employee(f"cap-{index:02d}")
		monday, sunday = get_week_bounds(EMPTY_WEEK)
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)

		def count(fn):
			calls = [0]
			original = frappe.db.sql

			def counted(*args, **kwargs):
				calls[0] += 1
				return original(*args, **kwargs)

			with patch.object(frappe.db, "sql", counted):
				fn()
			return calls[0]

		one = count(lambda: get_roster_week(EMPTY_WEEK, mode="hr", search="Roster cap-00"))
		ten = count(lambda: get_roster_week(EMPTY_WEEK, mode="hr", search="Roster cap-0"))
		# Holiday lists resolve per person (Team's rule, one cached Holiday
		# read per list); everything else -- shifts, leave, photos -- is one
		# read for the page.
		employee = roster_employee("cap-00")
		warm = {}
		_team_holiday_dates(employee, monday, sunday, warm)
		per_row = count(lambda: _team_holiday_dates(employee, monday, sunday, warm))
		self.assertGreater(one, 0)  # the counter is really counting
		self.assertLessEqual(ten - one, 9 * per_row)
