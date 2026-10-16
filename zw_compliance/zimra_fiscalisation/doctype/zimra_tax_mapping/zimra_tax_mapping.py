# Copyright (c) 2026, Donald Chinhuru and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class ZimraTaxMapping(Document):
	def validate(self):
		if self.is_exempt:
			self.zimra_tax_percent = 0
		if self.zimra_tax_percent and self.zimra_tax_percent < 0:
			frappe.throw(_("Tax percent cannot be negative."))
		if self.valid_from and self.valid_till and self.valid_till < self.valid_from:
			frappe.throw(_("Valid Till cannot be before Valid From."))
		for field in (
			"sales_tax_template",
			"item_tax_template",
			"purchase_tax_template",
			"purchase_item_tax_template",
			"output_account",
			"input_account",
		):
			value = self.get(field)
			doctype = self.meta.get_field(field).options
			if value and frappe.db.get_value(doctype, value, "company") != self.company:
				frappe.throw(_("{0} {1} belongs to another company.").format(_(doctype), value))


def on_doctype_update():
	frappe.db.add_unique("Zimra Tax Mapping", ["company", "zimra_tax_id"], "unique_company_tax")
