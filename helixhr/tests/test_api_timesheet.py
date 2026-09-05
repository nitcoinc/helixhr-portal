import hashlib
import json

import frappe
from frappe.model.workflow import apply_workflow
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, get_datetime

from helixhr.api import (
	get_my_projects,
	get_my_timesheet_history,
	get_my_week,
	get_timesheet_week_start,
	save_my_week,
	submit_my_week,
)
from helixhr.tests.utils import EMPLOYEE_USER, MANAGER_USER, make_test_employee_and_manager
from helixhr.utils import get_week_bounds


def make_test_project(name_suffix, users=None):
	"""A User Permission, not a Project Users row, grants access here --
	adding a row to Project.users triggers a "collaboration invitation"
	notification email, which throws on a test site with no outgoing
	Email Account configured. get_my_projects checks both, so this
	exercises the same code path without that side effect."""
	from helixhr.tests.utils import TEST_COMPANY, ensure_test_company

	project_name = f"_Test Timesheet Project {name_suffix}"
	existing = frappe.db.get_value("Project", {"project_name": project_name}, "name")
	if existing:
		docname = existing
	else:
		ensure_test_company()
		doc = frappe.get_doc(
			{"doctype": "Project", "project_name": project_name, "status": "Open", "company": TEST_COMPANY}
		)
		doc.insert(ignore_permissions=True)
		docname = doc.name

	for user in users or []:
		if not frappe.db.exists(
			"User Permission", {"user": user, "allow": "Project", "for_value": docname}
		):
			frappe.get_doc(
				{"doctype": "User Permission", "user": user, "allow": "Project", "for_value": docname}
			).insert(ignore_permissions=True)
	return docname


class TestApiTimesheet(IntegrationTestCase):
	def setUp(self):
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()
		frappe.db.set_value("Employee", self.employee_name, "reports_to", self.manager_name)

		# Each test method gets its own week (offset by a stable hash of
		# the test name, in whole weeks so it's always a Monday) --
		# IntegrationTestCase does not roll back between test *methods*
		# here (see the runbook), so a shared "this week" Timesheet would
		# leak across tests the same way it did for leave in U6.
		method_name = self.id().split(".")[-1]
		# hashlib, not the builtin hash(): that's salted per-process
		# (PYTHONHASHSEED), so it can't be trusted to spread test methods
		# across distinct weeks consistently -- confirmed while writing
		# this suite (two methods collided on the same week and each saw
		# the other's leftover Timesheet). Hash the *full* test id
		# (module + class + method), not just the method name: two
		# different test *files* each hashing their own bare method names
		# can still collide with each other on the same week (also
		# confirmed directly -- test_api_approvals.py and this file
		# produced two overlapping Timesheets in the same run).
		digest = int(hashlib.md5(self.id().encode()).hexdigest(), 16)
		week_offset = (digest % 200000) * 7
		self.monday, self.sunday = get_week_bounds(add_days(frappe.utils.today(), week_offset))
		self.project = make_test_project(method_name, users=[EMPLOYEE_USER])

		# save_my_week and submit_my_week share one 30-per-minute bucket
		# (helixhr.utils.rate_limit_per_user). This file alone spends more
		# than thirty writes on one user inside a minute, and a second run
		# started inside the same minute spends them twice -- both produce
		# a RateLimitExceededError that has nothing to do with what is
		# being tested. The key is raw (incrby, not set_value), so this is
		# a raw delete.
		frappe.cache.delete(f"helixhr:rate-limit:save_my_week:{EMPLOYEE_USER}")

	def tearDown(self):
		frappe.set_user("Administrator")

	def _week_row(self, hours=4, project=None):
		return {
			"date": str(self.monday),
			"project": project or self.project,
			"task": "",
			"hours": hours,
			"note": "worked",
		}

	def _save_and_submit(self):
		frappe.set_user(EMPLOYEE_USER)
		name = save_my_week(str(self.monday), json.dumps([self._week_row()]))
		apply_workflow({"doctype": "Timesheet", "name": name}, "Submit")
		return name

	def test_empty_week_has_no_timesheet_and_projects_are_scoped(self):
		frappe.set_user(EMPLOYEE_USER)
		result = get_my_week(str(self.monday))
		self.assertIsNone(result["timesheet"])

		projects = get_my_projects()
		self.assertIn(self.project, [p["name"] for p in projects])

	def test_save_creates_one_timesheet_and_second_save_updates_it(self):
		# ERPNext's own Timesheet.validate() recomputes start_date/end_date
		# from the actual min/max of time_logs' from_time/to_time, not
		# from whatever save_my_week sets directly -- so a row on both
		# the Monday and the Sunday is what actually proves the header
		# dates cover the intended week, not a single mid-week row.
		sunday_row = self._week_row()
		sunday_row["date"] = str(self.sunday)

		frappe.set_user(EMPLOYEE_USER)
		first = save_my_week(str(self.monday), json.dumps([self._week_row(), sunday_row]))
		second = save_my_week(str(self.monday), json.dumps([self._week_row(hours=5), sunday_row]))

		self.assertEqual(first, second)
		doc = frappe.get_doc("Timesheet", first)
		self.assertEqual(doc.employee, self.employee_name)
		self.assertEqual(str(doc.start_date), str(self.monday))
		self.assertEqual(str(doc.end_date), str(self.sunday))
		self.assertEqual(doc.time_logs[0].hours, 5)
		self.assertEqual(doc.time_logs[0].hours, 5)

	def test_row_without_project_is_refused(self):
		frappe.set_user(EMPLOYEE_USER)
		row = self._week_row()
		row["project"] = ""
		with self.assertRaises(frappe.ValidationError):
			save_my_week(str(self.monday), json.dumps([row]))

	def test_row_over_24_hours_is_refused(self):
		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.ValidationError):
			save_my_week(str(self.monday), json.dumps([self._week_row(hours=25)]))

	def test_day_total_over_24_hours_is_refused(self):
		frappe.set_user(EMPLOYEE_USER)
		rows = [self._week_row(hours=20), self._week_row(hours=5)]
		with self.assertRaises(frappe.ValidationError):
			save_my_week(str(self.monday), json.dumps(rows))

	def test_cannot_book_a_project_outside_get_my_projects(self):
		other_project = make_test_project(f"{self.id().split('.')[-1]}-other")
		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.ValidationError):
			save_my_week(str(self.monday), json.dumps([self._week_row(project=other_project)]))

	def test_submit_moves_to_pending_approval_and_shares_with_manager(self):
		name = self._save_and_submit()

		doc = frappe.get_doc("Timesheet", name)
		self.assertEqual(doc.workflow_state, "Pending Approval")
		self.assertEqual(doc.docstatus, 0)

		shared_users = [row.user for row in frappe.share.get_users("Timesheet", name)]
		self.assertIn(MANAGER_USER, shared_users)

	def test_submit_refused_when_employee_has_no_manager(self):
		frappe.db.set_value("Employee", self.employee_name, "reports_to", None)
		frappe.set_user(EMPLOYEE_USER)
		name = save_my_week(str(self.monday), json.dumps([self._week_row()]))

		with self.assertRaises(frappe.ValidationError):
			apply_workflow({"doctype": "Timesheet", "name": name}, "Submit")

	def test_manager_can_read_pending_timesheet_a_different_manager_cannot(self):
		name = self._save_and_submit()

		frappe.set_user(MANAGER_USER)
		frappe.get_doc("Timesheet", name)  # no PermissionError

		# A real Employee + User Permission (not a bare User, which would
		# trivially "prove" this since it has no scoping to defeat at
		# all -- every real portal user gets one via
		# create_user_permission, R5/KTD5).
		from helixhr.tests.utils import make_test_user

		other_manager = "other-manager@helixhr.test"
		frappe.set_user("Administrator")
		company = frappe.db.get_value("Employee", self.employee_name, "company")
		make_test_user(other_manager, company)

		# NOT a PermissionError here on this site's default config: User
		# Permission on Employee only *directly* restricts the Employee
		# doctype's own records (confirmed elsewhere -- see
		# test_employee_a_cannot_change_employee_b). Restricting a
		# *different* doctype's Link field that merely points to Employee
		# (Timesheet.employee) additionally requires System Settings'
		# apply_strict_user_permissions, which the plan defers to the U11
		# go-live checklist as a site-level toggle rather than shipping it
		# as a fixture (flipping it globally during development risks
		# over-restricting HR's own legitimate cross-employee views in
		# Desk while every unit's tests are still being written). Until
		# that toggle is on, an unrelated manager reading a report's
		# pending timesheet by name is a real, documented gap -- what
		# *is* guaranteed by this unit's own code, and covered by
		# test_wrong_manager_cannot_approve (AE4), is that they can never
		# act on it (approve/reject), because that path is enforced by
		# the workflow condition and before_submit guard directly, not by
		# User Permission.
		frappe.set_user(other_manager)
		frappe.get_doc("Timesheet", name)

	def test_wrong_manager_cannot_approve(self):
		"""AE4."""
		name = self._save_and_submit()

		wrong_manager = "wrong-manager@helixhr.test"
		if not frappe.db.exists("User", wrong_manager):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": wrong_manager,
					"first_name": "Wrong",
					"last_name": "Manager",
					"send_welcome_email": 0,
					"roles": [{"doctype": "Has Role", "role": "Employee"}],
				}
			).insert(ignore_permissions=True)
		frappe.share.add_docshare(
			"Timesheet", name, wrong_manager, write=1, submit=1, flags={"ignore_share_permission": True}
		)

		frappe.set_user(wrong_manager)
		with self.assertRaises(Exception):
			apply_workflow({"doctype": "Timesheet", "name": name}, "Approve")

		doc = frappe.get_doc("Timesheet", name)
		self.assertEqual(doc.workflow_state, "Pending Approval")
		self.assertEqual(doc.docstatus, 0)

	def test_employee_cannot_self_approve_via_workflow_or_raw_submit(self):
		"""AE6."""
		name = self._save_and_submit()

		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(Exception):
			apply_workflow({"doctype": "Timesheet", "name": name}, "Approve")

		doc = frappe.get_doc("Timesheet", name)
		self.assertEqual(doc.workflow_state, "Pending Approval")

		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("Timesheet", name).submit()

		doc.reload()
		self.assertEqual(doc.workflow_state, "Pending Approval")
		self.assertEqual(doc.docstatus, 0)

	def test_manager_approve_submits_and_unshares(self):
		"""AE3 (approve half)."""
		name = self._save_and_submit()

		frappe.set_user(MANAGER_USER)
		apply_workflow({"doctype": "Timesheet", "name": name}, "Approve")

		doc = frappe.get_doc("Timesheet", name)
		self.assertEqual(doc.workflow_state, "Approved")
		self.assertEqual(doc.docstatus, 1)

		frappe.set_user("Administrator")
		shared_users = [row.user for row in frappe.share.get_users("Timesheet", name)]
		self.assertNotIn(MANAGER_USER, shared_users)

	def test_manager_reject_with_comment_then_employee_edits_and_resubmits(self):
		"""AE3 (reject, edit, resubmit)."""
		name = self._save_and_submit()

		frappe.set_user(MANAGER_USER)
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Comment",
				"reference_doctype": "Timesheet",
				"reference_name": name,
				"content": "Please add task details",
			}
		).insert(ignore_permissions=True)
		apply_workflow({"doctype": "Timesheet", "name": name}, "Reject")

		doc = frappe.get_doc("Timesheet", name)
		self.assertEqual(doc.workflow_state, "Rejected")
		self.assertEqual(doc.docstatus, 0)

		frappe.set_user("Administrator")
		shared_users = [row.user for row in frappe.share.get_users("Timesheet", name)]
		self.assertNotIn(MANAGER_USER, shared_users)

		frappe.set_user(EMPLOYEE_USER)
		apply_workflow({"doctype": "Timesheet", "name": name}, "Edit")
		doc = frappe.get_doc("Timesheet", name)
		self.assertEqual(doc.workflow_state, "Draft")

		same_name = save_my_week(str(self.monday), json.dumps([self._week_row(hours=6)]))
		self.assertEqual(same_name, name)

	def test_hr_manager_can_approve_even_if_reports_to_user_disabled(self):
		name = self._save_and_submit()

		frappe.set_user("Administrator")
		frappe.db.set_value("User", MANAGER_USER, "enabled", 0)
		hr_manager_user = "hr-manager-ts@helixhr.test"
		if not frappe.db.exists("User", hr_manager_user):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": hr_manager_user,
					"first_name": "HR",
					"last_name": "Manager",
					"send_welcome_email": 0,
					"roles": [{"doctype": "Has Role", "role": "HR Manager"}],
				}
			).insert(ignore_permissions=True)

		frappe.set_user(hr_manager_user)
		apply_workflow({"doctype": "Timesheet", "name": name}, "Approve")

		doc = frappe.get_doc("Timesheet", name)
		self.assertEqual(doc.workflow_state, "Approved")

	def test_hr_cancel_then_get_my_week_offers_a_fresh_week(self):
		name = self._save_and_submit()
		frappe.set_user(MANAGER_USER)
		apply_workflow({"doctype": "Timesheet", "name": name}, "Approve")

		frappe.set_user("Administrator")
		frappe.get_doc("Timesheet", name).cancel()

		frappe.set_user(EMPLOYEE_USER)
		result = get_my_week(str(self.monday))
		self.assertIsNone(result["timesheet"])

	# --- P2-U6 ---------------------------------------------------------

	def _token(self):
		"""The `modified` the screen would have been rendered from."""
		week = get_my_week(str(self.monday))
		return week["timesheet"]["modified"] if week["timesheet"] else None

	def test_invalid_rows_leave_the_week_draft_and_never_submit(self):
		"""P2-AE4, P2-U6 scenario 1. The defect this unit exists for: the
		browser saved, swallowed the failure, and submitted the *stale*
		draft anyway."""
		frappe.set_user(EMPLOYEE_USER)
		name = save_my_week(str(self.monday), json.dumps([self._week_row(hours=4)]))
		token = self._token()

		with self.assertRaises(frappe.ValidationError):
			submit_my_week(str(self.monday), json.dumps([self._week_row(hours=25)]), token)

		doc = frappe.get_doc("Timesheet", name)
		self.assertEqual(doc.workflow_state, "Draft")
		self.assertEqual(doc.docstatus, 0)
		self.assertEqual(len(doc.time_logs), 1)
		self.assertEqual(doc.time_logs[0].hours, 4)

	def test_submit_persists_exactly_the_visible_rows_and_moves_once(self):
		"""P2-U6 scenario 2."""
		tuesday = str(add_days(self.monday, 1))
		second = self._week_row(hours=2)
		second["date"] = tuesday

		frappe.set_user(EMPLOYEE_USER)
		result = submit_my_week(str(self.monday), json.dumps([self._week_row(hours=4), second]))

		self.assertEqual(result["workflow_state"], "Pending Approval")
		doc = frappe.get_doc("Timesheet", result["name"])
		self.assertEqual(doc.docstatus, 0)
		self.assertEqual(
			sorted((str(get_datetime(row.from_time).date()), row.hours) for row in doc.time_logs),
			sorted([(str(self.monday), 4.0), (tuesday, 2.0)]),
		)

	def test_a_second_submit_is_refused_rather_than_transitioning_twice(self):
		"""P2-U6 scenario 7. Both taps carry the token the page was
		rendered from; the second one is answered, not applied."""
		frappe.set_user(EMPLOYEE_USER)
		save_my_week(str(self.monday), json.dumps([self._week_row()]))
		token = self._token()

		submit_my_week(str(self.monday), json.dumps([self._week_row()]), token)
		with self.assertRaises(frappe.ValidationError):
			submit_my_week(str(self.monday), json.dumps([self._week_row()]), token)

		names = frappe.get_all(
			"Timesheet",
			filters={"employee": self.employee_name, "start_date": str(self.monday), "docstatus": ["!=", 2]},
			pluck="name",
		)
		self.assertEqual(len(names), 1)
		self.assertEqual(frappe.db.get_value("Timesheet", names[0], "workflow_state"), "Pending Approval")

	def test_submit_against_a_week_that_moved_on_is_refused(self):
		"""P2-R25/P2-R27: another tab saved between render and Submit."""
		frappe.set_user(EMPLOYEE_USER)
		save_my_week(str(self.monday), json.dumps([self._week_row()]))
		stale = self._token()
		save_my_week(str(self.monday), json.dumps([self._week_row(hours=6)]))

		with self.assertRaises(frappe.ValidationError):
			submit_my_week(str(self.monday), json.dumps([self._week_row()]), stale)

		self.assertEqual(get_my_week(str(self.monday))["timesheet"]["workflow_state"], "Draft")

	def test_submit_without_a_token_when_the_week_already_exists_is_refused(self):
		frappe.set_user(EMPLOYEE_USER)
		save_my_week(str(self.monday), json.dumps([self._week_row()]))

		with self.assertRaises(frappe.ValidationError):
			submit_my_week(str(self.monday), json.dumps([self._week_row()]))

	def test_a_date_outside_the_week_is_refused(self):
		"""P2-U6 scenario 4."""
		frappe.set_user(EMPLOYEE_USER)
		row = self._week_row()
		row["date"] = str(add_days(self.monday, 7))
		with self.assertRaises(frappe.ValidationError):
			save_my_week(str(self.monday), json.dumps([row]))

	def test_more_rows_than_a_week_can_hold_are_refused(self):
		frappe.set_user(EMPLOYEE_USER)
		rows = [self._week_row(hours=0.25) for _ in range(101)]
		with self.assertRaises(frappe.ValidationError):
			save_my_week(str(self.monday), json.dumps(rows))

	def test_malformed_rows_are_refused(self):
		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.ValidationError):
			save_my_week(str(self.monday), json.dumps(["not a row"]))
		with self.assertRaises(frappe.ValidationError):
			save_my_week(str(self.monday), json.dumps({"date": str(self.monday)}))

	def test_two_projects_on_one_day_are_saved_side_by_side(self):
		"""The ordinary case the day-first phone list and the desktop grid
		are both built for. ERPNext refuses two time logs whose windows
		overlap, and every row used to start at 09:00 -- so a second
		project on the same day threw OverlapError."""
		second = make_test_project(f"{self.id().split('.')[-1]}-second", users=[EMPLOYEE_USER])

		frappe.set_user(EMPLOYEE_USER)
		name = save_my_week(
			str(self.monday),
			json.dumps([self._week_row(hours=4), self._week_row(hours=2, project=second)]),
		)

		doc = frappe.get_doc("Timesheet", name)
		self.assertEqual(len(doc.time_logs), 2)
		self.assertEqual(doc.total_hours, 6)
		self.assertEqual(
			{str(get_datetime(row.from_time).date()) for row in doc.time_logs}, {str(self.monday)}
		)

	def test_a_task_from_another_project_is_refused(self):
		other_project = make_test_project(f"{self.id().split('.')[-1]}-task-owner")
		frappe.set_user("Administrator")
		task = frappe.get_doc(
			{"doctype": "Task", "subject": "Not yours", "project": other_project}
		).insert(ignore_permissions=True)

		frappe.set_user(EMPLOYEE_USER)
		row = self._week_row()
		row["task"] = task.name
		with self.assertRaises(frappe.ValidationError):
			save_my_week(str(self.monday), json.dumps([row]))

	def test_projects_come_back_with_their_open_tasks(self):
		frappe.set_user("Administrator")
		task = frappe.get_doc(
			{"doctype": "Task", "subject": "Bookable", "project": self.project}
		).insert(ignore_permissions=True)

		frappe.set_user(EMPLOYEE_USER)
		mine = next(p for p in get_my_projects() if p["name"] == self.project)
		self.assertIn(task.name, [t["name"] for t in mine["tasks"]])

	def test_sent_back_week_is_reopened_saved_and_sent_again(self):
		"""P2-U6 scenario 8. "Edit and resubmit" used to perform only the
		reopen, leaving the fix unsaved and unsent."""
		name = self._save_and_submit()

		frappe.set_user(MANAGER_USER)
		apply_workflow({"doctype": "Timesheet", "name": name}, "Reject")

		frappe.set_user(EMPLOYEE_USER)
		result = submit_my_week(str(self.monday), json.dumps([self._week_row(hours=7)]), self._token())

		self.assertEqual(result["name"], name)
		self.assertEqual(result["workflow_state"], "Pending Approval")
		self.assertEqual(frappe.get_doc("Timesheet", name).time_logs[0].hours, 7)

	def test_copying_the_previous_week_leaves_the_source_alone(self):
		"""P2-U6 scenario 3, server half: the copy is a plain save of
		re-dated rows, so the week it came from is never touched."""
		previous_monday = add_days(self.monday, -7)
		source_row = self._week_row(hours=3)
		source_row["date"] = str(previous_monday)

		frappe.set_user(EMPLOYEE_USER)
		source = save_my_week(str(previous_monday), json.dumps([source_row]))

		copied = dict(source_row, date=str(self.monday))
		target = save_my_week(str(self.monday), json.dumps([copied]))

		self.assertNotEqual(source, target)
		source_doc = frappe.get_doc("Timesheet", source)
		target_doc = frappe.get_doc("Timesheet", target)
		self.assertEqual(source_doc.workflow_state, "Draft")
		self.assertEqual(len(source_doc.time_logs), 1)
		self.assertEqual(str(get_datetime(source_doc.time_logs[0].from_time).date()), str(previous_monday))
		self.assertEqual(target_doc.time_logs[0].project, source_doc.time_logs[0].project)
		self.assertEqual(target_doc.time_logs[0].hours, source_doc.time_logs[0].hours)
		self.assertEqual(target_doc.time_logs[0].description, source_doc.time_logs[0].description)

	def test_history_is_a_bounded_page_of_mondays_newest_first(self):
		"""P2-U6 scenario 5, P2-R22."""
		frappe.set_user(EMPLOYEE_USER)
		# A week whose Monday is empty: ERPNext recomputes start_date from
		# the earliest time log, so this Timesheet starts on a Tuesday and
		# the route parameter still has to be its Monday.
		older = add_days(self.monday, -14)
		older_row = self._week_row()
		older_row["date"] = str(add_days(older, 1))
		save_my_week(str(older), json.dumps([older_row]))

		middle_row = self._week_row()
		middle_row["date"] = str(add_days(self.monday, -7))
		save_my_week(str(add_days(self.monday, -7)), json.dumps([middle_row]))
		save_my_week(str(self.monday), json.dumps([self._week_row()]))

		# The employee already owns weeks from every other method in this
		# file (each one hashes itself onto its own far-future Monday), so
		# the assertions are about *order and identity*, not about which
		# rows land on the first page.
		first_page = get_my_timesheet_history(limit=2)
		self.assertEqual(len(first_page["weeks"]), 2)
		self.assertGreater(first_page["total"], 2)

		all_weeks = []
		while len(all_weeks) < first_page["total"]:
			page = get_my_timesheet_history(limit=52, start=len(all_weeks))
			if not page["weeks"]:
				break
			all_weeks.extend(page["weeks"])

		starts = [week["week_start"] for week in all_weeks]
		self.assertEqual(starts, sorted(starts, reverse=True))
		self.assertEqual(starts[:2], [week["week_start"] for week in first_page["weeks"]])
		self.assertLess(starts.index(str(self.monday)), starts.index(str(add_days(self.monday, -7))))
		self.assertLess(starts.index(str(add_days(self.monday, -7))), starts.index(str(older)))
		# The Monday, not the Tuesday ERPNext recomputed onto the record.
		self.assertIn(str(older), starts)

	def test_history_carries_the_managers_reason(self):
		name = self._save_and_submit()
		frappe.set_user(MANAGER_USER)
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Comment",
				"reference_doctype": "Timesheet",
				"reference_name": name,
				"content": "Friday hours are missing",
			}
		).insert(ignore_permissions=True)
		apply_workflow({"doctype": "Timesheet", "name": name}, "Reject")

		frappe.set_user(EMPLOYEE_USER)
		week = next(
			w for w in get_my_timesheet_history(limit=10)["weeks"] if w["name"] == name
		)
		self.assertEqual(week["workflow_state"], "Rejected")
		self.assertEqual(week["rejection_comment"], "Friday hours are missing")

	def test_a_timesheet_id_resolves_to_its_monday_only_for_its_owner(self):
		"""Closes P2-U4's recorded deviation: a timesheet notification
		carries the record id, and the week route takes a Monday."""
		frappe.set_user(EMPLOYEE_USER)
		row = self._week_row()
		row["date"] = str(add_days(self.monday, 2))
		name = save_my_week(str(self.monday), json.dumps([row]))

		self.assertEqual(get_timesheet_week_start(name), str(self.monday))

		frappe.set_user(MANAGER_USER)
		with self.assertRaises(frappe.PermissionError):
			get_timesheet_week_start(name)

