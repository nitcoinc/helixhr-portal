from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from helixhr import preflight
from helixhr.api import _can_open_desk, create_employee_login, get_person, send_employee_welcome
from helixhr.tests.test_api_people import _ensure_other_company
from helixhr.tests.utils import (
	EMPLOYEE_USER,
	assert_has_employee_user_permission,
	ensure_test_company,
	ensure_test_gender,
	make_test_employee_and_manager,
	make_test_hr_manager_employee,
)
from helixhr.utils import RATE_LIMIT_POLICY, portal_home_page, reset_rate_limit

# Plan 2026-10-22-001 U1 (R4-R8, R17). Every employee here is a throwaway
# with its own address: a Left status disables the linked User outside the
# test's rollback (docs/runbook.md), so no shared fixture login is ever
# linked, left or mailed.


def _throwaway_employee(company, email=False, **fields):
	"""A fresh Active Employee; `email=None` leaves the company email empty."""
	tag = frappe.generate_hash(length=8)
	doc = frappe.get_doc(
		{
			"doctype": "Employee",
			"employee_number": f"U1-{tag}",
			"first_name": "Login",
			"last_name": f"Probe {tag}",
			"company": company,
			"date_of_birth": "1990-01-01",
			"date_of_joining": "2020-01-01",
			"gender": ensure_test_gender(),
			"status": "Active",
			"company_email": f"u1-{tag}@helixhr.test" if email is False else email,
			**fields,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def _roles(user):
	return sorted(frappe.get_all("Has Role", filters={"parent": user, "parenttype": "User"}, pluck="role"))


def _has_password(user):
	return bool(
		frappe.db.sql(
			"select 1 from `__Auth` where doctype='User' and name=%s and fieldname='password'", user
		)
	)


class EmployeeLoginTestCase(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company = ensure_test_company()
		self.hr_employee, self.hr_user = make_test_hr_manager_employee()
		make_test_employee_and_manager()
		self.other_company = _ensure_other_company()
		self.employee = _throwaway_employee(self.company)
		self.email = self.employee.company_email
		reset_rate_limit("create_employee_login", self.hr_user)
		reset_rate_limit("send_employee_welcome", self.hr_user)

	def tearDown(self):
		frappe.set_user("Administrator")

	def _sign_in(self, enabled):
		return patch("helixhr.api.microsoft_sign_in_enabled", return_value=enabled)

	def _create(self, employee=None, sign_in=False, queued=1):
		frappe.set_user(self.hr_user)
		with self._sign_in(sign_in), patch("helixhr.api.send_notification", return_value=queued) as send:
			result = create_employee_login(employee or self.employee.name)
		frappe.set_user("Administrator")
		return result, send


class TestCreateEmployeeLogin(EmployeeLoginTestCase):
	def test_hr_creates_a_basic_login_like_erpnexts_own(self):
		result, _send = self._create()

		self.assertTrue(result["created"])
		self.assertEqual(result["login"]["state"], "active")
		self.assertEqual(result["login"]["user"], self.email)
		user = frappe.get_doc("User", self.email)
		self.assertTrue(user.enabled)
		self.assertEqual(_roles(self.email), ["Employee", "Employee Self Service"])
		self.assertFalse(_has_password(self.email))
		self.assertEqual(user.send_welcome_email, 0)
		self.assertEqual(user.first_name, "Login")
		self.assertEqual(frappe.db.get_value("Employee", self.employee.name, "user_id"), self.email)
		self.assertEqual(frappe.db.get_value("Employee", self.employee.name, "create_user_permission"), 1)
		assert_has_employee_user_permission(self.email, self.employee.name)
		self.assertTrue(
			frappe.db.exists(
				"User Permission", {"user": self.email, "allow": "Company", "for_value": self.company}
			)
		)
		self.assertNotIn(self.email, preflight.check_employee_user_permissions()["detail"])

	def test_the_login_and_its_creator_leave_an_info_comment(self):
		self._create()
		notes = frappe.get_all(
			"Comment",
			filters={"reference_doctype": "Employee", "reference_name": self.employee.name, "comment_type": "Info"},
			pluck="content",
		)
		self.assertTrue(any(self.email in note and "login" in note for note in notes), notes)

	def test_with_microsoft_sign_in_the_welcome_goes_to_the_company_email(self):
		result, send = self._create(sign_in=True)

		self.assertEqual(result["welcome"], "sent")
		send.assert_called_once()
		event_key, recipients, context = send.call_args.args[:3]
		self.assertEqual(event_key, "employee_welcome")
		self.assertEqual(recipients, [self.email])
		self.assertEqual(context["recipient_first_name"], "Login")
		self.assertEqual(context["company"], self.company)
		self.assertEqual(context["login_email"], self.email)
		self.assertTrue(context["portal_url"])
		self.assertFalse(result["login"]["welcome_held"])

	def test_without_microsoft_sign_in_the_welcome_is_held_then_sent_later(self):
		result, send = self._create(sign_in=False)

		self.assertEqual(result["welcome"], "held")
		send.assert_not_called()
		self.assertTrue(result["login"]["welcome_held"])

		frappe.set_user(self.hr_user)
		with self._sign_in(False), patch("helixhr.api.send_notification", return_value=1) as send:
			self.assertEqual(send_employee_welcome(self.employee.name)["welcome"], "held")
		send.assert_not_called()

		with self._sign_in(True), patch("helixhr.api.send_notification", return_value=1) as send:
			later = send_employee_welcome(self.employee.name)
		send.assert_called_once()
		self.assertEqual(later["welcome"], "sent")
		self.assertFalse(later["login"]["welcome_held"])
		self.assertFalse(get_person(self.employee.name)["login"]["welcome_held"])

	def test_calling_twice_returns_the_same_login_without_a_second_mail(self):
		self._create(sign_in=True)
		users = frappe.db.count("User", {"name": self.email})

		again, send = self._create(sign_in=True)

		self.assertFalse(again["created"])
		self.assertIsNone(again["welcome"])
		self.assertEqual(again["login"]["user"], self.email)
		send.assert_not_called()
		self.assertEqual(frappe.db.count("User", {"name": self.email}), users)

	def test_a_failed_welcome_never_undoes_the_login(self):
		frappe.set_user(self.hr_user)
		with self._sign_in(True), patch("helixhr.api.send_notification", side_effect=Exception("smtp down")):
			result = create_employee_login(self.employee.name)
		frappe.set_user("Administrator")

		self.assertTrue(result["created"])
		self.assertEqual(result["welcome"], "not_sent")
		self.assertTrue(frappe.db.exists("User", self.email))
		self.assertEqual(frappe.db.get_value("Employee", self.employee.name, "user_id"), self.email)
		self.assertTrue(result["login"]["welcome_held"])

	def test_the_new_login_lands_on_the_employee_home_like_any_employee(self):
		from helixhr.api import get_portal_bootstrap

		self._create()
		self.assertEqual(portal_home_page(self.email), portal_home_page(EMPLOYEE_USER))
		self.assertEqual(_can_open_desk(self.email), _can_open_desk(EMPLOYEE_USER))
		self.assertEqual(
			frappe.db.get_value("User", self.email, "user_type"),
			frappe.db.get_value("User", EMPLOYEE_USER, "user_type"),
		)
		frappe.set_user(self.email)
		boot = get_portal_bootstrap()
		self.assertEqual(boot["employee"]["name"], self.employee.name)
		self.assertIsNone(boot["desk_url"])
		self.assertFalse(boot["can_see_people"])

	def test_the_write_is_rate_limited(self):
		self.assertIn("create_employee_login", RATE_LIMIT_POLICY)
		self.assertIn("send_employee_welcome", RATE_LIMIT_POLICY)
		frappe.flags.helixhr_enforce_rate_limits = True
		self.addCleanup(lambda: frappe.flags.pop("helixhr_enforce_rate_limits", None))
		self.addCleanup(reset_rate_limit, "create_employee_login", self.hr_user)
		limit, _seconds = RATE_LIMIT_POLICY["create_employee_login"]
		frappe.set_user(self.hr_user)
		with self._sign_in(False):
			for _ in range(limit):
				create_employee_login(self.employee.name)
			with self.assertRaises(frappe.RateLimitExceededError):
				create_employee_login(self.employee.name)


class TestCreateEmployeeLoginRefusals(EmployeeLoginTestCase):
	"""The privilege boundary: each refusal writes nothing."""

	def _refused(self, employee, error=frappe.ValidationError, user=None):
		frappe.set_user(user or self.hr_user)
		with self._sign_in(True), patch("helixhr.api.send_notification", return_value=1) as send:
			with self.assertRaises(error) as caught:
				create_employee_login(employee)
		frappe.set_user("Administrator")
		send.assert_not_called()
		return str(caught.exception)

	def test_an_email_that_is_already_a_user_without_an_employee_is_refused_unnamed(self):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": self.email,
				"first_name": "Existing",
				"last_name": "Holder",
				"send_welcome_email": 0,
				"roles": [{"role": "System Manager"}],
			}
		).insert(ignore_permissions=True)
		before = frappe.get_doc("User", self.email)

		message = self._refused(self.employee.name)

		# No Employee in the caller's scope holds the address, so the holder
		# is not named (the refusal itself stays).
		self.assertIn("someone else", message)
		self.assertNotIn("Existing Holder", message)
		after = frappe.get_doc("User", self.email)
		self.assertEqual(after.modified, before.modified)
		self.assertEqual(_roles(self.email), ["System Manager"])
		self.assertFalse(frappe.db.get_value("Employee", self.employee.name, "user_id"))

	def test_an_email_that_is_another_employees_company_email_is_refused(self):
		holder = _throwaway_employee(self.company, email=self.email.upper())

		message = self._refused(self.employee.name)

		self.assertIn(holder.employee_name, message)
		self.assertFalse(frappe.db.exists("User", self.email))

	def test_an_email_that_is_another_employees_login_is_refused(self):
		holder = _throwaway_employee(self.company)
		frappe.db.set_value("Employee", holder.name, "user_id", self.email)

		self.assertIn(holder.employee_name, self._refused(self.employee.name))
		self.assertFalse(frappe.db.exists("User", self.email))

	def test_a_holder_outside_the_callers_scope_is_not_named(self):
		"""Security P3: the refusal stays, the cross-company name does not."""
		holder = _throwaway_employee(self.other_company, email=self.email.upper())
		message = self._refused(self.employee.name)
		self.assertIn("someone else", message)
		self.assertNotIn(holder.employee_name, message)

		frappe.db.set_value("Employee", holder.name, {"company_email": None, "user_id": self.email})
		frappe.get_doc(
			{
				"doctype": "User",
				"email": self.email,
				"first_name": "Abroad",
				"last_name": "Holder",
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
		message = self._refused(self.employee.name)
		self.assertIn("someone else", message)
		self.assertNotIn(holder.employee_name, message)
		self.assertNotIn("Abroad Holder", message)

	def test_a_left_employee_is_refused(self):
		frappe.db.set_value("Employee", self.employee.name, {"status": "Left", "relieving_date": today()})
		self._refused(self.employee.name)
		self.assertFalse(frappe.db.exists("User", self.email))

	def test_an_employee_with_no_company_email_is_refused(self):
		bare = _throwaway_employee(self.company, email=None)
		self.assertIn("company email", self._refused(bare.name))
		self.assertFalse(frappe.db.get_value("Employee", bare.name, "user_id"))

	def test_an_employee_outside_the_callers_scope_is_refused(self):
		outsider = _throwaway_employee(self.other_company)
		self._refused(outsider.name, frappe.PermissionError)
		self.assertFalse(frappe.db.exists("User", outsider.company_email))

	def test_an_employee_only_caller_is_refused(self):
		self._refused(self.employee.name, frappe.PermissionError, user=EMPLOYEE_USER)
		self.assertFalse(frappe.db.exists("User", self.email))

	def test_resend_is_refused_without_an_active_login_or_outside_scope(self):
		frappe.set_user(self.hr_user)
		with self._sign_in(True), self.assertRaises(frappe.ValidationError):
			send_employee_welcome(self.employee.name)
		outsider = _throwaway_employee(self.other_company)
		with self._sign_in(True), self.assertRaises(frappe.PermissionError):
			send_employee_welcome(outsider.name)
		frappe.set_user(EMPLOYEE_USER)
		with self._sign_in(True), self.assertRaises(frappe.PermissionError):
			send_employee_welcome(self.employee.name)


class TestPersonLoginBlock(EmployeeLoginTestCase):
	def _login(self, employee):
		frappe.set_user(self.hr_user)
		try:
			return get_person(employee)["login"]
		finally:
			frappe.set_user("Administrator")

	def test_no_login_then_active_then_disabled(self):
		self.assertEqual(self._login(self.employee.name), {"state": "none", "user": None, "welcome_held": False})

		self._create()
		self.assertEqual(
			self._login(self.employee.name), {"state": "active", "user": self.email, "welcome_held": True}
		)

		# Throwaway login: disabling it here touches nobody else's sign-in.
		frappe.db.set_value("User", self.email, "enabled", 0)
		self.assertEqual(
			self._login(self.employee.name), {"state": "disabled", "user": self.email, "welcome_held": False}
		)

	def test_a_login_made_outside_helixhr_is_never_reported_held(self):
		_employee, _user, _manager, _manager_user = make_test_employee_and_manager()
		login = self._login(_employee)
		self.assertEqual(login["state"], "active")
		self.assertFalse(login["welcome_held"])


class TestWelcomePreflight(EmployeeLoginTestCase):
	def test_held_welcomes_warn_while_microsoft_sign_in_is_off(self):
		self._create(sign_in=False)
		with patch.object(preflight, "_entra_enabled", return_value=False):
			result = preflight.check_welcome_emails()
		self.assertEqual(result["status"], preflight.WARN)
		self.assertIn("held", result["detail"])

	def test_it_passes_once_microsoft_sign_in_is_on(self):
		with patch.object(preflight, "_entra_enabled", return_value=True):
			self.assertEqual(preflight.check_welcome_emails()["status"], preflight.PASS)

	def test_the_check_is_registered(self):
		self.assertIn(preflight.check_welcome_emails, preflight.CHECKS)
