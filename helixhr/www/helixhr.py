import frappe

no_cache = 1


def get_context(context):
	context.csrf_token = frappe.sessions.get_csrf_token()
	# The CSRF token is regenerated per request; commit now so it is
	# actually persisted before the page (and any immediate API call
	# the SPA makes) relies on it.
	frappe.db.commit()  # nosemgrep
