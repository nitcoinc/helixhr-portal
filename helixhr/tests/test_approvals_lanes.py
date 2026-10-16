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
		patches = [patch.object(api, "_APPROVAL_SUMMARY_COLLECTORS", ((lambda employee, today: list(team), 99),))]
		for doctype, kind in api._APPROVAL_KINDS.items():
			rows = [row for row in hr if row["doctype"] == doctype]
			patches.append(patch.dict(kind, {"hr_queue": lambda employee, today, rows=rows: list(rows)}))
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
