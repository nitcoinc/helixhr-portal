import frappe
from frappe.tests import IntegrationTestCase

from helixhr.tests.utils import EMPLOYEE_USER, MANAGER_USER, make_test_employee_and_manager


class TestHRRequest(IntegrationTestCase):
	def setUp(self):
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()

	def tearDown(self):
		frappe.set_user("Administrator")

	def _make_request(self, as_user=EMPLOYEE_USER, **extra):
		frappe.set_user(as_user)
		doc = frappe.get_doc(
			{
				"doctype": "HR Request",
				"category": "HR Letter",
				"subject": "Need an employment letter",
				"details": "For a visa application",
				**extra,
			}
		)
		doc.insert()
		return doc

	def test_employee_is_set_from_session_even_if_posted_otherwise(self):
		# `before_insert` resolving `employee` from the session is now the
		# check that carries this (KTD5). It used to be Frappe's own Link
		# user-permission check, but that only fired because `employee`
		# was still empty when insert() checked create permission -- which
		# under strict User Permissions (P2-R26) refused *every* create,
		# including the employee's own. The field is now marked
		# `ignore_user_permissions` and if_owner is the read boundary; see
		# docs/architecture.md.
		doc = self._make_request(employee=self.manager_name)
		self.assertEqual(doc.employee, self.employee_name)

	def test_employee_cannot_change_status_or_hr_note(self):
		frappe.set_user(EMPLOYEE_USER)
		doc = self._make_request()
		self.assertEqual(doc.status, "Open")

		doc.status = "Done"
		doc.hr_note = "Sneaky note"
		doc.save()

		self.assertEqual(doc.status, "Open")
		self.assertIsNone(doc.hr_note)

	def test_employee_a_cannot_read_or_list_employee_bs_requests(self):
		other_request = self._make_request(as_user=MANAGER_USER)

		frappe.set_user(EMPLOYEE_USER)
		# frappe.get_doc() alone never checks permission -- Python code
		# always has raw ORM access; enforcement lives at the whitelisted/
		# REST layer (frappe.client.get, Desk's form loader), which calls
		# has_permission explicitly the way this asserts (confirmed while
		# writing this test: a bare get_doc() here returns the document
		# successfully even though has_permission is False).
		self.assertFalse(frappe.has_permission("HR Request", "read", other_request.name))

		names = frappe.get_list("HR Request", filters={"employee": self.manager_name}, pluck="name")
		self.assertNotIn(other_request.name, names)

	def test_attaching_a_file_to_another_employees_request_is_refused(self):
		other_request = self._make_request(as_user=MANAGER_USER)

		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(
				{
					"doctype": "File",
					"file_name": "sneaky.txt",
					"content": "hi",
					"attached_to_doctype": "HR Request",
					"attached_to_name": other_request.name,
					"is_private": 1,
				}
			).insert()

	def test_non_private_upload_to_a_request_is_refused(self):
		"""KTD18: a file's owner can attach it to any document they can
		*read*, so an is_private=0 upload targeting an HR Request is
		refused outright rather than silently coerced -- coercing after
		the fact would leave file_url pointing at the (already-written)
		public path. RequestForm.vue always uploads with is_private=1;
		this only matters against a caller that bypasses it."""
		frappe.set_user(EMPLOYEE_USER)
		doc = self._make_request()

		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(
				{
					"doctype": "File",
					"file_name": "letter.txt",
					"content": "details",
					"attached_to_doctype": "HR Request",
					"attached_to_name": doc.name,
					"is_private": 0,
				}
			).insert()

	def test_private_upload_to_own_request_succeeds(self):
		frappe.set_user(EMPLOYEE_USER)
		doc = self._make_request()

		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "letter.txt",
				"content": "details",
				"attached_to_doctype": "HR Request",
				"attached_to_name": doc.name,
				"is_private": 1,
			}
		)
		file_doc.insert()

		self.assertEqual(file_doc.is_private, 1)

	def test_hr_changes_status_and_note_in_desk(self):
		frappe.set_user(EMPLOYEE_USER)
		doc = self._make_request()

		frappe.set_user("Administrator")
		desk_doc = frappe.get_doc("HR Request", doc.name)
		desk_doc.status = "Done"
		desk_doc.hr_note = "Sent to your personal email"
		desk_doc.save()

		frappe.set_user(EMPLOYEE_USER)
		employee_view = frappe.get_doc("HR Request", doc.name)
		self.assertEqual(employee_view.status, "Done")
		self.assertEqual(employee_view.hr_note, "Sent to your personal email")


class TestHelixHRDocumentLink(IntegrationTestCase):
	def setUp(self):
		self.employee_name, _, _, _ = make_test_employee_and_manager()
		self.company = frappe.db.get_value("Employee", self.employee_name, "company")

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_company_less_and_own_company_links_are_returned_others_are_not(self):
		other_company = "_Test Company Other"
		if not frappe.db.exists("Company", other_company):
			frappe.get_doc(
				{
					"doctype": "Company",
					"company_name": other_company,
					"abbr": "TCO",
					"default_currency": "USD",
					"country": "United States",
				}
			).insert(ignore_permissions=True)

		everyone_link = frappe.get_doc(
			{"doctype": "HelixHR Document Link", "title": "Handbook", "url": "https://example.com/handbook"}
		).insert(ignore_permissions=True)
		own_company_link = frappe.get_doc(
			{
				"doctype": "HelixHR Document Link",
				"title": "Local policy",
				"url": "https://example.com/local",
				"company": self.company,
			}
		).insert(ignore_permissions=True)
		other_company_link = frappe.get_doc(
			{
				"doctype": "HelixHR Document Link",
				"title": "Other office policy",
				"url": "https://example.com/other",
				"company": other_company,
			}
		).insert(ignore_permissions=True)

		frappe.set_user(EMPLOYEE_USER)
		visible = frappe.get_list(
			"HelixHR Document Link",
			or_filters=[["company", "is", "not set"], ["company", "=", self.company]],
			pluck="name",
		)

		self.assertIn(everyone_link.name, visible)
		self.assertIn(own_company_link.name, visible)
		self.assertNotIn(other_company_link.name, visible)

	def test_employee_cannot_create_or_edit_document_links(self):
		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(
				{"doctype": "HelixHR Document Link", "title": "Sneaky", "url": "https://example.com"}
			).insert()


class TestDocumentLinkScope(IntegrationTestCase):
	"""P2-U1 / P2-R19 / P2-AE2: company scoping is a server rule on the
	DocType, not a filter the browser happens to send, and it must not
	depend on a site having created a Company User Permission."""

	OTHER_COMPANY = "_Test Company Other"

	def setUp(self):
		frappe.set_user("Administrator")
		self.employee_name, _, _, _ = make_test_employee_and_manager()
		self.company = frappe.db.get_value("Employee", self.employee_name, "company")
		if not frappe.db.exists("Company", self.OTHER_COMPANY):
			frappe.get_doc(
				{
					"doctype": "Company",
					"company_name": self.OTHER_COMPANY,
					"abbr": "TCO",
					"default_currency": "USD",
					"country": "United States",
				}
			).insert(ignore_permissions=True)

		self.everyone_link = self._link("P2-U1 handbook", "https://example.com/handbook")
		self.own_link = self._link("P2-U1 local policy", "https://example.com/local", self.company)
		self.other_link = self._link(
			"P2-U1 other office policy", "https://example.com/other", self.OTHER_COMPANY
		)

	def tearDown(self):
		frappe.set_user("Administrator")

	def _link(self, title, url, company=None):
		existing = frappe.db.get_value("HelixHR Document Link", {"title": title}, "name")
		if existing:
			return existing
		return frappe.get_doc(
			{
				"doctype": "HelixHR Document Link",
				"title": title,
				"url": url,
				"company": company,
			}
		).insert(ignore_permissions=True).name

	def test_the_portal_method_returns_global_and_own_company_links_only(self):
		from helixhr.api import get_my_documents

		frappe.set_user(EMPLOYEE_USER)
		names = [row["name"] for row in get_my_documents()]

		self.assertIn(self.everyone_link, names)
		self.assertIn(self.own_link, names)
		self.assertNotIn(self.other_link, names)

	def test_the_generic_list_get_and_count_routes_enforce_the_same_scope(self):
		frappe.set_user(EMPLOYEE_USER)

		# No or_filters: the scope has to come from the server. This is the
		# shape of frappe.client.get_list and /api/resource.
		names = frappe.get_list("HelixHR Document Link", pluck="name", limit=0)
		self.assertIn(self.everyone_link, names)
		self.assertIn(self.own_link, names)
		self.assertNotIn(self.other_link, names)

		self.assertTrue(frappe.has_permission("HelixHR Document Link", "read", self.everyone_link))
		self.assertTrue(frappe.has_permission("HelixHR Document Link", "read", self.own_link))
		self.assertFalse(frappe.has_permission("HelixHR Document Link", "read", self.other_link))

		with self.assertRaises(frappe.PermissionError):
			frappe.client.get("HelixHR Document Link", self.other_link)

		visible = frappe.client.get_count("HelixHR Document Link")
		self.assertEqual(
			visible,
			len(names),
			"get_count must count the same rows the scoped list returns",
		)

	def test_report_print_and_export_are_not_granted_to_employees(self):
		frappe.set_user(EMPLOYEE_USER)
		for ptype in ("report", "print", "export", "email", "write", "create", "share"):
			self.assertFalse(
				frappe.has_permission("HelixHR Document Link", ptype),
				f"Employee should not have {ptype} on HelixHR Document Link",
			)

	def test_hr_still_sees_every_company(self):
		frappe.set_user("Administrator")
		names = frappe.get_list("HelixHR Document Link", pluck="name", limit=0)
		self.assertIn(self.other_link, names)


class TestDocumentLinkUrlSafety(IntegrationTestCase):
	"""P2-R19: a policy catalogue stores web addresses, nothing else."""

	def tearDown(self):
		frappe.set_user("Administrator")

	def _insert(self, url, title="P2-U1 url check"):
		return frappe.get_doc(
			{"doctype": "HelixHR Document Link", "title": title, "url": url}
		).insert(ignore_permissions=True)

	def test_unsafe_and_malformed_links_are_refused(self):
		for url in (
			"javascript:alert(1)",
			"data:text/html;base64,PHNjcmlwdD4=",
			"not a url at all",
			"https://user:secret@example.com/handbook",
			"",
		):
			with self.assertRaises(frappe.ValidationError, msg=url):
				self._insert(url)

	def test_a_plain_https_link_is_still_accepted(self):
		doc = self._insert("https://example.com/policies/leave.pdf", title="P2-U1 valid url")
		self.assertEqual(doc.url, "https://example.com/policies/leave.pdf")
		frappe.delete_doc("HelixHR Document Link", doc.name, force=True, ignore_permissions=True)
