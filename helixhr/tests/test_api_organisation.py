import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days

from helixhr.api import create_my_request, get_organisation_view
from helixhr.tests.utils import EMPLOYEE_USER, ensure_test_email_account, make_test_it_user

# P5-U15 / P5-R20, P5-R23. A read-only, company-scoped, aggregate-only view
# for HR Manager and System Manager.
#
# Everything here runs against a dedicated company created just for this
# suite, not the shared `ensure_test_company()` fixture -- a long-lived
# bench's shared company carries Leave Applications, Timesheets and
# Attendance Requests seeded by every other suite that has ever run, and an
# *exact* count assertion against a shared company would be a guess dressed
# up as a test. Isolation, not a bigger fixture, is what makes "counts match
# directly computed figures" (the plan's own scenario) actually assertable.
ORG_COMPANY = "_Test Organisation Co"
ORG_HR_USER = "org-hr-manager@helixhr.test"
LEAVE_TYPE = "Casual Leave"


def _ensure_org_company():
	if not frappe.db.exists("Company", ORG_COMPANY):
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": ORG_COMPANY,
				"abbr": "TOC",
				"default_currency": "USD",
				"country": "United States",
			}
		).insert(ignore_permissions=True)
	return ORG_COMPANY


def _ensure_org_employee(number, company, user=None, extra_roles=()):
	name = frappe.db.get_value("Employee", {"employee_number": number}, "name")
	if name:
		frappe.db.set_value("Employee", name, "status", "Active")
		return name
	if user and not frappe.db.exists("User", user):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": user,
				"first_name": number,
				"send_welcome_email": 0,
				"roles": [{"role": "Employee"}, *({"role": role} for role in extra_roles)],
			}
		).insert(ignore_permissions=True)
	doc = frappe.get_doc(
		{
			"doctype": "Employee",
			"employee_number": number,
			"first_name": number,
			"company": company,
			"user_id": user,
			"date_of_birth": "1990-01-01",
			"date_of_joining": "2020-01-01",
			"gender": frappe.db.get_value("Gender", {}, "name"),
			"status": "Active",
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


class TestOrganisationView(IntegrationTestCase):
	"""`create_my_request` (below) queues mail through `frappe.enqueue`, which
	commits the current transaction as a side effect -- so this suite cannot
	rely on the usual per-test rollback the way `test_api_team.py`'s
	fixed-name `db_insert` rows do. Every row this suite seeds is named with
	a per-test tag and is explicitly deleted in `tearDown`, regardless of
	whether a commit happened."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.company = _ensure_org_company()
		self.tag = frappe.generate_hash(length=8)
		self.seeded_leaves = []

		self.hr_manager = _ensure_org_employee(
			"_test-org-hr", self.company, user=ORG_HR_USER, extra_roles=("HR Manager",)
		)
		self.employee_a = _ensure_org_employee("_test-org-a", self.company)
		self.employee_b = _ensure_org_employee("_test-org-b", self.company)
		self.left_employee = _ensure_org_employee("_test-org-left", self.company)
		frappe.db.set_value("Employee", self.left_employee, "status", "Left")

		# A far-past window, like `test_api_team.py`'s own fixtures, so a
		# company this test just created for itself carries no ambiguity
		# about "today" either -- except the one row deliberately dated today,
		# below.
		self.past_from, self.past_to = "2019-03-04", "2019-03-06"
		# `oldest_pending_days` measures the row's `creation` timestamp, not
		# its leave dates -- so the one row this suite means to be "the
		# oldest" is seeded with an explicit, old `creation`.
		self._seed_leave(
			self.employee_a,
			self.past_from,
			self.past_to,
			"OPEN-A",
			"Open",
			0,
			creation="2020-01-01 00:00:00",
		)
		self._seed_leave(self.employee_b, self.past_from, self.past_to, "OPEN-B", "Open", 0)
		# Approved and in the past: counted nowhere in this payload.
		self._seed_leave(self.employee_a, "2019-01-01", "2019-01-02", "DONE-A", "Approved", 1)

		today = frappe.utils.nowdate()
		self._seed_leave(self.employee_b, today, add_days(today, 1), "TODAY-B", "Approved", 1)

		ensure_test_email_account()
		frappe.set_user(EMPLOYEE_USER)

	def _seed_leave(self, employee, from_date, to_date, suffix, status, docstatus, creation=None):
		doc = frappe.get_doc(
			{
				"doctype": "Leave Application",
				"employee": employee,
				"leave_type": LEAVE_TYPE,
				"from_date": str(from_date),
				"to_date": str(to_date),
				"description": "_Test organisation view leave reason, never returned by this projection",
				"status": status,
				"docstatus": docstatus,
			}
		)
		doc.name = f"_TEST-ORG-LEAVE-{self.tag}-{suffix}"
		if creation:
			doc.creation = creation
			doc.modified = creation
		doc.db_insert()
		self.seeded_leaves.append(doc.name)
		return doc.name

	def tearDown(self):
		frappe.set_user("Administrator")
		for name in self.seeded_leaves:
			frappe.db.delete("Leave Application", {"name": name})
		frappe.db.delete("HR Request", {"employee": ["in", [self.employee_a, self.employee_b]]})
		frappe.db.commit()

	def _view(self):
		frappe.set_user(ORG_HR_USER)
		return get_organisation_view()

	def test_the_key_set_is_exhaustive(self):
		payload = self._view()
		self.assertEqual(
			set(payload.keys()),
			{"company", "headcount", "on_leave_today", "queues", "celebrations"},
		)
		self.assertEqual(
			set(payload["queues"].keys()), {"leave", "timesheet", "attendance", "request"}
		)
		for queue in payload["queues"].values():
			self.assertEqual(set(queue.keys()), {"pending", "oldest_pending_days"})

	def test_no_per_person_absence_appears_in_the_payload(self):
		payload = self._view()
		rendered = frappe.as_json(payload)
		# Neither employee's identifier nor the leave reason -- the boundary
		# this projection exists to hold -- may reach the wire.
		self.assertNotIn(self.employee_a, rendered)
		self.assertNotIn(self.employee_b, rendered)
		self.assertNotIn("organisation view leave reason", rendered)
		# Celebrations carry a name by design (the home page already makes
		# it public); absence must not.
		self.assertNotIn("from_date", rendered)
		self.assertNotIn("to_date", rendered)

	def test_a_caller_without_the_capability_is_refused_server_side(self):
		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.PermissionError):
			get_organisation_view()

		make_test_it_user()
		from helixhr.tests.utils import IT_TEAM_USER

		frappe.set_user(IT_TEAM_USER)
		with self.assertRaises(frappe.PermissionError):
			get_organisation_view()

	def test_counts_match_directly_computed_figures(self):
		payload = self._view()
		# 4 active employees: the HR manager plus A and B and -- deliberately
		# excluded -- nobody else; `left_employee` is status Left.
		self.assertEqual(payload["company"], self.company)
		self.assertEqual(payload["headcount"], 3)
		self.assertEqual(payload["on_leave_today"], 1)
		self.assertEqual(payload["queues"]["leave"]["pending"], 2)
		self.assertIsNotNone(payload["queues"]["leave"]["oldest_pending_days"])
		self.assertGreater(payload["queues"]["leave"]["oldest_pending_days"], 0)
		self.assertEqual(payload["queues"]["timesheet"]["pending"], 0)
		self.assertEqual(payload["queues"]["attendance"]["pending"], 0)
		self.assertEqual(payload["queues"]["request"]["pending"], 0)

	def test_a_pending_hr_request_is_counted_and_aged(self):
		import uuid

		# Give employee_a a login so `create_my_request` has a session to
		# resolve, matching every other request test's own setup.
		requester = "_test-org-a@helixhr.test"
		if not frappe.db.exists("User", requester):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": requester,
					"first_name": "OrgA",
					"send_welcome_email": 0,
					"roles": [{"role": "Employee"}],
				}
			).insert(ignore_permissions=True)
		frappe.db.set_value("Employee", self.employee_a, "user_id", requester)

		frappe.set_user(requester)
		create_my_request(
			category="HR Letter",
			subject="Organisation view fixture",
			details="counted by the queue aggregate",
			operation_key=str(uuid.uuid4()),
		)

		payload = self._view()
		self.assertEqual(payload["queues"]["request"]["pending"], 1)
		self.assertEqual(payload["queues"]["request"]["oldest_pending_days"], 0)

	def test_the_read_is_bounded_on_a_larger_organisation(self):
		"""Not one round trip per pending row: seed a batch and confirm the
		aggregate still answers with the exact count in one call, the shape
		`_queue_aggregate`'s single count(*)/min(creation) query guarantees."""
		for index in range(25):
			self._seed_leave(self.employee_a, self.past_from, self.past_to, f"BULK-{index}", "Open", 0)

		payload = self._view()
		self.assertEqual(payload["queues"]["leave"]["pending"], 2 + 25)

	def test_the_read_is_rate_limited(self):
		from helixhr.utils import RATE_LIMIT_POLICY

		self.assertIn("get_organisation_view", RATE_LIMIT_POLICY)
