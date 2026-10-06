"""P8-U10, per company since plan 2026-10-04-004 U1: `HelixHR Celebration
Reminder`'s own controller and the patches that populate its rows.
`test_reminders.py` covers the sender that reads these rows; this file
covers the doctype and the patches in isolation.

The old model keyed rows by `event` alone, so its tests had to delete the
real "birthday" row to free a unique constraint. `event` is no longer
unique -- rows are `{event}-{company}` -- so a test creates its own row
under a test-owned company and cleans it up, without touching whatever
the site's real rows are.
"""

import frappe
from frappe.tests import IntegrationTestCase

from helixhr.tests.utils import suspend_chart_of_account_fixtures


class TestHelixHRCelebrationReminderValidate(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		from helixhr.tests.utils import ensure_test_company, suspend_chart_of_account_fixtures

		cls.company = ensure_test_company()

	def setUp(self):
		frappe.set_user("Administrator")
		# The site's own rows for this company are snapshot-and-restored, the
		# same shape the old tests took against the unique `event` constraint:
		# a row named `birthday-{company}` cannot coexist with a test row of
		# the same name.
		self.snapshot = {}
		for event in ("birthday", "work_anniversary", "holiday"):
			name = frappe.db.get_value(
				"HelixHR Celebration Reminder", {"event": event, "company": self.company}, "name"
			)
			if name:
				self.snapshot[event] = frappe.get_doc("HelixHR Celebration Reminder", name).as_dict()
				frappe.delete_doc(
					"HelixHR Celebration Reminder", name, force=True, ignore_permissions=True
				)

	def tearDown(self):
		frappe.set_user("Administrator")
		for name in frappe.get_all(
			"HelixHR Celebration Reminder", filters={"company": self.company}, pluck="name"
		):
			frappe.delete_doc("HelixHR Celebration Reminder", name, force=True, ignore_permissions=True)
		for snapshot in self.snapshot.values():
			frappe.get_doc(snapshot).insert(ignore_permissions=True)

	def _reminder(self, event="birthday", **fields):
		doc = frappe.get_doc(
			{
				"doctype": "HelixHR Celebration Reminder",
				"event": event,
				"company": self.company,
				**fields,
			}
		)
		return doc

	def test_selected_employees_with_no_recipients_is_refused(self):
		doc = self._reminder(recipient_mode="Selected employees")
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_selected_employees_with_recipients_is_accepted(self):
		from helixhr.tests.utils import make_test_employee_and_manager

		employee, *_ = make_test_employee_and_manager()
		doc = self._reminder(recipient_mode="Selected employees", recipients=[{"employee": employee}])
		doc.insert(ignore_permissions=True)

		self.assertEqual(len(doc.recipients), 1)
		self.assertEqual(doc.name, f"birthday-{self.company}")

	def test_a_recipient_from_another_company_is_refused(self):
		"""Plan 2026-10-04-004 U1: a selected person is mailed for *this*
		company's celebrations, so a recipient from another company here
		would put that company's employee on this company's list."""
		from helixhr.tests.utils import make_celebration_employee

		other_company = "_Test Celebrations Other Co"
		if not frappe.db.exists("Company", other_company):
			with suspend_chart_of_account_fixtures():
				frappe.get_doc(
					{
						"doctype": "Company",
						"company_name": other_company,
						"abbr": "TCOC",
						"default_currency": "USD",
						"country": "United States",
					}
				).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc,
			"Company",
			other_company,
			force=True,
			ignore_permissions=True,
		)
		foreign = make_celebration_employee(
			"REM-FOREIGN", other_company, date_of_birth="1990-01-01", date_of_joining="2020-01-01"
		)

		doc = self._reminder(recipient_mode="Selected employees", recipients=[{"employee": foreign}])
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_all_employees_with_an_empty_recipients_table_is_accepted(self):
		doc = self._reminder(recipient_mode="All employees")
		doc.insert(ignore_permissions=True)

	def test_switching_to_all_employees_does_not_clear_a_saved_selection(self):
		"""Rows left in `recipients` while `recipient_mode` is "All
		employees" are ignored, not refused -- HR may be switching modes
		back and forth and should not lose the list they picked."""
		from helixhr.tests.utils import make_test_employee_and_manager

		employee, *_ = make_test_employee_and_manager()
		doc = self._reminder(recipient_mode="Selected employees", recipients=[{"employee": employee}])
		doc.insert(ignore_permissions=True)

		doc.recipient_mode = "All employees"
		doc.save(ignore_permissions=True)

		self.assertEqual(len(doc.recipients), 1)

	def test_event_cannot_be_changed_after_insert(self):
		self._reminder(recipient_mode="All employees").insert(ignore_permissions=True)
		self.assertTrue(
			frappe.get_meta("HelixHR Celebration Reminder").get_field("event").read_only_depends_on
		)

	def test_a_holiday_row_without_a_frequency_is_refused(self):
		doc = self._reminder(event="holiday")
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_a_holiday_row_with_a_frequency_is_accepted(self):
		doc = self._reminder(event="holiday", frequency="Weekly")
		doc.insert(ignore_permissions=True)
		self.assertEqual(doc.name, f"holiday-{self.company}")

	def test_a_non_holiday_row_ignores_frequency(self):
		doc = self._reminder(frequency="Weekly")
		doc.insert(ignore_permissions=True)

	def test_two_companies_can_hold_one_event_each(self):
		"""The old model's `unique` on `event` is gone: one event, one row
		per company."""
		other_company = "_Test Reminders Co A"
		if not frappe.db.exists("Company", other_company):
			with suspend_chart_of_account_fixtures():
				frappe.get_doc(
					{
						"doctype": "Company",
						"company_name": other_company,
						"abbr": "TRCA",
						"default_currency": "USD",
						"country": "United States",
					}
				).insert(ignore_permissions=True)
			self.addCleanup(
				frappe.delete_doc, "Company", other_company, force=True, ignore_permissions=True
			)
		self._reminder(recipient_mode="All employees").insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc,
			"HelixHR Celebration Reminder",
			f"birthday-{other_company}",
			force=True,
			ignore_permissions=True,
			ignore_missing=True,
		)
		other = frappe.get_doc(
			{
				"doctype": "HelixHR Celebration Reminder",
				"event": "birthday",
				"company": other_company,
				"recipient_mode": "All employees",
			}
		)
		other.insert(ignore_permissions=True)

		for company in (self.company, other_company):
			self.assertTrue(frappe.db.exists("HelixHR Celebration Reminder", f"birthday-{company}"))


class TestSplitCelebrationRemindersByCompany(IntegrationTestCase):
	"""Plan 2026-10-04-004 U1: the patch copies P8's global one-row-per-event
	rows into one row per (event, company) and deletes the global row.

	The patch moves real site state, so every test records which rows
	existed before it ran and restores exactly that after it: the per-company
	copies the test created are deleted, and any pre-existing row the patch
	deleted is recreated as it was."""

	@classmethod
	def setUpClass(cls):
		from helixhr.tests.utils import ensure_test_company, suspend_chart_of_account_fixtures

		cls.company = ensure_test_company()

	def setUp(self):
		frappe.set_user("Administrator")
		from helixhr.tests.test_reminders import _template

		_template("_Test HelixHR Birthday", "BDAYMARK {{ names }}")
		self.existing = {
			name: frappe.get_doc("HelixHR Celebration Reminder", name).as_dict()
			for name in frappe.get_all("HelixHR Celebration Reminder", pluck="name")
		}
		# `_global_row` produces per-company copies named `birthday-{company}`
		# for every company on the site, and a real (post-split) site already
		# owns some: remove them all for the duration, restore after.
		for name in [n for n in self.existing if n.startswith("birthday-")]:
			frappe.delete_doc(
				"HelixHR Celebration Reminder", name, force=True, ignore_permissions=True
			)

	def tearDown(self):
		frappe.set_user("Administrator")
		# Restore the site exactly as it was: everything the test (or the
		# patch, which it drives) wrote is removed, everything that existed
		# is recreated as it was.
		for name in frappe.get_all("HelixHR Celebration Reminder", pluck="name"):
			frappe.db.delete("HelixHR Celebration Recipient", {"parent": name})
			frappe.delete_doc("HelixHR Celebration Reminder", name, force=True, ignore_permissions=True)
		for snapshot in self.existing.values():
			frappe.get_doc(snapshot).insert(ignore_permissions=True)

	def _global_row(self, event="birthday", recipients=(), **fields):
		"""A global row, the way the old schema's rows looked: named exactly
		`{event}` by `autoname: field:event`, with no company. Built with a
		company so the insert passes validation, then set to the old model's
		name and company through raw SQL (the doctype does not allow
		rename, and the per-company row names are the site's own). Selected
		recipients ride along the same way -- the old model had no company
		to check them against, which is exactly the state R14's narrowing
		starts from."""
		fields.pop("recipient_mode", None)
		name = frappe.get_doc(
			{
				"doctype": "HelixHR Celebration Reminder",
				"event": event,
				"company": self.company,
				"recipient_mode": "All employees",
				**fields,
			}
		).insert(ignore_permissions=True).name
		global_name = event
		if recipients:
			mode = "Selected employees"
		else:
			mode = fields.get("recipient_mode", "All employees")
		frappe.db.sql(
			"update `tabHelixHR Celebration Reminder` set name = %s, company = NULL, "
			"recipient_mode = %s where name = %s",
			(global_name, mode, name),
		)
		for idx, employee in enumerate(recipients):
			frappe.db.sql(
				"insert into `tabHelixHR Celebration Recipient` "
				"(name, parent, parenttype, parentfield, employee, idx) "
				"values (%s, %s, 'HelixHR Celebration Reminder', 'recipients', %s, %s)",
				(f"GR-{event}-{idx}-{employee}", global_name, employee, idx),
			)
		return global_name

	def test_a_global_row_is_copied_to_every_company_and_deleted(self):
		from helixhr.patches.v1_0.split_celebration_reminders_by_company import execute

		companies = frappe.get_all("Company", pluck="name")
		row = self._global_row(
			is_enabled=1, email_template="_Test HelixHR Birthday", recipient_mode="All employees"
		)

		execute()

		for company in companies:
			name = f"birthday-{company}"
			self.assertTrue(frappe.db.exists("HelixHR Celebration Reminder", name))
			copied = frappe.db.get_value(
				"HelixHR Celebration Reminder",
				name,
				["is_enabled", "email_template"],
				as_dict=True,
			)
			self.assertEqual(copied.is_enabled, 1)
			self.assertEqual(copied.email_template, "_Test HelixHR Birthday")
		self.assertFalse(frappe.db.exists("HelixHR Celebration Reminder", row))

	def test_selected_recipients_are_narrowed_to_their_own_company(self):
		from helixhr.patches.v1_0.split_celebration_reminders_by_company import execute
		from helixhr.tests.utils import make_celebration_employee

		other_company = "_Test Reminders Co B"
		if not frappe.db.exists("Company", other_company):
			with suspend_chart_of_account_fixtures():
				frappe.get_doc(
					{
						"doctype": "Company",
						"company_name": other_company,
						"abbr": "TRCB",
						"default_currency": "USD",
						"country": "United States",
					}
				).insert(ignore_permissions=True)
			self.addCleanup(
				frappe.delete_doc, "Company", other_company, force=True, ignore_permissions=True
			)
		mine = make_celebration_employee(
			"REM-SPL-A", self.company, date_of_birth="1990-01-01", date_of_joining="2020-01-01"
		)
		theirs = make_celebration_employee(
			"REM-SPL-B", other_company, date_of_birth="1990-01-01", date_of_joining="2020-01-01"
		)
		self._global_row(
			is_enabled=1,
			email_template="_Test HelixHR Birthday",
			recipient_mode="Selected employees",
			recipients=(mine, theirs),
		)

		execute()

		mine_row = frappe.db.get_value(
			"HelixHR Celebration Reminder", f"birthday-{self.company}", "name"
		)
		theirs_row = frappe.db.get_value(
			"HelixHR Celebration Reminder", f"birthday-{other_company}", "name"
		)
		for parent, expected in ((mine_row, [mine]), (theirs_row, [theirs])):
			self.assertEqual(
				frappe.get_all("HelixHR Celebration Recipient", filters={"parent": parent}, pluck="employee"),
				expected,
			)

	def test_running_twice_makes_no_duplicates_and_no_error(self):
		from helixhr.patches.v1_0.split_celebration_reminders_by_company import execute

		self._global_row(is_enabled=0, email_template=None)

		execute()
		execute()

		self.assertEqual(
			frappe.db.count("HelixHR Celebration Reminder", filters={"event": "birthday"}),
			frappe.db.count("Company"),
		)


class TestMigrateCelebrationReminders(IntegrationTestCase):
	"""Plan 2026-10-04-004 U1: the rewritten patch carries the two HR
	Settings Custom Fields into per-company rows, and only on a site
	coming from before P8 -- any reminder row already present means a
	previous migration ran, and it must not resurrect an HR edit or
	deletion from the stale Custom Fields."""

	@classmethod
	def setUpClass(cls):
		from helixhr.tests.utils import ensure_test_company, suspend_chart_of_account_fixtures

		cls.company = ensure_test_company()

	def setUp(self):
		frappe.set_user("Administrator")
		# The patch moves real site state: record which rows existed and
		# restore exactly that after each test.
		self.existing = {
			name: frappe.get_doc("HelixHR Celebration Reminder", name).as_dict()
			for name in frappe.get_all("HelixHR Celebration Reminder", pluck="name")
		}

	def tearDown(self):
		frappe.set_user("Administrator")
		for name in frappe.get_all("HelixHR Celebration Reminder", pluck="name"):
			if name not in self.existing:
				frappe.delete_doc(
					"HelixHR Celebration Reminder", name, force=True, ignore_permissions=True
				)
		for name, snapshot in self.existing.items():
			if not frappe.db.exists("HelixHR Celebration Reminder", name):
				frappe.get_doc(snapshot).insert(ignore_permissions=True)

	def test_no_rows_means_nothing_is_created_without_the_old_fields(self):
		from helixhr.patches.v1_0.migrate_celebration_reminders import execute

		# This site has the old Custom Fields (they ship as fixtures and are
		# left in place after carrying), so a site with rows is guarded by
		# `test_any_existing_row_stops_the_patch`; here both fields are
		# blanked, so a rowless site creates nothing at all.
		original = {
			field: frappe.db.get_single_value("HR Settings", field)
			for field in ("helixhr_birthday_template", "helixhr_anniversary_template")
		}
		for field in original:
			frappe.db.set_single_value("HR Settings", field, None)

		before = frappe.db.count("HelixHR Celebration Reminder")
		execute()
		self.assertEqual(frappe.db.count("HelixHR Celebration Reminder"), before)

		for field, value in original.items():
			frappe.db.set_single_value("HR Settings", field, value)

	def test_any_existing_row_stops_the_patch(self):
		"""Once rows exist, an HR edit or deletion is never resurrected from
		the stale Custom Fields -- a second run copies nothing."""
		from helixhr.patches.v1_0.migrate_celebration_reminders import execute

		frappe.db.set_single_value("HR Settings", "helixhr_birthday_template", "_Stale Template")

		try:
			execute()  # the first run, from the Custom Fields
			row = frappe.db.get_value(
				"HelixHR Celebration Reminder", {"event": "birthday", "company": self.company}, "name"
			)
			frappe.db.set_value("HelixHR Celebration Reminder", row, "email_template", None)

			execute()  # the re-run: must not resurrect the stale template

			self.assertIsNone(
				frappe.db.get_value("HelixHR Celebration Reminder", row, "email_template"),
				"the stale Custom Field must not reach an existing row",
			)
		finally:
			frappe.db.set_single_value("HR Settings", "helixhr_birthday_template", None)


class TestSaveCelebrationReminder(IntegrationTestCase):
	"""P8-U12 / P8-R5, P8-R6."""

	@classmethod
	def setUpClass(cls):
		from helixhr.tests.utils import (
			ensure_test_company,
			make_test_employee_and_manager,
			make_test_hr_manager_employee,
		)

		ensure_test_company()
		make_test_hr_manager_employee()
		cls.employee, *_ = make_test_employee_and_manager()

	def setUp(self):
		frappe.set_user("Administrator")
		from helixhr.tests.utils import EMPLOYEE_USER, make_test_portal_admin

		self.company = frappe.db.get_value("Employee", self.employee, "company")
		# The Email templates owner: a Portal Admin anchored to this company,
		# so a save without `company` resolves to it.
		_, self.hr_user = make_test_portal_admin(self.company)
		self.employee_user = EMPLOYEE_USER

		self.snapshot = None
		row = frappe.db.get_value(
			"HelixHR Celebration Reminder", {"event": "birthday", "company": self.company}, "name"
		)
		if row:
			self.snapshot = frappe.get_doc("HelixHR Celebration Reminder", row).as_dict()
			frappe.delete_doc("HelixHR Celebration Reminder", row, force=True, ignore_permissions=True)
		self.template_snapshot = None
		if frappe.db.exists("Email Template", "HelixHR Birthday Reminder"):
			doc = frappe.get_doc("Email Template", "HelixHR Birthday Reminder")
			self.template_snapshot = {
				field: doc.get(field) for field in ("subject", "response_html", "response", "use_html")
			}

	def tearDown(self):
		frappe.set_user("Administrator")
		row = frappe.db.get_value(
			"HelixHR Celebration Reminder", {"event": "birthday", "company": self.company}, "name"
		)
		if row:
			frappe.delete_doc("HelixHR Celebration Reminder", row, force=True, ignore_permissions=True)
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

	def test_first_save_creates_the_companys_own_template_not_a_second_one(self):
		from helixhr.api import save_celebration_reminder

		frappe.set_user(self.hr_user)
		result = save_celebration_reminder(
			event="birthday", subject="Happy birthday {{ names }}", body="Cheers", is_enabled=1
		)
		self.assertEqual(result["subject"], "Happy birthday {{ names }}")
		# U11 (plan 2026-10-05-001): the per-company name, never the shared one.
		from helixhr.reminders import celebration_template_name

		template = celebration_template_name("birthday", self.company)
		self.assertTrue(frappe.db.exists("Email Template", template))

		# A second save edits the same template, not a new one.
		save_celebration_reminder(
			event="birthday", subject="Edited subject", body="Cheers", is_enabled=1
		)
		self.assertEqual(
			frappe.db.count(
				"HelixHR Celebration Reminder", filters={"event": "birthday", "company": self.company}
			),
			1,
		)
		self.assertEqual(
			frappe.db.get_value("Email Template", template, "subject"),
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
		self.assertFalse(
			frappe.db.get_value(
				"HelixHR Celebration Reminder",
				{"event": "birthday", "company": self.company},
				"is_enabled",
			)
		)

	def test_an_unknown_event_is_refused(self):
		from helixhr.api import save_celebration_reminder

		frappe.set_user(self.hr_user)
		with self.assertRaises(frappe.ValidationError):
			save_celebration_reminder(event="not_a_real_event", subject="x", body="y", is_enabled=1)

	def test_the_write_is_rate_limited(self):
		from helixhr.utils import RATE_LIMIT_POLICY

		self.assertIn("save_celebration_reminder", RATE_LIMIT_POLICY)
