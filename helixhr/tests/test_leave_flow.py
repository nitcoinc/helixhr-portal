import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from helixhr.tests.utils import (
	EMPLOYEE_USER,
	ensure_holiday_list_assignment,
	ensure_leave_allocation,
	make_test_employee_and_manager,
)


class TestLeaveFlow(IntegrationTestCase):
	def setUp(self):
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()
		# leave_approver isn't auto-fetched from Employee server-side --
		# hrms.hr.doctype.leave_application.leave_application.
		# validate_leave_approver checks the field on the Leave Application
		# itself, which the portal (LeaveForm.vue) fills from
		# hrms.api.get_leave_approval_details before insert. Set it
		# directly on Employee so that helper has something to return, and
		# pass it explicitly below the same way the frontend does.
		# frappe.db.set_value writes are visible within this same
		# connection/transaction regardless of session user, so no commit()
		# is needed here -- and a real commit() would break the per-test
		# rollback IntegrationTestCase relies on for isolation between
		# test methods (confirmed: it leaked a Leave Application from one
		# test into the next's overlap check before this was removed).
		frappe.db.set_value("Employee", self.employee_name, "leave_approver", frappe.session.user)
		frappe.db.set_value("Employee", self.manager_name, "leave_approver", frappe.session.user)

	def tearDown(self):
		frappe.set_user("Administrator")

	def _leave_application(self, employee, days_from_today, description, leave_type="Casual Leave", **extra):
		return {
			"doctype": "Leave Application",
			"employee": employee,
			"leave_type": leave_type,
			"from_date": add_days(today(), days_from_today),
			"to_date": add_days(today(), days_from_today),
			"description": description,
			"leave_approver": frappe.session.user,
			**extra,
		}

	def test_valid_leave_creates_open_application_waiting_for_approver(self):
		ensure_leave_allocation(self.employee_name, "Casual Leave", 5)

		frappe.set_user(EMPLOYEE_USER)
		doc = frappe.get_doc(self._leave_application(self.employee_name, 10, "Family event"))
		doc.insert()

		self.assertEqual(doc.status, "Open")
		self.assertEqual(doc.docstatus, 0)
		self.assertEqual(doc.leave_approver, frappe.session.user)

	def test_zero_balance_leave_is_refused(self):
		"""AE2: an employee with 0 Casual Leave left gets no Leave
		Application and Frappe's real "insufficient balance" error --
		the plain-sentence mapping for it is covered by
		errorMap.test.js on the frontend."""
		# A leave type not touched by this class's other tests -- Frappe's
		# IntegrationTestCase does not roll back between test *methods*
		# within one run here, only (presumably) between separate
		# `run-tests` invocations, so a shared "Casual Leave" balance
		# would leak state from whichever other method in this class ran
		# first. Confirmed while writing this suite: count_before came
		# back 3, not 0, the first time this used the same leave type as
		# test_valid_leave_creates_open_application_waiting_for_approver.
		ensure_leave_allocation(self.employee_name, "Sick Leave", 1)
		company = frappe.db.get_value("Employee", self.employee_name, "company")
		ensure_holiday_list_assignment(company)

		frappe.set_user(EMPLOYEE_USER)
		# Spend the one allocated day first. Leave Application is
		# submittable (is_submittable=1): a Leave Ledger Entry, which the
		# balance check actually reads, is only created on_submit -- an
		# inserted-but-unsubmitted ("Open") application does not yet
		# consume any balance. Submitting is the approver's action
		# (KTD17's "Approved" state), done here as Administrator to stand
		# in for that approval and get the leave type into a genuine
		# zero-balance state for the next assertion.
		first = frappe.get_doc(
			self._leave_application(self.employee_name, 20, "First request", leave_type="Sick Leave")
		)
		first.insert()
		frappe.set_user("Administrator")
		approved = frappe.get_doc("Leave Application", first.name)
		approved.status = "Approved"
		approved.submit()
		frappe.set_user(EMPLOYEE_USER)

		count_before = frappe.db.count(
			"Leave Application", {"employee": self.employee_name, "leave_type": "Sick Leave"}
		)

		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				self._leave_application(
					self.employee_name, 23, "Second request, no balance left", leave_type="Sick Leave"
				)
			).insert()

		count_after = frappe.db.count(
			"Leave Application", {"employee": self.employee_name, "leave_type": "Sick Leave"}
		)
		self.assertEqual(count_before, count_after)

	def test_half_day_leave_sets_half_totals(self):
		ensure_leave_allocation(self.employee_name, "Casual Leave", 5)

		frappe.set_user(EMPLOYEE_USER)
		doc = frappe.get_doc(
			self._leave_application(
				self.employee_name,
				30,
				"Half day",
				half_day=1,
				half_day_date=add_days(today(), 30),
			)
		)
		doc.insert()

		self.assertEqual(doc.total_leave_days, 0.5)

	def test_cannot_insert_leave_application_for_another_employee(self):
		ensure_leave_allocation(self.manager_name, "Casual Leave", 5)

		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(self._leave_application(self.manager_name, 40, "Sneaky")).insert()

	def test_withdraw_deletes_pending_leave(self):
		ensure_leave_allocation(self.employee_name, "Casual Leave", 5)

		frappe.set_user(EMPLOYEE_USER)
		doc = frappe.get_doc(self._leave_application(self.employee_name, 50, "To be withdrawn"))
		doc.insert()
		self.assertEqual(doc.status, "Open")

		frappe.delete_doc("Leave Application", doc.name)

		self.assertFalse(frappe.db.exists("Leave Application", doc.name))
