import io
import os
import zipfile
from contextlib import contextmanager
from urllib.parse import quote

import frappe
from frappe import _

# The request category the Profile page files corrections under. Seeded by
# `patches/v1_0/seed_profile_correction_category`; HR may reroute or retire it.
PROFILE_CORRECTION_CATEGORY = "Profile correction"

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

# Plan 2026-10-02-001 U13 / KTD14: the HR-locked Employee fields an employee
# may *propose* a new value for through a "Profile correction" request, with
# the plain-language label the notices and refusals use. Bank details only;
# each further field is an HR policy call, not an implementation choice.
PROFILE_CORRECTABLE_FIELDS = {
	"bank_name": "bank name",
	"bank_ac_no": "bank account number",
	"iban": "IBAN",
}
# R30: Done on a correction is refused this long after a personal email change.
CORRECTION_EMAIL_HOLD_HOURS = 72


# --- The employee's own profile (plan 2026-09-29-001, U3) -----------------
#
# Everything `api.get_my_profile` may return, by tab, named field by field.
# The projection reads HR-only (permlevel 2) fields on the owner's behalf, so
# this allow-list and `PROFILE_MASKED_FIELDS` are the whole boundary: a field
# is on the page only if it is named here. Fields a site does not have (the
# India payroll fields, say) are skipped. Never here: `ctc`, salary currency,
# payroll cost center, advance accounts, health details, exit fields.
PROFILE_SECTION_FIELDS = {
	"personal": (
		"salutation",
		"first_name",
		"middle_name",
		"last_name",
		"employee_number",
		"gender",
		"date_of_birth",
		"marital_status",
		"blood_group",
		"date_of_joining",
		"status",
	),
	"job": (
		"company",
		"department",
		"designation",
		"grade",
		"employment_type",
		"branch",
		"reports_to",
		"final_confirmation_date",
		"contract_end_date",
		"notice_number_of_days",
		"date_of_retirement",
		"default_shift",
		"holiday_list",
		"leave_approver",
		"expense_approver",
		"shift_request_approver",
	),
	"contact": (
		*PROFILE_EDITABLE_FIELDS,
		"company_email",
		"prefered_contact_email",
		"current_accommodation_type",
		"permanent_accommodation_type",
	),
	"history": ("family_background",),
	"bank": (
		"salary_mode",
		"bank_name",
		"bank_ac_no",
		"ifsc_code",
		"micr_code",
		"iban",
		"pan_number",
		"provident_fund_account",
		"passport_number",
		"date_of_issue",
		"valid_upto",
		"place_of_issue",
		"health_insurance_provider",
		"health_insurance_no",
	),
}

# Child tables, by the tab they render on, with the columns that may leave
# the server. External Work History's `salary` and `contact` are deliberately
# absent.
PROFILE_SECTION_TABLES = {
	"job": {"internal_work_history": ("branch", "department", "designation", "from_date", "to_date")},
	"history": {
		"education": ("school_univ", "qualification", "level", "year_of_passing", "class_per", "maj_opt_subj"),
		"external_work_history": ("company_name", "designation", "address", "total_experience"),
	},
}

# Link-to-User fields, shown by the person's name rather than their login.
PROFILE_USER_LINK_FIELDS = frozenset({"leave_approver", "expense_approver", "shift_request_approver"})

# Identifiers that reach the browser only as their last four characters.
PROFILE_MASKED_FIELDS = frozenset(
	{"bank_ac_no", "iban", "pan_number", "provident_fund_account", "passport_number", "health_insurance_no"}
)

# Plain-language labels where Frappe's own reads as Frappe (design-system
# copy rule) or is simply unclear to an employee.
PROFILE_LABELS = {
	"branch": "Location",
	"bank_ac_no": "Bank account",
	"prefered_contact_email": "Preferred contact email",
	"current_accommodation_type": "Current address is",
	"permanent_accommodation_type": "Permanent address is",
	"valid_upto": "Valid until",
	"notice_number_of_days": "Notice period (days)",
	"final_confirmation_date": "Confirmation date",
	"school_univ": "School / university",
	"class_per": "Grade / percentage",
	"maj_opt_subj": "Subjects",
	"company_name": "Company",
	"health_insurance_no": "Health insurance number",
}


def mask_identifier(value):
	"""`••••1234` for an identifier, never more than its last four
	characters; one of four characters or fewer is masked whole. None stays
	None so the page can say "Not recorded"."""
	text = "" if value is None else str(value).strip()
	if not text:
		return None
	return "••••" if len(text) <= 4 else f"••••{text[-4:]}"


# --- Portal email events and the HelixHR template sandbox (plan 2026-10-02-001 U8) ---
#
# KTD6: `frappe.render_template(restrict_globals=True)` is not a sandbox -- its
# safe globals still read any record, it prints unknown names literally
# (`DebugUndefined`), and `guess_is_path` loads a one-line `*.html` string as
# a file. Every editable email therefore renders through the environment
# below: jinja2's `ImmutableSandboxedEnvironment` (jinja2 ships with Frappe),
# empty globals, `StrictUndefined`, no loader, `from_string` only, and a
# context holding nothing but the event's declared variables as plain values.
#
# KTD7: event definitions live here; a `HelixHR Message Template` row exists
# only for a customised (`is_enabled=1`) or switched-off (`is_enabled=0`)
# event. No row means the default below. Locked events ignore Off, keep their
# developer-owned subject and core sentence, and use the row's body only as an
# optional extra paragraph.

SHARED_TEMPLATE_VARIABLES = {
	"company": ("Your company's name", "HelixHR Demo Ltd"),
	"portal_url": ("Link to the portal", "https://hr.example.com/helixhr"),
	"logo_url": ("Company logo address (may be empty)", "https://hr.example.com/files/logo.png"),
	"recipient_first_name": ("First name of the person receiving the email", "Priya"),
}

_LEAVE_VARIABLES = {
	"employee_name": ("Who asked for the leave", "Arjun Rao"),
	"leave_type": ("Leave type", "Casual Leave"),
	"from_date": ("First day", "12-10-2026"),
	"to_date": ("Last day", "14-10-2026"),
	"days": ("Number of days", 3),
	"half_day": ("Whether it is a half day", False),
	"reason": ("The employee's reason (may be empty)", "Family function"),
	"balance_after": ("Balance left if approved", 9),
	"action_url": ("Link to open the request", "https://hr.example.com/helixhr/approvals"),
}
_DECISION = {
	"approver_name": ("Who decided", "Meera Shah"),
	"decision_note": ("The approver's note (may be empty)", "Enjoy the break"),
}
_DATES = {key: _LEAVE_VARIABLES[key] for key in ("leave_type", "from_date", "to_date")}
_SAMPLE_ITEMS = [
	{
		"kind": "Leave",
		"title": "Casual Leave, 12-10-2026",
		"employee_name": "Arjun Rao",
		"age_days": 3,
		"url": "https://hr.example.com/helixhr/approvals",
	},
]

# Event key -> label, audience, variables {name: (description, sample)},
# default subject and body, optional action label, locked flag and (locked
# only) the developer-owned core sentence. The appendix of the plan is the
# catalog; U9/U11/U13 wire the sends.
NOTIFICATION_EVENTS = {
	"leave_submitted": {
		"label": "New leave request",
		"audience": "Approver",
		"variables": _LEAVE_VARIABLES,
		"subject": "Leave request from {{ employee_name }}: {{ leave_type }}",
		"body": (
			"<p>Hi {{ recipient_first_name }},</p>"
			"<p>{{ employee_name }} asked for {{ leave_type }}: {{ days }} day(s),"
			" {{ from_date }} to {{ to_date }}{% if half_day %} (half day){% endif %}.</p>"
			"{% if reason %}<p>Reason: {{ reason }}</p>{% endif %}"
			"{% if balance_after %}<p>Balance after approval: {{ balance_after }}</p>{% endif %}"
		),
		"action_label": "Review request",
	},
	"leave_for_hr": {
		"label": "Leave waiting for HR",
		"audience": "HR",
		"variables": {**_LEAVE_VARIABLES, "manager_name": ("The employee's manager", "Meera Shah")},
		"subject": "Leave for HR: {{ employee_name }}, {{ leave_type }}",
		"body": (
			"<p>Hi {{ recipient_first_name }},</p>"
			"<p>{{ employee_name }}'s {{ leave_type }} ({{ days }} day(s), {{ from_date }} to {{ to_date }})"
			" is waiting for HR.{% if manager_name %} Manager: {{ manager_name }}.{% endif %}</p>"
			"{% if reason %}<p>Reason: {{ reason }}</p>{% endif %}"
		),
		"action_label": "Review request",
	},
	"leave_approved": {
		"label": "Leave approved",
		"audience": "Employee",
		"variables": {
			**_DATES,
			"days": _LEAVE_VARIABLES["days"],
			**_DECISION,
			"balance_after": _LEAVE_VARIABLES["balance_after"],
		},
		"subject": "Your {{ leave_type }} was approved",
		"body": (
			"<p>Hi {{ recipient_first_name }},</p>"
			"<p>{{ approver_name }} approved your {{ leave_type }}: {{ days }} day(s),"
			" {{ from_date }} to {{ to_date }}.</p>"
			"{% if decision_note %}<p>Note: {{ decision_note }}</p>{% endif %}"
			"{% if balance_after %}<p>Balance after: {{ balance_after }}</p>{% endif %}"
		),
	},
	"leave_rejected": {
		"label": "Leave declined",
		"audience": "Employee",
		"variables": {**_DATES, **_DECISION},
		"subject": "Your {{ leave_type }} was declined",
		"body": (
			"<p>Hi {{ recipient_first_name }},</p>"
			"<p>{{ approver_name }} declined your {{ leave_type }} for {{ from_date }} to {{ to_date }}.</p>"
			"{% if decision_note %}<p>Note: {{ decision_note }}</p>{% endif %}"
		),
	},
	"leave_sent_back": {
		"label": "Leave sent back",
		"audience": "Employee",
		"variables": {**_DATES, **_DECISION, "action_url": _LEAVE_VARIABLES["action_url"]},
		"subject": "Your {{ leave_type }} needs a change",
		"body": (
			"<p>Hi {{ recipient_first_name }},</p>"
			"<p>{{ approver_name }} sent back your {{ leave_type }} for {{ from_date }} to {{ to_date }}.</p>"
			"{% if decision_note %}<p>Note: {{ decision_note }}</p>{% endif %}"
		),
		"action_label": "Open request",
	},
	"leave_cancelled": {
		"label": "Leave cancelled",
		"audience": "Employee",
		"variables": {
			**_DATES,
			"days": _LEAVE_VARIABLES["days"],
			"cancelled_by": ("Who cancelled it", "Meera Shah"),
		},
		"subject": "Your {{ leave_type }} was cancelled",
		"body": (
			"<p>Hi {{ recipient_first_name }},</p>"
			"<p>{{ cancelled_by }} cancelled your {{ leave_type }}: {{ days }} day(s),"
			" {{ from_date }} to {{ to_date }}.</p>"
		),
	},
	"timesheet_for_hr": {
		"label": "Timesheet waiting for HR",
		"audience": "HR",
		"variables": {
			"employee_name": _LEAVE_VARIABLES["employee_name"],
			"week_label": ("The week", "5 to 11 Oct 2026"),
			"total_hours": ("Hours on the timesheet", 40),
			"action_url": _LEAVE_VARIABLES["action_url"],
		},
		"subject": "Timesheet for HR: {{ employee_name }}, {{ week_label }}",
		"body": (
			"<p>Hi {{ recipient_first_name }},</p>"
			"<p>{{ employee_name }}'s timesheet for {{ week_label }} ({{ total_hours }} hours) is waiting for HR.</p>"
		),
		"action_label": "Review timesheet",
	},
	"timesheet_decided": {
		"label": "Timesheet decided",
		"audience": "Employee",
		"variables": {
			"week_label": ("The week", "5 to 11 Oct 2026"),
			"total_hours": ("Hours on the timesheet", 40),
			"state": ("What happened to it", "approved"),
			**_DECISION,
		},
		"subject": "Your timesheet for {{ week_label }} was {{ state }}",
		"body": (
			"<p>Hi {{ recipient_first_name }},</p>"
			"<p>{{ approver_name }} {{ state }} your timesheet for {{ week_label }} ({{ total_hours }} hours).</p>"
			"{% if decision_note %}<p>Note: {{ decision_note }}</p>{% endif %}"
		),
	},
	"timesheet_recalled": {
		"label": "Week recalled",
		"audience": "Approver",
		"variables": {
			"employee_name": _LEAVE_VARIABLES["employee_name"],
			"week_label": ("The week", "5 to 11 Oct 2026"),
		},
		"subject": "{{ employee_name }} recalled their week: {{ week_label }}",
		"body": (
			"<p>Hi {{ recipient_first_name }},</p>"
			"<p>{{ employee_name }} took back their timesheet for {{ week_label }} before you decided it."
			" It is no longer waiting for you.</p>"
		),
	},
	"timesheet_change_requested": {
		"label": "Change request on an approved week",
		"audience": "Approver",
		"variables": {
			"employee_name": _LEAVE_VARIABLES["employee_name"],
			"week_label": ("The week", "5 to 11 Oct 2026"),
			"comment": ("What the employee asked to change", "Tuesday should be 6 hours, not 2"),
			"action_url": _LEAVE_VARIABLES["action_url"],
		},
		"subject": "Change request: {{ employee_name }}, {{ week_label }}",
		"body": (
			"<p>Hi {{ recipient_first_name }},</p>"
			"<p>{{ employee_name }} asked to change their approved timesheet for {{ week_label }}:</p>"
			"<p>“{{ comment }}”</p>"
		),
		"action_label": "Review request",
	},
	"timesheet_change_decided": {
		"label": "Change request decided",
		"audience": "Employee",
		"variables": {
			"week_label": ("The week", "5 to 11 Oct 2026"),
			"state": ("What happened to it", "accepted"),
			**_DECISION,
		},
		"subject": "Your change request for {{ week_label }} was {{ state }}",
		"body": (
			"<p>Hi {{ recipient_first_name }},</p>"
			"{% if state == 'accepted' %}"
			"<p>{{ approver_name }} accepted your change request for {{ week_label }}."
			" The week is back with you as a draft -- edit it and send it again.</p>"
			"{% else %}"
			"<p>{{ approver_name }} declined your change request for {{ week_label }}."
			" The week stays as it was.</p>"
			"{% if decision_note %}<p>Note: {{ decision_note }}</p>{% endif %}"
			"{% endif %}"
		),
	},
	"attendance_for_hr": {
		"label": "Attendance request waiting for HR",
		"audience": "HR",
		"variables": {
			"employee_name": _LEAVE_VARIABLES["employee_name"],
			"date_range": ("The dates", "12-10-2026 to 13-10-2026"),
			"reason": ("The employee's reason (may be empty)", "On a client visit"),
			"action_url": _LEAVE_VARIABLES["action_url"],
		},
		"subject": "Attendance request for HR: {{ employee_name }}, {{ date_range }}",
		"body": (
			"<p>Hi {{ recipient_first_name }},</p>"
			"<p>{{ employee_name }}'s attendance request for {{ date_range }} is waiting for HR.</p>"
			"{% if reason %}<p>Reason: {{ reason }}</p>{% endif %}"
		),
		"action_label": "Review request",
	},
	"attendance_decided": {
		"label": "Attendance request decided",
		"audience": "Employee",
		"variables": {
			"date_range": ("The dates", "12-10-2026 to 13-10-2026"),
			"state": ("What happened to it", "approved"),
			**_DECISION,
		},
		"subject": "Your attendance request for {{ date_range }} was {{ state }}",
		"body": (
			"<p>Hi {{ recipient_first_name }},</p>"
			"<p>{{ approver_name }} {{ state }} your attendance request for {{ date_range }}.</p>"
			"{% if decision_note %}<p>Note: {{ decision_note }}</p>{% endif %}"
		),
	},
	"request_arrival": {
		"label": "New request",
		"audience": "Route role",
		"variables": {
			"employee_name": _LEAVE_VARIABLES["employee_name"],
			"category": ("Request category", "IT / Asset"),
			"subject": ("The request's subject", "Laptop replacement"),
			"action_url": ("Link to the requests queue", "https://hr.example.com/helixhr/requests"),
		},
		"subject": "New {{ category }} request: {{ subject }}",
		"body": "<p>A new {{ category }} request, “{{ subject }}”, is waiting for you.</p>",
		"action_label": "Open requests",
	},
	"request_status_changed": {
		"label": "Request status changed",
		"audience": "Employee",
		"variables": {
			"category": ("Request category", "IT / Asset"),
			"subject": ("The request's subject", "Laptop replacement"),
			"state": ("What happened to it", "is done"),
			"reason": ("HR's reason (may be empty)", "Replaced under warranty"),
		},
		"subject": "Your request {{ state }}: {{ subject }}",
		"body": "{% if reason %}{{ reason }}{% endif %}",
	},
	"request_reply": {
		"label": "Reply on a request",
		"audience": "Route role",
		"variables": {
			"employee_name": _LEAVE_VARIABLES["employee_name"],
			"category": ("Request category", "IT / Asset"),
			"subject": ("The request's subject", "Laptop replacement"),
			"reply_excerpt": ("The start of the reply", "Thanks, the old one is in the drawer."),
			"action_url": ("Link to the request", "https://hr.example.com/helixhr/requests"),
		},
		"subject": "{{ employee_name }} replied: {{ subject }}",
		"body": (
			"<p>Hi {{ recipient_first_name }},</p>"
			"<p>{{ employee_name }} replied on their {{ category }} request “{{ subject }}”:</p>"
			"<blockquote>{{ reply_excerpt }}</blockquote>"
		),
		"action_label": "Open request",
	},
	"approval_overdue_digest": {
		"label": "Overdue approvals digest",
		"audience": "Approver",
		"variables": {
			"items": ("Overdue items: kind, title, employee_name, age_days, url", _SAMPLE_ITEMS),
			"count": ("How many items are overdue", 1),
		},
		"subject": "{{ count }} approval(s) waiting on you",
		"body": (
			"<p>Hi {{ recipient_first_name }},</p>"
			"<p>These are waiting on you longer than they should:</p><ul>"
			'{% for item in items %}<li>{{ item.kind }}: <a href="{{ item.url }}">{{ item.title }}</a>'
			" ({{ item.employee_name }}, {{ item.age_days }} day(s))</li>{% endfor %}</ul>"
		),
	},
	"hr_overdue_summary": {
		"label": "Overdue summary for HR",
		"audience": "HR",
		"variables": {
			"owners": (
				"Approvers with overdue items: owner_name, inactive, items",
				[{"owner_name": "Meera Shah", "inactive": False, "items": _SAMPLE_ITEMS}],
			),
			"count": ("How many items are overdue", 1),
		},
		"subject": "{{ count }} overdue approval(s) across the company",
		"body": (
			"<p>Hi {{ recipient_first_name }},</p>"
			"{% for owner in owners %}<p><strong>{{ owner.owner_name }}</strong>"
			"{% if owner.inactive %} (inactive){% endif %}</p><ul>"
			"{% for item in owner.items %}<li>{{ item.kind }}: {{ item.title }}"
			" ({{ item.age_days }} day(s))</li>{% endfor %}</ul>{% endfor %}"
		),
	},
	"bank_change_requested": {
		"label": "Bank detail change requested",
		"audience": "Security",
		"locked": True,
		"variables": {
			"field_label": ("Which detail", "Bank account number"),
			"masked_new_value": ("The new value, masked", "••••1234"),
			"requested_on": ("When it was requested", "12-10-2026 10:30"),
		},
		"subject": "Security notice: a change to your {{ field_label }} was requested",
		"core": (
			"<p>Hi {{ recipient_first_name }},</p>"
			"<p>On {{ requested_on }} someone asked to change your {{ field_label }} to {{ masked_new_value }}."
			" If this was not you, contact HR immediately.</p>"
		),
		"body": "",
	},
	"bank_change_applied": {
		"label": "Bank detail change applied",
		"audience": "Security",
		"locked": True,
		"variables": {
			"field_label": ("Which detail", "Bank account number"),
			"masked_new_value": ("The new value, masked", "••••1234"),
			"applied_by": ("Who applied it", "Meera Shah"),
			"applied_on": ("When it was applied", "13-10-2026 09:00"),
		},
		"subject": "Security notice: your {{ field_label }} was changed",
		"core": (
			"<p>Hi {{ recipient_first_name }},</p>"
			"<p>On {{ applied_on }} {{ applied_by }} changed your {{ field_label }} to {{ masked_new_value }}."
			" If you did not ask for this, contact HR immediately.</p>"
		),
		"body": "",
	},
}

# Hard caps on what a template may cost, enforced on every compile and render:
# the Notification Manager edits these, and a template is not allowed to
# become a CPU or memory bomb through nested loops, repetition or padding.
_TEMPLATE_OUTPUT_MAX = 200_000
_TEMPLATE_LOOP_DEPTH_MAX = 3
_TEMPLATE_REPEAT_MAX = 100
# Only filters that cannot grow output much beyond their input. Notably
# absent: center/indent/wordwrap/format (padding to any width), replace
# (exponential when chained), safe/xmlattr/attr, and anything callable.
_TEMPLATE_FILTERS = frozenset(
	(
		"abs", "capitalize", "count", "d", "default", "e", "escape", "first", "float", "int",
		"join", "last", "length", "lower", "max", "min", "round", "sort", "string", "striptags",
		"sum", "title", "trim", "truncate", "unique", "upper", "urlencode", "wordcount",
	)
)  # fmt: skip


class TemplateRejected(Exception):
	"""A template that may not be saved or rendered; the message names why."""


def _template_envs():
	"""The (body, subject) sandbox environments, built once per process.

	Body: autoescape on. Subject: plain text (an email header, never HTML).
	Neither has a loader, so `{% include %}`/`{% extends %}`/`{% import %}`
	cannot reach a file, and both start with *empty* globals -- no `range`,
	`cycler`, `joiner`, `namespace`, `lipsum` or `dict`, let alone `frappe`.
	"""
	envs = getattr(_template_envs, "cached", None)
	if envs:
		return envs
	from jinja2 import StrictUndefined
	from jinja2.exceptions import SecurityError
	from jinja2.sandbox import ImmutableSandboxedEnvironment

	class _Env(ImmutableSandboxedEnvironment):
		intercepted_binops = frozenset(("*", "**"))

		def getattr(self, obj, attribute):
			# Context dicts are data: `owner.items` is the key, never the
			# `dict.items` method, and no dict method is reachable at all.
			if isinstance(obj, dict):
				if attribute in obj:
					return obj[attribute]
				return self.undefined(obj=obj, name=attribute)
			return super().getattr(obj, attribute)

		def call_binop(self, context, operator, left, right):
			if operator == "**":
				raise SecurityError("'**' is not allowed in a message template")
			if isinstance(left, int | float) and isinstance(right, int | float):
				return left * right
			if max(abs(left) if isinstance(left, int) else 0, abs(right) if isinstance(right, int) else 0) > (
				_TEMPLATE_REPEAT_MAX
			):
				raise SecurityError("repetition is limited in a message template")
			return left * right

	def build(autoescape):
		# `finalize`: an empty variable prints nothing, never "None".
		env = _Env(
			undefined=StrictUndefined,
			autoescape=autoescape,
			loader=None,
			finalize=lambda value: "" if value is None else value,
		)
		env.globals.clear()
		env.filters = {name: env.filters[name] for name in _TEMPLATE_FILTERS}
		return env

	_template_envs.cached = (build(True), build(False))
	return _template_envs.cached


def event_variables(event_key):
	"""Every variable `event_key`'s template may name: the shared four first."""
	return {**SHARED_TEMPLATE_VARIABLES, **NOTIFICATION_EVENTS[event_key]["variables"]}


def sample_context(event_key):
	return {name: sample for name, (_description, sample) in event_variables(event_key).items()}


def _plain(value):
	"""A context value as plain data: str/number/bool/None, lists and dicts of
	those. Dates and Decimals become strings; anything else -- a Document, an
	object with methods -- is refused so a template can never reach a record."""
	if value is None or isinstance(value, bool | int | float | str):
		return value
	if isinstance(value, list | tuple):
		return [_plain(item) for item in value]
	if type(value) is dict or isinstance(value, frappe._dict):
		return {str(key): _plain(item) for key, item in value.items()}
	import datetime
	import decimal

	if isinstance(value, datetime.date | datetime.time | decimal.Decimal):
		return str(value)
	raise TypeError(f"{type(value).__name__} is not allowed in a message template context")


def _escape_values(value):
	"""Recursively HTML-escape every string (body context), as `Markup` so
	autoescape does not escape it twice and `|e` is a no-op."""
	from markupsafe import escape

	if isinstance(value, str):
		return escape(value)
	if isinstance(value, list):
		return [_escape_values(item) for item in value]
	if isinstance(value, dict):
		return {key: _escape_values(item) for key, item in value.items()}
	return value


def _subject_values(value):
	"""Subject context: raw text with line breaks removed (a header)."""
	if isinstance(value, str):
		return " ".join(value.splitlines())
	if isinstance(value, list):
		return [_subject_values(item) for item in value]
	if isinstance(value, dict):
		return {key: _subject_values(item) for key, item in value.items()}
	return value


def _build_context(event_key, context, for_subject):
	declared = event_variables(event_key)
	plain = {name: _plain((context or {}).get(name)) for name in declared}
	if for_subject:
		return _subject_values(plain)
	return _escape_values(plain)


def _check_template_shape(env, source):
	"""Parse `source` and refuse every construct this engine does not need:
	function/method calls, macros, `set`, includes/imports/extends, filter
	blocks, recursive loops, loops over anything but a context value, and
	loops nested deeper than `_TEMPLATE_LOOP_DEPTH_MAX`. Returns the AST."""
	from jinja2 import nodes

	ast = env.parse(source)
	allowed_statements = (nodes.Output, nodes.If, nodes.For)

	def walk(node, depth):
		if isinstance(node, nodes.Stmt) and not isinstance(node, allowed_statements):
			raise TemplateRejected(
				_("Line {0}: only {{% if %}} and {{% for %}} blocks are allowed.").format(node.lineno)
			)
		if isinstance(node, nodes.Call):
			raise TemplateRejected(_("Line {0}: calling functions is not allowed.").format(node.lineno))
		if isinstance(node, nodes.Filter) and node.name not in _TEMPLATE_FILTERS:
			raise TemplateRejected(
				_("Line {0}: the filter “{1}” is not allowed.").format(node.lineno, node.name)
			)
		if isinstance(node, nodes.For):
			iterable = node.iter
			while isinstance(iterable, nodes.Getattr | nodes.Getitem):
				iterable = iterable.node
			if node.recursive or not isinstance(iterable, nodes.Name):
				raise TemplateRejected(
					_("Line {0}: a loop can only go over one of this message's lists.").format(node.lineno)
				)
			depth += 1
			if depth > _TEMPLATE_LOOP_DEPTH_MAX:
				raise TemplateRejected(_("Line {0}: loops are nested too deeply.").format(node.lineno))
		for child in node.iter_child_nodes():
			walk(child, depth)

	walk(ast, 0)
	return ast


def _compile(env, source):
	_check_template_shape(env, source)
	return env.from_string(source)


def _run(template, context):
	"""Render with an output cap, so a loop cannot produce an unbounded email."""
	out, size = [], 0
	for chunk in template.generate(context):
		size += len(chunk)
		if size > _TEMPLATE_OUTPUT_MAX:
			raise TemplateRejected(_("This message is too long."))
		out.append(chunk)
	return "".join(out)


def _error_line(exc):
	import traceback

	lineno = getattr(exc, "lineno", None)
	if lineno:
		return lineno
	for frame in reversed(traceback.extract_tb(exc.__traceback__)):
		if frame.filename == "<template>":
			return frame.lineno
	return None


def validate_message_template(event_key, subject, body):
	"""Refuse a template that may not be saved (R17): an unknown event, a
	construct outside the allowed shape, a syntax error (with its line), a
	variable outside the event's list (named), or one that errors against the
	event's sample data (with its line). Raises `TemplateRejected`."""
	from jinja2 import TemplateSyntaxError, meta

	if event_key not in NOTIFICATION_EVENTS:
		raise TemplateRejected(_("Not a valid message."))
	declared = set(event_variables(event_key))
	body_env, subject_env = _template_envs()
	samples = sample_context(event_key)
	for label, env, source, for_subject in (
		(_("Subject"), subject_env, subject, True),
		(_("Body"), body_env, body, False),
	):
		if not source:
			continue
		try:
			ast = _check_template_shape(env, source)
		except TemplateSyntaxError as exc:
			raise TemplateRejected(_("{0}, line {1}: {2}").format(label, exc.lineno, exc.message)) from exc
		except TemplateRejected as exc:
			raise TemplateRejected(f"{label}: {exc}") from exc
		unknown = sorted(meta.find_undeclared_variables(ast) - declared)
		if unknown:
			raise TemplateRejected(
				_("{0}: “{1}” is not a variable this message has.").format(label, ", ".join(unknown))
			)
		try:
			_run(env.from_string(source), _build_context(event_key, samples, for_subject))
		except Exception as exc:
			raise TemplateRejected(
				_("{0}, line {1}: {2}").format(label, _error_line(exc) or "?", exc)
			) from exc


_LAYOUT_PATH = ("templates", "emails", "helixhr_layout.html")


def _layout_template():
	"""The developer-owned branded layout (R19), compiled once by the same
	sandbox (autoescape on) so nothing it prints escapes unescaped."""
	cached = getattr(_layout_template, "cached", None)
	if cached is None:
		with open(frappe.get_app_path("helixhr", *_LAYOUT_PATH), encoding="utf-8") as handle:
			cached = _template_envs()[0].from_string(handle.read())
		_layout_template.cached = cached
	return cached


def _default_context():
	company = frappe.defaults.get_global_default("company") or ""
	logo = frappe.db.get_value("Company", company, "company_logo") if company else None
	return {
		"company": company,
		"portal_url": frappe.utils.get_url("/helixhr"),
		"logo_url": frappe.utils.get_url(logo) if logo else "",
	}


def render_message(event_key, context, source=None):
	"""Render `event_key` for one send: `{"subject", "content", "html"}`, or
	`None` when the event is switched off (and not locked).

	`source` (`{"subject", "body"}`) stands in for the saved row -- the
	Email templates preview and test send (U10) render an unsaved draft. The
	caller validates it first, so the default fallback below never hides it.

	The saved template is used when there is one; if it fails on real data the
	default renders instead, the failure goes to the Error Log, and nothing is
	left in `message_log` for the user whose action triggered the send (R17).
	`html` is `content` wrapped in the branded layout."""
	from markupsafe import Markup

	event = NOTIFICATION_EVENTS[event_key]
	locked = bool(event.get("locked"))
	context = {**_default_context(), **(context or {})}
	if source is not None:
		row = frappe._dict(subject=source.get("subject"), body=source.get("body"), is_enabled=1)
	else:
		row = frappe.db.get_value(
			"HelixHR Message Template", event_key, ["subject", "body", "is_enabled"], as_dict=True
		)
	if row and not row.is_enabled and not locked:
		return None

	body_env, subject_env = _template_envs()
	body_ctx = _build_context(event_key, context, for_subject=False)
	subject_ctx = _build_context(event_key, context, for_subject=True)

	def render(subject_source, body_source):
		return (
			" ".join(_run(_compile(subject_env, subject_source), subject_ctx).split()),
			_run(_compile(body_env, body_source), body_ctx) if body_source else "",
		)

	subject, content = None, None
	if row and row.is_enabled:
		messages = len(frappe.local.message_log or [])
		try:
			subject, content = render(event["subject"] if locked else row.subject, row.body or "")
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"HelixHR message template {event_key} failed")
			while len(frappe.local.message_log or []) > messages:
				frappe.clear_last_message()
			subject = None
	if subject is None:
		subject, content = render(event["subject"], "" if locked else event["body"])

	core = Markup(_run(_compile(body_env, event["core"]), body_ctx)) if locked else None
	action_url = context.get("action_url")
	html = _run(
		_layout_template(),
		{
			"subject": subject,
			"core": core,
			"content": Markup(content),
			"action_url": action_url,
			"action_label": event.get("action_label") or _("Open HelixHR"),
			**{name: body_ctx[name] for name in ("company", "logo_url", "portal_url")},
		},
	)
	return {"subject": subject, "content": content, "html": html}


def send_notification(event_key, recipients, context, reference_doctype=None, reference_name=None):
	"""Render and queue `event_key` to each recipient (KTD8). One mail per
	recipient so `recipient_first_name` is theirs. Never raises: a mail
	failure must not fail the write that triggered it (P5-KTD9)."""
	for recipient in recipients or ():
		try:
			first_name = frappe.db.get_value("User", recipient, "first_name")
			message = render_message(event_key, {"recipient_first_name": first_name, **(context or {})})
			if message is None:
				return
			frappe.sendmail(
				recipients=[recipient],
				subject=message["subject"],
				message=message["html"],
				reference_doctype=reference_doctype,
				reference_name=reference_name,
			)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"HelixHR {event_key} mail failed")


# The named, deliberately short field sets P5-KTD12 hands to the portal's
# configuration screens -- not every permlevel-0 field the underlying HRMS
# doctype ships. Each is written into the plan itself; this is not an
# implementation choice.
LEAVE_TYPE_EDITABLE_FIELDS = (
	"leave_type_name",
	"max_leaves_allowed",
	# U2 / KTD2: the limit HRMS actually enforces on one request, and its
	# overdraw switch. Widens P5-KTD12's list on purpose.
	"max_continuous_days_allowed",
	"allow_negative",
	"is_carry_forward",
	"is_lwp",
	"helixhr_hr_approves",
)

HOLIDAY_LIST_EDITABLE_FIELDS = (
	"holiday_list_name",
	"from_date",
	"to_date",
	"weekly_off",
	"holidays",
)

# Shift Type is prompt-autonamed (`name` is set once, at creation, and is not
# itself a field) -- the "name" in P5-KTD12's list is the identifier a create
# call supplies, not something `save_shift_type` rewrites on an existing row.
SHIFT_TYPE_EDITABLE_FIELDS = (
	"start_time",
	"end_time",
	"begin_check_in_before_shift_start_time",
	"allow_check_out_after_shift_end_time",
)


# P8-U7/U8/U9. What `save_person` may write on Employee, grouped the same
# way the person view's own edit cards are grouped -- every field here is
# permlevel 0, verified against ERPNext's `employee.json` and HRMS's own
# `hrms/setup.py` custom fields (P8-U7's own research note). This reverses
# P6-R3's "read-only" posture deliberately (KTD3): the write surface is
# exactly as wide as the read surface `get_person` already grants.
PERSON_EDITABLE_FIELDS = {
	"overview": ("designation", "department", "branch", "company_email"),
	"joining": (
		"date_of_joining",
		"employment_type",
		"grade",
		"scheduled_confirmation_date",
		"final_confirmation_date",
		"status",
	),
	# The three approver fields are Link-to-User -- `save_person` accepts
	# employee ids like every other portal picker and resolves each to
	# `Employee.user_id` itself (KTD5), so this list names the Employee
	# doctype fieldnames a caller may set, not the User values actually
	# written.
	"approvers": ("reports_to", "leave_approver", "expense_approver", "shift_request_approver"),
	# `default_shift` (KTD4): a dated Shift Assignment is Desk-only, on
	# purpose -- this is the one field HRMS's own `get_employee_shift`
	# falls back to, so the change is visible everywhere the portal
	# resolves a shift without this app modelling scheduling itself.
	"shift": ("default_shift", "holiday_list"),
}


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
PORTAL_HOME_PAGE = "helixhr"


def session_company(user):
	"""Return the active Employee's company for ``user``, if any."""
	return frappe.db.get_value("Employee", {"user_id": user, "status": "Active"}, "company")


# P6-R6, P6-R7. Every persona this app knows how to grant an administrative
# read to -- "HR Manager or System Manager" -- named once so U1's answer and
# `hr_request.py`'s own `_UNSCOPED_ROLES` describe the same set without one
# importing the other (that would cycle: `hr_request.py` already imports
# `session_company` from this module).
_ADMIN_UNSCOPED_ROLES = frozenset({"HR Manager", "System Manager"})


def resolve_admin_scope(user):
	"""Which employees ``user`` may administer -- one answer, in one place,
	so a list read (U2) and a single-record check (U3) consume the same
	result without re-deriving it.

	Returns a dict with a ``kind`` of:

	- ``"unscoped"`` -- every employee, on every company. Administrator, and
	  the Desk-only HR Manager / System Manager who holds no Employee record
	  (P3-KTD7, P4-KTD7: `ensure_hr_manager_user()` is built to be exactly
	  this, on purpose, and must keep working).
	- ``"company"`` -- the named company only. An HR Manager / System
	  Manager anchored to an active Employee (the 2026-09-16 fix this
	  mirrors) is scoped to that company, never another.
	- ``"none"`` -- nobody. A plain employee, an `IT Team` holder, or any
	  other caller with no administrative role.

	The one deliberate distinction: "no Employee record" and "an Employee
	record that is not Active" are NOT the same case. The first is the
	Desk-only persona above. The second is somebody who has been offboarded,
	suspended, or marked Inactive while their User still holds the role --
	an offboarding lag, not a persona -- and it resolves to ``"none"``, never
	to unscoped. Before this rule, marking an HR Manager's Employee as Left
	silently widened their reach from one company to every company.
	"""
	if user == "Administrator":
		return {"kind": "unscoped", "company": None}
	if set(frappe.get_roles(user)) & _ADMIN_UNSCOPED_ROLES:
		anchor = frappe.db.get_value(
			"Employee", {"user_id": user}, ["status", "company"], as_dict=True
		)
		if not anchor:
			return {"kind": "unscoped", "company": None}
		if anchor.status != "Active":
			return {"kind": "none", "company": None}
		return {"kind": "company", "company": anchor.company}
	return {"kind": "none", "company": None}


def admin_scope_employee_filters(scope):
	"""Turn `resolve_admin_scope`'s answer into an Employee filter dict for
	`frappe.get_all`, or ``None`` when the scope holds nobody -- the caller's
	cue to return an empty page rather than run a query at all."""
	if scope["kind"] == "unscoped":
		return {}
	if scope["kind"] == "company":
		return {"company": scope["company"]}
	return None


def employee_in_admin_scope(employee, scope):
	"""Whether ``employee`` falls inside `resolve_admin_scope`'s answer --
	the single-record half U3 resolves *before* reading anything else, so a
	caller who may not administer this person is refused before any record
	is touched, not filtered afterwards."""
	if scope["kind"] == "unscoped":
		return bool(frappe.db.exists("Employee", employee))
	if scope["kind"] == "company":
		return bool(frappe.db.exists("Employee", {"name": employee, "company": scope["company"]}))
	return False


# P7-U2: the role this app grants an ability to administer a project, named
# once so `resolve_project_scope` and `project_permissions.py`'s hooks answer
# from the same constant (KTD8 -- every DocPerm this plan grants must be
# paired with a hook expressing the identical rule).
DELIVERY_MANAGER_ROLE = "HelixHR Delivery Manager"


def resolve_project_scope(user):
	"""Which projects ``user`` may administer -- one answer, in the same
	``{"kind": ..., ...}`` shape `resolve_admin_scope` returns, so every
	caller after U2 (the REST-route hooks in `project_permissions.py`, and
	every read and write in later units) branches on one vocabulary.

	Returns a dict with a ``kind`` of:

	- ``"unscoped"`` -- every project. System Manager, and an HR-role holder
	  with no Employee record at all (the same Desk-only persona
	  `resolve_admin_scope` names) -- delegated to that helper outright
	  rather than re-derived, per U2's dependency note.
	- ``"company"`` -- every project in the named company. An HR Manager
	  anchored to an Active Employee.
	- ``"assigned"`` -- the projects named in ERPNext's own ``Project User``
	  child table for this user (KTD5: membership keys on User, not
	  Employee). A `HelixHR Delivery Manager` holder.
	- ``"none"`` -- nobody. A plain employee, a holder of neither
	  administrative role, or -- the offboarding lesson from the previous
	  phase's security review, carried forward rather than re-learned -- an
	  Employee record that exists but is not Active. That last case applies
	  to a HelixHR Delivery Manager exactly as it does to an HR Manager: an
	  Employee row going to Left, Inactive or Suspended must narrow this
	  scope, never leave it at whatever the role alone would otherwise grant.
	"""
	admin = resolve_admin_scope(user)
	if admin["kind"] != "none":
		return admin
	if DELIVERY_MANAGER_ROLE not in frappe.get_roles(user):
		return {"kind": "none"}
	status = frappe.db.get_value("Employee", {"user_id": user}, "status")
	if status and status != "Active":
		return {"kind": "none"}
	return {"kind": "assigned", "user": user}


def project_scope_filters(scope):
	"""Turn `resolve_project_scope`'s answer into a Project filter dict for
	`frappe.get_all`, or ``None`` when the scope holds nobody -- the same
	"return an empty page rather than run a query" contract
	`admin_scope_employee_filters` uses.

	A HelixHR Delivery Manager who is a member of zero projects resolves to ``None``
	here rather than to ``{"name": ["in", []]}`` -- not because the latter is
	unsafe (`frappe.get_all` handles an empty ``in`` list without error), but
	because a caller that already treats ``None`` as "skip the query" would
	otherwise run one for a list that can only ever come back empty."""
	if scope["kind"] == "unscoped":
		return {}
	if scope["kind"] == "company":
		return {"company": scope["company"]}
	if scope["kind"] == "assigned":
		projects = frappe.get_all("Project User", filters={"user": scope["user"]}, pluck="parent")
		if not projects:
			return None
		return {"name": ["in", projects]}
	return None


def project_in_scope(project, scope):
	"""Whether ``project`` falls inside `resolve_project_scope`'s answer --
	the project-scope sibling of `employee_in_admin_scope` (U3): checked
	before any other field of the record is read, so a caller outside the
	scope is refused before the record is touched rather than after.

	Deliberately a fresh `frappe.db.exists` per kind, not a call into
	`project_scope_filters` plus a membership test against the result --
	that would mean pulling every project name the caller's scope covers
	just to answer one yes/no."""
	if scope["kind"] == "unscoped":
		return bool(frappe.db.exists("Project", project))
	if scope["kind"] == "company":
		return bool(frappe.db.exists("Project", {"name": project, "company": scope["company"]}))
	if scope["kind"] == "assigned":
		return bool(frappe.db.exists("Project User", {"parent": project, "user": scope["user"]}))
	return False


# Plan 2026-10-04-001 U1: portal-only role that runs and exports every
# catalog report in its holder's company. Holds no DocPerm at all (resolved
# decision 1 replaces KTD15): wrapped HRMS reports run in `reports.elevated`.
REPORT_MANAGER_ROLE = "HelixHR Report Manager"
HR_USER_ROLE = "HR User"

# Widest first. `resolve_report_access` picks the widest scope among the
# tiers that grant a right on the report in question (resolved decision 2).
_SCOPE_RANK = {"unscoped": 3, "company": 2, "assigned": 1, "none": 0}


def _active_anchor_company(user):
	"""KTD6: the company of ``user``'s Employee when it is Active, else None.
	No anchor and an inactive anchor both resolve to None for the new tiers;
	the anchorless-HR-Manager exception stays inside `resolve_admin_scope`."""
	anchor = frappe.db.get_value("Employee", {"user_id": user}, ["status", "company"], as_dict=True)
	if anchor and anchor.status == "Active":
		return anchor.company
	return None


# Portal-only administrator of the portal itself: edits the report access
# matrix, reads the export log, and grants the portal-only roles below. Holds
# no DocPerm and never passes `resolve_admin_scope` -- it sees no HR data.
PORTAL_ADMIN_ROLE = "HelixHR Portal Admin"
# The only roles `set_portal_role` may grant or remove. Never HR Manager, HR
# User, System Manager or Portal Admin itself: those stay a Desk decision.
MANAGED_PORTAL_ROLES = (
	REPORT_MANAGER_ROLE,
	DELIVERY_MANAGER_ROLE,
	"HelixHR Notification Manager",
	"IT Team",
)


def resolve_portal_admin_scope(user):
	"""Which employees ``user`` may administer *portal settings* for -- the
	export log rows and the role holders -- in `resolve_admin_scope`'s shape.

	HR Manager / System Manager get exactly `resolve_admin_scope`'s answer.
	A Portal Admin gets its Active Employee anchor's company, or ``"none"``
	without one: unlike the Desk-only HR persona, it is never unscoped."""
	if user == "Administrator":
		return {"kind": "unscoped", "company": None}
	roles = set(frappe.get_roles(user))
	if roles & _ADMIN_UNSCOPED_ROLES:
		return resolve_admin_scope(user)
	if PORTAL_ADMIN_ROLE in roles:
		company = _active_anchor_company(user)
		if company:
			return {"kind": "company", "company": company}
	return {"kind": "none", "company": None}


def can_admin_portal(user):
	"""Portal Admin, HR Manager, System Manager or Administrator -- the callers
	of the access matrix, export log and portal-role endpoints."""
	if user == "Administrator":
		return True
	return bool(set(frappe.get_roles(user)) & (_ADMIN_UNSCOPED_ROLES | {PORTAL_ADMIN_ROLE}))


def resolve_report_access(user, report_key):
	"""Who may run and export one catalog report, and over what (KTD5).

	Returns ``{"tier", "scope", "can_run", "can_export", "export_scope"}``.
	``scope`` / ``export_scope`` use `resolve_admin_scope`'s vocabulary
	(``unscoped`` / ``company`` / ``assigned`` / ``none``) and are each the
	widest scope among the tiers granting that right on THIS report -- an HR
	User + Delivery Manager running a Delivery-Manager-only report gets
	project scope, not company scope. Export callers must use
	``export_scope``, which can be narrower than ``scope``.

	An unknown key and a denied key return the identical answer (R22).
	"""
	from helixhr.reports import get_entry

	denied = {
		"tier": None,
		"scope": {"kind": "none"},
		"can_run": False,
		"can_export": False,
		"export_scope": {"kind": "none"},
	}
	entry = get_entry(report_key)
	if not entry:
		return denied

	roles = set(frappe.get_roles(user))
	grants = []  # (tier, scope, can_export)

	admin = resolve_admin_scope(user)
	if admin["kind"] != "none":
		grants.append(("admin", admin, True))

	if REPORT_MANAGER_ROLE in roles:
		company = _active_anchor_company(user)
		if company:
			grants.append(("report_manager", {"kind": "company", "company": company}, True))

	matrix = None
	if roles & {HR_USER_ROLE, DELIVERY_MANAGER_ROLE}:
		matrix = frappe.db.get_value(
			"HelixHR Report Access",
			report_key,
			["hr_user_run", "hr_user_export", "dm_run", "dm_export"],
			as_dict=True,
		)

	if matrix and HR_USER_ROLE in roles and matrix.hr_user_run:
		company = _active_anchor_company(user)
		if company:
			grants.append(
				("hr_user", {"kind": "company", "company": company}, bool(matrix.hr_user_export))
			)

	if matrix and DELIVERY_MANAGER_ROLE in roles and matrix.dm_run and "project" in entry["scopes"]:
		# Same offboarding rule as `resolve_project_scope`: an Employee that
		# exists but is not Active narrows to nothing.
		status = frappe.db.get_value("Employee", {"user_id": user}, "status")
		if not status or status == "Active":
			grants.append(("delivery_manager", {"kind": "assigned", "user": user}, bool(matrix.dm_export)))

	if not grants:
		return denied

	def widest(candidates):
		return max(candidates, key=lambda grant: _SCOPE_RANK[grant[1]["kind"]], default=None)

	run_grant = widest(grants)
	export_grant = widest([grant for grant in grants if grant[2]])
	return {
		"tier": run_grant[0],
		"scope": run_grant[1],
		"can_run": True,
		"can_export": export_grant is not None,
		"export_scope": export_grant[1] if export_grant else {"kind": "none"},
	}


def portal_home_page(user=None):
	"""Where this user lands after signing in: the portal, for everyone.

	Registered as `get_website_user_home_page` in hooks.py, which Frappe calls
	with the user and consults before `role_home_page` and before Website
	Settings.

	Every signed-in user starts on /helixhr, Desk roles included (2026-09-29):
	one landing page is one thing to test, and HR, System Managers and
	Administrator reach Desk from the shell's Open Desk button or by typing
	/desk. The portal itself decides what each of them sees -- an employee's
	own pages, the desk-only admin pages for HR with no Employee record, or
	"not set up" for anyone else (`lib/session.js`).

	Two things still win over this, by Frappe's design, and both are in
	docs/deployment.md: a `home_page` set on the Role doctype, and a
	`default_workspace` set on the User.
	"""
	user = user or frappe.session.user
	if user == "Guest":
		return None
	return PORTAL_HOME_PAGE


RATE_LIMIT_POLICY = {
	"update_my_profile": (20, 60),
	"save_my_week": (30, 60),
	# Plan 2026-10-04-003 U2: recall is a rare correction, not a loop.
	"recall_my_week": (10, 60),
	# U3: a change request mails its approver, and a withdraw is rare.
	"raise_timesheet_change": (10, 3600),
	"withdraw_timesheet_change": (10, 3600),
	"act_on_approval": (30, 60),
	# Plan 2026-10-04-003 U6: one batch call stands in for up to 60 single
	# decisions, so it is bounded tighter than the per-item path, not looser.
	"approve_clean_items": (10, 60),
	"get_overdue_approvals": (60, 60),
	"apply_for_leave": (20, 3600),
	"withdraw_my_leave": (20, 3600),
	"create_my_request": (10, 3600),
	"attach_to_my_request": (20, 3600),
	# Both trigger mail to the routed role, so an unbounded reply or
	# attachment is mail amplification, not just a write to police (P5-U6).
	"reply_to_my_request": (20, 3600),
	"attach_to_request_reply": (20, 3600),
	"mark_notifications_read": (60, 60),
	# P3-U1 step 5 / P3-R25. Writes first; the two reads are bounded too
	# because each one fans out to a per-employee lookup.
	"punch_my_checkin": (12, 60),
	"create_my_attendance_request": (10, 60),
	"send_my_attendance_request": (10, 60),
	"withdraw_my_attendance_request": (10, 60),
	"get_attendance_request_preview": (30, 60),
	"download_my_payslip": (10, 60),
	"get_directory": (60, 60),
	"get_my_team_week": (60, 60),
	"get_organisation_view": (60, 60),
	"search_people": (60, 60),
	"get_person": (60, 60),
	# Plan 2026-09-29-001 U3: the employee's own profile, read on every visit.
	"get_my_profile": (60, 60),
	"get_report_link": (60, 60),
	# P7-U3. Both fan out per project (tasks, members), the same reason
	# `search_people` / `get_person` are bounded above.
	"search_projects": (60, 60),
	"get_project": (60, 60),
	# P7-U4. Occasional administrative writes, not a per-keystroke path --
	# bounded like `save_request_category` and the other config writes above.
	"create_project": (20, 3600),
	"save_task": (30, 3600),
	"set_project_members": (20, 3600),
	# Plan 2026-10-04-001 U2: one runner for every catalog report. Wrapped
	# HRMS reports are the heaviest read the portal makes, so it keeps
	# `run_portal_report`'s tighter bound.
	"run_report": (30, 60),
	# U3/U4: picker typeahead (debounced 250 ms client-side) and the catalog.
	"search_report_options": (60, 60),
	"get_report_catalog": (60, 60),
	# U5: an export runs the report and renders a file (wkhtmltopdf for PDF),
	# so it is bounded tighter than a screen run; the download only streams
	# cached bytes. The export log is a paginated HR read.
	"request_export": (10, 60),
	"download_export": (20, 60),
	"get_export_log": (60, 60),
	# U13: the "My exports" panel polls this while an export is preparing.
	"list_my_exports": (60, 60),
	# U12: saved views -- a read per report open, occasional writes.
	"list_report_views": (60, 60),
	"save_report_view": (60, 3600),
	"delete_report_view": (60, 3600),
	# U6: the access matrix -- a read, and an occasional administrative write.
	"get_report_access": (60, 60),
	"save_report_access": (30, 3600),
	# Portal Admin: role holders in scope (a search per keystroke pause), and
	# an occasional administrative grant.
	"get_portal_role_holders": (60, 60),
	"set_portal_role": (30, 3600),
	# Reads that fan out (the home page and the approvals queue each run
	# several queries) or that answer for one record by name -- bounded so
	# a scripted walk over sequential record ids is a flood the limiter
	# sees, not a free enumeration.
	"get_dashboard": (60, 60),
	"get_my_approvals": (60, 60),
	# Plan 2026-10-04-002 U3: the "To work on" tab polls like the queue.
	"get_request_work": (60, 60),
	"get_approval_detail": (60, 60),
	"get_leave_day_count": (60, 60),
	"get_my_leave_detail": (60, 60),
	"get_my_request": (60, 60),
	"get_my_attendance_request": (60, 60),
	"get_request_categories": (60, 60),
	# P5-U13 configuration writes. Reads (`get_portal_config`) are cheap and
	# server-scoped like `get_directory`; every write checks permission itself
	# and still deserves a bound against a scripted flood.
	"get_portal_config": (60, 60),
	"save_request_category": (30, 3600),
	"save_message_template": (30, 3600),
	# Plan 2026-10-02-001 U10: the Email templates page. Preview renders per
	# keystroke pause; a test send is real mail, so it gets a tight bound.
	"get_notification_setup": (60, 60),
	"preview_message_template": (60, 60),
	"reset_message_template": (30, 3600),
	"send_test_message": (5, 600),
	"save_leave_type": (30, 3600),
	"save_holiday_list": (30, 3600),
	"save_shift_type": (30, 3600),
	# P8-U7/U8/U9. `get_person_form_options` fans out across several Link
	# doctypes plus a scoped employee list, the same reason `get_person`
	# itself is bounded above; `save_person` is an occasional
	# administrative write like the config saves just above it.
	"get_person_form_options": (60, 60),
	"save_person": (30, 3600),
	# P8-U12: an occasional administrative write, like the config saves above.
	"save_celebration_reminder": (30, 3600),
	# Plan 2026-09-30-001 U2. Each re-encodes an image, so bounded like the
	# attachment writes. The photo GET is deliberately not listed: a page
	# loads one per avatar.
	"upload_my_photo": (20, 3600),
	"remove_my_photo": (20, 3600),
	# Plan 2026-09-30-001 U7: fans out across up to 50 rows, like `get_my_team_week`.
	"get_roster_week": (60, 60),
	# U8: occasional administrative writes, bounded like the config saves above.
	"assign_shift": (30, 3600),
	"end_shift_assignment": (30, 3600),
	"change_shift_assignment": (30, 3600),
	"cancel_shift_assignment": (30, 3600),
	# Plan 2026-10-02-001 U13: shows a full bank detail and writes an audit
	# comment each time, so it is bounded tightly.
	"reveal_correction_value": (20, 3600),
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


def validate_portal_upload(file_name, content, policy=None, max_bytes=UPLOAD_MAX_BYTES, kind_message=None):
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

	`policy`, `max_bytes` and `kind_message` narrow the same checks for a
	stricter upload (the profile photo's PNG/JPEG and 5 MB); the default is
	the HR Request attachment policy, unchanged.
	"""
	policy = UPLOAD_POLICY if policy is None else policy
	kind_message = kind_message or _UPLOAD_KIND_MESSAGE
	if not isinstance(content, bytes | bytearray):
		frappe.throw(_("That file couldn't be read. Pick it again."))
	if not content:
		frappe.throw(_("That file is empty. Pick another one."))
	if len(content) > max_bytes:
		frappe.throw(
			_("That file is bigger than {0} MB. Send a smaller one.").format(max_bytes // (1024 * 1024))
		)

	extension = upload_extension(file_name)
	if extension not in policy:
		frappe.throw(_(kind_message))

	content_type, signatures, ooxml_part = policy[extension]
	if not any(bytes(content).startswith(signature) for signature in signatures):
		# The name says one thing and the bytes say another.
		frappe.throw(_(kind_message))

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


# --- profile photo policy (plan 2026-09-30-001, U1) ------------------------

# A photo is served inline to colleagues (U3), so it is narrower than an
# attachment: PNG or JPEG only, and re-encoded on the server so nothing the
# camera wrote (EXIF, GPS) and nothing a crafted file smuggled survives --
# only decoded pixels are written back out.
PHOTO_POLICY = {extension: UPLOAD_POLICY[extension] for extension in (".png", ".jpg", ".jpeg")}
PHOTO_MAX_BYTES = 5 * 1024 * 1024
PHOTO_MAX_SIDE = 512
# Checked from the header, before any pixel is decoded: a 5 MB file can
# still claim a 50,000 x 50,000 canvas (a decompression bomb). A 48 MP
# phone camera is comfortably inside this.
PHOTO_MAX_PIXELS = 64_000_000
PHOTO_KIND_MESSAGE = "Your photo must be a PNG or JPEG image."


def photo_file_filters(**extra):
	"""`File` filters for an Employee photo attachment: (Employee, *, "image").
	One definition so the doc events and the API cannot disagree on it."""
	return {"attached_to_doctype": "Employee", "attached_to_field": "image", **extra}


_PHOTO_FORMATS = {"PNG": (".png", "image/png"), "JPEG": (".jpg", "image/jpeg")}


def is_photo_content(content):
	"""Whether `content` starts like a PNG or a JPEG -- the served-inline
	test for a photo this app did not re-encode itself (U3's legacy case)."""
	head = bytes(content or b"")[:8]
	return any(
		head.startswith(signature) for _type, signatures, _part in PHOTO_POLICY.values() for signature in signatures
	)


def prepare_profile_photo(file_name, content):
	"""Validate an uploaded photo and re-encode it (R1, R2).

	Returns `(content, extension, content_type)` of the re-encoded image:
	orientation applied, bounded to `PHOTO_MAX_SIDE`, saved with no EXIF or
	other metadata, in the same format it arrived in. Pillow directly rather
	than `frappe.utils.image`, whose helpers can carry EXIF through. Anything
	Pillow cannot fully decode -- truncated, corrupt, a bomb -- is the same
	plain refusal as a wrong type, never a PIL traceback.
	"""
	import warnings

	from PIL import Image, ImageFile, ImageOps

	validate_portal_upload(
		file_name, content, policy=PHOTO_POLICY, max_bytes=PHOTO_MAX_BYTES, kind_message=PHOTO_KIND_MESSAGE
	)
	# Frappe's File module turns on LOAD_TRUNCATED_IMAGES process-wide; a
	# half-uploaded photo must be refused here, not padded with grey.
	load_truncated = ImageFile.LOAD_TRUNCATED_IMAGES
	ImageFile.LOAD_TRUNCATED_IMAGES = False
	try:
		with warnings.catch_warnings():
			warnings.simplefilter("error", Image.DecompressionBombWarning)
			with Image.open(io.BytesIO(bytes(content))) as image:
				if image.format not in _PHOTO_FORMATS:
					raise ValueError("not a PNG or JPEG")
				if image.width * image.height > PHOTO_MAX_PIXELS:
					raise ValueError("too many pixels")
				extension, content_type = _PHOTO_FORMATS[image.format]
				if image.format == "JPEG":
					# Decode at a reduced scale where JPEG allows it: same
					# result after `thumbnail`, a fraction of the memory.
					image.draft("RGB", (PHOTO_MAX_SIDE * 2, PHOTO_MAX_SIDE * 2))
				image.load()
				photo = ImageOps.exif_transpose(image)
				photo.thumbnail((PHOTO_MAX_SIDE, PHOTO_MAX_SIDE))
				if extension == ".jpg" and photo.mode not in ("RGB", "L"):
					photo = photo.convert("RGB")
				elif extension == ".png" and photo.mode not in ("1", "L", "LA", "P", "RGB", "RGBA"):
					photo = photo.convert("RGBA")
				# Only pixels leave: no exif, icc, text chunks or comments.
				photo.info = {}
				out = io.BytesIO()
				if extension == ".jpg":
					photo.save(out, "JPEG", quality=85, optimize=True)
				else:
					photo.save(out, "PNG", optimize=True)
	except (Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, ValueError, SyntaxError):
		frappe.throw(_(PHOTO_KIND_MESSAGE))
	finally:
		ImageFile.LOAD_TRUNCATED_IMAGES = load_truncated
	return out.getvalue(), extension, content_type


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
	# P3-KTD12: geolocation is allowed for this origin only, because the
	# check-in button asks the browser for a position (P3-R6). Everything
	# else the portal never needs, and Frappe Desk sets its own where it
	# does; naming them denies them for this site's whole surface. A
	# reverse proxy that sets `geolocation=()` wins over this default and
	# makes every punch read as a user denial -- `preflight
	# .check_public_endpoint` asserts the effective value for that reason.
	"Permissions-Policy": "camera=(), microphone=(), geolocation=(self), payment=(), usb=()",
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


@contextmanager
def as_administrator():
	"""Run the wrapped block as Administrator, then restore the caller --
	without touching the live session (P8-U1 / P8-R1, KTD1).

	`frappe.set_user` is the wrong primitive for this: it does not just
	change *who* the current request acts as, it mutates
	`frappe.local.session` in place -- and `frappe.local.session` **is**
	`Session.data` (`Session.__init__` in frappe/sessions.py assigns
	`frappe.local.session = self.data`). `set_user` overwrites
	`session.sid` with the target username and replaces `session.data` with
	an empty dict, wiping `user`, `csrf_token` and `session_expiry`. At the
	end of the request `Session.update()` writes that gutted payload back
	into the Redis session cache under the *real* sid (the `Session`
	object's own `self.sid` slot, untouched by `set_user`), so the next
	request resumes a session with no `user` -- Guest -- and the caller is
	signed out. This was the actual cause of the "creating a project logs
	me out" defect: every call this helper replaces used to run through
	`frappe.set_user("Administrator")` for exactly the reason documented on
	`_write_project_users`.

	This changes only `frappe.local.session.user` (the in-memory attribute
	code reads to ask "who am I", not the session store) plus the two
	permission caches `set_user` also resets, so the target user's rights
	take effect immediately rather than the caller's stale ones:
	`frappe.local.role_permissions` and `frappe.local.user_perms`. It never
	touches `session.sid`, `session.data`, or `frappe.local.form_dict`.

	The escalation itself is not gratuitous and should not be "simplified"
	away: ERPNext's `Project.control_access_for_project_users` calls
	`frappe.share.add_docshare`, which checks the *session user's* `share`
	permission on Project regardless of `ignore_permissions` -- there is no
	flag ERPNext passes to skip that check, so something has to run as a
	user who already holds it. Administrator is that user. Upgrade path:
	if ERPNext ever grows a flag or hook for that side effect, drop the
	escalation entirely -- callers already remove the resulting `DocShare`
	rows immediately afterwards and rely on nothing else it grants.
	"""
	caller = frappe.session.user
	frappe.session.user = "Administrator"
	frappe.local.role_permissions = {}
	frappe.local.user_perms = None
	try:
		yield
	finally:
		frappe.session.user = caller
		frappe.local.role_permissions = {}
		frappe.local.user_perms = None
