"""Plan 2026-10-14-001 U8/U9: report access decided per company, the company
filter's fall-back rule, and the deferred actions (options, exports,
background jobs, downloads, saved views) that re-check it."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from helixhr import reports
from helixhr.api import (
	download_report_export,
	get_report_catalog,
	request_export,
	run_report,
	save_report_view,
	search_report_options,
)
from helixhr.tests.utils import (
	GRANT_HOLDER_USER,
	ensure_baseline_company,
	ensure_test_company,
	make_test_company_grant,
	make_test_delivery_manager,
	make_test_hr_manager_employee,
	make_test_hr_user,
	make_test_user,
	set_report_access,
)
from helixhr.utils import resolve_report_access

WRAPPED = "leave_ledger"
DIRECTORY = "employee_directory"
PROJECT_KEY = "hours_by_project"


def _as(user, fn, *args, **kwargs):
	frappe.set_user(user)
	try:
		return fn(*args, **kwargs)
	finally:
		frappe.set_user("Administrator")


def _forwarded_company(user, key, **filters):
	with patch("frappe.core.doctype.report.report.Report.execute_module") as execute:
		execute.return_value = ([], [])
		result = _as(user, run_report, key, filters=filters)
	return execute.call_args.args[0]["company"] if execute.called else None, result


def _clear_grants():
	frappe.db.delete("HelixHR Company Access Company", {"parent": ["like", "%@helixhr.test"]})
	frappe.db.delete("HelixHR Company Access", {"user": ["like", "%@helixhr.test"]})


class _GrantReports(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.other = ensure_baseline_company()
		self.own = make_test_user(GRANT_HOLDER_USER, self.company)
		self.foreign = make_test_user("grant-report-foreign@helixhr.test", self.other)
		# The e2e fixtures commit grants; every test starts from none.
		frappe.db.delete("HelixHR Company Access Company", {"parent": ["like", "%@helixhr.test"]})
		frappe.db.delete("HelixHR Company Access", {"user": ["like", "%@helixhr.test"]})

	def tearDown(self):
		frappe.set_user("Administrator")
		_clear_grants()


class TestPerCompanyAccess(_GrantReports):
	def test_reports_only_holder_runs_every_company_report_in_the_granted_company(self):
		make_test_company_grant(GRANT_HOLDER_USER, [self.other], level="Reports only")
		for entry in reports.CATALOG:
			access = resolve_report_access(GRANT_HOLDER_USER, entry["key"])
			self.assertEqual(access["can_run"], "company" in entry["scopes"], entry["key"])
			if access["can_run"]:
				# Home is not allowed, so the default is the granted company.
				self.assertEqual(access["company"], self.other)
				self.assertEqual(access["companies"], [{"company": self.other, "can_export": False}])
				self.assertFalse(access["can_export"])
				self.assertEqual(access["tier"], "grant")
		catalog = _as(GRANT_HOLDER_USER, get_report_catalog)
		self.assertTrue(catalog)
		self.assertNotIn("Timesheet Billing Summary", {e["report"] for e in catalog})
		self.assertTrue(all(e["company"] == self.other for e in catalog))

	def test_export_flag_follows_the_grant(self):
		make_test_company_grant(GRANT_HOLDER_USER, [self.other], level="All data", allow_export=1)
		access = resolve_report_access(GRANT_HOLDER_USER, DIRECTORY)
		self.assertTrue(access["can_export"])
		self.assertEqual(access["export_scope"], {"kind": "company", "company": self.other})

	def test_delivery_manager_is_assigned_at_home_and_company_scoped_in_the_grant(self):
		"""Covers AE5."""
		_, dm = make_test_delivery_manager()
		set_report_access(PROJECT_KEY, dm_run=1, dm_export=1)
		make_test_company_grant(dm, [self.other], level="Reports only")
		home = resolve_report_access(dm, PROJECT_KEY)
		self.assertEqual((home["company"], home["scope"]["kind"]), (self.company, "assigned"))
		granted = resolve_report_access(dm, PROJECT_KEY, self.other)
		self.assertEqual(granted["scope"], {"kind": "company", "company": self.other})
		self.assertFalse(granted["company_removed"])

	def test_hr_user_matrix_denial_at_home_does_not_block_the_grant(self):
		_, hr_user = make_test_hr_user()
		frappe.db.delete("HelixHR Report Access", {"name": WRAPPED})
		make_test_company_grant(hr_user, [self.other], level="Reports only")
		access = resolve_report_access(hr_user, WRAPPED, self.company)
		self.assertTrue(access["company_removed"])
		self.assertEqual(access["company"], self.other)
		self.assertEqual([row["company"] for row in access["companies"]], [self.other])

	def test_unallowed_company_falls_back_home_with_a_notice(self):
		"""Covers AE6: never an error, never wider."""
		_, hr = make_test_hr_manager_employee()
		company, result = _forwarded_company(hr, WRAPPED, company=self.other)
		self.assertEqual(company, self.company)
		self.assertEqual(result["filters_removed"], ["company"])
		directory = _as(hr, run_report, DIRECTORY, filters={"company": self.other})
		self.assertEqual(directory["filters_removed"], ["company"])
		self.assertEqual(directory["filters"]["company"], self.company)

	def test_holder_runs_every_wrapped_report_for_the_picked_company_only(self):
		_, hr = make_test_hr_manager_employee()
		make_test_company_grant(hr, [self.other])
		for key in [e["key"] for e in reports.CATALOG if e["engine"] == "frappe"]:
			company, result = _forwarded_company(hr, key, company=self.other)
			self.assertEqual(company, self.other, key)
			self.assertEqual(result["filters_removed"], [], key)
			self.assertEqual(_forwarded_company(hr, key)[0], self.company, key)

	def test_an_employee_from_another_company_is_removed_not_widened(self):
		make_test_company_grant(GRANT_HOLDER_USER, [self.other], level="Reports only")
		company, result = _forwarded_company(
			GRANT_HOLDER_USER, WRAPPED, company=self.other, employee=self.own
		)
		self.assertIsNone(company)
		self.assertEqual(result["filters_removed"], ["employee"])

	def test_administrator_picks_any_company(self):
		company, result = _forwarded_company("Administrator", WRAPPED, company=self.other)
		self.assertEqual(company, self.other)
		self.assertEqual(result["filters_removed"], [])

	def test_options_follow_the_picked_company(self):
		make_test_company_grant(GRANT_HOLDER_USER, [self.company, self.other], level="Reports only")

		def values(company):
			return {
				o["value"]
				for o in _as(
					GRANT_HOLDER_USER,
					search_report_options,
					WRAPPED,
					"employee",
					query="grant",
					context={"company": company},
				)
			}

		self.assertIn(self.foreign, values(self.other))
		self.assertNotIn(self.own, values(self.other))
		self.assertIn(self.own, values(self.company))
		self.assertEqual(values("_No Such Company"), set())


class TestPerCompanyExports(_GrantReports):
	def setUp(self):
		super().setUp()
		_, self.hr = make_test_hr_manager_employee()
		if not getattr(frappe.local, "response_headers", None):
			from werkzeug.datastructures import Headers

			frappe.local.response_headers = Headers()

	def tearDown(self):
		super().tearDown()
		frappe.local.response = frappe._dict({"docs": []})

	def test_fingerprint_and_log_carry_the_company(self):
		make_test_company_grant(self.hr, [self.other], allow_export=1)
		home = _as(self.hr, request_export, DIRECTORY, "csv", filters={"status": "Active"})
		granted = _as(
			self.hr, request_export, DIRECTORY, "csv", filters={"status": "Active", "company": self.other}
		)
		rows = {
			name: frappe.db.get_value("HelixHR Report Export", name, ["company", "filters_hash"], as_dict=True)
			for name in (home["export"], granted["export"])
		}
		self.assertEqual(rows[home["export"]].company, self.company)
		self.assertEqual(rows[granted["export"]].company, self.other)
		self.assertNotEqual(rows[home["export"]].filters_hash, rows[granted["export"]].filters_hash)

	def test_export_of_a_company_without_export_right_is_refused(self):
		make_test_company_grant(self.hr, [self.other], allow_export=0)
		with self.assertRaises(frappe.PermissionError):
			_as(self.hr, request_export, DIRECTORY, "csv", filters={"company": self.other})
		with self.assertRaises(frappe.PermissionError):
			_as(self.hr, request_export, DIRECTORY, "csv", filters={"company": "_No Such Company"})

	def test_shared_view_stays_in_the_home_company(self):
		make_test_company_grant(GRANT_HOLDER_USER, [self.other], level="Reports only")
		view = _as(
			GRANT_HOLDER_USER,
			save_report_view,
			DIRECTORY,
			"Outsourcing roster",
			{"company": self.other},
			"Shared",
		)
		self.assertEqual(view["query"], {"company": self.other})
		self.assertEqual(frappe.db.get_value("HelixHR Report View", view["name"], "company"), self.company)


class TestRevokedExports(IntegrationTestCase):
	"""AE7 and R15. The job commits, so the grant and every row it makes are
	committed and deleted explicitly (runbook isolation notes)."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.other = ensure_baseline_company()
		make_test_user(GRANT_HOLDER_USER, self.company)
		# At least one row in the export, or it is built inline (a fresh site
		# has nobody in the second company).
		make_test_user("grant-report-foreign@helixhr.test", self.other)
		make_test_company_grant(GRANT_HOLDER_USER, [self.other], level="Reports only", allow_export=1)
		frappe.db.commit()  # nosemgrep -- the job under test commits and rolls back
		self.addCleanup(self._clear)
		if not getattr(frappe.local, "response_headers", None):
			from werkzeug.datastructures import Headers

			frappe.local.response_headers = Headers()
		caps = patch.dict(reports.INLINE_EXPORT_CAP, {"csv": 0, "xlsx": 0, "pdf": 0})
		caps.start()
		self.addCleanup(caps.stop)

	def _clear(self):
		frappe.set_user("Administrator")
		frappe.local.response = frappe._dict({"docs": []})
		for name in frappe.get_all("HelixHR Report Export", {"owner": GRANT_HOLDER_USER}, pluck="name"):
			for file in frappe.get_all(
				"File", {"attached_to_doctype": "HelixHR Report Export", "attached_to_name": name}, pluck="name"
			):
				frappe.delete_doc("File", file, ignore_permissions=True, force=True)
			frappe.db.delete("Notification Log", {"document_type": "HelixHR Report Export", "document_name": name})
			frappe.db.delete("HelixHR Report Export", {"name": name})
		_clear_grants()
		frappe.db.commit()  # nosemgrep

	def _queue(self):
		with patch("frappe.enqueue") as enqueue:
			out = _as(GRANT_HOLDER_USER, request_export, DIRECTORY, "csv", filters={"company": self.other})
		kwargs = dict(enqueue.call_args.kwargs)
		for key in ("queue", "job_id", "deduplicate", "enqueue_after_commit"):
			kwargs.pop(key)
		return out["export"], kwargs

	def _revoke(self):
		from helixhr.utils import disable_company_access

		disable_company_access(GRANT_HOLDER_USER, "test")
		frappe.db.commit()  # nosemgrep

	def test_revoked_before_the_job_runs_ends_failed_with_no_file(self):
		export, kwargs = self._queue()
		self._revoke()
		reports.run_background_export(**kwargs)
		row = frappe.db.get_value("HelixHR Report Export", export, ["status", "file"], as_dict=True)
		self.assertEqual((row.status, row.file), ("Failed", None))
		self.assertFalse(frappe.db.exists("File", {"attached_to_name": export}))

	def test_revoking_expires_a_ready_file_and_refuses_its_download(self):
		export, kwargs = self._queue()
		reports.run_background_export(**kwargs)
		self.assertEqual(frappe.db.get_value("HelixHR Report Export", export, "company"), self.other)
		self.assertEqual(frappe.db.get_value("HelixHR Report Export", export, "status"), "Ready")
		self._revoke()
		self.assertEqual(frappe.db.get_value("HelixHR Report Export", export, "status"), "Expired")
		self.assertFalse(frappe.db.exists("File", {"attached_to_name": export}))
		with self.assertRaises(frappe.PermissionError):
			_as(GRANT_HOLDER_USER, download_report_export, export)

	def test_download_re_checks_the_company(self):
		export, kwargs = self._queue()
		reports.run_background_export(**kwargs)
		# Narrowed without the endpoint's clean-up: the download still refuses.
		frappe.db.set_value("HelixHR Company Access", GRANT_HOLDER_USER, "allow_export", 0)
		with self.assertRaises(frappe.PermissionError):
			_as(GRANT_HOLDER_USER, download_report_export, export)
