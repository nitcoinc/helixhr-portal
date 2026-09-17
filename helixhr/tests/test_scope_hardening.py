"""Regression tests for the 2026-09-17 security review.

Each test pins one way the admin scope used to widen, or one way a
record's existence used to leak. They live together, rather than spread
over the suites that own each surface, so the review's findings stay
legible as a set: a reader who wants to know "what did that review
change" reads this file.
"""

import frappe
from frappe.tests import IntegrationTestCase

from helixhr.api import (
	_APPROVAL_NOT_FOUND,
	_can_open_desk,
	attach_to_request_reply,
	get_approval_detail,
	get_person,
	get_portal_bootstrap,
	search_people,
)
from helixhr.tests.utils import (
	EMPLOYEE_USER,
	TEST_COMPANY,
	ensure_baseline_company,
	ensure_test_company,
	make_test_employee_and_manager,
	make_test_hr_manager_employee,
	make_test_it_user,
	make_test_user,
)
from helixhr.utils import resolve_admin_scope

LEFT_HR_USER = "left-hr-manager@helixhr.test"
OUTSIDER_USER = "hardening-outsider@helixhr.test"
DESK_LESS_SYSTEM_USER = "desk-less-system-user@helixhr.test"


def _grant_hr_manager(user):
	doc = frappe.get_doc("User", user)
	if "HR Manager" not in [row.role for row in doc.roles]:
		doc.append_roles("HR Manager")
		doc.save(ignore_permissions=True)
		frappe.clear_cache(user=user)


def _seed_request(tag, employee, status, routed_to_role="HR Manager"):
	"""One HR Request row, inserted at the table so no notification fires
	and no commit leaks past the test's own rollback (the same reason
	test_api_organisation.py seeds leave that way)."""
	category = frappe.db.get_value("HelixHR Request Category", {}, "name")
	doc = frappe.get_doc(
		{
			"doctype": "HR Request",
			"employee": employee,
			"category": category,
			"subject": f"_Test hardening {tag}",
			"details": "_Test hardening request body",
			"naming_series": "HR-REQ-.YYYY.-",
			"status": status,
			"routed_to_role": routed_to_role,
		}
	)
	doc.name = f"_TEST-HARDENING-{tag}-{frappe.generate_hash(length=6)}"
	doc.db_insert()
	return doc.name


class TestOffboardedHrManagerLosesScope(IntegrationTestCase):
	"""An HR Manager whose Employee is Left/Inactive/Suspended, but whose
	User still holds the role, used to resolve to *unscoped* -- the same
	bucket as the intentional Desk-only persona with no Employee record at
	all -- and could read every company. Now they resolve to nobody."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.colleague, _, _, _ = make_test_employee_and_manager()
		make_test_user(
			LEFT_HR_USER,
			self.company,
			create_user_permission=0,
			status="Left",
			relieving_date="2026-01-31",
		)
		_grant_hr_manager(LEFT_HR_USER)

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_the_scope_is_none_not_unscoped(self):
		self.assertEqual(resolve_admin_scope(LEFT_HR_USER), {"kind": "none", "company": None})

	def test_every_administrative_read_refuses_them(self):
		frappe.set_user(LEFT_HR_USER)
		with self.assertRaises(frappe.PermissionError):
			search_people(query="Emp")
		with self.assertRaises(frappe.PermissionError):
			get_person(self.colleague)
		self.assertFalse(get_portal_bootstrap()["can_see_people"])

	def test_the_hr_request_hooks_agree(self):
		from helixhr.helixhr.doctype.hr_request.hr_request import has_permission

		name = _seed_request("LEFT", self.colleague, "Open")
		frappe.set_user(LEFT_HR_USER)
		self.assertEqual(frappe.get_list("HR Request", filters={"name": name}), [])
		self.assertFalse(has_permission(frappe.get_doc("HR Request", name), "read", LEFT_HR_USER))

	def test_the_active_hr_manager_in_the_same_company_is_unaffected(self):
		_, hr_user = make_test_hr_manager_employee()
		self.assertEqual(resolve_admin_scope(hr_user), {"kind": "company", "company": TEST_COMPANY})


class TestHrRecordRoutesRespectCompanyScope(IntegrationTestCase):
	"""The list routes were company-scoped; the record routes
	(`get_approval_detail`, `act_on_approval`) short-circuited on "is HR"
	alone. A Company-A HR Manager could read a Company-B request's evidence
	by id. Now the record routes ask the same scope helper -- and refuse
	with the same words as a missing record, so the endpoint is no longer
	an oracle for which ids exist."""

	def setUp(self):
		frappe.set_user("Administrator")
		ensure_test_company()
		self.hr_employee, self.hr_user = make_test_hr_manager_employee()
		self.colleague, _, _, _ = make_test_employee_and_manager()
		other_company = ensure_baseline_company()
		self.outsider = make_test_user(OUTSIDER_USER, other_company)
		self.outsider_request = _seed_request("OUTSIDE", self.outsider, "Open")
		self.own_company_request = _seed_request("INSIDE", self.colleague, "Open")

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_hr_reads_their_own_company_and_not_another(self):
		frappe.set_user(self.hr_user)
		self.assertEqual(get_approval_detail("request", self.own_company_request)["name"], self.own_company_request)
		with self.assertRaises(frappe.PermissionError):
			get_approval_detail("request", self.outsider_request)

	def test_missing_and_foreign_are_indistinguishable(self):
		frappe.set_user(self.hr_user)
		with self.assertRaises(frappe.PermissionError) as foreign:
			get_approval_detail("request", self.outsider_request)
		with self.assertRaises(frappe.PermissionError) as missing:
			get_approval_detail("request", "HR-REQ-does-not-exist")
		self.assertEqual(str(foreign.exception), str(missing.exception))
		self.assertEqual(str(missing.exception), _APPROVAL_NOT_FOUND)

	def test_a_manager_refusal_names_no_employee(self):
		# The old text was "Only HR-EMP-00017's approver or HR can act on
		# this ..." -- a record-to-owner oracle for any signed-in employee.
		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.PermissionError) as refused:
			get_approval_detail("request", self.outsider_request)
		self.assertNotIn(self.outsider, str(refused.exception))
		self.assertEqual(str(refused.exception), _APPROVAL_NOT_FOUND)

	def test_attaching_to_a_closed_request_is_refused(self):
		closed = _seed_request("DONE", self.colleague, "Done")
		frappe.set_user(self.hr_user)
		with self.assertRaises(frappe.ValidationError):
			attach_to_request_reply(closed)

	def test_the_read_is_rate_limited(self):
		from helixhr.utils import RATE_LIMIT_POLICY

		for method in ("get_approval_detail", "get_my_approvals", "get_dashboard"):
			self.assertIn(method, RATE_LIMIT_POLICY)


class TestRoutedWorkerCannotRewriteAFiling(IntegrationTestCase):
	"""IT Team holds level-0 write on HR Request so it can pick a request
	up and reply. The filing fields -- what the employee wrote -- used to be
	frozen only once the request left Open, so a worker could rewrite them
	through /api/resource before picking it up."""

	def setUp(self):
		frappe.set_user("Administrator")
		ensure_test_company()
		self.colleague, _, _, _ = make_test_employee_and_manager()
		self.it_employee, self.it_user = make_test_it_user()
		self.request = _seed_request("IT", self.colleague, "Open", routed_to_role="IT Team")

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_details_are_frozen_for_the_worker_even_while_open(self):
		frappe.set_user(self.it_user)
		doc = frappe.get_doc("HR Request", self.request)
		doc.details = "rewritten by the worker"
		with self.assertRaises(frappe.PermissionError):
			doc.save()

	def test_the_worker_can_still_pick_it_up(self):
		frappe.set_user(self.it_user)
		doc = frappe.get_doc("HR Request", self.request)
		doc.status = "In Progress"
		doc.save()
		self.assertEqual(frappe.db.get_value("HR Request", self.request, "status"), "In Progress")


class TestCanOpenDeskIsTheUserType(IntegrationTestCase):
	"""P6-KTD4: the Desk flag follows the *user type*, never the role list.
	Frappe hands every System User the `Desk User` role automatically
	(`frappe.permissions.AUTOMATIC_ROLES`), so a role-based check is the
	same test in disguise -- and it was the role list this used to scan."""

	def setUp(self):
		frappe.set_user("Administrator")
		# A Website User handed `HR Manager` by hand: the role carries
		# desk_access=1, but Desk still does not load for a Website User,
		# so the flag must say no. A bare User rather than make_test_user,
		# because ERPNext's User hooks re-add `Employee` to anyone linked to
		# an Employee record.
		if not frappe.db.exists("User", DESK_LESS_SYSTEM_USER):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": DESK_LESS_SYSTEM_USER,
					"first_name": "Website-hr",
					"send_welcome_email": 0,
					"roles": [{"role": "HR Manager"}],
				}
			).insert(ignore_permissions=True)
		frappe.db.set_value("User", DESK_LESS_SYSTEM_USER, "user_type", "Website User")
		frappe.clear_cache(user=DESK_LESS_SYSTEM_USER)

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_a_website_user_holding_hr_manager_still_cannot_open_desk(self):
		self.assertIn("HR Manager", frappe.get_roles(DESK_LESS_SYSTEM_USER))
		self.assertFalse(_can_open_desk(DESK_LESS_SYSTEM_USER))

	def test_every_system_user_carries_the_automatic_desk_role(self):
		# The fact the simplification rests on, pinned so a Frappe upgrade
		# that changes it fails here first.
		_, hr_user = make_test_hr_manager_employee()
		self.assertEqual(frappe.db.get_value("User", hr_user, "user_type"), "System User")
		self.assertIn("Desk User", frappe.get_roles(hr_user))
		self.assertTrue(_can_open_desk(hr_user))

	def test_the_it_team_website_user_cannot(self):
		_, it_user = make_test_it_user()
		self.assertFalse(_can_open_desk(it_user))
