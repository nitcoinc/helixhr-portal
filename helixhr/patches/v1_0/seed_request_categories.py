"""P5-U1: request categories seeded once under their legacy names.

The category field changed from Select to Link, but both store the category
name. Keeping these exact names makes every existing HR Request resolve after
migration without a data rewrite. New-site installs mark patches complete, so
``helixhr.install.after_install`` calls this module too.
"""

import frappe

# Initial IT requests remain with HR until U2 installs the IT Team role and
# explicitly moves this route. The seed itself must run successfully on a
# fresh site before that role exists.
CATEGORIES = (
	{
		"category_name": "HR Letter",
		"hint": "Address, employment, visa",
		"route_to_role": "HR Manager",
	},
	{
		"category_name": "IT / Asset",
		"hint": "Laptop, access, badge",
		"route_to_role": "HR Manager",
	},
	{
		"category_name": "Payroll Question",
		"hint": "Payslip, tax, overtime",
		"route_to_role": "HR Manager",
	},
	{
		"category_name": "Other",
		"hint": "Anything HR can help with",
		"route_to_role": "HR Manager",
	},
)


def execute():
	for spec in CATEGORIES:
		if frappe.db.exists("HelixHR Request Category", spec["category_name"]):
			continue
		frappe.get_doc({"doctype": "HelixHR Request Category", **spec}).insert(ignore_permissions=True)
