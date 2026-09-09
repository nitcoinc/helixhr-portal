import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, date_diff, get_year_ending, get_year_start, getdate, today

from helixhr.api import get_my_holidays
from helixhr.tests.utils import EMPLOYEE_USER, make_test_employee_and_manager

# P3-U3. Deliberately years nothing else on the bench touches. The shared
# `_Test Holiday List` is assigned to the test company from the *current*
# year's first day, and `get_assigned_holiday_list` matches on
# `from_date <= as_on` with no upper bound -- so a past year is the only
# window where this suite can assert an exact set of holidays on a
# long-lived site without disturbing the fixture every other suite submits
# leave against (the plan's "unique date windows per test").
PAST_YEAR = 2019
NEXT_YEAR = 2020

COMPANY_LIST = "_Test Holidays Company"
EMPLOYEE_LIST = "_Test Holidays Employee"
DETAIL_LIST = "_Test Holidays Detail"
SPAN_LIST = "_Test Holidays Across Years"
CURRENT_LIST = "_Test Holidays This Year"

TEST_LISTS = [COMPANY_LIST, EMPLOYEE_LIST, DETAIL_LIST, SPAN_LIST, CURRENT_LIST]


class TestHelixHRHolidays(IntegrationTestCase):
	"""P3-R10 and P3-R11: the holidays HRMS resolves for one employee in one
	calendar year, and the "we cannot tell" answer when no list resolves.

	Every scenario seeds its own Holiday List and assignment and removes both
	again (`_clear`), because the resolver answers from whatever assignment
	has the latest `from_date` -- so one leftover row is enough to decide the
	next test's answer. The shared company assignment other suites submit
	leave against is never cancelled, only read.
	"""

	def setUp(self):
		self.employee_name, _, _, _ = make_test_employee_and_manager()
		self.company = frappe.db.get_value("Employee", self.employee_name, "company")
		self._clear()
		frappe.set_user(EMPLOYEE_USER)

	def tearDown(self):
		self._clear()

	def _clear(self):
		"""This suite's own Holiday Lists and their assignments, gone.

		Explicit rather than left to the transaction: the fixture helpers
		these tests share commit, so a leftover assignment from one test
		would decide the next one's answer -- which is exactly what it did
		the first time this file ran.
		"""
		frappe.set_user("Administrator")
		for name in frappe.get_all(
			"Holiday List Assignment", filters={"holiday_list": ["in", TEST_LISTS]}, pluck="name"
		):
			# Cancelled by field rather than by `doc.cancel()`: these are
			# fixtures being removed, not a lifecycle being exercised, and
			# cancel-then-delete on a submitted doc is the only way through.
			frappe.db.set_value("Holiday List Assignment", name, "docstatus", 2)
			frappe.delete_doc("Holiday List Assignment", name, force=True, ignore_permissions=True)
		for name in TEST_LISTS:
			if frappe.db.exists("Holiday List", name):
				frappe.delete_doc("Holiday List", name, force=True, ignore_permissions=True)
		frappe.db.commit()  # nosemgrep -- fixture cleanup, as in test_api_attendance

	# --- seeding -----------------------------------------------------------

	def _list(self, name, from_date, to_date, rows):
		"""A Holiday List with exactly the given rows. Recreated rather than
		reused: a leftover row from an earlier run would make every count in
		this file a guess."""
		frappe.set_user("Administrator")
		if frappe.db.exists("Holiday List", name):
			frappe.delete_doc("Holiday List", name, force=True, ignore_permissions=True)
		doc = frappe.get_doc(
			{
				"doctype": "Holiday List",
				"holiday_list_name": name,
				"from_date": str(from_date),
				"to_date": str(to_date),
				"holidays": [
					{
						"holiday_date": str(row["date"]),
						"description": row.get("description", "_Test Holiday"),
						"weekly_off": 1 if row.get("weekly_off") else 0,
						"is_half_day": 1 if row.get("half_day") else 0,
					}
					for row in rows
				],
			}
		)
		doc.insert(ignore_permissions=True)
		frappe.set_user(EMPLOYEE_USER)
		return doc.name

	def _assign(self, holiday_list, from_date, to_employee=True):
		frappe.set_user("Administrator")
		doc = frappe.get_doc(
			{
				"doctype": "Holiday List Assignment",
				"applicable_for": "Employee" if to_employee else "Company",
				"assigned_to": self.employee_name if to_employee else self.company,
				"holiday_list": holiday_list,
				"from_date": str(from_date),
			}
		)
		doc.insert(ignore_permissions=True)
		doc.submit()
		frappe.set_user(EMPLOYEE_USER)
		return doc.name

	# --- scenario 1: employee assignment wins, company is the fallback -----

	def test_company_assignment_is_the_fallback_and_the_employee_wins(self):
		self._list(
			COMPANY_LIST,
			f"{PAST_YEAR}-01-01",
			f"{PAST_YEAR}-12-31",
			[{"date": f"{PAST_YEAR}-03-05", "description": "Company day"}],
		)
		self._list(
			EMPLOYEE_LIST,
			f"{PAST_YEAR}-01-01",
			f"{PAST_YEAR}-12-31",
			[{"date": f"{PAST_YEAR}-03-06", "description": "Employee day"}],
		)

		# Company only: the fallback HRMS applies when the employee has no
		# assignment of their own.
		self._assign(COMPANY_LIST, f"{PAST_YEAR}-01-01", to_employee=False)
		payload = get_my_holidays(PAST_YEAR)
		self.assertTrue(payload["known"])
		self.assertEqual(payload["holiday_list"], COMPANY_LIST)
		self.assertEqual([row["date"] for row in payload["holidays"]], [f"{PAST_YEAR}-03-05"])

		# The employee's own assignment then wins for the same year.
		self._assign(EMPLOYEE_LIST, f"{PAST_YEAR}-01-01")
		payload = get_my_holidays(PAST_YEAR)
		self.assertEqual(payload["holiday_list"], EMPLOYEE_LIST)
		self.assertEqual([row["date"] for row in payload["holidays"]], [f"{PAST_YEAR}-03-06"])

	def test_a_mid_year_assignment_splits_the_year_between_two_lists(self):
		"""Which is why the year is resolved as date ranges rather than as one
		list name: HRMS's own `get_holiday_dates_between_range` splits at the
		later assignment's start date, and so does this."""
		self._list(
			COMPANY_LIST,
			f"{PAST_YEAR}-01-01",
			f"{PAST_YEAR}-12-31",
			[{"date": f"{PAST_YEAR}-02-01", "description": "Company day"}],
		)
		self._list(
			EMPLOYEE_LIST,
			f"{PAST_YEAR}-07-01",
			f"{PAST_YEAR}-12-31",
			[{"date": f"{PAST_YEAR}-08-01", "description": "Employee day"}],
		)
		self._assign(COMPANY_LIST, f"{PAST_YEAR}-01-01", to_employee=False)
		self._assign(EMPLOYEE_LIST, f"{PAST_YEAR}-07-01")

		payload = get_my_holidays(PAST_YEAR)
		self.assertEqual(
			[row["date"] for row in payload["holidays"]],
			[f"{PAST_YEAR}-02-01", f"{PAST_YEAR}-08-01"],
		)
		# The footnote names the list in force on the day being read, not
		# whichever one happened to be resolved first.
		self.assertEqual(payload["holiday_list"], EMPLOYEE_LIST)

	# --- scenario 2: weekly offs, half days, descriptions ------------------

	def test_weekly_offs_are_excluded_half_days_flagged_descriptions_stripped(self):
		self._list(
			DETAIL_LIST,
			f"{NEXT_YEAR}-01-01",
			f"{NEXT_YEAR}-12-31",
			[
				{"date": f"{NEXT_YEAR}-01-01", "description": "<b>New</b> Year&nbsp;Day"},
				{"date": f"{NEXT_YEAR}-01-04", "description": "Weekly Off", "weekly_off": True},
				{"date": f"{NEXT_YEAR}-01-15", "description": "Founders Day", "half_day": True},
			],
		)
		self._assign(DETAIL_LIST, f"{NEXT_YEAR}-01-01")

		payload = get_my_holidays(NEXT_YEAR)
		self.assertEqual(
			[row["date"] for row in payload["holidays"]],
			[f"{NEXT_YEAR}-01-01", f"{NEXT_YEAR}-01-15"],
		)
		first, second = payload["holidays"]
		# Markup HR pasted into the description never reaches the screen.
		self.assertNotIn("<", first["description"])
		self.assertIn("New Year", first["description"])
		self.assertFalse(first["is_half_day"])
		self.assertEqual(first["weekday"], "Wednesday")
		self.assertTrue(second["is_half_day"])

	# --- scenario 3: nothing resolves -> known: False ----------------------

	def test_no_resolvable_list_is_unknowable_not_an_empty_year(self):
		"""P3-R11. The same contract `get_my_attendance` uses for
		`working_days_known`: false means "cannot tell", and the page says so
		rather than claiming a year with no holidays in it."""
		# A year before any assignment on this bench begins. HRMS resolves a
		# list by `from_date <= as_on`, so nothing covers it.
		payload = get_my_holidays(PAST_YEAR)
		self.assertFalse(payload["known"])
		self.assertIsNone(payload["holiday_list"])
		self.assertEqual(payload["holidays"], [])
		self.assertIsNone(payload["next"])

		# And the current year with every assignment withdrawn -- an employee
		# whose company HR has not set up yet. `docstatus` is written
		# directly, not cancelled through the document, because this suite
		# must leave the shared company assignment exactly as it found it;
		# the transaction rolls this back.
		frappe.set_user("Administrator")
		withdrawn = frappe.get_all(
			"Holiday List Assignment",
			filters={"assigned_to": ["in", [self.employee_name, self.company]], "docstatus": 1},
			pluck="name",
		)
		try:
			for name in withdrawn:
				frappe.db.set_value("Holiday List Assignment", name, "docstatus", 2)
			frappe.set_user(EMPLOYEE_USER)
			payload = get_my_holidays()
			self.assertFalse(payload["known"])
			self.assertEqual(payload["holidays"], [])
		finally:
			frappe.set_user("Administrator")
			for name in withdrawn:
				frappe.db.set_value("Holiday List Assignment", name, "docstatus", 1)

	# --- scenario 4: the year boundary -------------------------------------

	def test_a_list_spanning_two_years_returns_only_the_year_asked_for(self):
		self._list(
			SPAN_LIST,
			f"{PAST_YEAR}-11-01",
			f"{NEXT_YEAR}-02-28",
			[
				{"date": f"{PAST_YEAR}-12-25", "description": "Christmas"},
				{"date": f"{NEXT_YEAR}-01-26", "description": "Republic Day"},
			],
		)
		self._assign(SPAN_LIST, f"{PAST_YEAR}-11-01")

		earlier = get_my_holidays(PAST_YEAR)
		self.assertEqual([row["date"] for row in earlier["holidays"]], [f"{PAST_YEAR}-12-25"])
		later = get_my_holidays(NEXT_YEAR)
		self.assertEqual([row["date"] for row in later["holidays"]], [f"{NEXT_YEAR}-01-26"])
		# Both years are offered by the chip, because the list covers both.
		self.assertIn(PAST_YEAR, later["years"])
		self.assertIn(NEXT_YEAR, later["years"])

	# --- the next holiday, and the bounds ----------------------------------

	def test_next_counts_from_the_sites_today_and_ignores_days_gone(self):
		year_start, year_end = get_year_start(today()), get_year_ending(today())
		upcoming = min(getdate(add_days(today(), 3)), getdate(year_end))
		self._list(
			CURRENT_LIST,
			str(year_start),
			str(year_end),
			[
				{"date": str(year_start), "description": "Already gone"},
				{"date": str(upcoming), "description": "Coming up"},
			],
		)
		self._assign(CURRENT_LIST, str(year_start))

		payload = get_my_holidays()
		self.assertEqual(payload["year"], getdate(today()).year)
		self.assertIsNotNone(payload["next"])
		self.assertEqual(payload["next"]["date"], str(upcoming))
		self.assertEqual(payload["next"]["days_until"], date_diff(upcoming, today()))
		# The past holiday is still listed -- it is the "Earlier this year"
		# group -- it is just never the next one. (Unless today is the year's
		# first day, when the two rows are one date.)
		self.assertIn(str(year_start), [row["date"] for row in payload["holidays"]])

	def test_an_impossible_year_is_refused_before_anything_is_read(self):
		self.assertRaises(frappe.ValidationError, get_my_holidays, 1999)
		self.assertRaises(frappe.ValidationError, get_my_holidays, 3000)
		# A junk value is not an error, it is "the year I am in" -- the page
		# never sends one, and refusing would be a dead end for no gain.
		self.assertEqual(get_my_holidays("not-a-year")["year"], getdate(today()).year)
