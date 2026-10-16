# Copyright (c) 2026, Donald Chinhuru and contributors
# For license information, please see license.txt
"""Output VAT on fiscalised sales against input VAT on purchases, per ZIMRA tax.

Output VAT comes from the fiscal receipts (what ZIMRA received), in the receipt
currency. Input VAT comes from the General Ledger on each tax's input VAT
account (see Zimra Tax Mapping), in company currency - ZIMRA's fiscal devices
never see purchases.
"""

import frappe
from frappe import _
from frappe.utils import add_days, flt


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.company:
		frappe.throw(_("Select a company."))
	return columns(), data(filters)


def columns():
	return [
		{"label": _("ZIMRA Tax"), "fieldname": "tax", "fieldtype": "Data", "width": 180},
		{
			"label": _("Currency"),
			"fieldname": "currency",
			"fieldtype": "Link",
			"options": "Currency",
			"width": 80,
		},
		{
			"label": _("Gross Sales"),
			"fieldname": "gross",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 130,
		},
		{
			"label": _("Net Sales"),
			"fieldname": "net",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 130,
		},
		{
			"label": _("Output VAT"),
			"fieldname": "output_vat",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"label": _("Input VAT"),
			"fieldname": "input_vat",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"label": _("Net VAT Payable"),
			"fieldname": "net_vat",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 130,
		},
	]


def data(filters):
	company_currency = frappe.get_cached_value("Company", filters.company, "default_currency")
	mappings = frappe.get_all(
		"Zimra Tax Mapping",
		filters={"company": filters.company},
		fields=["zimra_tax_id", "zimra_tax_name", "zimra_tax_percent", "is_exempt", "input_account"],
		order_by="is_exempt asc, zimra_tax_percent desc",
	)
	receipts = {
		r.name: r.receipt_currency
		for r in frappe.get_list(
			"Zimra Fiscal Receipt",
			filters={
				"company": filters.company,
				"sync_status": ["!=", "Rejected"],
				"receipt_date": ["between", [filters.from_date, add_days(filters.to_date, 1)]],
			},
			fields=["name", "receipt_currency"],
		)
	}
	output = {}
	if receipts:
		for row in frappe.get_all(
			"Zimra Fiscal Receipt Tax",
			filters={"parent": ["in", list(receipts)], "parenttype": "Zimra Fiscal Receipt"},
			fields=["parent", "tax_id", "tax_amount", "sales_amount_with_tax"],
		):
			currency = receipts[row.parent]
			o = output.setdefault((row.tax_id, currency), {"gross": 0, "tax": 0})
			o["gross"] += flt(row.sales_amount_with_tax)
			o["tax"] += flt(row.tax_amount)

	rows = []
	for m in mappings:
		currencies = {c for (tax_id, c) in output if tax_id == m.zimra_tax_id} | {company_currency}
		input_vat = _input_vat(m.input_account, filters)
		for currency in sorted(currencies):
			o = output.get((m.zimra_tax_id, currency), {"gross": 0, "tax": 0})
			i = input_vat if currency == company_currency else 0
			if not (o["gross"] or o["tax"] or i):
				continue
			rows.append(
				{
					"tax": m.zimra_tax_name,
					"currency": currency,
					"gross": o["gross"],
					"net": o["gross"] - o["tax"],
					"output_vat": o["tax"],
					"input_vat": i,
					"net_vat": o["tax"] - i,
				}
			)
	return rows


def _input_vat(account, filters) -> float:
	if not account:
		return 0
	result = frappe.db.sql(
		"""select sum(debit) - sum(credit) from `tabGL Entry`
		where account = %s and company = %s and is_cancelled = 0 and posting_date between %s and %s""",
		(account, filters.company, filters.from_date, filters.to_date),
	)
	return flt(result[0][0]) if result else 0
