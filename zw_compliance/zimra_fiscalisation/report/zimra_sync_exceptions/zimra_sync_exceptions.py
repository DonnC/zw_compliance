# Copyright (c) 2026, Donald Chinhuru and contributors
# For license information, please see license.txt
"""Everything that needs attention: receipts ZIMRA has not accepted yet, receipts
ZIMRA flagged (Grey / Yellow / Red, spec §8.2.1), and fiscal days that cannot close."""

import frappe
from frappe import _
from frappe.utils import flt, now_datetime, time_diff_in_hours

from zw_compliance.fdms.errors import describe


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return columns(), receipts(filters) + days(filters)


def columns():
	return [
		{"label": _("Kind"), "fieldname": "kind", "fieldtype": "Data", "width": 100},
		{
			"label": _("Record"),
			"fieldname": "record",
			"fieldtype": "Dynamic Link",
			"options": "record_type",
			"width": 160,
		},
		{
			"label": _("Document"),
			"fieldname": "reference_name",
			"fieldtype": "Dynamic Link",
			"options": "reference_doctype",
			"width": 160,
		},
		{
			"label": _("Device"),
			"fieldname": "fiscal_device",
			"fieldtype": "Link",
			"options": "Zimra Fiscal Device",
			"width": 100,
		},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 110},
		{"label": _("Code"), "fieldname": "code", "fieldtype": "Data", "width": 160},
		{"label": _("Problem"), "fieldname": "problem", "fieldtype": "Data", "width": 300},
		{"label": _("What to do"), "fieldname": "fix", "fieldtype": "Data", "width": 320},
		{"label": _("Age (hours)"), "fieldname": "age", "fieldtype": "Float", "precision": 1, "width": 90},
		{"label": _("Attempts"), "fieldname": "attempts", "fieldtype": "Int", "width": 75},
	]


def _base(filters):
	conditions = {}
	if filters.company:
		conditions["company"] = filters.company
	if filters.fiscal_device:
		conditions["fiscal_device"] = filters.fiscal_device
	return conditions


def receipts(filters):
	rows = []
	now = now_datetime()
	fields = [
		"name",
		"reference_doctype",
		"reference_name",
		"fiscal_device",
		"sync_status",
		"worst_colour",
		"receipt_date",
		"attempts",
		"last_error",
		"last_error_code",
	]
	pending = frappe.get_list(
		"Zimra Fiscal Receipt",
		filters={**_base(filters), "sync_status": ["!=", "Submitted"]},
		fields=fields,
		order_by="fiscal_day_no asc, receipt_global_no asc",
	)
	flagged = frappe.get_list(
		"Zimra Fiscal Receipt",
		filters={**_base(filters), "worst_colour": ["is", "set"]},
		fields=fields,
		order_by="fiscal_day_no asc, receipt_global_no asc",
	)
	for r in pending:
		entry = describe(r.last_error_code)
		rows.append(
			{
				"kind": _("Not accepted"),
				"record_type": "Zimra Fiscal Receipt",
				"record": r.name,
				"reference_doctype": r.reference_doctype,
				"reference_name": r.reference_name,
				"fiscal_device": r.fiscal_device,
				"status": r.sync_status,
				"code": r.last_error_code,
				"problem": r.last_error or _("Waiting to be sent"),
				"fix": entry.fix
				if entry
				else _(
					"Sent automatically when ZIMRA is reachable. Check the device's last sync error if this persists."
				),
				"age": flt(time_diff_in_hours(now, r.receipt_date), 1),
				"attempts": r.attempts,
			}
		)
	for r in flagged:
		if filters.colour and r.worst_colour != filters.colour:
			continue
		for e in frappe.get_all(
			"Zimra Validation Error",
			filters={"parent": r.name, "parenttype": "Zimra Fiscal Receipt"},
			fields=["code", "colour", "description", "fix"],
		):
			rows.append(
				{
					"kind": _("Flagged {0}").format(e.colour),
					"record_type": "Zimra Fiscal Receipt",
					"record": r.name,
					"reference_doctype": r.reference_doctype,
					"reference_name": r.reference_name,
					"fiscal_device": r.fiscal_device,
					"status": e.colour,
					"code": e.code,
					"problem": e.description,
					"fix": e.fix,
					"age": flt(time_diff_in_hours(now, r.receipt_date), 1),
					"attempts": r.attempts,
				}
			)
	return rows


def days(filters):
	rows = []
	for d in frappe.get_list(
		"Zimra Fiscal Day",
		filters={
			**_base(filters),
			"status": ["in", ["Close Failed", "Close Requested"]],
			"last_error": ["is", "set"],
		},
		fields=["name", "fiscal_device", "status", "closing_error_code", "last_error", "close_requested_on"],
	):
		entry = describe(d.closing_error_code)
		rows.append(
			{
				"kind": _("Fiscal day"),
				"record_type": "Zimra Fiscal Day",
				"record": d.name,
				"fiscal_device": d.fiscal_device,
				"status": d.status,
				"code": d.closing_error_code,
				"problem": d.last_error,
				"fix": entry.fix if entry else None,
				"age": flt(time_diff_in_hours(now_datetime(), d.close_requested_on), 1)
				if d.close_requested_on
				else None,
			}
		)
	return rows
