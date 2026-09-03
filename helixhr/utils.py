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
