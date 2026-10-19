"""Plan 2026-10-18-001 U11: every write-scope consumer handles a ``companies``
scope before any resolver returns one.

No resolver returns ``{"kind": "companies"}`` for a write scope yet -- Release
3 does -- so these tests inject it. A two-company scope [A, B] must reach A
and B and never C, at every consumer that used to branch only on
``kind == "company"`` and read anything else as "every company". An unknown
kind raises.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from helixhr import api, reports
from helixhr.helixhr.doctype.hr_request import hr_request
from helixhr.tests.utils import (
	create_test_company,
	ensure_baseline_company,
	ensure_test_company,
	make_assignment,
	make_test_user,
)
from helixhr.utils import (
	DELIVERY_MANAGER_ROLE,
	admin_scope_employee_filters,
	assignable,
	employee_in_admin_scope,
	may_assign,
	read_companies,
	scope_covers,
)

COMPANY_C = "_Test Scope Kinds C"
USER_A = "u11-a@helixhr.test"
USER_C = "u11-c@helixhr.test"
HR_USER = "u11-hr@helixhr.test"


class TestCompaniesScopeKind(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company_a = ensure_test_company()
		self.company_b = ensure_baseline_company()
		self.company_c = create_test_company(COMPANY_C, "TSKC")
		self.scope = {"kind": "companies", "companies": [self.company_a, self.company_b]}
		self.employee_a = make_test_user(USER_A, self.company_a)
		self.employee_c = make_test_user(USER_C, self.company_c)
		make_test_user(HR_USER, self.company_a, create_user_permission=0)
		# Plan 2026-10-19-001 U3: an HR Manager on [A, B] is now a real
		# `companies` scope, so the HR gates the patch does not reach agree.
		make_assignment(HR_USER, "HR Manager", [self.company_a, self.company_b])

	def _as_admin_scope(self):
		return patch("helixhr.api.resolve_admin_scope", return_value=self.scope)

	# --- the shared helpers --------------------------------------------------

	def test_read_companies_and_scope_covers(self):
		self.assertEqual(read_companies(self.scope), [self.company_a, self.company_b])
		self.assertTrue(scope_covers(self.scope, self.company_b))
		self.assertFalse(scope_covers(self.scope, self.company_c))
		self.assertTrue(scope_covers({"kind": "unscoped"}, self.company_c))
		self.assertFalse(scope_covers({"kind": "none"}, self.company_a))
		self.assertEqual(read_companies({"kind": "company", "company": self.company_a}), [self.company_a])

	def test_an_unknown_kind_raises(self):
		with self.assertRaises(frappe.ValidationError):
			read_companies({"kind": "everything"})
		with self.assertRaises(frappe.ValidationError):
			scope_covers({"kind": "everything"}, self.company_a)

	def test_employee_filters_and_single_record_check(self):
		self.assertEqual(
			admin_scope_employee_filters(self.scope), {"company": ["in", [self.company_a, self.company_b]]}
		)
		self.assertTrue(employee_in_admin_scope(self.employee_a, self.scope))
		self.assertFalse(employee_in_admin_scope(self.employee_c, self.scope))

	# --- api write gates -----------------------------------------------------

	def test_company_gate_and_company_picker(self):
		# Both import the resolver inside the function, so it is patched at home.
		with patch("helixhr.utils.resolve_admin_scope", return_value=self.scope):
			api._assert_company_in_admin_scope(self.company_b)
			with self.assertRaises(frappe.PermissionError):
				api._assert_company_in_admin_scope(self.company_c)
			self.assertEqual(api._companies_in_admin_scope(), [self.company_a, self.company_b])

	def test_none_scope_picks_no_company(self):
		with patch("helixhr.utils.resolve_admin_scope", return_value={"kind": "none", "company": None}):
			self.assertEqual(api._companies_in_admin_scope(), [])

	def test_oversight_covers_both_companies(self):
		with self._as_admin_scope():
			self.assertEqual(
				api._oversight_scope(HR_USER),
				{"hr_manager": True, "companies": [self.company_a, self.company_b]},
			)

	def test_portal_role_edits_stop_at_the_scope(self):
		# Plan 2026-10-19-001 U5/U6: a Portal Admin's grant authority is
		# `assignable`, which reads its Portal Admin scope.
		with (
			patch("helixhr.utils.can_manage_access", return_value=False),
			patch("helixhr.utils.portal_role_scope", return_value=self.scope),
		):
			authority = assignable(HR_USER)
		row = {"role": DELIVERY_MANAGER_ROLE, "all_orgs": 0}
		self.assertTrue(may_assign(authority, {**row, "company": self.company_b}))
		self.assertFalse(may_assign(authority, {**row, "company": self.company_c}))

	def test_export_log_shows_only_the_scope_companies(self):
		rows = []
		for company in (self.company_a, self.company_c):
			doc = frappe.get_doc(
				{
					"doctype": "HelixHR Report Export",
					"report_key": "employee_directory",
					"report_label": "Directory",
					"format": "csv",
					"mode": "Inline",
					"status": "Ready",
					"company": company,
					"exporter_company": company,
				}
			)
			doc.insert(ignore_permissions=True)
			rows.append(doc.name)
		with (
			patch("helixhr.api.resolve_portal_admin_scope", return_value=self.scope),
			patch("helixhr.utils.grantable_companies", return_value=[]),
		):
			names = {row["name"] for row in api.get_export_log(page_length=200)["rows"]}
		self.assertIn(rows[0], names)
		self.assertNotIn(rows[1], names)

	def test_people_search_stops_at_the_scope(self):
		names = frappe.get_all("Employee", **api._people_search_scope(self.scope, None), pluck="name")
		self.assertIn(self.employee_a, names)
		self.assertNotIn(self.employee_c, names)

	def test_organisation_view_refuses_a_company_outside_the_scope(self):
		with patch("helixhr.api.resolve_read_scope", return_value=self.scope):
			with self.assertRaises(frappe.PermissionError):
				api.get_organisation_view(self.company_c)

	# --- HR Request and reports ----------------------------------------------

	def test_hr_request_list_and_record_stop_at_the_scope(self):
		with patch.object(hr_request, "resolve_admin_scope", return_value=self.scope):
			condition = hr_request.get_permission_query_conditions(HR_USER)
			self.assertIn(frappe.db.escape(self.employee_a, percent=False), condition)
			self.assertNotIn(frappe.db.escape(self.employee_c, percent=False), condition)
			doc = frappe._dict(employee=self.employee_c, routed_to_role="HR Manager")
			self.assertFalse(hr_request.has_permission(doc, user=HR_USER))
			doc.employee = self.employee_a
			self.assertTrue(hr_request.has_permission(doc, user=HR_USER))

	def test_a_wrapped_report_never_runs_for_a_requested_company_under_a_companies_scope(self):
		with self.assertRaises(frappe.ValidationError):
			reports._scope_company(self.scope, {"company": self.company_c})
		self.assertEqual(
			reports._scope_company(
				{"kind": "company", "company": self.company_a}, {"company": self.company_c}
			),
			self.company_a,
		)

	def test_a_companies_admin_tier_runs_reports_one_company_at_a_time(self):
		from helixhr.utils import resolve_report_access

		with patch("helixhr.utils.resolve_admin_scope", return_value=self.scope):
			access = resolve_report_access(HR_USER, "employee_directory", company=self.company_b)
			refused = resolve_report_access(HR_USER, "employee_directory", company=self.company_c)
		self.assertEqual({row["company"] for row in access["companies"]}, {self.company_a, self.company_b})
		self.assertEqual(access["scope"], {"kind": "company", "company": self.company_b})
		self.assertTrue(refused["company_removed"])
		self.assertNotEqual(refused["company"], self.company_c)
