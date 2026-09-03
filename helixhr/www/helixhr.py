import frappe

no_cache = 1


def get_context(context):
	context.csrf_token = frappe.sessions.get_csrf_token()
	# The CSRF token is regenerated per request; commit now so it is
	# actually persisted before the page (and any immediate API call
	# the SPA makes) relies on it.
	frappe.db.commit()  # nosemgrep
	# Per-site configuration the SPA needs before it has an Employee to
	# ask about. The built shell already renders every `boot` key as a
	# `window.<key>` global (frappe-ui's index.html template), so this
	# costs no extra request. Set with:
	#   bench --site <site> set-config helixhr_hr_contact hr@example.com
	context.boot = {"helixhr_hr_contact": frappe.conf.get("helixhr_hr_contact") or ""}
