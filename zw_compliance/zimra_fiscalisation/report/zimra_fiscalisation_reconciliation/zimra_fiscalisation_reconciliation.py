# Copyright (c) 2026, Donald Chinhuru and contributors
# For license information, please see license.txt
"""Submitted sales documents compared with their fiscal receipts.

Finds compliance gaps: invoices of a company with fiscal devices that have no
fiscal receipt, receipts not (yet) accepted by ZIMRA, and totals or VAT that
differ between the invoice and what was fiscalised.
"""

import frappe
from frappe import _
from frappe.utils import flt

DOCTYPES = ("Sales Invoice", "POS Invoice")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.company:
		frappe.throw(_("Select a company."))
	rows = data(filters)
	if filters.only_issues:
		rows = [r for r in rows if r["issue"]]
	return columns(), rows


def columns():
	return [
		{"label": _("Document Type"), "fieldname": "doctype", "fieldtype": "Data", "width": 100},
		{
			"label": _("Document"),
			"fieldname": "name",
			"fieldtype": "Dynamic Link",
			"options": "doctype",
			"width": 160,
		},
		{"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{
			"label": _("Customer"),
			"fieldname": "customer",
			"fieldtype": "Link",
			"options": "Customer",
			"width": 150,
		},
		{
			"label": _("Currency"),
			"fieldname": "currency",
			"fieldtype": "Link",
			"options": "Currency",
			"width": 75,
		},
		{
			"label": _("Invoice Total"),
			"fieldname": "grand_total",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"label": _("Fiscalised Total"),
			"fieldname": "receipt_total",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"label": _("Invoice VAT"),
			"fieldname": "invoice_vat",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 110,
		},
		{
			"label": _("Fiscalised VAT"),
			"fieldname": "receipt_vat",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 110,
		},
		{"label": _("ZIMRA Status"), "fieldname": "zimra_status", "fieldtype": "Data", "width": 120},
		{
			"label": _("Fiscal Receipt"),
			"fieldname": "zimra_fiscal_receipt",
			"fieldtype": "Link",
			"options": "Zimra Fiscal Receipt",
			"width": 150,
		},
		{"label": _("Issue"), "fieldname": "issue", "fieldtype": "Data", "width": 260},
	]


def data(filters):
	rows = []
	for doctype in DOCTYPES:
		docs = frappe.get_list(
			doctype,
			filters={
				"company": filters.company,
				"docstatus": 1,
				"posting_date": ["between", [filters.from_date, filters.to_date]],
			},
			fields=[
				"name",
				"posting_date",
				"customer",
				"currency",
				"grand_total",
				"total_taxes_and_charges",
				*(["is_consolidated"] if doctype == "Sales Invoice" else []),
				"zimra_status",
				"zimra_fiscal_receipt",
			],
			order_by="posting_date asc, name asc",
		)
		receipt_names = [d.zimra_fiscal_receipt for d in docs if d.zimra_fiscal_receipt]
		receipts = (
			{
				r.name: r
				for r in frappe.get_all(
					"Zimra Fiscal Receipt",
					filters={"name": ["in", receipt_names]},
					fields=["name", "receipt_total", "receipt_currency", "sync_status"],
				)
			}
			if receipt_names
			else {}
		)
		vat = {}
		if receipt_names:
			for t in frappe.get_all(
				"Zimra Fiscal Receipt Tax",
				filters={"parent": ["in", receipt_names], "parenttype": "Zimra Fiscal Receipt"},
				fields=["parent", "tax_amount"],
			):
				vat[t.parent] = vat.get(t.parent, 0) + flt(t.tax_amount)
		for d in docs:
			if d.get("is_consolidated") or d.zimra_status == "Not Applicable":
				continue
			receipt = receipts.get(d.zimra_fiscal_receipt)
			row = {**d, "doctype": doctype, "invoice_vat": d.total_taxes_and_charges}
			issues = []
			if not receipt:
				issues.append(_("No fiscal receipt"))
			else:
				row["receipt_total"] = receipt.receipt_total
				row["receipt_vat"] = vat.get(receipt.name, 0)
				if receipt.sync_status != "Submitted":
					issues.append(_("Not accepted by ZIMRA ({0})").format(receipt.sync_status))
				if abs(flt(receipt.receipt_total) - flt(d.grand_total)) >= 0.005:
					issues.append(_("Total differs"))
				if abs(flt(row["receipt_vat"]) - flt(d.total_taxes_and_charges)) >= 0.005:
					issues.append(
						_("VAT differs by {0}").format(
							flt(row["receipt_vat"]) - flt(d.total_taxes_and_charges)
						)
					)
				if receipt.receipt_currency != d.currency:
					issues.append(_("Currency differs"))
			row["issue"] = "; ".join(issues)
			rows.append(row)
	return rows
