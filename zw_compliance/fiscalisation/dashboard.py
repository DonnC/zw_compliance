"""Data for the ZIMRA Fiscalisation workspace: compliance-health number cards
and the money charts (fiscal sales and output VAT).

Amounts are never added across currencies: each chart shows one currency,
chosen in the chart's filter (default: the company currency).
"""

from __future__ import annotations

from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import add_days, flt, get_datetime, getdate, now_datetime, today
from frappe.utils.dashboard import cache_source

from .constants import DAY, DAY_ACCEPTING, DEVICE, DEVICE_ACTIVE, RECEIPT

NO_DATA = "—"
CARD_LIMIT = 10000


def _filters(filters) -> dict:
	filters = frappe.parse_json(filters) or {}
	if isinstance(filters, list):  # [[doctype, field, op, value], ...]
		filters = {f[1]: f[3] for f in filters if len(f) >= 4 and f[2] == "="}
	return filters


def _company(filters) -> str | None:
	return _filters(filters).get("company") or frappe.defaults.get_user_default("Company")


def _currency(filters, company: str | None) -> str | None:
	return _filters(filters).get("currency") or (
		frappe.get_cached_value("Company", company, "default_currency") if company else None
	)


def _devices(company: str | None) -> list:
	filters = {"status": DEVICE_ACTIVE}
	if company:
		filters["company"] = company
	return frappe.get_list(
		DEVICE,
		filters=filters,
		fields=["name", "company", "current_fiscal_day", "day_max_hours", "certificate_valid_till"],
	)


# -- number cards -----------------------------------------------------------------


@frappe.whitelist()
def unfiscalised_invoices(filters=None) -> dict:
	"""Submitted Sales and POS Invoices with no fiscal receipt, since the
	company's first device went live. Should always be 0."""
	company = _company(filters)
	issued = frappe.get_list(
		DEVICE,
		filters={"company": company, "certificate_issued_on": ["is", "set"]},
		pluck="certificate_issued_on",
	)
	if not issued:
		return {"value": 0, "fieldtype": "Int"}
	since = getdate(min(get_datetime(d) for d in issued))
	common = {
		"company": company,
		"docstatus": 1,
		"zimra_fiscal_receipt": ["is", "not set"],
		"posting_date": [">=", since],
	}
	count = len(
		frappe.get_list(
			"Sales Invoice", filters={**common, "is_consolidated": 0}, pluck="name", limit=CARD_LIMIT
		)
	)
	count += len(frappe.get_list("POS Invoice", filters=common, pluck="name", limit=CARD_LIMIT))
	return {
		"value": count,
		"fieldtype": "Int",
		"route": ["query-report", "ZIMRA Fiscalisation Reconciliation"],
		"route_options": {"company": company, "from_date": since, "to_date": today(), "only_issues": 1},
	}


@frappe.whitelist()
def certificate_days_left(filters=None) -> dict | str:
	"""Days until the first device certificate of the company expires."""
	days = [
		(get_datetime(d.certificate_valid_till) - now_datetime()) // timedelta(days=1)
		for d in _devices(_company(filters))
		if d.certificate_valid_till
	]
	if not days:
		return NO_DATA
	return {"value": min(days), "fieldtype": "Int", "route": ["List", DEVICE]}


@frappe.whitelist()
def fiscal_day_hours_left(filters=None) -> dict | str:
	"""Hours left before ZIMRA's maximum fiscal day length, for the device
	closest to it."""
	left = []
	for device in _devices(_company(filters)):
		if not (device.current_fiscal_day and device.day_max_hours):
			continue
		day = frappe.db.get_value(
			DAY, device.current_fiscal_day, ["status", "fiscal_day_opened"], as_dict=True
		)
		if day and day.status in DAY_ACCEPTING and day.fiscal_day_opened:
			end = get_datetime(day.fiscal_day_opened) + timedelta(hours=device.day_max_hours)
			left.append(max((end - now_datetime()).total_seconds() / 3600, 0))
	if not left:
		return NO_DATA
	return {"value": flt(min(left), 1), "fieldtype": "Float", "precision": 1, "route": ["List", DAY]}


# -- chart sources ----------------------------------------------------------------


def _chart_filters(filters, chart):
	if filters:
		return filters
	chart = frappe.parse_json(chart) if chart else None
	return (chart or {}).get("filters_json")


def _check_read() -> None:
	if not frappe.has_permission(RECEIPT, "read"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)


@cache_source
def fiscal_sales(chart_name=None, chart=None, no_cache=None, filters=None, **kwargs) -> dict:
	"""Net fiscal sales (invoices + debit notes - credit notes) per day, last 30 days."""
	_check_read()
	filters = _chart_filters(filters, chart)
	company = _company(filters)
	currency = _currency(filters, company)
	start = getdate(add_days(today(), -29))
	totals = dict(
		frappe.db.sql(
			f"""select date(receipt_date), sum(receipt_total) from `tab{RECEIPT}`
			where company = %s and receipt_currency = %s and receipt_date >= %s
			group by date(receipt_date)""",
			(company, currency, start),
		)
	)
	days = [getdate(add_days(start, i)) for i in range(30)]
	return {
		"labels": [frappe.format(d, "Date") for d in days],
		"datasets": [{"name": currency, "values": [flt(totals.get(d), 2) for d in days]}],
		"type": "bar",
		"fieldtype": "Currency",
		"options": currency,
	}


@cache_source
def output_vat_by_tax(chart_name=None, chart=None, no_cache=None, filters=None, **kwargs) -> dict:
	"""Output VAT charged on fiscal receipts this month, per ZIMRA tax."""
	_check_read()
	filters = _chart_filters(filters, chart)
	company = _company(filters)
	currency = _currency(filters, company)
	rows = frappe.db.sql(
		f"""select t.tax_name, sum(t.tax_amount)
		from `tab{RECEIPT}` r join `tabZimra Fiscal Receipt Tax` t on t.parent = r.name
		where r.company = %s and r.receipt_currency = %s and r.receipt_date >= %s
		group by t.tax_name order by t.tax_name""",
		(company, currency, getdate(today()).replace(day=1)),
	)
	return {
		"labels": [r[0] for r in rows],
		"datasets": [{"name": currency, "values": [flt(r[1], 2) for r in rows]}],
		"type": "bar",
		"fieldtype": "Currency",
		"options": currency,
	}
