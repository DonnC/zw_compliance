# Copyright (c) 2026, Donald Chinhuru and contributors
# For license information, please see license.txt

"""A ZIMRA fiscal receipt: the signed record of one fiscalised invoice.

Created only by :func:`zw_compliance.fiscalisation.outbox.fiscalise`. What was
signed can never change afterwards; only the synchronisation fields written
by the sync job may.
"""

import frappe
from frappe import _
from frappe.model.document import Document

SIGNED_FIELDS = (
	"reference_doctype",
	"reference_name",
	"fiscal_device",
	"fiscal_day",
	"fiscal_day_no",
	"receipt_type",
	"receipt_currency",
	"receipt_total",
	"invoice_no",
	"receipt_counter",
	"receipt_global_no",
	"receipt_date",
	"qr_data",
	"verification_code",
	"qr_url",
	"previous_receipt_hash",
	"device_signature_hash",
	"device_signature",
	"payload",
)


class ZimraFiscalReceipt(Document):
	def validate(self):
		if self.is_new():
			return
		before = self.get_doc_before_save()
		if not before:
			return
		changed = [f for f in SIGNED_FIELDS if str(before.get(f) or "") != str(self.get(f) or "")]
		if changed:
			frappe.throw(
				_("A fiscal receipt cannot be changed once signed ({0}).").format(", ".join(changed)),
				title=_("Fiscal Receipt Locked"),
			)

	def on_trash(self):
		frappe.throw(_("Fiscal receipts are permanent records and cannot be deleted."))


def on_doctype_update():
	frappe.db.add_unique(
		"Zimra Fiscal Receipt", ["fiscal_device", "receipt_global_no"], "unique_device_global_no"
	)
	frappe.db.add_unique("Zimra Fiscal Receipt", ["reference_doctype", "reference_name"], "unique_reference")
	frappe.db.add_index("Zimra Fiscal Receipt", ["fiscal_device", "sync_status"])
	frappe.db.add_index("Zimra Fiscal Receipt", ["fiscal_day", "sync_status"])
