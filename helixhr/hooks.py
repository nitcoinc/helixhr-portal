app_name = "helixhr"
app_title = "HelixHR"
app_publisher = "Nitco Inc"
app_description = "HelixHR Employee Portal"
app_email = "dev@nitcoinc.ai"
app_license = "mit"

# Apps
# ------------------

required_apps = ["hrms"]

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "helixhr",
# 		"logo": "/assets/helixhr/logo.png",
# 		"title": "HelixHR",
# 		"route": "/helixhr",
# 		"has_permission": "helixhr.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/helixhr/css/helixhr.css"
# app_include_js = "/assets/helixhr/js/helixhr.js"

# include js, css files in header of web template
# web_include_css = "/assets/helixhr/css/helixhr.css"
# web_include_js = "/assets/helixhr/js/helixhr.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "helixhr/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "helixhr/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Website Route Rules
# --------------------
# Serves the built Vue single-page app for every /helixhr/* path. (KTD1, U2)

# An employee signing in lands on the portal, not on Desk. Frappe consults
# this before role_home_page and before Website Settings; returning None for
# HR and System Managers leaves their Desk landing untouched. Blocking Desk
# outright is a proxy concern -- see docs/deployment.md.
get_website_user_home_page = "helixhr.utils.portal_home_page"

website_route_rules = [
	{"from_route": "/helixhr/<path:app_path>", "to_route": "helixhr"},
]

# Fixtures
# --------
# Configuration shipped as data, not code, per KTD15 -- filtered to this
# app's own records so `bench migrate` never exports another app's Property
# Setters or permission rows.

fixtures = [
	{"dt": "Property Setter", "filters": [["module", "=", "HelixHR"]]},
	# P5-U2: this role is a portal role, so its fixture pins desk_access=0
	# rather than inheriting Frappe's Desk-user default.
	{"dt": "Role", "filters": [["name", "=", "IT Team"]]},
	# Custom DocPerm is deliberately NOT a fixture. Frappe *replaces* a
	# doctype's standard DocPerm rows with its Custom DocPerm rows rather than
	# merging them (frappe.permissions.get_valid_perms), so shipping a partial
	# set of roles removes every other role's access on a fresh site -- and
	# widening the filters would freeze this machine's Frappe/ERPNext/HRMS
	# permission rows into the app. helixhr.patches.v1_0.apply_permission_deltas
	# snapshots each site's own standard rows and applies only this app's
	# deltas on top. (P2-U1)
	{
		# "Approved" and "Rejected" already exist as shared Workflow State
		# records (HRMS's own Leave Application workflow uses them) --
		# reused by name on the transitions below, not exported here, so
		# this app never claims ownership of another app's record. "Draft"
		# and "Pending Approval" are the Timesheet workflow's; "Pending
		# Manager" and "Pending HR" are the attendance request's two
		# pending steps (P3-KTD6). "Sent Back" is P4-KTD1: it carries the
		# recoverable meaning "Rejected" used to carry on both workflows,
		# which frees "Rejected" to mean a final no. States before the
		# Workflows that link to them.
		"dt": "Workflow State",
		"filters": [
			[
				"name",
				"in",
				["Draft", "Pending Approval", "Pending Manager", "Pending HR", "Sent Back", "Waiting on Employee"],
			]
		],
	},
	# Timesheet Approval (KTD7) and Attendance Request Approval (P3-KTD6),
	# each carrying the four outcomes of P4-KTD1 (Timesheet has three --
	# P4-KTD2).
	#
	# Why every HR Manager transition keeps `allow_self_approval: 1` and a
	# `user_id != frappe.session.user` condition instead (P4-R8): Frappe's
	# own self-approval check is `user != doc.owner`
	# (`frappe.model.workflow.has_approval_access`), and `owner` is the login
	# that *created* the record -- which for a request or a week HR raised on
	# somebody's behalf is HR itself. With `allow_self_approval: 0` an HR
	# Manager could not decide any record they had filed for someone else.
	# The condition asks the question the rule actually means -- is this the
	# requester's own record -- and `events.timesheet_before_submit` and
	# `events.attendance_request_before_submit` ask it again on the raw
	# `frappe.client.submit` route the fixture never sees. The Employee-role
	# (line manager) transitions keep `allow_self_approval: 0`, where `owner`
	# genuinely is the employee.
	{
		"dt": "Workflow",
		"filters": [["document_type", "in", ["Timesheet", "Attendance Request", "HR Request"]]],
	},
	{
		# Likewise "Approve" and "Reject" already exist as shared Workflow
		# Action Master records; "Submit", "Edit", "Send Back" and
		# "Send to HR" are this app's own.
		"dt": "Workflow Action Master",
		"filters": [
			["name", "in", ["Submit", "Edit", "Send Back", "Send to HR", "Pick up", "Need info", "Done"]]
		],
	},
	# P4-KTD7a: `helixhr_decision_reason` on the two workflow kinds, at
	# permlevel 1 so only the roles `apply_permission_deltas` names can read
	# or write it. Module-scoped like every other fixture here.
	{"dt": "Custom Field", "filters": [["module", "=", "HelixHR"]]},
	{
		# ERPNext's Timesheet requires Activity Type on every row at
		# submit time (erpnext/projects/doctype/timesheet/timesheet.py),
		# but a headless install seeds none (same class of gap as Gender
		# and Warehouse Type -- see docs/runbook.md) and R17 doesn't ask
		# the portal to expose the field at all. save_my_week always uses
		# this one fixed value.
		"dt": "Activity Type",
		"filters": [["name", "=", "General"]],
	},
	{"dt": "Notification", "filters": [["module", "=", "HelixHR"]]},
]

# Document Events
# ---------------

doc_events = {
	"Timesheet": {
		"on_update": "helixhr.events.timesheet_on_update",
		"before_submit": "helixhr.events.timesheet_before_submit",
	},
	"File": {
		"before_insert": "helixhr.events.file_before_insert",
	},
	# P2-U7 step 6. A pending Timesheet is reachable by its approver through
	# a DocShare, and `reports_to` is what decides who the approver is -- so
	# a reassignment that does not move the share leaves the old manager
	# holding write and submit on a week that is no longer theirs.
	"Employee": {
		"on_update": "helixhr.events.employee_on_update",
	},
	# P2-U4 / P2-KTD6. The employee-facing "HR replied" event. A fixture
	# Notification watches one field on a Value Change and the existing one
	# watches `status`, so a reply written without a status change produced
	# no notification and therefore no obligation the employee could clear.
	"HR Request": {
		"after_insert": "helixhr.events.hr_request_after_insert",
		"validate": "helixhr.events.hr_request_validate",
		"on_update": "helixhr.events.hr_request_on_update",
	},
	# P4-U2 / P4-R8, P4-R8a. Leave has no Workflow, so the only guard on the
	# raw submit route -- Desk, `frappe.client.submit`, the `submit=1`
	# DocShare HRMS grants the approver -- is this hook: nobody approves their
	# own leave, and nobody but HR approves one that is in the HR stage.
	# `validate` is the same rule on the route that never submits: a send back
	# is `status = "Rejected"` at docstatus 0, which is a save.
	"Leave Application": {
		"validate": "helixhr.events.leave_application_validate",
		"before_submit": "helixhr.events.leave_application_before_submit",
		# P5-U10. The manager's arrival notice -- unlike Timesheet and
		# Attendance Request, filing a leave application is the insert
		# itself, not a later workflow-state move.
		"after_insert": "helixhr.events.leave_application_after_insert",
	},
	# P3-KTD8 / P4-KTD5. Frappe does not enforce a workflow state's
	# `allow_edit` on the server, so the attendance approval carries its
	# rules as doc events: a field freeze outside Draft, the manager's
	# DocShare (with `submit`, since P4 made their Approve the submit) while
	# Pending Manager, who may submit and from which stored state, and a
	# delete that follows the same states as the portal's withdraw.
	# P4-U6 / P4-R18 / P4-KTD10. Frappe cannot unregister HRMS's daily
	# reminder job, so a HelixHR template picked while the matching HRMS
	# checkbox is still ticked means two emails for the same event, every
	# morning. Refused here, where HR creates it; preflight is the backstop.
	"HR Settings": {
		"validate": "helixhr.events.hr_settings_validate",
	},
	"Attendance Request": {
		"validate": "helixhr.events.attendance_request_validate",
		"on_update": "helixhr.events.attendance_request_on_update",
		"before_submit": "helixhr.events.attendance_request_before_submit",
		"on_trash": "helixhr.events.attendance_request_on_trash",
	},
}

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "helixhr.utils.jinja_methods",
# 	"filters": "helixhr.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "helixhr.install.before_install"

# `bench new-site --install-app` marks every patch as already applied
# without running it (frappe.installer.set_all_patches_as_completed), so a
# fresh install never runs patches/v1_0/apply_permission_deltas or
# report_unsubmitted_approved_leave -- both mutate data, not schema, and
# `bench migrate` is the only other place either would run. See
# helixhr/install.py for the full account; this is the fix for CI's first
# real failure (P2-R26/P2-AE9 across the whole suite on a site nothing had
# ever migrated).
after_install = "helixhr.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "helixhr.uninstall.before_uninstall"
# after_uninstall = "helixhr.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "helixhr.utils.before_app_install"
# after_app_install = "helixhr.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "helixhr.utils.before_app_uninstall"
# after_app_uninstall = "helixhr.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "helixhr.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "helixhr.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways.
#
# P2-R19 / P2-AE2: HelixHR Document Link is scoped to global links plus the
# reader's own company, and both halves are registered -- the query
# condition covers every list-shaped route (frappe.client.get_list,
# /api/resource, report view, export) and the controller check covers every
# single-document route (frappe.client.get, print, the Desk form). A
# browser-side filter is not a boundary; these are.

permission_query_conditions = {
	"HR Request": "helixhr.helixhr.doctype.hr_request.hr_request.get_permission_query_conditions",
	"HelixHR Document Link": (
		"helixhr.helixhr.doctype.helixhr_document_link.helixhr_document_link"
		".get_permission_query_conditions"
	),
}

has_permission = {
	"HR Request": "helixhr.helixhr.doctype.hr_request.hr_request.has_permission",
	"HelixHR Document Link": (
		"helixhr.helixhr.doctype.helixhr_document_link.helixhr_document_link.has_permission"
	),
}

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# P3-U4 step 2a / P3-KTD15 / P3-R28. Punch coordinates have a retention
# period, and this is what enforces it. The job is idle until the site config
# key `helixhr_checkin_location_retention_days` is set; preflight warns while
# it is unset. Daily rather than hourly: the period is measured in days, so
# an hourly pass would do the same work 24 times.
# P4-U6 / P4-KTD10 / P4-R15. The birthday and work-anniversary emails HR
# words themselves, from an Email Template. Idle until HR picks a template on
# HR Settings, and it runs *beside* HRMS's own daily reminder job rather than
# replacing it -- Frappe merges scheduler hooks across apps and offers no
# removal, so having both senders on for one event is the thing
# `events.hr_settings_validate` and preflight guard against.
scheduler_events = {
	"daily": [
		"helixhr.tasks.null_stale_checkin_coordinates",
		"helixhr.reminders.send_celebration_reminders",
	],
}

# scheduler_events = {
# 	"all": [
# 		"helixhr.tasks.all"
# 	],
# 	"daily": [
# 		"helixhr.tasks.daily"
# 	],
# 	"hourly": [
# 		"helixhr.tasks.hourly"
# 	],
# 	"weekly": [
# 		"helixhr.tasks.weekly"
# 	],
# 	"monthly": [
# 		"helixhr.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "helixhr.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "helixhr.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "helixhr.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "helixhr.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# P2-U9 steps 5 and 8. Frappe version-16 sets no security headers of its own
# (the only Content-Security-Policy in the framework is the Web Form's
# frame-ancestors header), so nosniff, Referrer-Policy, Permissions-Policy,
# frame-ancestors and -- over HTTPS only -- HSTS are set here, for every
# response this site serves rather than only the portal's own routes. The
# same hook forces a download disposition on files attached to an HR Request,
# which is the one thing that cannot be done from `helixhr.api`: a
# `/private/files/...` request is served before any whitelisted method runs.
#
# Every header is set with `setdefault`, so a reverse proxy that already sets
# a stricter value keeps it.

after_request = ["helixhr.utils.set_security_headers"]

# before_request = ["helixhr.utils.before_request"]

# Job Events
# ----------
# before_job = ["helixhr.utils.before_job"]
# after_job = ["helixhr.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"helixhr.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

