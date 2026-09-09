import json

import frappe
from frappe.tests import IntegrationTestCase

from helixhr.api import (
	_PAYSLIP_MAX_PAGE,
	download_my_payslip,
	get_my_payslip,
	get_my_payslips,
)
from helixhr.tests.utils import (
	EMPLOYEE_USER,
	PAYSLIP_AMOUNTS,
	TEST_COMPANY,
	make_test_employee_and_manager,
	make_test_salary_slip,
	make_test_user,
)

# A past, closed year of its own, so the exact-count assertions here are
# never disturbed by a slip another suite seeds for the same employee (the
# strict-permission matrix in test_fixtures.py seeds one, in 2021).
YEAR = 2024
PERIODS = {
	"usd": (f"{YEAR}-06-01", f"{YEAR}-06-30"),
	"inr": (f"{YEAR}-07-01", f"{YEAR}-07-31"),
	"withheld": (f"{YEAR}-08-01", f"{YEAR}-08-31"),
	"revised": (f"{YEAR}-09-01", f"{YEAR}-09-30"),
	"draft": (f"{YEAR}-10-01", f"{YEAR}-10-31"),
	"extra": (f"{YEAR}-11-01", f"{YEAR}-11-30"),
}

# A colleague in the same company who is nobody's report and reports to
# nobody: Employee is a nested set, so a manager in the same line would
# legitimately reach their reports' records (P3-AE1).
COLLEAGUE_USER = "payslip-colleague@helixhr.test"


class PayslipTestCase(IntegrationTestCase):
	"""P3-U2 / P3-R1 to P3-R4. One employee with a year of slips in two
	currencies, one withheld, one cancelled-and-revised and one draft, plus a
	colleague with a slip of their own."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.employee_name, _, _, _ = make_test_employee_and_manager()
		cls.colleague_name = make_test_user(COLLEAGUE_USER, TEST_COMPANY)

		cls.slips = {
			"usd": make_test_salary_slip(cls.employee_name, *PERIODS["usd"], currency="USD"),
			"inr": make_test_salary_slip(cls.employee_name, *PERIODS["inr"], currency="INR"),
			"withheld": make_test_salary_slip(
				cls.employee_name, *PERIODS["withheld"], currency="USD", withheld=True
			),
			"cancelled": make_test_salary_slip(
				cls.employee_name, *PERIODS["revised"], currency="USD", docstatus=2
			),
			"draft": make_test_salary_slip(
				cls.employee_name, *PERIODS["draft"], currency="USD", docstatus=0
			),
			"extra": make_test_salary_slip(cls.employee_name, *PERIODS["extra"], currency="USD"),
		}
		# The correction of the cancelled one: same period, `amended_from`
		# set, which is the only thing "Revised" keys off (P3-R4).
		cls.slips["revised"] = make_test_salary_slip(
			cls.employee_name,
			*PERIODS["revised"],
			currency="USD",
			amended_from=cls.slips["cancelled"],
		)
		cls.colleague_slip = make_test_salary_slip(
			cls.colleague_name, *PERIODS["usd"], currency="USD"
		)

	def setUp(self):
		frappe.set_user(EMPLOYEE_USER)

	def tearDown(self):
		frappe.set_user("Administrator")

	def _rows(self, **kwargs):
		return get_my_payslips(year=YEAR, **kwargs)["payslips"]

	def _by_name(self, rows):
		return {row["name"]: row for row in rows}

	# --- scenario 1: isolation (P3-AE1) ---------------------------------

	def test_only_this_employees_slips_are_listed(self):
		names = {row["name"] for row in self._rows(limit=_PAYSLIP_MAX_PAGE)}
		self.assertIn(self.slips["usd"], names)
		self.assertNotIn(self.colleague_slip, names)

	def test_a_colleagues_slip_is_refused_by_the_wrapper(self):
		with self.assertRaises(frappe.DoesNotExistError):
			get_my_payslip(self.colleague_slip)
		with self.assertRaises(frappe.DoesNotExistError):
			download_my_payslip(self.colleague_slip)

	def test_a_colleagues_slip_is_refused_through_frappes_own_pdf_endpoint(self):
		"""P3-R3. `download_pdf` is whitelisted in its own right, so the
		wrapper's ownership check is not the only thing standing in front of
		a colleague's payslip -- the Employee role's Salary Slip rules and
		the User Permission are."""
		from frappe.utils.print_format import download_pdf

		with self.assertRaises(frappe.PermissionError):
			download_pdf("Salary Slip", self.colleague_slip)

	def test_a_colleagues_slip_is_refused_through_api_resource(self):
		"""`/api/resource/Salary Slip/<name>` is `frappe.client.get`."""
		from frappe.client import get as client_get

		with self.assertRaises(frappe.PermissionError):
			client_get("Salary Slip", self.colleague_slip)

	def test_the_generic_list_and_report_views_stay_refused(self):
		"""Role Employee has no `report` on Salary Slip, so the report view
		is refused outright, and the generic list never carries another
		employee's row."""
		names = frappe.get_list("Salary Slip", filters={"docstatus": 1}, pluck="name", limit=0)
		self.assertNotIn(self.colleague_slip, names)

		# The report view reads its arguments from the form dict, the way the
		# HTTP route does. It is not refused outright -- role Employee holds
		# `read` -- but it is scoped by the same User Permission, so it can
		# never answer with a colleague's slip.
		from frappe.desk.reportview import get_list as reportview_list

		frappe.local.form_dict = frappe._dict(
			doctype="Salary Slip",
			fields=json.dumps(["`tabSalary Slip`.`name`"]),
			filters=json.dumps([["Salary Slip", "name", "=", self.colleague_slip]]),
			limit_page_length=20,
		)
		try:
			self.assertEqual(reportview_list(), [])
		finally:
			frappe.local.form_dict = frappe._dict()

	# --- scenario 2: currency and status (P3-AE2) -----------------------

	def test_each_row_carries_its_own_currency_and_amounts(self):
		rows = self._by_name(self._rows(limit=_PAYSLIP_MAX_PAGE))
		usd, inr = rows[self.slips["usd"]], rows[self.slips["inr"]]

		self.assertEqual(usd["currency"], "USD")
		self.assertEqual(inr["currency"], "INR")
		usd_gross, usd_deduction = PAYSLIP_AMOUNTS["USD"]
		inr_gross, inr_deduction = PAYSLIP_AMOUNTS["INR"]
		self.assertEqual(usd["gross_pay"], usd_gross)
		self.assertEqual(usd["total_deduction"], usd_deduction)
		self.assertEqual(usd["net_pay"], usd_gross - usd_deduction)
		self.assertEqual(inr["gross_pay"], inr_gross)
		self.assertEqual(inr["net_pay"], inr_gross - inr_deduction)

	def test_the_list_runs_newest_period_first(self):
		ends = [row["end_date"] for row in self._rows(limit=_PAYSLIP_MAX_PAGE)]
		self.assertEqual(ends, sorted(ends, reverse=True))

	def test_a_withheld_slip_is_listed_and_flagged_without_a_pdf(self):
		row = self._by_name(self._rows(limit=_PAYSLIP_MAX_PAGE))[self.slips["withheld"]]
		self.assertEqual(row["status"], "Withheld")
		self.assertTrue(row["withheld"])
		self.assertFalse(row["can_download"])

	def test_a_cancelled_slip_is_absent_and_its_replacement_reads_revised(self):
		rows = self._by_name(self._rows(limit=_PAYSLIP_MAX_PAGE))
		self.assertNotIn(self.slips["cancelled"], rows)
		self.assertTrue(rows[self.slips["revised"]]["revised"])
		self.assertFalse(rows[self.slips["usd"]]["revised"])

	# --- scenario 3: drafts ---------------------------------------------

	def test_a_draft_slip_is_absent_from_the_list_and_from_the_detail(self):
		self.assertNotIn(self.slips["draft"], self._by_name(self._rows(limit=_PAYSLIP_MAX_PAGE)))
		with self.assertRaises(frappe.DoesNotExistError):
			get_my_payslip(self.slips["draft"])

	# --- scenario 4: paging ---------------------------------------------

	def test_paging_is_bounded_and_reports_the_total(self):
		page = get_my_payslips(year=YEAR, limit=2)
		self.assertEqual(len(page["payslips"]), 2)
		self.assertEqual(page["limit"], 2)
		# Five submitted slips in the seeded year: USD, INR, withheld, the
		# revision and one more. The cancelled one and the draft are out.
		self.assertEqual(page["total"], 5)

		second = get_my_payslips(year=YEAR, limit=2, start=2)
		self.assertEqual(second["start"], 2)
		self.assertFalse(
			{row["name"] for row in page["payslips"]}
			& {row["name"] for row in second["payslips"]},
		)

		clamped = get_my_payslips(year=YEAR, limit=100000)
		self.assertEqual(clamped["limit"], _PAYSLIP_MAX_PAGE)
		self.assertEqual(get_my_payslips(year=YEAR, limit=0)["limit"], 20)

	def test_the_years_offered_include_the_seeded_year(self):
		self.assertIn(YEAR, get_my_payslips()["years"])

	# --- scenario 2 and 5: the detail and the PDF -----------------------

	def test_the_detail_carries_the_breakdown(self):
		detail = get_my_payslip(self.slips["usd"])
		self.assertEqual(detail["currency"], "USD")
		self.assertGreater(detail["payment_days"], 0)
		self.assertGreater(detail["total_working_days"], 0)
		self.assertEqual(detail["leave_without_pay"], 0)
		self.assertEqual(
			[row["amount"] for row in detail["earnings"]], [PAYSLIP_AMOUNTS["USD"][0]]
		)
		self.assertEqual(
			[row["amount"] for row in detail["deductions"]], [PAYSLIP_AMOUNTS["USD"][1]]
		)
		self.assertTrue(all(row["salary_component"] for row in detail["earnings"]))

	def test_downloading_an_own_slip_answers_a_pdf_attachment_with_no_store(self):
		"""P3-U2 scenario 5.

		wkhtmltopdf fetches the print format's own stylesheet over HTTP from
		the site's own address, and a site running no web server -- which is
		every `bench run-tests` run, CI included -- cannot answer it, so the
		render itself is not portable. The refusals below and every guard in
		front of the render are; the rendered bytes and these two headers are
		asserted for real in `frontend/tests/e2e/payslips.spec.ts`, which
		goes through a served request.
		"""
		try:
			download_my_payslip(self.slips["usd"])
		except OSError as error:
			self.skipTest(f"wkhtmltopdf cannot reach this site over HTTP: {error}")

		self.assertEqual(frappe.local.response.type, "pdf")
		self.assertTrue(frappe.local.response.filecontent.startswith(b"%PDF"))
		disposition = frappe.local.response_headers["Content-Disposition"]
		self.assertTrue(disposition.startswith("attachment;"), disposition)
		self.assertIn(f"Payslip%20{YEAR}-06", disposition)
		self.assertEqual(frappe.local.response_headers["Cache-Control"], "no-store")

	def test_downloading_a_withheld_slip_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			download_my_payslip(self.slips["withheld"])

	def test_a_missing_name_and_a_colleagues_name_answer_the_same_refusal(self):
		"""P3-U2 step 3. Slip names embed the employee id, so a distinct
		"not yours" would confirm that a colleague's slip exists."""
		messages = []
		for name in ("Sal Slip/HR-EMP-NOPE/00001", self.colleague_slip):
			with self.assertRaises(frappe.DoesNotExistError) as caught:
				download_my_payslip(name)
			messages.append(str(caught.exception))
		self.assertEqual(messages[0], messages[1])
