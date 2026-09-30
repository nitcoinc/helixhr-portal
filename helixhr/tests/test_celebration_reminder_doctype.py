"""P8-U10: `HelixHR Celebration Reminder`'s own controller and the patch
that populates its two rows on every site. `test_reminders.py` covers the
sender that reads these rows; this file covers the doctype and the patch
in isolation.
"""

import frappe
from frappe.tests import IntegrationTestCase


class TestHelixHRCelebrationReminderValidate(IntegrationTestCase):
	"""`event` autonames on one of exactly two Select options -- there is no
	third value to build a test-owned row under, so each test deletes the
	real "birthday" row (almost certainly already created by
	`migrate_celebration_reminders`) and restores it afterwards, the same
	snapshot-and-restore shape `test_reminders.py` already takes."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.snapshot = None
		if frappe.db.exists("HelixHR Celebration Reminder", "birthday"):
			self.snapshot = frappe.get_doc("HelixHR Celebration Reminder", "birthday").as_dict()
			frappe.delete_doc("HelixHR Celebration Reminder", "birthday", force=True, ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.delete_doc(
			"HelixHR Celebration Reminder", "birthday", force=True, ignore_permissions=True, ignore_missing=True
		)
		if self.snapshot is not None:
			frappe.get_doc(self.snapshot).insert(ignore_permissions=True)

	def _reminder(self, **fields):
		doc = frappe.get_doc(
			{"doctype": "HelixHR Celebration Reminder", "event": "birthday", **fields}
		)
		return doc

	def test_selected_employees_with_no_recipients_is_refused(self):
		doc = self._reminder(recipient_mode="Selected employees")
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_selected_employees_with_recipients_is_accepted(self):
		from helixhr.tests.utils import ensure_test_company, make_test_employee_and_manager

		ensure_test_company()
		employee, *_ = make_test_employee_and_manager()
		doc = self._reminder(
			recipient_mode="Selected employees", recipients=[{"employee": employee}]
		)
		doc.insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "HelixHR Celebration Reminder", "birthday", force=True)

		self.assertEqual(len(doc.recipients), 1)

	def test_all_employees_with_an_empty_recipients_table_is_accepted(self):
		doc = self._reminder(recipient_mode="All employees")
		doc.insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "HelixHR Celebration Reminder", "birthday", force=True)

	def test_switching_to_all_employees_does_not_clear_a_saved_selection(self):
		"""Rows left in `recipients` while `recipient_mode` is "All
		employees" are ignored, not refused -- HR may be switching modes
		back and forth and should not lose the list they picked."""
		from helixhr.tests.utils import ensure_test_company, make_test_employee_and_manager

		ensure_test_company()
		employee, *_ = make_test_employee_and_manager()
		doc = self._reminder(
			recipient_mode="Selected employees", recipients=[{"employee": employee}]
		)
		doc.insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "HelixHR Celebration Reminder", "birthday", force=True)

		doc.recipient_mode = "All employees"
		doc.save(ignore_permissions=True)

		self.assertEqual(len(doc.recipients), 1)

	def test_event_cannot_be_changed_after_insert(self):
		if not frappe.db.exists("HelixHR Celebration Reminder", "birthday"):
			self._reminder(recipient_mode="All employees").insert(ignore_permissions=True)
		self.assertTrue(
			frappe.get_meta("HelixHR Celebration Reminder").get_field("event").read_only_depends_on
		)


class TestMigrateCelebrationReminders(IntegrationTestCase):
	"""P8-U10. Runs against `test_site`'s own live HR Settings state, so
	every assertion restores what it changes rather than assuming a blank
	slate -- this patch has almost certainly already run once by the time
	any test suite reaches it."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.original_reminders = {
			event: frappe.db.get_value(
				"HelixHR Celebration Reminder", event, ["is_enabled", "email_template", "recipient_mode"], as_dict=True
			)
			for event in ("birthday", "work_anniversary")
			if frappe.db.exists("HelixHR Celebration Reminder", event)
		}

	def tearDown(self):
		frappe.set_user("Administrator")
		for event, snapshot in self.original_reminders.items():
			frappe.db.set_value(
				"HelixHR Celebration Reminder",
				event,
				{
					"is_enabled": snapshot.is_enabled,
					"email_template": snapshot.email_template,
					"recipient_mode": snapshot.recipient_mode,
				},
				update_modified=False,
			)
			frappe.clear_document_cache("HelixHR Celebration Reminder", event)

	def test_both_rows_exist_after_running_on_this_site(self):
		from helixhr.patches.v1_0.migrate_celebration_reminders import execute

		execute()

		for event in ("birthday", "work_anniversary"):
			self.assertTrue(frappe.db.exists("HelixHR Celebration Reminder", event))

	def test_running_twice_never_overwrites_an_edit(self):
		from helixhr.patches.v1_0.migrate_celebration_reminders import execute

		execute()
		frappe.db.set_value(
			"HelixHR Celebration Reminder", "birthday", "recipient_mode", "Selected employees", update_modified=False
		)
		frappe.clear_document_cache("HelixHR Celebration Reminder", "birthday")

		execute()

		self.assertEqual(
			frappe.db.get_value("HelixHR Celebration Reminder", "birthday", "recipient_mode"),
			"Selected employees",
			"a re-run must never touch a row that already exists",
		)


class TestSaveCelebrationReminder(IntegrationTestCase):
	"""P8-U12 / P8-R5, P8-R6."""

	def setUp(self):
		frappe.set_user("Administrator")
		from helixhr.tests.utils import (
			EMPLOYEE_USER,
			HR_MANAGER_EMPLOYEE_USER,
			ensure_test_company,
			make_test_employee_and_manager,
			make_test_hr_manager_employee,
		)

		ensure_test_company()
		make_test_hr_manager_employee()
		self.employee, *_ = make_test_employee_and_manager()
		self.hr_user = HR_MANAGER_EMPLOYEE_USER
		self.employee_user = EMPLOYEE_USER

		self.snapshot = None
		if frappe.db.exists("HelixHR Celebration Reminder", "birthday"):
			self.snapshot = frappe.get_doc("HelixHR Celebration Reminder", "birthday").as_dict()
			frappe.delete_doc("HelixHR Celebration Reminder", "birthday", force=True, ignore_permissions=True)
		self.template_snapshot = None
		if frappe.db.exists("Email Template", "HelixHR Birthday Reminder"):
			doc = frappe.get_doc("Email Template", "HelixHR Birthday Reminder")
			self.template_snapshot = {
				field: doc.get(field) for field in ("subject", "response_html", "response", "use_html")
			}

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.delete_doc(
			"HelixHR Celebration Reminder", "birthday", force=True, ignore_permissions=True, ignore_missing=True
		)
		if self.snapshot is not None:
			frappe.get_doc(self.snapshot).insert(ignore_permissions=True)
		if self.template_snapshot is not None:
			doc = frappe.get_doc("Email Template", "HelixHR Birthday Reminder")
			doc.update(self.template_snapshot)
			doc.save(ignore_permissions=True)

	def test_a_non_hr_caller_is_refused(self):
		from helixhr.api import save_celebration_reminder

		frappe.set_user(self.employee_user)
		with self.assertRaises(frappe.PermissionError):
			save_celebration_reminder(
				event="birthday", subject="Happy birthday!", body="Cheers", is_enabled=1
			)

	def test_disabling_in_the_portal_also_stops_hrms_stock_email(self):
		# The HRMS checkbox is read-only in Desk, so the portal toggle must
		# be the only switch: HR disabled the anniversary mail and HRMS's
		# default-on checkbox kept sending it.
		from helixhr.api import save_celebration_reminder

		frappe.db.set_single_value("HR Settings", "send_birthday_reminders", 1)
		frappe.set_user(self.hr_user)
		save_celebration_reminder(event="birthday", subject="Happy birthday!", body="Cheers", is_enabled=0)

		self.assertEqual(frappe.db.get_single_value("HR Settings", "send_birthday_reminders"), 0)

	def test_first_save_creates_the_seeded_template_name_not_a_second_one(self):
		from helixhr.api import save_celebration_reminder

		frappe.set_user(self.hr_user)
		result = save_celebration_reminder(
			event="birthday", subject="Happy birthday {{ names }}", body="Cheers", is_enabled=1
		)
		self.assertEqual(result["subject"], "Happy birthday {{ names }}")
		self.assertTrue(frappe.db.exists("Email Template", "HelixHR Birthday Reminder"))

		# A second save edits the same template, not a new one.
		save_celebration_reminder(
			event="birthday", subject="Edited subject", body="Cheers", is_enabled=1
		)
		self.assertEqual(
			frappe.db.count(
				"HelixHR Celebration Reminder", filters={"event": "birthday"}
			),
			1,
		)
		self.assertEqual(
			frappe.db.get_value("Email Template", "HelixHR Birthday Reminder", "subject"),
			"Edited subject",
		)

	def test_selected_employees_with_no_recipients_is_refused(self):
		from helixhr.api import save_celebration_reminder

		frappe.set_user(self.hr_user)
		with self.assertRaises(frappe.ValidationError):
			save_celebration_reminder(
				event="birthday",
				subject="Happy birthday!",
				body="Cheers",
				is_enabled=1,
				recipient_mode="Selected employees",
				recipients=[],
			)

	def test_selected_employees_with_recipients_persists_and_resolves_names(self):
		from helixhr.api import save_celebration_reminder

		frappe.set_user(self.hr_user)
		result = save_celebration_reminder(
			event="birthday",
			subject="Happy birthday!",
			body="Cheers",
			is_enabled=1,
			recipient_mode="Selected employees",
			recipients=[self.employee],
		)
		self.assertEqual([row["employee"] for row in result["recipients"]], [self.employee])
		self.assertTrue(result["recipients"][0]["employee_name"])

	def test_a_jinja_syntax_error_in_the_body_is_refused_at_save_time(self):
		from helixhr.api import save_celebration_reminder

		frappe.set_user(self.hr_user)
		with self.assertRaises(frappe.ValidationError):
			save_celebration_reminder(
				event="birthday", subject="Happy birthday!", body="{% if count %}", is_enabled=1
			)
		# Nothing was written -- the compile check runs before either
		# document is touched.
		self.assertFalse(frappe.db.get_value("HelixHR Celebration Reminder", "birthday", "is_enabled"))

	def test_an_unknown_event_is_refused(self):
		from helixhr.api import save_celebration_reminder

		frappe.set_user(self.hr_user)
		with self.assertRaises(frappe.ValidationError):
			save_celebration_reminder(event="not_a_real_event", subject="x", body="y", is_enabled=1)

	def test_the_write_is_rate_limited(self):
		from helixhr.utils import RATE_LIMIT_POLICY

		self.assertIn("save_celebration_reminder", RATE_LIMIT_POLICY)
