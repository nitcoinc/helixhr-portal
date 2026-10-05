"""Documents: HR publishes Important / General documents from the portal.

`save_document_link` / `delete_document_link` are gated like the company
logo (an anchored HR Manager for their own company, System Manager for any);
an uploaded file is private, inside `DOCUMENT_POLICY`, and attached to the
row, so Frappe's File permission follows the row's `has_permission` scope.
"""

import frappe
from frappe.tests import IntegrationTestCase

from helixhr.tests.test_hr_request import SAFE_PDF, with_uploaded_file
from helixhr.tests.test_upload_security import _ooxml
from helixhr.tests.utils import (
	EMPLOYEE_USER,
	HR_MANAGER_EMPLOYEE_USER,
	create_test_company,
	make_test_employee_and_manager,
	make_test_hr_manager_employee,
	make_test_user,
)

DOCTYPE = "HelixHR Document Link"
OTHER_COMPANY = "_Test Documents Co B"
OTHER_USER = "_test_documents_b@example.com"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


class TestDocuments(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_test_employee_and_manager()
		make_test_hr_manager_employee()
		create_test_company(OTHER_COMPANY, "TDCB")
		make_test_user(OTHER_USER, OTHER_COMPANY)
		cls.company = frappe.db.get_value("Employee", {"user_id": HR_MANAGER_EMPLOYEE_USER}, "company")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.local.request = None

	def _save(self, file=None, **fields):
		from helixhr.api import save_document_link

		fields = {"title": "P-Docs policy", "category": "Important", "company": self.company, **fields}
		frappe.local.request = with_uploaded_file(*file) if file else None
		try:
			return save_document_link(**fields)
		finally:
			frappe.local.request = None

	def test_hr_uploads_a_private_file_an_in_scope_employee_can_download(self):
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		row = self._save(file=("policy.pdf", SAFE_PDF))

		self.assertTrue(row.file.startswith("/private/files/"), row.file)
		self.assertFalse(row.url)
		file_doc = frappe.get_doc("File", {"file_url": row.file})
		self.assertEqual(file_doc.is_private, 1)
		self.assertEqual((file_doc.attached_to_doctype, file_doc.attached_to_name), (DOCTYPE, row.name))

		frappe.set_user(EMPLOYEE_USER)
		self.assertTrue(frappe.get_doc("File", file_doc.name).is_downloadable())
		frappe.set_user(OTHER_USER)
		self.assertFalse(frappe.get_doc("File", file_doc.name).is_downloadable())

	def test_file_url_of_a_readable_file_is_copied_under_the_same_policy(self):
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		first = self._save(file=("deck.pptx", _ooxml("ppt/presentation.xml")))
		second = self._save(title="P-Docs copy", file_url=first.file)
		self.assertTrue(second.file.startswith("/private/files/"))
		self.assertTrue(
			frappe.db.exists("File", {"attached_to_doctype": DOCTYPE, "attached_to_name": second.name})
		)

	def test_no_file_and_no_url_bad_type_svg_and_oversize_are_refused(self):
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		for file in (
			None,
			("policy.svg", SVG),
			("policy.pdf", SVG),
			("policy.exe", b"MZ\x90\x00"),
			("policy.pdf", SAFE_PDF + b"\x00" * (20 * 1024 * 1024)),
		):
			with self.subTest(file=file and file[0]), self.assertRaises(frappe.ValidationError):
				self._save(file=file)
		with self.assertRaises(frappe.ValidationError):
			self._save(url="javascript:alert(1)")
		with self.assertRaises(frappe.ValidationError):
			self._save(url="https://example.com/p", category="Urgent")

	def test_scope_hr_manager_own_company_only_employee_never(self):
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		for company in (OTHER_COMPANY, None):
			with self.subTest(company=company), self.assertRaises(frappe.PermissionError):
				self._save(url="https://example.com/p", company=company)

		frappe.set_user("Administrator")
		foreign = self._save(url="https://example.com/b", company=OTHER_COMPANY)
		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		from helixhr.api import delete_document_link

		with self.assertRaises(frappe.PermissionError):
			self._save(name=foreign.name, url="https://example.com/b")
		with self.assertRaises(frappe.PermissionError):
			delete_document_link(foreign.name)

		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.PermissionError):
			self._save(url="https://example.com/p")

	def test_edit_switches_source_and_delete_removes_the_file(self):
		from helixhr.api import delete_document_link

		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		row = self._save(file=("policy.pdf", SAFE_PDF))
		edited = self._save(name=row.name, url="https://example.com/moved", category="General")
		self.assertEqual((edited.url, edited.file, edited.category), ("https://example.com/moved", None, "General"))
		self.assertFalse(frappe.db.exists("File", {"attached_to_doctype": DOCTYPE, "attached_to_name": row.name}))

		again = self._save(name=row.name, file=("policy.pdf", SAFE_PDF))
		delete_document_link(again.name)
		self.assertFalse(frappe.db.exists(DOCTYPE, row.name))
		# By attachment, not URL: Frappe shares one path between identical uploads.
		self.assertFalse(frappe.db.exists("File", {"attached_to_doctype": DOCTYPE, "attached_to_name": row.name}))

	def test_get_my_documents_is_newest_first_with_category_and_date(self):
		from helixhr.api import get_my_documents

		frappe.set_user(HR_MANAGER_EMPLOYEE_USER)
		old = self._save(title="P-Docs old", url="https://example.com/o", published_on="2001-01-01")
		new = self._save(title="P-Docs new", url="https://example.com/n", published_on="2099-01-01")

		frappe.set_user(EMPLOYEE_USER)
		rows = get_my_documents()
		names = [row.name for row in rows]
		self.assertLess(names.index(new.name), names.index(old.name))
		dates = [str(row.published_on) for row in rows]
		self.assertEqual(dates, sorted(dates, reverse=True))
		self.assertEqual({row.category for row in rows} - {"Important", "General"}, set())

	def test_can_manage_documents_flag(self):
		from helixhr.api import _can_manage_documents

		self.assertTrue(_can_manage_documents(HR_MANAGER_EMPLOYEE_USER))
		self.assertFalse(_can_manage_documents(EMPLOYEE_USER))

	def test_backfill_patch_is_idempotent(self):
		from helixhr.patches.v1_0 import backfill_document_link_dates

		row = self._save(url="https://example.com/legacy")
		frappe.db.sql(
			f"update `tab{DOCTYPE}` set category = null, published_on = null, creation = %s where name = %s",
			("2024-03-04 10:00:00", row.name),
		)
		for _run in range(2):
			backfill_document_link_dates.execute()
			stored = frappe.db.get_value(DOCTYPE, row.name, ["category", "published_on"], as_dict=True)
			self.assertEqual((stored.category, str(stored.published_on)), ("General", "2024-03-04"))
