"""Receipt assembly, pre-submission validation and fiscal counters.

``build_receipt`` turns a :class:`ReceiptDraft` into a numbered
:class:`Receipt` with its tax table and total, then runs every FDMS rule that
can be checked locally (spec §4.7, §8.2.1). A receipt that FDMS would flag Red
is refused here, *before* it is signed and enters the device's sequence: a Red
receipt can never be withdrawn and blocks the fiscal day close (§4.11).

``counters_for`` implements the counter table of spec §6. Amounts already
carry the receipt's sign (credit notes are negative), so counters are simply
added.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from datetime import datetime
from decimal import Decimal

from .errors import ReceiptValidationError
from .money import q2, q6
from .types import (
	MONEY_TYPES,
	RECEIPT_LINE_TYPES,
	RECEIPT_PRINT_FORMS,
	RECEIPT_TYPES,
	FiscalCounter,
	Payment,
	Receipt,
	ReceiptDraft,
	ReceiptLine,
	ReceiptTax,
)

ZERO = Decimal(0)
_HUNDRED = Decimal(100)
_DIGITS = re.compile(r"^\d+$")


def exact_price(total: Decimal, quantity: Decimal) -> Decimal | None:
	"""Unit price to send, or ``None`` when no 6-decimal price times the
	quantity reproduces the total exactly. RCPT024 requires
	``receiptLineTotal == receiptLinePrice * receiptLineQuantity`` whenever a
	price is sent, and the price is optional (§4.7 ReceiptLine), so an inexact
	price is omitted rather than risked."""
	if quantity == 1:
		return total
	if quantity <= 0:
		return None  # rejected as RCPT023 by validate_receipt
	price = q6(total / quantity)
	return price if price * quantity == total else None


def compute_taxes(lines: list[ReceiptLine], tax_inclusive: bool = True) -> list[ReceiptTax]:
	"""Tax table, one row per (taxID, taxPercent, taxCode), spec RCPT026/RCPT027.

	Tax is computed on the *group* total, as the spec's formula states:
	inclusive ``SUM(lineTotal) * p / (100 + p)``, exclusive ``SUM(lineTotal) * p``.
	Exempt (no percent) and 0% groups carry zero tax.
	"""
	groups: OrderedDict[tuple, list[ReceiptLine]] = OrderedDict()
	for line in lines:
		groups.setdefault((line.tax_id, line.tax_percent, line.tax_code), []).append(line)

	taxes = []
	for (tax_id, percent, code), group in groups.items():
		gross = sum((l.total for l in group), ZERO)
		rate = percent or ZERO
		if tax_inclusive:
			tax = q2(gross * rate / (_HUNDRED + rate)) if rate else ZERO
			sales_with_tax = gross
		else:
			tax = q2(gross * rate / _HUNDRED) if rate else ZERO
			sales_with_tax = gross + tax
		taxes.append(ReceiptTax(tax_id, percent, q2(tax), q2(sales_with_tax), code))
	taxes.sort(key=lambda t: (t.tax_id, t.tax_code or ""))
	return taxes


def build_receipt(
	draft: ReceiptDraft, *, counter: int, global_no: int, date: datetime, is_vat_payer: bool
) -> Receipt:
	lines = [
		ReceiptLine(
			line_type=item.line_type,
			line_no=i,
			name=item.name.strip()[:200],
			quantity=q6(item.quantity),
			total=q2(item.total),
			tax_id=item.tax_id,
			tax_percent=None if item.tax_percent is None else Decimal(item.tax_percent),
			price=exact_price(q2(item.total), q6(item.quantity)),
			hs_code=item.hs_code or None,
			tax_code=item.tax_code or None,
		)
		for i, item in enumerate(draft.lines, start=1)
	]
	taxes = compute_taxes(lines)
	receipt = Receipt(
		receipt_type=draft.receipt_type,
		currency=draft.currency.upper(),
		counter=counter,
		global_no=global_no,
		invoice_no=draft.invoice_no,
		date=date.replace(microsecond=0),
		lines=lines,
		taxes=taxes,
		payments=[Payment(p.money_type, q2(p.amount)) for p in draft.payments],
		total=q2(sum((l.total for l in lines), ZERO)),
		print_form=draft.print_form,
		buyer=draft.buyer,
		notes=(draft.notes or "").strip() or None,
		credit_debit_note=draft.credit_debit_note,
		user=draft.user,
	)
	validate_receipt(receipt, is_vat_payer=is_vat_payer)
	return receipt


def _fail(code: str, detail: str):
	raise ReceiptValidationError(code, detail)


def validate_hs_code(hs_code: str | None, tax_percent: Decimal | None, is_vat_payer: bool) -> str | None:
	"""RCPT047 / RCPT048. Returns an error code or ``None``."""
	if not hs_code:
		return "RCPT047" if is_vat_payer else None
	if not _DIGITS.match(hs_code):
		return "RCPT048"
	allowed = (8,) if is_vat_payer and not tax_percent else (4, 8)
	return None if len(hs_code) in allowed else "RCPT048"


def validate_receipt(receipt: Receipt, *, is_vat_payer: bool) -> None:
	"""Raise :class:`ReceiptValidationError` for any locally checkable FDMS rule."""
	rtype = receipt.receipt_type
	if rtype not in RECEIPT_TYPES:
		_fail("RCPT02", f"unknown receipt type {rtype}")
	if receipt.print_form not in RECEIPT_PRINT_FORMS:
		_fail("RCPT02", f"unknown print form {receipt.print_form}")
	if not re.fullmatch(r"[A-Z]{3}", receipt.currency):
		_fail("RCPT010", f"currency {receipt.currency!r} is not an ISO 4217 code")
	if not receipt.invoice_no or len(receipt.invoice_no) > 50:
		_fail("RCPT02", "invoiceNo is required and at most 50 characters")
	if not receipt.lines:
		_fail("RCPT016", "at least one receipt line is required")
	if not receipt.payments:
		_fail("RCPT018", "at least one payment is required")

	is_credit = rtype == "CreditNote"
	notes_required = rtype in ("CreditNote", "DebitNote")
	if notes_required and not receipt.notes:
		_fail("RCPT034", "a reason (receipt notes) is mandatory for credit and debit notes")
	if receipt.notes and len(receipt.notes) > 500:
		_fail("RCPT02", "receipt notes are limited to 500 characters")
	if notes_required and not receipt.credit_debit_note:
		_fail("RCPT015", "credit and debit notes must reference the original receipt")
	if not notes_required and receipt.credit_debit_note:
		_fail("RCPT029", "a fiscal invoice cannot reference another receipt")
	if receipt.credit_debit_note:
		cdn = receipt.credit_debit_note
		if not cdn.receipt_id and not (cdn.device_id and cdn.receipt_global_no and cdn.fiscal_day_no):
			_fail(
				"RCPT015", "the original receipt needs receiptID, or deviceID + receiptGlobalNo + fiscalDayNo"
			)

	codes = {bool(l.tax_code) for l in receipt.lines}
	if len(codes) > 1:
		_fail("RCPT02", "taxCode must be provided for all receipt lines or none of them")

	for line in receipt.lines:
		where = f"line {line.line_no} ({line.name})"
		if line.line_type not in RECEIPT_LINE_TYPES:
			_fail("RCPT02", f"{where}: unknown line type {line.line_type}")
		if not line.name:
			_fail("RCPT02", f"line {line.line_no}: a name is required")
		if line.quantity <= 0:
			_fail("RCPT023", f"{where}: quantity must be greater than 0")
		if line.price is not None and line.price * line.quantity != line.total:
			_fail("RCPT024", f"{where}: total {line.total} is not price * quantity")
		if line.line_type == "Sale":
			if is_credit and not line.total < 0:
				_fail("RCPT022", f"{where}: credit note sale lines must be negative")
			if not is_credit and not line.total > 0:
				_fail("RCPT022", f"{where}: sale lines must be greater than 0")
		elif rtype == "FiscalInvoice":
			if not line.total < 0:
				_fail("RCPT022", f"{where}: discount lines must be negative")
		else:
			_fail("RCPT022", f"{where}: discount lines are only supported on fiscal invoices")
		if line.tax_percent is not None and line.tax_percent > 0 and not is_vat_payer:
			_fail("RCPT021", f"{where}: a taxpayer without a VAT number cannot charge VAT")
		hs_error = validate_hs_code(line.hs_code, line.tax_percent, is_vat_payer)
		if hs_error:
			_fail(hs_error, f"{where}: HS code {line.hs_code or '(missing)'} is not valid for this line")

	total = sum((l.total for l in receipt.lines), ZERO)
	if receipt.total != total:
		_fail("RCPT019", f"total {receipt.total} differs from the sum of lines {total}")
	if receipt.total != sum((t.sales_amount_with_tax for t in receipt.taxes), ZERO):
		_fail("RCPT038", "total differs from the tax table")
	if is_credit and receipt.total > 0:
		_fail("RCPT040", "a credit note total must be 0 or negative")
	if not is_credit and receipt.total < 0:
		_fail("RCPT040", "an invoice or debit note total must be 0 or positive")

	for p in receipt.payments:
		if p.money_type not in MONEY_TYPES:
			_fail("RCPT02", f"unknown money type {p.money_type}")
		if (is_credit and p.amount > 0) or (not is_credit and p.amount < 0):
			_fail("RCPT028", f"{p.money_type} payment of {p.amount} has the wrong sign")
	paid = sum((p.amount for p in receipt.payments), ZERO)
	if paid != receipt.total:
		_fail("RCPT039", f"payments {paid} differ from the total {receipt.total}")

	if receipt.buyer:
		b = receipt.buyer
		if not (b.register_name or "").strip() or not (b.tin or "").strip():
			_fail("RCPT043", "buyer data needs both the registered name and the TIN")
		if not re.fullmatch(r"\d{10}", b.tin):
			_fail("RCPT043", f"buyer TIN {b.tin!r} must be 10 digits")
		if b.vat_number and not re.fullmatch(r"\d{9}", b.vat_number):
			_fail("RCPT02", f"buyer VAT number {b.vat_number!r} must be 9 digits")
		if len(b.register_name) > 250 or (b.trade_name and len(b.trade_name) > 250):
			_fail("RCPT02", "buyer names are limited to 250 characters")


_COUNTER_TYPES = {
	"FiscalInvoice": ("SaleByTax", "SaleTaxByTax"),
	"CreditNote": ("CreditNoteByTax", "CreditNoteTaxByTax"),
	"DebitNote": ("DebitNoteByTax", "DebitNoteTaxByTax"),
}


def counters_for(receipt: Receipt) -> list[FiscalCounter]:
	"""Fiscal counters a receipt contributes (spec §6).

	FiscalInvoice: +salesAmountWithTax to SaleByTax, +taxAmount to SaleTaxByTax;
	credit and debit notes the same into their own counters; every receipt adds
	+paymentAmount to BalanceByMoneyType. Credit-note amounts are negative, so
	their counters decrease (the "*" note in §6).
	"""
	by_tax, tax_by_tax = _COUNTER_TYPES[receipt.receipt_type]
	counters = []
	for t in receipt.taxes:
		counters.append(
			FiscalCounter(by_tax, receipt.currency, t.sales_amount_with_tax, t.tax_id, t.tax_percent)
		)
		counters.append(FiscalCounter(tax_by_tax, receipt.currency, t.tax_amount, t.tax_id, t.tax_percent))
	for p in receipt.payments:
		counters.append(
			FiscalCounter("BalanceByMoneyType", receipt.currency, p.amount, money_type=p.money_type)
		)
	return merge_counters([], counters)


def merge_counters(existing: list[FiscalCounter], new: list[FiscalCounter]) -> list[FiscalCounter]:
	"""Sum counters by (type, currency, taxID, taxPercent, moneyType)."""
	merged: OrderedDict[tuple, FiscalCounter] = OrderedDict()
	for c in [*existing, *new]:
		key = c.key()
		if key in merged:
			merged[key] = FiscalCounter(
				c.counter_type, c.currency, merged[key].value + c.value, c.tax_id, c.tax_percent, c.money_type
			)
		else:
			merged[key] = FiscalCounter(
				c.counter_type, c.currency, c.value, c.tax_id, c.tax_percent, c.money_type
			)
	return list(merged.values())
