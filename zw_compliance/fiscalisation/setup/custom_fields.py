"""Custom fields added to ERPNext doctypes.

Defined in code and applied with ``create_custom_fields`` on install and on
every migrate, so they are idempotent and versioned with the app. Each field
carries ``module`` so ``bench remove-app`` deletes it. Fiscal identifiers are
``read_only`` and ``no_copy`` (they are also cleared explicitly on insert, see
:mod:`..guards`), and ``allow_on_submit`` because they are written after the
invoice is submitted.
"""

from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from ..constants import MODULE

ZIMRA_STATUS = "\nNot Applicable\nFiscalised via POS\nQueued\nSubmitted\nRetrying\nRejected"


def _fiscal_fields(insert_after: str) -> list[dict]:
	common = {"read_only": 1, "no_copy": 1, "allow_on_submit": 1, "module": MODULE}
	return [
		{
			"fieldname": "zimra_section",
			"fieldtype": "Section Break",
			"label": "ZIMRA Fiscalisation",
			"insert_after": insert_after,
			"collapsible": 1,
			"collapsible_depends_on": "eval:doc.docstatus==0 && (doc.is_return || doc.is_debit_note)",
			"module": MODULE,
		},
		{
			"fieldname": "zimra_receipt_notes",
			"fieldtype": "Small Text",
			"label": "Reason for Credit / Debit Note",
			"insert_after": "zimra_section",
			"no_copy": 1,
			"description": "Printed on the fiscal receipt and sent to ZIMRA. Mandatory for credit and debit notes.",
			"mandatory_depends_on": "eval:doc.is_return || doc.is_debit_note",
			"module": MODULE,
		},
		{
			**common,
			"fieldname": "zimra_status",
			"fieldtype": "Select",
			"label": "ZIMRA Status",
			"options": ZIMRA_STATUS,
			"insert_after": "zimra_receipt_notes",
			"in_standard_filter": 1,
			"print_hide": 1,
		},
		{
			**common,
			"fieldname": "zimra_fiscal_device",
			"fieldtype": "Link",
			"label": "Fiscal Device",
			"options": "Zimra Fiscal Device",
			"insert_after": "zimra_status",
			"print_hide": 1,
		},
		{
			**common,
			"fieldname": "zimra_fiscal_receipt",
			"fieldtype": "Link",
			"label": "Fiscal Receipt",
			"options": "Zimra Fiscal Receipt",
			"insert_after": "zimra_fiscal_device",
			"print_hide": 1,
		},
		{
			**common,
			"fieldname": "zimra_fdms_receipt_id",
			"fieldtype": "Data",
			"label": "ZIMRA Receipt ID",
			"insert_after": "zimra_fiscal_receipt",
			"print_hide": 1,
		},
		{
			"fieldname": "zimra_column_break",
			"fieldtype": "Column Break",
			"insert_after": "zimra_fdms_receipt_id",
			"module": MODULE,
		},
		{
			**common,
			"fieldname": "zimra_fiscal_day_no",
			"fieldtype": "Int",
			"label": "Fiscal Day No",
			"insert_after": "zimra_column_break",
		},
		{
			**common,
			"fieldname": "zimra_receipt_counter",
			"fieldtype": "Int",
			"label": "Receipt Counter",
			"insert_after": "zimra_fiscal_day_no",
		},
		{
			**common,
			"fieldname": "zimra_receipt_global_no",
			"fieldtype": "Int",
			"label": "Receipt Global No",
			"insert_after": "zimra_receipt_counter",
		},
		{
			**common,
			"fieldname": "zimra_receipt_date",
			"fieldtype": "Datetime",
			"label": "Fiscal Receipt Date",
			"insert_after": "zimra_receipt_global_no",
		},
		{
			**common,
			"fieldname": "zimra_verification_code",
			"fieldtype": "Data",
			"label": "Verification Code",
			"insert_after": "zimra_receipt_date",
		},
		{
			**common,
			"fieldname": "zimra_qr_data",
			"fieldtype": "Small Text",
			"label": "QR Code Content",
			"insert_after": "zimra_verification_code",
			"print_hide": 1,
			"hidden": 1,
		},
	]


def custom_fields() -> dict:
	hs_code = {
		"fieldname": "zimra_hs_code",
		"fieldtype": "Link",
		"label": "ZIMRA HS Code",
		"options": "Zimra HSN Code",
		"link_filters": '[["Zimra HSN Code","usable_on_receipt","=",1]]',
		"description": "National HS code sent on fiscal receipts; mandatory for VAT-registered businesses (ZIMRA RCPT047).",
		"module": MODULE,
	}
	return {
		"Sales Invoice": [
			*_fiscal_fields("terms"),
			{
				"fieldname": "zimra_client_reference",
				"fieldtype": "Data",
				"label": "API Client Reference",
				"insert_after": "zimra_qr_data",
				"read_only": 1,
				"no_copy": 1,
				"unique": 1,
				"search_index": 1,
				"print_hide": 1,
				"description": "Idempotency key of a sale received through the remote API.",
				"module": MODULE,
			},
		],
		"POS Invoice": _fiscal_fields("terms"),
		"Customer": [
			{
				"fieldname": "zimra_vat_number",
				"fieldtype": "Data",
				"label": "VAT Number",
				"length": 9,
				"insert_after": "tax_id",
				"description": "9-digit ZIMRA VAT number, printed on fiscal invoices. The TIN goes in Tax ID.",
				"module": MODULE,
			},
			{
				"fieldname": "zimra_trade_name",
				"fieldtype": "Data",
				"label": "Trade Name",
				"insert_after": "zimra_vat_number",
				"description": "Store or branch name printed under the buyer's registered name.",
				"module": MODULE,
			},
		],
		"Item": [{**hs_code, "insert_after": "item_group"}],
		"Item Group": [
			{
				**hs_code,
				"insert_after": "parent_item_group",
				"description": "Default HS code for items in this group that have none of their own.",
			}
		],
	}


def ensure_module_defs() -> None:
	"""Frappe creates Module Defs only when an app is installed; a module added
	in a later release needs its Module Def before records can reference it."""
	for module in frappe.get_module_list("zw_compliance"):
		if not frappe.db.exists("Module Def", module):
			frappe.get_doc(
				{"doctype": "Module Def", "module_name": module, "app_name": "zw_compliance"}
			).insert(ignore_permissions=True)


def make_custom_fields() -> None:
	ensure_module_defs()
	create_custom_fields(custom_fields(), update=True)
