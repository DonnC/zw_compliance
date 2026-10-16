# Copyright (c) 2026, Donald Chinhuru and contributors
# For license information, please see license.txt
"""Every fiscal receipt issued, with its ZIMRA status and validation colour."""

import frappe
from frappe import _
from frappe.utils import add_days


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return columns(), data(filters)


def columns():
	return [
		{
			"label": _("Fiscal Receipt"),
			"fieldname": "name",
			"fieldtype": "Link",
			"options": "Zimra Fiscal Receipt",
			"width": 165,
		},
		{"label": _("Document Type"), "fieldname": "reference_doctype", "fieldtype": "Data", "width": 110},
		{
			"label": _("Document"),
			"fieldname": "reference_name",
			"fieldtype": "Dynamic Link",
			"options": "reference_doctype",
			"width": 190,
		},
		{"label": _("Type"), "fieldname": "receipt_type", "fieldtype": "Data", "width": 115},
		{"label": _("Date"), "fieldname": "receipt_date", "fieldtype": "Datetime", "width": 180},
		{
			"label": _("Device"),
			"fieldname": "fiscal_device",
			"fieldtype": "Link",
			"options": "Zimra Fiscal Device",
			"width": 115,
		},
		{"label": _("Day No"), "fieldname": "fiscal_day_no", "fieldtype": "Int", "width": 75},
		{"label": _("Counter"), "fieldname": "receipt_counter", "fieldtype": "Int", "width": 85},
		{"label": _("Global No"), "fieldname": "receipt_global_no", "fieldtype": "Int", "width": 95},
		{
			"label": _("Currency"),
			"fieldname": "receipt_currency",
			"fieldtype": "Link",
			"options": "Currency",
			"width": 90,
		},
		{
			"label": _("Total"),
			"fieldname": "receipt_total",
			"fieldtype": "Currency",
			"options": "receipt_currency",
			"width": 125,
		},
		{"label": _("ZIMRA Status"), "fieldname": "sync_status", "fieldtype": "Data", "width": 115},
		{"label": _("Validation"), "fieldname": "worst_colour", "fieldtype": "Data", "width": 100},
		{
			"label": _("Verification Code"),
			"fieldname": "verification_code",
			"fieldtype": "Data",
			"width": 195,
		},
		{"label": _("ZIMRA Receipt ID"), "fieldname": "fdms_receipt_id", "fieldtype": "Data", "width": 135},
	]


def data(filters):
	conditions = {}
	for key in (
		"company",
		"fiscal_device",
		"receipt_type",
		"receipt_currency",
		"sync_status",
		"worst_colour",
	):
		if filters.get(key):
			conditions[key] = filters.get(key)
	if filters.from_date and filters.to_date:
		conditions["receipt_date"] = ["between", [filters.from_date, add_days(filters.to_date, 1)]]
	return frappe.get_list(
		"Zimra Fiscal Receipt",
		filters=conditions,
		fields=[c["fieldname"] for c in columns()],
		order_by="fiscal_device asc, receipt_global_no desc",
	)
