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
		return frappe.get_all(
			"Notification Log",
			filters={"document_type": "Leave Application", "document_name": name},
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

		frappe.set_user("Administrator")
		doc.reload()
		doc.status = "Done"
		doc.hr_note = "Sent to your email"
		doc.save()

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

		doc.reload()
		doc.hr_note = "Reception is closed today -- collect it tomorrow."
		doc.status = "Done"
		doc.save()

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

	def test_new_hr_request_notifies_hr_manager_without_details(self):
		from helixhr.api import create_my_request

		hr_manager_user = "hr-manager-notif@helixhr.test"
		if not frappe.db.exists("User", hr_manager_user):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": hr_manager_user,
					"first_name": "HR",
					"last_name": "Manager",
					"send_welcome_email": 0,
					"roles": [{"doctype": "Has Role", "role": "HR Manager"}],
				}
			).insert(ignore_permissions=True)

		before = self._unread_count(hr_manager_user)

		frappe.set_user(EMPLOYEE_USER)
		create_my_request(
			category="Payroll Question",
			subject="Why is my payslip late",
			details="Some very private salary detail that should not leak into the subject line",
			operation_key=str(uuid.uuid4()),
		)

		frappe.set_user("Administrator")
		after = self._unread_count(hr_manager_user)
		self.assertGreater(after, before)

		log = frappe.get_last_doc(
			"Notification Log", filters={"for_user": hr_manager_user, "document_type": "HR Request"}
		)
		self.assertIn("Payroll Question", log.subject)
		self.assertNotIn("private salary detail", log.subject)

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
	"""P4-R12 / P4-KTD9. HR is told a request reached its queue by four
	fixture Notifications and by nothing in code.

	Channel Email, recipients by role HR Manager, one mail per escalation.
	Leave needs two of them: Frappe skips Value Change while
	`flags.in_insert`, and a request for an HR-approves Leave Type is
	*inserted* in the HR stage.

	These are the tests that need `ensure_test_email_account` -- without a
	default outgoing account `frappe.sendmail` throws from inside the save,
	so every Send to HR would fail rather than merely fail to notify.
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
			frappe.delete_doc("Leave Application", name, force=True, ignore_permissions=True)

	def test_a_leave_moving_to_the_hr_stage_mails_every_hr_manager_once(self):
		leave = self._leave()
		added = self._watch_mail()

		leave.db_set("helixhr_stage", "HR")

		mails = added()
		self.assertEqual(len(mails), 1, "one mail, however many HR Managers hold the role")
		self.assertIn(self.hr_user, mails[0][1])
		self.assertEqual(
			frappe.db.get_value("Email Queue", mails[0][0], "reference_name"), leave.name
		)

	def test_an_hr_approves_leave_type_mails_on_the_insert(self):
		"""Frappe does not evaluate Value Change while `flags.in_insert`, so
		the New-event fixture is the only thing that covers a request that
		starts in the HR queue (P4-R7, P4-KTD9)."""
		leave_type = "_Test HR Approved Leave"
		if not frappe.db.exists("Leave Type", leave_type):
			frappe.get_doc(
				{"doctype": "Leave Type", "leave_type_name": leave_type, "helixhr_hr_approves": 1}
			).insert(ignore_permissions=True)
		frappe.db.set_value("Leave Type", leave_type, "helixhr_hr_approves", 1)

		leave = self._leave(leave_type=leave_type)
		frappe.set_user("Administrator")
		leave.reload()
		self.assertEqual(leave.helixhr_stage, "Manager", "the insert itself never sets the stage")

		# `apply_for_leave` is what sets it, with `db_set` after the insert
		# (P4-KTD4) -- so on this route the Value Change fixture is what
		# fires. The New fixture covers a request HR files in Desk with the
		# stage already set.
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
		self.assertEqual(len(mails), 1)
		self.assertIn(self.hr_user, mails[0][1])
		self.assertEqual(
			frappe.db.get_value("Email Queue", mails[0][0], "reference_name"), hr_filed.name
		)

	def test_a_state_that_is_not_the_hr_queue_mails_nobody(self):
		leave = self._leave()
		added = self._watch_mail()

		leave.reload()
		leave.status = "Rejected"
		leave.save(ignore_permissions=True)

		self.assertEqual(added(), [], "a send-back is the employee's news, not HR's")

	def test_the_four_fixtures_are_email_channel_and_addressed_by_role(self):
		"""The mechanism is the fixture, so the fixture's shape is the
		requirement. A System Notification here would leave HR with no mail
		and only a bell they may never look at (P4 deferred work)."""
		for name in (
			"HelixHR Leave Sent To HR",
			"HelixHR New Leave For HR",
			"HelixHR Timesheet Sent To HR",
			"HelixHR Attendance Request Sent To HR",
		):
			alert = frappe.get_doc("Notification", name)
			self.assertEqual(alert.channel, "Email", msg=name)
			self.assertTrue(alert.enabled, msg=name)
			self.assertEqual([row.receiver_by_role for row in alert.recipients], ["HR Manager"], msg=name)
			self.assertIn("/helixhr/approvals/", alert.message, msg=name)
