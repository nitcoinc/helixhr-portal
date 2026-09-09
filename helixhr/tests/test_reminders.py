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

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import getdate

from helixhr.reminders import EVENTS, send_celebration_reminders
from helixhr.tests.utils import ensure_test_email_account, make_celebration_employee

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
		self.settings = {
			field: frappe.db.get_single_value("HR Settings", field)
			for field in ("helixhr_birthday_template", "helixhr_anniversary_template")
		}
		for field in self.settings:
			self._pick(field, None)

	def tearDown(self):
		frappe.set_user("Administrator")
		for field, value in self.settings.items():
			self._pick(field, value)
		for row in self.queued:
			frappe.db.delete("Email Queue Recipient", {"parent": row})
			frappe.db.delete("Email Queue", {"name": row})

	# --- fixtures ----------------------------------------------------------

	def _pick(self, field, template):
		"""Set the picker without going through `HR Settings.validate`.

		The refusal that hook carries is asserted on its own below; here the
		HRMS checkbox is beside the point, and a site whose checkbox happens
		to be ticked must not make the job's own tests unrunnable."""
		frappe.db.set_single_value("HR Settings", field, template)
		frappe.clear_document_cache("HR Settings", "HR Settings")

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

		self.assertEqual(send_celebration_reminders(), {})
		self.assertEqual(added(), [])

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

		added = self._watch_mail()
		send_celebration_reminders()
		self.assertIn("LOGO:[http", added()[0]["body"])

	def test_a_picked_template_that_does_not_exist_sends_nothing(self):
		self._stage(birthdays=("A1",))
		self._pick("helixhr_birthday_template", "_Test Template That Went Away")
		added = self._watch_mail()

		self.assertEqual(send_celebration_reminders()["birthday"]["emails"], 0)
		self.assertEqual(added(), [])

	def test_the_job_is_registered_as_a_daily_scheduler_event(self):
		self.assertIn(
			"helixhr.reminders.send_celebration_reminders",
			frappe.get_hooks("scheduler_events")["daily"],
		)


class TestHRSettingsBothSendersRefusal(IntegrationTestCase):
	"""P4-R18 / P4-KTD10: the contradiction is refused where HR creates it.

	Preflight FAILs on it too, but between HR's save and the next operator
	run there would be a morning of two emails to everybody.
	"""

	def setUp(self):
		frappe.set_user("Administrator")
		_template(BIRTHDAY_TEMPLATE, "BDAYMARK {{ names }}")
		self.original = {
			field: frappe.db.get_single_value("HR Settings", field)
			for field in (
				"helixhr_birthday_template",
				"helixhr_anniversary_template",
				"send_birthday_reminders",
				"send_work_anniversary_reminders",
			)
		}

	def tearDown(self):
		for field, value in self.original.items():
			frappe.db.set_single_value("HR Settings", field, value)
		frappe.clear_document_cache("HR Settings", "HR Settings")

	def test_a_helixhr_template_with_the_hrms_checkbox_on_is_refused(self):
		settings = frappe.get_doc("HR Settings")
		settings.send_birthday_reminders = 1
		settings.helixhr_birthday_template = BIRTHDAY_TEMPLATE

		with self.assertRaises(frappe.ValidationError) as caught:
			settings.save()

		message = str(caught.exception)
		spec = EVENTS["birthday"]
		self.assertIn(spec["hrms_label"], message)
		self.assertIn(spec["template_label"], message)

	def test_unticking_the_hrms_checkbox_in_the_same_save_is_accepted(self):
		settings = frappe.get_doc("HR Settings")
		settings.send_birthday_reminders = 0
		settings.helixhr_birthday_template = BIRTHDAY_TEMPLATE
		settings.save()

		self.assertEqual(
			frappe.db.get_single_value("HR Settings", "helixhr_birthday_template"),
			BIRTHDAY_TEMPLATE,
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
		read only from it."""
		from helixhr.patches.v1_0.seed_celebration_templates import TEMPLATES
		from helixhr.reminders import _context

		persons = [
			{"name": "Ada Lovelace", "image": None, "date_of_joining": "2020-01-01"},
			{"name": "Grace Hopper", "image": None, "date_of_joining": "2019-01-01"},
		]
		for spec, event in zip(TEMPLATES, ("birthday", "work_anniversary"), strict=True):
			template = frappe.get_doc("Email Template", spec["name"])
			for people in (persons[:1], persons):
				rendered = template.get_formatted_email(_context(people, "_Test Reminders Co A", event))
				self.assertIn("Ada Lovelace", rendered["message"])
				self.assertTrue(rendered["subject"].strip())


def _read(message):
	"""Subject and HTML body out of the MIME blob the Email Queue stores."""
	parsed = email.message_from_string(message)
	subject = str(email.header.make_header(email.header.decode_header(parsed["Subject"] or "")))
	body = ""
	for part in parsed.walk():
		if part.get_content_type() == "text/html":
			body += part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8")
	return {"subject": subject, "body": body}
