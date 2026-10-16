# Copyright (c) 2026, Donald Chinhuru and contributors
# For license information, please see license.txt
"""Health of every fiscal device: fiscal day age against ZIMRA's maximum,
waiting receipts, certificate expiry and connectivity."""

import frappe
from frappe import _
from frappe.utils import flt, now_datetime, time_diff_in_hours

from zw_compliance.fiscalisation.device import certificate_days_left


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return columns(), data(filters)


def columns():
	return [
		{
			"label": _("Device"),
			"fieldname": "name",
			"fieldtype": "Link",
			"options": "Zimra Fiscal Device",
			"width": 110,
		},
		{"label": _("Name"), "fieldname": "device_name", "fieldtype": "Data", "width": 150},
		{
			"label": _("Company"),
			"fieldname": "company",
			"fieldtype": "Link",
			"options": "Company",
			"width": 150,
		},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 100},
		{"label": _("Environment"), "fieldname": "environment", "fieldtype": "Data", "width": 90},
		{
			"label": _("Fiscal Day"),
			"fieldname": "current_fiscal_day",
			"fieldtype": "Link",
			"options": "Zimra Fiscal Day",
			"width": 160,
		},
		{"label": _("Day Status"), "fieldname": "day_status", "fieldtype": "Data", "width": 110},
		{
			"label": _("Hours Open"),
			"fieldname": "hours_open",
			"fieldtype": "Float",
			"precision": 1,
			"width": 90,
		},
		{"label": _("Max Hours"), "fieldname": "day_max_hours", "fieldtype": "Int", "width": 80},
		{"label": _("Receipts Today"), "fieldname": "receipt_counter", "fieldtype": "Int", "width": 100},
		{"label": _("Waiting for ZIMRA"), "fieldname": "waiting", "fieldtype": "Int", "width": 110},
		{"label": _("Rejected"), "fieldname": "rejected", "fieldtype": "Int", "width": 80},
		{"label": _("Grey/Red Today"), "fieldname": "blocking_receipts", "fieldtype": "Int", "width": 100},
		{
			"label": _("Certificate Days Left"),
			"fieldname": "certificate_days_left",
			"fieldtype": "Int",
			"width": 120,
		},
		{"label": _("Last Ping"), "fieldname": "last_ping", "fieldtype": "Datetime", "width": 150},
		{"label": _("Last Sync"), "fieldname": "last_sync", "fieldtype": "Datetime", "width": 150},
		{"label": _("Last Sync Error"), "fieldname": "last_sync_error", "fieldtype": "Data", "width": 260},
	]


def data(filters):
	conditions = {"company": filters.company} if filters.company else {}
	rows = []
	for d in frappe.get_list(
		"Zimra Fiscal Device",
		filters=conditions,
		fields=[
			"name",
			"device_name",
			"company",
			"status",
			"environment",
			"current_fiscal_day",
			"day_max_hours",
			"certificate_valid_till",
			"last_ping",
			"last_sync",
			"last_sync_error",
		],
		order_by="company asc, device_name asc",
	):
		row = {**d, "certificate_days_left": certificate_days_left(d)}
		if d.current_fiscal_day:
			day = frappe.db.get_value(
				"Zimra Fiscal Day",
				d.current_fiscal_day,
				["status", "fiscal_day_opened", "receipt_counter", "blocking_receipts"],
				as_dict=True,
			)
			row.update(
				{
					"day_status": day.status,
					"receipt_counter": day.receipt_counter,
					"blocking_receipts": day.blocking_receipts,
					"hours_open": flt(time_diff_in_hours(now_datetime(), day.fiscal_day_opened), 1)
					if day.status not in ("Closed", "Pending Open")
					else None,
				}
			)
		row["waiting"] = frappe.db.count(
			"Zimra Fiscal Receipt", {"fiscal_device": d.name, "sync_status": ["in", ["Queued", "Retrying"]]}
		)
		row["rejected"] = frappe.db.count(
			"Zimra Fiscal Receipt", {"fiscal_device": d.name, "sync_status": "Rejected"}
		)
		rows.append(row)
	return rows
