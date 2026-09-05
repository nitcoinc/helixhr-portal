import io
import uuid

import frappe
from frappe.tests import IntegrationTestCase

from helixhr.tests.utils import EMPLOYEE_USER, MANAGER_USER, make_test_employee_and_manager


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


def with_uploaded_file(filename, content=b"a private attachment"):
	"""Put one file on `frappe.local.request` for the duration of a call."""
	return _Request({"file": _UploadedFile(filename, content)})


class TestHRRequest(IntegrationTestCase):
	def setUp(self):
		self.employee_name, _, self.manager_name, _ = make_test_employee_and_manager()

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
		frappe.local.request = with_uploaded_file("bank-form.txt")
		attached = attach_to_my_request(created["name"])
		self.assertTrue(attached["created"])
		self.assertEqual(attached["is_private"], 1)

		# A third press of Retry upload with the same file attaches nothing
		# new -- the request must not end up carrying it twice.
		frappe.local.request = with_uploaded_file("bank-form.txt")
		again = attach_to_my_request(created["name"])
		self.assertFalse(again["created"])
		self.assertEqual(again["name"], attached["name"])

		detail = get_my_request(created["name"])
		self.assertEqual([row["file_name"] for row in detail["attachments"]], ["bank-form.txt"])

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
		frappe.local.request = with_uploaded_file("id-scan.txt")
		attach_to_my_request(created["name"])

		frappe.set_user("Administrator")
		desk = frappe.get_doc("HR Request", created["name"])
		desk.status = "Done"
		desk.hr_note = "Attached the signed letter."
		desk.save()

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
		self.assertEqual([row["file_name"] for row in detail["attachments"]], ["id-scan.txt"])
		self.assertTrue(all(row["is_private"] == 1 for row in detail["attachments"]))

	def test_an_unrelated_employee_cannot_open_alter_share_or_attach_to_the_request(self):
		from helixhr.api import attach_to_my_request, get_my_request, mark_my_request_read

		created = self._create(as_user=MANAGER_USER, subject="Manager's request")

		frappe.set_user(EMPLOYEE_USER)
		with self.assertRaises(frappe.PermissionError):
			get_my_request(created["name"])
		with self.assertRaises(frappe.PermissionError):
			mark_my_request_read(created["name"])
		frappe.local.request = with_uploaded_file("sneaky.txt")
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

		frappe.local.request = with_uploaded_file("huge.pdf", b"x" * (_ATTACHMENT_MAX_BYTES + 1))
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
