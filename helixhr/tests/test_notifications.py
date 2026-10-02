import hashlib
import uuid

import frappe
from frappe.model.workflow import apply_workflow
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from helixhr.events import HR_REPLY_SUBJECT_PREFIX
from helixhr.tests.utils import (
	EMPLOYEE_USER,
	MANAGER_USER,
	ensure_holiday_list_assignment,
	ensure_hr_manager_user,
	ensure_leave_allocation,
	ensure_test_email_account,
	make_test_employee_and_manager,
	make_test_it_user,
	make_test_user,
)
from helixhr.utils import get_week_bounds


class TestNotifications(IntegrationTestCase):
	def setUp(self):
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()
		frappe.db.set_value("Employee", self.employee_name, "reports_to", self.manager_name)
		frappe.db.set_value("Employee", self.employee_name, "leave_approver", frappe.session.user)

	def tearDown(self):
		frappe.set_user("Administrator")

	def _unread_count(self, user):
		return frappe.db.count("Notification Log", {"for_user": user, "read": 0})

	def test_leave_approval_notifies_the_employee(self):
		ensure_leave_allocation(self.employee_name, "Casual Leave", 5)

		frappe.set_user(EMPLOYEE_USER)
		leave = frappe.get_doc(
			{
				"doctype": "Leave Application",
				"employee": self.employee_name,
				"leave_type": "Casual Leave",
				"from_date": add_days(today(), 1),
				"to_date": add_days(today(), 1),
				"description": "test",
				"leave_approver": frappe.session.user,
			}
		)
		leave.insert()

		before = self._unread_count(EMPLOYEE_USER)

		frappe.set_user("Administrator")
		leave.reload()
		leave.status = "Approved"
		leave.save(ignore_permissions=True)

		after = self._unread_count(EMPLOYEE_USER)
		self.assertGreater(after, before)

		log = frappe.get_last_doc(
			"Notification Log", filters={"for_user": EMPLOYEE_USER, "document_type": "Leave Application"}
		)
		self.assertIn("Approved", log.subject)

	def _leave_subjects(self, name):
		"""The employee's own notifications on this application -- not P5-U10's
		manager arrival notice, which lands in the same document_type/
		document_name bucket and would otherwise double-count here."""
		return frappe.get_all(
			"Notification Log",
			filters={"for_user": EMPLOYEE_USER, "document_type": "Leave Application", "document_name": name},
			pluck="subject",
		)

	def _open_leave(self, offset):
		"""One unsubmitted leave of the employee's own, filed by them."""
		ensure_leave_allocation(self.employee_name, "Casual Leave", 5)
		date = add_days(today(), offset)
		frappe.set_user("Administrator")
		for existing in frappe.get_all(
			"Leave Application",
			filters={"employee": self.employee_name, "from_date": str(date)},
			pluck="name",
		):
			frappe.delete_doc("Leave Application", existing, force=True, ignore_permissions=True)

		frappe.set_user(EMPLOYEE_USER)
		doc = frappe.get_doc(
			{
				"doctype": "Leave Application",
				"employee": self.employee_name,
				"leave_type": "Casual Leave",
				"from_date": str(date),
				"to_date": str(date),
				"description": "test",
				"leave_approver": MANAGER_USER,
			}
		)
		doc.insert()
		frappe.set_user("Administrator")
		return doc

	def test_a_send_back_and_a_final_reject_are_told_apart_by_docstatus(self):
		"""P4-U2 / P4-KTD9. Both outcomes write `status = "Rejected"` -- one
		unsubmitted, one submitted -- and a Notification watches one field,
		so `docstatus` is what the wording keys on. A submit does raise Value
		Change (`run_post_save_methods` runs `on_change` after it), which is
		why one fixture covers both.
		"""
		sent_back = self._open_leave(3)
		sent_back.reload()
		sent_back.status = "Rejected"
		sent_back.save(ignore_permissions=True)

		subjects = self._leave_subjects(sent_back.name)
		self.assertEqual(len(subjects), 1)
		self.assertIn("Sent back", subjects[0])

		rejected = self._open_leave(5)
		rejected.reload()
		rejected.status = "Rejected"
		rejected.submit()

		subjects = self._leave_subjects(rejected.name)
		self.assertEqual(len(subjects), 1)
		self.assertIn("Rejected", subjects[0])
		self.assertNotIn("Sent back", subjects[0])

	def test_a_leave_sent_to_hr_tells_the_employee_it_is_waiting_for_hr(self):
		"""The stage is a second field, so it needs its own fixture -- and
		`db_set`, which is how a permlevel-1 field is written, still runs
		`on_change`, so Value Change fires on it."""
		leave = self._open_leave(7)

		leave.db_set("helixhr_stage", "HR")

		subjects = self._leave_subjects(leave.name)
		self.assertEqual(len(subjects), 1)
		self.assertIn("waiting for HR", subjects[0])

	def test_timesheet_rejection_notifies_the_users_field_with_the_comment_available(self):
		company = frappe.db.get_value("Employee", self.employee_name, "company")
		ensure_holiday_list_assignment(company)

		project = frappe.db.get_value("Project", {"project_name": "_Test Notif Project"}, "name")
		if not project:
			project = frappe.get_doc(
				{"doctype": "Project", "project_name": "_Test Notif Project", "status": "Open", "company": company}
			).insert(ignore_permissions=True).name
		if not frappe.db.exists("User Permission", {"user": EMPLOYEE_USER, "allow": "Project", "for_value": project}):
			frappe.get_doc(
				{"doctype": "User Permission", "user": EMPLOYEE_USER, "allow": "Project", "for_value": project}
			).insert(ignore_permissions=True)

		# A hashed week offset, not literally "this week" -- other test
		# files (test_api_timesheet.py, test_api_approvals.py) each pick
		# their own hashed week too, and "this week" (offset 0) is exactly
		# as likely to collide with one of them as any other week, which
		# happened while writing this test (two overlapping Timesheets in
		# the same run). See test_api_timesheet.py's setUp for the same
		# pattern and why it hashes the full test id, not just the method
		# name.
		digest = int(hashlib.md5(self.id().encode()).hexdigest(), 16)
		monday, _ = get_week_bounds(add_days(today(), (digest % 200000) * 7))
		frappe.set_user(EMPLOYEE_USER)
		doc = frappe.get_doc(
			{
				"doctype": "Timesheet",
				"employee": self.employee_name,
				"company": company,
				"start_date": str(monday),
				"end_date": str(monday),
				"time_logs": [
					{
						"project": project,
						"hours": 4,
						"activity_type": "General",
						"from_time": f"{monday} 09:00:00",
						"to_time": f"{monday} 13:00:00",
					}
				],
			}
		)
		doc.user = EMPLOYEE_USER
		doc.insert()
		apply_workflow({"doctype": "Timesheet", "name": doc.name}, "Submit")

		before = self._unread_count(EMPLOYEE_USER)

		frappe.set_user(MANAGER_USER)
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Comment",
				"reference_doctype": "Timesheet",
				"reference_name": doc.name,
				"content": "Please add a task",
			}
		).insert(ignore_permissions=True)
		apply_workflow({"doctype": "Timesheet", "name": doc.name}, "Send Back")

		after = self._unread_count(EMPLOYEE_USER)
		self.assertGreater(after, before)

		frappe.set_user("Administrator")
		log = frappe.get_last_doc(
			"Notification Log", filters={"for_user": EMPLOYEE_USER, "document_type": "Timesheet"}
		)
		self.assertIn("Sent back", log.subject)
		self.assertIn("Please add a task", log.description or "")

	def test_hr_request_status_change_notifies_the_requester(self):
		# P2-U8: role Employee has no generic `create` on HR Request any
		# more, so a request is made the way the portal makes one.
		from helixhr.api import create_my_request

		frappe.set_user(EMPLOYEE_USER)
		doc = frappe.get_doc(
			"HR Request",
			create_my_request(
				category="HR Letter",
				subject="Need a letter",
				details="test",
				operation_key=str(uuid.uuid4()),
			)["name"],
		)

		before = self._unread_count(EMPLOYEE_USER)

		frappe.set_user(ensure_hr_manager_user())
		apply_workflow({"doctype": "HR Request", "name": doc.name}, "Pick up")
		doc.reload()
		doc.hr_note = "Sent to your email"
		doc.save()
		apply_workflow({"doctype": "HR Request", "name": doc.name}, "Done")

		after = self._unread_count(EMPLOYEE_USER)
		self.assertGreater(after, before)

	# P2-U4 / P2-KTD6. The reply event: the fixture Notification watches
	# `status` on a Value Change and cannot see `hr_note` at all, so an HR
	# reply written without moving the status produced nothing to read and
	# nothing to clear.

	def _reply_logs(self, request=None):
		filters = {
			"for_user": EMPLOYEE_USER,
			"document_type": "HR Request",
			"subject": ["like", f"{HR_REPLY_SUBJECT_PREFIX}%"],
		}
		if request:
			filters["document_name"] = request
		return frappe.get_all(
			"Notification Log",
			filters=filters,
			fields=["name", "read", "subject", "description"],
			order_by="creation asc",
		)

	def _employee_request(self):
		# P2-U8: role Employee has no generic `create` on HR Request any
		# more, so a request is made the way the portal makes one.
		from helixhr.api import create_my_request

		frappe.set_user(EMPLOYEE_USER)
		doc = frappe.get_doc(
			"HR Request",
			create_my_request(
				category="HR Letter",
				subject="Address proof",
				details="For my bank.",
				operation_key=str(uuid.uuid4()),
			)["name"],
		)
		frappe.set_user("Administrator")
		doc.reload()
		return doc

	def test_a_reply_with_no_status_change_is_still_one_exact_notification(self):
		doc = self._employee_request()
		status_before = doc.status

		doc.hr_note = "Collect it from reception."
		doc.save()

		logs = self._reply_logs(doc.name)
		self.assertEqual(len(logs), 1)
		self.assertEqual(logs[0].subject, f"{HR_REPLY_SUBJECT_PREFIX} Address proof")
		self.assertIn("Collect it from reception.", logs[0].description)
		self.assertEqual(logs[0].read, 0)
		self.assertEqual(frappe.db.get_value("HR Request", doc.name, "status"), status_before)

	def test_saving_the_same_note_again_creates_nothing(self):
		doc = self._employee_request()
		doc.hr_note = "Collect it from reception."
		doc.save()

		for _ in range(3):
			doc.reload()
			doc.details = f"For my bank. {frappe.generate_hash(length=6)}"
			doc.save()

		self.assertEqual(len(self._reply_logs(doc.name)), 1)

	def test_a_revised_reply_is_a_new_obligation_and_leaves_the_read_one_read(self):
		doc = self._employee_request()
		doc.hr_note = "Collect it from reception."
		doc.save()

		first = self._reply_logs(doc.name)[0]
		frappe.db.set_value("Notification Log", first.name, "read", 1)

		frappe.set_user(ensure_hr_manager_user())
		apply_workflow({"doctype": "HR Request", "name": doc.name}, "Pick up")
		doc.reload()
		doc.hr_note = "Reception is closed today -- collect it tomorrow."
		doc.save()
		apply_workflow({"doctype": "HR Request", "name": doc.name}, "Done")

		logs = self._reply_logs(doc.name)
		self.assertEqual(len(logs), 2)
		self.assertEqual(logs[0].name, first.name)
		self.assertEqual(logs[0].read, 1, "reading the older reply is not undone by a newer one")
		self.assertEqual(logs[1].read, 0)
		self.assertIn("Reception is closed today", logs[1].description)

	def test_clearing_a_note_notifies_nobody(self):
		doc = self._employee_request()
		doc.hr_note = "Collect it from reception."
		doc.save()

		doc.reload()
		doc.hr_note = ""
		doc.save()

		self.assertEqual(len(self._reply_logs(doc.name)), 1)

	def test_new_hr_request_does_not_write_an_unrouted_hr_bell_notification(self):
		from helixhr.api import create_my_request

		hr_manager_user = ensure_hr_manager_user()
		before = self._unread_count(hr_manager_user)
		frappe.set_user(EMPLOYEE_USER)
		create_my_request(
			category="Payroll Question",
			subject="Why is my payslip late",
			details="Some very private salary detail",
			operation_key=str(uuid.uuid4()),
		)
		frappe.set_user("Administrator")
		self.assertEqual(self._unread_count(hr_manager_user), before)

	def test_mark_all_as_read_zeroes_the_count(self):
		from frappe.desk.doctype.notification_log.notification_log import mark_all_as_read

		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"for_user": EMPLOYEE_USER,
				"subject": "test",
				"type": "Alert",
			}
		).insert(ignore_permissions=True)

		self.assertGreater(self._unread_count(EMPLOYEE_USER), 0)

		frappe.set_user(EMPLOYEE_USER)
		mark_all_as_read()

		self.assertEqual(self._unread_count(EMPLOYEE_USER), 0)


class TestHrQueueEmails(IntegrationTestCase):
	"""Plan 2026-10-02-001 U9 (was P4-R12 / P4-KTD9's fixture Notifications).

	Every leave, timesheet and attendance email is a templated send from a
	doc event (`helixhr.events`), one Email Queue row per recipient so
	`recipient_first_name` is theirs. HR-queue mail goes to every enabled HR
	Manager; leave routing comes from `Leave Type.helixhr_hr_approves` at
	insert and from `on_change` for the Send to HR `db_set` (KTD8a). HRMS's
	`send_leave_notification` is off, so HelixHR is the only sender.

	These tests need `ensure_test_email_account`: without a default outgoing
	account `frappe.sendmail` throws (logged, never raised) and nothing is
	queued to assert on.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_test_email_account()

	EMAIL_EMPLOYEE_USER = "hr-email-employee@helixhr.test"

	def setUp(self):
		frappe.set_user("Administrator")
		_, _, self.manager_name, _ = make_test_employee_and_manager()
		self.company = frappe.db.get_value("Employee", self.manager_name, "company")
		# Its own employee, and therefore its own Leave Allocation with the
		# range `ensure_leave_allocation` writes today: `EMPLOYEE_USER`'s
		# allocation on a long-lived bench predates these suites and drifts
		# (docs/runbook.md).
		self.employee_name = make_test_user(
			self.EMAIL_EMPLOYEE_USER, self.company, reports_to=self.manager_name
		)
		frappe.db.set_value("Employee", self.employee_name, "leave_approver", MANAGER_USER)
		ensure_holiday_list_assignment(self.company)
		self.hr_user = ensure_hr_manager_user()
		digest = int(hashlib.md5(self.id().encode()).hexdigest(), 16)
		self.leave_date = add_days(today(), 200 + (digest % 60))
		self.queued = set()

	def tearDown(self):
		frappe.set_user("Administrator")
		# The rows are committed (see `_watch_mail`), so they have to be
		# removed on purpose or every run leaves a handful behind.
		for row in self.queued:
			frappe.db.delete("Email Queue Recipient", {"parent": row})
			frappe.db.delete("Email Queue", {"name": row})

	def _watch_mail(self):
		"""Snapshot the queue, and answer with what the next act added.

		A delta, not a `reference_name` filter, and that is the P4 question
		about `mute_emails` answered: `frappe.sendmail` *commits* the Email
		Queue row, so a row outlives the rollback of the very document it
		points at -- and Leave Application's naming series is rolled back
		with that document, so the next test method's application is handed
		the same name and would match the previous one's mail.
		"""
		before = set(frappe.get_all("Email Queue", pluck="name"))

		def added():
			rows = set(frappe.get_all("Email Queue", pluck="name")) - before
			self.queued.update(rows)
			return sorted(
				(
					row,
					sorted(
						frappe.get_all(
							"Email Queue Recipient", filters={"parent": row}, pluck="recipient"
						)
					),
				)
				for row in rows
			)

		return added

	@staticmethod
	def _to(mails, user):
		"""The queued rows addressed to `user` (each row has one recipient)."""
		return [row for row, recipients in mails if user in recipients]

	@staticmethod
	def _message(row):
		return frappe.db.get_value("Email Queue", row, "message") or ""

	def _hr_recipients(self):
		from helixhr.events import _enabled_users_with_role

		return set(_enabled_users_with_role("HR Manager")) - {frappe.session.user}

	def _leave(self, leave_type="Casual Leave"):
		ensure_leave_allocation(self.employee_name, leave_type, 30)
		frappe.set_user("Administrator")
		for existing in frappe.get_all(
			"Leave Application",
			filters={"employee": self.employee_name, "from_date": str(self.leave_date)},
			pluck="name",
		):
			frappe.delete_doc("Leave Application", existing, force=True, ignore_permissions=True)
		frappe.set_user(self.EMAIL_EMPLOYEE_USER)
		doc = frappe.get_doc(
			{
				"doctype": "Leave Application",
				"employee": self.employee_name,
				"leave_type": leave_type,
				"from_date": str(self.leave_date),
				"to_date": str(self.leave_date),
				"description": "hr email",
				"leave_approver": MANAGER_USER,
			}
		)
		doc.insert()
		frappe.set_user("Administrator")
		self.addCleanup(self._remove, doc.name)
		return doc

	def _remove(self, name):
		frappe.set_user("Administrator")
		if frappe.db.exists("Leave Application", name):
			if frappe.db.get_value("Leave Application", name, "docstatus") == 1:
				frappe.get_doc("Leave Application", name).cancel()
			frappe.delete_doc("Leave Application", name, force=True, ignore_permissions=True)

	def _manager_logs(self, name):
		return frappe.get_all(
			"Notification Log",
			filters={"for_user": MANAGER_USER, "document_type": "Leave Application", "document_name": name},
			pluck="subject",
		)

	def test_filing_a_leave_application_notifies_the_manager_once(self):
		"""P5-U10: the manager's arrival notice, a Notification Log (the
		portal bell) rather than mail -- unlike everything else in this
		class, which is HR's separate Email-channel fixture path."""
		leave = self._leave()

		subjects = self._manager_logs(leave.name)
		self.assertEqual(len(subjects), 1)
		self.assertIn("Casual Leave", subjects[0])

	def test_a_resave_does_not_notify_the_manager_again(self):
		leave = self._leave()
		self.assertEqual(len(self._manager_logs(leave.name)), 1)

		frappe.set_user("Administrator")
		leave.reload()
		leave.description = "edited"
		leave.save(ignore_permissions=True)

		self.assertEqual(len(self._manager_logs(leave.name)), 1)

	def test_an_hr_approves_leave_type_notifies_hr_not_the_manager(self):
		"""The fixture-mailed HR path (tested elsewhere in this class) must
		not *also* ring the manager's portal bell for a request that was
		never theirs to act on (P4-R7)."""
		from helixhr.api import apply_for_leave

		leave_type = self._hr_approves_leave_type()
		ensure_leave_allocation(self.employee_name, leave_type, 30)
		date = str(add_days(self.leave_date, 8))
		frappe.set_user("Administrator")
		for existing in frappe.get_all(
			"Leave Application", filters={"employee": self.employee_name, "from_date": date}, pluck="name"
		):
			frappe.delete_doc("Leave Application", existing, force=True, ignore_permissions=True)

		frappe.set_user(self.EMAIL_EMPLOYEE_USER)
		result = apply_for_leave(leave_type=leave_type, from_date=date, to_date=date)
		frappe.set_user("Administrator")
		self.addCleanup(self._remove, result["name"])

		self.assertEqual(result["stage"], "HR")
		self.assertEqual(self._manager_logs(result["name"]), [])

	def test_a_manager_whose_employee_is_inactive_is_not_notified_and_filing_still_succeeds(self):
		frappe.db.set_value("Employee", self.manager_name, "status", "Left")
		self.addCleanup(frappe.db.set_value, "Employee", self.manager_name, "status", "Active")

		leave = self._leave()
		self.assertTrue(frappe.db.exists("Leave Application", leave.name))
		self.assertEqual(self._manager_logs(leave.name), [])

	def test_the_notifier_never_addresses_the_acting_session_user(self):
		"""Direct unit coverage of the shared guard -- constructing a real
		document whose manager and submitter are the same login is not a
		reachable state through any portal path, so this calls the helper
		the way `leave_application_after_insert` does."""
		from helixhr.events import _notify_manager_of_arrival

		leave = self._leave()
		before = frappe.db.count("Notification Log")
		frappe.set_user(MANAGER_USER)
		_notify_manager_of_arrival("Leave Application", leave, MANAGER_USER, "should never be written")
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.count("Notification Log"), before)

	def test_a_leave_moving_to_the_hr_stage_mails_every_hr_manager_once(self):
		leave = self._leave()
		added = self._watch_mail()

		leave.db_set("helixhr_stage", "HR")

		mails = added()
		self.assertEqual(
			{user for _, recipients in mails for user in recipients},
			self._hr_recipients(),
			"leave_for_hr reaches every HR Manager, and nobody else",
		)
		self.assertEqual(len(self._to(mails, self.hr_user)), 1)
		row = self._to(mails, self.hr_user)[0]
		self.assertEqual(frappe.db.get_value("Email Queue", row, "reference_name"), leave.name)
		self.assertIn("waiting for HR", self._message(row))

	def _hr_approves_leave_type(self):
		leave_type = "_Test HR Approved Leave"
		if not frappe.db.exists("Leave Type", leave_type):
			frappe.get_doc(
				{"doctype": "Leave Type", "leave_type_name": leave_type, "helixhr_hr_approves": 1}
			).insert(ignore_permissions=True)
		frappe.db.set_value("Leave Type", leave_type, "helixhr_hr_approves", 1)
		return leave_type

	def test_the_portal_path_mails_hr_exactly_once_and_the_manager_never(self):
		"""KTD8a. `apply_for_leave` inserts, then `db_set`s the stage to HR.
		The insert routes by Leave Type and mails HR; the stage `db_set`
		that follows must not mail HR a second time, and the manager gets
		nothing (P4-R7)."""
		from helixhr.api import apply_for_leave

		leave_type = self._hr_approves_leave_type()
		ensure_leave_allocation(self.employee_name, leave_type, 30)
		date = str(add_days(self.leave_date, 4))
		frappe.set_user("Administrator")
		for existing in frappe.get_all(
			"Leave Application",
			filters={"employee": self.employee_name, "from_date": date},
			pluck="name",
		):
			frappe.delete_doc("Leave Application", existing, force=True, ignore_permissions=True)

		added = self._watch_mail()
		frappe.set_user(self.EMAIL_EMPLOYEE_USER)
		result = apply_for_leave(leave_type=leave_type, from_date=date, to_date=date)
		frappe.set_user("Administrator")
		self.addCleanup(self._remove, result["name"])

		self.assertEqual(result["stage"], "HR")
		mails = added()
		self.assertEqual(len(self._to(mails, self.hr_user)), 1, "one mail, not one per hook")
		self.assertEqual(self._to(mails, MANAGER_USER), [])
		self.assertEqual(
			frappe.db.get_value("Email Queue", self._to(mails, self.hr_user)[0], "reference_name"),
			result["name"],
		)

	def test_a_leave_filed_in_desk_for_an_hr_approves_type_mails_hr_not_the_manager(self):
		"""KTD8a: Desk inserts never set the stage, so the insert routes by
		Leave Type -- an employee's own Desk filing and one HR files already
		in the HR stage both mail HR once and the manager never."""
		leave_type = self._hr_approves_leave_type()

		added = self._watch_mail()
		leave = self._leave(leave_type=leave_type)
		frappe.set_user("Administrator")
		leave.reload()
		self.assertEqual(
			leave.helixhr_stage, "Manager", "an employee's own insert never sets the stage"
		)
		mails = added()
		self.assertEqual(len(self._to(mails, self.hr_user)), 1)
		self.assertEqual(self._to(mails, MANAGER_USER), [])

		frappe.set_user("Administrator")
		hr_filed = frappe.get_doc(
			{
				"doctype": "Leave Application",
				"employee": self.employee_name,
				"leave_type": leave_type,
				"from_date": str(add_days(self.leave_date, 2)),
				"to_date": str(add_days(self.leave_date, 2)),
				"description": "filed by HR",
				"leave_approver": MANAGER_USER,
				"helixhr_stage": "HR",
			}
		)
		added = self._watch_mail()
		hr_filed.insert(ignore_permissions=True)
		self.addCleanup(self._remove, hr_filed.name)

		mails = added()
		self.assertEqual(len(self._to(mails, self.hr_user)), 1)
		self.assertEqual(self._to(mails, MANAGER_USER), [])
		self.assertEqual(
			frappe.db.get_value("Email Queue", self._to(mails, self.hr_user)[0], "reference_name"),
			hr_filed.name,
		)

	def test_a_state_that_is_not_the_hr_queue_mails_nobody(self):
		leave = self._leave()
		added = self._watch_mail()

		leave.reload()
		leave.status = "Rejected"
		leave.save(ignore_permissions=True)

		mails = added()
		self.assertEqual(self._to(mails, self.hr_user), [], "a send-back is the employee's news, not HR's")
		self.assertEqual(len(self._to(mails, self.EMAIL_EMPLOYEE_USER)), 1)
		self.assertEqual(len(mails), 1)
		self.assertIn("sent back", self._message(mails[0][0]))

	def test_a_routed_request_mails_its_it_holders_without_employee_text(self):
		from helixhr.api import create_my_request

		_, it_user = make_test_it_user()
		added = self._watch_mail()
		frappe.set_user(EMPLOYEE_USER)
		created = create_my_request(
			category="IT / Asset",
			subject="Laptop replacement",
			details="Private asset serial 12345",
			operation_key=str(uuid.uuid4()),
		)
		frappe.set_user("Administrator")

		mails = added()
		self.assertEqual(len(mails), 1)
		self.assertIn(it_user, mails[0][1])
		self.assertNotIn(EMPLOYEE_USER, mails[0][1])
		body = frappe.db.get_value("Email Queue", mails[0][0], "message") or ""
		self.assertIn("Laptop replacement", body)
		self.assertNotIn("Private asset serial", body)
		self.assertEqual(frappe.db.get_value("Email Queue", mails[0][0], "reference_name"), created["name"])

	def test_an_employees_application_mails_the_approver_once_and_hrms_nothing(self):
		"""R21: one HelixHR email to the approver; HRMS's own leave mail is
		off, so the queue holds exactly that one row."""
		self.assertFalse(frappe.db.get_single_value("HR Settings", "send_leave_notification"))
		added = self._watch_mail()

		leave = self._leave()

		mails = added()
		self.assertEqual(len(mails), 1)
		self.assertEqual(mails[0][1], [MANAGER_USER])
		self.assertEqual(frappe.db.get_value("Email Queue", mails[0][0], "reference_name"), leave.name)
		self.assertIn("hr email", self._message(mails[0][0]))

	def test_leave_approved_in_desk_mails_the_employee(self):
		leave = self._leave()
		added = self._watch_mail()

		leave.reload()
		leave.status = "Approved"
		leave.submit()

		mails = added()
		self.assertEqual(len(self._to(mails, self.EMAIL_EMPLOYEE_USER)), 1)
		self.assertEqual(len(mails), 1)
		self.assertIn("approved", self._message(mails[0][0]))

	def test_hr_cancelling_approved_leave_mails_the_employee(self):
		leave = self._leave()
		leave.reload()
		leave.status = "Approved"
		leave.submit()
		added = self._watch_mail()

		frappe.set_user(self.hr_user)
		frappe.get_doc("Leave Application", leave.name).cancel()
		frappe.set_user("Administrator")

		mails = added()
		self.assertEqual(len(self._to(mails, self.EMAIL_EMPLOYEE_USER)), 1)
		self.assertEqual(len(mails), 1)
		self.assertIn("cancelled", self._message(mails[0][0]))

	def test_re_enabling_hrms_leave_notification_is_refused(self):
		settings = frappe.get_doc("HR Settings")
		settings.send_leave_notification = 1
		with self.assertRaises(frappe.ValidationError):
			settings.save(ignore_permissions=True)

	def test_a_mail_failure_does_not_fail_the_leave_insert(self):
		from unittest.mock import patch

		before = frappe.db.count("Error Log")
		with patch("frappe.sendmail", side_effect=Exception("queue down")):
			leave = self._leave()
		self.assertTrue(frappe.db.exists("Leave Application", leave.name))
		self.assertGreater(frappe.db.count("Error Log"), before)

	def test_an_event_switched_off_sends_nothing_and_the_bell_still_rings(self):
		leave = self._leave()
		_put_row("leave_approved", "x", "x", is_enabled=0)
		self.addCleanup(frappe.db.delete, _MT, {"name": "leave_approved"})
		added = self._watch_mail()

		leave.reload()
		leave.status = "Approved"
		leave.submit()

		self.assertEqual(self._to(added(), self.EMAIL_EMPLOYEE_USER), [])
		self.assertTrue(
			frappe.db.exists(
				"Notification Log",
				{"for_user": self.EMAIL_EMPLOYEE_USER, "document_name": leave.name},
			)
		)


class TestNotificationTemplateEscaping(IntegrationTestCase):
	"""P4 security follow-up. Frappe's Jinja environment has no autoescape,
	so a fixture that interpolates approver-authored free text into an HTML
	message has to escape it itself.

	`events._notify_attendance_request` already runs the same value through
	`frappe.utils.escape_html`; the fixture path was the inconsistent one.
	Rendered here rather than through a real send: the fixture's template
	text is the thing under test, and `Notification.get_context` supplies
	exactly `doc` and `comments`.
	"""

	PAYLOAD = "<script>alert(1)</script>"

	def _render(self, comments=None, **fields):
		message = frappe.db.get_value("Notification", "HelixHR Timesheet Status Changed", "message")
		return frappe.render_template(message, {"doc": frappe._dict(fields), "comments": comments})

	def test_a_decision_reason_carrying_markup_is_escaped(self):
		rendered = self._render(
			start_date="2026-09-07",
			end_date="2026-09-13",
			workflow_state="Sent Back",
			helixhr_decision_reason=self.PAYLOAD,
		)

		self.assertIn("&lt;script&gt;", rendered)
		self.assertNotIn("<script>", rendered)

	def test_the_comment_fallback_is_escaped_too(self):
		rendered = self._render(
			comments=[{"by": "hr@helixhr.test", "comment": self.PAYLOAD}],
			start_date="2026-09-07",
			end_date="2026-09-13",
			workflow_state="Sent Back",
			helixhr_decision_reason=None,
		)

		self.assertIn("&lt;script&gt;", rendered)
		self.assertNotIn("<script>", rendered)


# --- Plan 2026-10-02-001 U8: the HelixHR template sandbox --------------------

_MT = "HelixHR Message Template"


def _sandbox_render(source, context=None, autoescape=True):
	"""Render through the bare environment -- no shape check, no context
	filtering -- to prove the *environment itself* isolates, independent of
	the save-time validation layered on top of it."""
	from helixhr.utils import _run, _template_envs

	body_env, subject_env = _template_envs()
	env = body_env if autoescape else subject_env
	return _run(env.from_string(source), context or {})


def _put_row(template_key, subject, body, is_enabled=1, validate=False):
	"""A template row for one test, removed afterwards. `validate=False`
	writes it raw (a legacy or hand-imported row the new rules would refuse)."""
	frappe.set_user("Administrator")
	if frappe.db.exists(_MT, template_key):
		frappe.delete_doc(_MT, template_key, force=True, ignore_permissions=True)
	doc = frappe.get_doc(
		{
			"doctype": _MT,
			"template_key": template_key,
			"subject": subject,
			"body": body,
			"is_enabled": is_enabled,
		}
	)
	if validate:
		doc.insert(ignore_permissions=True)
	else:
		doc.name = template_key
		doc.db_insert()
	return doc


class TestTemplateSandbox(IntegrationTestCase):
	"""KTD6 is load-bearing: if any of these renders, stop before a template ships."""

	def _assert_blocked(self, source, context=None):
		from jinja2.exceptions import SecurityError, TemplateError, UndefinedError

		# TypeError: `{% include %}` with no loader configured at all.
		with self.assertRaises((SecurityError, UndefinedError, TemplateError, TypeError), msg=source):
			_sandbox_render(source, context)

	def test_frappe_doc_and_translation_are_not_reachable(self):
		for source in (
			"{{ frappe.db.get_value('User', 'Administrator', 'name') }}",
			"{{ frappe.get_all('User') }}",
			"{{ frappe.db.sql('select 1') }}",
			"{{ frappe.get_doc('User', 'Administrator') }}",
			"{{ frappe.session.user }}",
			"{{ doc }}",
			"{{ doc.name }}",
			"{{ _('x') }}",
		):
			self._assert_blocked(source)

	def test_jinja_default_globals_are_gone(self):
		for source in (
			"{{ range(10) }}",
			"{{ range(10**9)|list }}",
			"{{ cycler(1, 2) }}",
			"{{ joiner() }}",
			"{{ namespace(a=1) }}",
			"{{ lipsum() }}",
			"{{ dict(a=1) }}",
			"{{ cycler }}",
		):
			self._assert_blocked(source)

	def test_dunder_attribute_access_raises(self):
		for source in (
			"{{ ''.__class__ }}",
			"{{ ''.__class__.__mro__ }}",
			"{{ x.__class__.__base__.__subclasses__() }}",
			"{{ x.__init__.__globals__ }}",
			"{{ x|attr('__class__') }}",
		):
			self._assert_blocked(source, {"x": "plain"})

	def test_memory_bombs_raise(self):
		for source in ("{{ 'a' * 100000000 }}", "{{ 9 ** 9 ** 9 }}", "{{ ''|center(1000000000) }}"):
			self._assert_blocked(source)

	def test_an_undefined_variable_raises_instead_of_rendering_empty_or_literal(self):
		self._assert_blocked("Hi {{ nobody }}")

	def test_a_one_line_path_like_string_is_text_not_a_file(self):
		self.assertEqual(_sandbox_render("Report.html", autoescape=False), "Report.html")
		self.assertEqual(
			_sandbox_render("templates/emails/helixhr_layout.html"), "templates/emails/helixhr_layout.html"
		)

	def test_include_and_extends_cannot_reach_a_file(self):
		self._assert_blocked("{% include 'templates/emails/helixhr_layout.html' %}")
		self._assert_blocked("{% extends 'templates/emails/helixhr_layout.html' %}")


class TestTemplateValidation(IntegrationTestCase):
	"""R17: what save refuses, and that the refusal names the problem."""

	def _refused(self, body, event="leave_approved", subject="Leave"):
		from helixhr.utils import TemplateRejected, validate_message_template

		with self.assertRaises(TemplateRejected) as caught:
			validate_message_template(event, subject, body)
		return str(caught.exception)

	def test_frappe_session_user_is_refused(self):
		self.assertIn("frappe", self._refused("{{ frappe.session.user }}"))

	def test_a_misspelled_variable_is_refused_by_name(self):
		self.assertIn("employe_name", self._refused("Hi {{ employe_name }}", event="leave_submitted"))

	def test_a_syntax_error_names_its_line(self):
		self.assertIn("line 3", self._refused("<p>a</p>\n<p>b</p>\n{% if leave_type %}"))

	def test_an_error_against_sample_data_names_its_line(self):
		self.assertIn("line 2", self._refused("ok\n{{ items[7].title }}", event="approval_overdue_digest"))

	def test_constructs_outside_the_allowed_shape_are_refused(self):
		for body in (
			"{{ leave_type.upper() }}",
			"{% set x = leave_type %}{{ x }}",
			"{% macro m() %}x{% endmacro %}",
			"{% include 'x.html' %}",
			"{% import 'x.html' as y %}",
			"{% filter upper %}x{% endfilter %}",
			"{% for c in 'abc' %}{{ c }}{% endfor %}",
			"{{ leave_type|center(1000000) }}",
			"{{ leave_type|replace('a', 'bb') }}",
			"{{ leave_type|attr('__class__') }}",
		):
			with self.subTest(body=body):
				self._refused(body)

	def test_recursive_and_deeply_nested_loops_are_refused(self):
		self._refused(
			"{% for i in items recursive %}{{ loop(items) }}{% endfor %}", event="approval_overdue_digest"
		)
		self._refused(
			"{% for a in items %}{% for b in items %}{% for c in items %}{% for d in items %}"
			"{% endfor %}{% endfor %}{% endfor %}{% endfor %}",
			event="approval_overdue_digest",
		)

	def test_loops_over_the_events_own_lists_are_allowed(self):
		from helixhr.utils import validate_message_template

		validate_message_template(
			"hr_overdue_summary",
			"{{ count }} overdue",
			"{% for o in owners %}{{ o.owner_name }}{% for i in o.items %}{{ i.title }} {{ loop.index }}"
			"{% endfor %}{% endfor %}",
		)

	def test_every_registry_default_validates_and_renders_with_sample_data(self):
		from helixhr.utils import (
			NOTIFICATION_EVENTS,
			render_message,
			sample_context,
			validate_message_template,
		)

		for event_key, event in NOTIFICATION_EVENTS.items():
			with self.subTest(event=event_key):
				validate_message_template(event_key, event["subject"], event["body"])
				message = render_message(event_key, sample_context(event_key))
				self.assertTrue(message["subject"])
				self.assertNotIn("{{", message["html"])
				self.assertIn(message["content"], message["html"])


class TestRenderMessage(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.delete(_MT, {"template_key": ["in", self._keys]})

	_keys = ("leave_approved", "approval_overdue_digest", "bank_change_requested", "leave_rejected")

	def _ctx(self, event_key, **overrides):
		from helixhr.utils import sample_context

		return {**sample_context(event_key), **overrides}

	def test_variables_render_and_an_empty_if_block_is_omitted(self):
		from helixhr.utils import render_message

		message = render_message(
			"leave_approved", self._ctx("leave_approved", recipient_first_name="Priya", decision_note=None)
		)
		self.assertIn("Hi Priya,", message["content"])
		self.assertNotIn("Note:", message["content"])
		self.assertNotIn("None", message["content"])

	def test_values_are_escaped_in_the_body_and_plain_in_the_subject(self):
		from helixhr.utils import render_message

		message = render_message(
			"leave_approved",
			self._ctx(
				"leave_approved", decision_note="<script>alert(1)</script>", leave_type="Sick & Family"
			),
		)
		self.assertIn("&lt;script&gt;", message["html"])
		self.assertNotIn("<script>", message["html"])
		self.assertEqual(message["subject"], "Your Sick & Family was approved")
		self.assertIn("Sick &amp; Family", message["content"])
		self.assertNotIn("&amp;amp;", message["html"])

	def test_nested_list_values_are_escaped(self):
		from helixhr.utils import render_message

		item = {"kind": "Request", "title": "<a href=x>", "employee_name": "<b>", "age_days": 3, "url": "u"}
		digest = render_message("approval_overdue_digest", {"items": [item], "count": 1})
		self.assertIn("&lt;a href=x&gt;", digest["content"])
		self.assertNotIn("<a href=x>", digest["content"])
		summary = render_message(
			"hr_overdue_summary",
			{"owners": [{"owner_name": "<i>M</i>", "inactive": True, "items": [item]}], "count": 1},
		)
		self.assertIn("&lt;a href=x&gt;", summary["content"])
		self.assertIn("&lt;i&gt;M&lt;/i&gt;", summary["content"])
		self.assertNotIn("<a href=x>", summary["content"])

	def test_a_path_like_saved_subject_renders_as_text(self):
		from helixhr.utils import render_message

		_put_row("leave_rejected", "Report.html", "<p>x</p>", validate=True)
		self.assertEqual(
			render_message("leave_rejected", self._ctx("leave_rejected"))["subject"], "Report.html"
		)

	def test_a_document_never_reaches_the_template(self):
		from helixhr.utils import render_message

		with self.assertRaises(TypeError):
			render_message("leave_approved", {"leave_type": frappe.get_doc("User", "Administrator")})

	def test_off_sends_nothing_for_an_unlocked_event(self):
		from helixhr.utils import render_message

		_put_row("leave_approved", "x", "y", is_enabled=0, validate=True)
		self.assertIsNone(render_message("leave_approved", self._ctx("leave_approved")))

	def test_a_template_failing_on_real_data_falls_back_and_logs_quietly(self):
		from helixhr.utils import NOTIFICATION_EVENTS, render_message

		_put_row(
			"approval_overdue_digest",
			"Overdue: {{ items[0].title }}",
			"<p>{{ items[0].title }}</p>",
			validate=True,
		)
		before = frappe.db.count("Error Log")
		frappe.clear_messages()
		message = render_message("approval_overdue_digest", {"items": [], "count": 0})
		self.assertEqual(message["subject"], "0 approval(s) waiting on you")
		self.assertNotEqual(NOTIFICATION_EVENTS["approval_overdue_digest"]["subject"], message["subject"])
		self.assertEqual(frappe.db.count("Error Log"), before + 1)
		self.assertEqual(frappe.get_message_log(), [])

	def test_a_locked_events_core_sentence_renders_with_a_blank_extra_paragraph(self):
		from helixhr.utils import render_message

		ctx = self._ctx("bank_change_requested", masked_new_value="••••9876")
		self.assertIn("••••9876", render_message("bank_change_requested", ctx)["html"])

		_put_row("bank_change_requested", "Ignored subject", "<p>Call HR on 1234.</p>", validate=True)
		message = render_message("bank_change_requested", ctx)
		self.assertIn("••••9876", message["html"])
		self.assertIn("Call HR on 1234.", message["html"])
		self.assertTrue(message["subject"].startswith("Security notice"))

	def test_off_is_refused_for_a_locked_event(self):
		with self.assertRaises(frappe.ValidationError):
			_put_row("bank_change_requested", "x", "", is_enabled=0, validate=True)

	def test_a_save_writes_a_version_and_an_actor_comment(self):
		doc = _put_row("leave_approved", "Approved", "<p>{{ leave_type }}</p>", validate=True)
		doc.subject = "Approved: {{ leave_type }}"
		# Frappe skips Versions under test unless asked; production saves keep them.
		doc.save(ignore_permissions=True, ignore_version=False)
		self.assertTrue(frappe.db.exists("Version", {"ref_doctype": _MT, "docname": doc.name}))
		comments = frappe.get_all(
			"Comment",
			filters={"reference_doctype": _MT, "reference_name": doc.name, "comment_type": "Info"},
			pluck="content",
		)
		self.assertTrue(any("Administrator" in comment for comment in comments))


class TestMigrateMessageTemplatesToJinja(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self._saved = frappe.get_all(_MT, fields=["*"])
		frappe.db.delete(_MT)

	def tearDown(self):
		frappe.db.delete(_MT)
		for row in self._saved:
			frappe.get_doc({"doctype": _MT, **row}).db_insert()

	def test_tokens_convert_literals_stay_literal_and_disabled_rows_go(self):
		from helixhr.patches.v1_0.migrate_message_templates_to_jinja import execute
		from helixhr.utils import render_message, validate_message_template

		_put_row(
			"request_arrival", "Request {category} arrived", "Use {{ braces }} for {subject}: {portal_url}"
		)
		_put_row("request_status_changed", "Old {state}", "{reason}", is_enabled=0)

		execute()
		execute()  # idempotent

		row = frappe.db.get_value(_MT, "request_arrival", ["subject", "body"], as_dict=True)
		self.assertEqual(row.subject, "Request {{ category }} arrived")
		validate_message_template("request_arrival", row.subject, row.body)
		message = render_message(
			"request_arrival",
			{"category": "IT", "subject": "Laptop", "action_url": "https://x/helixhr/requests"},
		)
		self.assertEqual(message["subject"], "Request IT arrived")
		self.assertIn("Use {{ braces }} for Laptop: https://x/helixhr/requests", message["content"])
		self.assertFalse(frappe.db.exists(_MT, "request_status_changed"))
