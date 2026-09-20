"""P7-U1 / P7-U2: the HelixHR Delivery Manager role, its Project/Task permission
hooks, and the project scope helper they both call.

This file grows through the rest of the plan (creating projects and tasks,
reading them, the billable-hours report) -- it is organised by unit so a
later addition finds its section rather than being appended at the bottom.
"""

import frappe
from frappe.tests import IntegrationTestCase

from helixhr.tests.utils import (
	TEST_COMPANY,
	ensure_baseline_company,
	ensure_hr_manager_user,
	ensure_test_company,
	make_test_delivery_manager,
	make_test_employee_and_manager,
	make_test_hr_manager_employee,
	make_test_project,
	make_test_user,
)
from helixhr.utils import RATE_LIMIT_POLICY, project_scope_filters, resolve_project_scope

# --- U2: resolve_project_scope -----------------------------------------------


class TestResolveProjectScope(IntegrationTestCase):
	"""The scope table, as tests, per the plan's Execution note for U2 --
	written before `project_permissions.py`'s hooks so the hooks have a
	settled rule to call rather than one derived alongside them."""

	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_system_manager_is_unscoped(self):
		self.assertEqual(resolve_project_scope("Administrator")["kind"], "unscoped")

	def test_an_active_hr_manager_resolves_to_their_own_company(self):
		_, hr_user = make_test_hr_manager_employee()
		scope = resolve_project_scope(hr_user)
		self.assertEqual(scope, {"kind": "company", "company": TEST_COMPANY})

	def test_an_hr_manager_whose_employee_is_left_resolves_to_none(self):
		company = ensure_test_company()
		user = "left-hr-manager-projects@helixhr.test"
		make_test_user(user, company, create_user_permission=0, status="Left", relieving_date="2026-01-31")
		doc = frappe.get_doc("User", user)
		if "HR Manager" not in [row.role for row in doc.roles]:
			doc.append_roles("HR Manager")
			doc.save(ignore_permissions=True)
			frappe.clear_cache(user=user)
		self.assertEqual(resolve_project_scope(user), {"kind": "none"})

	def test_an_hr_role_holder_with_no_employee_record_resolves_unscoped(self):
		user = ensure_hr_manager_user()
		self.assertEqual(resolve_project_scope(user), {"kind": "unscoped", "company": None})

	def test_a_delivery_manager_resolves_to_exactly_their_member_projects(self):
		company = ensure_test_company()
		_, dm_user = make_test_delivery_manager()
		member_project = make_test_project(company, "_Test DM Member Project", members=[dm_user])
		other_project = make_test_project(company, "_Test DM Other Project")

		scope = resolve_project_scope(dm_user)
		self.assertEqual(scope, {"kind": "assigned", "user": dm_user})

		filters = project_scope_filters(scope)
		names = {p.name for p in frappe.get_all("Project", filters=filters)}
		self.assertIn(member_project, names)
		self.assertNotIn(other_project, names)

	def test_a_delivery_manager_with_zero_memberships_resolves_to_an_empty_set_without_an_empty_in(self):
		company = ensure_test_company()
		_, dm_user = make_test_delivery_manager()
		# `IntegrationTestCase` rolls back once per class, not per method, so
		# an earlier test method in this same class may have already made
		# this identity a member of something -- clear it so this test's
		# "zero memberships" premise holds regardless of run order.
		for row_name in frappe.get_all("Project User", filters={"user": dm_user}, pluck="name"):
			frappe.delete_doc("Project User", row_name, ignore_permissions=True, force=True)
		make_test_project(company, "_Test DM Zero Membership Project")

		scope = resolve_project_scope(dm_user)
		self.assertEqual(scope, {"kind": "assigned", "user": dm_user})

		filters = project_scope_filters(scope)
		self.assertIsNone(filters, "an empty membership list must skip the query, not filter with in ()")

	def test_a_plain_employee_resolves_to_none(self):
		_, employee_user, _, _ = make_test_employee_and_manager()
		self.assertEqual(resolve_project_scope(employee_user), {"kind": "none"})

	def test_a_delivery_manager_whose_employee_is_not_active_resolves_to_none(self):
		ensure_test_company()
		_, dm_user = make_test_delivery_manager()
		employee = frappe.get_doc("Employee", {"user_id": dm_user})
		employee.status = "Left"
		employee.relieving_date = "2026-01-31"
		employee.save(ignore_permissions=True)
		try:
			self.assertEqual(resolve_project_scope(dm_user), {"kind": "none"})
		finally:
			employee.reload()
			employee.status = "Active"
			employee.relieving_date = None
			employee.save(ignore_permissions=True)


# --- U1: the REST-route acceptance criterion ---------------------------------


class TestDeliveryManagerRestRouteScope(IntegrationTestCase):
	"""KTD8 / the plan's own words: "the REST-route tests are the unit's
	real acceptance criterion -- the portal methods are not." Every case
	here goes through `frappe.client`, the generic route Frappe itself
	exposes as `/api/resource/...`, never through a HelixHR whitelisted
	method."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		_, self.dm_user = make_test_delivery_manager()
		self.member_project = make_test_project(
			self.company, "_Test REST Member Project", members=[self.dm_user]
		)
		self.other_project = make_test_project(self.company, "_Test REST Other Project")
		self.member_task = self._make_task(self.member_project, "_Test REST Member Task")
		self.other_task = self._make_task(self.other_project, "_Test REST Other Task")

	def tearDown(self):
		frappe.set_user("Administrator")

	def _make_task(self, project, subject):
		existing = frappe.db.get_value("Task", {"project": project, "subject": subject})
		if existing:
			return existing
		return frappe.get_doc(
			{"doctype": "Task", "project": project, "subject": subject}
		).insert(ignore_permissions=True).name

	def test_listing_project_shows_only_member_projects(self):
		from frappe.client import get_list

		frappe.set_user(self.dm_user)
		names = {row["name"] for row in get_list("Project", filters={})}
		self.assertIn(self.member_project, names)
		self.assertNotIn(self.other_project, names)

	def test_reading_a_non_member_project_is_refused(self):
		from frappe.client import get

		frappe.set_user(self.dm_user)
		get("Project", self.member_project)
		with self.assertRaises(frappe.PermissionError):
			get("Project", self.other_project)

	def test_task_listing_and_reading_is_confined_to_member_project_tasks(self):
		from frappe.client import get, get_list

		frappe.set_user(self.dm_user)
		names = {row["name"] for row in get_list("Task", filters={})}
		self.assertIn(self.member_task, names)
		self.assertNotIn(self.other_task, names)

		get("Task", self.member_task)
		with self.assertRaises(frappe.PermissionError):
			get("Task", self.other_task)

	def test_writing_to_non_member_project_or_task_is_refused(self):
		from frappe.client import set_value

		frappe.set_user(self.dm_user)
		set_value("Project", self.member_project, "status", "On hold")
		with self.assertRaises(frappe.PermissionError):
			set_value("Project", self.other_project, "status", "On hold")
		with self.assertRaises(frappe.PermissionError):
			set_value("Task", self.other_task, "status", "Cancelled")

	def test_a_delivery_manager_calling_the_report_endpoint_for_timesheet_billing_summary_is_refused(self):
		from frappe.desk.query_report import run

		frappe.set_user(self.dm_user)
		with self.assertRaises(frappe.PermissionError):
			run("Timesheet Billing Summary")

	def test_a_delivery_manager_is_a_website_user_and_cannot_open_desk(self):
		from helixhr.api import _can_open_desk

		self.assertEqual(frappe.db.get_value("User", self.dm_user, "user_type"), "Website User")
		self.assertFalse(_can_open_desk(self.dm_user))


# --- U3: search_projects / get_project ---------------------------------------


class TestSearchAndGetProject(IntegrationTestCase):
	"""P7-U3. `search_projects` and `get_project`, the read half of R1-R4 --
	scoped by `resolve_project_scope` exactly like the REST routes above, but
	reached through HelixHR's own whitelisted methods rather than the
	generic `frappe.client` routes."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.other_company = ensure_baseline_company()
		_, self.dm_user = make_test_delivery_manager()
		_, self.hr_user = make_test_hr_manager_employee()
		_, self.employee_user, _, _ = make_test_employee_and_manager()

		self.member_project = make_test_project(
			self.company, "_Test U3 Member Project", members=[self.dm_user]
		)
		self.other_project = make_test_project(self.company, "_Test U3 Other Project")
		self.other_company_project = make_test_project(self.other_company, "_Test U3 Other Company Project")
		self.empty_project = make_test_project(self.company, "_Test U3 Empty Project")

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_a_delivery_manager_lists_exactly_their_member_projects(self):
		from helixhr.api import search_projects

		frappe.set_user(self.dm_user)
		names = {row["name"] for row in search_projects()["projects"]}
		self.assertEqual(names, {self.member_project})

	def test_an_hr_manager_lists_projects_in_their_own_company_only(self):
		from helixhr.api import search_projects

		frappe.set_user(self.hr_user)
		names = {row["name"] for row in search_projects()["projects"]}
		self.assertIn(self.member_project, names)
		self.assertIn(self.other_project, names)
		self.assertIn(self.empty_project, names)
		self.assertNotIn(self.other_company_project, names)

	def test_a_plain_employee_is_refused_by_both_methods(self):
		from helixhr.api import get_project, search_projects

		frappe.set_user(self.employee_user)
		with self.assertRaises(frappe.PermissionError):
			search_projects()
		with self.assertRaises(frappe.PermissionError):
			get_project(self.member_project)

	def test_opening_a_project_outside_scope_matches_the_missing_project_error(self):
		from helixhr.api import get_project

		frappe.set_user(self.dm_user)
		with self.assertRaises(frappe.PermissionError) as outside_scope:
			get_project(self.other_project)
		with self.assertRaises(frappe.PermissionError) as missing:
			get_project("_Test U3 Project That Does Not Exist")
		self.assertEqual(str(outside_scope.exception), str(missing.exception))

	def test_the_refusal_names_no_project_no_customer_no_person(self):
		from helixhr.api import get_project

		frappe.set_user(self.dm_user)
		with self.assertRaises(frappe.PermissionError) as caught:
			get_project(self.other_project)
		message = str(caught.exception)
		for leak in (self.other_project, "_Test U3 Other Project", self.company, self.dm_user):
			self.assertNotIn(leak, message)

	def test_get_project_returns_members_as_employee_names_never_logins(self):
		from helixhr.api import get_project

		frappe.set_user(self.dm_user)
		result = get_project(self.member_project)
		dm_employee_name = frappe.db.get_value("Employee", {"user_id": self.dm_user}, "employee_name")
		names = [member["employee_name"] for member in result["members"]]
		self.assertEqual(names, [dm_employee_name])
		for member in result["members"]:
			self.assertNotEqual(member["employee_name"], self.dm_user)

	def test_get_project_excludes_every_costing_and_link_field(self):
		from helixhr.api import get_project

		frappe.set_user(self.dm_user)
		result = get_project(self.member_project)
		expected_keys = {
			"name",
			"project_name",
			"status",
			"billable",
			"expected_start_date",
			"expected_end_date",
			"tasks",
			"members",
		}
		self.assertEqual(set(result.keys()), expected_keys)
		for forbidden in (
			"estimated_costing",
			"total_costing_amount",
			"total_billable_amount",
			"total_billed_amount",
			"total_sales_amount",
			"gross_margin",
			"customer",
			"sales_order",
		):
			self.assertNotIn(forbidden, result)

	def test_a_new_project_field_does_not_appear_in_the_response(self):
		from helixhr.api import get_project

		fieldname = "custom_helixhr_u3_leak_probe"
		custom_field = frappe.get_doc(
			{
				"doctype": "Custom Field",
				"dt": "Project",
				"fieldname": fieldname,
				"label": "HelixHR U3 Leak Probe",
				"fieldtype": "Data",
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value("Project", self.member_project, fieldname, "leaked-value")
		try:
			frappe.set_user(self.dm_user)
			result = get_project(self.member_project)
			self.assertNotIn(fieldname, result)
			self.assertNotIn("leaked-value", result.values())
		finally:
			frappe.set_user("Administrator")
			frappe.delete_doc("Custom Field", custom_field.name, ignore_permissions=True, force=True)

	def test_get_project_with_no_tasks_and_no_members_returns_empty_collections(self):
		from helixhr.api import get_project

		frappe.set_user(self.hr_user)
		result = get_project(self.empty_project)
		self.assertEqual(result["tasks"], [])
		self.assertEqual(result["members"], [])

	def test_a_member_with_no_linked_employee_is_still_reported(self):
		from helixhr.api import get_project
		from helixhr.tests.utils import make_test_user_without_employee

		orphan_user = make_test_user_without_employee()
		project = make_test_project(self.company, "_Test U3 Orphan Member Project", members=[orphan_user])

		frappe.set_user(self.hr_user)
		result = get_project(project)
		self.assertEqual(len(result["members"]), 1)
		member = result["members"][0]
		self.assertIsNone(member["employee"])
		self.assertNotEqual(member["employee_name"], orphan_user)
		self.assertTrue(member["employee_name"])

	def test_both_methods_appear_in_the_rate_limit_policy(self):
		self.assertIn("search_projects", RATE_LIMIT_POLICY)
		self.assertIn("get_project", RATE_LIMIT_POLICY)


# --- U4: create_project / save_task / set_project_members --------------------


def _make_employee_without_user(company, employee_number):
	"""An Employee with no `user_id` at all -- the case `set_project_members`
	must refuse the whole call over, naming this employee, rather than
	silently dropping them from the membership it does write.

	Deliberately built here rather than in `helixhr/tests/utils.py`: this
	unit's scope is this test file plus `api.py`/`utils.py`/the fixture, not
	the shared test-fixture module."""
	from helixhr.tests.utils import ensure_test_gender

	existing = frappe.db.get_value("Employee", {"employee_number": employee_number})
	if existing:
		return existing
	employee = frappe.get_doc(
		{
			"doctype": "Employee",
			"employee_number": employee_number,
			"first_name": employee_number,
			"company": company,
			"date_of_birth": "1990-01-01",
			"date_of_joining": "2020-01-01",
			"gender": ensure_test_gender(),
			"status": "Active",
		}
	)
	employee.insert(ignore_permissions=True)
	return employee.name


class TestCreateProject(IntegrationTestCase):
	"""P7-U4. `create_project` -- the company comes from the caller, never
	the request, and the creator must not immediately fall out of their own
	`resolve_project_scope` (KTD... / the plan's own named risk for this
	unit)."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.other_company = ensure_baseline_company()
		self.dm_employee, self.dm_user = make_test_delivery_manager()
		_, self.employee_user, _, _ = make_test_employee_and_manager()

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_a_delivery_manager_creates_a_project_and_is_immediately_a_member(self):
		from helixhr.api import create_project, search_projects

		frappe.set_user(self.dm_user)
		result = create_project(project_name="_Test U4 New Project", is_billable=1)

		self.assertTrue(result["billable"])
		dm_employee_name = frappe.db.get_value("Employee", self.dm_employee, "employee_name")
		self.assertEqual([m["employee_name"] for m in result["members"]], [dm_employee_name])

		names = {row["name"] for row in search_projects()["projects"]}
		self.assertIn(result["name"], names)

	def test_created_project_takes_the_callers_own_company_a_request_company_is_ignored(self):
		from helixhr.api import create_project

		frappe.set_user(self.dm_user)
		result = create_project(project_name="_Test U4 Company Ignored", company=self.other_company)
		self.assertEqual(frappe.db.get_value("Project", result["name"], "company"), self.company)

	def test_a_plain_employee_is_refused_on_all_three_methods(self):
		from helixhr.api import create_project, save_task, set_project_members

		project = make_test_project(self.company, "_Test U4 Refusal Target Project")

		frappe.set_user(self.employee_user)
		with self.assertRaises(frappe.PermissionError):
			create_project(project_name="_Test U4 Refused Project")
		with self.assertRaises(frappe.PermissionError):
			save_task(project, subject="_Test U4 Refused Task")
		with self.assertRaises(frappe.PermissionError):
			set_project_members(project, [])

	def test_all_three_methods_are_post_only(self):
		from helixhr import api

		for fn in (api.create_project, api.save_task, api.set_project_members):
			self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func[fn], ["POST"])

	def test_all_three_methods_appear_in_the_rate_limit_policy(self):
		for action in ("create_project", "save_task", "set_project_members"):
			self.assertIn(action, RATE_LIMIT_POLICY)

	def test_an_unscoped_caller_with_no_employee_record_is_refused_clearly(self):
		"""`resolve_project_scope` deliberately resolves an HR-role holder
		with no Employee record at all to "unscoped" (the Desk-only persona
		`ensure_hr_manager_user` builds). `create_project` must not crash with
		an unhandled `AttributeError` from `get_current_employee` for that
		caller -- `Project.company` is mandatory and there is no "own record"
		to take one from, so the call is refused with a clear message
		instead of guessing a company or propagating a crash."""
		from helixhr.api import create_project

		hr_user_no_employee = ensure_hr_manager_user()
		self.assertEqual(resolve_project_scope(hr_user_no_employee)["kind"], "unscoped")

		frappe.set_user(hr_user_no_employee)
		with self.assertRaises(frappe.ValidationError):
			create_project(project_name="_Test U4 Unscoped No Employee Project")


class TestSaveTaskAndSetProjectMembers(IntegrationTestCase):
	"""P7-U4. `save_task` (add, rename, close) and `set_project_members`
	(replace, idempotent), both scoped by the same `resolve_project_scope`
	pair `get_project` already uses -- no separate authorisation logic."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.other_company = ensure_baseline_company()
		self.dm_employee, self.dm_user = make_test_delivery_manager()
		_, self.hr_user = make_test_hr_manager_employee()
		# `HelixHR Delivery Manager` deliberately carries no `Employee` role
		# (P7-U1 -- the same shape as `IT Team`), so it cannot book its own
		# time through `save_my_week`. Booking time needs a real Employee;
		# this one is added to `member_project` alongside the Delivery
		# Manager so `save_my_week`'s own bookable-project check passes.
		#
		# A fresh identity of this class's own, not the shared
		# `make_test_employee_and_manager()` fixture: on a long-lived bench
		# that identity accumulates a strict-mode `User Permission` on every
		# Project it has ever booked time on (`test_api_timesheet.py`'s own
		# fixture), which would restrict it to projects this class never
		# created (docs/runbook.md's own warning about this site).
		self.worker_user = "u4-worker@helixhr.test"
		make_test_user(self.worker_user, self.company)

		self.member_project = make_test_project(
			self.company, "_Test U4 Member Project", members=[self.dm_user, self.worker_user]
		)
		self.other_project = make_test_project(self.company, "_Test U4 Other Project")
		self.other_company_project = make_test_project(
			self.other_company, "_Test U4 Other Company Project"
		)

	def tearDown(self):
		frappe.set_user("Administrator")

	def _book_time(self, project, task, hours=2):
		"""One row of the current week's Timesheet, through `save_my_week`
		exactly as an employee's own week screen would -- not a Timesheet
		inserted directly -- so "time already booked" means what it means
		for a real caller of this API. Booked as `worker_user`, the plain
		Employee added to `member_project` in `setUp`, since the Delivery
		Manager holds no `Employee` role to book time with."""
		from frappe.utils import today

		from helixhr.api import save_my_week
		from helixhr.utils import get_week_bounds

		monday, _sunday = get_week_bounds(today())
		frappe.set_user(self.worker_user)
		save_my_week(str(monday), [{"date": str(monday), "project": project, "task": task, "hours": hours}])
		return monday

	def test_a_delivery_manager_cannot_add_a_task_to_a_project_they_are_not_a_member_of(self):
		from helixhr.api import save_task

		frappe.set_user(self.dm_user)
		with self.assertRaises(frappe.PermissionError):
			save_task(self.other_project, subject="_Test U4 Non-Member Task")

	def test_an_hr_manager_cannot_write_to_a_project_in_another_company(self):
		from helixhr.api import save_task, set_project_members

		frappe.set_user(self.hr_user)
		with self.assertRaises(frappe.PermissionError):
			save_task(self.other_company_project, subject="_Test U4 Cross-Company Task")
		with self.assertRaises(frappe.PermissionError):
			set_project_members(self.other_company_project, [])

	def test_closing_a_task_sets_status_and_keeps_booked_time_readable(self):
		from helixhr.api import save_task

		frappe.set_user(self.dm_user)
		task = save_task(self.member_project, subject="_Test U4 Task To Close")
		self.assertEqual(task["status"], "Open")

		self._book_time(self.member_project, task["name"], hours=3)

		frappe.set_user(self.dm_user)
		closed = save_task(self.member_project, task=task["name"], status="Completed")
		self.assertEqual(closed["status"], "Completed")
		self.assertTrue(frappe.db.exists("Task", task["name"]))

		rows = frappe.get_all(
			"Timesheet Detail", filters={"task": task["name"]}, fields=["hours", "is_billable"]
		)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].hours, 3)

	def test_renaming_a_task_does_not_change_its_status(self):
		from helixhr.api import save_task

		frappe.set_user(self.dm_user)
		task = save_task(self.member_project, subject="_Test U4 Task To Rename")
		renamed = save_task(self.member_project, task=task["name"], subject="_Test U4 Task Renamed")
		self.assertEqual(renamed["name"], task["name"])
		self.assertEqual(renamed["subject"], "_Test U4 Task Renamed")
		self.assertEqual(renamed["status"], "Open")

	def test_adding_a_task_to_a_project_the_caller_does_not_administer_by_id_is_refused(self):
		"""A task that belongs to `other_project` cannot be reached by
		naming `member_project` (the caller's own scope) alongside it."""
		from helixhr.api import save_task

		other_task = frappe.get_doc(
			{"doctype": "Task", "project": self.other_project, "subject": "_Test U4 Foreign Task"}
		).insert(ignore_permissions=True)

		frappe.set_user(self.dm_user)
		with self.assertRaises(frappe.PermissionError):
			save_task(self.member_project, task=other_task.name, subject="Hijacked")

	def test_assigning_an_employee_with_no_linked_user_refuses_the_whole_call_and_names_them(self):
		from helixhr.api import set_project_members

		linked_employee = frappe.db.get_value("Employee", {"user_id": self.dm_user})
		orphan_name = _make_employee_without_user(self.company, "_Test U4 Orphan Employee")
		orphan_display_name = frappe.db.get_value("Employee", orphan_name, "employee_name")

		frappe.set_user(self.dm_user)
		with self.assertRaises(frappe.ValidationError) as caught:
			set_project_members(self.member_project, [linked_employee, orphan_name])
		self.assertIn(orphan_display_name, str(caught.exception))

		# The whole call refused -- membership is unchanged, not partially applied.
		members = {row.user for row in frappe.get_doc("Project", self.member_project).users}
		self.assertEqual(members, {self.dm_user, self.worker_user})

	def test_assigning_the_same_set_twice_is_idempotent(self):
		from helixhr.api import set_project_members

		linked_employee = frappe.db.get_value("Employee", {"user_id": self.dm_user})

		frappe.set_user(self.dm_user)
		first = set_project_members(self.member_project, [linked_employee])
		second = set_project_members(self.member_project, [linked_employee])
		self.assertEqual(first, second)
		members = [row.user for row in frappe.get_doc("Project", self.member_project).users]
		self.assertEqual(members, [self.dm_user])

	def test_removing_a_member_does_not_alter_time_they_already_recorded(self):
		from helixhr.api import save_task, set_project_members

		frappe.set_user(self.dm_user)
		task = save_task(self.member_project, subject="_Test U4 Removed Member Task")
		self._book_time(self.member_project, task["name"], hours=4)

		# Replace the membership with an empty set -- the caller removes
		# themselves along with everyone else.
		frappe.set_user(self.dm_user)
		set_project_members(self.member_project, [])

		rows = frappe.get_all("Timesheet Detail", filters={"task": task["name"]}, fields=["hours"])
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].hours, 4)
		# `setUp`'s `make_test_project` re-adds any member missing from its
		# `members` list on the next test, so membership left empty here
		# does not need restoring for this class's other tests to hold.

	def test_marking_a_project_billable_does_not_alter_an_existing_timesheet_detail_row(self):
		"""R9, thin per the plan's own note: full billable-hours capture is
		U6. This only confirms flipping the flag never touches a
		`Timesheet Detail` row that already exists."""
		from helixhr.api import save_task

		frappe.set_user(self.dm_user)
		task = save_task(self.member_project, subject="_Test U4 Billable Flip Task")
		self._book_time(self.member_project, task["name"], hours=1)

		before = frappe.get_all(
			"Timesheet Detail",
			filters={"task": task["name"]},
			fields=["name", "hours", "is_billable", "billing_hours"],
		)
		self.assertEqual(len(before), 1)

		frappe.set_user("Administrator")
		frappe.db.set_value("Project", self.member_project, "helixhr_is_billable", 1)

		after = frappe.get_all(
			"Timesheet Detail",
			filters={"task": task["name"]},
			fields=["name", "hours", "is_billable", "billing_hours"],
		)
		self.assertEqual(after, before)
