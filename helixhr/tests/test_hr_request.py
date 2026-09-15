import base64
import io
import uuid

import frappe
from frappe.model.workflow import apply_workflow
from frappe.tests import IntegrationTestCase

from helixhr.tests.utils import (
	EMPLOYEE_USER,
	IT_TEAM_USER,
	MANAGER_USER,
	ensure_hr_manager_user,
	ensure_test_company,
	make_test_employee_and_manager,
	make_test_it_user,
	make_test_user,
)


class _UploadedFile:
	"""The shape `frappe.request.files["file"]` has: a name and a stream.

	Werkzeug's FileStorage in three lines, so an attachment test can exercise
	`helixhr.api.attach_to_my_request` -- which reads the multipart body off
	the request -- without an HTTP round trip.
	"""

	def __init__(self, filename, content=b"a private attachment"):
		self.filename = filename
		self.stream = io.BytesIO(content)


class _Request:
	"""Only the two attributes Frappe reaches for on `frappe.local.request`
	while a File is being written: the multipart body, and the host it would
	build an absolute URL from (None, so `get_url` falls back to the site
	config the way it does in a background job)."""

	host = None

	def __init__(self, files):
		self.files = files


# One real, one-page PDF, produced once by pypdf. The P2-U9 upload policy
# checks extension *and* leading signature, and Frappe's own File controller
# then parses the bytes (`strip_exif_data` for images, `pdf_contains_js` for
# PDFs) -- so a plain text body under a .pdf name is refused twice over, and a
# test fixture has to be the real thing.
SAFE_PDF_BASE64 = (
	"JVBERi0xLjMKJeLjz9MKMSAwIG9iago8PAovUHJvZHVjZXIgKHB5cGRmKQo+PgplbmRvYmoKMiAwIG9iago8PAovVHlwZSAvUGFnZXMKL0NvdW50IDEKL0tpZHMgWyA0IDAgUiBdCj4+CmVuZG9iagozIDAgb2JqCjw8Ci9UeXBlIC9DYXRhbG9nCi9QYWdlcyAyIDAgUgo+PgplbmRvYmoKNCAwIG9iago8PAovVHlwZSAvUGFnZQovUmVzb3VyY2VzIDw8Cj4+Ci9NZWRpYUJveCBbIDAuMCAwLjAgNzIgNzIgXQovUGFyZW50IDIgMCBSCj4+CmVuZG9iagp4cmVmCjAgNQowMDAwMDAwMDAwIDY1NTM1IGYgCjAwMDAwMDAwMTUgMDAwMDAgbiAKMDAwMDAwMDA1NCAwMDAwMCBuIAowMDAwMDAwMTEzIDAwMDAwIG4gCjAwMDAwMDAxNjIgMDAwMDAgbiAKdHJhaWxlcgo8PAovU2l6ZSA1Ci9Sb290IDMgMCBSCi9JbmZvIDEgMCBSCj4+CnN0YXJ0eHJlZgoyNTQKJSVFT0YK"
)
SAFE_PDF = base64.b64decode(SAFE_PDF_BASE64)


def with_uploaded_file(filename, content=SAFE_PDF):
	"""Put one file on `frappe.local.request` for the duration of a call."""
	return _Request({"file": _UploadedFile(filename, content)})


class TestHRRequest(IntegrationTestCase):
	def setUp(self):
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()
		make_test_it_user()

	def tearDown(self):
		frappe.set_user("Administrator")

	def _make_request(self, as_user=EMPLOYEE_USER, **extra):
		"""One request, made the way the portal makes one.

		P2-U8: role Employee no longer has `create` on HR Request, so this
		goes through the session-scoped portal method rather than a generic
		insert. `employee` is deliberately still passed through by the tests
		that check it is ignored -- `create_my_request` never accepts it, and
		`before_insert` resolves it from the session either way.
		"""
		from helixhr.api import create_my_request

		frappe.set_user(as_user)
		fields = {
			"category": "HR Letter",
			"subject": "Need an employment letter",
			"details": "For a visa application",
			**extra,
		}
		fields.pop("employee", None)
		created = create_my_request(operation_key=str(uuid.uuid4()), **fields)
		return frappe.get_doc("HR Request", created["name"])

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

	def test_a_left_employee_no_longer_owns_their_old_request(self):
		"""`request_belongs_to_session` resolves the Employee the same way
		`_session_company` and `hrms.api.get_current_employee` do --
		`status = "Active"`. A user whose Employee is Left or Inactive with
		their login still enabled used to pass the ownership branch of
		`events.file_before_insert`."""
		from helixhr.helixhr.doctype.hr_request.hr_request import request_belongs_to_session

		doc = self._make_request()
		frappe.set_user(EMPLOYEE_USER)
		self.assertTrue(request_belongs_to_session(doc.name))

		frappe.set_user("Administrator")
		frappe.db.set_value("Employee", self.employee_name, "status", "Inactive")
		self.addCleanup(
			frappe.db.set_value, "Employee", self.employee_name, "status", "Active"
		)

		frappe.set_user(EMPLOYEE_USER)
		self.assertFalse(request_belongs_to_session(doc.name))

	def test_the_employee_role_can_no_longer_create_or_write_hr_requests_generically(self):
		"""P2-U8 step 2. Creation is `helixhr.api.create_my_request` and
		nothing else -- the generic route is closed at the DocType, not by
		the UI declining to offer it."""
		frappe.set_user(EMPLOYEE_USER)
		self.assertFalse(frappe.has_permission("HR Request", "create"))

		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(
				{"doctype": "HR Request", "category": "Other", "subject": "Generic insert"}
			).insert()

		own = self._make_request()
		frappe.set_user(EMPLOYEE_USER)
		self.assertFalse(frappe.has_permission("HR Request", "write", own.name))

	def test_employee_cannot_change_status_or_hr_note(self):
		"""Two gates, and P2-U8 added the outer one.

		`status` and `hr_note` are permlevel 1 with only HR granted write
		there, which is what used to reset an employee's write to either
		field. Since P2-U8 the employee has no write on the DocType at all,
		so the save is refused before permlevel is even consulted -- and the
		record still says Open with no note afterwards.
		"""
		frappe.set_user(EMPLOYEE_USER)
		doc = self._make_request()
		self.assertEqual(doc.status, "Open")

		doc.status = "Done"
		doc.hr_note = "Sneaky note"
		with self.assertRaises(frappe.PermissionError):
			doc.save()

		stored = frappe.db.get_value("HR Request", doc.name, ["status", "hr_note"], as_dict=True)
		self.assertEqual(stored.status, "Open")
		self.assertIsNone(stored.hr_note)

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
					"file_name": "sneaky.pdf",
					"content": SAFE_PDF_BASE64,
					"decode": 1,
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
					"file_name": "letter.pdf",
					"content": SAFE_PDF_BASE64,
					"decode": 1,
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
				"file_name": "letter.pdf",
				"content": SAFE_PDF_BASE64,
					"decode": 1,
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

		frappe.set_user(ensure_hr_manager_user())
		apply_workflow({"doctype": "HR Request", "name": doc.name}, "Pick up")
		desk_doc = frappe.get_doc("HR Request", doc.name)
		desk_doc.hr_note = "Sent to your personal email"
		desk_doc.save()
		apply_workflow({"doctype": "HR Request", "name": doc.name}, "Done")

		frappe.set_user(EMPLOYEE_USER)
		employee_view = frappe.get_doc("HR Request", doc.name)
		self.assertEqual(employee_view.status, "Done")
		self.assertEqual(employee_view.hr_note, "Sent to your personal email")

	def test_route_is_stamped_and_the_it_worker_can_pick_up_its_request(self):
		doc = self._make_request(category="IT / Asset")
		self.assertEqual(doc.routed_to_role, "IT Team")

		frappe.set_user(IT_TEAM_USER)
		apply_workflow({"doctype": "HR Request", "name": doc.name}, "Pick up")
		doc.reload()
		self.assertEqual(doc.status, "In Progress")
		self.assertEqual(doc.picked_up_by, IT_TEAM_USER)
		self.assertIsNotNone(doc.picked_up_on)

	def test_a_worker_cannot_decide_their_own_request_on_a_raw_save(self):
		doc = self._make_request(as_user=IT_TEAM_USER, category="IT / Asset")
		frappe.set_user(IT_TEAM_USER)
		doc.status = "In Progress"
		with self.assertRaises(frappe.PermissionError):
			doc.save()

	def test_request_filing_details_are_frozen_after_pickup(self):
		doc = self._make_request(category="IT / Asset")
		frappe.set_user(IT_TEAM_USER)
		apply_workflow({"doctype": "HR Request", "name": doc.name}, "Pick up")
		doc.reload()
		doc.subject = "A changed request"
		with self.assertRaises(frappe.PermissionError):
			doc.save()

	def test_it_worker_lists_only_stored_it_routes_and_can_read_them(self):
		it_request = self._make_request(category="IT / Asset")
		hr_request = self._make_request(category="HR Letter")
		frappe.set_user(IT_TEAM_USER)

		names = frappe.get_list("HR Request", pluck="name")
		self.assertIn(it_request.name, names)
		self.assertNotIn(hr_request.name, names)
		self.assertTrue(frappe.has_permission("HR Request", "read", it_request.name))
		self.assertFalse(frappe.has_permission("HR Request", "read", hr_request.name))

	def test_repointing_a_category_does_not_retroactively_change_a_request_route(self):
		doc = self._make_request(category="IT / Asset")
		frappe.set_user("Administrator")
		category = frappe.get_doc("HelixHR Request Category", "IT / Asset")
		original_route = category.route_to_role
		self.addCleanup(frappe.db.set_value, category.doctype, category.name, "route_to_role", original_route)
		category.route_to_role = "HR Manager"
		category.save(ignore_permissions=True)

		doc.reload()
		self.assertEqual(doc.routed_to_role, "IT Team")


class TestRequestCategories(IntegrationTestCase):
	"""P5-U1: categories are routable records, not a static Select list."""

	def setUp(self):
		frappe.set_user("Administrator")
		from helixhr.patches.v1_0.seed_request_categories import execute
		from helixhr.tests.utils import make_test_employee_and_manager

		make_test_employee_and_manager()
		execute()

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_seeded_categories_keep_the_legacy_values_as_their_names(self):
		from helixhr.patches.v1_0.seed_request_categories import CATEGORIES, execute

		execute()
		self.assertEqual(
			set(frappe.get_all("HelixHR Request Category", pluck="name")),
			{spec["category_name"] for spec in CATEGORIES},
		)

	def test_picker_returns_new_active_categories_and_create_refuses_inactive_ones(self):
		from helixhr.api import create_my_request, get_request_categories
		from helixhr.tests.utils import EMPLOYEE_USER

		category = frappe.get_doc(
			{
				"doctype": "HelixHR Request Category",
				"category_name": "Facilities",
				"hint": "Workspace and building help",
				"route_to_role": "HR Manager",
			}
		).insert()
		self.addCleanup(
			frappe.delete_doc,
			"HelixHR Request Category",
			category.name,
			force=True,
			ignore_permissions=True,
		)
		frappe.db.set_value("HelixHR Request Category", "Other", "is_active", 0)
		self.addCleanup(frappe.db.set_value, "HelixHR Request Category", "Other", "is_active", 1)

		names = [row["name"] for row in get_request_categories()]
		self.assertIn("Facilities", names)
		self.assertNotIn("Other", names)
		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.ValidationError):
			create_my_request(
				category="Other", subject="Inactive category", operation_key=str(uuid.uuid4())
			)

	def test_a_category_cannot_route_requests_to_a_broad_role(self):
		category = frappe.get_doc(
			{
				"doctype": "HelixHR Request Category",
				"category_name": "P5-U1 broad role test",
				"hint": "Must not be saved",
				"route_to_role": "Employee",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			category.insert()


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


class TestRequestApprovalQueue(IntegrationTestCase):
	"""P5-U6: a routed request is a fourth kind in the existing approval
	queue, workable end to end, with a conversation the employee can
	answer."""

	def setUp(self):
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()
		self.it_employee, self.it_user = make_test_it_user()
		self.addCleanup(setattr, frappe.local, "request", None)
		self._mailed = []

	def tearDown(self):
		frappe.set_user("Administrator")
		for row in self._mailed:
			frappe.db.delete("Email Queue Recipient", {"parent": row})
			frappe.db.delete("Email Queue", {"name": row})

	def _file(self, category="IT / Asset", **extra):
		from helixhr.api import create_my_request

		frappe.set_user(EMPLOYEE_USER)
		fields = {"category": category, "subject": "Need a new laptop", "details": "Mine died", **extra}
		created = create_my_request(operation_key=str(uuid.uuid4()), **fields)
		frappe.set_user("Administrator")
		return created["name"]

	def _token(self, name):
		row = frappe.db.get_value("HR Request", name, ["modified", "status"], as_dict=True)
		return {"expected_modified": str(row.modified), "expected_state": row.status}

	def _second_it_worker(self):
		company = ensure_test_company()
		user = "second-it-team@helixhr.test"
		employee = make_test_user(user, company)
		user_doc = frappe.get_doc("User", user)
		roles = [row.role for row in user_doc.roles if row.role != "Employee"]
		if "IT Team" not in roles:
			roles.append("IT Team")
			user_doc.set("roles", [{"role": role} for role in roles])
			user_doc.save(ignore_permissions=True)
			frappe.clear_cache(user=user)
		return employee, user

	def _watch_mail(self):
		before = set(frappe.get_all("Email Queue", pluck="name"))

		def added():
			rows = set(frappe.get_all("Email Queue", pluck="name")) - before
			self._mailed.extend(rows)
			return rows

		return added

	def test_get_approval_detail_actions_match_get_transitions_and_others_are_refused(self):
		from helixhr.api import act_on_approval, get_approval_detail

		name = self._file()
		frappe.set_user(self.it_user)
		detail = get_approval_detail("request", name)
		self.assertEqual(set(detail["actions"]), {"Pick up", "Reject"})

		with self.assertRaises(frappe.ValidationError):
			act_on_approval("HR Request", name, "Done", **self._token(name))

	def test_a_stale_token_is_refused_and_current_state_then_works(self):
		from helixhr.api import act_on_approval

		name = self._file()
		stale = self._token(name)

		frappe.set_user(self.it_user)
		act_on_approval("HR Request", name, "Pick up", **stale)

		with self.assertRaises(frappe.ValidationError):
			act_on_approval("HR Request", name, "Done", **stale)

		act_on_approval("HR Request", name, "Done", **self._token(name))
		self.assertEqual(frappe.db.get_value("HR Request", name, "status"), "Done")

	def test_two_workers_picking_up_the_same_request_the_second_is_refused_and_named(self):
		from helixhr.api import act_on_approval

		name = self._file()
		_, second_user = self._second_it_worker()
		token = self._token(name)

		frappe.set_user(self.it_user)
		act_on_approval("HR Request", name, "Pick up", **token)

		frappe.set_user(second_user)
		with self.assertRaises(frappe.ValidationError):
			act_on_approval("HR Request", name, "Pick up", **token)
		self.assertEqual(frappe.db.get_value("HR Request", name, "picked_up_by"), self.it_user)

	def test_need_info_and_reject_require_a_reason_written_before_the_transition(self):
		from helixhr.api import act_on_approval
		from helixhr.events import DECISION_REASON_FIELD

		name = self._file()
		frappe.set_user(self.it_user)
		act_on_approval("HR Request", name, "Pick up", **self._token(name))

		with self.assertRaises(frappe.ValidationError):
			act_on_approval("HR Request", name, "Need info", **self._token(name))

		act_on_approval(
			"HR Request", name, "Need info", comment="Which laptop model?", **self._token(name)
		)
		doc = frappe.get_doc("HR Request", name)
		self.assertEqual(doc.status, "Waiting on Employee")
		self.assertEqual(doc.get(DECISION_REASON_FIELD), "Which laptop model?")

	def test_employee_reply_moves_the_request_back_and_emails_the_routed_role(self):
		from helixhr.api import act_on_approval, reply_to_my_request

		name = self._file()
		frappe.set_user(self.it_user)
		act_on_approval("HR Request", name, "Pick up", **self._token(name))
		act_on_approval("HR Request", name, "Need info", comment="Which model?", **self._token(name))

		added = self._watch_mail()
		frappe.set_user(EMPLOYEE_USER)
		result = reply_to_my_request(
			name, "A Dell Latitude, please.", expected_modified=self._token(name)["expected_modified"]
		)

		self.assertEqual(result["status"], "In Progress")
		mails = added()
		self.assertEqual(len(mails), 1)
		recipients = frappe.get_all(
			"Email Queue Recipient", filters={"parent": next(iter(mails))}, pluck="recipient"
		)
		self.assertIn(self.it_user, recipients)

	def test_reply_against_a_status_that_isnt_waiting_on_employee_is_refused(self):
		from helixhr.api import reply_to_my_request

		name = self._file()
		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.ValidationError):
			reply_to_my_request(name, "hello", expected_modified=self._token(name)["expected_modified"])

	def test_an_over_long_reply_is_refused(self):
		from helixhr.api import _DETAILS_MAX, act_on_approval, reply_to_my_request

		name = self._file()
		frappe.set_user(self.it_user)
		act_on_approval("HR Request", name, "Pick up", **self._token(name))
		act_on_approval("HR Request", name, "Need info", comment="model?", **self._token(name))

		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.ValidationError):
			reply_to_my_request(
				name, "x" * (_DETAILS_MAX + 1), expected_modified=self._token(name)["expected_modified"]
			)

	def test_reply_and_attach_rate_limits_are_enforced(self):
		from helixhr.utils import rate_limit_bounds, rate_limit_per_user, reset_rate_limit

		frappe.set_user(EMPLOYEE_USER)
		for action in ("reply_to_my_request", "attach_to_request_reply"):
			limit, _seconds = rate_limit_bounds(action)
			frappe.flags.helixhr_enforce_rate_limits = True
			try:
				reset_rate_limit(action)
				with self.assertRaises(frappe.RateLimitExceededError, msg=action):
					for _ in range(limit + 5):
						rate_limit_per_user(action)
			finally:
				frappe.flags.helixhr_enforce_rate_limits = False
				reset_rate_limit(action)

	def test_an_employee_cannot_reply_to_somebody_elses_request(self):
		from helixhr.api import reply_to_my_request

		name = self._file()
		frappe.set_user(MANAGER_USER)
		with self.assertRaises(frappe.PermissionError):
			reply_to_my_request(name, "not mine", expected_modified=self._token(name)["expected_modified"])

	def test_hr_attaches_a_file_to_a_reply_employee_can_see_it_a_non_worker_cannot_attach(self):
		from helixhr.api import act_on_approval, attach_to_request_reply, get_my_request

		name = self._file()
		frappe.set_user(self.it_user)
		act_on_approval("HR Request", name, "Pick up", **self._token(name))

		frappe.local.request = with_uploaded_file("replacement-quote.pdf")
		attach_to_request_reply(name)
		frappe.local.request = None

		frappe.set_user(EMPLOYEE_USER)
		detail = get_my_request(name)
		self.assertEqual([row["file_name"] for row in detail["hr_attachments"]], ["replacement-quote.pdf"])

		# The request's own employee is refused: they may not act on their
		# own request (P5-R9), which is what this method reuses to authorise.
		frappe.local.request = with_uploaded_file("sneaky.pdf")
		with self.assertRaises(frappe.PermissionError):
			attach_to_request_reply(name)

	def test_the_thread_excludes_the_handover_prefix_and_reads_as_plain_conversation(self):
		from helixhr.api import act_on_approval, get_approval_detail

		name = self._file(details="My laptop died over the weekend.")
		frappe.set_user(self.it_user)
		act_on_approval("HR Request", name, "Pick up", **self._token(name))
		act_on_approval("HR Request", name, "Need info", comment="Which model?", **self._token(name))
		# Simulate a hand-over note landing on this record's Comments some
		# other way -- never reachable through this doctype's own actions,
		# but the filter should hold regardless of how one got there.
		frappe.get_doc("HR Request", name).add_comment("Comment", "Sent to HR: escalate this please")

		frappe.set_user(self.it_user)
		thread = get_approval_detail("request", name)["thread"]
		messages = [entry["message"] for entry in thread]
		self.assertIn("My laptop died over the weekend.", messages)
		self.assertIn("Which model?", messages)
		self.assertFalse(any(message.startswith("Sent to HR:") for message in messages))
		self.assertTrue(all("workflow" not in message.lower() for message in messages))

	def test_homes_action_queue_renders_with_a_request_pending(self):
		from helixhr.api import _pending_approvals

		self._file()
		frappe.set_user(self.it_user)
		decisions = _pending_approvals(self.it_employee)
		self.assertTrue(any(row["reference_doctype"] == "HR Request" for row in decisions))

	def test_an_it_team_holders_queue_is_populated(self):
		from helixhr.api import _approval_summaries

		name = self._file()
		frappe.set_user(self.it_user)
		rows, _capped = _approval_summaries(self.it_employee)
		matching = [row for row in rows if row["name"] == name]
		self.assertEqual(len(matching), 1)
		self.assertEqual(matching[0]["kind"], "request")
		self.assertFalse(matching[0]["for_hr"], "IT-routed rows must never carry HR's caption")


class TestRequestIdempotency(IntegrationTestCase):
	"""P2-U8 / P2-AE7. Creating a request and attaching its file are two
	steps, and neither may duplicate itself when a response goes missing."""

	def setUp(self):
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()
		self.addCleanup(setattr, frappe.local, "request", None)
		frappe.set_user(EMPLOYEE_USER)

	def tearDown(self):
		frappe.set_user("Administrator")

	def _create(self, key, **extra):
		from helixhr.api import create_my_request

		fields = {"category": "HR Letter", "subject": "Address proof for the bank", **extra}
		return create_my_request(operation_key=key, **fields)

	def test_a_lost_response_retried_with_the_same_key_returns_the_same_request(self):
		key = str(uuid.uuid4())
		first = self._create(key)
		self.assertTrue(first["created"])

		# The browser never saw the first answer, so it sends the identical
		# call again with the key it already generated.
		second = self._create(key)
		self.assertFalse(second["created"])
		self.assertEqual(second["name"], first["name"])

		self.assertEqual(
			frappe.db.count("HR Request", {"employee": self.employee_name, "client_operation_key": key}),
			1,
		)

	def test_an_upload_that_failed_can_be_retried_against_the_same_request(self):
		from helixhr.api import attach_to_my_request, get_my_request

		created = self._create(str(uuid.uuid4()))

		# First attempt: the network dropped it, so nothing was written.
		# Second attempt, same request, same file.
		frappe.local.request = with_uploaded_file("bank-form.pdf")
		attached = attach_to_my_request(created["name"])
		self.assertTrue(attached["created"])
		self.assertEqual(attached["is_private"], 1)

		# A third press of Retry upload with the same file attaches nothing
		# new -- the request must not end up carrying it twice.
		frappe.local.request = with_uploaded_file("bank-form.pdf")
		again = attach_to_my_request(created["name"])
		self.assertFalse(again["created"])
		self.assertEqual(again["name"], attached["name"])

		detail = get_my_request(created["name"])
		self.assertEqual([row["file_name"] for row in detail["attachments"]], ["bank-form.pdf"])

	def test_another_employees_operation_key_reveals_nothing_and_asks_for_a_new_one(self):
		key = str(uuid.uuid4())
		frappe.set_user(MANAGER_USER)
		theirs = self._create(key, subject="Manager's own private business")

		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.DuplicateEntryError) as caught:
			self._create(key)

		# Nothing about the other record leaks through the refusal.
		message = str(caught.exception)
		self.assertNotIn(theirs["name"], message)
		self.assertNotIn("Manager's own private business", message)

		# And nothing about it changed, or gained an attachment.
		stored = frappe.db.get_value(
			"HR Request", theirs["name"], ["employee", "subject"], as_dict=True
		)
		self.assertEqual(stored.employee, self.manager_name)
		self.assertEqual(stored.subject, "Manager's own private business")

		frappe.set_user(EMPLOYEE_USER)
		self.assertEqual(
			frappe.db.count("HR Request", {"employee": self.employee_name, "client_operation_key": key}),
			0,
		)

		# A rotated key is all the caller has to change.
		rotated = self._create(str(uuid.uuid4()))
		self.assertTrue(rotated["created"])

	def test_a_refused_create_writes_nothing_and_leaves_the_key_usable(self):
		"""P2-U8 scenario 3, server side: a terminal validation failure must
		not leave a half-made record behind for the retry to find."""
		key = str(uuid.uuid4())
		with self.assertRaises(frappe.ValidationError):
			self._create(key, category="Not A Real Category")
		self.assertFalse(frappe.db.exists("HR Request", {"client_operation_key": key}))

		with self.assertRaises(frappe.ValidationError):
			self._create(key, subject="   ")
		self.assertFalse(frappe.db.exists("HR Request", {"client_operation_key": key}))

	def test_a_malformed_operation_key_is_refused_at_the_boundary(self):
		for key in ("", "short", "x" * 200, "not a key; DROP"):
			with self.assertRaises(frappe.ValidationError, msg=key):
				self._create(key)


class TestRequestDetailAndScope(IntegrationTestCase):
	"""P2-U8 scenarios 4, 5, 6 and 8."""

	def setUp(self):
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()
		self.addCleanup(setattr, frappe.local, "request", None)

	def tearDown(self):
		frappe.set_user("Administrator")

	def _create(self, as_user=EMPLOYEE_USER, **extra):
		from helixhr.api import create_my_request

		frappe.set_user(as_user)
		fields = {"category": "HR Letter", "subject": "Address proof", **extra}
		return create_my_request(operation_key=str(uuid.uuid4()), **fields)

	def test_detail_carries_what_the_employee_wrote_the_dates_the_status_and_hrs_reply(self):
		from helixhr.api import attach_to_my_request, get_my_request

		created = self._create(details="Address as on my profile.")
		frappe.local.request = with_uploaded_file("id-scan.pdf")
		attach_to_my_request(created["name"])

		frappe.set_user(ensure_hr_manager_user())
		apply_workflow({"doctype": "HR Request", "name": created["name"]}, "Pick up")
		desk = frappe.get_doc("HR Request", created["name"])
		desk.hr_note = "Attached the signed letter."
		desk.save()
		apply_workflow({"doctype": "HR Request", "name": created["name"]}, "Done")

		frappe.set_user(EMPLOYEE_USER)
		detail = get_my_request(created["name"])

		self.assertEqual(detail["subject"], "Address proof")
		self.assertEqual(detail["details"], "Address as on my profile.")
		self.assertEqual(detail["status"], "Done")
		self.assertEqual(detail["hr_note"], "Attached the signed letter.")
		self.assertTrue(detail["creation"])
		# The lifecycle stamps the timeline is drawn from, written by the
		# controller inside HR's own save.
		self.assertTrue(detail["picked_up_on"])
		self.assertTrue(detail["replied_on"])
		self.assertTrue(detail["closed_on"])
		self.assertEqual([row["file_name"] for row in detail["attachments"]], ["id-scan.pdf"])
		self.assertTrue(all(row["is_private"] == 1 for row in detail["attachments"]))

	def test_an_unrelated_employee_cannot_open_alter_share_or_attach_to_the_request(self):
		from helixhr.api import attach_to_my_request, get_my_request, mark_my_request_read

		created = self._create(as_user=MANAGER_USER, subject="Manager's request")

		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.PermissionError):
			get_my_request(created["name"])
		with self.assertRaises(frappe.PermissionError):
			mark_my_request_read(created["name"])
		frappe.local.request = with_uploaded_file("sneaky.pdf")
		with self.assertRaises(frappe.PermissionError):
			attach_to_my_request(created["name"])
		with self.assertRaises(frappe.PermissionError):
			frappe.share.add("HR Request", created["name"], EMPLOYEE_USER, read=1)

		self.assertFalse(frappe.has_permission("HR Request", "read", created["name"]))
		self.assertNotIn(
			created["name"],
			frappe.get_list("HR Request", pluck="name", limit=0),
		)
		self.assertEqual(
			frappe.db.count(
				"File",
				{"attached_to_doctype": "HR Request", "attached_to_name": created["name"]},
			),
			0,
		)

	def test_an_attachment_must_be_a_document_and_must_fit(self):
		from helixhr.api import _ATTACHMENT_MAX_BYTES, attach_to_my_request

		created = self._create()
		frappe.local.request = with_uploaded_file("payload.exe")
		with self.assertRaises(frappe.ValidationError):
			attach_to_my_request(created["name"])

		frappe.local.request = with_uploaded_file("huge.pdf", SAFE_PDF + b"x" * _ATTACHMENT_MAX_BYTES)
		with self.assertRaises(frappe.ValidationError):
			attach_to_my_request(created["name"])

		self.assertEqual(
			frappe.db.count(
				"File",
				{"attached_to_doctype": "HR Request", "attached_to_name": created["name"]},
			),
			0,
		)

	def test_opening_a_request_clears_the_unread_reply_it_was_opened_from(self):
		from helixhr.api import get_my_requests, mark_my_request_read

		created = self._create()

		frappe.set_user("Administrator")
		desk = frappe.get_doc("HR Request", created["name"])
		desk.hr_note = "Ready at the front desk."
		desk.save()

		frappe.set_user(EMPLOYEE_USER)
		listed = {row["name"]: row for row in get_my_requests()["requests"]}
		self.assertTrue(listed[created["name"]]["unread"], "an HR reply is an unread obligation")

		cleared = mark_my_request_read(created["name"])
		self.assertEqual(cleared["cleared"], 1)

		listed = {row["name"]: row for row in get_my_requests()["requests"]}
		self.assertFalse(listed[created["name"]]["unread"])
		self.assertEqual(
			frappe.db.count(
				"Notification Log",
				{"for_user": EMPLOYEE_USER, "document_name": created["name"], "read": 0},
			),
			0,
		)

	def test_the_first_page_is_bounded_and_load_more_neither_duplicates_nor_loses_rows(self):
		from helixhr.api import get_my_requests

		frappe.set_user(EMPLOYEE_USER)
		before = get_my_requests(limit=100)["total"]
		for index in range(4):
			self._create(subject=f"P2-U8 paging {index}")

		frappe.set_user(EMPLOYEE_USER)
		first = get_my_requests(limit=2)
		self.assertEqual(len(first["requests"]), 2)
		self.assertEqual(first["limit"], 2)
		self.assertEqual(first["total"], before + 4)

		more = get_my_requests(limit=4)
		names = [row["name"] for row in more["requests"]]
		self.assertEqual(len(names), len(set(names)), "Load More must not repeat a row")
		# The larger page is a superset of the smaller one in the same order,
		# which is what keeps the scroll position meaningful.
		self.assertEqual(names[:2], [row["name"] for row in first["requests"]])

		# Bounded whatever the caller asks for.
		self.assertLessEqual(len(get_my_requests(limit=10_000)["requests"]), 100)
