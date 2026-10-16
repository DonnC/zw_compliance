# Copyright (c) 2026, Donald Chinhuru and contributors
# For license information, please see license.txt

"""A ZIMRA fiscal day of one device. Maintained by the fiscalisation code only."""

import frappe
from frappe import _
from frappe.model.document import Document


class ZimraFiscalDay(Document):
	def on_trash(self):
		if self.receipt_counter or self.open_operation_id:
			frappe.throw(_("A fiscal day that was opened with ZIMRA or holds receipts cannot be deleted."))


def on_doctype_update():
	frappe.db.add_unique("Zimra Fiscal Day", ["fiscal_device", "fiscal_day_no"], "unique_device_day")
