# Copyright (c) 2026, Donald Chinhuru and contributors
# For license information, please see license.txt

"""Output VAT (from fiscalised ZIMRA receipts) vs Input VAT (from GL
Entries against each tax's provisioned Input VAT account, i.e. VAT paid on
supplier purchases) per ZIMRA tax rate, for a period - a VAT201-style net
payable/receivable summary. FDMS itself never sees purchases; the input
side is entirely ERPNext's own books, reconciled here against the same
Zimra Tax Mapping rows the sales side uses (see zw_compliance.zimra.setup).
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns():
	return [
		{"label": _("ZIMRA Tax"), "fieldname": "zimra_tax_name", "fieldtype": "Data", "width": 200},
		{"label": _("Tax %"), "fieldname": "zimra_tax_percent", "fieldtype": "Float", "width": 70},
		{"label": _("Output VAT (Sales)"), "fieldname": "output_vat", "fieldtype": "Currency", "width": 160},
		{"label": _("Input VAT (Purchases)"), "fieldname": "input_vat", "fieldtype": "Currency", "width": 170},
		{"label": _("Net VAT Payable"), "fieldname": "net_vat", "fieldtype": "Currency", "width": 150},
	]


def get_data(filters):
	mappings = frappe.get_all(
		"Zimra Tax Mapping",
		fields=["name", "zimra_tax_id", "zimra_tax_name", "zimra_tax_percent", "erpnext_input_account"],
		order_by="zimra_tax_id",
	)

	rows = []
	total_output = total_input = 0.0
	for m in mappings:
		output_vat = _get_output_vat(m.zimra_tax_id, filters)
		input_vat = _get_input_vat(m.erpnext_input_account, filters)
		if not output_vat and not input_vat:
			continue
		rows.append(
			{
				"zimra_tax_name": m.zimra_tax_name or _("Tax {0}").format(m.zimra_tax_id),
				"zimra_tax_percent": m.zimra_tax_percent,
				"output_vat": output_vat,
				"input_vat": input_vat,
				"net_vat": output_vat - input_vat,
			}
		)
		total_output += output_vat
		total_input += input_vat

	if rows:
		rows.append(
			{
				"zimra_tax_name": _("Total"),
				"output_vat": total_output,
				"input_vat": total_input,
				"net_vat": total_output - total_input,
			}
		)
	return rows


def _get_output_vat(tax_id, filters) -> float:
	conditions = ["log.is_fiscalised = 1", "det.tax_id = %(tax_id)s"]
	values = {"tax_id": tax_id}
	if filters.get("from_date"):
		conditions.append("log.creation >= %(from_date)s")
		values["from_date"] = filters.from_date
	if filters.get("to_date"):
		conditions.append("log.creation <= %(to_date)s")
		values["to_date"] = filters.to_date

	result = frappe.db.sql(
		f"""
		select sum(det.tax_amount)
		from `tabZimra Receipt Tax Detail` det
		inner join `tabZimra Receipt Log` log on det.parent = log.name
		where {" and ".join(conditions)}
		""",
		values,
	)
	return flt(result[0][0]) if result and result[0][0] else 0.0


def _get_input_vat(account, filters) -> float:
	if not account:
		return 0.0
	conditions = ["account = %(account)s", "is_cancelled = 0"]
	values = {"account": account}
	if filters.get("from_date"):
		conditions.append("posting_date >= %(from_date)s")
		values["from_date"] = filters.from_date
	if filters.get("to_date"):
		conditions.append("posting_date <= %(to_date)s")
		values["to_date"] = filters.to_date
	if filters.get("company"):
		conditions.append("company = %(company)s")
		values["company"] = filters.company

	result = frappe.db.sql(
		f"""
		select sum(debit) - sum(credit)
		from `tabGL Entry`
		where {" and ".join(conditions)}
		""",
		values,
	)
	return flt(result[0][0]) if result and result[0][0] else 0.0
