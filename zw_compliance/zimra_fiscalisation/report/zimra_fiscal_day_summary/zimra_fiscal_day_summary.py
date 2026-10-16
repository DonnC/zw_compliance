# Copyright (c) 2026, Donald Chinhuru and contributors
# For license information, please see license.txt
"""Daily totals per fiscal day, currency and ZIMRA tax - the Z report figures
(spec v7.2 §10.4-§10.5) as a filterable table. Built from the counters signed
in each fiscal day, so it matches what was reported to ZIMRA."""

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return columns(), data(filters)


def columns():
	return [
		{
			"label": _("Fiscal Day"),
			"fieldname": "fiscal_day",
			"fieldtype": "Link",
			"options": "Zimra Fiscal Day",
			"width": 170,
		},
		{
			"label": _("Device"),
			"fieldname": "fiscal_device",
			"fieldtype": "Link",
			"options": "Zimra Fiscal Device",
			"width": 110,
		},
		{"label": _("Day No"), "fieldname": "fiscal_day_no", "fieldtype": "Int", "width": 70},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 110},
		{"label": _("Opened"), "fieldname": "fiscal_day_opened", "fieldtype": "Datetime", "width": 150},
		{
			"label": _("Currency"),
			"fieldname": "currency",
			"fieldtype": "Link",
			"options": "Currency",
			"width": 80,
		},
		{"label": _("ZIMRA Tax"), "fieldname": "tax", "fieldtype": "Data", "width": 160},
		{
			"label": _("Net Sales"),
			"fieldname": "net",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"label": _("Tax"),
			"fieldname": "tax_amount",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 110,
		},
		{
			"label": _("Gross Sales"),
			"fieldname": "gross",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"label": _("Of which Credit Notes"),
			"fieldname": "credit",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"label": _("Of which Debit Notes"),
			"fieldname": "debit",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
	]


def data(filters):
	conditions = {}
	if filters.company:
		conditions["company"] = filters.company
	if filters.fiscal_device:
		conditions["fiscal_device"] = filters.fiscal_device
	if filters.status:
		conditions["status"] = filters.status
	if filters.from_date and filters.to_date:
		conditions["fiscal_day_date"] = ["between", [filters.from_date, filters.to_date]]
	days = frappe.get_list(
		"Zimra Fiscal Day",
		filters=conditions,
		fields=["name", "company", "fiscal_device", "fiscal_day_no", "status", "fiscal_day_opened"],
		order_by="fiscal_day_opened desc",
	)
	names = {
		(m.company, m.zimra_tax_id): m.zimra_tax_name
		for m in frappe.get_all("Zimra Tax Mapping", fields=["company", "zimra_tax_id", "zimra_tax_name"])
	}
	rows = []
	for day in days:
		groups = {}
		for c in frappe.get_all(
			"Zimra Fiscal Counter",
			filters={
				"parent": day.name,
				"parenttype": "Zimra Fiscal Day",
				"counter_type": ["!=", "BalanceByMoneyType"],
			},
			fields=["counter_type", "currency", "tax_id", "tax_percent", "is_exempt", "value"],
		):
			if filters.currency and c.currency != filters.currency:
				continue
			g = groups.setdefault(
				(c.currency, c.tax_id, c.is_exempt, c.tax_percent),
				{"gross": 0, "tax": 0, "credit": 0, "debit": 0},
			)
			if c.counter_type.endswith("TaxByTax"):
				g["tax"] += flt(c.value)
			else:
				g["gross"] += flt(c.value)
				if c.counter_type == "CreditNoteByTax":
					g["credit"] += flt(c.value)
				elif c.counter_type == "DebitNoteByTax":
					g["debit"] += flt(c.value)
		for (currency, tax_id, exempt, percent), g in sorted(
			groups.items(), key=lambda kv: (kv[0][0], kv[0][2], -flt(kv[0][3]))
		):
			rows.append(
				{
					**day,
					"fiscal_day": day.name,
					"currency": currency,
					"tax": names.get((day.company, tax_id))
					or (_("Exempt") if exempt else f"{flt(percent):g}%"),
					"net": g["gross"] - g["tax"],
					"tax_amount": g["tax"],
					"gross": g["gross"],
					"credit": g["credit"],
					"debit": g["debit"],
				}
			)
	return rows
