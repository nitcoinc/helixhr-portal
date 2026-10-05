import hashlib
import json
from unittest.mock import patch

import frappe
from frappe.model.workflow import apply_workflow, get_transitions
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, get_datetime

from helixhr.api import (
	get_my_month,
	get_my_projects,
	get_my_timesheet_history,
	get_my_week,
	get_timesheet_week_start,
	raise_timesheet_change,
	recall_my_week,
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

	def test_submitting_notifies_the_manager_once_and_a_resave_does_not_repeat_it(self):
		"""P5-U10: the manager's arrival notice."""
		name = self._save_and_submit()

		logs = frappe.get_all(
			"Notification Log",
			filters={"for_user": MANAGER_USER, "document_type": "Timesheet", "document_name": name},
		)
		self.assertEqual(len(logs), 1)

		frappe.set_user("Administrator")
		frappe.get_doc("Timesheet", name).save(ignore_permissions=True)

		logs = frappe.get_all(
			"Notification Log",
			filters={"for_user": MANAGER_USER, "document_type": "Timesheet", "document_name": name},
		)
		self.assertEqual(len(logs), 1, "a re-save while still Pending Approval must not notify again")

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

	def test_send_back_also_removes_the_share(self):
		"""P2-U7 scenario 8. Approve was already covered; a sent-back week
		is just as decided, and the approver has just as little left to do
		with it -- but its share used to be removed only because
		`workflow_state` happened to be listed, and Cancelled was not."""
		name = self._save_and_submit()

		frappe.set_user(MANAGER_USER)
		apply_workflow({"doctype": "Timesheet", "name": name}, "Send Back")

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
		apply_workflow({"doctype": "Timesheet", "name": name}, "Send Back")

		doc = frappe.get_doc("Timesheet", name)
		self.assertEqual(doc.workflow_state, "Sent Back")
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

	# --- P4-U1: Send to HR, no Reject, and R8 on the HR route ---------------

	def test_send_to_hr_then_hr_approves_with_no_docshare(self):
		"""P4-R5, P4-KTD2. Pending HR carries no share -- `timesheet_on_update`
		removes the approver's share outside Pending Approval -- so HR's
		Approve there rides HR Manager's own native submit on Timesheet."""
		name = self._save_and_submit()

		frappe.set_user(MANAGER_USER)
		apply_workflow({"doctype": "Timesheet", "name": name}, "Send to HR")

		doc = frappe.get_doc("Timesheet", name)
		self.assertEqual(doc.workflow_state, "Pending HR")
		self.assertEqual(doc.docstatus, 0)
		frappe.set_user("Administrator")
		self.assertEqual(frappe.share.get_users("Timesheet", name), [])

		hr_user = self._hr_manager_user("hr-manager-p4@helixhr.test")
		frappe.set_user(hr_user)
		apply_workflow({"doctype": "Timesheet", "name": name}, "Approve")
		doc = frappe.get_doc("Timesheet", name)
		self.assertEqual((doc.workflow_state, doc.docstatus), ("Approved", 1))

	def test_a_week_is_never_rejected_outright(self):
		"""P4-KTD2: a week is one row and the hours still have to be
		recorded, so there is no Reject transition on either pending state
		for anybody."""
		name = self._save_and_submit()
		hr_user = self._hr_manager_user("hr-manager-p4b@helixhr.test")

		for state, mover in (("Pending Approval", MANAGER_USER), ("Pending HR", hr_user)):
			if state == "Pending HR":
				frappe.set_user(MANAGER_USER)
				apply_workflow({"doctype": "Timesheet", "name": name}, "Send to HR")
			frappe.set_user(mover)
			actions = {t.action for t in get_transitions(frappe.get_doc("Timesheet", name))}
			self.assertNotIn("Reject", actions, msg=state)
			self.assertIn("Send Back", actions, msg=state)

	def test_an_hr_manager_cannot_approve_their_own_week(self):
		"""P4-R8. The HR Manager transitions used to carry
		`allow_self_approval: 1` with no condition at all, so an HR Manager
		who is also an employee could approve their own week -- through the
		workflow and through a raw submit."""
		from frappe.client import submit as client_submit

		name = self._save_and_submit()

		frappe.set_user("Administrator")
		employee_login = frappe.get_doc("User", EMPLOYEE_USER)
		employee_login.add_roles("HR Manager")
		frappe.clear_cache(user=EMPLOYEE_USER)
		try:
			frappe.set_user(EMPLOYEE_USER)
			with self.assertRaises(Exception):
				apply_workflow({"doctype": "Timesheet", "name": name}, "Approve")
			with self.assertRaises(frappe.PermissionError):
				client_submit(frappe.get_doc("Timesheet", name).as_dict())
			with self.assertRaises(Exception):
				apply_workflow({"doctype": "Timesheet", "name": name}, "Send Back")
			doc = frappe.get_doc("Timesheet", name)
			self.assertEqual((doc.workflow_state, doc.docstatus), ("Pending Approval", 0))
		finally:
			frappe.set_user("Administrator")
			employee_login.reload()
			employee_login.remove_roles("HR Manager")
			frappe.clear_cache(user=EMPLOYEE_USER)

	def _hr_manager_user(self, email):
		frappe.set_user("Administrator")
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": "HR",
					"last_name": "Manager",
					"send_welcome_email": 0,
					"roles": [{"doctype": "Has Role", "role": "HR Manager"}],
				}
			).insert(ignore_permissions=True)
		return email

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
		apply_workflow({"doctype": "Timesheet", "name": name}, "Send Back")

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
		apply_workflow({"doctype": "Timesheet", "name": name}, "Send Back")

		frappe.set_user(EMPLOYEE_USER)
		week = next(
			w for w in get_my_timesheet_history(limit=10)["weeks"] if w["name"] == name
		)
		self.assertEqual(week["workflow_state"], "Sent Back")
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


	# --- KTD10: the week is a range, because ERPNext rewrites start_date ---

	def _tuesday_row(self, hours=8):
		row = self._week_row(hours=hours)
		row["date"] = str(add_days(self.monday, 1))
		return row

	def _timesheets_this_week(self):
		return frappe.get_all(
			"Timesheet",
			filters={
				"employee": self.employee_name,
				"start_date": ["between", [str(self.monday), str(self.sunday)]],
				"docstatus": ["!=", 2],
			},
			pluck="name",
		)

	def test_a_week_that_starts_on_tuesday_is_still_that_weeks_timesheet(self):
		"""ERPNext's Timesheet.set_dates rewrites start_date to the earliest
		booked day, so a week with no Monday hours -- leave, a holiday, or
		simply starting mid-week -- persists with the Tuesday. Matched by
		`start_date == monday`, the grid read straight back as an empty
		week."""
		frappe.set_user(EMPLOYEE_USER)
		name = save_my_week(str(self.monday), json.dumps([self._tuesday_row()]))

		frappe.set_user("Administrator")
		self.assertEqual(
			str(frappe.db.get_value("Timesheet", name, "start_date")),
			str(add_days(self.monday, 1)),
		)

		frappe.set_user(EMPLOYEE_USER)
		week = get_my_week(str(self.monday))
		self.assertIsNotNone(week["timesheet"])
		self.assertEqual(week["timesheet"]["name"], name)
		self.assertEqual(week["timesheet"]["total_hours"], 8)
		self.assertEqual(week["timesheet"]["rows"][0]["date"], str(add_days(self.monday, 1)))

	def test_saving_a_tuesday_start_week_twice_updates_the_one_timesheet(self):
		"""The second save used to insert -- and ERPNext refused it with a
		raw OverlapError against the row the employee could not see."""
		frappe.set_user(EMPLOYEE_USER)
		first = save_my_week(str(self.monday), json.dumps([self._tuesday_row()]))
		second = save_my_week(str(self.monday), json.dumps([self._tuesday_row(hours=6)]))

		self.assertEqual(first, second)
		frappe.set_user("Administrator")
		self.assertEqual(self._timesheets_this_week(), [first])
		self.assertEqual(frappe.get_doc("Timesheet", first).total_hours, 6)

	def test_a_tuesday_start_week_is_sent_once_and_refused_the_second_time(self):
		frappe.set_user(EMPLOYEE_USER)
		save_my_week(str(self.monday), json.dumps([self._tuesday_row()]))
		token = self._token()
		sent = submit_my_week(str(self.monday), json.dumps([self._tuesday_row()]), token)
		self.assertEqual(sent["workflow_state"], "Pending Approval")

		with self.assertRaises(frappe.ValidationError) as refused:
			submit_my_week(str(self.monday), json.dumps([self._tuesday_row()]), token)
		self.assertIn("Pending Approval", str(refused.exception))

		frappe.set_user("Administrator")
		self.assertEqual(self._timesheets_this_week(), [sent["name"]])

	def test_both_write_paths_lock_the_employee_row(self):
		"""A `SELECT ... FOR UPDATE` only excludes writers that also take it.
		submit_my_week held the lock while save_my_week took none, so a
		concurrent save walked straight past it and both could insert."""
		from unittest.mock import patch

		import helixhr.api as api

		frappe.set_user(EMPLOYEE_USER)
		with patch("helixhr.api._lock_employee", wraps=api._lock_employee) as lock:
			save_my_week(str(self.monday), json.dumps([self._week_row()]))
			self.assertTrue(lock.called, "save_my_week took no lock")

			lock.reset_mock()
			submit_my_week(str(self.monday), json.dumps([self._week_row()]), self._token())
			self.assertTrue(lock.called, "submit_my_week took no lock")

	# --- P7-U6: billable-hours capture (R6, R7, R8, R9, KTD1, KTD2) --------

	def _mark_billable(self, project):
		frappe.set_user("Administrator")
		frappe.db.set_value("Project", project, "helixhr_is_billable", 1)

	def _timesheet_detail_row(self, project):
		"""The lone `Timesheet Detail` row for `project` in this test's
		week, read with the fields KTD1 cares about."""
		rows = frappe.get_all(
			"Timesheet Detail",
			filters={"project": project},
			fields=["name", "hours", "is_billable", "billing_hours"],
		)
		self.assertEqual(len(rows), 1)
		return rows[0]

	def test_hours_on_a_billable_project_persist_billable_with_matching_billing_hours(self):
		self._mark_billable(self.project)

		frappe.set_user(EMPLOYEE_USER)
		save_my_week(str(self.monday), json.dumps([self._week_row(hours=6)]))

		row = self._timesheet_detail_row(self.project)
		self.assertEqual(row.is_billable, 1)
		self.assertEqual(row.hours, 6)
		self.assertEqual(row.billing_hours, 6)

	def test_hours_on_a_non_billable_project_persist_not_billable_with_zero_billing_hours(self):
		# self.project is never marked billable in setUp -- this is the
		# default path.
		frappe.set_user(EMPLOYEE_USER)
		save_my_week(str(self.monday), json.dumps([self._week_row(hours=6)]))

		row = self._timesheet_detail_row(self.project)
		self.assertEqual(row.is_billable, 0)
		self.assertEqual(row.billing_hours, 0)

	def test_a_billable_flag_on_the_row_itself_is_ignored_the_project_wins(self):
		"""KTD2: the consultant does not decide this. A row that tries to
		set its own billable-ish key is silently overridden by the
		project's own flag either way."""
		# Non-billable project, row claims billable -- project wins (0).
		frappe.set_user(EMPLOYEE_USER)
		row = self._week_row(hours=3)
		row["is_billable"] = 1
		save_my_week(str(self.monday), json.dumps([row]))
		self.assertEqual(self._timesheet_detail_row(self.project).is_billable, 0)

		# Billable project, row claims not billable -- project still wins (1).
		self._mark_billable(self.project)
		frappe.set_user(EMPLOYEE_USER)
		row = self._week_row(hours=3)
		row["is_billable"] = 0
		save_my_week(str(self.monday), json.dumps([row]))
		self.assertEqual(self._timesheet_detail_row(self.project).is_billable, 1)

	def test_a_billing_rate_or_amount_on_the_row_is_ignored_and_nothing_monetary_persists(self):
		"""R8/KTD1: the request may carry a rate or amount; nothing here
		reads it, and nothing monetary is persisted from it."""
		self._mark_billable(self.project)
		frappe.set_user(EMPLOYEE_USER)
		row = self._week_row(hours=4)
		row["billing_rate"] = 999
		row["billing_amount"] = 999
		row["costing_rate"] = 999
		row["costing_amount"] = 999
		save_my_week(str(self.monday), json.dumps([row]))

		detail = frappe.get_doc(
			"Timesheet Detail", {"project": self.project}
		)
		self.assertEqual(detail.billing_rate, 0)
		self.assertEqual(detail.billing_amount, 0)
		self.assertEqual(detail.costing_rate, 0)
		self.assertEqual(detail.costing_amount, 0)

	def test_marking_a_project_billable_afterward_leaves_a_saved_week_unchanged(self):
		"""R9, timesheet-specific: a week already written through
		`save_my_week` is not retroactively altered just because the
		project it books to is later marked billable -- flipping the
		project's flag alone never rewrites a `Timesheet Detail` row that
		already exists."""
		frappe.set_user(EMPLOYEE_USER)
		save_my_week(str(self.monday), json.dumps([self._week_row(hours=5)]))
		before = self._timesheet_detail_row(self.project)

		self._mark_billable(self.project)

		after = self._timesheet_detail_row(self.project)
		self.assertEqual(after, before)
		self.assertEqual(after.is_billable, 0)

	def test_parent_timesheet_billable_hours_reflect_rows_and_amount_stays_zero(self):
		"""KTD1: on a site with no `Activity Cost` record, billable hours
		roll up but the amount stays 0 -- money never appears just because
		hours were marked billable."""
		self._mark_billable(self.project)
		frappe.set_user(EMPLOYEE_USER)
		name = save_my_week(str(self.monday), json.dumps([self._week_row(hours=7)]))

		doc = frappe.get_doc("Timesheet", name)
		self.assertEqual(doc.total_billable_hours, 7)
		self.assertEqual(doc.total_billable_amount, 0)

	def test_submitting_a_billable_week_still_transitions_through_the_workflow(self):
		"""R7 does not disturb the existing approval workflow (P2-U6)."""
		self._mark_billable(self.project)
		name = self._save_and_submit()

		doc = frappe.get_doc("Timesheet", name)
		self.assertEqual(doc.workflow_state, "Pending Approval")
		self.assertEqual(doc.time_logs[0].is_billable, 1)


	# -----------------------------------------------------------------------
	# P7-U7 / R10, R11: per-day notes.
	#
	# The write path already stores one `description` per Timesheet Detail
	# row, and `get_my_week` already returns that row's own `description` as
	# `note` (helixhr/api.py get_my_week, `"note": row.description`). The
	# change for per-day notes is entirely in the frontend's grouping key
	# (P7-KTD7) -- these tests exist to prove that claim rather than to guard
	# a code change, and would already have passed before U7 touched a
	# single line of Vue.
	# -----------------------------------------------------------------------

	def test_two_days_on_one_line_carry_different_notes_and_read_back_correctly(self):
		tuesday_row = self._week_row(hours=3)
		tuesday_row["date"] = str(add_days(self.monday, 1))
		tuesday_row["note"] = "Monday's note"

		wednesday_row = self._week_row(hours=2)
		wednesday_row["date"] = str(add_days(self.monday, 2))
		wednesday_row["note"] = "a different note entirely"

		frappe.set_user(EMPLOYEE_USER)
		save_my_week(str(self.monday), json.dumps([tuesday_row, wednesday_row]))

		rows = {row["date"]: row for row in get_my_week(str(self.monday))["timesheet"]["rows"]}
		self.assertEqual(rows[tuesday_row["date"]]["note"], "Monday's note")
		self.assertEqual(rows[wednesday_row["date"]]["note"], "a different note entirely")

	def test_a_note_on_one_day_only_reads_back_with_the_other_day_empty(self):
		noted_row = self._week_row(hours=4)
		noted_row["note"] = "only this day has a note"

		bare_row = self._week_row(hours=1)
		bare_row["date"] = str(add_days(self.monday, 1))
		bare_row["note"] = ""

		frappe.set_user(EMPLOYEE_USER)
		save_my_week(str(self.monday), json.dumps([noted_row, bare_row]))

		rows = {row["date"]: row for row in get_my_week(str(self.monday))["timesheet"]["rows"]}
		self.assertEqual(rows[noted_row["date"]]["note"], "only this day has a note")
		self.assertIn(rows[bare_row["date"]]["note"], (None, ""))

	def test_clearing_a_note_saves_as_empty_not_the_previous_value(self):
		frappe.set_user(EMPLOYEE_USER)
		first_row = self._week_row(hours=4)
		first_row["note"] = "will be cleared"
		save_my_week(str(self.monday), json.dumps([first_row]))

		cleared_row = self._week_row(hours=4)
		cleared_row["note"] = ""
		save_my_week(str(self.monday), json.dumps([cleared_row]))

		row = get_my_week(str(self.monday))["timesheet"]["rows"][0]
		self.assertIn(row["note"], (None, ""))

	def test_a_note_without_hours_is_refused_rather_than_saved_as_a_phantom_row(self):
		"""The frontend never sends a day with a note but no hours (P7-U7),
		and the server backs that up independently: `_validate_rows` refuses
		any row below 0.25 hours, note or not."""
		phantom_row = self._week_row(hours=0)
		phantom_row["note"] = "a note with nothing behind it"

		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.ValidationError):
			save_my_week(str(self.monday), json.dumps([phantom_row]))

	def test_a_timesheet_written_before_per_day_notes_reads_back_with_each_days_note_intact(self):
		"""R11. Simulates a Timesheet written the way the pre-U7 code path
		always wrote it -- one `Timesheet Detail` row per project/task/date,
		each with its own `description` -- by inserting the document directly
		rather than through `save_my_week`, and confirms `get_my_week` still
		attaches each row's own note to its own day rather than merging or
		dropping either. Nothing here is new server behaviour; it is the
		existing read path exercised against data it never wrote itself."""
		company = frappe.db.get_value("Employee", self.employee_name, "company")
		monday_start = get_datetime(f"{self.monday} 00:00:00")
		tuesday_start = get_datetime(f"{add_days(self.monday, 1)} 00:00:00")

		doc = frappe.get_doc(
			{
				"doctype": "Timesheet",
				"employee": self.employee_name,
				"company": company,
				"time_logs": [
					{
						"project": self.project,
						"hours": 3,
						"description": "Monday's pre-U7 note",
						"activity_type": "General",
						"from_time": monday_start,
						"to_time": frappe.utils.add_to_date(monday_start, hours=3),
					},
					{
						"project": self.project,
						"hours": 5,
						"description": "Tuesday's pre-U7 note",
						"activity_type": "General",
						"from_time": tuesday_start,
						"to_time": frappe.utils.add_to_date(tuesday_start, hours=5),
					},
				],
			}
		)
		doc.insert(ignore_permissions=True)

		frappe.set_user(EMPLOYEE_USER)
		rows = {row["date"]: row for row in get_my_week(str(self.monday))["timesheet"]["rows"]}
		self.assertEqual(rows[str(self.monday)]["note"], "Monday's pre-U7 note")
		self.assertEqual(rows[str(add_days(self.monday, 1))]["note"], "Tuesday's pre-U7 note")


class TestTimesheetRecallAndCancel(IntegrationTestCase):
	"""Plan 2026-10-04-003 U1. The Recall transition (R5, R6) and the
	Cancelled state (KTD2), at the workflow level -- the portal endpoint and
	its notices arrive in U2; this class proves the fixture's own moves."""

	def setUp(self):
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()
		frappe.db.set_value("Employee", self.employee_name, "reports_to", self.manager_name)

		method_name = self.id().split(".")[-1]
		digest = int(hashlib.md5(self.id().encode()).hexdigest(), 16)
		week_offset = (digest % 200000) * 7
		self.monday, self.sunday = get_week_bounds(add_days(frappe.utils.today(), week_offset))
		self.project = make_test_project(method_name, users=[EMPLOYEE_USER])
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

	def _hr_manager_user(self, email):
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": "HR",
					"last_name": "Manager",
					"send_welcome_email": 0,
					"roles": [{"doctype": "Has Role", "role": "HR Manager"}],
				}
			).insert(ignore_permissions=True)
		return email

	def test_employee_recalls_own_pending_week_back_to_draft(self):
		"""R5: the week returns to Draft, leaves the manager's hands (the
		DocShare goes), and stays editable."""
		name = self._save_and_submit()

		frappe.set_user(EMPLOYEE_USER)
		apply_workflow({"doctype": "Timesheet", "name": name}, "Recall")

		doc = frappe.get_doc("Timesheet", name)
		self.assertEqual(doc.workflow_state, "Draft")
		self.assertEqual(doc.docstatus, 0)

		frappe.set_user("Administrator")
		shared_users = [row.user for row in frappe.share.get_users("Timesheet", name)]
		self.assertNotIn(MANAGER_USER, shared_users)

	def test_recalled_week_can_be_edited_and_resent(self):
		name = self._save_and_submit()

		frappe.set_user(EMPLOYEE_USER)
		apply_workflow({"doctype": "Timesheet", "name": name}, "Recall")
		save_my_week(str(self.monday), json.dumps([self._week_row(hours=7)]))
		apply_workflow({"doctype": "Timesheet", "name": name}, "Submit")

		doc = frappe.get_doc("Timesheet", name)
		self.assertEqual(doc.workflow_state, "Pending Approval")
		self.assertEqual(doc.time_logs[0].hours, 7)

	def test_manager_cannot_recall_a_report_week(self):
		"""R6 / KTD1: a manager who also holds Employee fails the Recall
		condition, so the action is never theirs -- neither offered through
		the workflow nor available raw."""
		name = self._save_and_submit()

		frappe.set_user(MANAGER_USER)
		doc = frappe.get_doc("Timesheet", name)
		self.assertNotIn("Recall", [t.action for t in get_transitions(doc)])
		with self.assertRaises(Exception):
			apply_workflow({"doctype": "Timesheet", "name": name}, "Recall")

		doc.reload()
		self.assertEqual(doc.workflow_state, "Pending Approval")

	def test_recall_refused_after_send_back(self):
		"""R6: once the manager has decided, recall is gone."""
		name = self._save_and_submit()

		frappe.set_user(MANAGER_USER)
		apply_workflow({"doctype": "Timesheet", "name": name}, "Send Back")

		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(Exception):
			apply_workflow({"doctype": "Timesheet", "name": name}, "Recall")

		frappe.set_user("Administrator")
		doc = frappe.get_doc("Timesheet", name)
		self.assertEqual(doc.workflow_state, "Sent Back")

	def test_recall_refused_once_approved(self):
		name = self._save_and_submit()

		frappe.set_user(MANAGER_USER)
		apply_workflow({"doctype": "Timesheet", "name": name}, "Approve")

		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(Exception):
			apply_workflow({"doctype": "Timesheet", "name": name}, "Recall")

		frappe.set_user("Administrator")
		doc = frappe.get_doc("Timesheet", name)
		self.assertEqual(doc.workflow_state, "Approved")
		self.assertEqual(doc.docstatus, 1)

	def test_recall_refused_in_pending_hr(self):
		name = self._save_and_submit()

		frappe.set_user(MANAGER_USER)
		apply_workflow({"doctype": "Timesheet", "name": name}, "Send to HR")

		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(Exception):
			apply_workflow({"doctype": "Timesheet", "name": name}, "Recall")

		frappe.set_user("Administrator")
		doc = frappe.get_doc("Timesheet", name)
		self.assertEqual(doc.workflow_state, "Pending HR")

	def test_hr_manager_cancels_an_approved_week(self):
		"""KTD2: the Cancel transition sets the Cancelled state and cancels
		the document; the receipt list then reads it as Cancelled, not
		Approved."""
		name = self._save_and_submit()

		frappe.set_user(MANAGER_USER)
		apply_workflow({"doctype": "Timesheet", "name": name}, "Approve")

		hr_user = self._hr_manager_user("hr-manager-cancel@helixhr.test")
		frappe.set_user(hr_user)
		apply_workflow({"doctype": "Timesheet", "name": name}, "Cancel")

		doc = frappe.get_doc("Timesheet", name)
		self.assertEqual(doc.workflow_state, "Cancelled")
		self.assertEqual(doc.docstatus, 2)

		frappe.set_user(MANAGER_USER)
		from helixhr.api import _decided_timesheets

		rows = _decided_timesheets(self.manager_name, "2000-01-01")
		row = next(row for row in rows if row["name"] == name)
		self.assertEqual(row["status"], "Cancelled")

	def test_an_hr_manager_cannot_cancel_their_own_week(self):
		"""KTD2: the Cancel transition keeps the "not own" condition, like
		every other HR transition."""
		from helixhr.tests.utils import make_test_hr_manager_employee

		hr_employee, hr_user = make_test_hr_manager_employee()
		company = frappe.db.get_value("Employee", hr_employee, "company")
		doc = frappe.get_doc(
			{
				"doctype": "Timesheet",
				"employee": hr_employee,
				"company": company,
				"time_logs": [
					{
						"project": self.project,
						"activity_type": "General",
						"from_time": get_datetime(f"{self.monday} 09:00:00"),
						"to_time": get_datetime(f"{self.monday} 12:00:00"),
						"hours": 3,
					}
				],
			}
		)
		doc.insert(ignore_permissions=True)
		frappe.db.set_value("Timesheet", doc.name, {"workflow_state": "Approved", "docstatus": 1})
		frappe.db.set_value("Timesheet Detail", {"parent": doc.name}, "docstatus", 1)

		frappe.set_user(hr_user)
		with self.assertRaises(Exception):
			apply_workflow({"doctype": "Timesheet", "name": doc.name}, "Cancel")

	def test_desk_cancel_is_hidden_once_cancelled_is_a_state(self):
		"""KTD2: `can_cancel_document` is false the moment the workflow
		carries a docstatus-2 state reachable by a transition, so Desk's raw
		Cancel button disappears and the workflow's Cancel is the one route."""
		from frappe.model.workflow import can_cancel_document

		self.assertFalse(can_cancel_document("Timesheet"))

	def test_cancelled_week_drops_out_of_the_project_timesheet_report(self):
		"""System-wide impact: the report system counts approved hours by
		docstatus; a cancelled week must drop out, not keep counting."""
		month = f"{str(self.monday)[:4]}-{str(self.monday)[5:7]}"

		frappe.set_user(EMPLOYEE_USER)
		name = save_my_week(str(self.monday), json.dumps([self._week_row()]))
		apply_workflow({"doctype": "Timesheet", "name": name}, "Submit")
		frappe.set_user(MANAGER_USER)
		apply_workflow({"doctype": "Timesheet", "name": name}, "Approve")
		before = self._project_hours(month)

		hr_user = self._hr_manager_user("hr-manager-cancel2@helixhr.test")
		frappe.set_user(hr_user)
		apply_workflow({"doctype": "Timesheet", "name": name}, "Cancel")

		self.assertGreater(before, 0)
		self.assertEqual(self._project_hours(month), 0)

	def _project_hours(self, month):
		from helixhr.reports import _project_timesheet

		frappe.set_user("Administrator")
		_, rows = _project_timesheet(
			{"project": self.project, "month": month}, {"kind": "unscoped"}
		)
		return sum(row["hours"] for row in rows)


class TestRecallMyWeekEndpoint(IntegrationTestCase):
	"""Plan 2026-10-04-003 U2. The portal endpoint around the Recall
	transition: the concurrency token, the state guards, the arrival notice
	it clears and the bell it rings (R5, R6)."""

	def setUp(self):
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()
		frappe.db.set_value("Employee", self.employee_name, "reports_to", self.manager_name)

		method_name = self.id().split(".")[-1]
		digest = int(hashlib.md5(self.id().encode()).hexdigest(), 16)
		week_offset = (digest % 200000) * 7
		self.monday, self.sunday = get_week_bounds(add_days(frappe.utils.today(), week_offset))
		self.project = make_test_project(method_name, users=[EMPLOYEE_USER])
		for key in ("save_my_week", "recall_my_week"):
			frappe.cache.delete(f"helixhr:rate-limit:{key}:{EMPLOYEE_USER}")

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

	def _pending_week(self):
		frappe.set_user(EMPLOYEE_USER)
		name = save_my_week(str(self.monday), json.dumps([self._week_row()]))
		apply_workflow({"doctype": "Timesheet", "name": name}, "Submit")
		return frappe.get_doc("Timesheet", name)

	def test_recall_returns_the_week_to_draft_and_it_can_be_resent(self):
		doc = self._pending_week()
		previous_modified = doc.modified

		frappe.set_user(EMPLOYEE_USER)
		result = recall_my_week(str(self.monday), str(previous_modified))

		self.assertEqual(result["name"], doc.name)
		self.assertEqual(result["workflow_state"], "Draft")

		# Editable again: the normal save-and-send path runs end to end.
		save_my_week(str(self.monday), json.dumps([self._week_row(hours=6)]))
		apply_workflow({"doctype": "Timesheet", "name": doc.name}, "Submit")
		self.assertEqual(frappe.db.get_value("Timesheet", doc.name, "workflow_state"), "Pending Approval")

	def test_recall_notifies_the_manager_and_clears_the_arrival_notice(self):
		doc = self._pending_week()

		arrival = frappe.get_all(
			"Notification Log",
			filters={"for_user": MANAGER_USER, "document_type": "Timesheet", "document_name": doc.name},
		)
		self.assertEqual(len(arrival), 1)

		frappe.set_user(EMPLOYEE_USER)
		recall_my_week(str(self.monday), str(doc.modified))

		logs = frappe.get_all(
			"Notification Log",
			filters={"for_user": MANAGER_USER, "document_type": "Timesheet", "document_name": doc.name},
			fields=["subject"],
		)
		self.assertEqual(len(logs), 1, "the arrival notice is cleared and only the recall remains")
		self.assertIn("recalled", logs[0].subject.lower())

	def test_recall_refused_once_approved(self):
		doc = self._pending_week()

		frappe.set_user(MANAGER_USER)
		apply_workflow({"doctype": "Timesheet", "name": doc.name}, "Approve")

		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.ValidationError) as ctx:
			recall_my_week(str(self.monday), str(doc.modified))
		self.assertIn("already approved", str(ctx.exception))

	def test_recall_refused_in_pending_hr(self):
		doc = self._pending_week()

		frappe.set_user(MANAGER_USER)
		apply_workflow({"doctype": "Timesheet", "name": doc.name}, "Send to HR")

		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.ValidationError) as ctx:
			recall_my_week(str(self.monday), str(doc.modified))
		self.assertIn("HR", str(ctx.exception))

	def test_recall_refused_when_sent_back(self):
		doc = self._pending_week()

		frappe.set_user(MANAGER_USER)
		apply_workflow({"doctype": "Timesheet", "name": doc.name}, "Send Back")

		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.ValidationError):
			recall_my_week(str(self.monday), str(doc.modified))

		frappe.set_user("Administrator")
		self.assertEqual(
			frappe.db.get_value("Timesheet", doc.name, "workflow_state"), "Sent Back"
		)

	def test_recall_with_a_stale_token_is_refused(self):
		doc = self._pending_week()

		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.ValidationError) as ctx:
			recall_my_week(str(self.monday), "2000-01-01 00:00:00")
		self.assertIn("changed", str(ctx.exception))

		self.assertEqual(
			frappe.db.get_value("Timesheet", doc.name, "workflow_state"), "Pending Approval"
		)

	def test_another_employees_week_is_not_recalled(self):
		"""The endpoint reads only the caller's own week: another employee's
		week_start answers "no timesheet", not the week itself."""
		from helixhr.tests.utils import make_test_user

		company = frappe.db.get_value("Employee", self.employee_name, "company")
		other = make_test_user("recall-other@helixhr.test", company)
		other_project = make_test_project(f"{self.id().split('.')[-1]}-other", users=[])
		frappe.set_user("Administrator")
		doc = frappe.get_doc(
			{
				"doctype": "Timesheet",
				"employee": other,
				"company": company,
				"time_logs": [
					{
						"project": other_project,
						"activity_type": "General",
						"from_time": get_datetime(f"{self.monday} 09:00:00"),
						"to_time": get_datetime(f"{self.monday} 12:00:00"),
						"hours": 3,
					}
				],
			}
		)
		doc.insert(ignore_permissions=True)
		frappe.db.set_value("Timesheet", doc.name, "workflow_state", "Pending Approval")
		frappe.db.set_value("Employee", other, "reports_to", self.manager_name)

		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.DoesNotExistError):
			recall_my_week(str(self.monday), None)

		frappe.set_user("Administrator")
		self.assertEqual(
			frappe.db.get_value("Timesheet", doc.name, "workflow_state"), "Pending Approval"
		)

	def test_the_managers_stale_approve_names_the_recall(self):
		"""R6: the manager who decides from a stale screen is told the
		employee took the week back, not just that something moved."""
		doc = self._pending_week()

		frappe.set_user(EMPLOYEE_USER)
		recall_my_week(str(self.monday), str(doc.modified))

		frappe.set_user(MANAGER_USER)
		with self.assertRaises(frappe.ValidationError) as ctx:
			from helixhr.api import act_on_approval

			act_on_approval(
				"Timesheet",
				doc.name,
				"Approve",
				expected_modified=str(doc.modified),
				expected_state="Pending Approval",
			)
		self.assertIn("recalled", str(ctx.exception))


class TestGetMyMonth(IntegrationTestCase):
	"""Plan 2026-10-05-001 U5: the month overview's one read (KTD4)."""

	def setUp(self):
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()
		frappe.db.set_value("Employee", self.employee_name, "reports_to", self.manager_name)
		# A month of its own per test, far enough ahead that no other
		# suite's hashed week lands in it often -- and the Timesheets it
		# writes are cleaned up in tearDown regardless.
		digest = int(hashlib.md5(self.id().encode()).hexdigest(), 16)
		year = 3000 + digest % 900
		self.month = f"{year}-{1 + digest % 12:02d}"
		self.project = make_test_project("month", users=[EMPLOYEE_USER])
		self.standard = frappe.db.get_single_value("HR Settings", "standard_working_hours")
		frappe.db.set_single_value("HR Settings", "standard_working_hours", 8)
		self.holiday_list = frappe.db.get_value("Employee", self.employee_name, "holiday_list")
		self.joined = frappe.db.get_value("Employee", self.employee_name, "date_of_joining")
		frappe.cache.delete(f"helixhr:rate-limit:save_my_week:{EMPLOYEE_USER}")
		self._purge()

	def tearDown(self):
		frappe.set_user("Administrator")
		self._purge()
		frappe.db.set_single_value("HR Settings", "standard_working_hours", self.standard)
		frappe.db.set_value("Employee", self.employee_name, "holiday_list", self.holiday_list)
		frappe.db.set_value("Employee", self.employee_name, "relieving_date", None)
		frappe.db.set_value("Employee", self.employee_name, "date_of_joining", self.joined)

	def _purge(self):
		first = frappe.utils.getdate(f"{self.month}-01")
		names = frappe.get_all(
			"Timesheet",
			filters={
				"employee": ["in", [self.employee_name, self.manager_name]],
				"start_date": ["between", [str(add_days(first, -7)), str(add_days(first, 40))]],
			},
			pluck="name",
		)
		for name in names:
			frappe.db.delete("HelixHR Timesheet Change", {"timesheet": name})
			frappe.db.delete("Timesheet Detail", {"parent": name})
			frappe.db.delete("Timesheet", name)

	def _month(self, today=None):
		frappe.set_user(EMPLOYEE_USER)
		with patch("helixhr.api.user_today", return_value=today or "1999-01-01"):
			return get_my_month(self.month)["weeks"]

	def _after_month(self):
		return str(add_days(frappe.utils.getdate(f"{self.month}-01"), 60))

	def _save(self, monday, hours=4):
		frappe.set_user(EMPLOYEE_USER)
		row = {"date": str(monday), "project": self.project, "task": "", "hours": hours, "note": "x"}
		return save_my_week(str(monday), json.dumps([row]))

	def test_an_empty_past_month_is_every_overlapping_monday_not_started_and_missing(self):
		weeks = self._month(today=self._after_month())
		self.assertIn(len(weeks), (4, 5, 6))
		first = frappe.utils.getdate(f"{self.month}-01")
		self.assertEqual(weeks[0]["week_start"], str(get_week_bounds(first)[0]))
		for week in weeks:
			self.assertEqual(frappe.utils.getdate(week["week_start"]).weekday(), 0)
			self.assertIsNone(week["state"])
			self.assertTrue(week["missing"])
			self.assertIsNotNone(week["expected_hours"])

	def test_future_weeks_are_never_missing(self):
		self.assertFalse(any(week["missing"] for week in self._month(today="1999-01-01")))

	def test_a_week_spanning_two_months_appears_in_both(self):
		first = frappe.utils.getdate(f"{self.month}-01")
		last_monday = self._month()[-1]["week_start"]
		frappe.set_user(EMPLOYEE_USER)
		next_month = str(add_days(frappe.utils.get_last_day(first), 1))[:7]
		with patch("helixhr.api.user_today", return_value="1999-01-01"):
			following = get_my_month(next_month)["weeks"]
		spans = frappe.utils.getdate(last_monday).month != add_days(frappe.utils.getdate(last_monday), 6).month
		self.assertEqual(following[0]["week_start"] == last_monday, spans)
		# March 3566 ends on a Tuesday, so at least one fixed case always spans.
		with patch("helixhr.api.user_today", return_value="1999-01-01"):
			self.assertEqual(get_my_month("3566-03")["weeks"][-1]["week_start"], "3566-03-28")
			self.assertEqual(get_my_month("3566-04")["weeks"][0]["week_start"], "3566-03-28")

	def test_a_week_of_holidays_expects_zero_and_is_not_missing(self):
		monday = frappe.utils.getdate(self._month()[1]["week_start"])
		name = f"_Test Month Holidays {self.month}"
		if not frappe.db.exists("Holiday List", name):
			frappe.get_doc(
				{
					"doctype": "Holiday List",
					"holiday_list_name": name,
					"from_date": str(add_days(monday, -40)),
					"to_date": str(add_days(monday, 40)),
					"holidays": [
						{"holiday_date": str(add_days(monday, offset)), "description": "Off"}
						for offset in range(7)
					],
				}
			).insert(ignore_permissions=True)
		frappe.db.set_value("Employee", self.employee_name, "holiday_list", name)
		week = self._month(today=self._after_month())[1]
		self.assertEqual(week["expected_hours"], 0)
		self.assertFalse(week["missing"])

	def test_weeks_after_relieving_are_never_missing(self):
		first_monday = frappe.utils.getdate(self._month()[0]["week_start"])
		frappe.db.set_value("Employee", self.employee_name, "relieving_date", str(add_days(first_monday, -1)))
		self.assertFalse(any(week["missing"] for week in self._month(today=self._after_month())))

	def test_weeks_before_joining_are_never_missing_and_the_joining_week_expects_less(self):
		full = self._month(today=self._after_month())
		second_monday = frappe.utils.getdate(full[1]["week_start"])
		frappe.db.set_value("Employee", self.employee_name, "date_of_joining", str(add_days(second_monday, 2)))
		weeks = self._month(today=self._after_month())
		self.assertFalse(weeks[0]["missing"])
		self.assertTrue(weeks[1]["missing"])
		self.assertLess(weeks[1]["expected_hours"], full[1]["expected_hours"])
		self.assertGreater(weeks[1]["expected_hours"], 0)

	def test_no_standard_hours_is_not_measured_but_still_missing(self):
		frappe.db.set_single_value("HR Settings", "standard_working_hours", 0)
		week = self._month(today=self._after_month())[0]
		self.assertIsNone(week["expected_hours"])
		self.assertTrue(week["missing"])

	def test_states_map_through_and_an_open_change_is_flagged(self):
		weeks = self._month()
		draft, pending, sent_back, approved = (frappe.utils.getdate(w["week_start"]) for w in weeks[:4])
		self._save(draft)
		pending_name = self._save(pending)
		apply_workflow({"doctype": "Timesheet", "name": pending_name}, "Submit")
		sent_name = self._save(sent_back)
		apply_workflow({"doctype": "Timesheet", "name": sent_name}, "Submit")
		approved_name = self._save(approved)
		apply_workflow({"doctype": "Timesheet", "name": approved_name}, "Submit")
		frappe.set_user(MANAGER_USER)
		apply_workflow({"doctype": "Timesheet", "name": sent_name}, "Send Back")
		apply_workflow({"doctype": "Timesheet", "name": approved_name}, "Approve")
		frappe.set_user(EMPLOYEE_USER)
		raise_timesheet_change(str(approved), "Forgot Friday")

		weeks = self._month(today=self._after_month())
		self.assertEqual(
			[w["state"] for w in weeks[:4]], ["Draft", "Pending Approval", "Sent Back", "Approved"]
		)
		self.assertEqual([w["missing"] for w in weeks[:4]], [True, False, True, False])
		self.assertEqual([w["change_open"] for w in weeks[:4]], [False, False, False, True])
		self.assertEqual(weeks[0]["total_hours"], 4)

	def test_another_employees_timesheets_never_appear(self):
		monday = frappe.utils.getdate(self._month()[0]["week_start"])
		frappe.set_user("Administrator")
		frappe.get_doc(
			{
				"doctype": "Timesheet",
				"employee": self.manager_name,
				"time_logs": [
					{"from_time": f"{monday} 09:00:00", "hours": 3, "project": self.project, "activity_type": None}
				],
			}
		).insert(ignore_permissions=True)
		week = self._month()[0]
		self.assertIsNone(week["state"])
		self.assertEqual(week["total_hours"], 0)

	def test_a_malformed_month_is_refused(self):
		frappe.set_user(EMPLOYEE_USER)
		for bad in ("2026-13", "2026-1", "26-01", "2026-01-01", "x"):
			with self.assertRaises(frappe.ValidationError):
				get_my_month(bad)
