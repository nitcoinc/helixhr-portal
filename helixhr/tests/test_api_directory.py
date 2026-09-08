import frappe
from frappe.tests import IntegrationTestCase

from helixhr.api import _DIRECTORY_MAX_PAGE, _DIRECTORY_PAGE, get_directory
from helixhr.tests.utils import (
	DIRECTORY_COLLEAGUE_EMAIL,
	DIRECTORY_MANAGER_EMAIL,
	EMPLOYEE_USER,
	ensure_directory_fixtures,
)

# What one person in the payload is allowed to be, and nothing else (P3-R23).
# `email` is the only contact key, and it is only there when HR published one.
ALLOWED_KEYS = {
	"name",
	"employee_name",
	"designation",
	"department",
	"manager",
	"manager_name",
	"email",
}

# Fields that exist on Employee and must never reach the browser: a login
# identifier, a personal address, a phone number, a photo, a birthday.
FORBIDDEN_KEYS = (
	"user_id",
	"company_email",
	"personal_email",
	"cell_number",
	"image",
	"date_of_birth",
	"date_of_joining",
	"company",
	"status",
)


class DirectoryTestCase(IntegrationTestCase):
	"""P3-U8 / P3-R22, P3-R23. The fixture company, plus a colleague who has
	Left, one who is Inactive and one in a second company."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.people = ensure_directory_fixtures()

	def setUp(self):
		frappe.set_user(EMPLOYEE_USER)

	def tearDown(self):
		frappe.set_user("Administrator")

	def _by_name(self, payload):
		return {person["name"]: person for person in payload["people"]}

	def _search(self, needle):
		"""The directory, addressed by search rather than by page one.

		A long-lived site (and the baseline profile in `utils.py`) has more
		than one page of active employees in the fixture company, so nothing
		here may assume a fixture is on the first page -- the same rule the
		rest of this suite follows about an empty baseline.
		"""
		return self._by_name(get_directory(query=needle, limit=_DIRECTORY_MAX_PAGE))

	# --- scenario 1: scope and the allow-list (P3-AE12) --------------------

	def test_the_directory_is_this_company_and_only_its_active_people(self):
		# The reader and their manager, each found by their own name.
		for role in ("employee", "manager"):
			name = self.people[role]
			found = self._search(frappe.db.get_value("Employee", name, "employee_name"))
			self.assertIn(name, found, f"the directory left out the {role}")

		# The four seeded colleagues share one name prefix, so one search
		# covers who is listed and who is not.
		found = self._search("Directory ")
		self.assertIn(self.people["colleague"], found)
		self.assertNotIn(
			self.people["other"], found, "an employee of another company reached the directory"
		)
		self.assertNotIn(self.people["left"], found, "an employee who has Left is still listed")
		self.assertNotIn(self.people["inactive"], found, "an Inactive employee is still listed")

	def test_a_person_carries_the_allow_listed_fields_and_nothing_else(self):
		payload = get_directory(query="Directory ", limit=_DIRECTORY_MAX_PAGE)
		for person in payload["people"] + get_directory()["people"]:
			extra = set(person) - ALLOWED_KEYS
			self.assertFalse(extra, f"the directory published {sorted(extra)}")
			for key in FORBIDDEN_KEYS:
				self.assertNotIn(key, person, f"the directory published {key}")

		colleague = self._by_name(payload)[self.people["colleague"]]
		self.assertEqual(colleague["designation"], self.people["designation"])
		self.assertEqual(colleague["department"], self.people["department"])
		self.assertEqual(colleague["email"], DIRECTORY_COLLEAGUE_EMAIL)
		# One extra query resolves every manager named on the page, so the
		# manager arrives as a name and as an id the page can link by.
		self.assertEqual(colleague["manager"], self.people["manager"])
		self.assertEqual(
			colleague["manager_name"],
			frappe.db.get_value("Employee", self.people["manager"], "employee_name"),
		)

	def test_the_work_email_is_company_email_and_is_absent_when_unset(self):
		"""P3-R22: `user_id` is a sign-in identifier, so an employee with no
		published work email has no email key at all -- never their login."""
		frappe.set_user("Administrator")
		employee = self.people["employee"]
		self.assertFalse(frappe.db.get_value("Employee", employee, "company_email"))
		login = frappe.db.get_value("Employee", employee, "user_id")
		frappe.set_user(EMPLOYEE_USER)

		reader = self._search(frappe.db.get_value("Employee", employee, "employee_name"))[employee]
		self.assertNotIn("email", reader)
		self.assertNotIn(login, str(reader))

		manager = self.people["manager"]
		listed = self._search(frappe.db.get_value("Employee", manager, "employee_name"))[manager]
		self.assertEqual(listed["email"], DIRECTORY_MANAGER_EMAIL)

	def test_the_generic_employee_list_is_still_only_self(self):
		"""The other half of P3-AE12, kept here as well as in
		`test_fixtures.TestStrictPermissionParity` because the projection is
		only safe while the generic route stays shut: the directory reads with
		`ignore_permissions`, so this is the assertion that says why it may.
		"""
		from frappe.client import get_list as client_get_list

		names = [row["name"] for row in client_get_list("Employee", limit_page_length=0)]
		self.assertEqual(names, [self.people["employee"]])

	# --- scenario 2: search ------------------------------------------------

	def test_a_search_shorter_than_two_characters_is_ignored(self):
		everyone = get_directory()
		for needle in ("", " ", "a", " x "):
			self.assertEqual(
				get_directory(query=needle)["total"],
				everyone["total"],
				f"a {needle!r} search filtered the directory",
			)

	def test_a_search_matches_a_name_a_role_or_a_department(self):
		colleague = self.people["colleague"]
		colleague_name = frappe.db.get_value("Employee", colleague, "employee_name")

		for needle in (
			colleague_name,
			self.people["designation"],
			self.people["department"],
		):
			found = self._search(needle)
			self.assertIn(colleague, found, f"searching {needle!r} did not find the colleague")

		self.assertEqual(get_directory(query="zzz-nobody-by-that-name")["people"], [])

	def test_a_long_search_is_cut_to_sixty_characters_rather_than_refused(self):
		colleague_name = frappe.db.get_value(
			"Employee", self.people["colleague"], "employee_name"
		)
		payload = get_directory(query=colleague_name.ljust(200, "z"))
		self.assertEqual(payload["people"], [])
		self.assertEqual(payload["total"], 0)

	def test_a_department_filter_narrows_the_page_and_the_chips_are_counted(self):
		payload = get_directory(department=self.people["department"])
		self.assertEqual(
			[person["name"] for person in payload["people"]], [self.people["colleague"]]
		)
		self.assertEqual(payload["total"], 1)

		chips = {chip["name"]: chip["count"] for chip in payload["departments"]}
		self.assertEqual(chips.get(self.people["department"]), 1)
		self.assertNotIn(None, chips, "a department chip was drawn for people who have none")
		self.assertNotIn("", chips)

	# --- scenario 3: no company -------------------------------------------

	def test_an_employee_with_no_company_gets_an_empty_page_not_an_error(self):
		employee = self.people["employee"]
		frappe.set_user("Administrator")
		company = frappe.db.get_value("Employee", employee, "company")
		# Set through the database on purpose: Company is mandatory on
		# Employee, and this is the shape of a record HR has left incomplete,
		# not a save the portal would ever make.
		frappe.db.set_value("Employee", employee, "company", None, update_modified=False)
		frappe.set_user(EMPLOYEE_USER)
		try:
			payload = get_directory()
			self.assertEqual(payload["people"], [])
			self.assertEqual(payload["total"], 0)
			self.assertEqual(payload["departments"], [])
			self.assertEqual(payload["limit"], _DIRECTORY_PAGE)
			self.assertEqual(payload["start"], 0)
		finally:
			frappe.set_user("Administrator")
			frappe.db.set_value("Employee", employee, "company", company, update_modified=False)
			frappe.set_user(EMPLOYEE_USER)

	# --- scenario 4: paging ------------------------------------------------

	def test_paging_clamps_and_reports_the_total(self):
		everyone = get_directory()
		self.assertGreaterEqual(everyone["total"], 3)
		self.assertEqual(everyone["limit"], _DIRECTORY_PAGE)

		first = get_directory(limit=2)
		self.assertEqual(first["limit"], 2)
		self.assertEqual(len(first["people"]), 2)
		self.assertEqual(first["total"], everyone["total"])

		second = get_directory(limit=2, start=2)
		self.assertEqual(second["start"], 2)
		self.assertEqual(second["total"], everyone["total"])
		self.assertFalse(
			{person["name"] for person in first["people"]}
			& {person["name"] for person in second["people"]},
			"the second page repeated the first",
		)
		# One order, so paging is stable: by name, and the two pages join up.
		names = [person["employee_name"] for person in first["people"] + second["people"]]
		self.assertEqual(names, sorted(names))

		self.assertEqual(get_directory(limit=0)["limit"], _DIRECTORY_PAGE)
		# A negative page is one row, not the default and not everything: the
		# floor is what every paged method in this module clamps to.
		self.assertEqual(get_directory(limit=-5)["limit"], 1)
		self.assertEqual(get_directory(limit=10_000)["limit"], _DIRECTORY_MAX_PAGE)
		self.assertEqual(get_directory(start=-5)["start"], 0)

		beyond = get_directory(start=everyone["total"] + 50)
		self.assertEqual(beyond["people"], [])
		self.assertEqual(beyond["total"], everyone["total"])
