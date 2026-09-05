import io
import os
import zipfile
from urllib.parse import quote

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


# --- per-user write limits (P2-U9 step 6, P2-R28) ---------------------------

# The policy, in one table, because three separate readers need the same
# numbers: `rate_limit_per_user` enforces them, `helixhr.preflight` checks
# that the running site has not been loosened below them, and the runbook
# quotes them. `(limit, seconds)`.
#
# These are the plan's numbers. Tighten freely; loosening one is a policy
# decision that preflight will FAIL on until this table is edited too.
# Roles whose holders work in Desk. Anyone holding one of these keeps
# Frappe's own landing page; everybody else with an active Employee record is
# sent to the portal. HR staff are employees too and hold the Employee role,
# so the rule cannot be "has the Employee role" -- it has to be "does not work
# in Desk".
DESK_ROLES = frozenset({"HR Manager", "HR User", "System Manager", "Administrator"})

PORTAL_HOME_PAGE = "helixhr"


def portal_home_page(user=None):
	"""Where this user lands after signing in.

	Registered as `get_website_user_home_page` in hooks.py, which Frappe calls
	with the user and consults before `role_home_page` and before Website
	Settings. `role_home_page` cannot express this rule: it matches the *first*
	entry in `frappe.get_roles()`, whose order is not defined, so mapping the
	Employee role would send an HR Manager to the portal on some sites and to
	Desk on others.

	Returning None means "no opinion" -- Frappe carries on down its own chain
	and a Desk user lands where they always did.

	Two things still win over this, by Frappe's design, and both are in
	docs/deployment.md: a `home_page` set on the Role doctype, and a
	`default_workspace` set on the User.
	"""
	user = user or frappe.session.user
	if user in ("Guest", "Administrator"):
		return None
	if set(frappe.get_roles(user)) & DESK_ROLES:
		return None
	if frappe.db.exists("Employee", {"user_id": user, "status": "Active"}):
		return PORTAL_HOME_PAGE
	return None


RATE_LIMIT_POLICY = {
	"update_my_profile": (20, 60),
	"save_my_week": (30, 60),
	"act_on_approval": (30, 60),
	"apply_for_leave": (20, 3600),
	"withdraw_my_leave": (20, 3600),
	"create_my_request": (10, 3600),
	"attach_to_my_request": (20, 3600),
	"mark_notifications_read": (60, 60),
}


def rate_limit_bounds(action):
	"""The `(limit, seconds)` actually in force for `action` on this site.

	A site may tighten a bound through site config without a code change --

	    bench --site <site> set-config helixhr_rate_limits '{"create_my_request": [5, 3600]}'

	-- because "adjust only from measured legitimate use" (P2-U9 step 6) is an
	operational decision, not a release. It may also *loosen* one, which is
	why `helixhr.preflight.check_rate_limits` re-derives every effective bound
	and FAILs on anything looser than `RATE_LIMIT_POLICY`.
	"""
	if action not in RATE_LIMIT_POLICY:
		raise KeyError(f"no rate-limit policy for {action!r}")
	overrides = frappe.conf.get("helixhr_rate_limits") or {}
	configured = overrides.get(action)
	if not configured:
		return RATE_LIMIT_POLICY[action]
	try:
		limit, seconds = int(configured[0]), int(configured[1])
	except (TypeError, ValueError, IndexError):
		# A malformed override is not permission to run unlimited.
		return RATE_LIMIT_POLICY[action]
	if limit < 1 or seconds < 1:
		return RATE_LIMIT_POLICY[action]
	return limit, seconds


def rate_limits_enforced():
	"""Whether the per-user limiter is live on this site.

	It is, everywhere except a site that has declared itself a test site with
	`allow_tests`. That gate exists because the limits and the test suites are
	otherwise mutually exclusive: the Python suite creates well over ten HR
	Requests as one user inside one run, and a second full Playwright pass
	inside the same minute re-trips the timesheet write limit -- both would
	fail on a limit that is doing exactly its job.

	It is safe *because* it is the same switch preflight refuses to see on a
	production site: `helixhr.preflight.check_test_mode` FAILs on `allow_tests`
	and the deploy gate exits non-zero, so no production site can reach this
	branch. Nothing here reads `frappe.flags.in_test`, which would leave the
	limiter unexercised by the suite and therefore unproven.

	`frappe.flags.helixhr_enforce_rate_limits` forces the limiter back on
	inside a test, which is how `TestPerUserRateLimits` proves the bound is
	real rather than asserting the bypass (P2-U9 step 6).
	"""
	if frappe.flags.get("helixhr_enforce_rate_limits"):
		return True
	return not frappe.utils.cint(frappe.conf.get("allow_tests"))


def rate_limit_per_user(action):
	"""A small per-user rate limit, independent of Frappe's built-in
	`rate_limit` decorator -- that decorator's own per-user mode keys off a
	named form_dict argument, not the session user, so it doesn't fit a
	method whose only argument is **kwargs. Keyed by session user (not IP)
	deliberately: one office network sharing an IP would otherwise share one
	bucket (KTD16).

	The bound comes from `RATE_LIMIT_POLICY` rather than the call site, so
	preflight can check the same number the code enforces (P2-U9 step 6)."""
	limit, seconds = rate_limit_bounds(action)
	if not rate_limits_enforced():
		return
	cache_key = _rate_limit_key(action, frappe.session.user)
	count = frappe.cache.incrby(cache_key, 1)
	if count == 1:
		frappe.cache.expire(cache_key, seconds)
	if count > limit:
		frappe.throw(
			_("You're doing that too often. Please wait a bit and try again."),
			frappe.RateLimitExceededError,
		)


def _rate_limit_key(action, user):
	"""`incrby`/`expire` are raw Redis calls -- unlike `set_value`, they do
	not go through RedisWrapper's own key prefixing -- so the site name is
	part of the key here, and `reset_rate_limit` has to delete exactly this
	string rather than call `delete_value`."""
	return f"helixhr:rate-limit:{frappe.local.site}:{action}:{user}"


def reset_rate_limit(action, user=None):
	"""Drop one user's bucket. For tests that deliberately exercise the
	limiter and must not leak a full bucket into the next test method."""
	frappe.cache.delete(_rate_limit_key(action, user or frappe.session.user))


# --- portal upload policy (P2-U9 step 5, P2-R28) ---------------------------

# 10MB, private, and five content types. Everything else is refused, and
# refused by *content* as well as by name -- an `.svg` renamed to `.png` is
# the whole point of the signature column.
#
# Deliberately not `frappe.handler.ALLOWED_MIMETYPES`, which this replaces:
# that list is Frappe's site-wide default and includes SVG, plain text and
# the legacy Office formats. A portal attachment is a document an employee
# sends to HR; none of those five belong in it.
UPLOAD_MAX_BYTES = 10 * 1024 * 1024

_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")

# extension -> (content type, accepted leading signatures, required OOXML part)
UPLOAD_POLICY = {
	".pdf": ("application/pdf", (b"%PDF-",), None),
	".png": ("image/png", (b"\x89PNG\r\n\x1a\n",), None),
	".jpg": ("image/jpeg", (b"\xff\xd8\xff",), None),
	".jpeg": ("image/jpeg", (b"\xff\xd8\xff",), None),
	".docx": (
		"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
		_ZIP_MAGIC,
		"word/document.xml",
	),
	".xlsx": (
		"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
		_ZIP_MAGIC,
		"xl/workbook.xml",
	),
}

ALLOWED_UPLOAD_EXTENSIONS = tuple(sorted(UPLOAD_POLICY))
ALLOWED_UPLOAD_TYPES = tuple(sorted({content_type for content_type, _, _ in UPLOAD_POLICY.values()}))

# What the employee is told. One sentence, the same one for every refusal
# reason that is about the file's *kind*, so a probe learns nothing from the
# wording about which check it tripped.
_UPLOAD_KIND_MESSAGE = "You can attach a PDF, a PNG or JPEG image, or a Word or Excel document."


def upload_extension(file_name):
	"""The lower-cased final extension of `file_name`, path components and
	any Windows trailing dot/space stripped."""
	base = os.path.basename((file_name or "").replace("\\", "/")).strip().rstrip(". ")
	return os.path.splitext(base)[1].lower()


def validate_portal_upload(file_name, content):
	"""Refuse anything the portal upload policy does not allow, by name and
	by content (P2-U9 step 5).

	Order matters: size first (cheapest, and the only refusal an employee can
	act on by choosing a smaller file), then extension, then the leading
	signature, then -- for the two OOXML types -- the container itself.

	The OOXML pass is what makes `.docm` renamed to `.docx` fail: a
	macro-enabled document carries `vbaProject.bin` inside the same zip, and
	a truncated or hand-made zip raises `BadZipFile` rather than being
	accepted as "well, it starts with PK".

	Returns the policy's content type, which the caller stores rather than
	trusting the browser's `Content-Type`.
	"""
	if not isinstance(content, bytes | bytearray):
		frappe.throw(_("That file couldn't be read. Pick it again."))
	if not content:
		frappe.throw(_("That file is empty. Pick another one."))
	if len(content) > UPLOAD_MAX_BYTES:
		frappe.throw(
			_("That file is bigger than {0} MB. Send a smaller one.").format(UPLOAD_MAX_BYTES // (1024 * 1024))
		)

	extension = upload_extension(file_name)
	if extension not in UPLOAD_POLICY:
		frappe.throw(_(_UPLOAD_KIND_MESSAGE))

	content_type, signatures, ooxml_part = UPLOAD_POLICY[extension]
	if not any(bytes(content).startswith(signature) for signature in signatures):
		# The name says one thing and the bytes say another.
		frappe.throw(_(_UPLOAD_KIND_MESSAGE))

	if ooxml_part:
		try:
			with zipfile.ZipFile(io.BytesIO(bytes(content))) as archive:
				names = set(archive.namelist())
		except (zipfile.BadZipFile, OSError):
			frappe.throw(_(_UPLOAD_KIND_MESSAGE))
		if "[Content_Types].xml" not in names or ooxml_part not in names:
			frappe.throw(_(_UPLOAD_KIND_MESSAGE))
		if any(name.lower().endswith("vbaproject.bin") for name in names):
			frappe.throw(_("Macro-enabled documents can't be attached. Save it without macros and try again."))

	return content_type


# --- response headers (P2-U9 steps 5 and 8) --------------------------------

# Frappe sets none of these itself (checked against frappe version-16: the
# only Content-Security-Policy in the codebase is the Web Form's own
# frame-ancestors header). They are set here rather than in the reverse
# proxy so that a site is not one nginx template away from having no
# security headers at all -- the proxy is welcome to set them too, and a
# proxy value wins because this hook never overwrites a header that is
# already present.
SECURITY_HEADERS = {
	"X-Content-Type-Options": "nosniff",
	"Referrer-Policy": "strict-origin-when-cross-origin",
	# The portal needs none of these, and Frappe Desk sets its own where it
	# does. Naming them denies them for this site's whole surface.
	"Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
	# frame-ancestors, not X-Frame-Options: the portal is never framed, and
	# CSP's directive is the one modern browsers honour. Deliberately not a
	# full CSP -- `script-src` would have to allow Desk's own inline
	# bootstrap and would be a lie the moment it did.
	"Content-Security-Policy": "frame-ancestors 'none'",
}

# Two years, subdomains included. Only ever sent over HTTPS: sending it on a
# plain-HTTP dev bench would pin localhost to HTTPS in the developer's own
# browser and break every other bench on that machine.
HSTS_HEADER = "max-age=63072000; includeSubDomains"


def set_security_headers(response=None, request=None):
	"""`after_request` hook. Adds the headers above, and forces a download
	disposition on portal-uploaded attachments (P2-U9 steps 5 and 8).

	Registered in `hooks.py`. It runs for every response Frappe produces --
	including `/private/files/...`, which is served before any whitelisted
	method is reached and so cannot be covered from `helixhr.api`.
	"""
	if response is None:
		return

	for header, value in SECURITY_HEADERS.items():
		response.headers.setdefault(header, value)

	if request is not None and getattr(request, "scheme", None) == "https":
		response.headers.setdefault("Strict-Transport-Security", HSTS_HEADER)

	_force_download_portal_attachment(response, request)


def _force_download_portal_attachment(response, request):
	"""A file an employee uploaded through the portal is a document to keep,
	never a page to render: served inline from the site's own origin, a
	crafted file would run in the site's security context.

	Scoped to files attached to HR Request -- the only doctype the portal
	uploads to -- so Desk's own inline previews of everything else are
	untouched. Frappe's `FORCE_DOWNLOAD_EXTENSIONS` already covers SVG,
	HTML and XML for every site; the upload policy refuses those outright,
	and this covers the five types that policy does allow.
	"""
	path = getattr(request, "path", "") or ""
	if not path.startswith("/private/files/"):
		return
	try:
		# Every File row on this URL, not the first one Frappe happens to
		# return: Frappe reuses one `file_url` across rows with identical
		# content, so two employees uploading the same PDF share it and a
		# single-row read can answer with somebody else's attachment. Any
		# row saying HR Request is enough to force the download.
		attached = frappe.get_all("File", filters={"file_url": path}, pluck="attached_to_doctype")
	except Exception:
		# after_request runs outside the request's own error handling; a
		# lookup failure must never replace a served file with a 500.
		return
	if "HR Request" not in attached:
		return

	filename = os.path.basename(path)
	response.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"
