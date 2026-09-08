"""Syncs the fdms.FiscalDevice's fiscal-day state onto the Frappe-visible
`Zimra Fiscal Day` / `Zimra Fiscal Day Detail` doctypes, and onto the
read-only mirror fields on `Zimra Settings`. The device's own persisted
state (FrappeFileStorage) remains the single source of truth for anything
that controls sequencing/signing; these records exist for reporting and
desk visibility only.
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime


def record_day_opened(settings, fiscal_day_no: int, opened_at):
	name = f"ZFD-{fiscal_day_no}-{frappe.utils.formatdate(opened_at, 'dd.mm.yy')}"
	if not frappe.db.exists("Zimra Fiscal Day", name):
		frappe.get_doc(
			{
				"doctype": "Zimra Fiscal Day",
				"device_id": str(settings.device_id),
				"serial_id": settings.serial_no,
				"fiscal_day_no": fiscal_day_no,
				"fiscal_day_date": frappe.utils.formatdate(opened_at, "yyyy-mm-dd"),
				"fiscal_day_opened": opened_at,
				"status": "Opened",
			}
		).insert(ignore_permissions=True)
	_update_settings_mirror(settings, fiscal_day_no=fiscal_day_no, fiscal_day_status="FiscalDayOpened")


def record_day_closed(settings, fiscal_day_no: int, counters: list, document_counts: dict | None = None):
	name = frappe.db.get_value("Zimra Fiscal Day", {"fiscal_day_no": fiscal_day_no, "device_id": str(settings.device_id)})
	if not name:
		return
	doc = frappe.get_doc("Zimra Fiscal Day", name)
	doc.status = "Closed"
	doc.fiscal_day_closed = now_datetime()
	doc.set("table_daily_totals_per_currency", [])
	for c in counters:
		doc.append(
			"table_daily_totals_per_currency",
			{
				"currency": c.fiscal_counter_currency,
				"tax_id": c.fiscal_counter_tax_id,
				"tax_percent": c.fiscal_counter_tax_percent,
				"money_type": c.fiscal_counter_money_type,
				"gross_amount" if "TaxByTax" not in c.fiscal_counter_type else "tax_amount": c.fiscal_counter_value,
			},
		)
	if document_counts:
		doc.invoice_qty = document_counts.get("FiscalInvoice", 0)
		doc.credit_note_qty = document_counts.get("CreditNote", 0)
		doc.debit_note_qty = document_counts.get("DebitNote", 0)
		doc.total_documents = doc.invoice_qty + doc.credit_note_qty + doc.debit_note_qty
	doc.save(ignore_permissions=True)
	_update_settings_mirror(settings, fiscal_day_status="FiscalDayClosed", receipt_counter=0)


def _update_settings_mirror(settings, **fields):
	updates = {}
	if "fiscal_day_no" in fields:
		updates["fiscal_day_no"] = fields["fiscal_day_no"]
	if "fiscal_day_status" in fields:
		updates["fiscal_day_status"] = fields["fiscal_day_status"]
	if "receipt_counter" in fields:
		updates["receipt_counter"] = fields["receipt_counter"]
	if updates:
		frappe.db.set_value("Zimra Settings", None, updates)
