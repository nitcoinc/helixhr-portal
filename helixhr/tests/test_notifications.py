import hashlib

import frappe
from frappe.model.workflow import apply_workflow
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from helixhr.tests.utils import (
	EMPLOYEE_USER,
	MANAGER_USER,
	ensure_holiday_list_assignment,
	ensure_leave_allocation,
	make_test_employee_and_manager,
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
		apply_workflow({"doctype": "Timesheet", "name": doc.name}, "Reject")

		after = self._unread_count(EMPLOYEE_USER)
		self.assertGreater(after, before)

		frappe.set_user("Administrator")
		log = frappe.get_last_doc(
			"Notification Log", filters={"for_user": EMPLOYEE_USER, "document_type": "Timesheet"}
		)
		self.assertIn("Sent back", log.subject)
		self.assertIn("Please add a task", log.description or "")

	def test_hr_request_status_change_notifies_the_requester(self):
		frappe.set_user(EMPLOYEE_USER)
		doc = frappe.get_doc(
			{
				"doctype": "HR Request",
				"category": "HR Letter",
				"subject": "Need a letter",
				"details": "test",
			}
		)
		doc.insert()

		before = self._unread_count(EMPLOYEE_USER)

		frappe.set_user("Administrator")
		doc.reload()
		doc.status = "Done"
		doc.hr_note = "Sent to your email"
		doc.save()

		after = self._unread_count(EMPLOYEE_USER)
		self.assertGreater(after, before)

	def test_new_hr_request_notifies_hr_manager_without_details(self):
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
		frappe.get_doc(
			{
				"doctype": "HR Request",
				"category": "Payroll Question",
				"subject": "Why is my payslip late",
				"details": "Some very private salary detail that should not leak into the subject line",
			}
		).insert()

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
