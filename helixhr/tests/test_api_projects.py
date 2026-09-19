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
	ensure_hr_manager_user,
	ensure_test_company,
	make_test_delivery_manager,
	make_test_employee_and_manager,
	make_test_hr_manager_employee,
	make_test_project,
	make_test_user,
)
from helixhr.utils import project_scope_filters, resolve_project_scope

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
