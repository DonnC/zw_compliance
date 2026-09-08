# Copyright (c) 2026, Donald Chinhuru and contributors
# For license information, please see license.txt

"""ZIMRA fiscal-day Z-report: per fiscal day, the exact counters ZIMRA
holds (tax-by-tax and money-type balances), as signed at CloseDay. Sourced
from Zimra Fiscal Day / Zimra Fiscal Day Detail, populated by
zw_compliance.zimra.fiscal_day.record_day_closed() from the device's own
counter state - not recomputed from invoices - so it always matches what
was actually reported to ZIMRA.
"""

from __future__ import annotations

import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns():
	return [
		{"label": _("Fiscal Day"), "fieldname": "fiscal_day", "fieldtype": "Link", "options": "Zimra Fiscal Day", "width": 160},
		{"label": _("Day No"), "fieldname": "fiscal_day_no", "fieldtype": "Int", "width": 70},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 90},
		{"label": _("Opened"), "fieldname": "fiscal_day_opened", "fieldtype": "Datetime", "width": 150},
		{"label": _("Closed"), "fieldname": "fiscal_day_closed", "fieldtype": "Datetime", "width": 150},
		{"label": _("Currency"), "fieldname": "currency", "fieldtype": "Data", "width": 80},
		{"label": _("Tax ID"), "fieldname": "tax_id", "fieldtype": "Int", "width": 60},
		{"label": _("Tax %"), "fieldname": "tax_percent", "fieldtype": "Float", "width": 70},
		{"label": _("Money Type"), "fieldname": "money_type", "fieldtype": "Data", "width": 110},
		{"label": _("Net Amount"), "fieldname": "net_amount", "fieldtype": "Currency", "width": 120},
		{"label": _("Tax Amount"), "fieldname": "tax_amount", "fieldtype": "Currency", "width": 120},
		{"label": _("Gross Amount"), "fieldname": "gross_amount", "fieldtype": "Currency", "width": 120},
	]


def get_data(filters):
	conditions = []
	values = {}

	if filters.get("from_date"):
		conditions.append("fd.fiscal_day_date >= %(from_date)s")
		values["from_date"] = filters.from_date
	if filters.get("to_date"):
		conditions.append("fd.fiscal_day_date <= %(to_date)s")
		values["to_date"] = filters.to_date
	if filters.get("fiscal_day_no"):
		conditions.append("fd.fiscal_day_no = %(fiscal_day_no)s")
		values["fiscal_day_no"] = filters.fiscal_day_no
	if filters.get("currency"):
		conditions.append("det.currency = %(currency)s")
		values["currency"] = filters.currency
	if filters.get("status"):
		conditions.append("fd.status = %(status)s")
		values["status"] = filters.status

	where_clause = ("where " + " and ".join(conditions)) if conditions else ""

	return frappe.db.sql(
		f"""
		select
			fd.name as fiscal_day,
			fd.fiscal_day_no as fiscal_day_no,
			fd.status as status,
			fd.fiscal_day_opened as fiscal_day_opened,
			fd.fiscal_day_closed as fiscal_day_closed,
			det.currency as currency,
			det.tax_id as tax_id,
			det.tax_percent as tax_percent,
			det.money_type as money_type,
			det.net_amount as net_amount,
			det.tax_amount as tax_amount,
			det.gross_amount as gross_amount
		from `tabZimra Fiscal Day` fd
		inner join `tabZimra Fiscal Day Detail` det on det.parent = fd.name
		{where_clause}
		order by fd.fiscal_day_no desc, det.currency, det.tax_id
		""",
		values,
		as_dict=True,
	)
