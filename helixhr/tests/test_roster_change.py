"""Plan 2026-10-31-001 U8: a manager's roster request -- draft, submit,
withdraw, reopen -- the team and HR roster payloads, and the notices. U9:
HR's decision through Approvals -- the queue, the re-check and the apply.

Every report here is a throwaway, login-less Employee (`roster_employee`)
whose assignments are cleared per test, every date is in 2031 under a
pinned clock, and `setUp` deletes every request the two test managers own:
tests own their rows (CI order varies and nothing is rolled back).
"""

import json
from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from helixhr import api, reminders
from helixhr.api import (
	act_on_approval,
	get_approval_detail,
	get_my_approvals,
	get_pending_by_approver,
	get_portal_bootstrap,
	get_roster_draft,
	get_roster_week,
	reopen_roster_change,
	save_roster_draft,
	submit_roster_change,
	withdraw_roster_change,
)
from helixhr.tests.test_api_people import _ensure_other_company
from helixhr.tests.test_api_roster import roster_employee, row_for, seed_assignment
from helixhr.tests.utils import (
	HR_MANAGER_EMPLOYEE_USER,
	MANAGER_USER,
	OTHER_MANAGER_USER,
	PORTAL_SHIFT_TYPE,
	TEST_COMPANY,
	ensure_test_shift_type,
	make_assignment,
	make_test_employee_and_manager,
	make_test_hr_manager_employee,
	make_test_user,
	remove_assignment,
)
from helixhr.utils import render_message

DOCTYPE = "HelixHR Roster Change"
LATE_SHIFT = "_Test Roster Late Shift"
NOON = "2031-03-05 12:00:00"  # a Wednesday; the site clock is Asia/Kolkata
WEEK = "2031-03-03"
TOMORROW = "2031-03-06"
HR_IN_SCOPE = "roster-hr-a@helixhr.test"
HR_OUT_OF_SCOPE = "roster-hr-b@helixhr.test"


def assign(employee, from_date, to_date=None, shift_type=PORTAL_SHIFT_TYPE):
	return {
		"employee": employee,
		"action": "Assign",
		"shift_type": shift_type,
		"from_date": from_date,
		"to_date": to_date,
	}


def clear_requests(manager):
	names = frappe.get_all(DOCTYPE, filters={"employee": manager}, pluck="name")
	if names:
		frappe.db.delete("HelixHR Roster Change Row", {"parent": ["in", names]})
		frappe.db.delete("Comment", {"reference_doctype": DOCTYPE, "reference_name": ["in", names]})
		frappe.db.delete("Notification Log", {"document_type": DOCTYPE, "document_name": ["in", names]})
		frappe.db.delete(DOCTYPE, {"name": ["in", names]})


def set_time_zone(test, company, zone):
	before = frappe.db.get_value("Company", company, "helixhr_time_zone")
	test.addCleanup(
		frappe.db.set_value, "Company", company, "helixhr_time_zone", before, update_modified=False
	)
	frappe.db.set_value("Company", company, "helixhr_time_zone", zone, update_modified=False)


class RosterChangeTestCase(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.enterContext(self.freeze_time(NOON, tick=True))
		_, _, self.manager, _ = make_test_employee_and_manager()
		self.other_manager = make_test_user(OTHER_MANAGER_USER, TEST_COMPANY)
		self.other_company = _ensure_other_company()
		ensure_test_shift_type()
		ensure_test_shift_type(LATE_SHIFT, start_time="14:00:00", end_time="22:00:00")
		set_time_zone(self, TEST_COMPANY, "Asia/Kolkata")
		set_time_zone(self, self.other_company, "Asia/Kolkata")
		for manager in (self.manager, self.other_manager):
			clear_requests(manager)

	def tearDown(self):
		frappe.set_user("Administrator")

	def report(self, key, company=TEST_COMPANY, **fields):
		"""A throwaway Active direct report of the manager, no shifts."""
		name = roster_employee(
			f"rc-{key}", company=company, reports_to=self.manager, relieving_date=None, **fields
		)
		frappe.db.delete("Shift Assignment", {"employee": name})
		return name

	def as_manager(self):
		frappe.set_user(MANAGER_USER)

	def pending(self, *reports, note="Holiday cover"):
		"""Submit one Assign per report as the manager; the request names."""
		self.as_manager()
		save_roster_draft([assign(report, TOMORROW) for report in reports])
		names = submit_roster_change(note)["names"]
		frappe.set_user("Administrator")
		return names

	def refused(self, ops, exception=frappe.ValidationError):
		with self.assertRaises(exception) as caught:
			save_roster_draft(ops)
		return str(caught.exception)


class TestDraft(RosterChangeTestCase):
	def test_a_draft_for_two_reports_persists_with_its_preview(self):
		first, second = self.report("draft-1"), self.report("draft-2")
		seed_assignment(second, "2031-01-01")
		self.as_manager()
		saved = save_roster_draft(
			json.dumps(
				[
					assign(first, TOMORROW, "2031-03-20"),
					{
						"employee": second,
						"action": "Change",
						"shift_type": LATE_SHIFT,
						"from_date": "2031-03-10",
						# Not a Change field: dropped, never stored.
						"to_date": "garbage",
					},
				]
			),
			note="Holiday cover",
		)["draft"]
		self.assertEqual(saved["status"], "Draft")
		self.assertEqual([row["employee"] for row in saved["rows"]], [first, second])
		self.assertIsNone(saved["rows"][1]["to_date"])
		self.assertEqual(saved["problems"], [])
		self.assertEqual(
			saved["preview"][first],
			[
				{
					"key": "new-0",
					"shift_type": PORTAL_SHIFT_TYPE,
					"start_date": TOMORROW,
					"end_date": "2031-03-20",
				}
			],
		)
		self.assertEqual(
			[(item["shift_type"], item["start_date"], item["end_date"]) for item in saved["preview"][second]],
			[(PORTAL_SHIFT_TYPE, "2031-01-01", "2031-03-09"), (LATE_SHIFT, "2031-03-10", None)],
		)

		# A fresh session (another login in between) reads the same draft.
		frappe.set_user("Administrator")
		self.as_manager()
		read = get_roster_draft()
		self.assertEqual(read["draft"]["name"], saved["name"])
		self.assertEqual(read["draft"]["note"], "Holiday cover")
		self.assertEqual(len(read["draft"]["rows"]), 2)
		self.assertEqual(read["draft"]["preview"], saved["preview"])

		# A second save replaces the rows of the same one draft.
		again = save_roster_draft([assign(first, "2031-03-07")])["draft"]
		self.assertEqual(again["name"], saved["name"])
		self.assertEqual(len(again["rows"]), 1)
		self.assertEqual(frappe.db.count(DOCTYPE, {"employee": self.manager, "status": "Draft"}), 1)

	def test_a_non_report_the_managers_own_row_and_an_inactive_report_are_refused(self):
		stranger = roster_employee("rc-stranger", reports_to=None)
		inactive = self.report("inactive")
		frappe.db.set_value("Employee", inactive, "status", "Inactive")
		inactive_name = frappe.db.get_value("Employee", inactive, "employee_name")
		self.as_manager()

		message = self.refused([assign(stranger, TOMORROW)])
		self.assertIn(stranger, message)
		self.assertIn("doesn't report to you", message)
		self.assertIn("own shifts", self.refused([assign(self.manager, TOMORROW)]))
		message = self.refused([assign(inactive, TOMORROW)])
		self.assertIn(inactive_name, message)
		self.assertIn("no longer active", message)
		self.assertFalse(frappe.db.exists(DOCTYPE, {"employee": self.manager}))

	def test_the_simulator_refuses_an_overlap_naming_the_person(self):
		report = self.report("overlap")
		seed_assignment(report, "2031-01-01")
		self.as_manager()
		message = self.refused([assign(report, TOMORROW)])
		self.assertIn(frappe.db.get_value("Employee", report, "employee_name"), message)
		self.assertIn("already has a shift", message)

	def test_more_than_two_hundred_rows_or_a_malformed_payload_is_refused(self):
		report = self.report("cap")
		self.as_manager()
		self.assertIn("at most 200", self.refused([assign(report, TOMORROW)] * 201))
		self.assertIn("Invalid", self.refused("not json"))
		self.assertIn("Invalid", self.refused(["a string row"]))

	def test_today_is_refused_and_tomorrow_passes_on_each_companys_clock(self):
		# 00:30 on 6 March in India is still 14:00 on 5 March in New York.
		self.enterContext(self.freeze_time("2031-03-06 00:30:00", tick=True))
		set_time_zone(self, self.other_company, "America/New_York")
		india = self.report("tz-in")
		new_york = self.report("tz-ny", company=self.other_company)
		self.as_manager()

		self.assertIn("tomorrow at the earliest", self.refused([assign(india, "2031-03-06")]))
		self.assertIn("tomorrow at the earliest", self.refused([assign(new_york, "2031-03-05")]))
		draft = save_roster_draft([assign(india, "2031-03-07"), assign(new_york, "2031-03-06")])["draft"]
		self.assertEqual(len(draft["rows"]), 2)


class TestSubmit(RosterChangeTestCase):
	def test_a_two_company_batch_splits_into_two_pending_requests_with_snapshots(self):
		home = self.report("split-a")
		away = self.report("split-b", company=self.other_company)
		seed_assignment(away, "2031-01-01", "2031-03-31")
		self.as_manager()
		draft = save_roster_draft(
			[
				assign(home, TOMORROW, "2031-03-12"),
				{"employee": away, "action": "End", "to_date": "2031-03-15"},
			]
		)["draft"]
		names = submit_roster_change("Spring cover")["names"]

		self.assertEqual(len(names), 2)
		self.assertEqual(names[0], draft["name"])
		first, second = (frappe.get_doc(DOCTYPE, name) for name in names)
		self.assertEqual((first.company, first.status), (TEST_COMPANY, "Pending"))
		self.assertEqual((second.company, second.status), (self.other_company, "Pending"))
		self.assertEqual([row.employee for row in first.rows], [home])
		self.assertEqual([row.employee for row in second.rows], [away])
		self.assertEqual(second.note, "Spring cover")
		self.assertEqual(second.employee, self.manager)
		self.assertEqual(second.manager_user, MANAGER_USER)
		self.assertTrue(first.helixhr_pending_since and second.helixhr_pending_since)
		self.assertEqual(
			json.loads(first.snapshot),
			{home: {"window": [TOMORROW, "2031-03-12"], "coverage": []}},
		)
		self.assertEqual(
			json.loads(second.snapshot),
			{
				away: {
					"window": ["2031-03-15", "2031-03-15"],
					"coverage": [[PORTAL_SHIFT_TYPE, "2031-01-01", "2031-03-31"]],
				}
			},
		)
		self.assertIsNone(get_roster_draft()["draft"])
		self.assertEqual({row["name"] for row in get_roster_draft()["recent"]}, set(names))

	def test_an_employee_already_in_a_pending_request_is_refused_by_name(self):
		taken, free = self.report("taken"), self.report("free")
		self.pending(taken)
		self.as_manager()
		save_roster_draft([assign(free, TOMORROW), assign(taken, "2031-03-20")])
		with self.assertRaises(frappe.ValidationError) as caught:
			submit_roster_change()
		self.assertIn(frappe.db.get_value("Employee", taken, "employee_name"), str(caught.exception))
		self.assertNotIn(frappe.db.get_value("Employee", free, "employee_name"), str(caught.exception))
		self.assertEqual(get_roster_draft()["draft"]["status"], "Draft")

	def test_an_empty_draft_cannot_be_sent(self):
		self.as_manager()
		with self.assertRaises(frappe.ValidationError):
			submit_roster_change()

	def test_a_draft_that_went_stale_is_refused_at_submit(self):
		report = self.report("stale")
		self.as_manager()
		save_roster_draft([assign(report, TOMORROW)])
		seed_assignment(report, "2031-03-01")  # HR assigned in the meantime
		with self.assertRaises(frappe.ValidationError) as caught:
			submit_roster_change()
		self.assertIn("already has a shift", str(caught.exception))


class TestWithdrawAndReopen(RosterChangeTestCase):
	def test_withdraw_then_reopen_and_only_the_manager_may_do_either(self):
		(name,) = self.pending(self.report("withdraw"))
		frappe.set_user(OTHER_MANAGER_USER)
		with self.assertRaises(frappe.PermissionError):
			withdraw_roster_change(name)
		with self.assertRaises(frappe.PermissionError):
			reopen_roster_change(name)

		self.as_manager()
		with self.assertRaises(frappe.ValidationError):
			reopen_roster_change(name)  # still Pending
		self.assertEqual(withdraw_roster_change(name)["status"], "Withdrawn")
		with self.assertRaises(frappe.ValidationError):
			withdraw_roster_change(name)
		reopened = reopen_roster_change(name)
		self.assertFalse(reopened["merged"])
		self.assertEqual((reopened["draft"]["name"], reopened["draft"]["status"]), (name, "Draft"))
		self.assertEqual(get_roster_draft()["draft"]["name"], name)

	def test_reopen_while_a_draft_exists_merges_into_it_and_keeps_the_history(self):
		old, new = self.report("merge-old"), self.report("merge-new")
		(name,) = self.pending(old)
		self.as_manager()
		withdraw_roster_change(name)
		draft = save_roster_draft([assign(new, TOMORROW)])["draft"]

		merged = reopen_roster_change(name)
		self.assertTrue(merged["merged"])
		self.assertEqual(merged["draft"]["name"], draft["name"])
		self.assertEqual([row["employee"] for row in merged["draft"]["rows"]], [new, old])
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, "status"), "Withdrawn")
		self.assertEqual(frappe.db.count(DOCTYPE, {"employee": self.manager, "status": "Draft"}), 1)
		with self.assertRaises(frappe.ValidationError):
			reopen_roster_change(name)

	def test_reopen_is_refused_when_the_same_person_is_in_the_draft(self):
		report = self.report("merge-same")
		(name,) = self.pending(report)
		self.as_manager()
		withdraw_roster_change(name)
		draft = save_roster_draft([assign(report, "2031-03-20")])["draft"]
		with self.assertRaises(frappe.ValidationError) as caught:
			reopen_roster_change(name)
		self.assertIn(frappe.db.get_value("Employee", report, "employee_name"), str(caught.exception))
		self.assertEqual(len(get_roster_draft()["draft"]["rows"]), 1)
		self.assertEqual(get_roster_draft()["draft"]["name"], draft["name"])


class TestAutoWithdraw(RosterChangeTestCase):
	def test_a_report_who_leaves_withdraws_the_request_and_the_manager_hears(self):
		report = self.report("leaves")
		(name,) = self.pending(report)
		employee = frappe.get_doc("Employee", report)
		employee.status = "Left"
		employee.relieving_date = "2031-03-05"
		with patch("helixhr.events.send_notification") as send:
			employee.save(ignore_permissions=True)

		doc = frappe.get_doc(DOCTYPE, name)
		self.assertEqual(doc.status, "Withdrawn")
		self.assertIn("has left", doc.decision_note)
		calls = [call.args for call in send.call_args_list if call.args[0] == "roster_change_decided"]
		self.assertEqual(len(calls), 1)
		self.assertEqual(calls[0][1], [MANAGER_USER])
		self.assertEqual(calls[0][2]["state"], "withdrawn")
		self.assertEqual(calls[0][2]["approver_name"], "")
		self.assertTrue(
			frappe.db.exists(
				"Notification Log",
				{"for_user": MANAGER_USER, "document_type": DOCTYPE, "document_name": name},
			)
		)

	def test_a_reports_to_change_withdraws_it_and_the_new_manager_can_then_submit(self):
		report = self.report("moves")
		(name,) = self.pending(report)
		employee = frappe.get_doc("Employee", report)
		employee.reports_to = self.other_manager
		employee.save(ignore_permissions=True)

		self.assertEqual(frappe.db.get_value(DOCTYPE, name, "status"), "Withdrawn")
		self.assertIn("no longer reports to you", frappe.db.get_value(DOCTYPE, name, "decision_note"))
		frappe.set_user(OTHER_MANAGER_USER)
		try:
			save_roster_draft([assign(report, TOMORROW)])
			(mine,) = submit_roster_change()["names"]
			self.assertEqual(frappe.db.get_value(DOCTYPE, mine, "status"), "Pending")
		finally:
			frappe.set_user("Administrator")
			clear_requests(self.other_manager)

	def test_an_unrelated_save_leaves_the_request_pending(self):
		report = self.report("stays")
		(name,) = self.pending(report)
		employee = frappe.get_doc("Employee", report)
		employee.cell_number = "5550100"
		employee.save(ignore_permissions=True)
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, "status"), "Pending")


class TestNotifications(RosterChangeTestCase):
	def setUp(self):
		super().setUp()
		for user, company in ((HR_IN_SCOPE, TEST_COMPANY), (HR_OUT_OF_SCOPE, self.other_company)):
			make_test_user(user, company, create_user_permission=0)
			remove_assignment(user, "HR Manager")
			make_assignment(user, "HR Manager", [company])
		frappe.db.delete("Notification Log", {"for_user": ["in", [HR_IN_SCOPE, HR_OUT_OF_SCOPE]]})

	def test_hr_of_the_requests_company_gets_a_bell_and_mail_and_other_hr_neither(self):
		report = self.report("notify")
		with patch("helixhr.events.send_notification") as send:
			(name,) = self.pending(report, note="<b>Holiday</b> cover")

		calls = [call.args for call in send.call_args_list if call.args[0] == "roster_change_submitted"]
		self.assertEqual(len(calls), 1)
		recipients, context = calls[0][1], calls[0][2]
		self.assertIn(HR_IN_SCOPE, recipients)
		self.assertNotIn(HR_OUT_OF_SCOPE, recipients)
		self.assertNotIn(MANAGER_USER, recipients)
		self.assertEqual(context["change_count"], 1)
		self.assertIn(frappe.db.get_value("Employee", report, "employee_name"), context["people"])
		self.assertTrue(context["action_url"].endswith(f"/helixhr/approvals/roster/{name}"))
		logs = {"document_type": DOCTYPE, "document_name": name}
		self.assertTrue(frappe.db.exists("Notification Log", {"for_user": HR_IN_SCOPE, **logs}))
		self.assertFalse(frappe.db.exists("Notification Log", {"for_user": HR_OUT_OF_SCOPE, **logs}))

		# The manager's note is free text: the body escapes it.
		html = render_message("roster_change_submitted", context)["html"]
		self.assertIn("&lt;b&gt;Holiday&lt;/b&gt; cover", html)
		self.assertNotIn("<b>Holiday</b>", html)

	def test_the_other_company_half_of_a_split_mails_its_own_hr(self):
		self.report("notify-home")
		away = self.report("notify-away", company=self.other_company)
		with patch("helixhr.events.send_notification") as send:
			self.pending(away)
		(call,) = [call.args for call in send.call_args_list if call.args[0] == "roster_change_submitted"]
		self.assertIn(HR_OUT_OF_SCOPE, call[1])
		self.assertNotIn(HR_IN_SCOPE, call[1])

	def test_a_managers_own_withdraw_mails_nobody(self):
		(name,) = self.pending(self.report("quiet"))
		self.as_manager()
		with patch("helixhr.events.send_notification") as send:
			withdraw_roster_change(name)
		send.assert_not_called()


class TestRosterWeekPayload(RosterChangeTestCase):
	def test_team_mode_offers_requests_and_names_assignments_for_reports_only(self):
		report, waiting = self.report("team-a"), self.report("team-b")
		seed_assignment(report, "2031-01-01")
		(name,) = self.pending(waiting)
		self.as_manager()
		save_roster_draft([{"employee": report, "action": "End", "to_date": "2031-04-01"}])

		payload = get_roster_week(WEEK, mode="team", search="Roster rc-team")
		self.assertTrue(payload["can_request"])
		self.assertFalse(payload["can_edit"])
		self.assertTrue(payload["shift_types"])
		self.assertEqual([row["employee"] for row in payload["draft"]["rows"]], [report])
		cell = row_for(payload, report)["cells"][2]
		self.assertEqual(cell["assignment_start"], "2031-01-01")
		self.assertTrue(cell["assignment"])
		self.assertIsNone(row_for(payload, report)["pending_request"])
		self.assertEqual(row_for(payload, waiting)["pending_request"], name)

		own = row_for(get_roster_week(WEEK, mode="team", search=self.manager), self.manager)
		self.assertNotIn("assignment", own["cells"][2])
		self.assertNotIn("assignment_start", own["cells"][2])

		# Plain "mine" mode is unchanged: no request surface at all.
		mine = get_roster_week(WEEK)
		self.assertFalse(mine["can_request"])
		self.assertIsNone(mine["draft"])

	def test_hr_mode_marks_an_employee_with_a_pending_request(self):
		waiting, idle = self.report("hr-a"), self.report("hr-b")
		(name,) = self.pending(waiting)
		make_test_hr_manager_employee()
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		payload = get_roster_week(WEEK, mode="hr", search="Roster rc-hr")
		self.assertFalse(payload["can_request"])
		self.assertEqual(row_for(payload, waiting)["pending_request"], name)
		self.assertIsNone(row_for(payload, idle)["pending_request"])


def end_op(employee, to_date, assignment=None):
	return {"employee": employee, "action": "End", "to_date": to_date, "assignment": assignment}


def change_op(employee, from_date, shift_type=LATE_SHIFT):
	return {"employee": employee, "action": "Change", "shift_type": shift_type, "from_date": from_date}


def coverage(employee):
	"""Submitted assignments as `(shift_type, start, end)`, oldest first."""
	return [
		(row.shift_type, str(row.start_date), str(row.end_date) if row.end_date else None)
		for row in frappe.get_all(
			"Shift Assignment",
			filters={"employee": employee, "docstatus": 1},
			fields=["shift_type", "start_date", "end_date"],
			order_by="start_date asc",
		)
	]


def decision_calls(send):
	return [call.args for call in send.call_args_list if call.args[0] == "roster_change_decided"]


class RosterApprovalTestCase(RosterChangeTestCase):
	"""U9. Two HR Managers, one per company, with no self-scoping User
	Permission (an HR Manager's Shift Assignment writes would trip it)."""

	def setUp(self):
		super().setUp()
		for user, company in ((HR_IN_SCOPE, TEST_COMPANY), (HR_OUT_OF_SCOPE, self.other_company)):
			make_test_user(user, company, create_user_permission=0)
			frappe.db.delete("User Permission", {"user": user, "allow": "Employee"})
			remove_assignment(user, "HR Manager")
			make_assignment(user, "HR Manager", [company])

	def submit(self, ops, note="Holiday cover"):
		self.as_manager()
		try:
			save_roster_draft(ops)
			return submit_roster_change(note)["names"]
		finally:
			frappe.set_user("Administrator")

	def detail(self, user, name):
		frappe.set_user(user)
		try:
			return get_approval_detail("roster", name)
		finally:
			frappe.set_user("Administrator")

	def decide(self, user, name, action="Approve", comment=None, detail=None):
		"""Act the way the screen does: with the token the detail handed out."""
		detail = detail or self.detail(user, name)
		frappe.set_user(user)
		try:
			return act_on_approval(
				DOCTYPE,
				name,
				action,
				comment=comment,
				expected_modified=detail["modified"],
				expected_state=detail["status"],
			)
		finally:
			frappe.set_user("Administrator")

	def queue(self, user):
		frappe.set_user(user)
		try:
			return get_my_approvals()
		finally:
			frappe.set_user("Administrator")

	def employee_name(self, employee):
		return frappe.db.get_value("Employee", employee, "employee_name")


class TestApproveApplies(RosterApprovalTestCase):
	def test_a_clean_approve_applies_three_operations_across_two_people_as_simulated(self):
		"""R18: in order, chained (the End acts on the Assign before it), as
		the HR Manager's own writes."""
		first, second = self.report("apply-1"), self.report("apply-2")
		seed_assignment(second, "2031-01-01")
		ops = [assign(first, TOMORROW, "2031-03-20"), change_op(second, "2031-03-10"), end_op(first, "2031-03-15")]
		self.as_manager()
		preview = save_roster_draft(ops)["draft"]["preview"]
		frappe.set_user("Administrator")
		(name,) = self.submit(ops)

		detail = self.detail(HR_IN_SCOPE, name)
		self.assertEqual(detail["kind"], "roster")
		self.assertEqual(detail["actions"], ["Approve", "Send Back", "Decline"])
		self.assertEqual(detail["manager_name"], frappe.db.get_value("Employee", self.manager, "employee_name"))
		self.assertEqual((detail["company"], detail["note"], detail["change_count"]), (TEST_COMPANY, "Holiday cover", 3))
		self.assertTrue(detail["submitted_on"])
		self.assertEqual(detail["conflicts"], [])
		by_employee = {row["employee"]: row for row in detail["employees"]}
		self.assertEqual([op["index"] for op in by_employee[first]["operations"]], [0, 2])
		self.assertEqual(by_employee[first]["before"], [])
		self.assertEqual(
			by_employee[first]["after"],
			[{"shift_type": PORTAL_SHIFT_TYPE, "start_date": TOMORROW, "end_date": "2031-03-15", "new": True}],
		)
		self.assertEqual(
			[(row["shift_type"], row["start_date"], row["end_date"]) for row in by_employee[second]["before"]],
			[(PORTAL_SHIFT_TYPE, "2031-01-01", None)],
		)

		with patch("helixhr.events.send_notification") as send:
			result = self.decide(HR_IN_SCOPE, name, detail=detail)
		self.assertEqual((result["state"], result["returned"], result["reasons"]), ("Approved", False, []))
		for employee in (first, second):
			self.assertEqual(
				coverage(employee),
				[(row["shift_type"], row["start_date"], row["end_date"]) for row in preview[employee]],
			)
		doc = frappe.get_doc(DOCTYPE, name)
		self.assertEqual((doc.status, doc.decided_by), ("Approved", HR_IN_SCOPE))
		self.assertTrue(doc.decided_on)
		# KTD10: HR's own session wrote the new assignments, not Administrator.
		self.assertEqual(
			set(frappe.get_all("Shift Assignment", filters={"employee": first, "docstatus": 1}, pluck="owner")),
			{HR_IN_SCOPE},
		)
		(call,) = decision_calls(send)
		self.assertEqual((call[1], call[2]["state"]), ([MANAGER_USER], "approved"))

	def test_the_nightly_inactive_flip_after_submit_is_not_a_conflict(self):
		"""KTD8: HRMS's job rewrites `status`; the snapshot never reads it."""
		report = self.report("nightly")
		seeded = seed_assignment(report, "2031-01-01", "2031-03-31")
		(name,) = self.submit([end_op(report, "2031-03-15")])
		frappe.db.set_value("Shift Assignment", seeded.name, "status", "Inactive")

		self.assertEqual(self.detail(HR_IN_SCOPE, name)["conflicts"], [])
		self.assertEqual(self.decide(HR_IN_SCOPE, name)["state"], "Approved")
		self.assertEqual(coverage(report), [(PORTAL_SHIFT_TYPE, "2031-01-01", "2031-03-15")])


class TestApproveReturns(RosterApprovalTestCase):
	def test_hr_changing_a_shift_after_submit_returns_it_naming_that_person_and_writes_nothing(self):
		"""R16 / KTD11."""
		kept, changed = self.report("conflict-1"), self.report("conflict-2")
		(name,) = self.submit([assign(kept, TOMORROW), assign(changed, TOMORROW)])
		seed_assignment(changed, "2031-03-10", "2031-03-12")  # HR, straight in the roster

		conflicts = self.detail(HR_IN_SCOPE, name)["conflicts"]
		self.assertEqual([item["employee"] for item in conflicts], [changed])
		with patch("helixhr.events.send_notification") as send:
			result = self.decide(HR_IN_SCOPE, name)

		self.assertTrue(result["returned"])
		self.assertEqual((result["action"], result["state"]), ("Approve", "Returned"))
		self.assertEqual([item["employee"] for item in result["reasons"]], [changed])
		self.assertIn(self.employee_name(changed), result["reasons"][0]["message"])
		self.assertEqual(coverage(kept), [])
		self.assertEqual(coverage(changed), [(PORTAL_SHIFT_TYPE, "2031-03-10", "2031-03-12")])
		doc = frappe.get_doc(DOCTYPE, name)
		self.assertEqual((doc.status, doc.decided_by), ("Returned", HR_IN_SCOPE))
		self.assertIn(self.employee_name(changed), doc.return_reasons)
		self.assertNotIn(self.employee_name(kept), doc.return_reasons)
		(call,) = decision_calls(send)
		self.assertEqual((call[1], call[2]["state"]), ([MANAGER_USER], "sent back"))
		self.assertIn(self.employee_name(changed), call[2]["decision_note"])

	def test_an_operation_date_that_has_passed_returns_it(self):
		report = self.report("passed")
		(name,) = self.submit([assign(report, TOMORROW)])
		self.enterContext(self.freeze_time("2031-03-07 12:00:00", tick=True))

		result = self.decide(HR_IN_SCOPE, name)
		self.assertTrue(result["returned"])
		self.assertIn("date has passed", result["reasons"][0]["message"])
		self.assertEqual(coverage(report), [])

	def test_a_report_who_moved_or_went_inactive_returns_it_with_a_reason_each(self):
		"""The reporting-line hook withdraws on a `reports_to` save (U8); a
		direct write skips it, so the approval's own re-check is what holds."""
		moved, inactive = self.report("moved"), self.report("inactive-later")
		(name,) = self.submit([assign(moved, TOMORROW), assign(inactive, TOMORROW)])
		frappe.db.set_value("Employee", moved, "reports_to", self.other_manager)
		frappe.db.set_value("Employee", inactive, "status", "Inactive")

		result = self.decide(HR_IN_SCOPE, name)
		self.assertTrue(result["returned"])
		messages = {item["employee"]: item["message"] for item in result["reasons"]}
		self.assertIn("no longer reports to", messages[moved])
		self.assertIn("no longer active", messages[inactive])
		self.assertEqual(coverage(moved) + coverage(inactive), [])

	def test_a_refusal_on_the_last_operation_rolls_back_the_first_two(self):
		"""KTD11. Another writer's assignment lands after the re-check, just
		before the third operation (modelled by inserting it there): HRMS
		refuses the overlap, ops 1 and 2 are undone and no message leaks."""
		first, second, third = self.report("mid-1"), self.report("mid-2"), self.report("mid-3")
		seed_assignment(second, "2031-01-01", "2031-03-31")
		(name,) = self.submit(
			[assign(first, TOMORROW, "2031-03-20"), end_op(second, "2031-03-15"), assign(third, TOMORROW, "2031-03-20")]
		)
		real = api._new_roster_assignment

		def racing(employee, *args):
			if employee == third:
				seed_assignment(third, "2031-03-10", "2031-03-12")
			return real(employee, *args)

		detail = self.detail(HR_IN_SCOPE, name)
		frappe.local.message_log = []
		with patch.object(api, "_new_roster_assignment", side_effect=racing):
			result = self.decide(HR_IN_SCOPE, name, detail=detail)

		self.assertTrue(result["returned"])
		self.assertEqual(result["state"], "Returned")
		(reason,) = result["reasons"]
		self.assertEqual(reason["employee"], third)
		self.assertIn(self.employee_name(third), reason["message"])
		self.assertIn("already has a shift", reason["message"])
		self.assertNotIn("<", reason["message"])
		self.assertEqual(coverage(first), [])
		self.assertEqual(coverage(second), [(PORTAL_SHIFT_TYPE, "2031-01-01", "2031-03-31")])
		self.assertEqual(coverage(third), [])
		self.assertEqual(frappe.local.message_log, [])
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, "status"), "Returned")


class TestWhoDecides(RosterApprovalTestCase):
	def test_two_hr_managers_with_the_same_token_only_the_first_decides(self):
		report = self.report("twice")
		(name,) = self.submit([assign(report, TOMORROW)])
		_hr_employee, second_hr = make_test_hr_manager_employee()
		first_detail = self.detail(HR_IN_SCOPE, name)
		second_detail = self.detail(second_hr, name)
		self.assertEqual(first_detail["modified"], second_detail["modified"])

		self.decide(HR_IN_SCOPE, name, detail=first_detail)
		with self.assertRaises(frappe.ValidationError):
			self.decide(second_hr, name, detail=second_detail)
		self.assertEqual(len(coverage(report)), 1)
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, "decided_by"), HR_IN_SCOPE)

	def test_a_manager_who_is_also_hr_cannot_decide_their_own_request(self):
		"""R21."""
		make_assignment(MANAGER_USER, "HR Manager", [TEST_COMPANY])
		self.addCleanup(frappe.clear_cache, user=MANAGER_USER)
		self.addCleanup(lambda: frappe.get_doc("User", MANAGER_USER).remove_roles("HR Manager"))
		self.addCleanup(remove_assignment, MANAGER_USER, "HR Manager")
		report = self.report("self")
		(name,) = self.submit([assign(report, TOMORROW)])

		self.assertNotIn(name, {row["name"] for row in self.queue(MANAGER_USER)["pending"]})
		with self.assertRaises(frappe.PermissionError):
			self.detail(MANAGER_USER, name)
		with self.assertRaises(frappe.PermissionError):
			self.decide(MANAGER_USER, name, detail=self.detail(HR_IN_SCOPE, name))
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, "status"), "Pending")

	def test_hr_of_another_company_neither_sees_nor_decides_it(self):
		report = self.report("out-of-scope")
		(name,) = self.submit([assign(report, TOMORROW)])

		self.assertNotIn(name, {row["name"] for row in self.queue(HR_OUT_OF_SCOPE)["pending"]})
		with self.assertRaises(frappe.PermissionError):
			self.detail(HR_OUT_OF_SCOPE, name)
		with self.assertRaises(frappe.PermissionError):
			self.decide(HR_OUT_OF_SCOPE, name, detail=self.detail(HR_IN_SCOPE, name))
		self.assertEqual(coverage(report), [])

	def test_a_request_for_another_company_is_that_companys_hr_not_the_managers(self):
		"""KTD12: scope follows the request's company, not the manager's."""
		away = self.report("xco", company=self.other_company)
		(name,) = self.submit([assign(away, TOMORROW)])
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, "company"), self.other_company)

		self.assertNotIn(name, {row["name"] for row in self.queue(HR_IN_SCOPE)["pending"]})
		with self.assertRaises(frappe.PermissionError):
			self.detail(HR_IN_SCOPE, name)

		rows = {row["name"]: row for row in self.queue(HR_OUT_OF_SCOPE)["pending"]}
		self.assertEqual((rows[name]["kind"], rows[name]["lane"]), ("roster", "hr"))
		self.assertEqual(self.decide(HR_OUT_OF_SCOPE, name)["state"], "Approved")
		self.assertEqual(
			frappe.get_all("Shift Assignment", filters={"employee": away, "docstatus": 1}, pluck="company"),
			[self.other_company],
		)


class TestSendBackAndDecline(RosterApprovalTestCase):
	def test_send_back_needs_a_reason_and_returns_it_with_hrs_words(self):
		report = self.report("send-back")
		(name,) = self.submit([assign(report, TOMORROW)])
		with self.assertRaises(frappe.ValidationError):
			self.decide(HR_IN_SCOPE, name, "Send Back")

		with patch("helixhr.events.send_notification") as send:
			result = self.decide(HR_IN_SCOPE, name, "Send Back", comment="Split the late shifts")
		self.assertEqual(result["state"], "Returned")
		self.assertNotIn("returned", result)
		doc = frappe.get_doc(DOCTYPE, name)
		self.assertEqual((doc.return_reasons, doc.decision_note), ("Split the late shifts", "Split the late shifts"))
		self.assertEqual(coverage(report), [])
		(call,) = decision_calls(send)
		self.assertEqual(call[2]["decision_note"], "Split the late shifts")

	def test_decline_is_final_and_lands_in_decided_by_me(self):
		report = self.report("decline")
		(name,) = self.submit([assign(report, TOMORROW)])
		with self.assertRaises(frappe.ValidationError):
			self.decide(HR_IN_SCOPE, name, "Decline")
		self.assertEqual(self.decide(HR_IN_SCOPE, name, "Decline", comment="Budget freeze")["state"], "Declined")
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, "decision_note"), "Budget freeze")
		with self.assertRaises(frappe.ValidationError):
			self.decide(HR_IN_SCOPE, name, "Approve")

		decided = {row["name"]: row for row in self.queue(HR_IN_SCOPE)["decided"]}
		self.assertEqual((decided[name]["kind"], decided[name]["status"]), ("roster", "Declined"))
		self.assertNotIn(name, {row["name"] for row in self.queue(HR_OUT_OF_SCOPE)["decided"]})
		self.assertEqual(coverage(report), [])


class TestQueueAndOversight(RosterApprovalTestCase):
	def test_the_badge_counts_it_and_equals_the_page(self):
		report = self.report("badge")
		(name,) = self.submit([assign(report, TOMORROW), end_op(report, "2031-03-20")])
		frappe.set_user(HR_IN_SCOPE)
		try:
			page = get_my_approvals()
			boot = get_portal_bootstrap()
		finally:
			frappe.set_user("Administrator")

		row = next(row for row in page["pending"] if row["name"] == name)
		self.assertEqual((row["kind"], row["lane"], row["for_hr"]), ("roster", "hr", True))
		self.assertEqual((row["change_count"], row["people_count"]), (2, 1))
		self.assertEqual((row["from_date"], row["to_date"]), (TOMORROW, "2031-03-20"))
		self.assertEqual(row["company"], TEST_COMPANY)
		counts = {kind["name"]: kind["count"] for kind in page["counts"]["kinds"]}
		self.assertGreaterEqual(counts["roster"], 1)
		self.assertEqual(boot["approvals_count"], page["total"])

	def test_pending_since_the_overdue_digest_and_all_pending_include_it(self):
		report = self.report("overdue")
		(name,) = self.submit([assign(report, TOMORROW)])
		since = frappe.db.get_value(DOCTYPE, name, "helixhr_pending_since")
		self.assertTrue(since)
		frappe.db.set_value(DOCTYPE, name, "helixhr_pending_since", since - timedelta(days=30), update_modified=False)

		items = reminders.collect_overdue(frappe.utils.getdate(NOON), companies=[TEST_COMPANY])
		item = next(item for item in items if item["name"] == name)
		self.assertEqual((item["kind"], item["route_kind"], item["company"]), ("Roster change", "roster", TEST_COMPANY))
		self.assertTrue(item["overdue"])
		self.assertIn(HR_IN_SCOPE, item["owners"])
		self.assertNotIn(MANAGER_USER, item["owners"])
		self.assertTrue(item["url"].endswith(f"/helixhr/approvals/roster/{name}"))
		self.assertNotIn(name, {item["name"] for item in reminders.collect_overdue(frappe.utils.getdate(NOON), companies=[self.other_company])})
		summary = [entry for owner in reminders._summary_owners(items, HR_IN_SCOPE) for entry in owner["items"]]
		self.assertIn(self.employee_name(self.manager), {entry["employee_name"] for entry in summary})
		self.assertEqual(reminders._summary_owners([item], HR_OUT_OF_SCOPE), [])

		frappe.set_user(HR_IN_SCOPE)
		try:
			groups = get_pending_by_approver(kind="roster")["groups"]
		finally:
			frappe.set_user("Administrator")
		(group,) = [group for group in groups if any(entry["name"] == name for entry in group["items"])]
		self.assertEqual((group["key"], group["approver_role"]), ("role:HR Manager", "HR Manager"))
		entry = next(entry for entry in group["items"] if entry["name"] == name)
		self.assertEqual((entry["doctype"], entry["can_decide"], entry["is_own"]), (DOCTYPE, True, False))
