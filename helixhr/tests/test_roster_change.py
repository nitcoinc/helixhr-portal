"""Plan 2026-10-31-001 U8: a manager's roster request -- draft, submit,
withdraw, reopen -- the team and HR roster payloads, and the notices.

Every report here is a throwaway, login-less Employee (`roster_employee`)
whose assignments are cleared per test, every date is in 2031 under a
pinned clock, and `setUp` deletes every request the two test managers own:
tests own their rows (CI order varies and nothing is rolled back).
"""

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from helixhr.api import (
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
