"""P4-KTD11 / P4-R19: the two default Email Templates, seeded once and never
overwritten.

`helixhr.reminders` sends nothing until HR picks a template on HR Settings,
and HR should not have to write one from scratch to switch the branded email
on -- so the app ships two, as *data a site owns* rather than as fixtures.

Why not fixtures: `bench migrate` re-imports every fixture, so a template
shipped that way would have HR's edited subject and HTML replaced on the next
deploy (P4-AE7 says the edit survives). This patch inserts each template only
if a record of that name is absent and never touches one that exists, so the
default lands once and HR owns it afterwards.

It deliberately does **not** set the templates on HR Settings. Seeding is the
app's act; switching the emails on is HR's, and a site that upgrades must not
start emailing everybody because a patch ran.

Idempotent, and called twice on purpose: `bench new-site --install-app` marks
every patch complete without running it, so `helixhr/install.py`'s
`after_install` calls `execute()` too -- exactly as it does for
`apply_permission_deltas`.

A later change to the default HTML does not reach a site that already has the
templates. That is accepted: what the app promises is the Jinja context
(P4-KTD13, `docs/deployment.md`), not the markup.
"""

import frappe

# The default markup, in one shape for both events. Jinja and %-formatting
# both spell their placeholders with braces and percent signs, so the two
# variable parts are plain markers substituted with `str.replace` -- the
# alternative is a template that will not even import.
_WRAPPER = """<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
 color:#1f2933;line-height:1.5;max-width:520px">
{%- if logo_url %}
 <img src="{{ logo_url }}" alt="{{ company }}" style="max-height:40px;margin-bottom:20px">
{%- endif %}
 <p style="font-size:20px;font-weight:600;margin:0 0 12px">@HEADLINE@</p>
 <p style="margin:0 0 16px">@BODY@</p>
 <p style="margin:0 0 20px;color:#52606d">{%- for person in persons %}
  {%- if person.image_url %}<img src="{{ person.image_url }}" alt="" width="24" height="24"
   style="border-radius:12px;vertical-align:middle;margin-right:8px">{% endif -%}
  @LINE@<br>
 {%- endfor %}</p>
 <p style="margin:0"><a href="{{ portal_url }}" style="color:#1f6feb">Open HelixHR</a></p>
 <p style="margin:24px 0 0;font-size:12px;color:#9aa5b1">{{ company }} &middot; {{ date }}</p>
</div>"""


def _html(headline, body, line):
	return _WRAPPER.replace("@HEADLINE@", headline).replace("@BODY@", body).replace("@LINE@", line)


TEMPLATES = (
	{
		"name": "HelixHR Birthday Reminder",
		"subject": "{% if count > 1 %}Birthdays today: {{ names }}{% else %}"
		"Today is {{ names }}'s birthday{% endif %}",
		"response_html": _html(
			headline="{% if count > 1 %}Birthdays today{% else %}"
			"Happy birthday, {{ persons[0].first_name }}{% endif %}",
			body="{% if count > 1 %}{{ count }} of us are celebrating today."
			"{% else %}One of us is celebrating today.{% endif %} "
			"Do say something to {{ names }}.",
			line="{{ person.name }}",
		),
	},
	{
		"name": "HelixHR Work Anniversary Reminder",
		# Email Template caps the subject at 140 characters, Jinja included.
		"subject": "{% if count > 1 %}Work anniversaries today{% else %}"
		"{{ names }}: {{ persons[0].years }} years today{% endif %}",
		"response_html": _html(
			headline="{% if count > 1 %}Work anniversaries today{% else %}"
			"{{ persons[0].years }} year{% if persons[0].years != 1 %}s{% endif %}"
			", {{ persons[0].first_name }}{% endif %}",
			body="Thank you for the time you have given {{ company }}.",
			line="{{ person.name }} &middot; {{ person.years }} "
			"year{% if person.years != 1 %}s{% endif %}",
		),
	},
)


def execute():
	for spec in TEMPLATES:
		if frappe.db.exists("Email Template", spec["name"]):
			continue
		frappe.get_doc(
			{
				"doctype": "Email Template",
				"name": spec["name"],
				"use_html": 1,
				"subject": spec["subject"],
				"response_html": spec["response_html"],
			}
		).insert(ignore_permissions=True)
