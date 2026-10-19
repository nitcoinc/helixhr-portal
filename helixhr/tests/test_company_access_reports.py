"""Plan 2026-10-14-001 U8/U9: report access decided per company, the company
filter's fall-back rule, and the deferred actions (options, exports,
background jobs, downloads, saved views) that re-check it."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from helixhr import reports
from helixhr.api import (
	download_report_export,
	get_export_log,
	get_report_catalog,
	request_export,
	run_report,
	save_report_view,
	search_report_options,
)
from helixhr.tests.utils import (
	GRANT_HOLDER_USER,
	clear_test_company_grants,
	ensure_baseline_company,
	ensure_test_company,
	make_assignment,
	make_test_company_grant,
	make_test_delivery_manager,
	make_test_hr_manager_employee,
	make_test_hr_user,
	make_test_user,
	remove_assignment,
	set_report_access,
)
from helixhr.utils import (
	ORG_VIEWER_ROLE,
	PORTAL_ADMIN_ROLE,
	REPORT_MANAGER_ROLE,
	SENTINEL_ALL,
	clear_role_scope_cache,
	resolve_report_access,
)

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
	clear_test_company_grants()


def _turn_export_off(user, role):
	frappe.db.set_value("HelixHR Access Assignment", {"user": user, "role": role}, "allow_export", 0)
	clear_role_scope_cache(user)


class _GrantReports(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.other = ensure_baseline_company()
		self.own = make_test_user(GRANT_HOLDER_USER, self.company)
		self.foreign = make_test_user("grant-report-foreign@helixhr.test", self.other)
		# The e2e fixtures commit grants; every test starts from none.
		clear_test_company_grants()

	def tearDown(self):
		frappe.set_user("Administrator")
		_clear_grants()


class TestPerCompanyAccess(_GrantReports):
	def test_reports_only_holder_runs_every_catalog_report_in_the_granted_company(self):
		"""Plan 2026-10-19-001 U4: a "Reports only" grant migrates to Reports
		rows, which run every catalog report in their orgs -- the Report
		Manager's reach, not the grant's company-scoped subset."""
		make_test_company_grant(GRANT_HOLDER_USER, [self.other], level="Reports only")
		for entry in reports.CATALOG:
			access = resolve_report_access(GRANT_HOLDER_USER, entry["key"])
			self.assertTrue(access["can_run"], entry["key"])
			# Home is not allowed, so the default is the granted company.
			self.assertEqual(access["company"], self.other)
			self.assertEqual(access["companies"], [{"company": self.other, "can_export": False}])
			self.assertFalse(access["can_export"])
			self.assertEqual(access["tier"], "report_manager")
		catalog = _as(GRANT_HOLDER_USER, get_report_catalog)
		self.assertEqual(len(catalog), len(reports.CATALOG))
		self.assertTrue(all(e["company"] == self.other for e in catalog))

	def test_export_flag_follows_the_grant(self):
		make_test_company_grant(GRANT_HOLDER_USER, [self.other], level="All data", allow_export=1)
		access = resolve_report_access(GRANT_HOLDER_USER, DIRECTORY)
		self.assertTrue(access["can_export"])
		self.assertEqual(access["export_scope"], {"kind": "company", "company": self.other})

	def test_delivery_manager_is_company_scoped_in_their_org_and_in_the_grant(self):
		"""Covers AE5. Plan 2026-10-19-001 U4: the Delivery Manager tier is a
		company tier at their org, no longer their assigned projects."""
		_, dm = make_test_delivery_manager()
		set_report_access(PROJECT_KEY, dm_run=1, dm_export=1)
		make_test_company_grant(dm, [self.other], level="Reports only")
		home = resolve_report_access(dm, PROJECT_KEY)
		# R2: their org's projects with every hour logged on them.
		self.assertEqual(
			(home["company"], home["tier"], home["scope"]),
			(
				self.company,
				"delivery_manager",
				{"kind": "company", "company": self.company, "project_companies": [self.company]},
			),
		)
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
		"""Remove the Reports rows and expire what they no longer reach -- what
		the People & access save does (plan 2026-10-19-001 U5)."""
		from helixhr.helixhr.doctype.helixhr_report_export.helixhr_report_export import (
			expire_unreachable_exports,
		)

		remove_assignment(GRANT_HOLDER_USER, REPORT_MANAGER_ROLE)
		expire_unreachable_exports(GRANT_HOLDER_USER)
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

	def test_a_narrowing_save_expires_the_ready_file_once_it_commits(self):
		"""Plan 2026-10-19-001 U5: the People & access save expires what the
		person no longer reaches -- after commit, through the callback the
		save registers, not by hand."""
		from helixhr.api import access_token, save_person_access

		export, kwargs = self._queue()
		reports.run_background_export(**kwargs)
		self.assertEqual(frappe.db.get_value("HelixHR Report Export", export, "status"), "Ready")

		save_person_access(GRANT_HOLDER_USER, [], access_token(GRANT_HOLDER_USER))
		# Saved, not yet committed: the file is still there.
		self.assertEqual(frappe.db.get_value("HelixHR Report Export", export, "status"), "Ready")
		frappe.db.commit()  # nosemgrep -- runs the save's after-commit callbacks
		self.assertEqual(frappe.db.get_value("HelixHR Report Export", export, "status"), "Expired")
		self.assertFalse(frappe.db.exists("File", {"attached_to_name": export}))
		# The Reports row came from this grant: it is off too (rollback safety).
		self.assertEqual(frappe.db.get_value("HelixHR Company Access", GRANT_HOLDER_USER, "enabled"), 0)

	def test_download_re_checks_the_company(self):
		export, kwargs = self._queue()
		reports.run_background_export(**kwargs)
		# Narrowed without the endpoint's clean-up: the download still refuses.
		_turn_export_off(GRANT_HOLDER_USER, REPORT_MANAGER_ROLE)
		with self.assertRaises(frappe.PermissionError):
			_as(GRANT_HOLDER_USER, download_report_export, export)


class TestExportLogReach(_GrantReports):
	"""Plan 2026-10-14-001 U9/KTD12. The export row records the picked
	(data) company and the exporter's home company; the log lists a row
	when either is in the Portal Admin's reach."""

	def _grant_holder_export(self):
		make_test_company_grant(GRANT_HOLDER_USER, [self.other], level="Reports only", allow_export=1)
		return _as(
			GRANT_HOLDER_USER, request_export, DIRECTORY, "csv", filters={"company": self.other}
		)["export"]

	def _make_portal_admin(self, user, company):
		make_test_user(user, company)
		make_assignment(user, PORTAL_ADMIN_ROLE, [company])
		return user

	def test_row_records_data_company_and_exporter_company(self):
		name = self._grant_holder_export()
		row = frappe.db.get_value(
			"HelixHR Report Export", name, ["company", "exporter_company"], as_dict=True
		)
		self.assertEqual((row.company, row.exporter_company), (self.other, self.company))

	def test_fingerprint_covers_the_company(self):
		from helixhr.api import _export_fingerprint

		home_hash = _export_fingerprint(DIRECTORY, "csv", {"company": self.company}, [], None, None)
		other_hash = _export_fingerprint(DIRECTORY, "csv", {"company": self.other}, [], None, None)
		self.assertNotEqual(home_hash, other_hash)

	def test_log_lists_a_row_on_either_side_of_the_reach(self):
		name = self._grant_holder_export()
		admin_a = self._make_portal_admin("grant-admin-a@helixhr.test", self.company)
		admin_b = self._make_portal_admin("grant-admin-b@helixhr.test", self.other)
		# Exporter side: an admin reaching only the holder's home company.
		rows_a = _as(admin_a, get_export_log, page_length=200)["rows"]
		seen = next(row for row in rows_a if row.name == name)
		self.assertEqual(seen.exporter_company, self.company)
		# Data side: an admin reaching only the exported company.
		names_b = [row.name for row in _as(admin_b, get_export_log, page_length=200)["rows"]]
		self.assertIn(name, names_b)

	def test_log_hides_a_row_on_neither_side(self):
		foreign_user = "grant-report-foreign@helixhr.test"
		make_assignment(foreign_user, "HR User", [self.other])
		set_report_access(DIRECTORY, hr_user_run=1, hr_user_export=1)
		other_only = _as(foreign_user, request_export, DIRECTORY, "csv")["export"]
		admin_a = self._make_portal_admin("grant-admin-a@helixhr.test", self.company)
		names = [row.name for row in _as(admin_a, get_export_log, page_length=200)["rows"]]
		self.assertNotIn(other_only, names)

	def test_backfill_patch_fills_and_is_idempotent(self):
		from helixhr.patches.v1_0 import backfill_export_exporter_company

		name = self._grant_holder_export()
		frappe.db.set_value("HelixHR Report Export", name, "exporter_company", None)
		backfill_export_exporter_company.execute()
		self.assertEqual(
			frappe.db.get_value("HelixHR Report Export", name, "exporter_company"), self.company
		)
		backfill_export_exporter_company.execute()
		self.assertEqual(
			frappe.db.get_value("HelixHR Report Export", name, "exporter_company"), self.company
		)

# --- Plan 2026-10-15-001: combined company scope (U1) -----------------------


class TestCombinedCompanyScope(_GrantReports):
	"""Plan 2026-10-15-001 KTD1/KTD3: the sentinel resolves to a combined
	scope across all run-capable companies for project-scoped entries."""

	def setUp(self):
		super().setUp()
		# Make the grant holder scoped to both companies with export.
		make_test_company_grant(GRANT_HOLDER_USER, [self.company, self.other], level="All data", allow_export=1)

	def test_combined_scope_default_when_exportable(self):
		"""R1/R2: holder with export in both gets combined as default."""
		access = resolve_report_access(GRANT_HOLDER_USER, PROJECT_KEY)
		self.assertTrue(access["combined_option"])
		self.assertTrue(access["combined_exportable"])
		self.assertEqual(access["company"], SENTINEL_ALL)
		self.assertEqual(access["scope"]["kind"], "combined")
		self.assertIn(self.company, access["scope"]["companies"])
		self.assertIn(self.other, access["scope"]["companies"])
		self.assertTrue(access["can_export"])

	def test_defaults_to_home_when_not_combined_exportable(self):
		"""R2: Reports-only grant without export defaults to home."""
		_clear_grants()
		make_test_company_grant(GRANT_HOLDER_USER, [self.company, self.other], level="Reports only", allow_export=0)
		access = resolve_report_access(GRANT_HOLDER_USER, PROJECT_KEY)
		self.assertTrue(access["combined_option"])
		self.assertFalse(access["combined_exportable"])
		self.assertEqual(access["company"], self.company)
		self.assertIn(self.other, access["blocking_companies"])

	def test_dm_with_outsourcing_grant_combined_scope(self):
		"""DM with Outsourcing-only grant: combined over both companies, each
		company-wide (plan 2026-10-19-001 U4: no assigned-projects part)."""
		_clear_grants()
		_, dm = make_test_delivery_manager()
		set_report_access(PROJECT_KEY, dm_run=1, dm_export=1)
		make_test_company_grant(dm, [self.other], level="Reports only")
		access = resolve_report_access(dm, PROJECT_KEY, SENTINEL_ALL)
		self.assertEqual(access["company"], SENTINEL_ALL)
		self.assertEqual(access["scope"]["companies"], sorted([self.company, self.other]))
		self.assertIsNone(access["scope"].get("user"))

	def test_single_company_sentinel_falls_back(self):
		"""KTD3: single-company HR passing __all falls back with company_removed."""
		_clear_grants()
		_, hr = make_test_hr_manager_employee()
		access = resolve_report_access(hr, PROJECT_KEY, SENTINEL_ALL)
		self.assertFalse(access["combined_option"])
		self.assertTrue(access["company_removed"])
		self.assertEqual(access["company"], self.company)

	def test_non_project_report_rejects_sentinel(self):
		"""KTD3: sentinel on non-project report falls back."""
		access = resolve_report_access(GRANT_HOLDER_USER, DIRECTORY)
		self.assertFalse(access["combined_option"])

	def test_revoked_grant_resolves_to_home_when_user_has_other_tier(self):
		"""Error: revoked grant resolves to home only for a user with another
		tier. A grant-only holder loses all access on revocation. Since plan
		2026-10-19-001 U4 revoking is removing the Org viewer rows."""
		# Create a DM who also has a grant.
		_, dm = make_test_delivery_manager()
		set_report_access(PROJECT_KEY, dm_run=1, dm_export=1)
		make_test_company_grant(dm, [self.company, self.other], level="All data", allow_export=1)

		# Revoke the grant; DM still has their Delivery Manager org.
		remove_assignment(dm, ORG_VIEWER_ROLE)
		access = resolve_report_access(dm, PROJECT_KEY)
		self.assertEqual(access["company"], self.company)
		self.assertFalse(access["combined_option"])
		self.assertEqual(access["tier"], "delivery_manager")

	def test_hr_user_run_without_export_blocks_combined_export_at_home(self):
		"""KTD2: an HR User who may run but not export Inc: combined export
		is blocked, naming Inc."""
		_clear_grants()
		_, hr_user = make_test_hr_user()
		set_report_access(PROJECT_KEY, hr_user_run=1, hr_user_export=0, dm_run=1, dm_export=1)
		make_test_company_grant(hr_user, [self.other], level="Reports only", allow_export=1)
		access = resolve_report_access(hr_user, PROJECT_KEY, SENTINEL_ALL)
		self.assertEqual(access["company"], SENTINEL_ALL)
		self.assertFalse(access["can_export"])
		self.assertEqual(access["blocking_companies"], [self.company])
		self.assertEqual(access["export_scope"], {"kind": "none"})
		# The default stays home (R2).
		self.assertEqual(resolve_report_access(hr_user, PROJECT_KEY)["company"], self.company)

	def test_unscoped_hr_gets_the_option_and_the_concrete_company_list(self):
		"""R1: unscoped HR counts; exports store every company."""
		from helixhr.tests.utils import ensure_hr_manager_user

		hr = ensure_hr_manager_user()
		access = resolve_report_access(hr, PROJECT_KEY, SENTINEL_ALL)
		self.assertEqual(access["scope"], {"kind": "unscoped", "combined": True})
		self.assertEqual(access["combined_companies"], sorted(frappe.get_all("Company", pluck="name")))

	def test_dm_combined_scope_covers_their_org_company_wide(self):
		"""Plan 2026-10-19-001 U4: the Delivery Manager org joins the combined
		set company-wide; no assigned-projects part is left."""
		_clear_grants()
		_, dm = make_test_delivery_manager()
		set_report_access(PROJECT_KEY, dm_run=1, dm_export=1)
		make_test_company_grant(dm, [self.other], level="Reports only", allow_export=1)
		access = resolve_report_access(dm, PROJECT_KEY, SENTINEL_ALL)
		self.assertEqual(
			access["scope"],
			{
				"kind": "combined",
				"combined": True,
				"companies": sorted([self.company, self.other]),
				# Only the Delivery Manager org counts other orgs' hours on
				# its projects; the grant's org is its own staff's.
				"project_companies": [self.company],
			},
		)
		self.assertNotIn("assigned_companies", access)
		self.assertTrue(access["can_export"])


class TestCombinedExports(IntegrationTestCase):
	"""Plan 2026-10-15-001 U3/R5/R7. The background job commits, so rows are
	committed and deleted explicitly, like `TestRevokedExports`."""

	def setUp(self):
		from helixhr.tests.utils import ensure_cross_company_project_fixture

		frappe.set_user("Administrator")
		self.fx = ensure_cross_company_project_fixture()
		self.company, self.other = self.fx.company, self.fx.other
		make_test_user(GRANT_HOLDER_USER, self.company)
		_clear_grants()
		make_test_company_grant(GRANT_HOLDER_USER, [self.company, self.other], allow_export=1)
		frappe.db.commit()  # nosemgrep -- the job under test commits and rolls back
		self.addCleanup(self._clear)
		if not getattr(frappe.local, "response_headers", None):
			from werkzeug.datastructures import Headers

			frappe.local.response_headers = Headers()
		self.filters = {"project": self.fx.project, "month": "2016-04", "company": SENTINEL_ALL}

	def _clear(self):
		frappe.set_user("Administrator")
		frappe.local.response = frappe._dict({"docs": []})
		for name in frappe.get_all("HelixHR Report Export", {"owner": GRANT_HOLDER_USER}, pluck="name"):
			for file in frappe.get_all(
				"File", {"attached_to_doctype": "HelixHR Report Export", "attached_to_name": name}, pluck="name"
			):
				frappe.delete_doc("File", file, ignore_permissions=True, force=True)
			frappe.db.delete("Notification Log", {"document_type": "HelixHR Report Export", "document_name": name})
			frappe.db.delete("HelixHR Report Export Company", {"parent": name})
			frappe.db.delete("HelixHR Report Export", {"name": name})
		_clear_grants()
		frappe.db.commit()  # nosemgrep

	def _stored(self, export):
		from helixhr.utils import stored_export_companies

		return sorted(stored_export_companies(export))

	def _queue(self):
		caps = patch.dict(reports.INLINE_EXPORT_CAP, {"csv": 0, "xlsx": 0, "pdf": 0})
		with caps, patch("frappe.enqueue") as enqueue:
			out = _as(GRANT_HOLDER_USER, request_export, "project_timesheet", "csv", filters=self.filters)
		kwargs = dict(enqueue.call_args.kwargs)
		for key in ("queue", "job_id", "deduplicate", "enqueue_after_commit"):
			kwargs.pop(key)
		frappe.db.commit()  # nosemgrep
		return out["export"], kwargs

	def _assert_ready(self, export):
		row = frappe.db.get_value("HelixHR Report Export", export, ["status", "error"], as_dict=True)
		self.assertEqual(row.status, "Ready", row.error)

	def _narrow_export(self):
		_turn_export_off(GRANT_HOLDER_USER, ORG_VIEWER_ROLE)
		frappe.db.commit()  # nosemgrep

	def test_inline_combined_export_stores_both_companies_and_the_home_company(self):
		out = _as(GRANT_HOLDER_USER, request_export, "project_timesheet", "csv", filters=self.filters)
		self.assertEqual(self._stored(out["export"]), sorted([self.company, self.other]))
		self.assertEqual(frappe.db.get_value("HelixHR Report Export", out["export"], "company"), self.company)

	def test_combined_export_is_refused_when_not_exportable_in_full(self):
		self._narrow_export()
		with self.assertRaises(frappe.PermissionError):
			_as(GRANT_HOLDER_USER, request_export, "project_timesheet", "csv", filters=self.filters)

	def test_background_job_refuses_after_export_is_removed(self):
		export, kwargs = self._queue()
		self._narrow_export()
		reports.run_background_export(**kwargs)
		self.assertEqual(frappe.db.get_value("HelixHR Report Export", export, "status"), "Failed")

	def test_ready_file_is_refused_and_expired_after_export_is_removed(self):
		from helixhr.helixhr.doctype.helixhr_report_export.helixhr_report_export import (
			expire_unreachable_exports,
		)

		export, kwargs = self._queue()
		reports.run_background_export(**kwargs)
		self._assert_ready(export)
		self._narrow_export()
		with self.assertRaises(frappe.PermissionError):
			_as(GRANT_HOLDER_USER, download_report_export, export)
		expire_unreachable_exports(GRANT_HOLDER_USER)
		self.assertEqual(frappe.db.get_value("HelixHR Report Export", export, "status"), "Expired")

	def test_a_grant_widened_before_the_job_runs_does_not_widen_the_file(self):
		from helixhr.tests.utils import COMPANY_ACCESS_THIRD_COMPANY, create_test_company

		export, kwargs = self._queue()
		third = create_test_company(COMPANY_ACCESS_THIRD_COMPANY, "TCAC")
		make_test_company_grant(GRANT_HOLDER_USER, [self.company, self.other, third], allow_export=1)
		frappe.db.commit()  # nosemgrep
		seen = []
		real_run = reports.run

		def recording_run(key, scope, *args, **kwargs):
			seen.append(scope)
			return real_run(key, scope, *args, **kwargs)

		with patch.object(reports, "run", side_effect=recording_run):
			reports.run_background_export(**kwargs)
		self._assert_ready(export)
		self.assertEqual(seen[0]["companies"], sorted([self.company, self.other]))

	def test_an_empty_stored_set_is_refused_never_re_resolved(self):
		export, kwargs = self._queue()
		frappe.db.delete("HelixHR Report Export Company", {"parent": export})
		frappe.db.commit()  # nosemgrep
		reports.run_background_export(**kwargs)
		self.assertEqual(frappe.db.get_value("HelixHR Report Export", export, "status"), "Failed")

	def test_log_shows_a_combined_export_to_an_admin_reaching_only_a_stored_company(self):
		out = _as(GRANT_HOLDER_USER, request_export, "project_timesheet", "csv", filters=self.filters)
		admin = "xco-admin-b@helixhr.test"
		make_test_user(admin, self.other)
		make_assignment(admin, PORTAL_ADMIN_ROLE, [self.other])
		names = [row.name for row in _as(admin, get_export_log, page_length=200)["rows"]]
		self.assertIn(out["export"], names)
		# A single-company export of the holder's home company stays hidden.
		home = _as(
			GRANT_HOLDER_USER,
			request_export,
			"project_timesheet",
			"csv",
			filters={**self.filters, "company": self.company},
		)
		names = [row.name for row in _as(admin, get_export_log, page_length=200)["rows"]]
		self.assertNotIn(home["export"], names)
