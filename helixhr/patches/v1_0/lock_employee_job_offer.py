"""Lock HRMS's Employee.job_offer field from Employee-role writes.

HRMS v16 adds this field at permlevel 0 on some versions. The Employee role
can write permlevel-0 fields on its own record, so mirror the fixture
Property Setter on upgraded sites where the field exists.
"""

import frappe


def execute():
	if not frappe.get_meta("Employee").has_field("job_offer"):
		return

	name = "Employee-job_offer-permlevel"
	if frappe.db.exists("Property Setter", name):
		frappe.db.set_value("Property Setter", name, "value", "1")
	else:
		frappe.get_doc(
			{
				"doctype": "Property Setter",
				"doctype_or_field": "DocField",
				"doc_type": "Employee",
				"field_name": "job_offer",
				"property": "permlevel",
				"property_type": "Int",
				"value": "1",
				"module": "HelixHR",
			}
		).insert(ignore_permissions=True)

	frappe.clear_cache(doctype="Employee")
