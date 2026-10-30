"""Plan 2026-10-31-001 U4 (R8, R9, KTD6): "Open to everyone".

A flagged, Open project is bookable by every Active employee whose current
company is the project's company, without a Project User row. The rule lives
in `_my_project_names`, so the task picker, week reads and save/submit
validation agree on it. The flag widens booking only: no Project User row,
User Permission or DocShare is ever written for it, and Frappe's REST route
to the Project stays refused.

Tests own their rows (no rollback between methods on this bench): every
identity and project here is this file's own, and each test sets the flag
it relies on rather than inheriting one.
"""

import hashlib
from contextlib import contextmanager

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days

from helixhr.tests.utils import (
	ensure_baseline_company,
	ensure_test_company,
	make_test_delivery_manager,
	make_test_employee_and_manager,
	make_test_project,
	make_test_user,
)
from helixhr.utils import RATE_LIMIT_POLICY, get_week_bounds

WORKER = "open-all-worker@helixhr.test"
OUTSIDER = "open-all-outsider@helixhr.test"
NEW_HIRE = "open-all-new-hire@helixhr.test"


@contextmanager
def _strict_user_permissions(value):
	"""Set System Settings' strict flag for the block, restoring what the
	site had. `set_single_value` clears the per-request settings cache."""
	before = frappe.db.get_single_value("System Settings", "apply_strict_user_permissions")
	frappe.db.set_single_value("System Settings", "apply_strict_user_permissions", value)
	try:
		yield
	finally:
		frappe.db.set_single_value("System Settings", "apply_strict_user_permissions", before)


class TestProjectOpenToAll(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.other_company = ensure_baseline_company()
		_, _, self.manager, _ = make_test_employee_and_manager()
		make_test_user(WORKER, self.company, reports_to=self.manager)
		make_test_user(OUTSIDER, self.other_company)
		_, self.dm_user = make_test_delivery_manager()

		self.open_project = self._project("_Test U4 Open To All", open_to_all=1)
		self.open_task = self._task(self.open_project, "_Test U4 Open Task")
		self.other_company_open = make_test_project(self.other_company, "_Test U4 Open Elsewhere")
		frappe.db.set_value("Project", self.other_company_open, {"helixhr_open_to_all": 1, "status": "Open"})

		# One week per test id, far from any other suite's (test_api_timesheet's
		# own scheme), and a fresh save bucket for the booking identities.
		digest = int(hashlib.md5(self.id().encode()).hexdigest(), 16)
		self.monday, _ = get_week_bounds(add_days(frappe.utils.today(), (digest % 200000) * 7))
		for user in (WORKER, OUTSIDER, NEW_HIRE):
			frappe.cache.delete(f"helixhr:rate-limit:save_my_week:{user}")

	def tearDown(self):
		frappe.set_user("Administrator")
		# Leave no flagged project behind for another suite's employees.
		frappe.db.set_value(
			"Project",
			{"name": ["in", [self.open_project, self.other_company_open]]},
			"helixhr_open_to_all",
			0,
		)

	def _project(self, name, open_to_all=0, status="Open", company=None):
		project = make_test_project(company or self.company, name)
		frappe.db.set_value("Project", project, {"helixhr_open_to_all": open_to_all, "status": status})
		return project

	def _task(self, project, subject):
		existing = frappe.db.get_value("Task", {"project": project, "subject": subject})
		if existing:
			return existing
		return (
			frappe.get_doc({"doctype": "Task", "project": project, "subject": subject, "status": "Open"})
			.insert(ignore_permissions=True)
			.name
		)

	def _rows(self, project, task=None):
		return [{"date": str(self.monday), "project": project, "task": task or "", "hours": 2}]

	def _save(self, user, project, task=None):
		from helixhr.api import save_my_week

		frappe.set_user(user)
		try:
			return save_my_week(str(self.monday), self._rows(project, task))
		finally:
			frappe.set_user("Administrator")

	def _submit(self, user, project, task=None):
		from helixhr.api import get_my_week, submit_my_week

		frappe.set_user(user)
		try:
			week = get_my_week(str(self.monday))["timesheet"]
			return submit_my_week(
				str(self.monday), self._rows(project, task), expected_modified=week and week["modified"]
			)
		finally:
			frappe.set_user("Administrator")

	def _bookable(self, user):
		from helixhr.api import get_my_projects

		frappe.set_user(user)
		try:
			return {row["name"]: row for row in get_my_projects()}
		finally:
			frappe.set_user("Administrator")

	def _clear_week(self, user):
		"""This test's week starts empty for ``user`` on every run."""
		employee = frappe.db.get_value("Employee", {"user_id": user, "status": "Active"})
		for name in frappe.get_all(
			"Timesheet", filters={"employee": employee, "start_date": [">=", self.monday]}, pluck="name"
		):
			doc = frappe.get_doc("Timesheet", name)
			if doc.docstatus == 1:
				doc.flags.ignore_permissions = True
				doc.cancel()
			frappe.delete_doc("Timesheet", name, ignore_permissions=True, force=True)

	# --- Execution note: User Permission on another Project -----------------

	def _assert_books_open_project_despite_other_project_permission(self):
		other = make_test_project(self.company, "_Test U4 Worker Own Project")
		if not frappe.db.exists("User Permission", {"user": WORKER, "allow": "Project", "for_value": other}):
			frappe.get_doc(
				{"doctype": "User Permission", "user": WORKER, "allow": "Project", "for_value": other}
			).insert(ignore_permissions=True)
		self._clear_week(WORKER)

		self.assertIn(self.open_project, self._bookable(WORKER))
		self._save(WORKER, self.open_project, self.open_task)
		result = self._submit(WORKER, self.open_project, self.open_task)
		self.assertEqual(result["workflow_state"], "Pending Approval")
		self.assertEqual(
			frappe.get_all("Timesheet Detail", filters={"parent": result["name"]}, pluck="project"),
			[self.open_project],
		)

	def test_a_user_permission_on_another_project_does_not_block_booking_an_open_one(self):
		"""Frappe refuses a `Timesheet Detail.project` outside the user's
		allowed Project set whenever *any* User Permission on Project
		exists; the Property Setters on `Timesheet Detail.project`/`.task`
		take that check out, and `_validate_rows` stays the allow-list."""
		with _strict_user_permissions(0):
			self._assert_books_open_project_despite_other_project_permission()

	def test_strict_user_permissions_do_not_block_booking_an_open_one(self):
		with _strict_user_permissions(1):
			self.assertEqual(
				frappe.utils.cint(frappe.get_system_settings("apply_strict_user_permissions")), 1
			)
			self._assert_books_open_project_despite_other_project_permission()

	# --- The booking rule ----------------------------------------------------

	def test_a_non_member_in_the_projects_company_sees_it_and_books_it(self):
		self._clear_week(OUTSIDER)
		self._clear_week(WORKER)
		self.assertFalse(frappe.db.exists("Project User", {"parent": self.open_project, "user": WORKER}))
		bookable = self._bookable(WORKER)
		self.assertIn(self.open_project, bookable)
		self.assertIn(self.open_task, {task["name"] for task in bookable[self.open_project]["tasks"]})

		name = self._save(WORKER, self.open_project, self.open_task)
		self.assertTrue(name)

	def test_week_read_names_the_open_projects_task(self):
		from helixhr.api import get_my_week

		self._clear_week(WORKER)
		self._save(WORKER, self.open_project, self.open_task)
		frappe.set_user(WORKER)
		rows = get_my_week(str(self.monday))["timesheet"]["rows"]
		self.assertEqual([row["task_subject"] for row in rows], ["_Test U4 Open Task"])

	def test_an_employee_of_another_company_cannot_see_or_book_it(self):
		self.assertNotIn(self.open_project, self._bookable(OUTSIDER))
		self._clear_week(OUTSIDER)
		with self.assertRaises(frappe.ValidationError) as caught:
			self._save(OUTSIDER, self.open_project)
		self.assertIn(f"You can't book time on {self.open_project}", str(caught.exception))
		# The other company's own flagged project is theirs to book.
		self.assertIn(self.other_company_open, self._bookable(OUTSIDER))
		self.assertNotIn(self.other_company_open, self._bookable(WORKER))

	def test_a_flagged_project_that_is_not_open_is_not_bookable(self):
		done = self._project("_Test U4 Open But Completed", open_to_all=1, status="Completed")
		try:
			self.assertNotIn(done, self._bookable(WORKER))
			self._clear_week(WORKER)
			with self.assertRaises(frappe.ValidationError):
				self._save(WORKER, done)
		finally:
			frappe.db.set_value("Project", done, "helixhr_open_to_all", 0)

	def test_turning_the_flag_off_refuses_a_draft_that_still_names_it(self):
		self._clear_week(WORKER)
		self._save(WORKER, self.open_project)
		frappe.db.set_value("Project", self.open_project, "helixhr_open_to_all", 0)
		self.assertNotIn(self.open_project, self._bookable(WORKER))
		with self.assertRaises(frappe.ValidationError) as caught:
			self._save(WORKER, self.open_project)
		self.assertIn(f"You can't book time on {self.open_project}", str(caught.exception))

	def test_a_new_hire_can_book_immediately(self):
		if frappe.db.exists("Employee", {"user_id": NEW_HIRE}):
			for name in frappe.get_all("Employee", filters={"user_id": NEW_HIRE}, pluck="name"):
				frappe.db.set_value("Employee", name, "status", "Left")
				frappe.db.set_value("Employee", name, "user_id", None)
		make_test_user(NEW_HIRE, self.company, reports_to=self.manager)
		self.assertIn(self.open_project, self._bookable(NEW_HIRE))
		self._clear_week(NEW_HIRE)
		self.assertTrue(self._save(NEW_HIRE, self.open_project, self.open_task))

	def test_a_left_employee_in_the_company_cannot_book_it(self):
		"""Only the user's *current Active* Employee row decides."""
		from helixhr.api import _my_project_names

		left = "open-all-left@helixhr.test"
		make_test_user(left, self.company)
		frappe.db.set_value("Employee", {"user_id": left}, "status", "Left")
		self.assertNotIn(self.open_project, _my_project_names(left))

	# --- Access stays where it was -------------------------------------------

	def test_the_rest_route_still_refuses_a_non_member(self):
		from frappe.client import get

		frappe.set_user(WORKER)
		with self.assertRaises(frappe.PermissionError):
			get("Project", self.open_project)
		with self.assertRaises(frappe.PermissionError):
			get("Task", self.open_task)

	def test_the_flag_writes_no_member_permission_or_share_rows(self):
		from helixhr.api import set_project_open_to_all

		project = self._project("_Test U4 Toggle No Rows")
		before = self._access_rows(project)
		frappe.set_user(self.dm_user)
		set_project_open_to_all(project, 1)
		frappe.set_user("Administrator")
		self.assertEqual(self._access_rows(project), before)
		self.assertIn(project, self._bookable(WORKER))
		frappe.db.set_value("Project", project, "helixhr_open_to_all", 0)

	def _access_rows(self, project):
		return (
			frappe.db.count("Project User", {"parent": project}),
			frappe.db.count("User Permission", {"allow": "Project", "for_value": project}),
			frappe.db.count("DocShare", {"share_doctype": "Project", "share_name": project}),
		)

	# --- set_project_open_to_all ----------------------------------------------

	def test_an_in_scope_editor_toggles_and_reads_the_flag_back(self):
		from helixhr.api import get_project, search_projects, set_project_open_to_all

		project = self._project("_Test U4 Toggle Read Back")
		frappe.set_user(self.dm_user)
		self.assertEqual(set_project_open_to_all(project, 1), {"open_to_all": True})
		self.assertTrue(get_project(project)["open_to_all"])
		rows = {row["name"]: row for row in search_projects()["projects"]}
		self.assertTrue(rows[project]["open_to_all"])
		self.assertEqual(set_project_open_to_all(project, 0), {"open_to_all": False})
		self.assertFalse(get_project(project)["open_to_all"])

	def test_a_delivery_manager_outside_scope_cannot_toggle(self):
		from helixhr.api import _PROJECT_NOT_FOUND, set_project_open_to_all

		elsewhere = self._project("_Test U4 Toggle Elsewhere", company=self.other_company)
		frappe.set_user(self.dm_user)
		with self.assertRaises(frappe.PermissionError) as caught:
			set_project_open_to_all(elsewhere, 1)
		self.assertEqual(str(caught.exception), _PROJECT_NOT_FOUND)
		frappe.set_user("Administrator")
		self.assertFalse(frappe.db.get_value("Project", elsewhere, "helixhr_open_to_all"))

	def test_a_plain_employee_cannot_toggle(self):
		from helixhr.api import set_project_open_to_all

		frappe.set_user(WORKER)
		with self.assertRaises(frappe.PermissionError):
			set_project_open_to_all(self.open_project, 0)

	def test_toggle_is_post_only_and_rate_limited(self):
		from helixhr import api

		self.assertEqual(
			frappe.allowed_http_methods_for_whitelisted_func[api.set_project_open_to_all], ["POST"]
		)
		self.assertIn("set_project_open_to_all", RATE_LIMIT_POLICY)
