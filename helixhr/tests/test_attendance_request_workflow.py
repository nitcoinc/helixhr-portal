"""P3-U5: the two-step attendance-request approval -- fixture, guards,
shares, notifications and the employee-facing API (P3-R12 to P3-R19,
P3-AE7 to P3-AE10, P3-AE8a, P3-AE8b, P3-AE14).

Every test picks its own past-year date window (hashed from the test id, and
never nearer than 450 days back, which keeps clear of test_fixtures' own
draft at -410) and removes what it wrote in tearDown, because
IntegrationTestCase does not roll back between methods here
(docs/runbook.md) and HRMS refuses two Attendance Requests of one employee
that overlap.
"""

import hashlib
from unittest.mock import patch

import frappe
from frappe.model.workflow import WorkflowTransitionError, apply_workflow, get_workflow_name
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from helixhr.api import (
	create_my_attendance_request,
	get_attendance_request_preview,
	get_dashboard,
	get_my_attendance_request,
	get_my_attendance_requests,
	send_my_attendance_request,
	withdraw_my_attendance_request,
)
from helixhr.events import (
	ATTENDANCE_REQUEST_SENT_BACK_FALLBACK,
	attendance_request_subject,
)
from helixhr.tests.utils import (
	EMPLOYEE_USER,
	MANAGER_USER,
	OTHER_MANAGER_USER,
	ensure_holiday_list_assignment,
	ensure_hr_manager_user,
	ensure_test_holiday,
	make_test_employee_and_manager,
	make_test_user,
)

DOCTYPE = "Attendance Request"
WORKFLOW = "Attendance Request Approval"


def _ref(name):
	return {"doctype": DOCTYPE, "name": name}


class TestAttendanceRequestWorkflow(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()
		frappe.db.set_value("Employee", self.employee_name, "reports_to", self.manager_name)
		self.company = frappe.db.get_value("Employee", self.employee_name, "company")
		ensure_holiday_list_assignment(self.company)
		self.hr_user = ensure_hr_manager_user()

		digest = int(hashlib.md5(self.id().encode()).hexdigest(), 16)
		# 2021..2025, one 3-day window per test method, never overlapping
		# another method's window (3 days apart per digest step).
		self.start = add_days(today(), -(450 + 3 * (digest % 600)))
		self.created = []
		self.attendance = []

	def tearDown(self):
		frappe.set_user("Administrator")
		for name in self.created:
			if not frappe.db.exists(DOCTYPE, name):
				continue
			doc = frappe.get_doc(DOCTYPE, name)
			if doc.docstatus == 1:
				doc.cancel()
			for row in frappe.get_all("Attendance", filters={"attendance_request": name}, pluck="name"):
				self.attendance.append(row)
			frappe.db.delete("DocShare", {"share_doctype": DOCTYPE, "share_name": name})
			frappe.delete_doc(DOCTYPE, name, force=True, ignore_permissions=True)
		for name in set(self.attendance):
			if not frappe.db.exists("Attendance", name):
				continue
			row = frappe.get_doc("Attendance", name)
			if row.docstatus == 1:
				row.cancel()
			frappe.delete_doc("Attendance", name, force=True, ignore_permissions=True)

	# --- helpers -----------------------------------------------------------

	def _day(self, offset=0):
		return str(add_days(self.start, offset))

	def _create(self, offset=0, days=1, reason="Work From Home", **kwargs):
		frappe.set_user(EMPLOYEE_USER)
		result = create_my_attendance_request(
			from_date=self._day(offset),
			to_date=self._day(offset + days - 1),
			reason=reason,
			explanation=f"P3-U5 {self.id().split('.')[-1]}",
			**kwargs,
		)
		self.created.append(result["name"])
		return result

	def _send(self, created):
		frappe.set_user(EMPLOYEE_USER)
		return send_my_attendance_request(created["name"], expected_modified=created["modified"])

	def _pending_manager(self, **kwargs):
		created = self._create(**kwargs)
		self._send(created)
		return created["name"]

	def _pending_hr(self, **kwargs):
		name = self._pending_manager(**kwargs)
		frappe.set_user(MANAGER_USER)
		apply_workflow(_ref(name), "Approve")
		return name

	def _hr_request(self, offset=0):
		"""A request HR raised for the employee in Desk: `owner` is the HR
		login, not the employee (P3-KTD9)."""
		frappe.set_user(self.hr_user)
		doc = frappe.get_doc(
			{
				"doctype": DOCTYPE,
				"employee": self.employee_name,
				"company": self.company,
				"from_date": self._day(offset),
				"to_date": self._day(offset),
				"reason": "On Duty",
				"explanation": "raised by HR",
			}
		).insert()
		self.created.append(doc.name)
		return doc.name

	def _seed_attendance(self, offset, status):
		frappe.set_user("Administrator")
		doc = frappe.get_doc(
			{
				"doctype": "Attendance",
				"employee": self.employee_name,
				"attendance_date": self._day(offset),
				"status": status,
				"company": self.company,
			}
		).insert(ignore_permissions=True)
		doc.submit()
		self.attendance.append(doc.name)
		return doc.name

	def _state(self, name):
		return frappe.db.get_value(DOCTYPE, name, ["workflow_state", "docstatus"], as_dict=True)

	def _shares(self, name):
		return frappe.get_all(
			"DocShare",
			filters={"share_doctype": DOCTYPE, "share_name": name},
			fields=["user", "read", "write", "submit"],
		)

	def _logs(self, name, for_user=EMPLOYEE_USER):
		return frappe.get_all(
			"Notification Log",
			filters={"for_user": for_user, "document_type": DOCTYPE, "document_name": name},
			fields=["subject", "description", "from_user"],
			order_by="creation asc",
		)

	def _attendance_rows(self, name):
		return frappe.get_all(
			"Attendance",
			filters={"attendance_request": name},
			fields=["name", "attendance_date", "status", "docstatus"],
		)

	# --- scenario 8: the fixture is live ------------------------------------

	def test_the_workflow_is_installed_and_active(self):
		self.assertEqual(get_workflow_name(DOCTYPE), WORKFLOW)
		workflow = frappe.get_doc("Workflow", WORKFLOW)
		self.assertEqual(workflow.is_active, 1)
		self.assertEqual(workflow.send_email_alert, 0)
		# P3-KTD6: the state order is the backfill order and must never move.
		self.assertEqual(
			[row.state for row in workflow.states],
			["Draft", "Pending Manager", "Pending HR", "Approved", "Rejected"],
		)
		self.assertEqual([row.doc_status for row in workflow.states], ["0", "0", "0", "1", "0"])

	# --- scenario 1 / AE7: the lifecycle ------------------------------------

	def test_full_lifecycle(self):
		created = self._create(days=2)
		self.assertEqual(created["workflow_state"], "Draft")
		self.assertEqual(created["docstatus"], 0)

		logs_before = len(self._logs(created["name"]))
		sent = self._send(created)
		name = created["name"]
		self.assertEqual(sent["workflow_state"], "Pending Manager")
		shares = self._shares(name)
		self.assertEqual([share.user for share in shares], [MANAGER_USER])
		self.assertEqual((shares[0].write, shares[0].submit), (1, 0), "the manager step is a save")
		self.assertEqual(len(self._logs(name)), logs_before, "nobody tells you what you just sent")

		frappe.set_user(MANAGER_USER)
		apply_workflow(_ref(name), "Approve")
		self.assertEqual(self._state(name).workflow_state, "Pending HR")
		self.assertEqual(self._shares(name), [], "the share ends with the manager's decision")
		self.assertEqual(self._attendance_rows(name), [], "no Attendance before HR confirms")
		logs = self._logs(name)
		self.assertEqual(len(logs), logs_before + 1)
		self.assertEqual(logs[-1].subject, attendance_request_subject("Pending HR", self._day(), self._day(1)))
		self.assertIn("is with HR", logs[-1].subject)

		frappe.set_user(self.hr_user)
		apply_workflow(_ref(name), "Approve")
		state = self._state(name)
		self.assertEqual((state.workflow_state, state.docstatus), ("Approved", 1))
		rows = self._attendance_rows(name)
		self.assertEqual(sorted(str(row.attendance_date) for row in rows), [self._day(), self._day(1)])
		self.assertEqual({row.status for row in rows}, {"Work From Home"})
		self.assertEqual({row.docstatus for row in rows}, {1})
		self.assertIn("counts", self._logs(name)[-1].subject)

		frappe.set_user(EMPLOYEE_USER)
		listed = get_my_attendance_requests()
		mine = next(row for row in listed["requests"] if row["name"] == name)
		self.assertEqual(mine["workflow_state"], "Approved")
		self.assertEqual(listed["approver_name"], "Manager")
		detail = get_my_attendance_request(name)
		self.assertEqual(detail["reason"], "Work From Home")
		self.assertEqual(detail["docstatus"], 1)

	# --- scenario 2 / AE8: refusals ------------------------------------------

	def test_the_employee_cannot_submit_their_own_request(self):
		from frappe.client import submit as client_submit

		name = self._pending_manager()
		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.PermissionError):
			client_submit(frappe.get_doc(DOCTYPE, name).as_dict())
		self.assertEqual(self._state(name).docstatus, 0)

	def test_an_unrelated_manager_is_refused(self):
		frappe.set_user("Administrator")
		make_test_user(OTHER_MANAGER_USER, self.company)
		name = self._pending_manager()

		frappe.set_user(OTHER_MANAGER_USER)
		with self.assertRaises((frappe.PermissionError, WorkflowTransitionError)):
			apply_workflow(_ref(name), "Approve")
		self.assertEqual(self._state(name).workflow_state, "Pending Manager")

	def test_a_request_over_only_a_holiday_is_refused(self):
		frappe.set_user("Administrator")
		ensure_test_holiday(self._day())

		frappe.set_user(EMPLOYEE_USER)
		preview = get_attendance_request_preview(self._day(), self._day())
		self.assertTrue(preview["known"])
		self.assertEqual(preview["mark"], 0)
		self.assertEqual(preview["skipped"]["holiday"], 1)
		self.assertFalse(preview["can_send"])

		# The Draft itself is refused by HRMS (nothing to mark), so nothing
		# to send either way.
		with self.assertRaises(frappe.ValidationError):
			self._create()

	def test_a_weekly_off_is_reported_as_one(self):
		frappe.set_user("Administrator")
		ensure_test_holiday(self._day(1), weekly_off=True)

		frappe.set_user(EMPLOYEE_USER)
		preview = get_attendance_request_preview(self._day(), self._day(1))
		self.assertEqual(preview["mark"], 1)
		self.assertEqual(preview["skipped"]["weekly_off"], 1)
		self.assertEqual(preview["skipped"]["holiday"], 0)
		self.assertTrue(preview["can_send"])

	def test_a_request_over_an_existing_present_day_is_an_overwrite_and_refused(self):
		self._seed_attendance(1, "Present")

		frappe.set_user(EMPLOYEE_USER)
		preview = get_attendance_request_preview(self._day(), self._day(1))
		self.assertEqual(preview["mark"], 1)
		self.assertEqual(preview["overwrite"], 1)
		self.assertFalse(preview["can_send"])
		self.assertEqual([d["date"] for d in preview["days"] if d["bucket"] == "overwrite"], [self._day(1)])

		created = self._create(days=2)
		with self.assertRaises(frappe.ValidationError) as caught:
			self._send(created)
		self.assertIn("already", str(caught.exception))
		self.assertEqual(self._state(created["name"]).workflow_state, "Draft")
		self.assertEqual(self._shares(created["name"]), [])

	def test_no_holiday_list_means_cannot_tell(self):
		frappe.set_user(EMPLOYEE_USER)
		with patch("helixhr.api.get_holiday_list_for_employee", return_value=None):
			preview = get_attendance_request_preview(self._day(), self._day())
		self.assertFalse(preview["known"])
		self.assertFalse(preview["can_send"])
		self.assertEqual(preview["mark"], 0)

	def test_range_and_reason_are_bounded(self):
		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.ValidationError):
			get_attendance_request_preview(self._day(), self._day(31))
		with self.assertRaises(frappe.ValidationError):
			get_attendance_request_preview(self._day(1), self._day())
		with self.assertRaises(frappe.ValidationError):
			create_my_attendance_request(self._day(), self._day(), reason="Sick", explanation="x")
		with self.assertRaises(frappe.ValidationError):
			create_my_attendance_request(self._day(), self._day(), reason="On Duty", explanation="x" * 1001)

	# --- scenario 2a / AE8a: pending is read-only ----------------------------

	def test_pending_requests_are_read_only(self):
		from frappe.client import delete as client_delete
		from frappe.client import set_value as client_set_value
		from frappe.client import submit as client_submit

		name = self._pending_manager()

		for user in (EMPLOYEE_USER, MANAGER_USER):
			frappe.set_user(user)
			with self.assertRaises((frappe.ValidationError, frappe.PermissionError), msg=user):
				client_set_value(DOCTYPE, name, "from_date", self._day(1))
		self.assertEqual(str(frappe.db.get_value(DOCTYPE, name, "from_date")), self._day())

		# A colleague cannot be granted anything (P3-KTD13 delta).
		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.PermissionError):
			frappe.share.add(DOCTYPE, name, OTHER_MANAGER_USER, write=1)

		# A raw submit from Pending Manager is refused even for HR.
		frappe.set_user(self.hr_user)
		with self.assertRaises(frappe.PermissionError):
			client_submit(frappe.get_doc(DOCTYPE, name).as_dict())
		self.assertEqual(self._state(name).docstatus, 0)
		self.assertEqual(self._state(name).workflow_state, "Pending Manager")

		frappe.set_user(MANAGER_USER)
		apply_workflow(_ref(name), "Approve")

		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises((frappe.ValidationError, frappe.PermissionError)):
			client_set_value(DOCTYPE, name, "explanation", "changed")
		with self.assertRaises((frappe.ValidationError, frappe.PermissionError)):
			client_delete(DOCTYPE, name)
		self.assertTrue(frappe.db.exists(DOCTYPE, name))

		# HR's own raw submit from Pending HR is the legitimate final step.
		frappe.set_user(self.hr_user)
		client_submit(frappe.get_doc(DOCTYPE, name).as_dict())
		state = self._state(name)
		self.assertEqual((state.workflow_state, state.docstatus), ("Approved", 1))

	def test_hr_may_edit_a_pending_request(self):
		name = self._pending_manager()
		frappe.set_user(self.hr_user)
		doc = frappe.get_doc(DOCTYPE, name)
		doc.explanation = "HR clarified this"
		doc.save()
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, "explanation"), "HR clarified this")

	# --- scenario 3 / AE9: reassignment ----------------------------------------

	def test_reassignment_moves_the_share_and_deactivation_removes_it(self):
		frappe.set_user("Administrator")
		other_name = make_test_user(OTHER_MANAGER_USER, self.company)
		name = self._pending_manager()
		self.assertEqual([s.user for s in self._shares(name)], [MANAGER_USER])

		frappe.set_user("Administrator")
		employee = frappe.get_doc("Employee", self.employee_name)
		employee.reports_to = other_name
		employee.save()
		try:
			self.assertEqual([s.user for s in self._shares(name)], [OTHER_MANAGER_USER])
			frappe.set_user(MANAGER_USER)
			self.assertFalse(frappe.has_permission(DOCTYPE, "read", name))
			with self.assertRaises((frappe.PermissionError, WorkflowTransitionError)):
				apply_workflow(_ref(name), "Approve")
			frappe.set_user(OTHER_MANAGER_USER)
			self.assertTrue(frappe.has_permission(DOCTYPE, "read", name))

			frappe.set_user("Administrator")
			other = frappe.get_doc("Employee", other_name)
			other.status = "Inactive"
			other.save()
			try:
				self.assertEqual(self._shares(name), [])
				frappe.set_user(OTHER_MANAGER_USER)
				with self.assertRaises((frappe.PermissionError, WorkflowTransitionError)):
					apply_workflow(_ref(name), "Approve")
			finally:
				frappe.set_user("Administrator")
				other.reload()
				other.status = "Active"
				other.save()
		finally:
			frappe.set_user("Administrator")
			employee.reload()
			employee.reports_to = self.manager_name
			employee.save()
		self.assertEqual([s.user for s in self._shares(name)], [MANAGER_USER])

	# --- scenario 4 / AE10: sent back --------------------------------------------

	def test_sent_back_with_a_reason(self):
		name = self._pending_manager()
		frappe.set_user(MANAGER_USER)
		doc = frappe.get_doc(DOCTYPE, name)
		doc.add_comment("Comment", "Log the hours in the timesheet instead")
		apply_workflow(_ref(name), "Reject")

		self.assertEqual(self._state(name).workflow_state, "Rejected")
		self.assertEqual(self._shares(name), [])
		log = self._logs(name)[-1]
		self.assertEqual(log.subject, attendance_request_subject("Rejected", self._day(), self._day()))
		self.assertIn("was sent back", log.subject)
		self.assertIn("Log the hours in the timesheet instead", log.description)

		frappe.set_user(EMPLOYEE_USER)
		items = get_dashboard()["needs_you"]["items"]
		item = next(
			i
			for i in items
			if i["kind"] == "attendance_request_rejected" and i["to"]["params"]["name"] == name
		)
		self.assertEqual(item["detail"], "Log the hours in the timesheet instead")
		self.assertEqual(item["urgency"], "blocked")
		self.assertEqual(item["to"], {"name": "AttendanceRequestDetail", "params": {"name": name}})
		listed = get_my_attendance_requests()
		mine = next(row for row in listed["requests"] if row["name"] == name)
		self.assertEqual(mine["reason_sent_back"], "Log the hours in the timesheet instead")

		apply_workflow(_ref(name), "Edit")
		self.assertEqual(self._state(name).workflow_state, "Draft")

	def test_hr_raised_request_notifies_the_employee_not_the_owner(self):
		name = self._hr_request()
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, "owner"), self.hr_user)

		frappe.set_user(self.hr_user)
		apply_workflow(_ref(name), "Approve")  # Draft -> Pending HR (P3-KTD6)
		self.assertEqual(self._state(name).workflow_state, "Pending HR")
		self.assertIn("is with HR", self._logs(name)[-1].subject)
		self.assertEqual(self._logs(name, for_user=self.hr_user), [])

		apply_workflow(_ref(name), "Reject")  # Desk rejection without a comment
		log = self._logs(name)[-1]
		self.assertIn("was sent back", log.subject)
		self.assertIn(ATTENDANCE_REQUEST_SENT_BACK_FALLBACK, log.description)

	def test_waiting_list_names_the_owner_of_the_step(self):
		name = self._pending_manager()
		frappe.set_user(EMPLOYEE_USER)
		waiting = get_dashboard()["needs_you"]["waiting"]
		item = next(
			i
			for i in waiting
			if i["kind"] == "attendance_request_waiting" and i["to"]["params"]["name"] == name
		)
		self.assertEqual(item["owner"], "manager")

		frappe.set_user(MANAGER_USER)
		apply_workflow(_ref(name), "Approve")
		frappe.set_user(EMPLOYEE_USER)
		waiting = get_dashboard()["needs_you"]["waiting"]
		item = next(
			i
			for i in waiting
			if i["kind"] == "attendance_request_waiting" and i["to"]["params"]["name"] == name
		)
		self.assertEqual(item["owner"], "hr")

	# --- scenario 5: no manager ----------------------------------------------------

	def test_no_manager_refuses_the_send_everywhere(self):
		created = self._create()
		frappe.db.set_value("Employee", self.employee_name, "reports_to", None)
		try:
			frappe.set_user(EMPLOYEE_USER)
			with self.assertRaises(frappe.ValidationError) as caught:
				self._send(created)
			self.assertIn("Ask HR to set one", str(caught.exception))
			with self.assertRaises(frappe.ValidationError) as caught:
				apply_workflow(_ref(created["name"]), "Submit")
			self.assertIn("Ask HR to set one", str(caught.exception))
		finally:
			frappe.db.set_value("Employee", self.employee_name, "reports_to", self.manager_name)
		self.assertEqual(self._state(created["name"]).workflow_state, "Draft")
		self.assertEqual(self._shares(created["name"]), [])

	# --- scenario 5a / AE14: backfill -------------------------------------------------

	def test_backfill_by_state_order(self):
		frappe.set_user("Administrator")
		names = []
		for offset, docstatus in ((0, 0), (1, 1), (2, 2)):
			doc = frappe.get_doc(
				{
					"doctype": DOCTYPE,
					"employee": self.employee_name,
					"company": self.company,
					"from_date": self._day(offset),
					"to_date": self._day(offset),
					"reason": "On Duty",
					"explanation": "predates the workflow",
				}
			).insert()
			# What a row written before the fixture landed looks like: a
			# docstatus, no state. Raw writes on purpose -- a real submit
			# would run the workflow this test pretends does not exist yet.
			frappe.db.set_value(DOCTYPE, doc.name, {"docstatus": docstatus, "workflow_state": None})
			names.append(doc.name)
			self.created.append(doc.name)

		frappe.get_doc("Workflow", WORKFLOW).update_default_workflow_status()

		states = [frappe.db.get_value(DOCTYPE, name, "workflow_state") for name in names]
		self.assertEqual(states, ["Draft", "Approved", None])

		# The old draft is not a dead end: HR moves it on, the employee cannot.
		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(WorkflowTransitionError):
			apply_workflow(_ref(names[0]), "Approve")
		frappe.set_user(self.hr_user)
		apply_workflow(_ref(names[0]), "Approve")
		self.assertEqual(self._state(names[0]).workflow_state, "Pending HR")

	# --- scenario 6: half day --------------------------------------------------------------

	def test_half_day_on_one_day_derives_the_date(self):
		created = self._create(half_day=1)
		self.assertEqual(created["half_day"], True)
		self.assertEqual(created["half_day_date"], self._day())

		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.ValidationError):
			create_my_attendance_request(
				self._day(1), self._day(2), reason="Work From Home", explanation="x", half_day=1
			)
		with self.assertRaises(frappe.ValidationError):
			create_my_attendance_request(
				self._day(1),
				self._day(2),
				reason="Work From Home",
				explanation="x",
				half_day=1,
				half_day_date=self._day(5),
			)

	# --- scenario 7: withdraw -------------------------------------------------------------------

	def test_withdraw_follows_the_states(self):
		draft = self._create()
		frappe.set_user(EMPLOYEE_USER)
		self.assertEqual(withdraw_my_attendance_request(draft["name"])["withdrawn"], True)
		self.assertFalse(frappe.db.exists(DOCTYPE, draft["name"]))

		pending = self._pending_manager(offset=1)
		frappe.set_user(EMPLOYEE_USER)
		withdraw_my_attendance_request(pending)
		self.assertFalse(frappe.db.exists(DOCTYPE, pending))
		self.assertEqual(self._shares(pending), [])

		rejected = self._pending_manager(offset=2)
		frappe.set_user(MANAGER_USER)
		frappe.get_doc(DOCTYPE, rejected).add_comment("Comment", "no")
		apply_workflow(_ref(rejected), "Reject")
		frappe.set_user(EMPLOYEE_USER)
		withdraw_my_attendance_request(rejected)
		self.assertFalse(frappe.db.exists(DOCTYPE, rejected))

	def test_withdraw_is_refused_once_with_hr(self):
		name = self._pending_hr()
		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.ValidationError) as caught:
			withdraw_my_attendance_request(name)
		self.assertIn("Ask HR", str(caught.exception))
		self.assertTrue(frappe.db.exists(DOCTYPE, name))

		frappe.set_user(self.hr_user)
		apply_workflow(_ref(name), "Approve")
		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.ValidationError):
			withdraw_my_attendance_request(name)
		# And the generic route agrees (P3-R17a).
		with self.assertRaises((frappe.ValidationError, frappe.PermissionError)):
			frappe.delete_doc(DOCTYPE, name)
		self.assertTrue(frappe.db.exists(DOCTYPE, name))

	def test_withdraw_refuses_somebody_elses_request(self):
		frappe.set_user("Administrator")
		make_test_user(OTHER_MANAGER_USER, self.company)
		name = self._pending_manager()
		frappe.set_user(OTHER_MANAGER_USER)
		with self.assertRaises(frappe.PermissionError):
			withdraw_my_attendance_request(name)
		with self.assertRaises(frappe.PermissionError):
			get_my_attendance_request(name)

	# --- scenario 9: cancel ------------------------------------------------------------------------

	def test_cancel_of_an_approved_request_removes_only_its_own_rows(self):
		unrelated = self._seed_attendance(2, "Present")
		name = self._pending_hr(days=2)
		frappe.set_user(self.hr_user)
		apply_workflow(_ref(name), "Approve")
		own = self._attendance_rows(name)
		self.assertEqual(len(own), 2)

		frappe.get_doc(DOCTYPE, name).cancel()
		state = self._state(name)
		self.assertEqual((state.workflow_state, state.docstatus), ("Approved", 2))
		self.assertEqual(self._shares(name), [])
		self.assertEqual({row.docstatus for row in self._attendance_rows(name)}, {2})
		self.assertEqual(frappe.db.get_value("Attendance", unrelated, "docstatus"), 1)

	# --- AE8b: Absent is replaceable -----------------------------------------------------------------

	def test_absent_is_replaced_not_overwritten(self):
		absent = self._seed_attendance(0, "Absent")

		frappe.set_user(EMPLOYEE_USER)
		preview = get_attendance_request_preview(self._day(), self._day())
		self.assertEqual(preview["mark"], 1)
		self.assertEqual(preview["replaces_absent"], 1)
		self.assertEqual(preview["overwrite"], 0)
		self.assertTrue(preview["can_send"])

		name = self._pending_hr()
		frappe.set_user(self.hr_user)
		apply_workflow(_ref(name), "Approve")
		row = frappe.db.get_value("Attendance", absent, ["status", "attendance_request"], as_dict=True)
		self.assertEqual(row.status, "Work From Home")
		self.assertEqual(row.attendance_request, name)

	# --- list shape ----------------------------------------------------------------------------------

	def test_list_is_own_bounded_and_paged(self):
		first = self._create(offset=0)
		second = self._create(offset=1)
		frappe.set_user(EMPLOYEE_USER)
		page = get_my_attendance_requests(limit=1)
		self.assertEqual(len(page["requests"]), 1)
		self.assertGreaterEqual(page["total"], 2)
		self.assertEqual(page["limit"], 1)
		names = {row["name"] for row in get_my_attendance_requests(limit=500)["requests"]}
		self.assertTrue({first["name"], second["name"]} <= names)
		for row in get_my_attendance_requests()["requests"]:
			self.assertNotIn("employee", row)
