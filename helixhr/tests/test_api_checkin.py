import json
from datetime import datetime, timedelta

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, get_datetime, getdate, now_datetime, time_diff_in_seconds

from helixhr.api import (
	_PORTAL_DEVICE_ID,
	_last_punch_in_window,
	_next_log_type,
	_shift_window,
	get_my_attendance,
	get_my_checkins,
	punch_my_checkin,
)
from helixhr.tests.utils import (
	EMPLOYEE_USER,
	NIGHT_SHIFT_TYPE,
	PORTAL_SHIFT_TYPE,
	assign_test_shift,
	clear_test_shifts,
	ensure_test_shift_type,
	make_test_employee_and_manager,
)

# Somewhere in Bengaluru, and a point far enough away to fall outside any
# sane check-in radius (P3-U4 scenario 6).
HERE = (12.9716, 77.5946)
FAR = (13.0827, 80.2707)


class CheckinTestCase(IntegrationTestCase):
	"""Shared fixture: an employee with a shift whose window covers the whole
	site day, so `now_datetime()` is always inside it whatever time the suite
	runs at."""

	def setUp(self):
		self.employee_name, _, _, _ = make_test_employee_and_manager()
		frappe.set_user("Administrator")
		self._clear_punches()
		ensure_test_shift_type()
		assign_test_shift(self.employee_name)
		self.previous_mobile = frappe.db.get_single_value(
			"HR Settings", "allow_employee_checkin_from_mobile_app"
		)
		self.previous_tracking = frappe.db.get_single_value("HR Settings", "allow_geolocation_tracking")
		frappe.db.set_single_value("HR Settings", "allow_employee_checkin_from_mobile_app", 1)
		frappe.set_user(EMPLOYEE_USER)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value(
			"HR Settings", "allow_employee_checkin_from_mobile_app", self.previous_mobile
		)
		frappe.db.set_single_value("HR Settings", "allow_geolocation_tracking", self.previous_tracking)
		self._clear_punches()

	def _clear_punches(self):
		frappe.db.delete("Employee Checkin", {"employee": self.employee_name})

	def _punches(self):
		return frappe.get_all(
			"Employee Checkin",
			filters={"employee": self.employee_name},
			fields=["name", "time", "log_type", "device_id", "latitude", "longitude"],
			order_by="time asc",
		)

	def _age_last_punch(self, seconds):
		"""Backdate the newest punch so the 60-second debounce no longer
		covers it. The suite cannot sleep, and the debounce is measured
		against the stored time."""
		rows = self._punches()
		frappe.db.set_value(
			"Employee Checkin",
			rows[-1].name,
			"time",
			add_to_date(now_datetime(), seconds=-seconds),
			update_modified=False,
		)

	def _seed_punch(self, time, log_type, latitude=None, longitude=None):
		frappe.set_user("Administrator")
		doc = frappe.get_doc(
			{
				"doctype": "Employee Checkin",
				"employee": self.employee_name,
				"time": time,
				"log_type": log_type,
				"latitude": latitude,
				"longitude": longitude,
			}
		)
		doc.flags.ignore_validate = True
		doc.insert(ignore_permissions=True)
		frappe.set_user(EMPLOYEE_USER)
		return doc.name


class TestPunchDerivation(CheckinTestCase):
	"""P3-U4 scenarios 1, 2, 3a, 5 / P3-AE3, P3-AE4, P3-R7."""

	def test_a_second_tap_inside_a_minute_returns_the_same_punch(self):
		first = punch_my_checkin(HERE[0], HERE[1], "IN")
		second = punch_my_checkin(HERE[0], HERE[1], "IN")

		self.assertEqual(first["log_type"], "IN")
		self.assertFalse(first["existing"])
		self.assertTrue(second["existing"])
		self.assertEqual(second["name"], first["name"])
		self.assertEqual(len(self._punches()), 1)

	def test_the_punch_after_an_in_is_an_out(self):
		punch_my_checkin(HERE[0], HERE[1], "IN")
		self._age_last_punch(120)

		second = punch_my_checkin(HERE[0], HERE[1], "OUT")

		self.assertEqual(second["log_type"], "OUT")
		self.assertFalse(second["existing"])
		self.assertEqual([row.log_type for row in self._punches()], ["IN", "OUT"])

	def test_a_stale_expected_type_is_refused_rather_than_guessed(self):
		punch_my_checkin(HERE[0], HERE[1], "IN")
		self._age_last_punch(120)

		with self.assertRaises(frappe.ValidationError) as caught:
			punch_my_checkin(HERE[0], HERE[1], "IN")

		self.assertIn("reload", str(caught.exception).lower())
		self.assertEqual(len(self._punches()), 1)

	def test_a_type_that_is_neither_in_nor_out_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			punch_my_checkin(HERE[0], HERE[1], "MAYBE")
		self.assertEqual(self._punches(), [])

	def test_the_punch_carries_server_time_and_the_portal_device_id(self):
		"""P3-U4 scenario 5. The method takes no timestamp at all, so a
		client clock cannot reach the row."""
		result = punch_my_checkin(HERE[0], HERE[1], "IN")

		row = self._punches()[0]
		self.assertEqual(row.device_id, _PORTAL_DEVICE_ID)
		self.assertLess(abs(time_diff_in_seconds(now_datetime(), row.time)), 120)
		self.assertTrue(result["has_location"])
		self.assertEqual(row.latitude, HERE[0])
		self.assertEqual(row.longitude, HERE[1])

	def test_derivation_follows_the_shift_window_not_the_users_local_day(self):
		"""P3-U4 scenario 2 / P3-AE4. The employee's user sits in New York,
		where the earlier punch belongs to yesterday; the derivation reads
		the shift window HRMS resolves for the punch, so the next punch is
		still an OUT."""
		frappe.set_user("Administrator")
		frappe.db.set_value("User", EMPLOYEE_USER, "time_zone", "America/New_York")
		frappe.set_user(EMPLOYEE_USER)
		try:
			window = _shift_window(self.employee_name, now_datetime())
			self._seed_punch(window["start"] + timedelta(minutes=5), "IN")

			result = punch_my_checkin(HERE[0], HERE[1], "OUT")

			self.assertEqual(result["log_type"], "OUT")
		finally:
			frappe.set_user("Administrator")
			frappe.db.set_value("User", EMPLOYEE_USER, "time_zone", None)
			frappe.set_user(EMPLOYEE_USER)

	def test_a_night_shift_punch_after_midnight_derives_out(self):
		"""P3-U4 scenario 3a. 23:00 and 01:00 sit on two calendar days and
		inside one shift window."""
		frappe.set_user("Administrator")
		ensure_test_shift_type(NIGHT_SHIFT_TYPE, start_time="22:00:00", end_time="06:00:00")
		assign_test_shift(self.employee_name, NIGHT_SHIFT_TYPE)
		frappe.set_user(EMPLOYEE_USER)

		after_midnight = datetime.combine(getdate(), datetime.min.time()) + timedelta(hours=1)
		window = _shift_window(self.employee_name, after_midnight)

		self.assertIsNotNone(window)
		self.assertLess(window["start"], after_midnight)
		self.assertGreater(window["end"], after_midnight)

		self._seed_punch(window["start"] + timedelta(hours=1), "IN")
		last = _last_punch_in_window(self.employee_name, window["start"], window["end"])

		self.assertEqual(last.log_type, "IN")
		self.assertEqual(_next_log_type(last), "OUT")

	def test_no_punch_in_the_window_derives_in(self):
		self.assertEqual(_next_log_type(None), "IN")


class TestPunchCoordinates(CheckinTestCase):
	"""P3-U4 scenario 3 / P3-AE5, P3-R7a."""

	def _refused(self, latitude, longitude):
		with self.assertRaises(frappe.ValidationError):
			punch_my_checkin(latitude, longitude, "IN")
		self.assertEqual(self._punches(), [])

	def test_missing_coordinates_are_refused(self):
		self._refused(None, None)

	def test_empty_coordinates_are_refused(self):
		self._refused("", "")

	def test_non_numeric_coordinates_are_refused(self):
		self._refused("here", "there")

	def test_nan_is_refused(self):
		self._refused("NaN", "NaN")

	def test_infinity_is_refused(self):
		self._refused("Infinity", HERE[1])

	def test_an_out_of_range_latitude_is_refused(self):
		self._refused(95, HERE[1])

	def test_an_out_of_range_longitude_is_refused(self):
		self._refused(HERE[0], 200)

	def test_both_coordinates_zero_are_refused(self):
		self._refused(0, 0)

	def test_a_real_location_on_the_null_meridian_is_accepted(self):
		"""Zero is only meaningless as a *pair*: 0 latitude with a real
		longitude is a place in the Gulf of Guinea, and refusing it would be
		a rule about arithmetic rather than about locations."""
		result = punch_my_checkin(0, HERE[1], "IN")
		self.assertEqual(result["log_type"], "IN")


class TestCheckinAvailability(CheckinTestCase):
	"""P3-U4 scenario 4 / P3-AE6, P3-R5, P3-R8, P3-R9."""

	def _month(self):
		from frappe.utils import get_first_day, get_last_day, today

		return str(get_first_day(today())), str(get_last_day(today()))

	def _checkin_block(self):
		return get_my_attendance(*self._month())["checkin"]

	def test_a_shift_and_the_hr_flag_make_the_button_available(self):
		block = self._checkin_block()

		self.assertTrue(block["enabled"])
		self.assertIsNone(block["reason"])
		self.assertIsNotNone(block["window"])
		self.assertIsNone(block["last"])

	def test_the_last_punch_is_reported_with_whether_location_was_captured(self):
		punch_my_checkin(HERE[0], HERE[1], "IN")

		block = self._checkin_block()

		self.assertEqual(block["last"]["log_type"], "IN")
		self.assertTrue(block["last"]["has_location"])

	def test_the_hr_flag_off_disables_the_button_and_refuses_the_punch(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value("HR Settings", "allow_employee_checkin_from_mobile_app", 0)
		frappe.set_user(EMPLOYEE_USER)

		block = self._checkin_block()

		self.assertFalse(block["enabled"])
		self.assertIn("HR", block["reason"])
		with self.assertRaises(frappe.ValidationError):
			punch_my_checkin(HERE[0], HERE[1], "IN")
		self.assertEqual(self._punches(), [])

	def test_no_shift_at_all_disables_the_button_and_refuses_the_punch(self):
		frappe.set_user("Administrator")
		clear_test_shifts(self.employee_name)
		frappe.set_user(EMPLOYEE_USER)

		block = self._checkin_block()

		self.assertFalse(block["enabled"])
		self.assertIsNone(block["window"])
		self.assertIn("HR", block["reason"])
		with self.assertRaises(frappe.ValidationError):
			punch_my_checkin(HERE[0], HERE[1], "IN")
		self.assertEqual(self._punches(), [])

	def test_a_window_that_has_not_opened_yet_says_when_it_does(self):
		"""A narrow window nobody is inside: the strip names the time
		instead of claiming check-in is not set up (P3-R8)."""
		frappe.set_user("Administrator")
		# A half-hour window at a time the suite is not running in, with no
		# grace period either side to widen it.
		night = now_datetime().hour != 3
		opens_at, closes_at = ("03:33:00", "04:03:00") if night else ("15:33:00", "16:03:00")
		ensure_test_shift_type(
			PORTAL_SHIFT_TYPE,
			start_time=opens_at,
			end_time=closes_at,
			begin_before=0,
			allow_after=0,
		)
		frappe.set_user(EMPLOYEE_USER)

		block = self._checkin_block()

		self.assertFalse(block["enabled"])
		self.assertIsNotNone(block["window"])
		self.assertIn(opens_at[:5], block["reason"])
		with self.assertRaises(frappe.ValidationError):
			punch_my_checkin(HERE[0], HERE[1], "IN")

	def test_the_existing_attendance_keys_are_untouched(self):
		result = get_my_attendance(*self._month())

		for key in ("tracked", "days", "missing", "summary", "exceptions", "working_days_known"):
			self.assertIn(key, result)

	def test_the_day_sheet_reports_location_per_punch(self):
		"""P3-R9. `has_location` rather than the coordinates themselves: the
		day sheet draws a pin, and nothing on it needs the position."""
		punch_my_checkin(HERE[0], HERE[1], "IN")
		self._age_last_punch(120)
		self._seed_punch(now_datetime(), "OUT")

		rows = get_my_checkins(str(getdate()))

		self.assertEqual(len(rows), 2)
		self.assertTrue(rows[0]["has_location"])
		self.assertFalse(rows[1]["has_location"])
		self.assertNotIn("latitude", rows[0])


class TestGeofence(CheckinTestCase):
	"""P3-U4 scenario 6. HRMS owns the radius; the portal surfaces its
	message unchanged."""

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		if not frappe.db.exists("Shift Location", "_Test Shift Location"):
			frappe.get_doc(
				{
					"doctype": "Shift Location",
					"location_name": "_Test Shift Location",
					"latitude": HERE[0],
					"longitude": HERE[1],
					"checkin_radius": 100,
				}
			).insert(ignore_permissions=True)
		assign_test_shift(
			self.employee_name, PORTAL_SHIFT_TYPE, shift_location="_Test Shift Location"
		)
		frappe.db.set_single_value("HR Settings", "allow_geolocation_tracking", 1)
		frappe.set_user(EMPLOYEE_USER)

	def test_inside_the_radius_the_punch_lands(self):
		result = punch_my_checkin(HERE[0], HERE[1], "IN")

		self.assertEqual(result["log_type"], "IN")

	def test_outside_the_radius_hrms_refuses_with_its_own_message(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			punch_my_checkin(FAR[0], FAR[1], "IN")

		self.assertIn("100 meters", str(caught.exception))
		self.assertEqual(self._punches(), [])


class TestLocationRetention(CheckinTestCase):
	"""P3-U4 scenario 3b / P3-AE15, P3-KTD15, P3-R28."""

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.previous_days = frappe.conf.get("helixhr_checkin_location_retention_days")

	def tearDown(self):
		frappe.conf["helixhr_checkin_location_retention_days"] = self.previous_days
		super().tearDown()

	def _punch_with_version(self, days_ago):
		name = self._seed_punch(
			add_to_date(now_datetime(), days=-days_ago), "IN", latitude=HERE[0], longitude=HERE[1]
		)
		frappe.set_user("Administrator")
		frappe.get_doc(
			{
				"doctype": "Version",
				"ref_doctype": "Employee Checkin",
				"docname": name,
				"data": json.dumps(
					{
						"changed": [
							["latitude", 0, HERE[0]],
							["longitude", 0, HERE[1]],
							["log_type", "OUT", "IN"],
						],
						"added": [],
						"removed": [],
					}
				),
			}
		).insert(ignore_permissions=True)
		return name

	def _coordinates(self, name):
		return frappe.db.get_value(
			"Employee Checkin", name, ["latitude", "longitude", "geolocation"], as_dict=True
		)

	def _version_fields(self, name):
		fields = []
		for row in frappe.get_all("Version", filters={"docname": name}, pluck="data"):
			for entry in json.loads(row).get("changed", []):
				fields.append(entry[0])
		return fields

	def test_the_job_does_nothing_while_the_retention_key_is_unset(self):
		from helixhr.tasks import null_stale_checkin_coordinates

		frappe.conf["helixhr_checkin_location_retention_days"] = None
		old = self._punch_with_version(400)

		result = null_stale_checkin_coordinates()

		self.assertEqual(result["scrubbed"], 0)
		self.assertEqual(self._coordinates(old).latitude, HERE[0])

	def test_the_job_ignores_a_zero_or_negative_retention_period(self):
		from helixhr.tasks import null_stale_checkin_coordinates

		frappe.conf["helixhr_checkin_location_retention_days"] = 0
		old = self._punch_with_version(400)

		self.assertEqual(null_stale_checkin_coordinates()["scrubbed"], 0)
		self.assertEqual(self._coordinates(old).latitude, HERE[0])

	def test_old_punches_lose_their_coordinates_and_their_version_rows(self):
		from helixhr.tasks import null_stale_checkin_coordinates

		frappe.conf["helixhr_checkin_location_retention_days"] = 30
		old = self._punch_with_version(90)
		recent = self._punch_with_version(2)

		result = null_stale_checkin_coordinates()

		self.assertEqual(result["scrubbed"], 1)
		self.assertFalse(self._coordinates(old).latitude)
		self.assertFalse(self._coordinates(old).longitude)
		self.assertFalse(self._coordinates(old).geolocation)
		self.assertEqual(self._version_fields(old), ["log_type"])
		# The recent punch of an active employee keeps everything.
		self.assertEqual(self._coordinates(recent).latitude, HERE[0])
		self.assertIn("latitude", self._version_fields(recent))

	def test_the_job_is_idempotent(self):
		from helixhr.tasks import null_stale_checkin_coordinates

		frappe.conf["helixhr_checkin_location_retention_days"] = 30
		self._punch_with_version(90)

		self.assertEqual(null_stale_checkin_coordinates()["scrubbed"], 1)
		self.assertEqual(null_stale_checkin_coordinates()["scrubbed"], 0)

	def test_an_employee_who_leaves_loses_their_coordinates_at_once(self):
		recent = self._punch_with_version(1)
		frappe.set_user("Administrator")

		employee = frappe.get_doc("Employee", self.employee_name)
		employee.relieving_date = str(getdate())
		employee.status = "Left"
		employee.save(ignore_permissions=True)

		try:
			self.assertFalse(self._coordinates(recent).latitude)
			self.assertFalse(self._coordinates(recent).longitude)
			self.assertEqual(self._version_fields(recent), ["log_type"])
		finally:
			employee.reload()
			employee.status = "Active"
			employee.relieving_date = None
			employee.save(ignore_permissions=True)

	def test_a_punch_of_a_still_active_employee_keeps_its_coordinates(self):
		recent = self._punch_with_version(1)
		frappe.set_user("Administrator")

		employee = frappe.get_doc("Employee", self.employee_name)
		employee.designation = employee.designation or None
		employee.save(ignore_permissions=True)

		self.assertEqual(self._coordinates(recent).latitude, HERE[0])


class TestPunchIsolation(CheckinTestCase):
	"""P3-R7a: the generic routes are closed by the permission delta, so the
	portal method is the only way in -- and it never takes an employee."""

	def test_the_punch_is_always_the_callers_own(self):
		punch_my_checkin(HERE[0], HERE[1], "IN")

		rows = self._punches()
		self.assertEqual(len(rows), 1)
		self.assertEqual(
			frappe.db.get_value("Employee Checkin", rows[0].name, "employee"), self.employee_name
		)

	def test_an_employee_cannot_insert_a_punch_through_the_document_api(self):
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(
				{
					"doctype": "Employee Checkin",
					"employee": self.employee_name,
					"time": str(get_datetime(now_datetime())),
					"log_type": "IN",
				}
			).insert()
