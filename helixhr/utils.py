import frappe
from frappe import _

# The only Employee fields the portal lets an employee change themselves
# (R9). Everything else on Employee sits behind permlevel 1 or 2 (U5
# fixtures) -- this list is a second, independent gate in front of
# `update_my_profile` so a caller can never widen what gets written just by
# adding another keyword argument.
PROFILE_EDITABLE_FIELDS = (
	"cell_number",
	"personal_email",
	"current_address",
	"permanent_address",
	"person_to_be_contacted",
	"emergency_phone_number",
	"relation",
)


def get_week_bounds(any_date):
	"""Monday..Sunday for the week containing `any_date` (KTD10 -- one
	week equals one Timesheet, always Monday to Sunday regardless of the
	site's own week-start setting, so week identity never depends on
	site config)."""
	from frappe.utils import add_days, getdate

	date = getdate(any_date)
	monday = add_days(date, -date.weekday())
	sunday = add_days(monday, 6)
	return monday, sunday


def get_manager_user(employee):
	"""The Frappe User of `employee`'s manager (Employee.reports_to), or
	None if there isn't one. Two hops: reports_to is an Employee id, not a
	User -- the share/guard/approvals code all needs the User."""
	reports_to = frappe.db.get_value("Employee", employee, "reports_to")
	if not reports_to:
		return None
	return frappe.db.get_value("Employee", reports_to, "user_id")


def rate_limit_per_user(action, limit, seconds):
	"""A small per-user rate limit, independent of Frappe's built-in
	`rate_limit` decorator -- that decorator's own per-user mode keys off a
	named form_dict argument, not the session user, so it doesn't fit a
	method whose only argument is **kwargs. Keyed by session user (not IP)
	deliberately: one office network sharing an IP would otherwise share one
	bucket (KTD16)."""
	cache_key = f"helixhr:rate-limit:{action}:{frappe.session.user}"
	count = frappe.cache.incrby(cache_key, 1)
	if count == 1:
		frappe.cache.expire(cache_key, seconds)
	if count > limit:
		frappe.throw(
			_("You're doing that too often. Please wait a bit and try again."),
			frappe.RateLimitExceededError,
		)
