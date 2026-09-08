# Copyright (c) 2026, Donald Chinhuru and contributors
# For license information, please see license.txt

"""Document hooks wired in hooks.py `doc_events`. A Sales Invoice created
from an already-fiscalised POS Invoice inherits that receipt's identity
instead of being fiscalised again (ZIMRA sees one receipt per sale, not one
per ERPNext document).
"""

from __future__ import annotations

import frappe

from zw_compliance.zimra.connector import ZimraConnector

_INHERITED_FIELDS = [
	"custom_zimra_is_fiscalised",
	"custom_zimra_receipt_id",
	"custom_zimra_global_no",
	"custom_zimra_receipt_counter",
	"custom_zimra_fiscal_day_no",
	"custom_zimra_fiscal_date",
	"custom_zimra_server_signature",
	"custom_zimra_qr_code",
	"custom_zimra_qr_code_view",
	"custom_zimra_verification_code",
]


def fiscalise_on_submit(doc, method=None):
	if doc.get("custom_zimra_is_fiscalised"):
		return

	if doc.doctype == "Sales Invoice":
		source_pos_names = {item.pos_invoice for item in doc.items if item.get("pos_invoice")}
		if source_pos_names:
			_inherit_from_pos(doc, source_pos_names)
			return

	if not frappe.db.exists("Zimra Settings") or not frappe.db.get_single_value("Zimra Settings", "device_id"):
		return  # device not configured yet - do not block invoice submission

	ZimraConnector().fiscalise(doc)


def _inherit_from_pos(doc, source_pos_names):
	for pos_name in source_pos_names:
		pos_data = frappe.db.get_value("POS Invoice", pos_name, _INHERITED_FIELDS, as_dict=True)
		if pos_data and pos_data.custom_zimra_is_fiscalised:
			doc.db_set({**pos_data, "custom_zimra_fiscalisation_mode": "Inherited from POS"})
			_log_inherited(doc, pos_data)
			return
	# No fiscalised source POS Invoice found (e.g. POS Invoice not yet
	# fiscalised, or a non-POS-consolidated invoice) - fall through to a
	# normal fresh fiscalisation attempt.
	ZimraConnector().fiscalise(doc)


def _log_inherited(doc, pos_data):
	frappe.get_doc(
		{
			"doctype": "Zimra Receipt Log",
			"reference_doctype": doc.doctype,
			"reference_docname": doc.name,
			"receipt_id": pos_data.custom_zimra_receipt_id,
			"receipt_global_no": pos_data.custom_zimra_global_no,
			"receipt_counter": pos_data.custom_zimra_receipt_counter,
			"receipt_type": "FiscalInvoice",
			"currency": doc.currency,
			"total_amount": doc.base_grand_total,
			"is_fiscalised": 1,
			"fiscalisation_mode": "Inherited from POS",
			"device_signature": pos_data.custom_zimra_server_signature,
		}
	).insert(ignore_permissions=True)
