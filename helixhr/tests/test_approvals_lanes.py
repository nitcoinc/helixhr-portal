"""Plan 2026-10-15-002 U1 and U4: lane tags and the decided window on
`get_my_approvals` (R1-R3, KTD1, KTD8), and HR User's flag and read-only
timesheet detail (R7, R14, KTD4).

The lane tests replace the per-kind collectors with stubs: what is under test
is which half of `_approval_summaries` tags a row with which lane, not the
collectors themselves, which `test_api_approvals` already covers."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from helixhr import api
from helixhr.api import act_on_approval, get_approval_detail, get_my_approvals, get_portal_bootstrap
from helixhr.tests.test_api_approvals import token
from helixhr.tests.test_pending_by_approver import make_timesheet
from helixhr.tests.utils import (
	_make_role_user,
	ensure_baseline_company,
	ensure_test_company,
	make_test_hr_manager_employee,
	make_test_it_user,
	make_test_user,
)

HR_USER = "lanes-hr-user@helixhr.test"
MANAGER = "lanes-manager@helixhr.test"
REPORT = "lanes-report@helixhr.test"


def _as(user, fn, *args, **kwargs):
	frappe.set_user(user)
	try:
		return fn(*args, **kwargs)
	finally:
		frappe.set_user("Administrator")


def _row(kind, name, employee, **extra):
	return {
		"id": f"{kind}:{name}",
		"kind": kind,
		"name": name,
		"employee": employee,
		"employee_name": employee,
		"initials": "XX",
		"sent_on": "2026-01-01",
		**extra,
	}


class TestApprovalLanes(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.hr_employee, self.hr_manager = make_test_hr_manager_employee()

	def _stubbed(self, team, hr):
		"""Patch both halves: the line-manager collectors return `team`, the
		HR collectors return `hr` (requests through the HR Request kind)."""
		patches = [patch.object(api, "_APPROVAL_SUMMARY_COLLECTORS", ((lambda employee, today, count_only=False: list(team), 99),))]
		for doctype, kind in api._APPROVAL_KINDS.items():
			rows = [row for row in hr if row["doctype"] == doctype]
			patches.append(patch.dict(kind, {"hr_queue": lambda employee, today, count_only=False, rows=rows: list(rows)}))
		return patches

	def _lanes(self, team, hr):
		patches = self._stubbed(team, hr)
		for active in patches:
			active.start()
			self.addCleanup(active.stop)
		result = _as(self.hr_manager, get_my_approvals)
		return {row["name"]: row["lane"] for row in result["pending"]}, result

	def test_hr_manager_who_manages_sees_team_and_hr_lanes_and_no_requests(self):
		"""Plan 2026-10-16-001 U3 / KTD1: requests leave Approvals, so IT and HR
		requests never appear in rows, lanes or counts, and counts.lanes has no it key."""
		team = [_row("leave", "L-TEAM", "E1", doctype="Leave Application")]
		hr = [
			_row("leave", "L-HR", "E2", doctype="Leave Application", for_hr=True),
			_row("request", "R-IT", "E3", doctype="HR Request", routed_to_role="IT Team"),
			_row("request", "R-HR", "E4", doctype="HR Request", routed_to_role="HR Manager"),
		]
		lanes, result = self._lanes(team, hr)
		self.assertEqual(lanes, {"L-TEAM": "team", "L-HR": "hr"})
		self.assertEqual(result["counts"]["lanes"], {"team": 1, "hr": 1})
		self.assertEqual(result["counts"]["categories"], [])

	def test_a_roster_request_rides_the_hr_lane_and_counts_as_its_own_kind(self):
		"""Plan 2026-10-31-001 U9: HR's alone -- the HR lane, never the team
		lane, even though its `employee` is a manager."""
		team = [_row("leave", "L-TEAM", "E1", doctype="Leave Application")]
		hr = [_row("roster", "HRC-1", "E5", doctype="HelixHR Roster Change", for_hr=True)]
		lanes, result = self._lanes(team, hr)
		self.assertEqual(lanes, {"L-TEAM": "team", "HRC-1": "hr"})
		self.assertEqual(result["counts"]["lanes"], {"team": 1, "hr": 1})
		counts = {kind["name"]: kind["count"] for kind in result["counts"]["kinds"]}
		self.assertEqual(counts["roster"], 1)

	def test_every_row_has_a_lane_and_the_lane_counts_are_the_home_count(self):
		team = [_row("timesheet", "T-1", "E1", doctype="Timesheet")]
		hr = [_row("attendance", "A-1", "E2", doctype="Attendance Request")]
		lanes, result = self._lanes(team, hr)
		self.assertTrue(all(lanes.values()))
		self.assertEqual(sum(result["counts"]["lanes"].values()), result["total"])
		rows, _capped, _counts = _as(self.hr_manager, api._approval_summaries, self.hr_employee)
		self.assertEqual(len(rows), result["total"])

	def test_an_it_holder_has_empty_approvals_queue(self):
		"""Plan 2026-10-16-001 U3 / KTD1: requests leave Approvals, so an IT Team
		holder with no reports has an empty queue in get_my_approvals."""
		_it_employee, it_user = make_test_it_user()
		with patch.object(
			api,
			"_hr_request_summaries",
			return_value=[_row("request", "R-IT", "E3", doctype="HR Request", routed_to_role="IT Team")],
		):
			result = _as(it_user, get_my_approvals)
		self.assertEqual(result["pending"], [])
		self.assertEqual(result["total"], 0)

	def test_hr_managers_own_timesheet_never_reaches_their_hr_lane(self):
		own = make_timesheet(self, self.hr_employee, 3, state="Pending HR")
		names = [
			row["name"]
			for row in _as(
				self.hr_manager,
				api._APPROVAL_KINDS["Timesheet"]["hr_queue"],
				self.hr_employee,
				api._as_date(api.user_today()),
			)
		]
		self.assertNotIn(own, names)

	def test_decided_is_fourteen_days_twenty_five_rows_and_no_timesheets(self):
		self.assertEqual(api._DECIDED_DAYS, 14)
		self.assertNotIn(api._decided_timesheets, api._DECIDED_COLLECTORS)
		seen = []

		def collector(employee, since):
			seen.append(since)
			return [
				api._decided_row("leave", f"L-{index}", "E", "Leave", None, None, "Approved", f"2026-10-{index % 28 + 1:02d}")
				for index in range(30)
			]

		with patch.object(api, "_DECIDED_COLLECTORS", (collector,)):
			decided = _as(self.hr_manager, api._recently_decided, self.hr_employee)
		self.assertEqual(len(decided), 25)
		self.assertEqual(str(seen[0]), str(frappe.utils.add_days(api.user_today(), -14)))

	def test_search_runs_every_collector_with_no_window_and_timesheets(self):
		"""Approvals search: the receipt's collectors plus timesheets run with
		`since=None` and the pattern, capped at 50; without a pattern the
		receipt is exactly what it was."""
		calls = []

		def collector(label, count):
			def collect(employee, since, pattern=None):
				calls.append((label, since, pattern))
				return [
					api._decided_row("leave", f"{label}-{index}", "E", "Leave", None, None, "Approved", f"2026-09-{index % 28 + 1:02d}")
					for index in range(count)
				]

			return collect

		with (
			patch.object(api, "_DECIDED_COLLECTORS", (collector("leave", 30),)),
			patch.object(api, "_DECIDED_STAMPED_COLLECTORS", (collector("change", 5),)),
			patch.object(api, "_decided_timesheets", collector("timesheet", 30)),
		):
			found = _as(self.hr_manager, api._recently_decided, self.hr_employee, "%asha%")
			self.assertEqual(sorted(calls), [("change", None, "%asha%"), ("leave", None, "%asha%"), ("timesheet", None, "%asha%")])
			self.assertEqual(len(found), 50)
			stamps = [row["decided_on"] for row in found]
			self.assertEqual(stamps, sorted(stamps, reverse=True))

			calls.clear()
			receipt = _as(self.hr_manager, api._recently_decided, self.hr_employee)
			self.assertEqual({label for label, _since, _pattern in calls}, {"leave", "change"})
			self.assertTrue(all(since and pattern is None for _label, since, pattern in calls))
			self.assertEqual(len(receipt), 25)

	def test_search_narrows_pending_by_person_type_kind_or_name(self):
		team = [
			_row("leave", "L-ONE", "E1", employee_name="Asha Rao", leave_type="Casual Leave", doctype="Leave Application"),
			_row("timesheet", "TS-TWO", "E2", employee_name="Bilal Khan", doctype="Timesheet"),
			_row("attendance", "AR-THREE", "E3", employee_name="Chen Wu", reason="Work From Home", doctype="Attendance Request"),
		]
		patches = self._stubbed(team, [])
		for active in patches:
			active.start()
			self.addCleanup(active.stop)
		plain = _as(self.hr_manager, get_my_approvals)
		for q, expected in (
			("asha", ["L-ONE"]),
			("CASUAL", ["L-ONE"]),
			("timesheet", ["TS-TWO"]),
			("att", ["AR-THREE"]),
			# The kind matches as a prefix only: "an" is in "attendance", but
			# here it is Bilal Khan's name being typed.
			("an", ["TS-TWO"]),
			("from home", ["AR-THREE"]),
			("ar-three", ["AR-THREE"]),
			("nobody", []),
		):
			found = _as(self.hr_manager, get_my_approvals, q=q)
			self.assertTrue(found["searched"], q)
			self.assertEqual([row["name"] for row in found["pending"]], expected, q)
			self.assertEqual(found["total"], len(expected), q)
			self.assertEqual(found["counts"], plain["counts"], q)
		self.assertEqual(_as(self.hr_manager, get_my_approvals, q="a"), plain)

	def test_search_finds_an_old_timesheet_decision_only_for_its_decider(self):
		"""Timesheets are out of the receipt (KTD8) but in the history, and
		`_decided_by_me` still scopes HR's to what HR itself decided."""
		other = make_test_user(REPORT, ensure_test_company())
		mine = make_timesheet(self, other, None, state="Approved")
		theirs = make_timesheet(self, other, None, state="Approved", week_offset=1)
		old = frappe.utils.add_days(frappe.utils.now_datetime(), -40)
		frappe.db.set_value("Timesheet", mine, {"modified": old, "modified_by": self.hr_manager}, update_modified=False)
		frappe.db.set_value("Timesheet", theirs, {"modified": old, "modified_by": "Administrator"}, update_modified=False)
		person = frappe.db.get_value("Employee", other, "employee_name")

		receipt = _as(self.hr_manager, get_my_approvals)
		self.assertNotIn(mine, [row["name"] for row in receipt["decided"]])
		found = {row["name"]: row for row in _as(self.hr_manager, get_my_approvals, q=person)["decided"]}
		self.assertEqual(found[mine]["kind"], "timesheet")
		self.assertEqual(found[mine]["status"], "Approved")
		self.assertNotIn(theirs, found)

	def _raw(self, doctype, **fields):
		doc = frappe.get_doc({"doctype": doctype, "name": f"LANES-DEC-{frappe.generate_hash(length=8)}", **fields})
		doc.db_insert()
		return doc.name

	def test_decided_includes_change_and_routed_decisions(self):
		_it_employee, it_user = make_test_it_user()
		now = frappe.utils.now_datetime()
		old = frappe.utils.add_days(now, -20)
		other = make_test_user(REPORT, ensure_test_company())
		change = self._raw(
			"HelixHR Timesheet Change",
			employee=other,
			employee_name="Lanes Report",
			week_start=frappe.utils.getdate(),
			status="Accepted",
			decided_by=self.hr_manager,
			decided_on=now,
		)
		stale = self._raw(
			"HelixHR Timesheet Change",
			employee=other,
			employee_name="Lanes Report",
			week_start=frappe.utils.getdate(),
			status="Declined",
			decided_by=self.hr_manager,
			decided_on=old,
		)
		routed = self._raw(
			"HR Request", employee=other, subject="Laptop", status="Done", picked_up_by=it_user, closed_on=now
		)
		open_routed = self._raw(
			"HR Request", employee=other, subject="Mouse", status="In Progress", picked_up_by=it_user
		)

		hr_decided = {row["name"]: row for row in _as(self.hr_manager, api._recently_decided, self.hr_employee)}
		self.assertEqual(hr_decided[change]["kind"], "change")
		self.assertEqual(hr_decided[change]["status"], "Accepted")
		self.assertNotIn(stale, hr_decided)
		self.assertNotIn(routed, hr_decided)

		# An IT holder has no Employee role; requests are dropped from Decided.
		it_decided = {row["name"]: row for row in _as(it_user, api._recently_decided, _it_employee)}
		self.assertNotIn(routed, it_decided)
		self.assertNotIn(open_routed, it_decided)
		self.assertNotIn(change, it_decided)

	def test_all_pending_flag_matches_the_endpoint_gate(self):
		self.assertTrue(_as(self.hr_manager, get_portal_bootstrap)["can_see_all_pending"])
		_hr, hr_user = _make_role_user(HR_USER, "HR User", ensure_test_company(), status="Active")
		self.assertFalse(_as(hr_user, get_portal_bootstrap)["can_see_all_pending"])
		self.assertRaises(frappe.PermissionError, _as, hr_user, api.get_pending_by_approver)


class TestHrUserOversightDetail(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.manager = make_test_user(MANAGER, self.company, status="Active")
		self.report = make_test_user(REPORT, self.company, reports_to=self.manager)
		self.hr_employee, self.hr_user = _make_role_user(HR_USER, "HR User", self.company, status="Active")

	def test_the_bootstrap_flag_is_on_for_an_hr_user_with_an_active_employee(self):
		self.assertTrue(_as(self.hr_user, get_portal_bootstrap)["can_oversee_timesheets"])

	def test_the_flag_is_off_for_an_hr_user_with_no_employee(self):
		user = "lanes-hr-no-employee@helixhr.test"
		if not frappe.db.exists("User", user):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": user,
					"first_name": "Lanes",
					"send_welcome_email": 0,
					"roles": [{"doctype": "Has Role", "role": "HR User"}],
				}
			).insert(ignore_permissions=True)
		self.assertFalse(api._can_oversee_timesheets(user))

	def test_hr_user_opens_a_pending_timesheet_read_only(self):
		name = make_timesheet(self, self.report, 3)
		detail = _as(self.hr_user, get_approval_detail, "timesheet", name)
		self.assertEqual(detail["actions"], [])
		self.assertTrue(detail["read_only"])

	def test_hr_user_cannot_act_on_it(self):
		name = make_timesheet(self, self.report, 3)
		self.assertRaises(
			frappe.PermissionError,
			_as,
			self.hr_user,
			act_on_approval,
			"Timesheet",
			name,
			"Approve",
			**token("Timesheet", name),
		)

	def test_other_kinds_are_not_found_for_hr_user(self):
		for kind, doctype in (("leave", "Leave Application"), ("request", "HR Request")):
			with self.subTest(kind=kind):
				name = frappe.get_all(doctype, pluck="name", limit=1)
				name = name[0] if name else "missing"
				self.assertRaises(frappe.PermissionError, _as, self.hr_user, get_approval_detail, kind, name)

	def test_an_out_of_company_timesheet_is_not_found(self):
		other_company = ensure_baseline_company()
		outsider = make_test_user("lanes-outsider@helixhr.test", other_company, reports_to=self.manager)
		name = make_timesheet(self, outsider, 3, company=other_company)
		self.assertRaises(frappe.PermissionError, _as, self.hr_user, get_approval_detail, "timesheet", name)


class TestHrManagerDecidesFromAllPending(IntegrationTestCase):
	"""Plan 2026-10-19-001 U9 (R27): an HR Manager decides any pending item in
	their company from All pending, not only the ones handed to HR, and each
	row says whether the caller can decide it."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.hr_employee, self.hr_manager = make_test_hr_manager_employee()
		self.manager = make_test_user(MANAGER, self.company, status="Active")
		self.report = make_test_user(REPORT, self.company, reports_to=self.manager)
		self.hr_employee_user, self.hr_user = _make_role_user(
			HR_USER, "HR User", self.company, status="Active"
		)

	def _items(self, user, kind="timesheet"):
		result = _as(user, api.get_pending_by_approver, kind)  # kind=None: the All pending tab
		return {item["name"]: item for group in result["groups"] for item in group["items"]}

	def test_a_manager_stage_timesheet_is_decidable_by_hr(self):
		name = make_timesheet(self, self.report, 3)
		item = self._items(self.hr_manager)[name]
		self.assertTrue(item["can_decide"])
		self.assertEqual((item["route_kind"], item["doctype"]), ("timesheet", "Timesheet"))

		# The same rule table `act_on_approval` enforces (P4-KTD6), so the
		# panel the row opens offers exactly what the act accepts.
		detail = _as(self.hr_manager, get_approval_detail, "timesheet", name)
		self.assertEqual(detail["actions"], ["Approve", "Send Back"])

	def test_hr_users_own_item_and_hr_user_rows_are_not_decidable(self):
		name = make_timesheet(self, self.report, 3)
		self.assertFalse(self._items(self.hr_user)[name]["can_decide"])
		own = make_timesheet(self, self.hr_employee, 3, week_offset=2)
		self.assertFalse(self._items(self.hr_manager)[own]["can_decide"])
		# The flag is a hint; the server refuses the own item on its own.
		self.assertRaises(frappe.PermissionError, _as, self.hr_manager, get_approval_detail, "timesheet", own)
		self.assertRaises(
			frappe.PermissionError,
			_as,
			self.hr_manager,
			act_on_approval,
			"Timesheet",
			own,
			"Approve",
			**token("Timesheet", own),
		)

	def _raw_leave(self, employee, approver):
		"""A pending leave written straight to the table: what All pending
		lists is under test, not HRMS's own validation."""
		doc = frappe.get_doc(
			{
				"doctype": "Leave Application",
				"employee": employee,
				"employee_name": frappe.db.get_value("Employee", employee, "employee_name"),
				"company": frappe.db.get_value("Employee", employee, "company"),
				"leave_type": frappe.db.get_value("Leave Type", {}, "name"),
				"from_date": "2031-03-03",
				"to_date": "2031-03-03",
				"status": "Open",
				"leave_approver": approver,
			}
		)
		doc.set_new_name()
		doc.db_insert()
		self.addCleanup(frappe.db.delete, "Leave Application", {"name": doc.name})
		return doc.name

	def test_a_manager_stage_leave_row_is_decidable_and_typed(self):
		leave = self._raw_leave(self.report, MANAGER)
		item = self._items(self.hr_manager, kind=None)[leave]
		self.assertEqual((item["doctype"], item["can_decide"]), ("Leave Application", True))

	def test_another_companys_item_is_not_listed(self):
		other = make_test_user("lanes-other-co@helixhr.test", ensure_baseline_company())
		leave = self._raw_leave(other, MANAGER)
		self.assertNotIn(leave, self._items(self.hr_manager, kind=None))
