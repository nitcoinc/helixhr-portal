from datetime import date

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, get_first_day, getdate
from hrms.api import get_leave_balance_map

from helixhr.api import _LINKS_LIMIT, _get_celebrations, get_dashboard, get_my_documents
from helixhr.tests.utils import (
	EMPLOYEE_USER,
	ensure_test_company,
	make_celebration_employee,
	make_test_employee_and_manager,
)


class TestHelixHRDashboard(IntegrationTestCase):
	def setUp(self):
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()

	def test_employee_header_and_manager_name_for_session_user(self):
		frappe.set_user(EMPLOYEE_USER)
		result = get_dashboard()

		self.assertEqual(result["employee"]["name"], self.employee_name)
		self.assertEqual(result["employee"]["reports_to"], self.manager_name)
		self.assertEqual(result["employee"]["manager_name"], frappe.db.get_value("Employee", self.manager_name, "employee_name"))

	def test_leave_balances_matches_hrms_api_with_no_allocation(self):
		frappe.set_user(EMPLOYEE_USER)
		result = get_dashboard()

		self.assertEqual(result["leave_balances"], get_leave_balance_map())
		self.assertEqual(result["leave_balances"], {})

	def test_employee_argument_is_ignored(self):
		frappe.set_user(EMPLOYEE_USER)
		result = get_dashboard(employee=self.manager_name)

		self.assertEqual(result["employee"]["name"], self.employee_name)

	def test_guest_is_refused(self):
		# A direct in-process call bypasses Frappe's guest check entirely --
		# it lives in the HTTP dispatch layer (frappe.handler.is_whitelisted),
		# not in the whitelisted function itself. That layer's real check is
		# exactly this set membership (see frappe/__init__.py's whitelist()
		# decorator and is_whitelisted()) -- assert it directly instead of
		# a real nested HTTP request. A werkzeug.test.Client(application)
		# call from inside this same process reliably leaked a DB
		# connection that then hung every later test's first insert with
		# a MariaDB lock-wait timeout -- reproduced identically on a clean
		# GitHub Actions runner, not just the dev VM. Real HTTP-level
		# guest coverage already exists in Playwright (login-dashboard.spec.ts).
		self.assertNotIn(get_dashboard, frappe.guest_methods)

	def test_a_broken_section_names_itself_and_leaves_the_rest_of_the_page_alone(self):
		"""P2-U4 scenario 7 / P2-R25. A null section used to be
		indistinguishable from "nothing recorded yet", so an outage read as
		an empty month. The response now says which region failed, and only
		that region."""
		from unittest.mock import patch

		frappe.set_user(EMPLOYEE_USER)
		with patch("helixhr.api._get_attendance_summary", side_effect=Exception("boom")):
			result = get_dashboard()

		self.assertEqual(result["failed_sections"], ["attendance_this_month"])
		self.assertIsNone(result["attendance_this_month"])
		self.assertEqual(result["employee"]["name"], self.employee_name)
		self.assertIsNotNone(result["week"])
		self.assertIsNotNone(result["needs_you"])

	def test_a_healthy_page_names_no_failed_section(self):
		frappe.set_user(EMPLOYEE_USER)

		self.assertEqual(get_dashboard()["failed_sections"], [])

	def tearDown(self):
		frappe.set_user("Administrator")

	def _document_link(self, title, company=None):
		"""One catalogue row, as HR would add it in Desk."""
		frappe.get_doc(
			{
				"doctype": "HelixHR Document Link",
				"title": title,
				"url": "https://example.com/p4-u9",
				**({"company": company} if company else {}),
			}
		).insert(ignore_permissions=True)

	def test_documents_card_is_the_first_five_of_what_the_page_shows(self):
		"""P4-U9. The rail card is bounded at `_LINKS_LIMIT` and discloses the
		remainder, and it shows exactly the head of the list /documents shows.

		Asserted against that list rather than against fixed counts: this site
		carries document links planted outside the test transaction, so an
		absolute number would be asserting the state of the bench, not the
		contract.
		"""
		company = frappe.db.get_value("Employee", self.employee_name, "company")
		for index in range(_LINKS_LIMIT + 2):
			self._document_link(f"P4-U9 link {index}")
		self._document_link("P4-U9 own company link", company)

		frappe.set_user(EMPLOYEE_USER)
		page = get_my_documents()
		card = get_dashboard()["documents"]

		self.assertEqual(card["items"], page[:_LINKS_LIMIT])
		self.assertEqual(card["more"], len(page) - _LINKS_LIMIT)
		self.assertGreater(card["more"], 0, "the card must say how many it did not show")
		self.assertEqual(len(card["items"]), _LINKS_LIMIT)

	def test_documents_card_failure_is_named_and_isolated(self):
		"""A broken read names itself in `failed_sections` and leaves the rest
		of the page standing -- the same contract the other sections hold."""
		from unittest.mock import patch

		frappe.set_user(EMPLOYEE_USER)
		self.assertNotIn("documents", get_dashboard()["failed_sections"])

		with patch("helixhr.api._visible_document_links", side_effect=Exception("boom")):
			broken = get_dashboard()

		self.assertEqual(broken["failed_sections"], ["documents"])
		self.assertIsNone(broken["documents"])
		self.assertIsNotNone(broken["week"])
		self.assertIsNotNone(broken["needs_you"])


# P4-U5 / P4-R14. Its own Company, and that is the point rather than tidiness:
# the celebrations card is *everyone active in the caller's company*, so an
# assertion about who is on it is only stable if the test owns the whole
# company. `_Test Company` accumulates fixture people from a dozen other
# suites (the directory rows, the team-week report, the HR Manager employee),
# every one of them born on 1 January 2020 -- which makes any exact-membership
# assertion there pass eleven months of the year and fail in January.
CELEBRATION_COMPANY = "_Test Celebrations Co"


def _celebration_company():
	ensure_test_company()  # for the Warehouse Type a headless install lacks
	if not frappe.db.exists("Company", CELEBRATION_COMPANY):
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": CELEBRATION_COMPANY,
				"abbr": "TCEL",
				"default_currency": "USD",
				"country": "United States",
			}
		).insert(ignore_permissions=True)
	return CELEBRATION_COMPANY


class TestHelixHRCelebrations(IntegrationTestCase):
	"""P4-R14: this month's birthdays and work anniversaries, day and month
	only, years for anniversaries.

	Every date is derived from the run's own today, because a fixture pinned
	to a calendar date is only in "this month" for one month a year.
	"""

	def setUp(self):
		make_test_employee_and_manager()
		self.today = getdate()
		self.company = _celebration_company()
		# A month that is certainly not the current one, for the people who
		# must be absent.
		self.other_month = 1 if self.today.month != 1 else 7
		self.anchor = make_celebration_employee(
			"ANCHOR",
			self.company,
			date_of_birth=self._elsewhere(),
			date_of_joining=self._joined_elsewhere(),
		)

	def _elsewhere(self, day=4):
		"""A birthday in some other month -- present in the company, absent
		from the card."""
		return date(1990, self.other_month, day)

	def _joined_elsewhere(self, day=4):
		"""A joining date in some other month, years back -- absent from the
		anniversary list, and always after `_elsewhere()` so ERPNext's
		"joining after birth" validation is satisfied."""
		return date(self.today.year - 5, self.other_month, day)

	def _this_month(self, day, year=1990):
		return date(year, self.today.month, day)

	def _celebrations(self):
		return _get_celebrations(self.anchor, self.today)

	def _mine(self, entries, seeded):
		"""The rows this test seeded, in the order the card would draw them.

		Employee fixtures on a bench survive the run (Company and the naming
		series commit around them), so the company holds every earlier test's
		people too. Asserting "exactly these two, in this order" against the
		rows the test itself created is the same statement about scoping and
		ordering, and it is the one that stays true on a long-lived site.
		"""
		return [row["employee"] for row in entries if row["employee"] in set(seeded)]

	def _assert_card_order(self, entries):
		"""The whole list, however many rows the company has: today first,
		then up the month."""
		self.assertEqual(
			entries, sorted(entries, key=lambda row: (not row["is_today"], row["day"]))
		)

	def test_birthdays_this_month_only_in_day_order_and_with_no_year_or_age(self):
		late = make_celebration_employee(
			"BDAY-LATE", self.company, self._this_month(12), self._joined_elsewhere()
		)
		early = make_celebration_employee(
			"BDAY-EARLY", self.company, self._this_month(5), self._joined_elsewhere()
		)
		last_month = make_celebration_employee(
			"BDAY-LAST-MONTH",
			self.company,
			date(1990, getdate(add_to_date(get_first_day(self.today), months=-1)).month, 9),
			self._joined_elsewhere(),
		)
		no_dob = make_celebration_employee(
			"BDAY-NONE", self.company, self._this_month(7), self._joined_elsewhere()
		)
		# Employee.date_of_birth is mandatory-ish in practice but nullable in
		# the database: this is the shape of a record HR has left incomplete.
		frappe.db.set_value("Employee", no_dob, "date_of_birth", None, update_modified=False)

		birthdays = self._celebrations()["birthdays"]

		self.assertEqual(self._mine(birthdays, [early, late, last_month, no_dob]), [early, late])
		self._assert_card_order(birthdays)
		self.assertTrue(all(row["month"] == self.today.month for row in birthdays))
		# P4-KTD14: the projection is the privacy boundary. No birth year, no
		# age, no date -- not under any key, and not anywhere in the payload
		# as a value either.
		for row in birthdays:
			self.assertEqual(
				set(row),
				{"employee", "employee_name", "initials", "day", "month", "is_today"},
			)
			self.assertNotIn(1990, row.values())
			self.assertNotIn(self.today.year - 1990, row.values())

	def test_birthday_today_is_marked_and_sorts_first(self):
		if self.today.day <= 2:
			self.skipTest("needs a day earlier in the month to sort behind today")
		if (self.today.month, self.today.day) == (2, 29):
			self.skipTest("29 February is not a date in 1990")
		today_person = make_celebration_employee(
			"BDAY-TODAY", self.company, self._this_month(self.today.day), self._joined_elsewhere()
		)
		first_of_month = make_celebration_employee(
			"BDAY-1ST", self.company, self._this_month(1), self._joined_elsewhere()
		)

		birthdays = self._celebrations()["birthdays"]
		mine = self._mine(birthdays, [today_person, first_of_month])

		self.assertEqual(mine, [today_person, first_of_month])
		self._assert_card_order(birthdays)
		by_employee = {row["employee"]: row for row in birthdays}
		self.assertTrue(by_employee[today_person]["is_today"])
		self.assertFalse(by_employee[first_of_month]["is_today"])

	def test_an_anniversary_reports_years_completed_and_the_first_year_is_not_one(self):
		joined_three_years_ago = getdate(add_to_date(self.today, years=-3))
		veteran = make_celebration_employee(
			"ANNIV", self.company, self._elsewhere(), joined_three_years_ago
		)
		# Joined this year: HRMS counts an event only when the year is
		# strictly earlier, so a first year is not an anniversary.
		newcomer = make_celebration_employee(
			"ANNIV-THIS-YEAR",
			self.company,
			self._elsewhere(),
			self._this_month(1, year=self.today.year),
		)

		anniversaries = self._celebrations()["anniversaries"]

		row = next(row for row in anniversaries if row["employee"] == veteran)
		self.assertEqual(row["years"], 3)
		self.assertTrue(row["is_today"])
		self.assertEqual(row["day"], self.today.day)
		self.assertNotIn(joined_three_years_ago.year, row.values())
		self.assertNotIn(newcomer, [entry["employee"] for entry in anniversaries])

	def test_another_company_and_people_who_have_gone_are_absent(self):
		other = make_celebration_employee(
			"OTHER-CO", ensure_test_company(), self._this_month(3), self._joined_elsewhere()
		)
		left = make_celebration_employee(
			"LEFT", self.company, self._this_month(3), self._joined_elsewhere(), status="Left"
		)
		inactive = make_celebration_employee(
			"INACTIVE", self.company, self._this_month(3), self._joined_elsewhere(), status="Inactive"
		)

		listed = [row["employee"] for row in self._celebrations()["birthdays"]]

		self.assertNotIn(other, listed)
		self.assertNotIn(left, listed)
		self.assertNotIn(inactive, listed)

	def test_an_employee_with_no_company_gets_empty_lists_not_an_error(self):
		make_celebration_employee(
			"BDAY-VISIBLE", self.company, self._this_month(8), self._joined_elsewhere()
		)
		# Through the database: Company is mandatory on Employee, so this is
		# a record HR has left incomplete, never a save the portal would make.
		frappe.db.set_value("Employee", self.anchor, "company", None, update_modified=False)

		self.assertEqual(self._celebrations(), {"birthdays": [], "anniversaries": []})

	def test_the_dashboard_carries_the_section_and_isolates_its_failure(self):
		from unittest.mock import patch

		frappe.set_user(EMPLOYEE_USER)
		payload = get_dashboard()
		self.assertEqual(sorted(payload["celebrations"]), ["anniversaries", "birthdays"])
		self.assertNotIn("celebrations", payload["failed_sections"])

		with patch("helixhr.api._get_celebrations", side_effect=Exception("boom")):
			broken = get_dashboard()

		self.assertEqual(broken["failed_sections"], ["celebrations"])
		self.assertIsNone(broken["celebrations"])
		self.assertIsNotNone(broken["week"])
		self.assertIsNotNone(broken["needs_you"])

	def tearDown(self):
		frappe.set_user("Administrator")
