"""Plan 2026-10-04-003 U3: change requests on approved weeks.

The raise/withdraw projections, the accept path (KTD4: cancel + amend in
one elevated transaction, owner = the employee), decline, the approver
re-pointing (KTD10), HR routing and the two-company isolation. The
accept-path scenarios come first per the plan's execution note.
"""

import hashlib
import json

import frappe
from frappe.model.workflow import apply_workflow
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from helixhr.api import (
	act_on_approval,
	get_approval_detail,
	get_my_approvals,
	get_my_week,
	raise_timesheet_change,
	recall_my_week,
	save_my_week,
	withdraw_timesheet_change,
)
from helixhr.tests.test_api_timesheet import make_test_project
from helixhr.tests.utils import (
	ensure_baseline_company,
	make_test_employee_and_manager,
	make_test_hr_manager_employee,
	make_test_user,
)
from helixhr.utils import get_week_bounds

CHANGE_DOCTYPE = "HelixHR Timesheet Change"
COMMENT = "Tuesday should be six hours, not two"


class TestTimesheetChange(IntegrationTestCase):
	def setUp(self):
		self.employee_name, self.employee_user, self.manager_name, self.manager_user = (
			make_test_employee_and_manager()
		)
		frappe.db.set_value("Employee", self.employee_name, "reports_to", self.manager_name)

		method_name = self.id().split(".")[-1]
		digest = int(hashlib.md5(self.id().encode()).hexdigest(), 16)
		week_offset = (digest % 200000) * 7
		self.monday, self.sunday = get_week_bounds(add_days(frappe.utils.today(), week_offset))
		self.project = make_test_project(method_name, users=[self.employee_user])
		for key in ("save_my_week", "recall_my_week", "raise_timesheet_change", "withdraw_timesheet_change"):
			frappe.cache.delete(f"helixhr:rate-limit:{key}:{self.employee_user}")
		frappe.cache.delete(f"helixhr:rate-limit:act_on_approval:{self.manager_user}")

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

	def _approved_week(self):
		frappe.set_user(self.employee_user)
		name = save_my_week(str(self.monday), json.dumps([self._week_row()]))
		apply_workflow({"doctype": "Timesheet", "name": name}, "Submit")
		frappe.set_user(self.manager_user)
		apply_workflow({"doctype": "Timesheet", "name": name}, "Approve")
		frappe.set_user(self.employee_user)
		return name

	def _raise(self, comment=COMMENT):
		return raise_timesheet_change(str(self.monday), comment)

	def _act(self, change_name, action, user, comment=None):
		"""One decision with the concurrency token a real screen carries."""
		frappe.set_user(user)
		modified = frappe.db.get_value(CHANGE_DOCTYPE, change_name, "modified")
		return act_on_approval(
			CHANGE_DOCTYPE,
			change_name,
			action,
			comment=comment,
			expected_modified=str(modified),
			expected_state="Open",
		)

	def _open_change(self, timesheet):
		return frappe.db.get_value(CHANGE_DOCTYPE, {"timesheet": timesheet, "status": "Open"}, "name")

	def _submit_invoice_for(self, timesheet):
		"""A submitted Sales Invoice carrying the week (KTD5's first lock).

		Skips when the bench cannot build one -- the missing-defaults gap the
		runbook documents for headless sites -- rather than failing the whole
		suite on environment shape."""
		company = frappe.db.get_value("Employee", self.employee_name, "company")
		frappe.set_user("Administrator")
		customer = frappe.db.get_value("Customer", {"company": company}, "name")
		if not customer:
			customer = (
				frappe.get_doc(
					{"doctype": "Customer", "customer_name": "_Test Change Customer", "company": company}
				)
				.insert(ignore_permissions=True)
				.name
			)
		try:
			invoice = frappe.get_doc(
				{
					"doctype": "Sales Invoice",
					"company": company,
					"customer": customer,
					"currency": "INR",
					"conversion_rate": 1.0,
					"timesheets": [{"time_sheet": timesheet, "billing_hours": 4, "billing_rate": 100}],
				}
			)
			invoice.insert(ignore_permissions=True)
			invoice.submit()
			return invoice.name
		except Exception:
			# No rollback here on purpose: this class shares one transaction,
			# and rolling back would erase every earlier method's data.
			self.skipTest("this bench cannot build a Sales Invoice for the lock test")

	# --- the accept path, first per the plan's execution note ----------------

	def test_accept_cancels_and_amends_with_the_employee_as_owner(self):
		"""KTD4 end to end: the plain week is cancelled (docstatus 2,
		Cancelled) and the amended copy starts at Draft, owned by the
		employee, with the old decision reason gone."""
		timesheet = self._approved_week()
		change = self._raise()

		result = self._act(change["name"], "Accept", self.manager_user)
		self.assertEqual(result["state"], "Accepted")

		original = frappe.db.get_value(
			"Timesheet", timesheet, ["workflow_state", "docstatus"], as_dict=True
		)
		self.assertEqual(original.workflow_state, "Cancelled")
		self.assertEqual(original.docstatus, 2)

		amended = frappe.db.get_value(
			"Timesheet",
			{"amended_from": timesheet},
			["name", "workflow_state", "docstatus", "owner", "amended_from"],
			as_dict=True,
		)
		self.assertIsNotNone(amended)
		self.assertEqual(amended.workflow_state, "Draft")
		self.assertEqual(amended.docstatus, 0)
		self.assertEqual(amended.owner, self.employee_user)
		self.assertEqual(amended.amended_from, timesheet)
		self.assertIsNone(
			frappe.db.get_value("Timesheet", amended.name, "helixhr_decision_reason")
		)
		self.assertEqual(
			frappe.db.get_value(CHANGE_DOCTYPE, change["name"], "amended_timesheet"),
			amended.name,
		)
		self.assertEqual(
			frappe.db.get_value(CHANGE_DOCTYPE, change["name"], "decided_by"), self.manager_user
		)

	def test_accept_then_resubmit_reaches_the_manager_without_a_self_approval_trip(self):
		"""KTD4's owner rule is the whole point: the amended copy is the
		employee's, so their resubmit is a normal Submit and the manager's
		Approve passes `timesheet_before_submit` cleanly."""
		self._approved_week()
		change = self._raise()
		self._act(change["name"], "Accept", self.manager_user)

		frappe.set_user(self.employee_user)
		saved = save_my_week(str(self.monday), json.dumps([self._week_row(hours=7)]))
		apply_workflow({"doctype": "Timesheet", "name": saved}, "Submit")
		self.assertEqual(frappe.db.get_value("Timesheet", saved, "workflow_state"), "Pending Approval")

		frappe.set_user(self.manager_user)
		apply_workflow({"doctype": "Timesheet", "name": saved}, "Approve")
		self.assertEqual(frappe.db.get_value("Timesheet", saved, "workflow_state"), "Approved")

	def test_accept_refused_when_an_invoice_landed_after_the_raise(self):
		"""R9 rechecked under the lock: the week was changeable when the
		request was raised and is not when it is accepted, so the accept
		refuses and the request stays Open."""
		timesheet = self._approved_week()
		change = self._raise()

		self._submit_invoice_for(timesheet)

		frappe.set_user(self.manager_user)
		with self.assertRaises(frappe.ValidationError):
			self._act(change["name"], "Accept", self.manager_user)

		self.assertEqual(self._open_change(timesheet), change["name"], "the request stays Open")
		self.assertEqual(
			frappe.db.get_value("Timesheet", timesheet, "workflow_state"), "Approved"
		)

	# --- raise -----------------------------------------------------------------

	def test_raise_on_an_approved_week_reaches_the_manager(self):
		timesheet = self._approved_week()
		change = self._raise()

		self.assertEqual(change["status"], "Open")
		self.assertEqual(change["approver_user"], self.manager_user)
		self.assertEqual(self._open_change(timesheet), change["name"])

		bell = frappe.get_all(
			"Notification Log",
			filters={
				"for_user": self.manager_user,
				"document_type": CHANGE_DOCTYPE,
				"document_name": change["name"],
			},
		)
		self.assertEqual(len(bell), 1)

	def test_a_second_raise_on_the_same_week_is_refused(self):
		self._approved_week()
		self._raise()
		with self.assertRaises(frappe.ValidationError) as ctx:
			self._raise()
		self.assertIn("already an open change request", str(ctx.exception))

	def test_a_short_comment_is_refused(self):
		self._approved_week()
		with self.assertRaises(frappe.ValidationError):
			self._raise("fix it")

	def test_an_invoiced_week_cannot_raise_and_reads_not_changeable(self):
		timesheet = self._approved_week()
		self._submit_invoice_for(timesheet)

		frappe.set_user(self.employee_user)
		week = get_my_week(str(self.monday))
		self.assertFalse(week["changeable"]["ok"])
		self.assertIn("invoice", week["changeable"]["reason"])

		with self.assertRaises(frappe.ValidationError) as ctx:
			self._raise()
		self.assertIn("invoice", str(ctx.exception))

	def test_a_week_inside_a_payslip_cannot_raise_or_change(self):
		"""KTD5's second lock. The bench cannot build a full Salary Slip (the
		runbook's missing-masters gap), so the link is set the way payroll
		leaves it -- a submitted slip pointing back at its timesheet -- and
		the real read path is what the test exercises."""
		timesheet = self._approved_week()
		frappe.set_user("Administrator")
		frappe.db.set_value("Timesheet", timesheet, "salary_slip", "SAL-2026-00001")

		frappe.set_user(self.employee_user)
		week = get_my_week(str(self.monday))
		self.assertFalse(week["changeable"]["ok"])
		self.assertIn("payslip", week["changeable"]["reason"])

		with self.assertRaises(frappe.ValidationError) as ctx:
			self._raise()
		self.assertIn("payslip", str(ctx.exception))

	def test_raise_refused_before_approval_and_on_another_employees_week(self):
		# A pending week cannot carry a change request (R7: approved weeks only).
		frappe.set_user(self.employee_user)
		name = save_my_week(str(self.monday), json.dumps([self._week_row()]))
		apply_workflow({"doctype": "Timesheet", "name": name}, "Submit")
		with self.assertRaises(frappe.ValidationError):
			self._raise()

		# Another employee's week_start answers "no timesheet", never theirs.
		company = frappe.db.get_value("Employee", self.employee_name, "company")
		make_test_user("change-other@helixhr.test", company)
		frappe.set_user("change-other@helixhr.test")
		with self.assertRaises(frappe.DoesNotExistError):
			raise_timesheet_change(str(self.monday), COMMENT)

	def test_a_recalled_week_cannot_carry_a_change_request(self):
		"""Recall and a change request are two doors out of an approved week;
		once the week is back at Draft only the normal flow applies."""
		frappe.set_user(self.employee_user)
		name = save_my_week(str(self.monday), json.dumps([self._week_row()]))
		apply_workflow({"doctype": "Timesheet", "name": name}, "Submit")
		recall_my_week(str(self.monday))

		with self.assertRaises(frappe.ValidationError):
			self._raise()

	# --- decline and withdraw ----------------------------------------------------

	def test_decline_requires_a_reason_and_the_week_stays_approved(self):
		timesheet = self._approved_week()
		change = self._raise()

		with self.assertRaises(frappe.ValidationError):
			self._act(change["name"], "Decline", self.manager_user)

		result = self._act(
			change["name"], "Decline", self.manager_user, comment="The hours match the project's record."
		)
		self.assertEqual(result["state"], "Declined")

		row = frappe.db.get_value(
			CHANGE_DOCTYPE, change["name"], ["status", "decision_note", "decided_by"], as_dict=True
		)
		self.assertEqual(row.status, "Declined")
		self.assertEqual(row.decision_note, "The hours match the project's record.")
		self.assertEqual(row.decided_by, self.manager_user)
		self.assertEqual(
			frappe.db.get_value("Timesheet", timesheet, "workflow_state"), "Approved"
		)

	def test_the_employee_withdraws_an_open_request(self):
		timesheet = self._approved_week()
		change = self._raise()

		frappe.set_user(self.employee_user)
		result = withdraw_timesheet_change(change["name"])
		self.assertEqual(result["status"], "Withdrawn")
		self.assertIsNone(self._open_change(timesheet))

	def test_nobody_else_withdraws_or_accepts_another_employees_request(self):
		self._approved_week()
		change = self._raise()

		company = frappe.db.get_value("Employee", self.employee_name, "company")
		make_test_user("change-notmine@helixhr.test", company)
		frappe.set_user("change-notmine@helixhr.test")
		with self.assertRaises(frappe.PermissionError):
			withdraw_timesheet_change(change["name"])
		with self.assertRaises(frappe.PermissionError):
			self._act(change["name"], "Accept", "change-notmine@helixhr.test")

	def test_the_employee_cannot_accept_their_own_request(self):
		self._approved_week()
		change = self._raise()
		with self.assertRaises(frappe.PermissionError):
			self._act(change["name"], "Accept", self.employee_user)

	# --- routing (R10, KTD10) ------------------------------------------------------

	def test_a_reports_to_change_repoints_the_open_request(self):
		self._approved_week()
		change = self._raise()

		company = frappe.db.get_value("Employee", self.employee_name, "company")
		new_manager_user = "change-new-manager@helixhr.test"
		new_manager = make_test_user(new_manager_user, company)
		frappe.set_user("Administrator")
		employee = frappe.get_doc("Employee", self.employee_name)
		employee.reports_to = new_manager
		employee.save(ignore_permissions=True)

		self.assertEqual(
			frappe.db.get_value(CHANGE_DOCTYPE, change["name"], "approver_user"), new_manager_user
		)

		# The old manager cannot act; the new one can.
		with self.assertRaises(frappe.PermissionError):
			self._act(change["name"], "Accept", self.manager_user)
		result = self._act(
			change["name"],
			"Decline",
			new_manager_user,
			comment="Checked with the project lead; the week stands.",
		)
		self.assertEqual(result["state"], "Declined")

	def test_an_employee_who_left_auto_withdraws_their_open_request(self):
		timesheet = self._approved_week()
		change = self._raise()

		frappe.set_user("Administrator")
		employee = frappe.get_doc("Employee", self.employee_name)
		if not employee.relieving_date:
			employee.relieving_date = today()
		employee.status = "Left"
		employee.save(ignore_permissions=True)

		self.assertEqual(frappe.db.get_value(CHANGE_DOCTYPE, change["name"], "status"), "Withdrawn")
		self.assertIsNone(self._open_change(timesheet))

		# This class shares one Employee row across methods in a single
		# transaction; restore it so the methods after this one keep their
		# Active employee.
		employee.status = "Active"
		employee.relieving_date = None
		employee.save(ignore_permissions=True)

	def test_with_no_manager_the_request_routes_to_hr(self):
		hr_employee, hr_user = make_test_hr_manager_employee()
		company = frappe.db.get_value("Employee", self.employee_name, "company")
		self.assertEqual(frappe.db.get_value("Employee", hr_employee, "company"), company)

		timesheet = self._approved_week()
		# The manager exists while the week is decided, then goes away -- the
		# change request raised afterwards routes to HR (R10).
		frappe.set_user("Administrator")
		frappe.db.set_value("Employee", self.employee_name, "reports_to", None)
		frappe.set_user(self.employee_user)
		change = self._raise()
		self.assertIsNone(change["approver_user"], "no manager routes it to HR")

		# HR of the company sees and decides it (R10).
		frappe.set_user(hr_user)
		queue = get_my_approvals()
		row = next(row for row in queue["pending"] if row["name"] == change["name"])
		self.assertEqual(row["kind"], "change")
		detail = get_approval_detail("change", change["name"])
		self.assertIn("Accept", detail["actions"])
		result = self._act(change["name"], "Accept", hr_user)
		self.assertEqual(result["state"], "Accepted")
		self.assertEqual(frappe.db.get_value("Timesheet", timesheet, "docstatus"), 2)

	def test_hr_of_another_company_cannot_see_or_decide_it(self):
		# An HR Manager anchored to another company, with their own Employee
		# and User Permission there.
		other_company = ensure_baseline_company()
		make_test_user("other-company-hr@helixhr.test", other_company)
		frappe.set_user("Administrator")
		user = frappe.get_doc("User", "other-company-hr@helixhr.test")
		if not any(row.role == "HR Manager" for row in user.roles):
			user.append("roles", {"doctype": "Has Role", "role": "HR Manager"})
			user.save(ignore_permissions=True)
		other_hr_employee = frappe.db.get_value("Employee", {"user_id": "other-company-hr@helixhr.test"})
		if not frappe.db.exists(
			"User Permission",
			{"user": "other-company-hr@helixhr.test", "allow": "Employee", "for_value": other_hr_employee},
		):
			frappe.get_doc(
				{
					"doctype": "User Permission",
					"user": "other-company-hr@helixhr.test",
					"allow": "Employee",
					"for_value": other_hr_employee,
				}
			).insert(ignore_permissions=True)

		self._approved_week()
		# The manager goes away before the raise, so the request routes to HR.
		frappe.db.set_value("Employee", self.employee_name, "reports_to", None)
		frappe.set_user(self.employee_user)
		change = self._raise()

		frappe.set_user("other-company-hr@helixhr.test")
		queue = get_my_approvals()
		self.assertNotIn(change["name"], {row["name"] for row in queue["pending"]})
		with self.assertRaises(frappe.PermissionError):
			get_approval_detail("change", change["name"])

	def test_hr_cannot_decide_their_own_request(self):
		hr_employee, hr_user = make_test_hr_manager_employee()
		project = make_test_project(f"{self.id().split('.')[-1]}-hr", users=[hr_user])
		monday = add_days(self.monday, 3500)
		row = [{"date": str(monday), "project": project, "task": "", "hours": 2, "note": ""}]

		# A manager is needed to carry the week to Approved; it goes away
		# before the raise, so the request routes to HR -- themselves.
		frappe.set_user("Administrator")
		frappe.db.set_value("Employee", hr_employee, "reports_to", self.manager_name)
		frappe.set_user(hr_user)
		name = save_my_week(str(monday), json.dumps(row))
		apply_workflow({"doctype": "Timesheet", "name": name}, "Submit")
		frappe.set_user(self.manager_user)
		apply_workflow({"doctype": "Timesheet", "name": name}, "Approve")
		frappe.set_user("Administrator")
		frappe.db.set_value("Employee", hr_employee, "reports_to", None)
		frappe.set_user(hr_user)

		change = raise_timesheet_change(str(monday), "My own week needs a correction")
		self.assertIsNone(change["approver_user"], "no manager routes it to HR")

		with self.assertRaises(frappe.PermissionError):
			self._act(change["name"], "Accept", hr_user)

	# --- the queue (R13) -----------------------------------------------------------

	def test_the_change_request_is_a_distinct_queue_item(self):
		self._approved_week()
		change = self._raise()

		frappe.set_user(self.manager_user)
		queue = get_my_approvals()
		row = next(row for row in queue["pending"] if row["name"] == change["name"])
		self.assertEqual(row["kind"], "change")
		self.assertEqual(row["comment"], COMMENT)
		self.assertEqual(row["total_hours"], 4)

		detail = get_approval_detail("change", change["name"])
		self.assertEqual(detail["actions"], ["Accept", "Decline"])
		self.assertEqual(detail["comment"], COMMENT)
		self.assertEqual(detail["total_hours"], 4)
