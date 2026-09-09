import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days

from helixhr.api import get_my_team_week, get_portal_bootstrap
from helixhr.tests.utils import (
	MANAGER_USER,
	OTHER_MANAGER_USER,
	make_test_employee_and_manager,
	make_test_user,
)

# P3-U7. The team week is a *projection*: a server-derived report set, an
# explicit field allow-list, and no leave reason anywhere in it (P3-R20,
# P3-R21, P3-R23).
#
# Weeks in a past year, deliberately, for everything except the one scenario
# that is about today. A long-lived bench carries real leave for the fixture
# employee in the current week from every other suite that has ever run, and
# an exact assertion about "this week" would be a guess -- so each scenario
# that asserts an exact set of rows owns a week nothing else touches (the
# plan's "unique date windows per test").
PAST_YEAR = 2019
SCOPE_WEEK = "2019-03-04"  # a Monday
SPAN_WEEK = "2019-04-01"  # a Monday
HOLIDAY_WEEK = "2019-05-06"  # a Monday

LEAVE_TYPE = "Casual Leave"
NAME_PREFIX = "_TEST-TEAM-"
# The one thing this payload must never carry, planted on every seeded row so
# its absence is a fact about the projection and not about the fixture.
REASON = "_Test team leave reason, which nobody on the team may read"

HOLIDAY_LIST = "_Test Team Holidays"


class TestHelixHRTeamWeek(IntegrationTestCase):
	"""P3-R20, P3-R21, P3-R23 and P3-AE11.

	Leave rows are seeded with `db_insert()` rather than through the
	Leave Application controller, on purpose: HRMS validates balance,
	allocation period, overlap and holidays on every save, so a full
	document would tie a *read* projection's assertions to a leave
	allocation covering a past year and to whatever else the bench has
	booked on those dates. What this endpoint reads is table columns, and
	that is exactly what these rows are. Nothing commits, so the
	IntegrationTestCase transaction removes them.
	"""

	def setUp(self):
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()
		self.company = frappe.db.get_value("Employee", self.manager_name, "company")

		frappe.set_user("Administrator")
		# The fixture employee is the manager's one active report. Other
		# suites move `reports_to` around and restore it; asserting the
		# precondition here is cheaper than debugging a payload that is
		# right about a team this test did not expect.
		frappe.db.set_value("Employee", self.employee_name, "reports_to", self.manager_name)

		# Z: an employee of the same company who reports to nobody. Also the
		# identity scenario 3 calls with -- a login with a real Employee and
		# no direct reports is the case P3-KTD11 gates the page on.
		self.other_name = make_test_user(OTHER_MANAGER_USER, self.company)
		frappe.db.set_value("Employee", self.other_name, "reports_to", None)

		# Y: a report whose Employee is no longer Active. Created without a
		# User because it is never logged in as -- it exists to be left out.
		self.left_name = self._left_report()

		self.seeded = []
		frappe.set_user(MANAGER_USER)

	def tearDown(self):
		frappe.set_user("Administrator")

	# --- seeding -----------------------------------------------------------

	def _left_report(self):
		"""A direct report with `status == "Left"`. The status is written
		after the insert because Employee's own validation demands a
		relieving date for a leaver, which is HR's data and not this test's
		subject."""
		name = frappe.db.get_value("Employee", {"employee_number": "_test-team-left"}, "name")
		if not name:
			doc = frappe.get_doc(
				{
					"doctype": "Employee",
					"employee_number": "_test-team-left",
					"first_name": "Left",
					"last_name": "Report",
					"company": self.company,
					"date_of_birth": "1990-01-01",
					"date_of_joining": "2020-01-01",
					"gender": frappe.db.get_value("Gender", {}, "name"),
					"status": "Active",
				}
			)
			doc.insert(ignore_permissions=True)
			name = doc.name
		frappe.db.set_value("Employee", name, {"reports_to": self.manager_name, "status": "Left"})
		return name

	def _seed_leave(
		self,
		employee,
		from_date,
		to_date,
		suffix,
		approved=True,
		half_day=False,
		half_day_date=None,
	):
		doc = frappe.get_doc(
			{
				"doctype": "Leave Application",
				"employee": employee,
				"leave_type": LEAVE_TYPE,
				"from_date": str(from_date),
				"to_date": str(to_date),
				"half_day": 1 if half_day else 0,
				"half_day_date": str(half_day_date) if half_day_date else None,
				"description": REASON,
				"status": "Approved" if approved else "Open",
				"docstatus": 1 if approved else 0,
			}
		)
		doc.name = f"{NAME_PREFIX}{suffix}"
		doc.db_insert()
		self.seeded.append(doc.name)
		return doc.name

	# --- reading -----------------------------------------------------------

	def _week(self, week_start):
		frappe.set_user(MANAGER_USER)
		return get_my_team_week(week_start)

	def _row(self, payload, employee):
		return next((row for row in payload["reports"] if row["employee"] == employee), None)

	def _seeded_leaves(self, payload, employee):
		"""Only the rows this test put there. A bench that has been used
		carries other leave for the fixture employee."""
		row = self._row(payload, employee)
		return [leave for leave in (row or {}).get("leaves", []) if leave["name"] in self.seeded]

	# --- scenario 1 (P3-AE11): who is in the payload, and what it says -----

	def test_only_active_direct_reports_and_no_leave_reason(self):
		mine = self._seed_leave(self.employee_name, SCOPE_WEEK, add_days(SCOPE_WEEK, 1), "scope-x")
		waiting = self._seed_leave(
			self.employee_name,
			add_days(SCOPE_WEEK, 3),
			add_days(SCOPE_WEEK, 3),
			"scope-x-waiting",
			approved=False,
		)
		self._seed_leave(self.left_name, SCOPE_WEEK, SCOPE_WEEK, "scope-y")
		self._seed_leave(self.other_name, SCOPE_WEEK, SCOPE_WEEK, "scope-z")

		payload = self._week(SCOPE_WEEK)

		self.assertEqual(payload["week_start"], SCOPE_WEEK)
		self.assertEqual(payload["week_end"], str(add_days(SCOPE_WEEK, 6)))

		listed = [row["employee"] for row in payload["reports"]]
		self.assertIn(self.employee_name, listed)
		# Y is a report but has left; Z is neither.
		self.assertNotIn(self.left_name, listed)
		self.assertNotIn(self.other_name, listed)

		leaves = {leave["name"]: leave for leave in self._seeded_leaves(payload, self.employee_name)}
		self.assertEqual(set(leaves), {mine, waiting})

		approved = leaves[mine]
		self.assertEqual(approved["leave_type"], LEAVE_TYPE)
		self.assertEqual(approved["from_date"], SCOPE_WEEK)
		self.assertEqual(approved["to_date"], str(add_days(SCOPE_WEEK, 1)))
		self.assertFalse(approved["waiting"])

		# P3-R21. Not "empty" and not "stripped": the key does not exist,
		# because the field is never selected.
		for leave in leaves.values():
			self.assertNotIn("description", leave)
			self.assertNotIn("status", leave)
			self.assertNotIn("docstatus", leave)

		# A submitted-but-not-Approved row, and an Approved row that is still
		# docstatus 0, are both "waiting" -- the second is the one HRMS
		# leaves behind when an approver sets the status without submitting.
		self.assertTrue(leaves[waiting]["waiting"])
		self.assertGreaterEqual(payload["waiting_count"], 1)
		# The avatar's two letters, as on Approvals -- a second reading of a
		# name that is always printed beside it.
		self.assertTrue(self._row(payload, self.employee_name)["initials"])

	def test_an_approved_status_that_was_never_submitted_is_still_waiting(self):
		"""P3-AE11's last clause. `status == "Approved"` with `docstatus 0`
		is a decision that was typed and not sent, so the calendar must not
		draw it as settled."""
		name = self._seed_leave(self.employee_name, SCOPE_WEEK, SCOPE_WEEK, "unsent")
		frappe.db.set_value("Leave Application", name, "docstatus", 0)

		leaves = self._seeded_leaves(self._week(SCOPE_WEEK), self.employee_name)
		self.assertEqual([leave["waiting"] for leave in leaves], [True])

	# --- scenario 2: half days, and a leave that outgrows the week ---------

	def test_half_day_carries_its_date_and_a_long_leave_is_clipped_to_each_week(self):
		half = self._seed_leave(
			self.employee_name,
			SPAN_WEEK,
			add_days(SPAN_WEEK, 1),
			"half",
			half_day=True,
			half_day_date=add_days(SPAN_WEEK, 1),
		)
		# Wednesday of this week to Tuesday of the next one.
		span = self._seed_leave(self.employee_name, add_days(SPAN_WEEK, 2), add_days(SPAN_WEEK, 8), "span")

		first = {leave["name"]: leave for leave in self._seeded_leaves(self._week(SPAN_WEEK), self.employee_name)}
		self.assertTrue(first[half]["half_day"])
		self.assertEqual(first[half]["half_day_date"], str(add_days(SPAN_WEEK, 1)))
		self.assertFalse(first[span]["half_day"])
		self.assertIsNone(first[span]["half_day_date"])

		# Week one: the bar starts on Wednesday and stops at Sunday, while
		# the true range it reports is the whole leave.
		self.assertEqual(first[span]["start"], str(add_days(SPAN_WEEK, 2)))
		self.assertEqual(first[span]["end"], str(add_days(SPAN_WEEK, 6)))
		self.assertEqual(first[span]["from_date"], str(add_days(SPAN_WEEK, 2)))
		self.assertEqual(first[span]["to_date"], str(add_days(SPAN_WEEK, 8)))

		# Week two: the same leave, clipped from the other end. Asking for a
		# mid-week date proves the Monday normalisation as well.
		second_week = add_days(SPAN_WEEK, 9)
		second = {
			leave["name"]: leave
			for leave in self._seeded_leaves(self._week(second_week), self.employee_name)
		}
		self.assertIn(span, second)
		self.assertNotIn(half, second)
		self.assertEqual(second[span]["start"], str(add_days(SPAN_WEEK, 7)))
		self.assertEqual(second[span]["end"], str(add_days(SPAN_WEEK, 8)))

	def test_the_week_is_normalised_to_monday(self):
		payload = self._week(add_days(SCOPE_WEEK, 4))
		self.assertEqual(payload["week_start"], SCOPE_WEEK)
		self.assertEqual(payload["days"][0]["date"], SCOPE_WEEK)
		self.assertEqual(
			[day["is_weekend"] for day in payload["days"]],
			[False, False, False, False, False, True, True],
		)

	# --- scenario 3: no reports, no page ----------------------------------

	def test_a_manager_with_no_reports_is_refused_and_the_nav_item_is_hidden(self):
		frappe.set_user(OTHER_MANAGER_USER)
		self.assertFalse(get_portal_bootstrap()["has_reports"])
		with self.assertRaises(frappe.PermissionError):
			get_my_team_week()

		# And the same bootstrap flag is what puts the page in front of a
		# manager who does have reports (P3-KTD11).
		frappe.set_user(MANAGER_USER)
		self.assertTrue(get_portal_bootstrap()["has_reports"])

	# --- scenario 4: holidays shade the week ------------------------------

	def test_holidays_mark_the_columns_and_the_person(self):
		"""The column header carries the manager's own holiday list, and each
		report carries theirs -- HRMS resolves a list per employee, so a team
		split across two lists must not be shaded from one of them."""
		holiday = str(add_days(HOLIDAY_WEEK, 2))
		self._holiday_list(holiday)
		self._assign(self.manager_name)

		payload = self._week(HOLIDAY_WEEK)
		shaded = [day["date"] for day in payload["days"] if day["is_holiday"]]
		self.assertEqual(shaded, [holiday])
		# The report is on no list at all in this year, so nothing of theirs
		# is dimmed yet.
		self.assertEqual(self._row(payload, self.employee_name)["holidays"], [])

		self._assign(self.employee_name)
		payload = self._week(HOLIDAY_WEEK)
		self.assertEqual(self._row(payload, self.employee_name)["holidays"], [holiday])

	def _holiday_list(self, holiday_date):
		frappe.set_user("Administrator")
		if frappe.db.exists("Holiday List", HOLIDAY_LIST):
			frappe.delete_doc("Holiday List", HOLIDAY_LIST, force=True, ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Holiday List",
				"holiday_list_name": HOLIDAY_LIST,
				"from_date": f"{PAST_YEAR}-01-01",
				"to_date": f"{PAST_YEAR}-12-31",
				"holidays": [
					{"holiday_date": holiday_date, "description": "_Test Team Holiday"},
					# A weekly off, which is a Holiday row in HRMS too. It
					# must not read as a named holiday: the weekend is
					# already dimmed as a weekend.
					{
						"holiday_date": str(add_days(HOLIDAY_WEEK, 5)),
						"description": "Weekly Off",
						"weekly_off": 1,
					},
				],
			}
		).insert(ignore_permissions=True)

	def _assign(self, employee):
		frappe.set_user("Administrator")
		doc = frappe.get_doc(
			{
				"doctype": "Holiday List Assignment",
				"applicable_for": "Employee",
				"assigned_to": employee,
				"holiday_list": HOLIDAY_LIST,
				"from_date": f"{PAST_YEAR}-01-01",
			}
		)
		doc.insert(ignore_permissions=True)
		doc.submit()
		frappe.set_user(MANAGER_USER)

	# --- who is out today --------------------------------------------------

	def test_out_today_names_the_report_and_another_week_says_nothing(self):
		"""The field block is about today, and the rows in hand only cover
		the week on screen -- so a manager paging back a month must not be
		told the office is full."""
		# The server's own today for this user, which is the same value the
		# payload reports and the browser renders (P2-AE3).
		today = get_portal_bootstrap()["today"]
		name = self._seed_leave(self.employee_name, today, today, "today", approved=False)

		payload = self._week(None)
		self.assertTrue(payload["is_current_week"])
		self.assertEqual(payload["today"], today)
		self.assertIn(self.employee_name, [row["employee"] for row in payload["out_today"]])
		mine = next(row for row in payload["out_today"] if row["employee"] == self.employee_name)
		self.assertEqual(mine["leave_type"], LEAVE_TYPE)
		self.assertTrue(mine["waiting"])
		self.assertNotIn("description", mine)
		self.assertIn(name, [leave["name"] for leave in self._seeded_leaves(payload, self.employee_name)])

		past = self._week(SCOPE_WEEK)
		self.assertFalse(past["is_current_week"])
		self.assertEqual(past["out_today"], [])

	def test_total_reports_is_the_exact_count(self):
		payload = self._week(SCOPE_WEEK)
		self.assertEqual(
			payload["total_reports"],
			frappe.db.count("Employee", {"reports_to": self.manager_name, "status": "Active"}),
		)
		self.assertGreaterEqual(payload["total_reports"], 1)
