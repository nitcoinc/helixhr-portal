# Copyright (c) 2026, Nitco and contributors
# For license information, please see license.txt

from frappe.model.document import Document
from hrms.api import get_current_employee


class HRRequest(Document):
	def before_insert(self):
		# `employee` is set_only_once at the field level (locks it after
		# the first save), but that alone doesn't stop a caller naming a
		# different employee on the very first insert -- resolve it from
		# the session instead of trusting whatever was posted (KTD5).
		self.employee = get_current_employee()

	# status and hr_note are permlevel 1 with only HR Manager/HR User/
	# System Manager granted write there (see this doctype's own
	# permissions, not a fixture) -- Frappe resets an ESS write to either
	# field the same way it does for Employee's locked fields (KTD6), so
	# R22's "Only HR can change status and note" needs no extra code here.
