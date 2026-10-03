"""Plan 2026-10-04-001 U12: saved report views -- private/shared, company
scoped listing, validation against the catalog, owner/HR delete, and
applying a view as the viewer (never widened, resolved decision 8)."""

import frappe
from frappe.tests import IntegrationTestCase

from helixhr.api import delete_report_view, list_report_views, run_report, save_report_view
from helixhr.tests.utils import (
	_make_role_user,
	ensure_baseline_company,
	ensure_test_company,
	make_test_hr_manager_employee,
	make_test_hr_user,
	make_test_user,
	set_report_access,
)

KEY = "leave_taken"
SUBJECT_USER = "view-subject@helixhr.test"


def _as(user, fn, *args, **kwargs):
	frappe.set_user(user)
	try:
		return fn(*args, **kwargs)
	finally:
		frappe.set_user("Administrator")


class TestReportViews(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.subject = make_test_user(SUBJECT_USER, self.company)
		_, self.hr_manager = make_test_hr_manager_employee()
		_, self.hr_user = make_test_hr_user()
		set_report_access(KEY, hr_user_run=1)
		_, self.manager_b = _make_role_user(
			"report-manager-b@helixhr.test", "HelixHR Report Manager", ensure_baseline_company()
		)

	def tearDown(self):
		frappe.set_user("Administrator")

	def _save(self, user, label, visibility="Private", query=None):
		return _as(
			user,
			save_report_view,
			KEY,
			label,
			query if query is not None else {"employee": self.subject, "group": "leave_type"},
			visibility,
		)

	def _labels(self, user):
		return {view["label"] for view in _as(user, list_report_views, KEY)}

	def test_shared_view_reaches_same_company_and_narrows_elsewhere(self):
		view = self._save(self.hr_manager, "Subject leave", "Shared")
		self.assertEqual(frappe.db.get_value("HelixHR Report View", view["name"], "company"), self.company)

		listed = {v["label"]: v for v in _as(self.hr_user, list_report_views, KEY)}
		self.assertIn("Subject leave", listed)
		self.assertFalse(listed["Subject leave"]["is_owner"])
		self.assertFalse(listed["Subject leave"]["can_delete"])
		out = _as(
			self.hr_user, run_report, KEY, filters={"employee": listed["Subject leave"]["query"]["employee"]}
		)
		self.assertEqual(out["filters_removed"], [])

		# Company B: not listed, and applying the same query never widens.
		self.assertNotIn("Subject leave", self._labels(self.manager_b))
		out = _as(self.manager_b, run_report, KEY, filters={"employee": view["query"]["employee"]})
		self.assertEqual(out["filters_removed"], ["employee"])
		self.assertEqual([row for row in out["rows"] if row["_kind"] == "row"], [])

	def test_private_view_is_invisible_and_unreadable_to_others(self):
		view = self._save(self.hr_manager, "Mine only")
		self.assertIn("Mine only", self._labels(self.hr_manager))
		self.assertNotIn("Mine only", self._labels(self.hr_user))
		doc = frappe.get_doc("HelixHR Report View", view["name"])
		self.assertFalse(frappe.has_permission("HelixHR Report View", "read", doc, user=self.hr_user))
		self.assertTrue(frappe.has_permission("HelixHR Report View", "read", doc, user=self.hr_manager))

	def test_duplicate_label_refused_and_query_validated(self):
		self._save(self.hr_user, "Dup")
		with self.assertRaises(frappe.ValidationError):
			self._save(self.hr_user, "Dup")
		for bad in ({"not_a_filter": "x"}, {"employee": ["in", ["a"]]}, {"group": "salary"}, "[]"):
			with self.assertRaises(frappe.ValidationError):
				self._save(self.hr_user, "Bad", query=bad)

	def test_only_owner_edits_and_hr_manager_may_delete_shared(self):
		view = self._save(self.hr_user, "HR user shared", "Shared")
		with self.assertRaises(frappe.PermissionError):
			_as(self.hr_manager, save_report_view, KEY, "Renamed", {}, "Shared", view["name"])
		with self.assertRaises(frappe.PermissionError):
			_as(self.manager_b, delete_report_view, view["name"])
		_as(self.hr_manager, delete_report_view, view["name"])
		self.assertFalse(frappe.db.exists("HelixHR Report View", view["name"]))

		private = self._save(self.hr_user, "Private one")
		with self.assertRaises(frappe.PermissionError):
			_as(self.hr_manager, delete_report_view, private["name"])
		_as(self.hr_user, delete_report_view, private["name"])

	def test_lost_access_hides_views_without_deleting(self):
		view = self._save(self.hr_user, "Kept")
		set_report_access(KEY, hr_user_run=0)
		with self.assertRaises(frappe.PermissionError):
			_as(self.hr_user, list_report_views, KEY)
		self.assertTrue(frappe.db.exists("HelixHR Report View", view["name"]))
