# Copyright (c) 2026, HelixHR Contributors
# For license information, please see license.txt

from frappe.model.document import Document


class HelixHRProfileChangeRow(Document):
	"""One field of a Profile correction (plan 2026-11-03-001 KTD9). Rows are
	written only by `api.submit_my_profile_changes`; every rule lives in
	`events.hr_request_validate`."""

	pass
