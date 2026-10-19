"""Plan 2026-10-19-001 U1: the HelixHR Access Assignment row, the
append-only HelixHR Access Change log, and the two new portal-only roles."""

import frappe
from frappe.client import delete as client_delete
from frappe.client import insert as client_insert
from frappe.client import set_value as client_set_value
from frappe.tests import IntegrationTestCase

from helixhr import preflight
from helixhr.tests.utils import (
	_make_role_user,
	ensure_baseline_company,
	ensure_test_company,
	make_test_user,
)
from helixhr.utils import (
	ACCESS_ADMIN_ROLE,
	ALL_ORGS_SCOPE_KEY,
	NOTIFICATION_MANAGER_ROLE,
	ORG_VIEWER_ROLE,
	REPORT_MANAGER_ROLE,
)

ASSIGNEE_USER = "access-assignee@helixhr.test"
SYSTEM_MANAGER_USER = "access-system-manager@helixhr.test"


def _assignment(role, company=None, all_orgs=0, allow_export=0, user=ASSIGNEE_USER):
	return frappe.get_doc(
		{
			"doctype": "HelixHR Access Assignment",
			"user": user,
			"role": role,
			"company": company,
			"all_orgs": all_orgs,
			"allow_export": allow_export,
		}
	)


def _change(target=ASSIGNEE_USER):
	return frappe.get_doc(
		{
			"doctype": "HelixHR Access Change",
			"target_user": target,
			"changed_by": "Administrator",
			"reason": "Covering Org B during leave.",
			# A JSON field takes text; a list would be read as a child table.
			"before": frappe.as_json([]),
			"after": frappe.as_json([{"role": "HR Manager", "company": None, "all_orgs": 1}]),
		}
	)


class _Assignments(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.other = ensure_baseline_company()
		make_test_user(ASSIGNEE_USER, self.company)
		frappe.db.delete("HelixHR Access Assignment", {"user": ASSIGNEE_USER})

	def tearDown(self):
		frappe.set_user("Administrator")


class TestAssignmentRows(_Assignments):
	def test_a_named_org_row_saves_keyed_by_its_company(self):
		row = _assignment("HR Manager", self.company).insert(ignore_permissions=True)
		self.assertEqual(row.scope_key, self.company)
		self.assertEqual(row.origin, "manual")

	def test_an_all_orgs_row_saves_keyed_all(self):
		row = _assignment("HR Manager", all_orgs=1).insert(ignore_permissions=True)
		self.assertEqual(row.scope_key, ALL_ORGS_SCOPE_KEY)
		self.assertIsNone(row.company)

	def test_one_role_on_two_orgs_is_two_rows(self):
		_assignment("HR Manager", self.company).insert(ignore_permissions=True)
		_assignment("HR Manager", self.other).insert(ignore_permissions=True)
		self.assertEqual(
			frappe.db.count("HelixHR Access Assignment", {"user": ASSIGNEE_USER, "role": "HR Manager"}), 2
		)

	def test_export_saves_on_reports_and_org_viewer(self):
		for role in (REPORT_MANAGER_ROLE, ORG_VIEWER_ROLE):
			row = _assignment(role, self.company, allow_export=1).insert(ignore_permissions=True)
			self.assertEqual(row.allow_export, 1)

	def test_all_orgs_only_roles_save_on_all_orgs(self):
		for role in (NOTIFICATION_MANAGER_ROLE, ACCESS_ADMIN_ROLE):
			_assignment(role, all_orgs=1).insert(ignore_permissions=True)

	def test_editing_a_row_to_all_orgs_moves_its_key(self):
		row = _assignment("IT Team", self.company).insert(ignore_permissions=True)
		row.company, row.all_orgs = None, 1
		row.save(ignore_permissions=True)
		self.assertEqual(row.scope_key, ALL_ORGS_SCOPE_KEY)


class TestAssignmentRefusals(_Assignments):
	def _refused(self, row, message=None):
		with self.assertRaises(frappe.ValidationError) as caught:
			row.insert(ignore_permissions=True)
		if message:
			self.assertIn(message, str(caught.exception))

	def test_all_orgs_with_a_company_is_refused(self):
		self._refused(_assignment("HR Manager", self.company, all_orgs=1), "not both")

	def test_neither_all_orgs_nor_a_company_is_refused(self):
		self._refused(_assignment("HR Manager"), "Choose All orgs or an org")

	def test_a_duplicate_named_org_row_is_refused(self):
		_assignment("HR Manager", self.company).insert(ignore_permissions=True)
		self._refused(_assignment("HR Manager", self.company), "already holds")

	def test_a_second_all_orgs_row_is_refused(self):
		_assignment("HR Manager", all_orgs=1).insert(ignore_permissions=True)
		self._refused(_assignment("HR Manager", all_orgs=1), "already holds")

	def test_the_unique_index_refuses_a_duplicate_validate_never_saw(self):
		"""Two concurrent saves both pass `validate`; the index refuses the
		second. Simulated by inserting the row without running validate."""
		_assignment("HR Manager", all_orgs=1).insert(ignore_permissions=True)
		twin = _assignment("HR Manager", all_orgs=1)
		twin.scope_key = ALL_ORGS_SCOPE_KEY
		twin.set_new_name()
		with self.assertRaises(frappe.UniqueValidationError):
			twin.db_insert()

	def test_export_on_hr_manager_is_refused(self):
		self._refused(_assignment("HR Manager", self.company, allow_export=1), "Export")

	def test_all_orgs_only_roles_on_a_named_org_are_refused(self):
		for role in (NOTIFICATION_MANAGER_ROLE, ACCESS_ADMIN_ROLE):
			self._refused(_assignment(role, self.company), "All orgs")

	def test_an_unknown_role_is_refused(self):
		self._refused(_assignment("No Such Role", self.company))

	def test_a_role_helixhr_does_not_assign_is_refused(self):
		self._refused(_assignment("Employee", self.company), "not a role HelixHR assigns")

	def test_system_manager_is_not_assignable(self):
		self._refused(_assignment("System Manager", all_orgs=1), "not a role HelixHR assigns")

	def test_an_unknown_company_is_refused(self):
		self._refused(_assignment("HR Manager", "No Such Company"))

	def test_administrator_cannot_hold_an_assignment(self):
		self._refused(_assignment("HR Manager", all_orgs=1, user="Administrator"), "cannot hold")


class TestAccessChangeLog(_Assignments):
	def test_a_change_inserts_with_its_before_and_after(self):
		change = _change().insert(ignore_permissions=True)
		stored = frappe.get_doc("HelixHR Access Change", change.name)
		self.assertEqual(frappe.parse_json(stored.after)[0]["role"], "HR Manager")

	def test_a_saved_change_cannot_be_edited(self):
		change = _change().insert(ignore_permissions=True)
		change.reason = "Rewritten."
		with self.assertRaises(frappe.ValidationError):
			change.save(ignore_permissions=True)


class TestDeskAndRestWritesRefused(_Assignments):
	"""KTD1: System Manager holds read/report only, so every Desk or REST
	write is refused -- the save endpoint and the migration are the only
	writers, and Desk can never bypass the role sync or the audit."""

	def setUp(self):
		super().setUp()
		_make_role_user(SYSTEM_MANAGER_USER, "System Manager")
		self.assignment = _assignment("IT Team", self.company).insert(ignore_permissions=True)
		self.change = _change().insert(ignore_permissions=True)
		frappe.set_user(SYSTEM_MANAGER_USER)

	def test_a_system_manager_reads_both(self):
		self.assertTrue(frappe.has_permission("HelixHR Access Assignment", "read", self.assignment.name))
		self.assertTrue(frappe.has_permission("HelixHR Access Change", "read", self.change.name))

	def test_a_system_manager_cannot_create_either(self):
		with self.assertRaises(frappe.PermissionError):
			_assignment("IT Team", self.other).insert()
		with self.assertRaises(frappe.PermissionError):
			client_insert(_assignment("IT Team", self.other).as_dict())
		with self.assertRaises(frappe.PermissionError):
			client_insert(_change().as_dict())

	def test_a_system_manager_cannot_edit_either(self):
		with self.assertRaises(frappe.PermissionError):
			client_set_value("HelixHR Access Assignment", self.assignment.name, "all_orgs", 1)
		with self.assertRaises(frappe.PermissionError):
			client_set_value("HelixHR Access Change", self.change.name, "reason", "Rewritten.")

	def test_a_system_manager_cannot_delete_either(self):
		with self.assertRaises(frappe.PermissionError):
			client_delete("HelixHR Access Assignment", self.assignment.name)
		with self.assertRaises(frappe.PermissionError):
			client_delete("HelixHR Access Change", self.change.name)


class TestNewRoles(IntegrationTestCase):
	def test_the_new_roles_are_portal_only_with_no_grant(self):
		for role in (ORG_VIEWER_ROLE, ACCESS_ADMIN_ROLE):
			doc = frappe.db.get_value("Role", role, ["desk_access", "is_custom"], as_dict=True)
			self.assertIsNotNone(doc, role)
			self.assertEqual((doc.desk_access, doc.is_custom), (0, 0), role)
		self.assertEqual(preflight.check_org_viewer_role()["status"], preflight.PASS)
		self.assertEqual(preflight.check_access_admin_role()["status"], preflight.PASS)
