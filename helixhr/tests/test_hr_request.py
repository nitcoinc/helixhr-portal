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
		# Base permission (a Link field's value is restricted by the
		# acting user's own User Permission on Employee, enforced on
		# create too, not just read) already refuses this outright, before
		# the doctype's own before_insert override even gets a chance to
		# run -- confirmed while writing this test. before_insert setting
		# `self.employee` from the session stays as defense in depth for
		# any other path that reaches it (e.g. a future bulk-import
		# route bypassing the ordinary create check).
		with self.assertRaises(frappe.PermissionError):
			self._make_request(employee=self.manager_name)

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
