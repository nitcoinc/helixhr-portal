app_name = "helixhr"
app_title = "HelixHR"
app_publisher = "Nitco"
app_description = "HelixHR Employee Portal"
app_email = "dev@nitcoinc.com"
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
	# Custom DocPerm is deliberately NOT a fixture. Frappe *replaces* a
	# doctype's standard DocPerm rows with its Custom DocPerm rows rather than
	# merging them (frappe.permissions.get_valid_perms), so shipping a partial
	# set of roles removes every other role's access on a fresh site -- and
	# widening the filters would freeze this machine's Frappe/ERPNext/HRMS
	# permission rows into the app. helixhr.patches.v1_0.apply_permission_deltas
	# snapshots each site's own standard rows and applies only this app's
	# deltas on top. (P2-U1)
	{"dt": "Workflow", "filters": [["document_type", "=", "Timesheet"]]},
	{
		# "Approved" and "Rejected" already exist as shared Workflow State
		# records (HRMS's own Leave Application workflow uses them) --
		# reused by name on the transitions below, not exported here, so
		# this app never claims ownership of another app's record. Only
		# "Draft" and "Pending Approval" are new.
		"dt": "Workflow State",
		"filters": [["name", "in", ["Draft", "Pending Approval"]]],
	},
	{
		# Likewise "Approve" and "Reject" already exist as shared Workflow
		# Action Master records; only "Submit" and "Edit" are new here.
		"dt": "Workflow Action Master",
		"filters": [["name", "in", ["Submit", "Edit"]]],
	},
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
		"on_update": "helixhr.events.hr_request_on_update",
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
# after_install = "helixhr.install.after_install"

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
	"HelixHR Document Link": (
		"helixhr.helixhr.doctype.helixhr_document_link.helixhr_document_link"
		".get_permission_query_conditions"
	),
}

has_permission = {
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

