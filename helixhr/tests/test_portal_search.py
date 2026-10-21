"""Plan 2026-10-17-001 U5 (KTD5, R5, R7): `portal_search`, the command
palette's one rate-limited read -- who it finds, per identity, what each row
may carry, and that it never spends another page's bucket."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from helixhr import preflight
from helixhr.api import portal_search
from helixhr.tests.utils import (
	EMPLOYEE_USER,
	GRANT_HOLDER_USER,
	HR_MANAGER_EMPLOYEE_USER,
	IT_TEAM_USER,
	_make_role_user,
	clear_test_company_grants,
	ensure_baseline_company,
	ensure_email_admin_user,
	ensure_hr_manager_user,
	ensure_test_company,
	ensure_test_gender,
	make_test_company_grant,
	make_test_employee_and_manager,
	make_test_hr_manager_employee,
	make_test_it_user,
	make_test_user,
	remove_assignment,
)
from helixhr.utils import ORG_VIEWER_ROLE, RATE_LIMIT_POLICY, _rate_limit_key, reset_rate_limit

TAG = "Palettecheck"
FOREIGN_USER = "palette-foreign@helixhr.test"
SYSTEM_MANAGER_USER = "palette-sysman@helixhr.test"

# `_directory_projection`'s keys (test_api_directory.ALLOWED_KEYS minus the
# photo, which the palette never carries).
DIRECTORY_KEYS = {
	"name",
	"employee_name",
	"initials",
	"designation",
	"department",
	"manager",
	"manager_name",
	"email",
}
REQUEST_KEYS = {"name", "subject", "status", "category"}


def _as(user, fn, *args, **kwargs):
	frappe.set_user(user)
	try:
		return fn(*args, **kwargs)
	finally:
		frappe.set_user("Administrator")


def _person(suffix, company, status="Active", **fields):
	"""One Employee named "Palettecheck <suffix>" with no login. Idempotent on
	`employee_number`, like `_ensure_directory_employee`."""
	number = f"U5-PAL-{suffix}"
	name = frappe.db.get_value("Employee", {"employee_number": number}, "name")
	desired = {"status": status, "company": company, **fields}
	if status == "Left":
		desired.setdefault("relieving_date", "2024-12-31")
	if name:
		doc = frappe.get_doc("Employee", name)
		if any(doc.get(field) != value for field, value in desired.items()):
			doc.update(desired)
			doc.save(ignore_permissions=True)
		return name
	doc = frappe.get_doc(
		{
			"doctype": "Employee",
			"employee_number": number,
			"first_name": TAG,
			"last_name": suffix.title(),
			"date_of_birth": "1990-01-01",
			"date_of_joining": "2020-01-01",
			"gender": ensure_test_gender(),
			**desired,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def _request(employee, subject, routed_to_role="HR Manager", status="Open", creation=None):
	"""One HR Request inserted at the table, so no notification fires and
	nothing commits past the test's rollback (test_scope_hardening's way)."""
	category = "IT / Asset" if routed_to_role == "IT Team" else "HR Letter"
	doc = frappe.get_doc(
		{
			"doctype": "HR Request",
			"employee": employee,
			"category": category,
			"subject": subject,
			"details": "_Test palette request body",
			"naming_series": "HR-REQ-.YYYY.-",
			"status": status,
			"routed_to_role": routed_to_role,
			"hr_note": "_Test hidden note",
		}
	)
	doc.name = f"_TEST-U5-PAL-{frappe.generate_hash(length=8)}"
	if creation:
		doc.creation = doc.modified = creation
	doc.db_insert()
	return doc.name


def _names(rows):
	return {row["name"] for row in rows}


class _PortalSearch(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.other = ensure_baseline_company()
		self.employee, _, self.manager, _ = make_test_employee_and_manager()
		self.people = {
			"mailed": _person("Mailed", self.company, company_email="palettecheck-mailed@helixhr.test"),
			"plain": _person("Plain", self.company),
			"left": _person("Left", self.company, status="Left"),
			"inactive": _person("Inactive", self.company, status="Inactive"),
			"other": _person("Otherco", self.other),
		}
		self.foreign = make_test_user(FOREIGN_USER, self.other, employee_number="U5-PAL-FOREIGN")
		self._reset()

	def tearDown(self):
		frappe.set_user("Administrator")
		self._reset()

	def _reset(self):
		# The e2e fixtures commit grants; every test starts from none. Seeded
		# requests go too: a fixture helper can commit mid-test.
		clear_test_company_grants()
		frappe.db.delete("HR Request", {"name": ["like", "_TEST-U5-PAL-%"]})


class TestPortalSearchPeople(_PortalSearch):
	def test_a_plain_employee_finds_only_active_colleagues_in_their_own_company(self):
		found = _as(EMPLOYEE_USER, portal_search, TAG)
		self.assertEqual(_names(found["people"]), {self.people["mailed"], self.people["plain"]})
		self.assertEqual(found["failed"], [])

	def test_a_plain_employees_people_carry_directory_keys_only(self):
		rows = {row["name"]: row for row in _as(EMPLOYEE_USER, portal_search, TAG)["people"]}
		for row in rows.values():
			self.assertLessEqual(set(row), DIRECTORY_KEYS)
			for forbidden in ("employee_number", "company", "user_id", "company_email", "photo_url"):
				self.assertNotIn(forbidden, row)
		self.assertEqual(rows[self.people["mailed"]]["email"], "palettecheck-mailed@helixhr.test")
		self.assertNotIn("email", rows[self.people["plain"]])

	def test_a_reports_only_grant_finds_nobody_in_the_granted_company(self):
		make_test_user(GRANT_HOLDER_USER, self.company)
		make_test_company_grant(GRANT_HOLDER_USER, [self.other], level="Reports only")
		found = _as(GRANT_HOLDER_USER, portal_search, TAG)
		self.assertNotIn(self.people["other"], _names(found["people"]))
		self.assertIn(self.people["plain"], _names(found["people"]))

	def test_an_all_data_grant_finds_that_companys_people_and_none_of_its_requests(self):
		make_test_user(GRANT_HOLDER_USER, self.company)
		make_test_company_grant(GRANT_HOLDER_USER, [self.company, self.other])
		foreign_request = _request(self.foreign, f"{TAG} foreign letter")
		found = _as(GRANT_HOLDER_USER, portal_search, TAG)
		people = {row["name"]: row for row in found["people"]}
		self.assertEqual(people[self.people["other"]]["company"], self.other)
		self.assertNotIn(foreign_request, _names(found["requests"]) | _names(found["inbox"]))
		self.assertEqual(found["inbox"], [])

	def test_removed_org_viewer_rows_give_home_scope_only(self):
		make_test_user(GRANT_HOLDER_USER, self.company)
		make_test_company_grant(GRANT_HOLDER_USER, [self.company, self.other])
		# Plan 2026-10-19-001 U4: reach is the rows, not the grant (which
		# stays enabled here): without them the holder is a plain employee.
		remove_assignment(GRANT_HOLDER_USER, ORG_VIEWER_ROLE)
		found = _as(GRANT_HOLDER_USER, portal_search, TAG)
		self.assertEqual(_names(found["people"]), {self.people["mailed"], self.people["plain"]})
		for row in found["people"]:
			self.assertNotIn("company", row)

	def test_short_or_blank_queries_answer_empty_groups_without_a_query(self):
		calls = []
		original = frappe.db.sql

		def counted(*args, **kwargs):
			calls.append(args)
			return original(*args, **kwargs)

		frappe.set_user(EMPLOYEE_USER)
		with patch.object(frappe.db, "sql", counted):
			for q in (None, "", " ", "P", "  P  ", 42):
				self.assertEqual(
					portal_search(q), {"people": [], "requests": [], "inbox": [], "failed": []}, repr(q)
				)
		self.assertEqual(calls, [])

	def test_like_metacharacters_match_themselves(self):
		self.assertEqual(_as(EMPLOYEE_USER, portal_search, "%%")["people"], [])
		self.assertEqual(_as(EMPLOYEE_USER, portal_search, "__")["people"], [])

	def test_a_long_query_is_cut_at_sixty_characters(self):
		# A 60-character subject, searched with noise after it: the noise is
		# cut, so the subject is still found.
		subject = f"{TAG} sixty".ljust(60, "x")
		mine = _request(self.employee, subject)
		found = _as(EMPLOYEE_USER, portal_search, f"  {subject}NOISE")
		self.assertEqual(_names(found["requests"]), {mine})


class TestPortalSearchRequests(_PortalSearch):
	def setUp(self):
		super().setUp()
		make_test_hr_manager_employee()
		make_test_it_user()
		self.hr_routed = _request(self.employee, f"{TAG} letter for a visa")
		self.it_routed = _request(self.employee, f"{TAG} laptop broken", routed_to_role="IT Team")
		self.it_elsewhere = _request(self.foreign, f"{TAG} laptop elsewhere", routed_to_role="IT Team")

	def test_an_hr_manager_never_sees_an_it_routed_subject_in_the_inbox(self):
		found = _as(HR_MANAGER_EMPLOYEE_USER, portal_search, f"{TAG} laptop")
		self.assertEqual(found["inbox"], [])
		found = _as(HR_MANAGER_EMPLOYEE_USER, portal_search, f"{TAG} letter")
		self.assertEqual(_names(found["inbox"]), {self.hr_routed})

	def test_it_team_sees_only_it_routed_requests_in_its_own_company(self):
		found = _as(IT_TEAM_USER, portal_search, TAG)
		self.assertIn(self.it_routed, _names(found["inbox"]))
		self.assertNotIn(self.hr_routed, _names(found["inbox"]))
		self.assertNotIn(self.it_elsewhere, _names(found["inbox"]))

	def test_a_system_manager_with_no_routed_role_has_an_empty_inbox(self):
		_make_role_user(SYSTEM_MANAGER_USER, "System Manager")
		found = _as(SYSTEM_MANAGER_USER, portal_search, TAG)
		self.assertEqual(found["inbox"], [])
		self.assertEqual(found["failed"], [])

	def test_own_requests_never_include_a_colleagues_identical_subject(self):
		subject = f"{TAG} identical subject"
		mine = _request(self.employee, subject)
		_request(self.manager, subject)
		found = _as(EMPLOYEE_USER, portal_search, subject)
		self.assertEqual(_names(found["requests"]), {mine})

	def test_an_own_request_older_than_the_newest_twenty_is_found(self):
		old = _request(self.employee, f"{TAG}ancient request", creation="2001-01-01 09:00:00")
		for index in range(21):
			_request(self.employee, f"{TAG} filler {index:02d}")
		found = _as(EMPLOYEE_USER, portal_search, f"{TAG}ancient")
		self.assertEqual(_names(found["requests"]), {old})

	def test_request_rows_are_cut_to_the_allow_list(self):
		found = _as(HR_MANAGER_EMPLOYEE_USER, portal_search, f"{TAG} letter")
		self.assertTrue(found["inbox"])
		for row in found["inbox"]:
			self.assertEqual(set(row), REQUEST_KEYS)
		own = _as(EMPLOYEE_USER, portal_search, f"{TAG} letter")["requests"]
		self.assertTrue(own)
		for row in own:
			self.assertEqual(set(row), REQUEST_KEYS)

	def test_no_group_holds_more_than_five_rows(self):
		for index in range(7):
			_request(self.employee, f"{TAG} cap {index}")
		found = _as(EMPLOYEE_USER, portal_search, f"{TAG} cap")
		self.assertEqual(len(found["requests"]), 5)

	def test_a_portal_admin_with_no_employee_gets_nothing_and_no_error(self):
		admin = ensure_email_admin_user()
		before = frappe.db.count("Error Log")
		found = _as(admin, portal_search, TAG)
		self.assertEqual(found, {"people": [], "requests": [], "inbox": [], "failed": []})
		self.assertEqual(frappe.db.count("Error Log"), before)

	def test_desk_only_hr_finds_people_with_no_request_groups(self):
		found = _as(ensure_hr_manager_user(), portal_search, f"{TAG} Plain")
		self.assertEqual(_names(found["people"]), {self.people["plain"]})
		self.assertEqual((found["requests"], found["inbox"], found["failed"]), ([], [], []))

	def test_a_failing_group_is_named_and_the_others_still_answer(self):
		with patch("helixhr.api._portal_search_people", side_effect=RuntimeError("boom")):
			found = _as(EMPLOYEE_USER, portal_search, f"{TAG} letter")
		self.assertEqual(found["failed"], ["people"])
		self.assertEqual(_names(found["requests"]), {self.hr_routed})


class TestPortalSearchLimits(_PortalSearch):
	def tearDown(self):
		frappe.flags.pop("helixhr_enforce_rate_limits", None)
		for action in ("portal_search", "get_directory", "search_people", "get_request_work"):
			for user in (EMPLOYEE_USER, HR_MANAGER_EMPLOYEE_USER):
				reset_rate_limit(action, user)
		super().tearDown()

	def test_portal_search_has_its_own_policy_and_preflight_covers_it(self):
		self.assertEqual(RATE_LIMIT_POLICY["portal_search"], (60, 60))
		self.assertEqual(preflight.check_rate_limits()["status"], preflight.PASS)
		frappe.conf["helixhr_rate_limits"] = {"portal_search": [600, 60]}
		try:
			result = preflight.check_rate_limits()
		finally:
			frappe.conf.pop("helixhr_rate_limits", None)
		self.assertEqual(result["status"], preflight.FAIL)
		self.assertIn("portal_search", result["detail"])

	def test_the_call_after_the_limit_is_refused(self):
		limit, _seconds = RATE_LIMIT_POLICY["portal_search"]
		frappe.set_user(EMPLOYEE_USER)
		reset_rate_limit("portal_search")
		frappe.flags.helixhr_enforce_rate_limits = True
		for _ in range(limit):
			portal_search("P")
		with self.assertRaises(frappe.RateLimitExceededError):
			portal_search("P")

	def test_one_call_spends_only_its_own_bucket(self):
		make_test_hr_manager_employee()
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		for action in ("portal_search", "get_directory", "search_people", "get_request_work"):
			reset_rate_limit(action)
		frappe.flags.helixhr_enforce_rate_limits = True
		portal_search(TAG)
		user = frappe.session.user
		self.assertEqual(int(frappe.cache.get(_rate_limit_key("portal_search", user))), 1)
		for action in ("get_directory", "search_people", "get_request_work"):
			self.assertIsNone(frappe.cache.get(_rate_limit_key(action, user)), action)

	def test_an_hr_handler_call_costs_at_most_eight_queries(self):
		make_test_hr_manager_employee()
		_request(self.employee, f"{TAG} letter for a visa")
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		portal_search(TAG)  # warm role and meta caches
		calls = []
		original = frappe.db.sql

		def counted(*args, **kwargs):
			calls.append(args[0] if args else kwargs.get("query"))
			return original(*args, **kwargs)

		with patch.object(frappe.db, "sql", counted):
			found = portal_search(TAG)
		# Every group ran: People (HR's company scope), own requests and inbox.
		self.assertTrue(found["people"])
		self.assertTrue(found["inbox"])
		self.assertEqual(found["failed"], [])
		self.assertLessEqual(len(calls), 8, "\n".join(str(sql) for sql in calls))
