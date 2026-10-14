"""Plan 2026-10-14-001 U4-U7: what an All-data or Reports-only company access
grant opens -- the session flags, People and photos, the Organisation view
and Projects -- and that every write next to those reads still refuses."""

import frappe
from frappe.tests import IntegrationTestCase

from helixhr.api import (
	_may_see_photo,
	create_project,
	get_organisation_view,
	get_person,
	get_person_form_options,
	get_portal_bootstrap,
	get_project,
	save_person,
	save_task,
	search_people,
	search_projects,
)
from helixhr.tests.utils import (
	GRANT_HOLDER_USER,
	ensure_baseline_company,
	ensure_test_company,
	make_test_company_grant,
	make_test_delivery_manager,
	make_test_hr_manager_employee,
	make_test_project,
	make_test_user,
)

FOREIGN_USER = "grant-surface-foreign@helixhr.test"


def _as(user, fn, *args, **kwargs):
	frappe.set_user(user)
	try:
		return fn(*args, **kwargs)
	finally:
		frappe.set_user("Administrator")


class _Surfaces(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.other = ensure_baseline_company()
		self.own = make_test_user(GRANT_HOLDER_USER, self.company)
		self.foreign = make_test_user(FOREIGN_USER, self.other, employee_number="GRANT-SURFACE-B1")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.delete("HelixHR Company Access Company", {"parent": ["like", "%@helixhr.test"]})
		frappe.db.delete("HelixHR Company Access", {"user": ["like", "%@helixhr.test"]})


class TestSessionFlags(_Surfaces):
	FLAGS = ("can_see_people", "can_see_organisation", "can_see_projects", "can_run_reports", "can_roster_everyone")

	def _flags(self, user):
		boot = _as(user, get_portal_bootstrap)
		return {flag: boot[flag] for flag in self.FLAGS}

	def test_all_data_holder_sees_read_surfaces_but_not_the_roster(self):
		make_test_company_grant(GRANT_HOLDER_USER, [self.company, self.other])
		self.assertEqual(
			self._flags(GRANT_HOLDER_USER),
			{
				"can_see_people": True,
				"can_see_organisation": True,
				"can_see_projects": True,
				"can_run_reports": True,
				"can_roster_everyone": False,
			},
		)

	def test_reports_only_holder_sees_reports_alone(self):
		make_test_company_grant(GRANT_HOLDER_USER, [self.other], level="Reports only")
		flags = self._flags(GRANT_HOLDER_USER)
		self.assertTrue(flags.pop("can_run_reports"))
		self.assertFalse(any(flags.values()), flags)

	def test_flags_drop_after_revoke_and_hr_is_unchanged(self):
		make_test_company_grant(GRANT_HOLDER_USER, [self.other], enabled=0)
		self.assertFalse(any(self._flags(GRANT_HOLDER_USER).values()))
		_, hr = make_test_hr_manager_employee()
		flags = self._flags(hr)
		self.assertTrue(flags["can_see_people"] and flags["can_roster_everyone"])


class TestPeople(_Surfaces):
	def test_holder_reads_a_granted_employee_without_edit_controls(self):
		"""Covers AE4: under strict user permissions with a self User
		Permission, the sections still populate."""
		make_test_company_grant(GRANT_HOLDER_USER, [self.other])
		frappe.db.set_single_value("System Settings", "apply_strict_user_permissions", 1)
		today = frappe.utils.today()
		frappe.db.bulk_insert(
			"Attendance",
			["name", "employee", "employee_name", "attendance_date", "status", "company", "docstatus"],
			[("_T Grant AE4", self.foreign, FOREIGN_USER, today, "Present", self.other, 1)],
			ignore_duplicates=True,
		)
		person = _as(GRANT_HOLDER_USER, get_person, self.foreign)
		self.assertEqual(person["failed_sections"], [])
		self.assertFalse(person["can_edit"])
		self.assertIsNone(person["desk_url"])
		self.assertEqual(person["attendance"]["days"][today]["status"], "Present")
		self.assertIsNotNone(person["requests"])

	def test_search_finds_the_granted_company_only_for_the_holder(self):
		make_test_company_grant(GRANT_HOLDER_USER, [self.company, self.other])
		found = _as(GRANT_HOLDER_USER, search_people, "GRANT-SURFACE-B1")["people"]
		self.assertEqual([(p["name"], p["company"]) for p in found], [(self.foreign, self.other)])
		_, hr = make_test_hr_manager_employee()
		self.assertEqual(_as(hr, search_people, "GRANT-SURFACE-B1")["people"], [])

	def test_hr_manager_holder_reads_but_cannot_edit_the_granted_company(self):
		_, hr = make_test_hr_manager_employee()
		make_test_company_grant(hr, [self.other])
		self.assertFalse(_as(hr, get_person, self.foreign)["can_edit"])
		self.assertTrue(_as(hr, get_person, self.own)["can_edit"])
		with self.assertRaises(frappe.PermissionError):
			_as(hr, save_person, self.foreign, designation=None)
		with self.assertRaises(frappe.PermissionError):
			_as(hr, get_person_form_options, self.foreign)

	def test_photo_follows_the_read_scope(self):
		_, hr = make_test_hr_manager_employee()
		self.assertFalse(_as(hr, _may_see_photo, self.foreign))
		make_test_company_grant(hr, [self.other])
		self.assertTrue(_as(hr, _may_see_photo, self.foreign))

	def test_reports_only_holder_cannot_search_people(self):
		make_test_company_grant(GRANT_HOLDER_USER, [self.other], level="Reports only")
		with self.assertRaises(frappe.PermissionError):
			_as(GRANT_HOLDER_USER, search_people, "GRANT")
		with self.assertRaises(frappe.PermissionError):
			_as(GRANT_HOLDER_USER, get_person, self.foreign)


class TestOrganisation(_Surfaces):
	def test_holder_picks_an_allowed_company(self):
		make_test_company_grant(GRANT_HOLDER_USER, [self.company, self.other])
		view = _as(GRANT_HOLDER_USER, get_organisation_view, self.other)
		self.assertEqual(view["company"], self.other)
		self.assertEqual(view["companies"], [self.company, self.other])
		self.assertEqual(
			view["headcount"], frappe.db.count("Employee", {"status": "Active", "company": self.other})
		)
		self.assertEqual(_as(GRANT_HOLDER_USER, get_organisation_view)["company"], self.company)
		with self.assertRaises(frappe.PermissionError):
			_as(GRANT_HOLDER_USER, get_organisation_view, "_No Such Company")

	def test_default_is_the_first_allowed_when_home_is_not_granted(self):
		make_test_company_grant(GRANT_HOLDER_USER, [self.other])
		self.assertEqual(_as(GRANT_HOLDER_USER, get_organisation_view)["company"], self.other)

	def test_hr_without_a_grant_stays_on_home(self):
		_, hr = make_test_hr_manager_employee()
		view = _as(hr, get_organisation_view)
		self.assertEqual((view["company"], view["companies"]), (self.company, [self.company]))
		with self.assertRaises(frappe.PermissionError):
			_as(hr, get_organisation_view, self.other)


class TestProjects(_Surfaces):
	def test_delivery_manager_holder_reads_granted_projects_but_edits_only_their_own(self):
		_, dm = make_test_delivery_manager()
		mine = make_test_project(self.company, "_T Grant surface DM own", members=(dm,))
		theirs = make_test_project(self.other, "_T Grant surface B")
		make_test_company_grant(dm, [self.other])

		listed = {row["name"]: row for row in _as(dm, search_projects)["projects"]}
		self.assertTrue(listed[mine]["can_edit"])
		self.assertFalse(listed[theirs]["can_edit"])
		self.assertTrue(_as(dm, get_project, mine)["can_edit"])
		self.assertFalse(_as(dm, get_project, theirs)["can_edit"])
		with self.assertRaises(frappe.PermissionError):
			_as(dm, save_task, theirs, subject="Should not land")
		# The REST route is untouched: the grant opens no document read.
		from frappe.client import get

		with self.assertRaises(frappe.PermissionError):
			_as(dm, get, "Project", theirs)

	def test_reader_without_a_project_role_cannot_create(self):
		theirs = make_test_project(self.other, "_T Grant surface reader B")
		make_test_company_grant(GRANT_HOLDER_USER, [self.other])
		out = _as(GRANT_HOLDER_USER, search_projects)
		self.assertIn(theirs, [row["name"] for row in out["projects"]])
		self.assertFalse(out["can_create"])
		with self.assertRaises(frappe.PermissionError):
			_as(GRANT_HOLDER_USER, create_project, "Should not land")

	def test_non_holder_gets_the_missing_project_refusal(self):
		theirs = make_test_project(self.other, "_T Grant surface hidden B")
		_, hr = make_test_hr_manager_employee()
		with self.assertRaises(frappe.PermissionError) as hidden:
			_as(hr, get_project, theirs)
		with self.assertRaises(frappe.PermissionError) as missing:
			_as(hr, get_project, "PROJ-DOES-NOT-EXIST")
		self.assertEqual(str(hidden.exception), str(missing.exception))
