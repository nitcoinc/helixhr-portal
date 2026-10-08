# Copyright (c) 2026, HelixHR Contributors
# For license information, please see license.txt

"""Plan 2026-10-08-001 U1: a company's own clock."""

from datetime import UTC, date, datetime
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from helixhr import preflight
from helixhr.tests.utils import clock_at
from helixhr.utils import company_time_zone, company_today, employee_company_today

COMPANY = "_Test Company"
SYSTEM = "America/Chicago"
# 2026-11-01 01:30 UTC: 20:30 CDT on Oct 31 in Chicago, 07:00 IST on Nov 1.
ACROSS_MIDNIGHT = datetime(2026, 11, 1, 1, 30, tzinfo=UTC)


class TestCompanyTimeZone(IntegrationTestCase):
	def setUp(self):
		before = frappe.db.get_value("Company", COMPANY, "helixhr_time_zone")
		self.addCleanup(
			frappe.db.set_value, "Company", COMPANY, "helixhr_time_zone", before, update_modified=False
		)
		system = patch("frappe.utils.get_system_timezone", return_value=SYSTEM)
		system.start()
		self.addCleanup(system.stop)

	def _set(self, zone):
		frappe.db.set_value("Company", COMPANY, "helixhr_time_zone", zone, update_modified=False)

	def test_a_company_zone_decides_its_date(self):
		self._set("Asia/Kolkata")
		with clock_at(ACROSS_MIDNIGHT):
			self.assertEqual(company_time_zone(COMPANY), "Asia/Kolkata")
			self.assertEqual(company_today(COMPANY), date(2026, 11, 1))

	def test_a_blank_zone_is_the_system_zone(self):
		self._set(None)
		with clock_at(ACROSS_MIDNIGHT):
			self.assertEqual(company_time_zone(COMPANY), SYSTEM)
			self.assertEqual(company_today(COMPANY), date(2026, 10, 31))

	def test_no_company_is_the_system_date(self):
		with clock_at(ACROSS_MIDNIGHT):
			self.assertEqual(company_today(None), date(2026, 10, 31))
			self.assertEqual(employee_company_today(None), date(2026, 10, 31))

	def test_an_employee_follows_their_company(self):
		self._set("Asia/Kolkata")
		employee = frappe.db.get_value("Employee", {"company": COMPANY, "status": "Active"}, "name")
		if not employee:
			self.skipTest("needs an active employee in the test company")
		with clock_at(ACROSS_MIDNIGHT):
			self.assertEqual(employee_company_today(employee), date(2026, 11, 1))

	def test_an_unknown_zone_is_refused_on_save(self):
		company = frappe.get_doc("Company", COMPANY)
		company.helixhr_time_zone = "Asia/Kolkatta"
		with self.assertRaisesRegex(frappe.ValidationError, "not a known time zone"):
			company.save()

	def test_a_known_zone_is_accepted_and_trimmed(self):
		company = frappe.get_doc("Company", COMPANY)
		company.helixhr_time_zone = " Asia/Kolkata "
		company.save()
		self.assertEqual(frappe.db.get_value("Company", COMPANY, "helixhr_time_zone"), "Asia/Kolkata")

	def test_a_bad_value_behind_validate_falls_back_and_preflight_warns(self):
		self._set("Mars/Olympus")
		self.assertEqual(company_time_zone(COMPANY), SYSTEM)
		result = preflight.check_company_time_zones()
		self.assertEqual(result["status"], preflight.WARN)
		self.assertIn("Mars/Olympus", result["detail"])

	def test_preflight_lists_each_companys_effective_zone(self):
		self._set("Asia/Kolkata")
		result = preflight.check_company_time_zones()
		self.assertEqual(result["status"], preflight.PASS)
		self.assertIn(f"{COMPANY}: Asia/Kolkata", result["detail"])
