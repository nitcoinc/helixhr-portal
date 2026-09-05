import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, today

from helixhr.api import (
	apply_for_leave,
	get_leave_day_count,
	get_leave_form_context,
	get_my_leave,
	get_my_leave_detail,
	withdraw_my_leave,
)
from helixhr.tests.utils import (
	EMPLOYEE_USER,
	MANAGER_USER,
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


class TestPortalLeaveApi(IntegrationTestCase):
	"""P2-U5. The session-scoped leave boundary: what the portal reads, what it
	is allowed to write, and which of the three lifecycle states each row is
	in.

	These replace browser-side `frappe.client.insert` / `frappe.client.delete`
	calls, so the assertions that matter most are the refusals -- the old path
	had none of them anywhere a test could reach.
	"""

	def setUp(self):
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()
		frappe.db.set_value("Employee", self.employee_name, "leave_approver", MANAGER_USER)
		frappe.db.set_value("Employee", self.manager_name, "leave_approver", frappe.session.user)
		self.company = frappe.db.get_value("Employee", self.employee_name, "company")
		ensure_holiday_list_assignment(self.company)
		ensure_leave_allocation(self.employee_name, "Casual Leave", 30)

	def tearDown(self):
		frappe.set_user("Administrator")

	def _apply(self, days_from_today, **kwargs):
		params = {
			"leave_type": "Casual Leave",
			"from_date": add_days(today(), days_from_today),
			"to_date": add_days(today(), days_from_today),
		}
		params.update(kwargs)
		return apply_for_leave(**params)

	# --- scenario 3: the half-day date is never a stale form value ---------

	def test_half_day_always_uses_the_selected_from_date(self):
		"""The old form carried a third date field kept in step by a watcher.
		When the watcher and the user disagreed the request went in with a
		half-day date from an earlier edit; here `from_date` *is* the
		half-day date, so there is nothing to fall out of step."""
		frappe.set_user(EMPLOYEE_USER)
		start = add_days(today(), 60)

		# A deliberately inconsistent caller: a To date three days out, which
		# a half day cannot have.
		result = apply_for_leave(
			leave_type="Casual Leave",
			from_date=start,
			to_date=add_days(today(), 63),
			half_day=1,
		)

		doc = frappe.get_doc("Leave Application", result["name"])
		self.assertEqual(str(doc.half_day_date), str(getdate(start)))
		self.assertEqual(str(doc.from_date), str(getdate(start)))
		self.assertEqual(str(doc.to_date), str(getdate(start)))
		self.assertEqual(doc.total_leave_days, 0.5)

	# --- scenario 4: no approver, no draft --------------------------------

	def test_missing_approver_blocks_submission_and_leaves_no_draft(self):
		frappe.db.set_value("Employee", self.employee_name, "leave_approver", None)
		# get_leave_approval_details falls back to the department's first
		# approver, so the department has to be clear of one too.
		frappe.db.set_value("Employee", self.employee_name, "department", None)
		before = frappe.db.count("Leave Application", {"employee": self.employee_name})

		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.ValidationError):
			self._apply(63)

		frappe.set_user("Administrator")
		self.assertEqual(
			before, frappe.db.count("Leave Application", {"employee": self.employee_name})
		)

	# --- scenario 2: the day count is HRMS's, holidays and all ------------

	def test_a_range_crossing_a_holiday_previews_the_count_hrms_stores(self):
		"""Every leave type on a stock site has `include_holiday` on, which
		means holidays are *not* deducted -- so this makes its own type with
		it off. Without that the assertion would pass for the wrong reason:
		"the preview matches HRMS" is trivially true when neither of them
		skips anything."""
		leave_type = self._holiday_excluding_leave_type()
		ensure_leave_allocation(self.employee_name, leave_type, 10)
		holiday = add_days(today(), 67)
		self._add_holiday(holiday)
		start, end = add_days(today(), 66), add_days(today(), 68)

		frappe.set_user(EMPLOYEE_USER)
		preview = get_leave_day_count(leave_type, start, end)
		result = apply_for_leave(leave_type=leave_type, from_date=start, to_date=end)

		doc = frappe.get_doc("Leave Application", result["name"])
		# The point of the whole endpoint: the number shown before Send is
		# the number the record ends up holding.
		self.assertEqual(preview["total_leave_days"], doc.total_leave_days)
		# Three calendar days, one of them a holiday.
		self.assertEqual(preview["total_leave_days"], 2)
		self.assertIn(str(getdate(holiday)), preview["skipped"])
		self.assertTrue(preview["skipped_label"])

	def _holiday_excluding_leave_type(self):
		name = "_Test Excl Holiday Leave"
		frappe.set_user("Administrator")
		if not frappe.db.exists("Leave Type", name):
			frappe.get_doc(
				{"doctype": "Leave Type", "leave_type_name": name, "include_holiday": 0}
			).insert(ignore_permissions=True)
		return name

	def test_a_reversed_range_is_refused_before_anything_is_read(self):
		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.ValidationError):
			get_leave_day_count("Casual Leave", add_days(today(), 10), add_days(today(), 3))

	def _add_holiday(self, date):
		frappe.set_user("Administrator")
		holiday_list = frappe.get_doc("Holiday List", "_Test Holiday List")
		if not any(str(row.holiday_date) == str(getdate(date)) for row in holiday_list.holidays):
			holiday_list.append(
				"holidays", {"holiday_date": str(getdate(date)), "description": "Test holiday"}
			)
			holiday_list.save(ignore_permissions=True)

	# --- scenario 5: withdrawal, by lifecycle -----------------------------

	def test_withdraw_removes_the_employees_own_open_leave(self):
		frappe.set_user(EMPLOYEE_USER)
		result = self._apply(72)

		withdraw_my_leave(result["name"])

		self.assertFalse(frappe.db.exists("Leave Application", result["name"]))

	def test_withdraw_refuses_another_employees_leave(self):
		ensure_leave_allocation(self.manager_name, "Casual Leave", 5)
		frappe.set_user("Administrator")
		other = frappe.get_doc(
			{
				"doctype": "Leave Application",
				"employee": self.manager_name,
				"leave_type": "Casual Leave",
				"from_date": add_days(today(), 75),
				"to_date": add_days(today(), 75),
				"leave_approver": frappe.session.user,
				"status": "Open",
			}
		).insert(ignore_permissions=True)

		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.PermissionError):
			withdraw_my_leave(other.name)

		frappe.set_user("Administrator")
		self.assertTrue(frappe.db.exists("Leave Application", other.name))

	def test_withdraw_refuses_a_submitted_leave(self):
		frappe.set_user(EMPLOYEE_USER)
		result = self._apply(78)
		frappe.set_user("Administrator")
		approved = frappe.get_doc("Leave Application", result["name"])
		approved.status = "Approved"
		approved.submit()

		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.ValidationError):
			withdraw_my_leave(result["name"])

		frappe.set_user("Administrator")
		self.assertTrue(frappe.db.exists("Leave Application", result["name"]))

	def test_withdraw_refuses_the_legacy_waiting_for_hr_row(self):
		"""P2-U1 step 4's defect state: docstatus 0 with status Approved. It
		never consumed balance and HR is resolving it in Desk; deleting it
		here would destroy the record they were asked to look at."""
		frappe.set_user(EMPLOYEE_USER)
		result = self._apply(82)
		frappe.db.set_value("Leave Application", result["name"], "status", "Approved")

		with self.assertRaises(frappe.ValidationError):
			withdraw_my_leave(result["name"])

		frappe.set_user("Administrator")
		self.assertTrue(frappe.db.exists("Leave Application", result["name"]))

	def test_hr_filed_leave_is_readable_but_not_withdrawable(self):
		"""The `if_owner` delete grant (patches/v1_0/apply_permission_deltas)
		matches `Document.owner`, not `employee`. A leave HR files in Desk
		for an employee is theirs to read and never theirs to delete, so the
		portal must not offer Withdraw on it -- it used to, and the button
		threw a bare PermissionError."""
		frappe.set_user("Administrator")
		hr_filed = frappe.get_doc(
			{
				"doctype": "Leave Application",
				"employee": self.employee_name,
				"leave_type": "Casual Leave",
				"from_date": add_days(today(), 104),
				"to_date": add_days(today(), 104),
				"leave_approver": MANAGER_USER,
				"status": "Open",
			}
		).insert(ignore_permissions=True)

		frappe.set_user(EMPLOYEE_USER)
		row = get_my_leave_detail(hr_filed.name)
		self.assertEqual(row["state"], "open")
		self.assertFalse(row["can_withdraw"])

		with self.assertRaises(frappe.ValidationError) as refused:
			withdraw_my_leave(hr_filed.name)
		self.assertIn("Ask HR", str(refused.exception))

		frappe.set_user("Administrator")
		self.assertTrue(frappe.db.exists("Leave Application", hr_filed.name))

	# --- the projection ----------------------------------------------------

	def test_the_legacy_row_is_waiting_for_hr_and_offers_no_action(self):
		frappe.set_user(EMPLOYEE_USER)
		result = self._apply(86)
		frappe.db.set_value("Leave Application", result["name"], "status", "Approved")

		row = get_my_leave_detail(result["name"])

		self.assertEqual(row["state"], "waiting_for_hr")
		self.assertFalse(row["can_withdraw"])

	def test_an_open_row_names_its_approver_and_can_be_withdrawn(self):
		frappe.set_user(EMPLOYEE_USER)
		result = self._apply(90)

		payload = get_my_leave()
		row = next(r for r in payload["applications"] if r["name"] == result["name"])

		self.assertEqual(row["state"], "open")
		self.assertTrue(row["can_withdraw"])
		self.assertEqual(row["approver"], MANAGER_USER)
		self.assertTrue(row["approver_name"])
		self.assertTrue(payload["balances"])

	def test_a_sent_back_row_carries_the_managers_reason_and_nothing_else(self):
		"""P2-U5 scenario 1. Only a rejected record is asked about at all, and
		only a comment written by somebody other than the employee comes
		back -- an employee's own note on their own record is not a
		manager's reason."""
		frappe.set_user(EMPLOYEE_USER)
		result = self._apply(94)
		own = frappe.get_doc("Leave Application", result["name"])
		own.add_comment("Comment", "My own note, which is not a decision")

		frappe.set_user("Administrator")
		frappe.db.set_value("Leave Application", result["name"], "status", "Rejected")
		frappe.get_doc("Leave Application", result["name"]).add_comment(
			"Comment", "Team offsite that day"
		)

		frappe.set_user(EMPLOYEE_USER)
		row = get_my_leave_detail(result["name"])

		self.assertEqual(row["state"], "sent_back")
		self.assertEqual(row["reason"], "Team offsite that day")
		self.assertTrue(row["can_withdraw"])

	def test_an_approved_row_is_not_withdrawable_from_the_portal(self):
		frappe.set_user(EMPLOYEE_USER)
		result = self._apply(98)
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Leave Application", result["name"])
		doc.status = "Approved"
		doc.submit()

		frappe.set_user(EMPLOYEE_USER)
		row = get_my_leave_detail(result["name"])

		self.assertEqual(row["state"], "approved")
		self.assertFalse(row["can_withdraw"])

	def test_detail_refuses_another_employees_leave(self):
		ensure_leave_allocation(self.manager_name, "Casual Leave", 5)
		frappe.set_user("Administrator")
		other = frappe.get_doc(
			{
				"doctype": "Leave Application",
				"employee": self.manager_name,
				"leave_type": "Casual Leave",
				"from_date": add_days(today(), 102),
				"to_date": add_days(today(), 102),
				"leave_approver": frappe.session.user,
				"status": "Open",
			}
		).insert(ignore_permissions=True)

		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.PermissionError):
			get_my_leave_detail(other.name)

	def test_the_list_is_bounded_and_reports_the_true_total(self):
		frappe.set_user(EMPLOYEE_USER)
		for offset in (106, 110, 114):
			self._apply(offset)

		payload = get_my_leave(limit=2)

		self.assertEqual(len(payload["applications"]), 2)
		self.assertGreaterEqual(payload["total"], 3)
		self.assertEqual(payload["limit"], 2)

	def test_the_form_context_names_the_approver_and_the_balance(self):
		frappe.set_user(EMPLOYEE_USER)

		context = get_leave_form_context()

		self.assertEqual(context["approver"], MANAGER_USER)
		self.assertTrue(context["approver_name"])
		casual = next(t for t in context["types"] if t["leave_type"] == "Casual Leave")
		self.assertIsNotNone(casual["left"])
