"""Move data off the first release's ``custom_zimra_*`` fields, then remove them.

Only those custom fields are touched; property setters a site created for its
own layout are left alone.
"""

import frappe

from zw_compliance.fiscalisation.setup.custom_fields import make_custom_fields

LEGACY_COPY = {
	"Item": ("custom_zimra_hsn_code", "zimra_hs_code"),
	"Item Group": ("custom_zimra_hsn_code", "zimra_hs_code"),
	"Customer": ("custom_zimra_vat_id", "zimra_vat_number"),
}
LEGACY_DOCTYPES = ("Sales Invoice", "POS Invoice", "Customer", "Item", "Item Group")
LEGACY_COLUMN_BREAKS = ("Sales Invoice-custom_column_break_yx7xw", "POS Invoice-custom_column_break_org11")


def execute():
	make_custom_fields()
	for doctype, (old, new) in LEGACY_COPY.items():
		if frappe.db.has_column(doctype, old) and frappe.db.has_column(doctype, new):
			frappe.db.sql(
				f"update `tab{doctype}` set `{new}` = `{old}` where ifnull(`{old}`, '') != '' and ifnull(`{new}`, '') = ''"
			)
	for name in frappe.get_all(
		"Custom Field",
		filters={"dt": ["in", LEGACY_DOCTYPES], "fieldname": ["like", "custom_zimra%"]},
		pluck="name",
	):
		frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
	for name in LEGACY_COLUMN_BREAKS:
		if frappe.db.exists("Custom Field", name):
			frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
