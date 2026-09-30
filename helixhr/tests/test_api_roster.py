from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from helixhr.api import (
	_shift_windows,
	_team_holiday_dates,
	assign_shift,
	cancel_shift_assignment,
	change_shift_assignment,
	end_shift_assignment,
	get_my_team_week,
	get_roster_week,
)
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


WRITE_WEEK = "2019-07-01"


class TestRosterWrites(RosterTestCase):
	"""U8 / R9, R10, R11. Every write runs as the company-scoped HR Manager
	fixture unless the test is about who is refused."""

	def setUp(self):
		super().setUp()
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)

	def assertPlainRefusal(self, sentence, fn, *args, **kwargs):
		with self.assertRaises(frappe.ValidationError) as caught:
			fn(*args, **kwargs)
		self.assertEqual(str(caught.exception), sentence)
		# HRMS's own msgprint (HTML, record links) never rides along.
		for entry in frappe.local.message_log:
			self.assertNotIn("<", str(entry))

	def test_hr_assigns_a_submitted_active_shift_that_check_in_resolves(self):
		employee = self.fresh("assign")
		today = frappe.utils.getdate()
		result = assign_shift(employee, PORTAL_SHIFT_TYPE, str(today), str(frappe.utils.add_days(today, 6)))
		doc = frappe.get_doc("Shift Assignment", result["name"])
		self.assertEqual((doc.docstatus, doc.status), (1, "Active"))
		self.assertEqual(result["start_date"], str(today))
		window, _upcoming = _shift_windows(employee, frappe.utils.now_datetime())
		self.assertTrue(window)
		self.assertEqual(window.shift_type.name, PORTAL_SHIFT_TYPE)

	def test_a_company_argument_is_never_read(self):
		employee = self.fresh("company")
		result = assign_shift(employee, PORTAL_SHIFT_TYPE, WRITE_WEEK, company=_ensure_other_company())
		self.assertEqual(frappe.db.get_value("Shift Assignment", result["name"], "company"), TEST_COMPANY)

	def test_an_overlapping_assign_is_refused_in_plain_words_and_inserts_nothing(self):
		employee = self.fresh("overlap")
		seed_assignment(employee, WRITE_WEEK)
		before = frappe.db.count("Shift Assignment", {"employee": employee})
		self.assertPlainRefusal(
			"This person already has a shift on some of those dates. End or change that one first.",
			assign_shift,
			employee,
			PORTAL_SHIFT_TYPE,
			"2019-07-03",
		)
		self.assertEqual(frappe.db.count("Shift Assignment", {"employee": employee}), before)

	def test_assign_refuses_an_unknown_shift_and_an_end_before_the_start(self):
		employee = self.fresh("bad-assign")
		self.assertPlainRefusal("Choose a shift.", assign_shift, employee, "_No Such Shift", WRITE_WEEK)
		self.assertPlainRefusal(
			"The end date can't be before the shift starts.",
			assign_shift,
			employee,
			PORTAL_SHIFT_TYPE,
			"2019-07-05",
			"2019-07-01",
		)
		self.assertFalse(frappe.db.count("Shift Assignment", {"employee": employee}))

	def test_end_sets_end_date_on_the_submitted_doc(self):
		doc = seed_assignment(self.fresh("end"), WRITE_WEEK)
		result = end_shift_assignment(doc.name, "2019-07-03")
		self.assertEqual(result["end_date"], "2019-07-03")
		saved = frappe.db.get_value("Shift Assignment", doc.name, ["end_date", "docstatus"], as_dict=True)
		self.assertEqual((str(saved.end_date), saved.docstatus), ("2019-07-03", 1))

	def test_end_before_the_start_is_refused(self):
		doc = seed_assignment(self.fresh("end-early"), WRITE_WEEK)
		self.assertPlainRefusal(
			"The end date can't be before the shift starts.", end_shift_assignment, doc.name, "2019-06-30"
		)
		self.assertIsNone(frappe.db.get_value("Shift Assignment", doc.name, "end_date"))

	def test_change_ends_the_old_one_the_day_before_and_starts_the_new_one(self):
		doc = seed_assignment(self.fresh("change"), WRITE_WEEK, "2019-07-31")
		result = change_shift_assignment(doc.name, "2019-07-04", SECOND_SHIFT_TYPE)
		self.assertEqual(result["ended"]["end_date"], "2019-07-03")
		self.assertEqual(result["assigned"]["start_date"], "2019-07-04")
		# The new one keeps the old end date rather than running open-ended.
		self.assertEqual(result["assigned"]["end_date"], "2019-07-31")
		self.assertEqual(result["assigned"]["shift_type"], SECOND_SHIFT_TYPE)
		self.assertEqual(frappe.db.get_value("Shift Assignment", result["assigned"]["name"], "docstatus"), 1)

	def test_a_failed_change_leaves_the_old_end_date_as_it_was(self):
		doc = seed_assignment(self.fresh("change-fail"), WRITE_WEEK)
		with patch("helixhr.api._new_roster_assignment", side_effect=frappe.ValidationError("Refused.")):
			self.assertPlainRefusal(
				"Refused.", change_shift_assignment, doc.name, "2019-07-04", SECOND_SHIFT_TYPE
			)
		self.assertIsNone(frappe.db.get_value("Shift Assignment", doc.name, "end_date"))

	def test_change_on_the_first_day_or_after_the_end_is_refused(self):
		doc = seed_assignment(self.fresh("change-edge"), WRITE_WEEK, "2019-07-10")
		self.assertPlainRefusal(
			"That is the day this shift starts. Cancel it and assign the new shift instead.",
			change_shift_assignment,
			doc.name,
			WRITE_WEEK,
			SECOND_SHIFT_TYPE,
		)
		self.assertPlainRefusal(
			"That date is after this shift ends.",
			change_shift_assignment,
			doc.name,
			"2019-07-11",
			SECOND_SHIFT_TYPE,
		)

	def test_cancel_succeeds_without_check_ins_and_is_refused_with_one(self):
		free = seed_assignment(self.fresh("cancel"), WRITE_WEEK, "2019-07-05")
		self.assertEqual(cancel_shift_assignment(free.name), {"name": free.name, "cancelled": True})
		self.assertEqual(frappe.db.get_value("Shift Assignment", free.name, "docstatus"), 2)

		employee = self.fresh("cancel-blocked")
		frappe.db.delete("Employee Checkin", {"employee": employee})
		held = seed_assignment(employee, WRITE_WEEK, "2019-07-05")
		frappe.get_doc(
			{
				"doctype": "Employee Checkin",
				"employee": employee,
				"time": "2019-07-02 10:00:00",
				"log_type": "IN",
				"shift": PORTAL_SHIFT_TYPE,
			}
		).insert(ignore_permissions=True)
		self.assertPlainRefusal(
			"This shift can't be cancelled because check-ins or attendance are already recorded against it. "
			"End it on a date instead.",
			cancel_shift_assignment,
			held.name,
		)
		self.assertEqual(frappe.db.get_value("Shift Assignment", held.name, "docstatus"), 1)

	def test_a_cancelled_assignment_is_not_found(self):
		doc = seed_assignment(self.fresh("gone"), WRITE_WEEK, "2019-07-05")
		cancel_shift_assignment(doc.name)
		with self.assertRaises(frappe.DoesNotExistError):
			end_shift_assignment(doc.name, "2019-07-03")

	def test_employees_and_managers_are_refused_every_write(self):
		employee = self.fresh("refused", reports_to=self.manager_name)
		doc = seed_assignment(employee, WRITE_WEEK)
		for user in (EMPLOYEE_USER, MANAGER_USER):
			frappe.set_user(user)
			for call in (
				lambda: assign_shift(employee, PORTAL_SHIFT_TYPE, "2019-08-01"),
				lambda: end_shift_assignment(doc.name, "2019-07-03"),
				lambda: change_shift_assignment(doc.name, "2019-07-04", SECOND_SHIFT_TYPE),
				lambda: cancel_shift_assignment(doc.name),
			):
				with self.assertRaises(frappe.PermissionError):
					call()
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.count("Shift Assignment", {"employee": employee}), 1)
		self.assertIsNone(frappe.db.get_value("Shift Assignment", doc.name, "end_date"))

	def test_a_company_scoped_hr_manager_cannot_write_for_another_company(self):
		theirs = roster_employee("scope-b", company=_ensure_other_company())
		frappe.db.delete("Shift Assignment", {"employee": theirs})
		doc = seed_assignment(theirs, WRITE_WEEK)
		with self.assertRaises(frappe.PermissionError):
			assign_shift(theirs, PORTAL_SHIFT_TYPE, "2019-08-01")
		with self.assertRaises(frappe.PermissionError):
			end_shift_assignment(doc.name, "2019-07-03")
		with self.assertRaises(frappe.PermissionError):
			cancel_shift_assignment(doc.name)
