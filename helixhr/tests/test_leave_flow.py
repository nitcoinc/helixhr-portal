from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, today

from helixhr.api import (
	_act_on_leave_application,
	_assert_may_act_on,
	act_on_approval,
	apply_for_leave,
	get_approval_detail,
	get_leave_day_count,
	get_leave_form_context,
	get_my_leave,
	get_my_leave_detail,
	withdraw_my_leave,
)
from helixhr.events import backdated_grace_days, backdated_leave_earliest, backdated_leave_reason
from helixhr.tests.utils import (
	EMPLOYEE_USER,
	MANAGER_USER,
	clear_open_leave,
	ensure_holiday_list_assignment,
	ensure_hr_manager_user,
	ensure_leave_allocation,
	ensure_leave_approver_role,
	leave_rules,
	make_test_employee_and_manager,
)


def _token(name):
	"""The concurrency token `act_on_approval` requires: the `modified` and
	status the approver was shown (P2-U7 step 3)."""
	row = frappe.db.get_value("Leave Application", name, ["modified", "status"], as_dict=True)
	return {"expected_modified": str(row.modified), "expected_state": row.status}


class TestLeaveFlow(IntegrationTestCase):
	def setUp(self):
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()
		# Other suites file leave on the shared fixture employee and module
		# discovery (os.walk) is not sorted, so whichever of them ran last
		# may leave Open requests and spent balance behind. Clear the
		# pendings so the pending-aware rule starts this method from what
		# this class itself budgets (TestLeaveApprovalIsNative's pattern).
		# leave_approver isn't auto-fetched from Employee server-side --
		# hrms.hr.doctype.leave_application.leave_application.
		# validate_leave_approver checks the field on the Leave Application
		# itself, which the portal (apply_for_leave) fills from
		# get_employee_leave_approver before insert. Set it
		# directly on Employee so that helper has something to return, and
		# pass it explicitly below the same way the frontend does.
		# frappe.db.set_value writes are visible within this same
		# connection/transaction regardless of session user, so no commit()
		# is needed here -- and a real commit() would break the per-test
		# rollback IntegrationTestCase relies on for isolation between
		# test methods (confirmed: it leaked a Leave Application from one
		# test into the next's overlap check before this was removed).
		clear_open_leave(self.employee_name)
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
		clear_open_leave(self.employee_name)

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
		# get_employee_leave_approver falls back to the department's first
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
		# Earlier methods deliberately leave submitted Casual Leave records on
		# this shared employee. Make this test's three pending applications
		# independent of their spent balance.
		allocation = ensure_leave_allocation(self.employee_name, "Casual Leave", 30)
		frappe.db.set_value(
			"Leave Allocation",
			allocation,
			{"new_leaves_allocated": 30, "total_leaves_allocated": 30},
		)
		# HRMS calculates balance from ledger entries; editing the allocation
		# total alone does not update its already-posted ledger row.
		for entry in frappe.get_all(
			"Leave Ledger Entry",
			filters={"transaction_type": "Leave Allocation", "transaction_name": allocation},
			pluck="name",
		):
			frappe.db.set_value("Leave Ledger Entry", entry, "leaves", 30)
		frappe.set_user(EMPLOYEE_USER)
		# Other leave suites use offsets through 290 (and 96+ even offsets in
		# approval tests), so keep this bounded-list fixture in a separate window.
		for offset in (310, 320, 330):
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

	def test_a_department_only_approver_opens_the_sheet_and_receives_the_request(self):
		"""HRMS's `get_leave_approval_details` checks Department read before
		falling back to the department's approver; the Employee role has
		none, so the sheet used to come back empty and Send was refused."""
		ensure_leave_allocation(self.employee_name, "Casual Leave", 5)
		ensure_holiday_list_assignment(frappe.db.get_value("Employee", self.employee_name, "company"))
		department = self._department_with_leave_approver(MANAGER_USER)
		frappe.db.set_value(
			"Employee", self.employee_name, {"leave_approver": None, "department": department}
		)

		frappe.set_user(EMPLOYEE_USER)
		self.assertFalse(frappe.has_permission("Department", "read", department))
		context = get_leave_form_context()
		self.assertEqual(context["approver"], MANAGER_USER)
		self.assertTrue(context["approver_name"])
		self.assertIn("Casual Leave", [t["leave_type"] for t in context["types"]])

		result = apply_for_leave(
			leave_type="Casual Leave", from_date=add_days(today(), 120), to_date=add_days(today(), 120)
		)
		self.assertEqual(
			frappe.db.get_value("Leave Application", result["name"], "leave_approver"), MANAGER_USER
		)

	def test_the_sheet_lists_the_policy_not_every_unpaid_type(self):
		"""A 0-day policy row gets no Leave Allocation from HRMS, and HRMS
		offers every leave-without-pay type to everybody; the sheet follows
		the employee's own Leave Policy Assignment instead."""
		frappe.set_user("Administrator")
		zero = self._leave_type("_Test Policy Zero Leave", allow_negative=1)
		unpaid = self._leave_type("_Test Policy Unpaid Leave", is_lwp=1)
		other_unpaid = self._leave_type("_Test Unlisted Unpaid Leave", is_lwp=1)
		self._assign_policy([(zero, 0), (unpaid, 0)])
		self.assertFalse(
			frappe.db.exists("Leave Allocation", {"employee": self.employee_name, "leave_type": zero})
		)

		frappe.set_user(EMPLOYEE_USER)
		types = {t["leave_type"]: t for t in get_leave_form_context()["types"]}
		self.assertIn(zero, types)
		self.assertIn(unpaid, types)
		self.assertNotIn(other_unpaid, types)
		self.assertIsNone(types[zero]["left"])

		# Negative-allowed, so HRMS accepts it with no allocation at all.
		ensure_holiday_list_assignment(frappe.db.get_value("Employee", self.employee_name, "company"))
		day = add_days(today(), 122)
		self.assertTrue(apply_for_leave(leave_type=zero, from_date=day, to_date=day)["name"])

	def _department_with_leave_approver(self, approver):
		company = frappe.db.get_value("Employee", self.employee_name, "company")
		name = frappe.db.get_value(
			"Department", {"department_name": "_Test Leave Approver Dept", "company": company}
		)
		doc = (
			frappe.get_doc("Department", name)
			if name
			else frappe.get_doc(
				{"doctype": "Department", "department_name": "_Test Leave Approver Dept", "company": company}
			)
		)
		doc.set("leave_approvers", [{"approver": approver}])
		doc.save(ignore_permissions=True)
		return doc.name

	def _leave_type(self, name, **flags):
		if not frappe.db.exists("Leave Type", name):
			frappe.get_doc({"doctype": "Leave Type", "leave_type_name": name, **flags}).insert(
				ignore_permissions=True
			)
		return name

	def _assign_policy(self, rows):
		"""One submitted Leave Policy Assignment covering today, replacing any
		earlier one (HRMS refuses overlapping assignments)."""
		for name in frappe.get_all(
			"Leave Policy Assignment", filters={"employee": self.employee_name}, pluck="name"
		):
			doc = frappe.get_doc("Leave Policy Assignment", name)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Leave Policy Assignment", name, force=True, ignore_permissions=True)
		policy = frappe.get_doc(
			{
				"doctype": "Leave Policy",
				"title": "_Test Portal Policy",
				"leave_policy_details": [
					{"leave_type": leave_type, "annual_allocation": days} for leave_type, days in rows
				],
			}
		).insert(ignore_permissions=True)
		policy.submit()
		frappe.get_doc(
			{
				"doctype": "Leave Policy Assignment",
				"employee": self.employee_name,
				"leave_policy": policy.name,
				"effective_from": add_days(today(), -30),
				"effective_to": add_days(today(), 300),
			}
		).submit()


class TestLeaveStageAndOutcomes(IntegrationTestCase):
	"""P4-U2. Leave carries the same four outcomes as the workflow kinds
	through HRMS's own lifecycle plus one stage field (P4-KTD4).

	Two of the four were not reachable through `act_on_approval` when this
	class was written -- P4-U3 replaced its two-word vocabulary with the four
	canonical names -- so the final reject and the escalation are driven here
	through the per-kind `act` the dispatcher calls, after the same
	authorization the dispatcher performs. The dispatcher's own vocabulary is
	covered in `test_api_approvals.py`; the refusals are asserted here on the
	public surfaces (`get_approval_detail`, a raw `submit`, a generic
	`set_value`), which is where they actually matter.

	Dates: offsets 78-92, clear of every other leave suite in this repo
	(60-75, 96+, and 102-114) and inside the allocation year.
	"""

	def setUp(self):
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()
		frappe.db.set_value("Employee", self.employee_name, "reports_to", self.manager_name)
		frappe.db.set_value("Employee", self.employee_name, "leave_approver", MANAGER_USER)
		ensure_leave_approver_role(MANAGER_USER)
		self.company = frappe.db.get_value("Employee", self.employee_name, "company")
		ensure_holiday_list_assignment(self.company)
		ensure_leave_allocation(self.employee_name, "Casual Leave", 30)
		self.hr_user = ensure_hr_manager_user()
		clear_open_leave(self.employee_name)

	def tearDown(self):
		frappe.set_user("Administrator")

	# helpers

	def _clear(self, offset):
		frappe.set_user("Administrator")
		date = add_days(today(), offset)
		for name in frappe.get_all(
			"Leave Application",
			filters={"employee": self.employee_name, "from_date": ["<=", str(date)], "to_date": [">=", str(date)]},
			pluck="name",
		):
			doc = frappe.get_doc("Leave Application", name)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Leave Application", name, force=True, ignore_permissions=True)
		return date

	def _open_leave(self, offset, leave_type="Casual Leave"):
		date = self._clear(offset)
		frappe.set_user(EMPLOYEE_USER)
		result = apply_for_leave(leave_type=leave_type, from_date=date, to_date=date)
		frappe.set_user("Administrator")
		return result

	def _hr_approved_type(self):
		"""A Leave Type HR decides for itself (P4-R7)."""
		name = "_Test HR Approved Leave"
		frappe.set_user("Administrator")
		if not frappe.db.exists("Leave Type", name):
			frappe.get_doc(
				{"doctype": "Leave Type", "leave_type_name": name, "helixhr_hr_approves": 1}
			).insert(ignore_permissions=True)
		frappe.db.set_value("Leave Type", name, "helixhr_hr_approves", 1)
		ensure_leave_allocation(self.employee_name, name, 10)
		return name

	def _stage(self, name):
		return frappe.db.get_value("Leave Application", name, "helixhr_stage")

	def _balance(self, date, leave_type="Casual Leave"):
		from hrms.hr.doctype.leave_application.leave_application import get_leave_balance_on

		return get_leave_balance_on(self.employee_name, leave_type, str(date))

	def _act(self, name, action, user):
		"""What `act_on_approval` does, minus the vocabulary P4-U3 owns:
		authorize the session against the stored record, then run the
		lifecycle."""
		frappe.set_user(user)
		doc = frappe.get_doc("Leave Application", name)
		_assert_may_act_on(doc)
		_act_on_leave_application(doc, action)
		return doc

	# --- the stage, and who it hands the request to -----------------------

	def test_a_normal_type_waits_for_the_manager_and_an_hr_type_starts_with_hr(self):
		mine = self._open_leave(78)
		self.assertEqual(self._stage(mine["name"]), "Manager")
		self.assertEqual(mine["stage"], "Manager")

		frappe.set_user(EMPLOYEE_USER)
		self.assertEqual(get_my_leave_detail(mine["name"])["stage"], "Manager")

		# The manager may open it, which is the same check that lets them
		# decide it (`_assert_may_act_on`).
		frappe.set_user(MANAGER_USER)
		self.assertTrue(get_approval_detail("leave", mine["name"]))

		hr_type = self._hr_approved_type()
		theirs = self._open_leave(80, leave_type=hr_type)
		# Written by the employee's own session, through `db_set`, because
		# the field is permlevel 1 and `insert()` would have reset it.
		self.assertEqual(self._stage(theirs["name"]), "HR")
		self.assertEqual(theirs["stage"], "HR")

		frappe.set_user(MANAGER_USER)
		with self.assertRaises(frappe.PermissionError):
			get_approval_detail("leave", theirs["name"])

		frappe.set_user(self.hr_user)
		self.assertTrue(get_approval_detail("leave", theirs["name"]))

	def test_the_form_context_marks_the_types_that_go_straight_to_hr(self):
		hr_type = self._hr_approved_type()

		frappe.set_user(EMPLOYEE_USER)
		types = {row["leave_type"]: row for row in get_leave_form_context()["types"]}

		self.assertTrue(types[hr_type]["hr_approves"])
		self.assertFalse(types["Casual Leave"]["hr_approves"])

	def test_send_to_hr_moves_the_queue_and_nothing_else(self):
		mine = self._open_leave(82)

		self._act(mine["name"], "Send to HR", MANAGER_USER)

		frappe.set_user("Administrator")
		doc = frappe.get_doc("Leave Application", mine["name"])
		self.assertEqual(doc.helixhr_stage, "HR")
		# HRMS's own lifecycle is untouched: still Open, still unsubmitted,
		# and still the manager on `leave_approver` -- HRMS requires one and
		# shares the document with them (P4-KTD4).
		self.assertEqual(doc.status, "Open")
		self.assertEqual(doc.docstatus, 0)
		self.assertEqual(doc.leave_approver, MANAGER_USER)

		frappe.set_user(MANAGER_USER)
		with self.assertRaises(frappe.PermissionError):
			get_approval_detail("leave", mine["name"])

		frappe.set_user(self.hr_user)
		self.assertTrue(get_approval_detail("leave", mine["name"]))

	# --- the raw routes (P4-R8, P4-R8a) -----------------------------------

	def test_the_employee_cannot_move_their_own_request_into_the_hr_queue(self):
		mine = self._open_leave(84)

		frappe.set_user(EMPLOYEE_USER)
		try:
			frappe.client.set_value("Leave Application", mine["name"], "helixhr_stage", "HR")
		except frappe.PermissionError:
			pass

		frappe.set_user("Administrator")
		# Refused outright, or silently reset by
		# `reset_values_if_no_permlevel_access` -- either way the queue did
		# not move.
		self.assertEqual(self._stage(mine["name"]), "Manager")

	def test_the_approvers_share_cannot_submit_a_request_that_is_with_hr(self):
		"""HRMS shares every application with its `leave_approver` at
		`submit=1`, so the manager of an escalated request keeps a Desk route
		to Approve that never consults the portal. `before_submit` is what
		closes it."""
		mine = self._open_leave(86)
		self._act(mine["name"], "Send to HR", MANAGER_USER)

		frappe.set_user(MANAGER_USER)
		doc = frappe.get_doc("Leave Application", mine["name"])
		doc.status = "Approved"
		with self.assertRaises(frappe.PermissionError):
			doc.submit()

		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Leave Application", mine["name"], "docstatus"), 0)

		frappe.set_user(self.hr_user)
		hr_doc = frappe.get_doc("Leave Application", mine["name"])
		hr_doc.status = "Approved"
		hr_doc.submit()

		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Leave Application", mine["name"], "docstatus"), 1)

	def test_the_approvers_share_cannot_send_back_a_request_that_is_with_hr(self):
		"""P4-R8a on the route that never submits.

		Send back is `status = "Rejected"` at docstatus 0 -- a save -- so
		`before_submit` never sees it, and HRMS's `submit=1` DocShare leaves
		the manager write on an application that was escalated. `validate`
		is what closes it, on both the generic `set_value` and a plain save.
		"""
		mine = self._open_leave(90)
		self._act(mine["name"], "Send to HR", MANAGER_USER)

		frappe.set_user(MANAGER_USER)
		with self.assertRaises(frappe.PermissionError):
			frappe.client.set_value("Leave Application", mine["name"], "status", "Rejected")

		frappe.set_user(MANAGER_USER)
		doc = frappe.get_doc("Leave Application", mine["name"])
		doc.status = "Rejected"
		with self.assertRaises(frappe.PermissionError):
			doc.save()

		frappe.set_user("Administrator")
		stored = frappe.db.get_value(
			"Leave Application", mine["name"], ["status", "docstatus", "helixhr_stage"], as_dict=True
		)
		self.assertEqual(stored.status, "Open")
		self.assertEqual(stored.docstatus, 0)
		self.assertEqual(stored.helixhr_stage, "HR")

		# HR still decides it, on the same route.
		frappe.set_user(self.hr_user)
		hr_doc = frappe.get_doc("Leave Application", mine["name"])
		hr_doc.status = "Rejected"
		hr_doc.save()

		frappe.set_user("Administrator")
		self.assertEqual(
			frappe.db.get_value("Leave Application", mine["name"], "status"), "Rejected"
		)

	def test_nobody_submits_their_own_leave_even_holding_hr_manager(self):
		"""P4-R8's leave half. The HR-Manager role carries submit on Leave
		Application, so without this hook an HR Manager could approve their
		own days from Desk."""
		mine = self._open_leave(88)
		self._grant_hr_manager(EMPLOYEE_USER)
		# HRMS has its own self-approval refusal, but it is a *setting*
		# (`HR Settings.prevent_self_leave_approval`), it only looks at
		# status Approved, and it stands down entirely if a Workflow is ever
		# added to Leave Application. Turned off here so what the assertion
		# proves is this app's hook and not HRMS's.
		self._allow_self_approval()

		frappe.set_user(EMPLOYEE_USER)
		doc = frappe.get_doc("Leave Application", mine["name"])
		doc.status = "Approved"
		with self.assertRaises(frappe.PermissionError):
			doc.submit()

		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Leave Application", mine["name"], "docstatus"), 0)

	def _allow_self_approval(self):
		frappe.set_user("Administrator")
		before = frappe.db.get_single_value("HR Settings", "prevent_self_leave_approval")
		frappe.db.set_single_value("HR Settings", "prevent_self_leave_approval", 0)
		self.addCleanup(
			frappe.db.set_single_value, "HR Settings", "prevent_self_leave_approval", before
		)

	def _grant_hr_manager(self, user):
		frappe.set_user("Administrator")
		doc = frappe.get_doc("User", user)
		if "HR Manager" not in {row.role for row in doc.roles}:
			doc.append("roles", {"role": "HR Manager"})
			doc.save(ignore_permissions=True)
			self.addCleanup(self._revoke_hr_manager, user)

	def _revoke_hr_manager(self, user):
		frappe.set_user("Administrator")
		doc = frappe.get_doc("User", user)
		doc.roles = [row for row in doc.roles if row.role != "HR Manager"]
		doc.save(ignore_permissions=True)

	# --- the two rejections, which are not the same thing -----------------

	def test_a_final_reject_submits_consumes_nothing_and_frees_the_dates(self):
		date = self._clear(90)
		mine = self._open_leave(90)
		before = self._balance(date)

		self._act(mine["name"], "Reject", MANAGER_USER)

		frappe.set_user("Administrator")
		doc = frappe.get_doc("Leave Application", mine["name"])
		self.assertEqual(doc.status, "Rejected")
		self.assertEqual(doc.docstatus, 1)
		self.assertEqual(
			frappe.get_all(
				"Leave Ledger Entry", filters={"transaction_name": mine["name"], "docstatus": 1}
			),
			[],
		)
		self.assertEqual(self._balance(date), before)

		frappe.set_user(EMPLOYEE_USER)
		row = get_my_leave_detail(mine["name"])
		self.assertEqual(row["state"], "rejected")
		self.assertFalse(row["can_withdraw"])

		# Terminal for the row, not for the dates: HRMS's overlap rule
		# ignores a Rejected application, so the employee can ask again.
		again = apply_for_leave(leave_type="Casual Leave", from_date=date, to_date=date)
		self.assertTrue(again["name"])

	def test_a_send_back_still_stays_unsubmitted_and_withdrawable(self):
		mine = self._open_leave(92)

		frappe.set_user(MANAGER_USER)
		act_on_approval(
			"Leave Application",
			mine["name"],
			"Send Back",
			comment="Cover the Friday first",
			**_token(mine["name"]),
		)

		frappe.set_user("Administrator")
		doc = frappe.get_doc("Leave Application", mine["name"])
		self.assertEqual(doc.status, "Rejected")
		self.assertEqual(doc.docstatus, 0)
		self.assertEqual(doc.helixhr_stage, "Manager")

		frappe.set_user(EMPLOYEE_USER)
		row = get_my_leave_detail(mine["name"])
		self.assertEqual(row["state"], "sent_back")
		self.assertTrue(row["can_withdraw"])
		self.assertEqual(row["reason"], "Cover the Friday first")

		withdraw_my_leave(mine["name"])
		self.assertFalse(frappe.db.exists("Leave Application", mine["name"]))


class TestPendingAwareBalance(IntegrationTestCase):
	"""U1 (R1-R3). A request is refused when it does not fit the balance once
	the employee's other Open, unsubmitted requests of that type are counted.

	Each test uses its own Leave Type with `include_holiday` on, so every
	calendar day counts as one day and no other suite's balance leaks in.
	Dates: offsets 130-290, unique per test (HRMS refuses overlaps across
	types) and clear of the other leave suites.
	"""

	def setUp(self):
		self.employee_name, _, _, _ = make_test_employee_and_manager()
		frappe.db.set_value("Employee", self.employee_name, "leave_approver", "Administrator")
		ensure_holiday_list_assignment(frappe.db.get_value("Employee", self.employee_name, "company"))

	def tearDown(self):
		frappe.set_user("Administrator")

	def _type(self, suffix, leaves, **flags):
		frappe.set_user("Administrator")
		name = f"_Test U1 {suffix}"
		if not frappe.db.exists("Leave Type", name):
			frappe.get_doc(
				{"doctype": "Leave Type", "leave_type_name": name, "include_holiday": 1, **flags}
			).insert(ignore_permissions=True)
		for row in frappe.get_all(
			"Leave Application", filters={"employee": self.employee_name, "leave_type": name}, pluck="name"
		):
			doc = frappe.get_doc("Leave Application", row)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Leave Application", row, force=True, ignore_permissions=True)
		if leaves:
			ensure_leave_allocation(self.employee_name, name, leaves)
		return name

	def _apply(self, leave_type, offset, days=1, **extra):
		frappe.set_user(EMPLOYEE_USER)
		doc = frappe.get_doc(
			{
				"doctype": "Leave Application",
				"employee": self.employee_name,
				"leave_type": leave_type,
				"from_date": add_days(today(), offset),
				"to_date": add_days(today(), offset + days - 1),
				"leave_approver": "Administrator",
				**extra,
			}
		).insert()
		frappe.set_user("Administrator")
		return doc

	def test_two_pending_requests_that_together_overdraw_are_refused(self):
		leave_type = self._type("Overdraw", 3)
		self._apply(leave_type, 130, days=2)
		with self.assertRaises(frappe.ValidationError) as caught:
			self._apply(leave_type, 133, days=2)
		self.assertIn("2 already waiting for approval", str(caught.exception))
		self.assertIn("3 days left", str(caught.exception))

	def test_a_request_that_fits_beside_the_pending_one_is_accepted(self):
		leave_type = self._type("Fits", 3)
		self._apply(leave_type, 136, days=2)
		self.assertEqual(self._apply(leave_type, 139, days=1).status, "Open")

	def test_half_days_count_as_half_against_the_balance(self):
		leave_type = self._type("Half", 0.5)
		first = self._apply(leave_type, 142, half_day=1, half_day_date=add_days(today(), 142))
		self.assertEqual(first.total_leave_days, 0.5)
		with self.assertRaises(frappe.ValidationError):
			self._apply(leave_type, 144, half_day=1, half_day_date=add_days(today(), 144))

	def test_allow_negative_and_lwp_types_are_never_refused_by_this_rule(self):
		# HRMS refuses an allocation on an LWP type, so that one has none.
		for suffix, leaves, flags, offset in (
			("Negative", 1, {"allow_negative": 1}, 146),
			("LWP", 0, {"is_lwp": 1}, 150),
		):
			leave_type = self._type(suffix, leaves, **flags)
			self._apply(leave_type, offset)
			self.assertEqual(self._apply(leave_type, offset + 2).status, "Open")

	def _overdrawn_pair(self, suffix, offset):
		"""Open A (2 days) and Open B whose stored total is bumped to 2, so A
		no longer fits beside B -- the state a shrunken allocation leaves."""
		leave_type = self._type(suffix, 3)
		a = self._apply(leave_type, offset, days=2)
		b = self._apply(leave_type, offset + 3, days=1)
		frappe.db.set_value("Leave Application", b.name, "total_leave_days", 2)
		return a

	def test_the_approvers_submit_is_not_gated_by_the_pending_rule(self):
		a = self._overdrawn_pair("Submit", 155)
		doc = frappe.get_doc("Leave Application", a.name)
		doc.status = "Approved"
		doc.submit()
		self.assertEqual(doc.docstatus, 1)

	def test_a_request_does_not_count_itself_on_an_unrelated_edit(self):
		a = self._overdrawn_pair("Self", 161)
		frappe.set_user(EMPLOYEE_USER)
		doc = frappe.get_doc("Leave Application", a.name)
		doc.description = "Updated reason"
		doc.save()
		self.assertEqual(doc.description, "Updated reason")

	def test_a_date_change_reruns_the_rule(self):
		a = self._overdrawn_pair("Dates", 167)
		frappe.set_user(EMPLOYEE_USER)
		doc = frappe.get_doc("Leave Application", a.name)
		doc.from_date, doc.to_date = add_days(today(), 172), add_days(today(), 173)
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_a_sent_back_request_resent_into_an_overdraw_is_refused(self):
		leave_type = self._type("Resend", 3)
		a = self._apply(leave_type, 176, days=2)
		sent_back = frappe.get_doc("Leave Application", a.name)
		sent_back.status = "Rejected"
		sent_back.save()
		self._apply(leave_type, 179, days=2)

		resent = frappe.get_doc("Leave Application", a.name)
		resent.status = "Open"
		with self.assertRaises(frappe.ValidationError):
			resent.save()

	def test_pending_counts_across_the_whole_allocation_period(self):
		leave_type = self._type("Period", 3)
		self._apply(leave_type, 200, days=2)
		with self.assertRaises(frappe.ValidationError):
			self._apply(leave_type, 290, days=2)

	def test_the_preview_reports_pending_and_the_blocked_reason(self):
		leave_type = self._type("Preview", 3)
		self._apply(leave_type, 185, days=2)
		frappe.set_user(EMPLOYEE_USER)
		start, end = add_days(today(), 188), add_days(today(), 189)
		result = get_leave_day_count(leave_type, start, end)
		self.assertEqual(result["pending"], 2)
		self.assertIn("2 already waiting for approval", result["blocked_reason"])

		self.assertIsNone(get_leave_day_count(leave_type, start, start)["blocked_reason"])

	def test_the_preview_names_the_consecutive_days_limit(self):
		leave_type = self._type("Limit", 10)
		frappe.db.set_value("Leave Type", leave_type, "max_continuous_days_allowed", 2)
		frappe.set_user(EMPLOYEE_USER)
		result = get_leave_day_count(leave_type, add_days(today(), 140), add_days(today(), 142))
		self.assertEqual(result["max_continuous"], 2)
		self.assertEqual(result["blocked_reason"], f"{leave_type} allows at most 2 days in one request.")


class TestBackdatedGrace(IntegrationTestCase):
	"""U3 (R6): an employee may start leave up to N working days back; HR
	Manager and the configured exempt role are unlimited; the rule runs on
	insert and on a start-date change, never on approver submit.

	Dates: offsets -1 to -12, the only past window any leave suite books;
	setUp clears it (cancelling anything approved) because nothing rolls
	back between methods here. `_holiday_dates` is patched where the walk's
	answer must not depend on which weekday the suite runs.
	"""

	def setUp(self):
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()
		frappe.db.set_value("Employee", self.employee_name, "leave_approver", MANAGER_USER)
		ensure_leave_approver_role(MANAGER_USER)
		self.company = frappe.db.get_value("Employee", self.employee_name, "company")
		ensure_holiday_list_assignment(self.company)
		ensure_leave_allocation(self.employee_name, "Casual Leave", 30)
		clear_open_leave(self.employee_name)
		for name in frappe.get_all(
			"Leave Application",
			filters={
				"employee": self.employee_name,
				"from_date": ["<=", add_days(today(), -1)],
				"to_date": [">=", add_days(today(), -12)],
				"docstatus": ["<", 2],
			},
			pluck="name",
		):
			doc = frappe.get_doc("Leave Application", name)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Leave Application", name, force=True, ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")

	def _conf(self, grace_days=1, exempt_role=None):
		"""The HelixHR Leave Rules Single the rule now reads (plan
		2026-10-06-001 U1), set for the `with` block. Same call shape the
		old site-config helper had."""
		return leave_rules(grace_days=grace_days, exempt_role=exempt_role)

	def _apply(self, offset):
		day = add_days(today(), offset)
		return apply_for_leave(leave_type="Casual Leave", from_date=day, to_date=day)

	# --- the working-day walk ------------------------------------------------

	def test_a_weekend_is_not_counted_so_friday_is_within_one_day_of_monday(self):
		monday = getdate("2026-09-28")
		weekend = {"2026-09-26", "2026-09-27"}
		frappe.set_user(EMPLOYEE_USER)
		with self._conf(grace_days=1), patch("helixhr.api._holiday_dates", return_value=weekend):
			self.assertEqual(backdated_leave_earliest(self.employee_name, monday), getdate("2026-09-25"))
			self.assertIsNone(backdated_leave_reason(self.employee_name, "2026-09-25", monday))
			reason = backdated_leave_reason(self.employee_name, "2026-09-24", monday)
		self.assertIn("Leave can start no earlier than", reason)
		self.assertIn("ask HR", reason)

	def test_no_holiday_list_counts_calendar_days(self):
		monday = getdate("2026-09-28")
		frappe.set_user(EMPLOYEE_USER)
		with self._conf(grace_days=1), patch("helixhr.api._holiday_dates", return_value=None):
			self.assertEqual(backdated_leave_earliest(self.employee_name, monday), getdate("2026-09-27"))
			self.assertIsNone(backdated_leave_reason(self.employee_name, "2026-09-27", monday))
			self.assertTrue(backdated_leave_reason(self.employee_name, "2026-09-26", monday))

	def test_grace_defaults_to_one_day(self):
		# Unset in the Single -- no stored value -- reads as 1.
		with patch("helixhr.events.leave_rule_stored", return_value=None):
			self.assertEqual(backdated_grace_days(), 1)

	# --- insert, edit, approve -----------------------------------------------

	def test_yesterday_is_accepted_and_two_days_ago_is_refused(self):
		frappe.set_user(EMPLOYEE_USER)
		with self._conf(grace_days=1), patch("helixhr.api._holiday_dates", return_value=None):
			self.assertTrue(self._apply(-1)["name"])
			with self.assertRaises(frappe.ValidationError) as caught:
				self._apply(-3)
		self.assertIn("Leave can start no earlier than", str(caught.exception))

	def test_an_exempt_role_files_ten_days_back(self):
		frappe.set_user(EMPLOYEE_USER)
		with self._conf(grace_days=1, exempt_role="Employee"):
			self.assertTrue(self._apply(-10)["name"])

	def test_zero_days_still_blocks_a_backdated_start(self):
		"""0 is a real value, not "unset": nothing may start before today."""
		frappe.set_user(EMPLOYEE_USER)
		with self._conf(grace_days=0):
			self.assertEqual(backdated_grace_days(), 0)
			with (
				patch("helixhr.api._holiday_dates", return_value=None),
				self.assertRaises(frappe.ValidationError),
			):
				self._apply(-1)

	def test_an_unrelated_edit_on_an_old_open_request_does_not_trigger_the_rule(self):
		frappe.set_user(EMPLOYEE_USER)
		with self._conf(grace_days=30):
			name = self._apply(-8)["name"]
		with self._conf(grace_days=1):
			doc = frappe.get_doc("Leave Application", name)
			doc.description = "Edited later"
			doc.save()
			doc.reload()
			doc.from_date = doc.to_date = add_days(today(), -9)
			with self.assertRaises(frappe.ValidationError):
				doc.save()

	def test_an_approver_submits_a_request_after_the_grace_window_passed(self):
		frappe.set_user(EMPLOYEE_USER)
		with self._conf(grace_days=30):
			name = self._apply(-6)["name"]
		with self._conf(grace_days=1):
			frappe.set_user(MANAGER_USER)
			doc = frappe.get_doc("Leave Application", name)
			_assert_may_act_on(doc)
			_act_on_leave_application(doc, "Approve")
		self.assertEqual(frappe.db.get_value("Leave Application", name, "docstatus"), 1)

	# --- the preview ---------------------------------------------------------

	def test_the_preview_blocks_a_too_old_start_with_the_grace_sentence(self):
		frappe.set_user(EMPLOYEE_USER)
		day = add_days(today(), -5)
		with self._conf(grace_days=1), patch("helixhr.api._holiday_dates", return_value=None):
			result = get_leave_day_count("Casual Leave", day, day)
		self.assertEqual(result["earliest_start"], str(add_days(today(), -1)))
		self.assertIn("Leave can start no earlier than", result["blocked_reason"])


class TestBackdatedLeaveRulesPatch(IntegrationTestCase):
	"""U1/KTD2, R4: the two site-config values carry into the HelixHR Leave
	Rules Single once, and a rerun never overwrites an HR edit."""

	SINGLE = "HelixHR Leave Rules"

	def _unset(self):
		frappe.db.set_single_value(self.SINGLE, "backdated_grace_days", None)
		frappe.db.set_single_value(self.SINGLE, "backdated_exempt_role", None)

	def test_the_patch_copies_site_config_into_the_single(self):
		from helixhr.patches.v1_0.migrate_backdated_leave_rules import execute

		self._unset()
		with patch.dict(
			frappe.conf,
			{"helixhr_backdated_leave_grace_days": 4, "helixhr_backdated_leave_exempt_role": "HR User"},
		):
			execute()
		self.assertEqual(frappe.db.get_single_value(self.SINGLE, "backdated_grace_days"), 4)
		self.assertEqual(frappe.db.get_single_value(self.SINGLE, "backdated_exempt_role"), "HR User")

	def test_a_second_run_keeps_an_hr_edit(self):
		from helixhr.patches.v1_0.migrate_backdated_leave_rules import execute

		self._unset()
		with patch.dict(frappe.conf, {"helixhr_backdated_leave_grace_days": 4}):
			execute()
		frappe.db.set_single_value(self.SINGLE, "backdated_grace_days", 7)
		with patch.dict(frappe.conf, {"helixhr_backdated_leave_grace_days": 4}):
			execute()
		self.assertEqual(frappe.db.get_single_value(self.SINGLE, "backdated_grace_days"), 7)

	def test_no_config_leaves_the_single_unset(self):
		from helixhr.events import leave_rule_stored
		from helixhr.patches.v1_0.migrate_backdated_leave_rules import execute

		self._unset()
		with patch.dict(frappe.conf) as conf:
			conf.pop("helixhr_backdated_leave_grace_days", None)
			conf.pop("helixhr_backdated_leave_exempt_role", None)
			execute()
		self.assertIsNone(leave_rule_stored("backdated_grace_days"))
		# And the rule still reads the documented default of 1.
		self.assertEqual(backdated_grace_days(), 1)

	def test_an_out_of_range_grace_is_clamped_not_fatal(self):
		"""Review fix: the old reader tolerated any value, so migrate must not
		abort on one (`doc.save()` validation would have thrown)."""
		from helixhr.patches.v1_0.migrate_backdated_leave_rules import execute

		self._unset()
		with patch.dict(frappe.conf, {"helixhr_backdated_leave_grace_days": 400}):
			execute()
		self.assertEqual(frappe.db.get_single_value(self.SINGLE, "backdated_grace_days"), 365)

		self._unset()
		with patch.dict(frappe.conf, {"helixhr_backdated_leave_grace_days": -5}):
			execute()
		self.assertEqual(frappe.db.get_single_value(self.SINGLE, "backdated_grace_days"), 0)

	def test_a_deleted_exempt_role_is_skipped_not_fatal(self):
		from helixhr.events import leave_rule_stored
		from helixhr.patches.v1_0.migrate_backdated_leave_rules import execute

		self._unset()
		with patch.dict(frappe.conf, {"helixhr_backdated_leave_exempt_role": "No Such Role"}):
			execute()
		self.assertIsNone(leave_rule_stored("backdated_exempt_role"))

	def test_a_role_only_config_is_not_reapplied_over_an_edit(self):
		"""Review fix: the guard must key on both fields, or a config with only
		the exempt role re-applies it over a later HR edit on the next run."""
		from helixhr.events import leave_rule_stored
		from helixhr.patches.v1_0.migrate_backdated_leave_rules import execute

		self._unset()
		with patch.dict(frappe.conf, {"helixhr_backdated_leave_exempt_role": "HR User"}):
			execute()
		self.assertEqual(leave_rule_stored("backdated_exempt_role"), "HR User")
		frappe.db.set_single_value(self.SINGLE, "backdated_exempt_role", "IT Team")
		with patch.dict(frappe.conf, {"helixhr_backdated_leave_exempt_role": "HR User"}):
			execute()
		self.assertEqual(leave_rule_stored("backdated_exempt_role"), "IT Team")
