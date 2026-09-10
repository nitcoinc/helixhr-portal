"""The attendance-request approval -- fixture, guards, shares, notifications
and the employee-facing API (P3-R12 to P3-R19, P3-AE7 to P3-AE10, P3-AE8a,
P3-AE8b, P3-AE14; P4-R2 to R6, R8).

P4-U1 collapsed the two-step approval to one: the manager's Approve is now the
submit that writes Attendance, and HR decides only what a manager handed over
(Send to HR) or what HR raised itself. The state that used to be called
Rejected and *meant* sent back is now Sent Back; Rejected is a final no the
employee may only remove (P4-KTD1, P4-KTD3).

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
	DECISION_REASON_FIELD,
	attendance_request_subject,
)
from helixhr.tests.utils import (
	EMPLOYEE_USER,
	MANAGER_USER,
	NIGHT_SHIFT_TYPE,
	OTHER_MANAGER_USER,
	ensure_holiday_list_assignment,
	ensure_holiday_list_assignment_from,
	ensure_hr_manager_user,
	ensure_test_holiday,
	ensure_test_shift_type,
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
		# The preview resolves the holiday list per date (P3-U9), so the
		# window this method books needs an assignment covering it -- not just
		# the current-year one every other suite reads.
		ensure_holiday_list_assignment_from(self.company, self.start)
		self.created = []
		self.attendance = []
		self.holiday_lists = []

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
		for list_name in self.holiday_lists:
			# Explicit, like test_api_holidays' own cleanup: one leftover
			# assignment decides the next test's holiday list.
			for name in frappe.get_all(
				"Holiday List Assignment", filters={"holiday_list": list_name}, pluck="name"
			):
				frappe.db.set_value("Holiday List Assignment", name, "docstatus", 2)
				frappe.delete_doc(
					"Holiday List Assignment", name, force=True, ignore_permissions=True
				)
			if frappe.db.exists("Holiday List", list_name):
				frappe.delete_doc("Holiday List", list_name, force=True, ignore_permissions=True)

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
		"""P4-R5: the only way a request reaches HR is a manager handing it
		over. The manager's Approve is the final decision now."""
		name = self._pending_manager(**kwargs)
		frappe.set_user(MANAGER_USER)
		apply_workflow(_ref(name), "Send to HR")
		return name

	def _reason(self, name, text):
		"""What `act_on_approval` writes before it moves the record (P4-R9):
		the approver's reason on the record itself, not a comment (P4-KTD7a).
		Set as Administrator because the field sits at permlevel 1.
		"""
		previous = frappe.session.user
		frappe.set_user("Administrator")
		frappe.db.set_value(DOCTYPE, name, DECISION_REASON_FIELD, text, update_modified=False)
		frappe.set_user(previous)

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
			["Draft", "Pending Manager", "Pending HR", "Approved", "Sent Back", "Rejected"],
		)
		self.assertEqual(
			[row.doc_status for row in workflow.states], ["0", "0", "0", "1", "0", "0"]
		)

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
		self.assertEqual(
			(shares[0].write, shares[0].submit),
			(1, 1),
			"P4-KTD5: the manager's Approve is the submit, so the share carries it",
		)
		self.assertEqual(len(self._logs(name)), logs_before, "nobody tells you what you just sent")

		# One step: the manager's Approve submits the request and writes the
		# Attendance rows, under a role-Employee session (P4-R6, P4-KTD5).
		frappe.set_user(MANAGER_USER)
		apply_workflow(_ref(name), "Approve")
		self.assertEqual(self._shares(name), [], "the share ends with the manager's decision")
		logs = self._logs(name)
		self.assertEqual(len(logs), logs_before + 1)
		state = self._state(name)
		self.assertEqual((state.workflow_state, state.docstatus), ("Approved", 1))
		rows = self._attendance_rows(name)
		self.assertEqual(sorted(str(row.attendance_date) for row in rows), [self._day(), self._day(1)])
		self.assertEqual({row.status for row in rows}, {"Work From Home"})
		self.assertEqual({row.docstatus for row in rows}, {1})
		self.assertIn("counts", self._logs(name)[-1].subject)
		self.assertEqual(
			self._logs(name)[-1].subject,
			attendance_request_subject("Approved", self._day(), self._day(1)),
		)

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

	def test_the_preview_reads_the_list_in_force_over_each_half_of_the_range(self):
		"""P3-U9. The holiday list is resolved per date, through Holiday List
		Assignment, and not once as of today.

		Before this fix the preview asked `get_holiday_list_for_employee` with
		no `as_on`, so a range straddling an assignment change was previewed
		entirely against today's list: the new list's holiday was offered as a
		day to mark, and the old list's rows were honoured for days it no
		longer covered. The three days below tell those two answers apart --
		the counts alone do not.
		"""
		frappe.set_user("Administrator")
		# In force over the first half (assigned from the window's start by
		# `ensure_holiday_list_assignment_from`), with a holiday in each half.
		ensure_test_holiday(self._day(1))
		ensure_test_holiday(self._day(8))

		handover = "_Test Handover Holiday List"
		self.holiday_lists.append(handover)
		if frappe.db.exists("Holiday List", handover):
			frappe.delete_doc("Holiday List", handover, force=True, ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Holiday List",
				"holiday_list_name": handover,
				"from_date": self._day(),
				"to_date": self._day(20),
				"holidays": [{"holiday_date": self._day(7), "description": "Handover holiday"}],
			}
		).insert(ignore_permissions=True)
		# ...and in force from the middle of the window onwards. A company
		# assignment, not an employee one: an employee assignment would win
		# outright at both ends of the range and there would be no split to
		# resolve.
		assignment = frappe.get_doc(
			{
				"doctype": "Holiday List Assignment",
				"applicable_for": "Company",
				"assigned_to": self.company,
				"holiday_list": handover,
				"from_date": self._day(5),
			}
		)
		assignment.insert(ignore_permissions=True)
		assignment.submit()

		frappe.set_user(EMPLOYEE_USER)
		preview = get_attendance_request_preview(self._day(), self._day(9))
		self.assertTrue(preview["known"])
		# The footnote names the list in force where the range starts.
		self.assertEqual(preview["holiday_list"], "_Test Holiday List")
		buckets = {day["date"]: day["bucket"] for day in preview["days"]}
		self.assertEqual(buckets[self._day(1)], "skipped")  # the old list's day
		self.assertEqual(buckets[self._day(7)], "skipped")  # the new list's day
		# The old list's row on a day it no longer covers is *not* a holiday:
		# reading one list for the whole range is exactly the bug.
		self.assertEqual(buckets[self._day(8)], "mark")
		self.assertEqual(preview["skipped"]["holiday"], 2)
		self.assertEqual(preview["mark"], 8)
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

	def test_the_overwrite_gate_holds_on_the_raw_submit_routes(self):
		"""P4-KTD5 on every route, not only the portal's.

		The manager's DocShare carries `submit=1`, so `apply_workflow` and
		`frappe.client.submit` both reach the submit HRMS turns into
		Attendance -- and HRMS rewrites an existing row in place. The refusal
		therefore lives in `attendance_request_before_submit`, which is the
		one choke point all three routes share.
		"""
		from frappe.client import submit as client_submit

		name = self._pending_manager(days=2)
		# Auto-attendance marking a day Present *after* the send: the case the
		# send-time preview cannot have seen.
		self._seed_attendance(1, "Present")

		frappe.set_user(MANAGER_USER)
		with self.assertRaises(frappe.ValidationError) as refused:
			apply_workflow(_ref(name), "Approve")
		self.assertIn("send this to HR instead", str(refused.exception))

		frappe.set_user(MANAGER_USER)
		with self.assertRaises(frappe.ValidationError) as refused:
			client_submit(frappe.get_doc(DOCTYPE, name).as_dict())
		self.assertIn("send this to HR instead", str(refused.exception))

		frappe.set_user("Administrator")
		self.assertEqual(self._state(name).docstatus, 0)
		self.assertEqual(self._state(name).workflow_state, "Pending Manager")
		self.assertEqual(self._attendance_rows(name), [])

		# HR is not gated: they can read the calendar and weigh the rewrite.
		frappe.set_user(self.hr_user)
		apply_workflow(_ref(name), "Approve")
		frappe.set_user("Administrator")
		self.assertEqual(self._state(name).docstatus, 1)

	def test_an_unanswerable_preview_is_treated_as_an_overwrite(self):
		"""A preview that cannot answer (`known` false -- no holiday list
		resolves) used to report `overwrite: 0` and let the Approve through,
		leaving HRMS's own `is_holiday(raise_exception=True)` to throw a raw
		Frappe error a few lines later. Same answer as `can_send` gives at
		send time: this one is HR's call."""
		name = self._pending_manager()

		frappe.set_user(MANAGER_USER)
		with patch("helixhr.api.get_holiday_list_for_employee", return_value=None):
			with self.assertRaises(frappe.ValidationError) as refused:
				apply_workflow(_ref(name), "Approve")
		self.assertIn("Send it to HR instead", str(refused.exception))

		frappe.set_user("Administrator")
		self.assertEqual(self._state(name).docstatus, 0)
		self.assertEqual(self._attendance_rows(name), [])

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

		# P4-U1: HR *may* now submit from Pending Manager (HR can decide
		# anything), but a colleague who is neither the approver nor HR
		# cannot -- the branch `attendance_request_before_submit` refuses.
		frappe.set_user("Administrator")
		make_test_user(OTHER_MANAGER_USER, self.company)
		frappe.set_user(OTHER_MANAGER_USER)
		with self.assertRaises(frappe.PermissionError):
			client_submit(frappe.get_doc(DOCTYPE, name).as_dict())
		self.assertEqual(self._state(name).docstatus, 0)
		self.assertEqual(self._state(name).workflow_state, "Pending Manager")

		frappe.set_user(MANAGER_USER)
		apply_workflow(_ref(name), "Send to HR")

		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises((frappe.ValidationError, frappe.PermissionError)):
			client_set_value(DOCTYPE, name, "explanation", "changed")
		with self.assertRaises((frappe.ValidationError, frappe.PermissionError)):
			client_delete(DOCTYPE, name)
		self.assertTrue(frappe.db.exists(DOCTYPE, name))

		# HR's own raw submit from Pending HR is the legitimate step for a
		# request a manager handed over.
		frappe.set_user(self.hr_user)
		client_submit(frappe.get_doc(DOCTYPE, name).as_dict())
		state = self._state(name)
		self.assertEqual((state.workflow_state, state.docstatus), ("Approved", 1))

	def test_an_hr_manager_who_is_the_requester_cannot_confirm_it(self):
		"""P3-KTD6: never your own request, at either step. The workflow
		fixture's `user_id != session.user` condition covers the transition
		route only, and the legitimate final step is a plain submit."""
		from frappe.client import submit as client_submit

		name = self._pending_hr()

		frappe.set_user("Administrator")
		employee_login = frappe.get_doc("User", EMPLOYEE_USER)
		employee_login.add_roles("HR Manager")
		frappe.clear_cache(user=EMPLOYEE_USER)
		try:
			frappe.set_user(EMPLOYEE_USER)
			with self.assertRaises(frappe.PermissionError):
				client_submit(frappe.get_doc(DOCTYPE, name).as_dict())
			self.assertEqual(self._state(name).docstatus, 0)
		finally:
			frappe.set_user("Administrator")
			employee_login.reload()
			employee_login.remove_roles("HR Manager")
			frappe.clear_cache(user=EMPLOYEE_USER)

		# Another HR Manager still confirms it.
		frappe.set_user(self.hr_user)
		client_submit(frappe.get_doc(DOCTYPE, name).as_dict())
		state = self._state(name)
		self.assertEqual((state.workflow_state, state.docstatus), ("Approved", 1))

	def test_a_pending_requests_shift_is_frozen_once_it_has_one(self):
		"""P3-R17a. HRMS's `validate_shifts` fills `shift` only while it is
		empty and never checks the Shift Type is one the employee was
		assigned, so an unconditional exemption let either party PUT any
		shift onto a pending request -- and HR's confirmation then wrote
		Attendance against it."""
		from frappe.client import set_value as client_set_value

		frappe.set_user("Administrator")
		assigned = ensure_test_shift_type()
		other = ensure_test_shift_type(name=NIGHT_SHIFT_TYPE)
		name = self._pending_manager()

		# The empty -> filled case HRMS needs is still allowed.
		frappe.set_user("Administrator")
		frappe.db.set_value(DOCTYPE, name, "shift", None, update_modified=False)
		frappe.set_user(EMPLOYEE_USER)
		client_set_value(DOCTYPE, name, "shift", assigned)
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, "shift"), assigned)

		# Filled -> something else is not, from either side.
		for user in (EMPLOYEE_USER, MANAGER_USER):
			frappe.set_user(user)
			with self.assertRaises((frappe.ValidationError, frappe.PermissionError), msg=user):
				client_set_value(DOCTYPE, name, "shift", other)
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, "shift"), assigned)

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
		self._reason(name, "Log the hours in the timesheet instead")
		frappe.set_user(MANAGER_USER)
		doc = frappe.get_doc(DOCTYPE, name)
		doc.add_comment("Comment", "Log the hours in the timesheet instead")
		apply_workflow(_ref(name), "Send Back")

		self.assertEqual(self._state(name).workflow_state, "Sent Back")
		self.assertEqual(self._shares(name), [])
		log = self._logs(name)[-1]
		self.assertEqual(log.subject, attendance_request_subject("Sent Back", self._day(), self._day()))
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

	def test_the_employees_own_comment_is_never_the_reason_it_came_back(self):
		"""P3-KTD9. "The newest comment on the document" was whatever the
		employee last typed on their own sent-back request."""
		name = self._pending_manager()
		frappe.set_user(MANAGER_USER)
		doc = frappe.get_doc(DOCTYPE, name)
		doc.add_comment("Comment", "Log the hours in the timesheet instead")
		apply_workflow(_ref(name), "Send Back")

		frappe.set_user(EMPLOYEE_USER)
		frappe.get_doc(DOCTYPE, name).add_comment("Comment", "Will do, sorry")

		listed = get_my_attendance_requests()
		mine = next(row for row in listed["requests"] if row["name"] == name)
		self.assertEqual(mine["reason_sent_back"], "Log the hours in the timesheet instead")
		item = next(
			i
			for i in get_dashboard()["needs_you"]["items"]
			if i["kind"] == "attendance_request_rejected" and i["to"]["params"]["name"] == name
		)
		self.assertEqual(item["detail"], "Log the hours in the timesheet instead")

	def test_a_managers_rejection_without_a_reason_does_not_name_hr(self):
		"""The fallback sentence is state-neutral: either step can send a
		request back (P3-KTD9)."""
		name = self._pending_manager()
		frappe.set_user(EMPLOYEE_USER)
		# The employee's own comment is not a reason either, whichever
		# route reads it.
		frappe.get_doc(DOCTYPE, name).add_comment("Comment", "Any news?")

		frappe.set_user(MANAGER_USER)
		apply_workflow(_ref(name), "Send Back")

		log = self._logs(name)[-1]
		self.assertIn("was sent back", log.subject)
		self.assertIn(ATTENDANCE_REQUEST_SENT_BACK_FALLBACK, log.description)
		self.assertNotIn("Any news?", log.description)
		self.assertNotIn("HR sent", log.description)

	def test_hr_raised_request_notifies_the_employee_not_the_owner(self):
		name = self._hr_request()
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, "owner"), self.hr_user)

		frappe.set_user(self.hr_user)
		apply_workflow(_ref(name), "Approve")  # Draft -> Approved (P4-KTD1)
		state = self._state(name)
		self.assertEqual((state.workflow_state, state.docstatus), ("Approved", 1))
		self.assertIn("counts", self._logs(name)[-1].subject)
		self.assertEqual(self._logs(name, for_user=self.hr_user), [])

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
		apply_workflow(_ref(name), "Send to HR")
		frappe.set_user(EMPLOYEE_USER)
		waiting = get_dashboard()["needs_you"]["waiting"]
		item = next(
			i
			for i in waiting
			if i["kind"] == "attendance_request_waiting" and i["to"]["params"]["name"] == name
		)
		self.assertEqual(item["owner"], "hr")

	# --- P4-U1: the four outcomes, single step -------------------------------

	def test_send_to_hr_hands_the_request_over(self):
		"""P4-R5. Send to HR is a routing act, not a decision: the request
		leaves the manager's hands (and their share goes with it) and only HR
		can move it afterwards."""
		name = self._pending_manager()
		frappe.set_user(MANAGER_USER)
		apply_workflow(_ref(name), "Send to HR")

		self.assertEqual(self._state(name).workflow_state, "Pending HR")
		self.assertEqual(self._shares(name), [], "the manager's share ends with the hand-over")
		self.assertIn("is with HR", self._logs(name)[-1].subject)

		frappe.set_user(MANAGER_USER)
		with self.assertRaises((frappe.PermissionError, WorkflowTransitionError)):
			apply_workflow(_ref(name), "Approve")

		frappe.set_user(self.hr_user)
		apply_workflow(_ref(name), "Approve")
		state = self._state(name)
		self.assertEqual((state.workflow_state, state.docstatus), ("Approved", 1))
		self.assertEqual(len(self._attendance_rows(name)), 1)

	def test_reject_is_final_and_its_reason_outlives_the_row(self):
		"""P4-R4, P4-KTD3. Rejected has no way back to Draft, but the row is
		removable so the dates are not blocked for ever -- and the reason
		travels into the Deleted Document snapshot because it is a field of
		the record, not a comment."""
		name = self._pending_manager()
		self._reason(name, "Not on the WFH roster that week")
		frappe.set_user(MANAGER_USER)
		apply_workflow(_ref(name), "Reject")

		self.assertEqual(self._state(name).workflow_state, "Rejected")
		self.assertEqual(self._shares(name), [])
		log = self._logs(name)[-1]
		self.assertEqual(log.subject, attendance_request_subject("Rejected", self._day(), self._day()))
		self.assertIn("was rejected", log.subject)
		self.assertIn("Not on the WFH roster that week", log.description)

		# No Edit transition out of Rejected, for anybody.
		for user in (EMPLOYEE_USER, MANAGER_USER, self.hr_user):
			frappe.set_user(user)
			with self.assertRaises((frappe.PermissionError, WorkflowTransitionError), msg=user):
				apply_workflow(_ref(name), "Edit")
		self.assertEqual(self._state(name).workflow_state, "Rejected")

		frappe.set_user(EMPLOYEE_USER)
		self.assertEqual(withdraw_my_attendance_request(name)["withdrawn"], True)
		self.assertFalse(frappe.db.exists(DOCTYPE, name))

		frappe.set_user("Administrator")
		snapshot = frappe.db.get_value(
			"Deleted Document", {"deleted_doctype": DOCTYPE, "deleted_name": name}, "data"
		)
		self.assertIn("Not on the WFH roster that week", snapshot or "")

		# ...and HRMS accepts a new request over the same dates.
		replacement = self._create()
		self.assertEqual(replacement["workflow_state"], "Draft")

	def test_nobody_decides_their_own_request_on_any_route(self):
		"""P4-R8. A `reports_to` pointing at oneself reaches the manager
		branch of `attendance_request_before_submit`, which is why the
		self-request refusal runs before the branch table -- and why the
		transitions carry `allow_self_approval=0`."""
		from frappe.client import submit as client_submit

		name = self._pending_manager()
		frappe.set_user("Administrator")
		frappe.db.set_value("Employee", self.employee_name, "reports_to", self.employee_name)
		try:
			frappe.set_user(EMPLOYEE_USER)
			# Frappe's own `allow_self_approval=0` check throws a plain
			# ValidationError ("Self approval is not allowed") before the
			# condition is even evaluated.
			with self.assertRaises(
				(frappe.ValidationError, frappe.PermissionError, WorkflowTransitionError)
			):
				apply_workflow(_ref(name), "Approve")
			with self.assertRaises(frappe.PermissionError):
				client_submit(frappe.get_doc(DOCTYPE, name).as_dict())
			self.assertEqual(self._state(name).docstatus, 0)
		finally:
			frappe.set_user("Administrator")
			frappe.db.set_value("Employee", self.employee_name, "reports_to", self.manager_name)

	def test_an_hr_manager_cannot_approve_their_own_request_through_the_workflow(self):
		"""The transition route, beside the raw-submit route the older test
		covers. Both matter: HR reaches Approve from Pending Manager now."""
		name = self._pending_manager()

		frappe.set_user("Administrator")
		employee_login = frappe.get_doc("User", EMPLOYEE_USER)
		employee_login.add_roles("HR Manager")
		frappe.clear_cache(user=EMPLOYEE_USER)
		try:
			frappe.set_user(EMPLOYEE_USER)
			with self.assertRaises(
				(frappe.ValidationError, frappe.PermissionError, WorkflowTransitionError)
			):
				apply_workflow(_ref(name), "Approve")
			self.assertEqual(self._state(name).docstatus, 0)
		finally:
			frappe.set_user("Administrator")
			employee_login.reload()
			employee_login.remove_roles("HR Manager")
			frappe.clear_cache(user=EMPLOYEE_USER)

	def test_the_decision_reason_is_not_the_employees_to_write(self):
		"""P4-KTD7a. The field is at permlevel 1, so a generic save by the
		employee (or their manager) leaves whatever the approver wrote."""
		from frappe.client import set_value as client_set_value

		name = self._pending_manager()
		self._reason(name, "Ask your manager")

		for user in (EMPLOYEE_USER, MANAGER_USER):
			frappe.set_user(user)
			try:
				client_set_value(DOCTYPE, name, DECISION_REASON_FIELD, "actually it is fine")
			except (frappe.ValidationError, frappe.PermissionError):
				pass
			self.assertEqual(
				frappe.db.get_value(DOCTYPE, name, DECISION_REASON_FIELD),
				"Ask your manager",
				msg=user,
			)

	def test_a_raw_submit_is_refused_from_every_state_but_a_pending_one(self):
		"""P4-R8 / P3-R17a. `frappe.client.submit` consults no transition, so
		the stored state is the only evidence of where a request came from."""
		from frappe.client import submit as client_submit

		draft = self._create()["name"]
		frappe.set_user(self.hr_user)
		# Draft is HR's own edge (HR raised it for somebody), so this one is
		# allowed -- and is the boundary the other two sit outside.
		client_submit(frappe.get_doc(DOCTYPE, draft).as_dict())
		self.assertEqual(self._state(draft).docstatus, 1)

		sent_back = self._pending_manager(offset=1)
		frappe.set_user(MANAGER_USER)
		apply_workflow(_ref(sent_back), "Send Back")
		frappe.set_user(self.hr_user)
		with self.assertRaises(frappe.PermissionError):
			client_submit(frappe.get_doc(DOCTYPE, sent_back).as_dict())
		self.assertEqual(self._state(sent_back).docstatus, 0)

		rejected = self._pending_manager(offset=2)
		frappe.set_user(MANAGER_USER)
		apply_workflow(_ref(rejected), "Reject")
		frappe.set_user(self.hr_user)
		with self.assertRaises(frappe.PermissionError):
			client_submit(frappe.get_doc(DOCTYPE, rejected).as_dict())
		self.assertEqual(self._state(rejected).docstatus, 0)

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
		state = self._state(names[0])
		self.assertEqual((state.workflow_state, state.docstatus), ("Approved", 1))

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

		sent_back = self._pending_manager(offset=2)
		frappe.set_user(MANAGER_USER)
		frappe.get_doc(DOCTYPE, sent_back).add_comment("Comment", "no")
		apply_workflow(_ref(sent_back), "Send Back")
		frappe.set_user(EMPLOYEE_USER)
		withdraw_my_attendance_request(sent_back)
		self.assertFalse(frappe.db.exists(DOCTYPE, sent_back))

		# P4-KTD3: a final Reject is terminal for the *row*, not for the
		# dates -- so the employee may still remove it.
		rejected = self._pending_manager(offset=3)
		frappe.set_user(MANAGER_USER)
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
