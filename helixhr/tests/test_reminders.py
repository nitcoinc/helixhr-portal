"""P4-U6: the celebration reminders HR words themselves.

Every date is derived from the run's own today, because a fixture pinned to a
calendar date is only "today" one morning a year.

Two companies of this suite's own, and that is the point rather than tidiness:
recipients are *everyone active in the celebrating person's company*, so an
assertion about who got the mail is only stable if the test owns the whole
company. `_Test Company` accumulates fixture people from a dozen other
suites, and `_Test Celebrations Co` is re-dated by the Home card's own tests
-- both of which is why every assertion here filters the queue down to this
suite's own address domain rather than counting rows.

`frappe.sendmail` *commits* the Email Queue row it writes (the P4-U3
finding), so the queue is watched as a delta and the rows this suite creates
are deleted on purpose in `tearDown`.
"""

import email
from datetime import date
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, add_to_date, getdate, now_datetime, today

from helixhr import reminders
from helixhr.events import PENDING_SINCE_FIELD as PENDING_SINCE
from helixhr.reminders import EVENTS, send_celebration_reminders
from helixhr.tests.utils import (
	HR_MANAGER_USER,
	ensure_hr_manager_user,
	ensure_test_email_account,
	make_celebration_employee,
	make_test_employee_and_manager,
)

COMPANY_A = "_Test Reminders Co A"
COMPANY_B = "_Test Reminders Co B"
# Every fixture person's company_email lives here, so a queue row can be told
# apart from one another suite's celebrating employee produced in the same
# run.
DOMAIN = "reminders.test"

# The people this suite owns, and the company each belongs to. Every test
# re-dates all of them, so nothing leaks from the test before it -- the rows
# are committed with the mail and outlive the transaction.
POOL = {"A1": COMPANY_A, "A2": COMPANY_A, "A3": COMPANY_A, "B1": COMPANY_B, "B2": COMPANY_B}

BIRTHDAY_TEMPLATE = "_Test HelixHR Birthday"
ANNIVERSARY_TEMPLATE = "_Test HelixHR Anniversary"
# One template that raises while rendering, for one company only -- the
# failure-isolation tests below.
BOOM_TEMPLATE = "_Test HelixHR Boom"
# The title `reminders` logs every swallowed failure under. Error Log keeps
# the title in `method`.
ERROR_LOG_TITLE = "HelixHR celebration reminders"

# Deliberately not the shipped default's markup: these assert the *context*
# contract of P4-KTD13, which is what the app promises, one marker per key.
TEMPLATE_HTML = (
	"COUNT:{{ count }} FIRST:{{ persons[0].first_name }} COMPANY:{{ company }} "
	"LOGO:[{{ logo_url }}] PORTAL:{{ portal_url }} DATE:{{ date }} "
	"YEARS:{% for person in persons %}{{ person.years }};{% endfor %}"
)


def _company(name, abbr):
	if not frappe.db.exists("Company", name):
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": name,
				"abbr": abbr,
				"default_currency": "USD",
				"country": "United States",
			}
		).insert(ignore_permissions=True)
	return name


def _template(name, subject):
	if frappe.db.exists("Email Template", name):
		return name
	frappe.get_doc(
		{
			"doctype": "Email Template",
			"name": name,
			"use_html": 1,
			"subject": subject,
			"response_html": TEMPLATE_HTML,
		}
	).insert(ignore_permissions=True)
	return name


def _holiday_template(name, subject, html):
	if frappe.db.exists("Email Template", name):
		frappe.db.set_value("Email Template", name, {"subject": subject, "response_html": html})
		frappe.clear_document_cache("Email Template", name)
		return name
	frappe.get_doc(
		{
			"doctype": "Email Template",
			"name": name,
			"use_html": 1,
			"subject": subject,
			"response_html": html,
		}
	).insert(ignore_permissions=True)
	return name


def _boom_template(company):
	"""A template that raises for one company and renders for every other.

	Division by zero rather than a missing variable: Jinja's default
	Undefined is happy to be printed, so an undefined name would render an
	empty string instead of failing.
	"""
	html = "OK {%% if company == %r %%}{{ 1 / 0 }}{%% endif %%}" % company
	if frappe.db.exists("Email Template", BOOM_TEMPLATE):
		frappe.db.set_value("Email Template", BOOM_TEMPLATE, "response_html", html)
		frappe.clear_document_cache("Email Template", BOOM_TEMPLATE)
		return BOOM_TEMPLATE
	frappe.get_doc(
		{
			"doctype": "Email Template",
			"name": BOOM_TEMPLATE,
			"use_html": 1,
			"subject": "BOOMMARK {{ names }}",
			"response_html": html,
		}
	).insert(ignore_permissions=True)
	return BOOM_TEMPLATE


_FIELD_TO_EVENT = {
	"helixhr_birthday_template": "birthday",
	"helixhr_anniversary_template": "work_anniversary",
}


class TestCelebrationReminders(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		ensure_test_email_account()
		_company(COMPANY_A, "TRCA")
		_company(COMPANY_B, "TRCB")
		_template(BIRTHDAY_TEMPLATE, "BDAYMARK {{ names }}")
		_template(ANNIVERSARY_TEMPLATE, "ANNIVMARK {{ names }}")

		self.today = getdate()
		# A month that is certainly not this one, for the people who must be
		# absent from every list.
		self.other_month = 1 if self.today.month != 1 else 7
		self.queued = set()
		# P8-U10/U11, per company since plan 2026-10-04-004 U1: the picker is
		# one `HelixHR Celebration Reminder` row per (event, company) --
		# captured and restored the same way, per company.
		self.original = {}
		for field, event in _FIELD_TO_EVENT.items():
			for company in (COMPANY_A, COMPANY_B):
				key = (field, company)
				self._ensure_reminder(event, company)
				self.original[key] = frappe.db.get_value(
					"HelixHR Celebration Reminder",
					{"event": event, "company": company},
					["name", "is_enabled", "email_template", "recipient_mode"],
					as_dict=True,
				)
				self._pick(field, None, company=company)

	def tearDown(self):
		frappe.set_user("Administrator")
		for (field, _company), snapshot in self.original.items():
			event = _FIELD_TO_EVENT[field]
			frappe.db.set_value(
				"HelixHR Celebration Reminder",
				snapshot.name,
				{
					"is_enabled": snapshot.is_enabled,
					"email_template": snapshot.email_template,
					"recipient_mode": snapshot.recipient_mode,
				},
				update_modified=False,
			)
			frappe.clear_document_cache("HelixHR Celebration Reminder", snapshot.name)
		# KTD8's rerun guard is a Redis key a previous test may have left
		# behind for today.
		for company in (COMPANY_A, COMPANY_B):
			for event in _FIELD_TO_EVENT.values():
				frappe.cache.delete_value(
					f"{reminders.CELEBRATION_GUARD_PREFIX}{getdate()}|{event}|{company}"
				)
		for row in self.queued:
			frappe.db.delete("Email Queue Recipient", {"parent": row})
			frappe.db.delete("Email Queue", {"name": row})

	def _clear_guard(self, event="birthday", company=COMPANY_A):
		"""Make the (event, company) pair sendable again today -- for tests
		that send twice on purpose."""
		frappe.cache.delete_value(
			f"{reminders.CELEBRATION_GUARD_PREFIX}{getdate()}|{event}|{company}"
		)

	def _ensure_reminder(self, event, company):
		"""This suite runs standalone from the patches (which normally
		create rows once, on install/migrate) -- a test run against a site
		that has not migrated since the rows shipped would otherwise fail
		every test here on a missing row."""
		if not frappe.db.get_value(
			"HelixHR Celebration Reminder", {"event": event, "company": company}, "name"
		):
			frappe.get_doc(
				{
					"doctype": "HelixHR Celebration Reminder",
					"event": event,
					"company": company,
					"recipient_mode": "All employees",
				}
			).insert(ignore_permissions=True)

	# --- fixtures ----------------------------------------------------------

	def _pick(self, field, template, company=None):
		"""Set the picker without going through
		`HelixHRCelebrationReminder.validate` -- "All employees" always
		satisfies it regardless of `template`, so the refusal that
		controller carries (an empty `recipients` list in "Selected
		employees" mode) never fires here; it is asserted on its own
		below."""
		event = _FIELD_TO_EVENT[field]
		company = company or COMPANY_A
		# `recipient_mode` is forced back to "All employees" here too --
		# every scenario in this class assumes it except the ones that
		# explicitly call `_select()` afterwards, and a real site's saved
		# selection (from HR actually using Settings > Celebrations) must
		# not silently narrow this suite's own mail-everyone assertions.
		row = frappe.db.get_value(
			"HelixHR Celebration Reminder", {"event": event, "company": company}, "name"
		)
		frappe.db.set_value(
			"HelixHR Celebration Reminder",
			row,
			{"email_template": template, "is_enabled": 1 if template else 0, "recipient_mode": "All employees"},
			update_modified=False,
		)
		frappe.clear_document_cache("HelixHR Celebration Reminder", row)

	def _stage(self, birthdays=(), anniversaries=(), years=None):
		"""Re-date every person this suite owns: a birthday or a joining
		anniversary today, or in some other month."""
		years = years or {}
		for suffix, company in POOL.items():
			since = years.get(suffix, 5)
			dob = (
				date(1990, self.today.month, self.today.day)
				if suffix in birthdays
				else date(1990, self.other_month, 4)
			)
			doj = (
				date(self.today.year - since, self.today.month, self.today.day)
				if suffix in anniversaries
				else date(self.today.year - since, self.other_month, 4)
			)
			make_celebration_employee(
				f"REM-{suffix}",
				company,
				date_of_birth=dob,
				date_of_joining=doj,
				company_email=self._address(suffix),
			)

	def _address(self, suffix):
		return f"{suffix.lower()}@{DOMAIN}"

	# --- the queue ---------------------------------------------------------

	def _watch_mail(self):
		before = set(frappe.get_all("Email Queue", pluck="name"))

		def added():
			mails = []
			for row in sorted(set(frappe.get_all("Email Queue", pluck="name")) - before):
				recipients = sorted(
					frappe.get_all("Email Queue Recipient", filters={"parent": row}, pluck="recipient")
				)
				if not any(address.endswith(DOMAIN) for address in recipients):
					# Another suite's celebrating employee, in another
					# company. Not this test's mail and not this test's to
					# clean up.
					continue
				self.queued.add(row)
				message = frappe.db.get_value("Email Queue", row, "message")
				mails.append({"recipients": recipients, **_read(message)})
			return mails

		return added

	# --- scenarios ---------------------------------------------------------

	def test_nothing_is_sent_while_no_template_is_picked(self):
		self._stage(birthdays=("A1",), anniversaries=("B1",))
		added = self._watch_mail()

		result = send_celebration_reminders()
		self.assertEqual(
			result,
			{
				"birthday": {"companies": 0, "emails": 0, "failed": 0},
				"work_anniversary": {"companies": 0, "emails": 0, "failed": 0},
				"holiday": {"companies": 0, "emails": 0, "failed": 0},
			},
		)
		self.assertEqual(added(), [])

	def test_a_company_with_no_row_for_the_event_sends_nothing(self):
		"""R1: settings are per company, and absent (or disabled) means that
		company gets nothing -- celebrants in B are not celebrated by A's
		enabled row, and B's employees are not mailed about them."""
		self._stage(birthdays=("A1", "B1"))
		self._pick("helixhr_birthday_template", BIRTHDAY_TEMPLATE, company=COMPANY_A)
		added = self._watch_mail()

		result = send_celebration_reminders()
		mails = added()

		self.assertEqual(result["birthday"]["companies"], 1)
		self.assertEqual(
			sorted(tuple(mail["recipients"]) for mail in mails),
			[(self._address("A2"), self._address("A3"))],
			"only the enabled company is mailed, about its own celebrant",
		)

	def test_an_employee_with_no_company_is_never_celebrated_or_mailed(self):
		"""R4: HRMS groups celebrants by company, and a None group is nobody's
		settings row and nobody's pool. Skipped people are counted in the log."""
		from unittest.mock import patch

		self._stage(birthdays=("A1",))
		self._pick("helixhr_birthday_template", BIRTHDAY_TEMPLATE)
		ghost = frappe._dict(
			name="No Company Employee",
			image=None,
			user_id=None,
			company_email="ghost@" + DOMAIN,
			personal_email=None,
		)

		real = reminders.get_employees_having_an_event_today
		with patch.object(
			reminders,
			"get_employees_having_an_event_today",
			side_effect=lambda event: {None: [ghost], COMPANY_A: real(event).get(COMPANY_A, [])},
		):
			added = self._watch_mail()
			result = send_celebration_reminders()

		self.assertEqual(
			result["birthday"],
			{"companies": 1, "emails": 1, "failed": 0},
			"A1, who has a company, is still celebrated normally",
		)
		# A1's mail went out; the ghost is in no pool and hears about nobody.
		recipients = [r for mail in added() for r in mail["recipients"]]
		self.assertNotIn("ghost@" + DOMAIN, recipients)
		logs = frappe.get_all(
			"Error Log", filters={"method": reminders.NO_COMPANY_LOG_TITLE}, pluck="name"
		)
		self.assertTrue(logs, "the skip is counted in the log, not silent")
		for log in logs:
			self.addCleanup(frappe.delete_doc, "Error Log", log, force=True, ignore_permissions=True)

	def test_the_celebrant_with_no_user_never_receives_their_own_announcement(self):
		"""KTD5: HRMS resolves a celebrant's address in two different orders
		(`user -> personal -> company` for the exclusion, `user -> company ->
		personal` for the shared-day mail). The pool falls to `company_email`
		when there is no User, so excluding only the first order's pick mailed
		a celebrant their own announcement. Both orders are subtracted now."""
		from unittest.mock import patch

		from helixhr.tests.utils import CELEBRATION_TAG, make_celebration_employee

		self._pick("helixhr_birthday_template", BIRTHDAY_TEMPLATE)
		# The celebrant: no User, both mail fields set. HRMS's own exclusion
		# picks `personal_email` first; the pool picks `company_email` when
		# there is no user_id -- both would have been in the pool.
		make_celebration_employee(
			"REM-NOUSER",
			COMPANY_A,
			date_of_birth=date(1990, self.today.month, self.today.day),
			date_of_joining=date(2020, self.other_month, 4),
		)
		nouser = frappe.db.get_value(
			"Employee", {"employee_number": f"{CELEBRATION_TAG}-REM-NOUSER"}, "name"
		)
		frappe.db.set_value(
			"Employee",
			nouser,
			{
				"personal_email": f"nouser-personal@{DOMAIN}",
				"company_email": f"nouser-company@{DOMAIN}",
			},
		)
		nouser_row = frappe.db.get_value(
			"Employee", nouser, ["name", "company_email", "personal_email", "user_id"], as_dict=True
		)

		with patch.object(
			reminders,
			"get_employees_having_an_event_today",
			return_value={COMPANY_A: [dict(nouser_row)]},
		):
			added = self._watch_mail()
			send_celebration_reminders()
			mails = added()

		recipients = [r for mail in mails for r in mail["recipients"]]
		self.assertNotIn(
			nouser_row.company_email, recipients, "their own announcement, via company_email"
		)
		self.assertNotIn(
			nouser_row.personal_email, recipients, "their own announcement, via personal_email"
		)
		# The fixture must not leak an extra COMPANY_A address into later
		# suites' pools.
		self.addCleanup(frappe.db.set_value, "Employee", nouser, "status", "Left")

	def test_a_duplicate_mailbox_in_another_company_is_dropped_and_logged(self):
		"""R3 / KTD4, the reported bug: X1 is the real active employee in A;
		X2 is a stale active duplicate in B with the same address. B's own
		pool helper happily includes the address; the guard drops it and
		logs the drop naming both Employee records."""
		from helixhr.tests.utils import make_celebration_employee

		shared = "shared-mailbox@" + DOMAIN
		real_one = make_celebration_employee(
			"REM-DUP-A",
			COMPANY_A,
			date_of_birth="1990-01-01",
			date_of_joining="2020-01-01",
			company_email=shared,
		)
		stale = make_celebration_employee(
			"REM-DUP-B",
			COMPANY_B,
			date_of_birth="1990-01-01",
			date_of_joining="2020-01-01",
			company_email=shared,
		)
		self._stage(birthdays=("A1",))
		self._pick("helixhr_birthday_template", BIRTHDAY_TEMPLATE)
		added = self._watch_mail()

		send_celebration_reminders()
		mails = added()

		recipients = [r for mail in mails for r in mail["recipients"]]
		self.assertIn(self._address("A2"), recipients)
		self.assertNotIn(shared, recipients, "the stale duplicate's address is dropped from the pool")
		logs = frappe.get_all(
			"Error Log", filters={"method": reminders.CROSS_COMPANY_LOG_TITLE}, pluck="name"
		)
		self.assertTrue(logs, "the drop is logged once per run, naming both records")
		for log in logs:
			detail = frappe.db.get_value("Error Log", log, "error")
			self.assertIn(real_one, detail)
			self.assertIn(stale, detail)
			self.addCleanup(frappe.delete_doc, "Error Log", log, force=True, ignore_permissions=True)
		# These two tagged fixtures must not leak a duplicate mailbox into
		# later suites' pools: both are set to Left, which takes them out of
		# every pool and every celebration.
		self.addCleanup(frappe.db.set_value, "Employee", stale, "status", "Left")
		self.addCleanup(frappe.db.set_value, "Employee", real_one, "status", "Left")

	def test_the_celebration_guard_stops_a_second_run_the_same_day(self):
		"""KTD8: a hand-run `bench execute` after the scheduler has already
		been round mails no company twice. Per company, per event, per day."""
		self._stage(birthdays=("A1",))
		self._pick("helixhr_birthday_template", BIRTHDAY_TEMPLATE)
		added = self._watch_mail()

		send_celebration_reminders()
		send_celebration_reminders()

		self.assertEqual(len(added()), 1)

	def test_a_day_after_the_guard_the_mail_sends_again(self):
		self._stage(birthdays=("A1",))
		self._pick("helixhr_birthday_template", BIRTHDAY_TEMPLATE)
		added = self._watch_mail()

		send_celebration_reminders()
		self._clear_guard()
		send_celebration_reminders()

		self.assertEqual(len(added()), 2)

	def test_a_company_that_received_nothing_is_not_marked(self):
		"""The guard marks a company only after mail actually went out, so a
		broken template fixed and hand-run the same morning still delivers."""
		self._stage(birthdays=("A1",))
		self._pick("helixhr_birthday_template", "_Test Template That Went Away")
		send_celebration_reminders()

		self._pick("helixhr_birthday_template", BIRTHDAY_TEMPLATE)
		added = self._watch_mail()
		send_celebration_reminders()

		self.assertEqual(len(added()), 1, "the failed morning must not consume the company's send")

	def test_the_celebration_guard_is_registered_as_persistent(self):
		self.assertIn(
			f"{reminders.CELEBRATION_GUARD_PREFIX}*", frappe.get_hooks("persistent_cache_keys")
		)

	def test_one_birthday_mails_everyone_else_in_that_company(self):
		self._stage(birthdays=("A1",))
		self._pick("helixhr_birthday_template", BIRTHDAY_TEMPLATE)
		added = self._watch_mail()

		send_celebration_reminders()
		mails = added()

		self.assertEqual(len(mails), 1)
		self.assertEqual(mails[0]["recipients"], [self._address("A2"), self._address("A3")])
		self.assertNotIn(self._address("A1"), mails[0]["recipients"])
		self.assertIn("BDAYMARK", mails[0]["subject"])
		self.assertIn("COUNT:1", mails[0]["body"])
		self.assertIn("COMPANY:" + COMPANY_A, mails[0]["body"])
		self.assertIn("PORTAL:http", mails[0]["body"])

	def test_a_shared_birthday_adds_one_mail_per_person_about_the_others(self):
		self._stage(birthdays=("A1", "A2"))
		self._pick("helixhr_birthday_template", BIRTHDAY_TEMPLATE)
		added = self._watch_mail()

		send_celebration_reminders()
		mails = {tuple(mail["recipients"]): mail for mail in added()}

		self.assertEqual(len(mails), 3, "one to the company, one to each of the two celebrating")
		company_mail = mails[(self._address("A3"),)]
		self.assertIn("COUNT:2", company_mail["body"])
		# Each of the two hears only about the other.
		self.assertIn("COUNT:1", mails[(self._address("A1"),)]["body"])
		self.assertIn("A2", mails[(self._address("A1"),)]["subject"])
		self.assertIn("A1", mails[(self._address("A2"),)]["subject"])

	def test_an_anniversary_carries_the_years_completed(self):
		self._stage(anniversaries=("A1",), years={"A1": 5})
		self._pick("helixhr_anniversary_template", ANNIVERSARY_TEMPLATE)
		added = self._watch_mail()

		send_celebration_reminders()
		mails = added()

		self.assertEqual(len(mails), 1)
		self.assertIn("ANNIVMARK", mails[0]["subject"])
		self.assertIn("YEARS:5;", mails[0]["body"])

	def test_somebody_who_joined_this_year_has_no_anniversary(self):
		"""HRMS's own rule: the event year must be before this one. Imported,
		never re-implemented (P4-KTD12), so this is a guard on the import."""
		self._stage(anniversaries=("A1",), years={"A1": 0})
		self._pick("helixhr_anniversary_template", ANNIVERSARY_TEMPLATE)
		added = self._watch_mail()

		send_celebration_reminders()

		self.assertEqual(added(), [])

	def test_recipients_never_cross_companies(self):
		self._stage(birthdays=("A1", "B1"))
		self._pick("helixhr_birthday_template", BIRTHDAY_TEMPLATE)
		# Both companies' own settings are on here -- the point of this test
		# is that B's mail names B, not that B is silent (that is R1's test).
		self._pick("helixhr_birthday_template", BIRTHDAY_TEMPLATE, company=COMPANY_B)
		added = self._watch_mail()

		send_celebration_reminders()
		mails = {tuple(mail["recipients"]): mail for mail in added()}

		self.assertEqual(
			sorted(mails),
			[(self._address("A2"), self._address("A3")), (self._address("B2"),)],
		)
		self.assertIn("COMPANY:" + COMPANY_B, mails[(self._address("B2"),)]["body"])

	def test_the_logo_is_absolute_and_empty_for_a_company_without_one(self):
		self._stage(birthdays=("A1",))
		self._pick("helixhr_birthday_template", BIRTHDAY_TEMPLATE)

		added = self._watch_mail()
		send_celebration_reminders()
		self.assertIn("LOGO:[]", added()[0]["body"], "no logo renders as empty, not as an error")

		frappe.db.set_value("Company", COMPANY_A, "company_logo", "/files/_test_reminders_logo.png")
		frappe.clear_document_cache("Company", COMPANY_A)
		self.addCleanup(frappe.db.set_value, "Company", COMPANY_A, "company_logo", None)

		# The same-day rerun guard would keep the second look silent.
		self._clear_guard()
		added = self._watch_mail()
		send_celebration_reminders()
		self.assertIn("LOGO:[http", added()[0]["body"])

	def test_a_picked_template_that_does_not_exist_sends_nothing(self):
		self._stage(birthdays=("A1",))
		self._pick("helixhr_birthday_template", "_Test Template That Went Away")
		added = self._watch_mail()

		self.assertEqual(send_celebration_reminders()["birthday"]["emails"], 0)
		self.assertEqual(added(), [])

	# --- failure isolation and the once-a-day assumption -------------------

	def _errors(self):
		"""A delta of the job's own Error Log rows. `frappe.log_error` writes
		one per swallowed failure, which is the whole point -- a failure has
		to surface in the scheduler log rather than vanish."""
		before = set(
			frappe.get_all("Error Log", filters={"method": ERROR_LOG_TITLE}, pluck="name")
		)

		def added():
			return sorted(
				set(frappe.get_all("Error Log", filters={"method": ERROR_LOG_TITLE}, pluck="name"))
				- before
			)

		return added

	def test_one_companys_render_failure_leaves_the_other_company_its_mail(self):
		"""HR writes this template (P4-KTD13), so a template that raises is
		one HR edit away. It must cost that company its mail and nothing
		else."""
		_boom_template(COMPANY_A)
		self._stage(birthdays=("A1", "B1"))
		self._pick("helixhr_birthday_template", BOOM_TEMPLATE)
		# Both companies' settings on, so the failing one is the only
		# difference between them.
		self._pick("helixhr_birthday_template", BOOM_TEMPLATE, company=COMPANY_B)
		added = self._watch_mail()
		errors = self._errors()

		result = send_celebration_reminders()
		mails = added()

		self.assertEqual(result["birthday"]["companies"], 2)
		self.assertEqual(result["birthday"]["failed"], 1)
		self.assertEqual(result["birthday"]["emails"], 1)
		self.assertEqual([mail["recipients"] for mail in mails], [[self._address("B2")]])
		self.assertEqual(len(errors()), 1, "the failure is in the scheduler log, not swallowed")

	def test_an_event_that_fails_outright_still_leaves_the_other_event_its_mail(self):
		"""The per-company guard cannot cover a failure before any company is
		reached -- resolving the template, the sender or HRMS's grouping -- and
		that failure used to take the day's second event with it."""
		from unittest.mock import patch

		self._stage(birthdays=("A1",), anniversaries=("B1",))
		self._pick("helixhr_birthday_template", BIRTHDAY_TEMPLATE)
		self._pick("helixhr_anniversary_template", ANNIVERSARY_TEMPLATE, company=COMPANY_B)
		added = self._watch_mail()
		errors = self._errors()

		real = reminders._send_event

		def boom(event, *args):
			if event == "birthday":
				raise RuntimeError("HRMS grouping blew up")
			return real(event, *args)

		with patch("helixhr.reminders._send_event", side_effect=boom):
			result = send_celebration_reminders()

		mails = added()
		self.assertEqual(result["birthday"], {"companies": 0, "emails": 0, "failed": 1})
		self.assertEqual(result["work_anniversary"]["emails"], 1)
		self.assertEqual([mail["recipients"] for mail in mails], [[self._address("B2")]])
		self.assertEqual(len(errors()), 1)

	def test_the_job_is_registered_as_a_daily_scheduler_event(self):
		self.assertIn(
			"helixhr.reminders.send_celebration_reminders",
			frappe.get_hooks("scheduler_events")["daily"],
		)

	# --- P8-U11: "Selected employees" recipient mode ------------------------

	def _employee_id(self, suffix):
		from helixhr.tests.utils import CELEBRATION_TAG

		return frappe.db.get_value("Employee", {"employee_number": f"{CELEBRATION_TAG}-REM-{suffix}"}, "name")

	def _select(self, event, *suffixes, company=COMPANY_A):
		row = frappe.db.get_value(
			"HelixHR Celebration Reminder", {"event": event, "company": company}, "name"
		)
		frappe.db.set_value(
			"HelixHR Celebration Reminder", row, "recipient_mode", "Selected employees", update_modified=False
		)
		frappe.db.delete("HelixHR Celebration Recipient", {"parent": row})
		doc = frappe.get_doc("HelixHR Celebration Reminder", row)
		doc.set("recipients", [{"employee": self._employee_id(suffix)} for suffix in suffixes])
		doc.save(ignore_permissions=True)
		frappe.clear_document_cache("HelixHR Celebration Reminder", row)

	def test_selected_employees_mode_only_mails_the_selected_list(self):
		self._stage(birthdays=("A1",))
		self._pick("helixhr_birthday_template", BIRTHDAY_TEMPLATE)
		self._select("birthday", "A2")
		added = self._watch_mail()

		send_celebration_reminders()
		mails = added()

		self.assertEqual(len(mails), 1)
		self.assertEqual(mails[0]["recipients"], [self._address("A2")])
		self.assertNotIn(self._address("A3"), mails[0]["recipients"])

	def test_selected_employees_excludes_a_selected_person_in_another_company(self):
		"""P8-U11's narrowing is now enforced where the list is picked
		(plan 2026-10-04-004 U1): the controller refuses a selected person
		from another company, so the send-time narrowing is a second line,
		not the only one."""
		self._pick("helixhr_birthday_template", BIRTHDAY_TEMPLATE)
		# B2 is in COMPANY_B, not the celebrating COMPANY_A -- refused at
		# save instead of silently narrowed at send.
		with self.assertRaises(frappe.ValidationError):
			self._select("birthday", "A2", "B2")

	def test_selected_employees_where_a_selected_person_is_celebrating_is_excluded_but_still_hears_about_others(self):
		self._stage(birthdays=("A1", "A2"))
		self._pick("helixhr_birthday_template", BIRTHDAY_TEMPLATE)
		# A1 is celebrating and also on the selected list -- excluded from
		# the announcement (they are the news, not the audience), but still
		# gets the shared-day mail about A2, unconditional on selection.
		self._select("birthday", "A1", "A3")
		added = self._watch_mail()

		send_celebration_reminders()
		mails = {tuple(mail["recipients"]): mail for mail in added()}

		self.assertIn((self._address("A3"),), mails, "the announcement to the rest of the selected list")
		self.assertNotIn(
			self._address("A1"),
			mails[(self._address("A3"),)]["recipients"],
			"A1 must not be in the announcement about their own birthday",
		)
		# The shared-day mail: A1 still hears about A2, unconditional on
		# whether A1 is themselves selected.
		self.assertIn((self._address("A1"),), mails, "A1 still gets the shared-day mail about A2")
		self.assertIn("A2", mails[(self._address("A1"),)]["subject"])

	def test_selected_employees_where_everyone_selected_is_celebrating_sends_no_announcement(self):
		self._stage(birthdays=("A1",))
		self._pick("helixhr_birthday_template", BIRTHDAY_TEMPLATE)
		self._select("birthday", "A1")
		added = self._watch_mail()

		result = send_celebration_reminders()

		self.assertEqual(result["birthday"]["emails"], 0)
		self.assertEqual(added(), [])

	def test_switching_back_to_all_employees_does_not_lose_the_saved_selection(self):
		self._select("birthday", "A2")
		row = frappe.db.get_value(
			"HelixHR Celebration Reminder", {"event": "birthday", "company": COMPANY_A}, "name"
		)
		frappe.db.set_value(
			"HelixHR Celebration Reminder", row, "recipient_mode", "All employees", update_modified=False
		)
		frappe.clear_document_cache("HelixHR Celebration Reminder", row)

		recipients = frappe.get_all(
			"HelixHR Celebration Recipient", filters={"parent": row}, pluck="employee"
		)
		self.assertEqual(recipients, [self._employee_id("A2")])

	# --- P8-KTD7: restricted Jinja rendering ---------------------------------

	def test_restricted_rendering_blocks_a_template_side_effect_write(self):
		"""The security assertion KTD7 actually buys: `frappe.db.get_value`
		is exposed under *both* `get_safe_globals()` and the restricted
		`render_safe_globals()` -- it is a permission-free raw read either
		way, restricted or not, so a template reading it behaves the same
		under `restrict_globals=True` as without it. What the restricted
		set drops is anything with a *side effect*: `frappe.db.set_value`,
		`frappe.new_doc`/`delete_doc`, `frappe.call` (arbitrary whitelisted
		method invocation), `frappe.sendmail`, `db.commit`. This is the
		actual boundary HR's own template body is held to, and the one
		worth pinning."""
		mutating_name = "_Test Reminders Mutating Template"
		if not frappe.db.exists("Email Template", mutating_name):
			frappe.get_doc(
				{
					"doctype": "Email Template",
					"name": mutating_name,
					"use_html": 1,
					"subject": "MUTATE {{ names }}",
					"response_html": "{{ frappe.db.set_value('User', 'Administrator', 'full_name', 'Pwned') }}",
				}
			).insert(ignore_permissions=True)

		before = frappe.db.get_value("User", "Administrator", "full_name")

		self._stage(birthdays=("A1",))
		self._pick("helixhr_birthday_template", mutating_name)
		added = self._watch_mail()
		errors = self._errors()

		result = send_celebration_reminders()

		self.assertEqual(result["birthday"]["emails"], 0)
		self.assertEqual(added(), [])
		self.assertEqual(len(errors()), 1, "the render failure is in the scheduler log, not swallowed")
		self.assertEqual(
			frappe.db.get_value("User", "Administrator", "full_name"),
			before,
			"the template's write must never have executed",
		)


class TestHRSettingsBothSendersRefusal(IntegrationTestCase):
	"""P4-R18 / P4-KTD10: the contradiction is refused where HR creates it.

	Preflight FAILs on it too, but between HR's save and the next operator
	run there would be a morning of two emails to everybody.
	"""

	def setUp(self):
		frappe.set_user("Administrator")
		from helixhr.tests.utils import ensure_test_company

		self.company = ensure_test_company()
		_template(BIRTHDAY_TEMPLATE, "BDAYMARK {{ names }}")
		if not frappe.db.get_value(
			"HelixHR Celebration Reminder", {"event": "birthday", "company": self.company}, "name"
		):
			frappe.get_doc(
				{
					"doctype": "HelixHR Celebration Reminder",
					"event": "birthday",
					"company": self.company,
					"recipient_mode": "All employees",
				}
			).insert(ignore_permissions=True)
		self.row = frappe.db.get_value(
			"HelixHR Celebration Reminder", {"event": "birthday", "company": self.company}, "name"
		)
		self.original_reminder = frappe.db.get_value(
			"HelixHR Celebration Reminder", self.row, ["is_enabled", "email_template"], as_dict=True
		)
		self.original_hrms = {
			field: frappe.db.get_single_value("HR Settings", field)
			for field in ("send_birthday_reminders", "send_work_anniversary_reminders")
		}

	def tearDown(self):
		frappe.db.set_value(
			"HelixHR Celebration Reminder",
			self.row,
			{"is_enabled": self.original_reminder.is_enabled, "email_template": self.original_reminder.email_template},
			update_modified=False,
		)
		frappe.clear_document_cache("HelixHR Celebration Reminder", self.row)
		for field, value in self.original_hrms.items():
			frappe.db.set_single_value("HR Settings", field, value)
		frappe.clear_document_cache("HR Settings", "HR Settings")

	def test_a_helixhr_reminder_enabled_with_the_hrms_checkbox_on_is_refused(self):
		frappe.db.set_value(
			"HelixHR Celebration Reminder",
			self.row,
			{"is_enabled": 1, "email_template": BIRTHDAY_TEMPLATE},
			update_modified=False,
		)
		frappe.clear_document_cache("HelixHR Celebration Reminder", self.row)

		settings = frappe.get_doc("HR Settings")
		settings.send_birthday_reminders = 1

		with self.assertRaises(frappe.ValidationError) as caught:
			settings.save()

		message = str(caught.exception)
		spec = EVENTS["birthday"]
		self.assertIn(spec["hrms_label"], message)

	def test_unticking_the_hrms_checkbox_in_the_same_save_is_accepted(self):
		frappe.db.set_value(
			"HelixHR Celebration Reminder",
			self.row,
			{"is_enabled": 1, "email_template": BIRTHDAY_TEMPLATE},
			update_modified=False,
		)
		frappe.clear_document_cache("HelixHR Celebration Reminder", self.row)

		settings = frappe.get_doc("HR Settings")
		settings.send_birthday_reminders = 0
		# Never raises: HelixHR's own reminder stays enabled, but the HRMS
		# checkbox this save turns off means the two are no longer both on.
		settings.save()

		self.assertEqual(frappe.db.get_single_value("HR Settings", "send_birthday_reminders"), 0)

	def test_a_disabled_helixhr_reminder_never_refuses_the_hrms_checkbox(self):
		frappe.db.set_value(
			"HelixHR Celebration Reminder",
			self.row,
			{"is_enabled": 0, "email_template": BIRTHDAY_TEMPLATE},
			update_modified=False,
		)
		frappe.clear_document_cache("HelixHR Celebration Reminder", self.row)

		settings = frappe.get_doc("HR Settings")
		settings.send_birthday_reminders = 1
		settings.save()

		self.assertEqual(frappe.db.get_single_value("HR Settings", "send_birthday_reminders"), 1)

	def test_the_two_hrms_checkboxes_are_desk_read_only_fixtures(self):
		"""P8-KTD8, extended to the holiday takeover (plan 2026-10-04-004
		U3): the affordance for the hazard is never offered in Desk in the
		first place -- a Property Setter, not just the `validate` refusal
		above."""
		for field in (
			"send_birthday_reminders",
			"send_work_anniversary_reminders",
			"send_holiday_reminders",
			"frequency",
		):
			self.assertTrue(
				frappe.get_meta("HR Settings").get_field(field).read_only,
				f"{field} is not read-only in Desk",
			)


class TestCelebrationTemplateSeed(IntegrationTestCase):
	"""P4-KTD11 / P4-R19 / P4-AE7: seeded once, never overwritten."""

	def setUp(self):
		frappe.set_user("Administrator")

	def test_both_templates_are_present_and_hrs_edit_survives_a_re_run(self):
		from helixhr.patches.v1_0.seed_celebration_templates import TEMPLATES, execute

		for spec in TEMPLATES:
			self.assertTrue(frappe.db.exists("Email Template", spec["name"]), spec["name"])

		edited = TEMPLATES[0]["name"]
		frappe.db.set_value("Email Template", edited, "subject", "HR wrote this")
		execute()
		self.assertEqual(
			frappe.db.get_value("Email Template", edited, "subject"),
			"HR wrote this",
			"a re-run must never replace what HR edited",
		)

		# And a site that does not have them yet gets them, which is the
		# `after_install` path (`--install-app` marks patches complete
		# without running them).
		frappe.delete_doc("Email Template", edited, force=True, ignore_permissions=True)
		execute()
		self.assertEqual(
			frappe.db.get_value("Email Template", edited, "subject"), TEMPLATES[0]["subject"]
		)

	def test_the_defaults_render_against_the_documented_context(self):
		"""The context of P4-KTD13 is the contract; the shipped markup has to
		read only from it. Rendered through `_render_restricted` (P8-KTD7),
		the same call the real job makes -- not `get_formatted_email` -- so
		this also pins that the shipped defaults still render under
		restricted globals."""
		from helixhr.patches.v1_0.seed_celebration_templates import TEMPLATES
		from helixhr.reminders import _context, _render_restricted

		persons = [
			{"name": "Ada Lovelace", "image": None, "date_of_joining": "2020-01-01"},
			{"name": "Grace Hopper", "image": None, "date_of_joining": "2019-01-01"},
		]
		celebration_templates = (
			(next(t for t in TEMPLATES if t["name"] == "HelixHR Birthday Reminder"), "birthday"),
			(next(t for t in TEMPLATES if t["name"] == "HelixHR Work Anniversary Reminder"), "work_anniversary"),
		)
		for spec, event in celebration_templates:
			template = frappe.get_doc("Email Template", spec["name"])
			for people in (persons[:1], persons):
				rendered = _render_restricted(
					template, _context(people, "_Test Reminders Co A", event), sender=None
				)
				self.assertIn("Ada Lovelace", rendered["message"])
				self.assertTrue(rendered["subject"].strip())

	def test_the_holiday_default_renders_against_its_own_context(self):
		"""Plan 2026-10-04-004 U3: the holiday template's context is the one
		`_send_holiday_company` builds -- `employee_name`, `holidays`,
		`company`, `logo_url`, `portal_url`, `date`, `frequency`."""
		from helixhr.patches.v1_0.seed_celebration_templates import TEMPLATES
		from helixhr.reminders import _render_restricted

		spec = next(t for t in TEMPLATES if t["name"] == "HelixHR Holiday Reminder")
		template = frappe.get_doc("Email Template", spec["name"])
		rendered = _render_restricted(
			template,
			{
				"employee_name": "Ada Lovelace",
				"holidays": [{"date": "2026-12-25", "description": "Christmas"}],
				"company": "_Test Reminders Co A",
				"logo_url": "",
				"portal_url": "http://x/helixhr",
				"date": "2026-12-21",
				"frequency": "Weekly",
			},
			sender=None,
		)
		self.assertIn("Ada Lovelace", rendered["message"])
		self.assertIn("Christmas", rendered["message"])
		self.assertTrue(rendered["subject"].strip())

	def test_the_tookover_holiday_sender_replaces_hrms_own(self):
		"""R8: HRMS's `send_reminders_in_advance_weekly/monthly` read the
		same global checkbox this app now refuses to leave on -- they must
		never run while HelixHR owns the mail."""
		from helixhr.patches.v1_0.turn_off_hrms_celebration_senders import execute

		original = {
			field: frappe.db.get_single_value("HR Settings", field)
			for field in (
				"send_birthday_reminders",
				"send_work_anniversary_reminders",
				"send_holiday_reminders",
				"frequency",
			)
		}
		frappe.db.set_single_value("HR Settings", "send_holiday_reminders", 1)
		frappe.db.set_single_value("HR Settings", "frequency", "Weekly")
		try:
			execute()
			self.assertEqual(frappe.db.get_single_value("HR Settings", "send_holiday_reminders"), 0)
		finally:
			for field, value in original.items():
				frappe.db.set_single_value("HR Settings", field, value)
			frappe.clear_document_cache("HR Settings", "HR Settings")


class TestHolidayReminders(IntegrationTestCase):
	"""Plan 2026-10-04-004 U3: HelixHR's own per-company holiday reminder,
	replacing HRMS's hardcoded global one (R7, R8, KTD6, KTD7).

	Dates are pinned to a fixed Monday via `reminders.getdate`, so the
	Weekly window is Monday..Sunday regardless of which day the suite runs
	on."""

	def setUp(self):
		frappe.set_user("Administrator")
		ensure_test_email_account()
		_company(COMPANY_A, "TRCA")
		_company(COMPANY_B, "TRCB")
		# The holiday context has no `persons`, so this template must not
		# reuse the celebration marker HTML above.
		self.holiday_template = _holiday_template(
			"_Test HelixHR Holiday",
			"HOLIMARK {{ company }}",
			"HOLIDAYS:{% for holiday in holidays %}{{ holiday.description }};{% endfor %}"
			"TO:{{ employee_name }} FREQ:{{ frequency }}",
		)

		self.today = getdate()
		# The Monday of this week -- the Weekly send day, whatever today is.
		# Pushed a week later if it is also the 1st, so the Monthly test's
		# "plain Monday" premise holds every day of the year.
		self.monday = add_days(self.today, -self.today.weekday())
		if self.monday.day == 1:
			self.monday = add_days(self.monday, 7)
		self.wednesday = add_days(self.monday, 2)
		self._getdate_patch = patch.object(reminders, "getdate", return_value=self.monday)
		self._getdate_patch.start()
		self.addCleanup(self._getdate_patch.stop)
		# The rerun guard is keyed on the pinned Monday: clear it before and
		# after every test, or the alphabetically first test's send disables
		# every later one's.
		for company in (COMPANY_A, COMPANY_B):
			frappe.cache.delete_value(
				f"{reminders.CELEBRATION_GUARD_PREFIX}{self.monday}|holiday|{company}"
			)
			self.addCleanup(
				frappe.cache.delete_value,
				f"{reminders.CELEBRATION_GUARD_PREFIX}{self.monday}|holiday|{company}",
			)

		self.queued = set()
		self.original = {}
		for company in (COMPANY_A, COMPANY_B):
			row = frappe.db.get_value(
				"HelixHR Celebration Reminder", {"event": "holiday", "company": company}, "name"
			)
			if row:
				self.original[company] = frappe.get_doc("HelixHR Celebration Reminder", row).as_dict()
				frappe.delete_doc("HelixHR Celebration Reminder", row, force=True, ignore_permissions=True)
			# A enabled, B not (R1): most tests assert "only A's people are
			# mailed", so B starts disabled and its own test enables it.
			frappe.get_doc(
				{
					"doctype": "HelixHR Celebration Reminder",
					"event": "holiday",
					"company": company,
					"is_enabled": 1 if company == COMPANY_A else 0,
					"email_template": self.holiday_template,
					"recipient_mode": "All employees",
					"frequency": "Weekly",
				}
			).insert(ignore_permissions=True)
		self.addCleanup(self._restore)

	def _restore(self):
		frappe.set_user("Administrator")
		for company in (COMPANY_A, COMPANY_B):
			row = frappe.db.get_value(
				"HelixHR Celebration Reminder", {"event": "holiday", "company": company}, "name"
			)
			if row:
				frappe.db.delete("HelixHR Celebration Recipient", {"parent": row})
				frappe.delete_doc(
					"HelixHR Celebration Reminder", row, force=True, ignore_permissions=True
				)
			if company in self.original:
				frappe.get_doc(self.original[company]).insert(ignore_permissions=True)
			else:
				self.original.setdefault(company, None)
		frappe.cache.delete_value(
			f"{reminders.CELEBRATION_GUARD_PREFIX}{self.monday}|holiday|{COMPANY_A}"
		)
		frappe.cache.delete_value(
			f"{reminders.CELEBRATION_GUARD_PREFIX}{self.monday}|holiday|{COMPANY_B}"
		)
		for row in self.queued:
			frappe.db.delete("Email Queue Recipient", {"parent": row})
			frappe.db.delete("Email Queue", {"name": row})

	def _list(self, name, dates):
		"""A Holiday List with non-weekly holidays on the given dates (and
		one weekly-off row, which must never be listed)."""
		if frappe.db.exists("Holiday List", name):
			return name
		frappe.get_doc(
			{
				"doctype": "Holiday List",
				"holiday_list_name": name,
				"from_date": add_days(self.monday, -300),
				"to_date": add_days(self.monday, 300),
				"holidays": [
					{"holiday_date": str(day), "description": f"Holiday {index}", "weekly_off": 0}
					for index, day in enumerate(dates)
				]
				+ [
					# A weekly off inside the window: excluded by
					# `only_non_weekly=True`.
					{
						"holiday_date": str(add_days(self.monday, 3)),
						"description": "Weekly off",
						"weekly_off": 1,
					}
				],
			}
		).insert(ignore_permissions=True)
		return name

	def _employees(self, assignments):
		"""`{suffix: holiday_list}` -- stage the pool people and assign each
		one's list. Current HRMS resolves a person's list through submitted
		`Holiday List Assignment` rows (`get_holiday_list_for_employee`), not
		the Employee field, so the assignment is what the fixture makes."""
		for suffix, company in POOL.items():
			make_celebration_employee(
				f"REM-{suffix}",
				company,
				# No celebrating here -- keep both event dates out of today.
				date_of_birth=date(1990, self.other_month(), 4),
				date_of_joining=date(self.today.year - 5, self.other_month(), 4),
				company_email=self._address(suffix),
			)
			list_name = assignments.get(suffix)
			name = self._employee_id(suffix)
			if list_name:
				assignment = frappe.get_doc(
					{
						"doctype": "Holiday List Assignment",
						"assigned_to": name,
						"holiday_list": list_name,
						"from_date": add_days(self.monday, -300),
						"to_date": add_days(self.monday, 300),
					}
				)
				assignment.insert(ignore_permissions=True)
				assignment.submit()
				self.addCleanup(
					frappe.db.delete,
					"Holiday List Assignment",
					{"assigned_to": name},
				)

	def _employee_id(self, suffix):
		from helixhr.tests.utils import CELEBRATION_TAG

		return frappe.db.get_value(
			"Employee", {"employee_number": f"{CELEBRATION_TAG}-REM-{suffix}"}, "name"
		)

	def _address(self, suffix):
		return f"{suffix.lower()}@{DOMAIN}"

	def other_month(self):
		return 1 if self.today.month != 1 else 7

	def _watch_mail(self):
		before = set(frappe.get_all("Email Queue", pluck="name"))

		def added():
			mails = []
			for row in sorted(set(frappe.get_all("Email Queue", pluck="name")) - before):
				recipients = sorted(
					frappe.get_all("Email Queue Recipient", filters={"parent": row}, pluck="recipient")
				)
				if not any(address.endswith(DOMAIN) for address in recipients):
					continue
				self.queued.add(row)
				message = frappe.db.get_value("Email Queue", row, "message")
				mails.append({"recipients": recipients, **_read(message)})
			return mails

		return added

	def test_weekly_on_monday_lists_the_holidays_ahead(self):
		self._list("_Test Holiday List A", (self.wednesday,))
		self._employees({"A1": "_Test Holiday List A", "A2": "_Test Holiday List A", "A3": None})
		added = self._watch_mail()

		result = reminders.send_holiday_reminders()

		mails = added()
		self.assertEqual(result["companies"], 1)
		self.assertEqual(result["emails"], 1, "one send for the shared holiday set")
		self.assertEqual(mails[0]["recipients"], [self._address("A1"), self._address("A2")])
		self.assertIn("HOLIMARK", mails[0]["subject"])
		self.assertIn("Holiday 0", mails[0]["body"])
		self.assertNotIn("Weekly off", mails[0]["body"], "weekly-off rows are never listed")

	def test_an_employee_on_a_list_with_nothing_ahead_gets_no_mail(self):
		self._list("_Test Holiday List Empty", (add_days(self.monday, 100),))
		self._employees({"A1": "_Test Holiday List Empty", "A2": None, "A3": None})
		added = self._watch_mail()

		result = reminders.send_holiday_reminders()

		self.assertEqual(result["emails"], 0)
		self.assertEqual(added(), [])

	def test_two_holiday_lists_in_one_company_mean_two_sends(self):
		self._list("_Test Holiday List A", (self.wednesday,))
		self._list("_Test Holiday List B", (add_days(self.monday, 4),))
		self._employees({"A1": "_Test Holiday List A", "A2": "_Test Holiday List B", "A3": "_Test Holiday List B"})
		added = self._watch_mail()

		result = reminders.send_holiday_reminders()

		mails = added()
		self.assertEqual(result["emails"], 2)
		by_recipient = {tuple(mail["recipients"]): mail for mail in mails}
		self.assertIn((self._address("A1"),), by_recipient)
		self.assertEqual(
			by_recipient[(self._address("A2"), self._address("A3"))]["recipients"],
			[self._address("A2"), self._address("A3")],
		)

	def test_a_monthly_company_on_a_plain_monday_sends_nothing(self):
		frappe.db.set_value(
			"HelixHR Celebration Reminder",
			frappe.db.get_value(
				"HelixHR Celebration Reminder",
				{"event": "holiday", "company": COMPANY_A},
				"name",
			),
			"frequency",
			"Monthly",
		)
		self._list("_Test Holiday List A", (self.wednesday,))
		self._employees({"A1": "_Test Holiday List A", "A2": None, "A3": None})
		added = self._watch_mail()

		result = reminders.send_holiday_reminders()

		self.assertEqual(result["companies"], 0)
		self.assertEqual(added(), [])

	def test_a_disabled_company_sends_nothing(self):
		"""R1 for holidays: B's row is off (setUp), B's employees get nothing
		even though B1 shares A's list."""
		self._list("_Test Holiday List A", (self.wednesday,))
		self._employees(
			{"A1": "_Test Holiday List A", "A2": None, "A3": None, "B1": "_Test Holiday List A"}
		)
		added = self._watch_mail()

		reminders.send_holiday_reminders()

		mails = added()
		recipients = [r for mail in mails for r in mail["recipients"]]
		self.assertNotIn(self._address("B1"), recipients)

	def test_a_second_run_the_same_day_is_a_no_op(self):
		"""KTD8: the dated per-company guard covers the holiday sender too."""
		self._list("_Test Holiday List A", (self.wednesday,))
		self._employees({"A1": "_Test Holiday List A", "A2": None, "A3": None})
		added = self._watch_mail()

		reminders.send_holiday_reminders()
		reminders.send_holiday_reminders()

		self.assertEqual(len(added()), 1)

	def test_an_hrms_holiday_flag_on_with_an_enabled_row_is_refused(self):
		frappe.db.set_single_value("HR Settings", "send_holiday_reminders", 1)
		try:
			settings = frappe.get_doc("HR Settings")
			settings.send_holiday_reminders = 1
			with self.assertRaises(frappe.ValidationError):
				settings.save()
		finally:
			frappe.db.set_single_value("HR Settings", "send_holiday_reminders", 0)

	def test_the_sender_is_registered_as_a_daily_scheduler_event(self):
		self.assertIn(
			"helixhr.reminders.send_holiday_reminders",
			frappe.get_hooks("scheduler_events")["daily"],
		)


class TestOverdueDigests(IntegrationTestCase):
	"""Plan 2026-10-02-001 U11 (R23..R25, KTD13). Leave rows are raw inserts:
	the collector reads columns only, and a real submit depends on the day.
	The queue is a delta per recipient, as above -- `sendmail` commits."""

	def setUp(self):
		frappe.set_user("Administrator")
		ensure_test_email_account()
		self.employee, _, self.manager, self.manager_user = make_test_employee_and_manager()
		self.hr_user = ensure_hr_manager_user()
		self.company = frappe.db.get_value("Employee", self.employee, "company")
		frappe.local.conf["helixhr_approval_overdue_days"] = 2
		self.addCleanup(frappe.local.conf.pop, "helixhr_approval_overdue_days", None)
		self.guard = f"{reminders.OVERDUE_GUARD_PREFIX}{getdate()}"
		frappe.cache.delete_value(self.guard)
		self.addCleanup(frappe.cache.delete_value, self.guard)
		frappe.db.delete("HelixHR Message Template", {"name": ["in", ["approval_overdue_digest", "hr_overdue_summary"]]})
		self.since = now_datetime()

	def _leave(self, age_days, offset):
		doc = frappe.get_doc(
			{
				"doctype": "Leave Application",
				"employee": self.employee,
				"employee_name": "Overdue Fixture",
				"leave_type": "Casual Leave",
				"from_date": add_days(today(), 200 + offset),
				"to_date": add_days(today(), 200 + offset),
				"status": "Open",
				"company": self.company,
				"posting_date": today(),
				"leave_approver": self.manager_user,
				PENDING_SINCE: add_to_date(now_datetime(), days=-age_days),
			}
		)
		doc.set_new_name()
		doc.db_insert()
		self.addCleanup(frappe.db.delete, "Leave Application", {"name": doc.name})
		return doc.name

	def _mail_to(self, user):
		return frappe.get_all(
			"Email Queue",
			filters={"creation": [">=", self.since], "name": ["in", frappe.get_all(
				"Email Queue Recipient", filters={"recipient": user}, pluck="parent")]},
			fields=["name", "message"],
		)

	def _mine(self, items):
		return [item for item in items if item["employee"] == self.employee]

	def test_one_digest_lists_every_item_the_approver_is_late_on(self):
		names = [self._leave(3, 0), self._leave(5, 1)]
		self._leave(1, 2)  # within the threshold
		send = mock_send()
		with send:
			reminders.send_overdue_digests()
		digests = [c for c in send.calls if c[0] == "approval_overdue_digest" and c[1] == [self.manager_user]]
		self.assertEqual(len(digests), 1)
		urls = [item["url"] for item in digests[0][2]["items"]]
		for name in names:
			self.assertTrue(any(url.endswith(name) for url in urls))

	def test_the_digest_reaches_the_mail_queue_once_even_after_clear_cache(self):
		self._leave(3, 0)
		reminders.send_overdue_digests()
		self.assertEqual(len(self._mail_to(self.manager_user)), 1)
		frappe.clear_cache()
		self.assertEqual(reminders.send_overdue_digests(), {"skipped": True})
		self.assertEqual(len(self._mail_to(self.manager_user)), 1)
		for row in self._mail_to(self.manager_user) + self._mail_to(self.hr_user):
			frappe.delete_doc("Email Queue", row.name, force=True, ignore_permissions=True)

	def test_the_next_day_sends_again(self):
		self._leave(3, 0)
		reminders.send_overdue_digests()
		frappe.cache.delete_value(self.guard)  # the key a new day would not find
		send = mock_send()
		with send:
			reminders.send_overdue_digests()
		self.assertTrue(any(c[1] == [self.manager_user] for c in send.calls))
		for row in self._mail_to(self.manager_user) + self._mail_to(self.hr_user):
			frappe.delete_doc("Email Queue", row.name, force=True, ignore_permissions=True)

	def test_nothing_overdue_sends_nothing(self):
		send = mock_send()
		with send, patch.object(reminders, "collect_overdue", return_value=[]):
			reminders.send_overdue_digests()
		self.assertEqual(send.calls, [])

	def test_an_event_switched_off_sends_no_mail(self):
		self._leave(3, 0)
		for key in ("approval_overdue_digest", "hr_overdue_summary"):
			frappe.get_doc(
				{"doctype": "HelixHR Message Template", "template_key": key, "name": key,
				 "subject": "x", "body": "x", "is_enabled": 0}
			).db_insert()
		reminders.send_overdue_digests()
		self.assertEqual(self._mail_to(self.manager_user), [])
		self.assertEqual(self._mail_to(self.hr_user), [])

	def test_an_approver_without_email_goes_to_the_hr_summary(self):
		name = self._leave(3, 0)
		email_before = frappe.db.get_value("User", self.manager_user, "email")
		frappe.db.set_value("User", self.manager_user, "email", "")
		self.addCleanup(frappe.db.set_value, "User", self.manager_user, "email", email_before)
		self._assert_no_active_owner(name)

	def test_a_disabled_approver_goes_to_the_hr_summary(self):
		name = self._leave(3, 0)
		frappe.db.set_value("User", self.manager_user, "enabled", 0)
		self.addCleanup(frappe.db.set_value, "User", self.manager_user, "enabled", 1)
		self._assert_no_active_owner(name)

	def _assert_no_active_owner(self, name):
		item = next(i for i in reminders.collect_overdue() if i["name"] == name)
		self.assertEqual(item["owners"], [])
		send = mock_send()
		with send:
			reminders.send_overdue_digests()
		self.assertFalse(any(c[1] == [self.manager_user] for c in send.calls))
		summary = next(c for c in send.calls if c[0] == "hr_overdue_summary" and c[1] == [self.hr_user])
		group = next(o for o in summary[2]["owners"] if any(i["url"].endswith(name) for i in o["items"]))
		self.assertTrue(group["inactive"])

	def test_the_hr_summary_is_narrowed_to_admin_scope(self):
		name = self._leave(3, 0)
		items = reminders.collect_overdue()
		with patch("helixhr.utils.resolve_admin_scope", return_value={"kind": "company", "company": "_No Such Co"}):
			self.assertEqual(reminders._summary_owners(items, self.hr_user), [])
		with patch("helixhr.utils.resolve_admin_scope", return_value={"kind": "company", "company": self.company}):
			owners = reminders._summary_owners(items, self.hr_user)
		self.assertTrue(any(i["url"].endswith(name) for o in owners for i in o["items"]))

	def test_an_hr_request_without_sla_or_waiting_on_the_employee_is_never_overdue(self):
		old = add_to_date(now_datetime(), days=-30)
		rows = [
			frappe._dict(name="R1", category="no-sla", status="Open", creation=old),
			frappe._dict(name="R2", category="sla", status="Waiting on Employee", creation=old),
		]

		def get_all(doctype, **kwargs):
			if doctype == "HelixHR Request Category":
				return [frappe._dict(name="no-sla", sla_days=0), frappe._dict(name="sla", sla_days=1)]
			if doctype == "HR Request":
				statuses = kwargs["filters"]["status"][1]
				return [frappe._dict(r, employee=self.employee, subject="s", routed_to_role="HR Manager",
					picked_up_by=None) for r in rows if r.status in statuses]
			return []

		with patch.object(reminders.frappe, "get_all", side_effect=get_all):
			self.assertEqual(reminders.collect_overdue(), [])

	def test_one_failing_approver_leaves_the_others_their_mail(self):
		self._leave(3, 0)
		before = frappe.db.count("Error Log", {"method": reminders.OVERDUE_ERROR_TITLE})
		calls = []

		def boom(event, recipients, context, *args, **kwargs):
			calls.append((event, recipients))
			if recipients == [self.manager_user]:
				raise RuntimeError("boom")

		with patch("helixhr.utils.send_notification", side_effect=boom):
			reminders.send_overdue_digests()
		self.assertIn(("hr_overdue_summary", [self.hr_user]), calls)
		self.assertGreater(frappe.db.count("Error Log", {"method": reminders.OVERDUE_ERROR_TITLE}), before)

	def test_the_job_and_its_guard_are_registered(self):
		self.assertIn("helixhr.reminders.send_overdue_digests", frappe.get_hooks("scheduler_events")["daily"])
		self.assertIn(f"{reminders.OVERDUE_GUARD_PREFIX}*", frappe.get_hooks("persistent_cache_keys"))


class mock_send:
	"""Records `send_notification` calls instead of queueing mail."""

	def __init__(self):
		self.calls = []
		self._patch = patch(
			"helixhr.utils.send_notification",
			side_effect=lambda event, recipients, context, *a, **k: self.calls.append((event, recipients, context)),
		)

	def __enter__(self):
		self._patch.__enter__()
		return self

	def __exit__(self, *exc):
		return self._patch.__exit__(*exc)


def _read(message):
	"""Subject and HTML body out of the MIME blob the Email Queue stores."""
	parsed = email.message_from_string(message)
	subject = str(email.header.make_header(email.header.decode_header(parsed["Subject"] or "")))
	body = ""
	for part in parsed.walk():
		if part.get_content_type() == "text/html":
			body += part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8")
	return {"subject": subject, "body": body}
