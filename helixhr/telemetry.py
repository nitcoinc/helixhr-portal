"""Anonymous install counting -- off by default, and a no-op until an
operator explicitly configures where the ping goes.

The payload is exactly `{"install_id", "app_version", "frappe_version"}`.
`install_id` is a random value generated once and persisted in this site's
own config -- it identifies an installation, never a company or a person,
and nothing here ever reads an Employee, User or Company record. Two site
config keys turn it on; leaving either unset keeps this idle:

    bench --site <site> set-config helixhr_telemetry_enabled true
    bench --site <site> set-config helixhr_telemetry_url https://example.com/ping

See docs/deployment.md for the full payload shape and how to turn it off
again.
"""

import frappe
from frappe.installer import update_site_config

INSTALL_ID_KEY = "helixhr_telemetry_install_id"
ENABLED_KEY = "helixhr_telemetry_enabled"
URL_KEY = "helixhr_telemetry_url"


def _install_id():
	"""A random identifier for this site, generated once and persisted in
	site config -- not derived from the company, its employees, or anything
	else about who runs this site."""
	existing = frappe.conf.get(INSTALL_ID_KEY)
	if existing:
		return existing
	install_id = frappe.generate_hash(length=32)
	update_site_config(INSTALL_ID_KEY, install_id)
	return install_id


def send_ping():
	"""Run weekly (`hooks.py`). No-op unless both `helixhr_telemetry_enabled`
	and `helixhr_telemetry_url` are set -- the safe default is off, and a
	failed request is swallowed rather than surfaced: a customer's network
	policy blocking this call is not a reason to fill their error log."""
	if not frappe.conf.get(ENABLED_KEY) or not frappe.conf.get(URL_KEY):
		return

	import requests

	from helixhr import __version__

	payload = {
		"install_id": _install_id(),
		"app_version": __version__,
		"frappe_version": frappe.__version__,
	}
	try:
		requests.post(frappe.conf.get(URL_KEY), json=payload, timeout=10)
	except Exception:
		frappe.log_error(title="HelixHR telemetry ping failed")
