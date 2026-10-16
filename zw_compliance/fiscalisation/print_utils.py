"""Jinja helpers for the ZIMRA print formats (spec §10, §11).

Print formats render from the stored *Zimra Fiscal Receipt* - the exact signed
payload plus the seller block captured at signing - never from the live
invoice, so a printed fiscal receipt always matches what ZIMRA received.
"""

from __future__ import annotations

import io
from decimal import Decimal

import frappe
from frappe import _
from markupsafe import Markup

from zw_compliance.fdms.money import q2
from zw_compliance.fdms.wire import receipt_from_wire

from .constants import DAY, RECEIPT, REJECTED

TITLES = {"CreditNote": "CREDIT NOTE", "DebitNote": "DEBIT NOTE"}
ZIMRA_VERIFY_URL = "https://fdms.zimra.co.zw"


def _receipt_doc(doc):
	if doc.doctype == RECEIPT:
		return doc
	name = doc.get("zimra_fiscal_receipt")
	return frappe.get_doc(RECEIPT, name) if name else None


def _print_codes(company: str) -> dict:
	return {
		m.zimra_tax_id: m
		for m in frappe.get_all(
			"Zimra Tax Mapping",
			filters={"company": company},
			fields=["zimra_tax_id", "zimra_tax_name", "print_code"],
		)
	}


def zimra_fiscal_receipt(doc) -> dict | None:
	"""Everything a receipt template needs, or ``None`` if ``doc`` is not fiscalised."""
	record = _receipt_doc(doc)
	if not record:
		return None
	receipt = receipt_from_wire(frappe.parse_json(record.payload))
	codes = _print_codes(record.company)
	is_vat_payer = bool(record.vat_number)
	if receipt.receipt_type == "FiscalInvoice":
		title = "FISCAL TAX INVOICE" if is_vat_payer else "FISCAL INVOICE"  # §10.3 [9]
	else:
		title = TITLES[receipt.receipt_type]

	lines = []
	for line in receipt.lines:
		rate = line.tax_percent or Decimal(0)
		mapping = codes.get(line.tax_id)
		lines.append(
			{
				"is_discount": line.line_type == "Discount",
				"name": line.name,
				"hs_code": line.hs_code,
				"quantity": line.quantity.normalize(),
				"price": line.price,
				"total": line.total,
				"vat": q2(line.total * rate / (100 + rate)) if line.tax_percent else None,  # §10.3 [34.1]
				"tax_code": mapping.print_code if mapping else "",
			}
		)
	taxes = []
	for tax in sorted(receipt.taxes, key=lambda t: (t.tax_percent is None, -(t.tax_percent or 0))):
		mapping = codes.get(tax.tax_id)
		taxes.append(
			{
				"code": mapping.print_code if mapping else "",
				"name": (mapping.zimra_tax_name if mapping else None)
				or (_("Exempt") if tax.tax_percent is None else f"{tax.tax_percent:.2f}%"),
				"percent": tax.tax_percent,
				"net": tax.sales_amount_with_tax - tax.tax_amount,
				"tax": tax.tax_amount,
				"gross": tax.sales_amount_with_tax,
			}
		)
	items_count = sum((l.quantity for l in receipt.lines if l.line_type == "Sale"), Decimal(0))  # §10.3 [39]
	return frappe._dict(
		record=record,
		receipt=receipt,
		title=title,
		is_vat_payer=is_vat_payer,
		lines=lines,
		taxes=taxes,
		payments=receipt.payments,
		buyer=receipt.buyer,
		number_of_items=abs(items_count).normalize(),
		verify_url=frappe.db.get_value("Zimra Fiscal Device", record.fiscal_device, "qr_url")
		or ZIMRA_VERIFY_URL,
		pending=record.sync_status != "Submitted",
	)


def zimra_qr_svg(data: str, scale: int = 3) -> Markup:
	"""QR code as inline SVG (pyqrcode ships with Frappe)."""
	import pyqrcode

	if not data:
		return Markup("")
	buffer = io.BytesIO()
	pyqrcode.create(data, error="M").svg(buffer, scale=scale, xmldecl=False, svgns=True, quiet_zone=2)
	return Markup(buffer.getvalue().decode("utf-8"))


def zimra_z_report(day) -> dict:
	"""Z / X report data for a fiscal day (§10.4, §10.5), per currency."""
	if isinstance(day, str):
		day = frappe.get_doc(DAY, day)
	device = frappe.get_doc("Zimra Fiscal Device", day.fiscal_device)
	names = {
		m.zimra_tax_id: m.zimra_tax_name
		for m in frappe.get_all(
			"Zimra Tax Mapping", filters={"company": day.company}, fields=["zimra_tax_id", "zimra_tax_name"]
		)
	}
	by_currency: dict[str, dict] = {}
	for c in day.counters:
		block = by_currency.setdefault(c.currency, {"taxes": {}, "balances": []})
		if c.counter_type == "BalanceByMoneyType":
			block["balances"].append({"money_type": c.money_type, "value": c.value})
			continue
		key = (c.tax_id, None if c.is_exempt else c.tax_percent)
		tax = block["taxes"].setdefault(
			key,
			{
				"name": names.get(c.tax_id) or (_("Exempt") if c.is_exempt else f"{c.tax_percent:.2f}%"),
				"percent": None if c.is_exempt else c.tax_percent,
				"gross": 0,
				"tax": 0,
				"sales": 0,
				"credit": 0,
				"debit": 0,
			},
		)
		if c.counter_type.endswith("TaxByTax"):
			tax["tax"] += c.value
		else:
			tax["gross"] += c.value
			tax[
				{"SaleByTax": "sales", "CreditNoteByTax": "credit", "DebitNoteByTax": "debit"}[c.counter_type]
			] += c.value

	documents = frappe.get_all(
		RECEIPT,
		filters={"fiscal_day": day.name, "sync_status": ["!=", REJECTED]},
		fields=["receipt_currency", "receipt_type", "count(name) as qty", "sum(receipt_total) as total"],
		group_by="receipt_currency, receipt_type",
	)
	currencies = []
	for currency in sorted(
		set(by_currency) | {d.receipt_currency for d in documents}
	):  # §10.5 [15] alphabetical
		block = by_currency.get(currency, {"taxes": {}, "balances": []})
		taxes = sorted(
			block["taxes"].values(), key=lambda t: (t["percent"] is None, -(t["percent"] or 0))
		)  # descending
		for t in taxes:
			t["net"] = t["gross"] - t["tax"]
		docs = {d.receipt_type: d for d in documents if d.receipt_currency == currency}
		currencies.append(
			frappe._dict(
				currency=currency,
				taxes=taxes,
				taxed=[t for t in taxes if t["percent"]],  # §10.5 [21]-[23]: 0% and exempt not shown
				total_net=sum(t["net"] for t in taxes),
				total_tax=sum(t["tax"] for t in taxes),
				total_gross=sum(t["gross"] for t in taxes),
				balances=block["balances"],
				documents=[
					(label, docs[t].qty if t in docs else 0, docs[t].total if t in docs else 0)
					for t, label in (
						("FiscalInvoice", _("Invoices")),
						("CreditNote", _("Credit notes")),
						("DebitNote", _("Debit notes")),
					)
				],
			)
		)
	for c in currencies:
		c.total_documents = sum(d[1] for d in c.documents)
		c.total_documents_amount = sum(d[2] for d in c.documents)
	return frappe._dict(
		day=day,
		device=device,
		title="Z REPORT" if day.status == "Closed" else "X REPORT",  # §10.5 [8]
		currencies=currencies,
	)
