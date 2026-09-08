"""Canonical signing strings, ported from src/core/signing.ts.

This is the single most safety-critical module in the port: any deviation
from field order/formatting here produces a signature FDMS rejects with
RCPT010, or worse, one it silently accepts that doesn't mean what the rest
of the system thinks it means. See fdms/tests/test_vectors.py, which checks
this module byte-for-byte against the SDK's own published conformance
vectors.
"""

from __future__ import annotations

from datetime import datetime

from .types import FiscalDayCounter, Receipt, ReceiptTax

# Fixed counter-type priority for CloseDay signing/sorting - NOT alphabetical.
_COUNTER_ORDER = {
	"SaleByTax": 1,
	"SaleTaxByTax": 2,
	"CreditNoteByTax": 3,
	"CreditNoteTaxByTax": 4,
	"DebitNoteByTax": 5,
	"DebitNoteTaxByTax": 6,
	"BalanceByMoneyType": 7,
}

# Fixed money-type wire order for BalanceByMoneyType sorting - NOT alphabetical.
_MONEY_TYPE_ORDER = {
	"Cash": 0,
	"Card": 1,
	"MobileWallet": 2,
	"Coupon": 3,
	"Credit": 4,
	"BankTransfer": 5,
	"Other": 6,
}


def to_cents(amount: float) -> int:
	"""Float major-units amount -> integer cents, with an epsilon nudge to
	counter float representation error before rounding. Prefer passing
	values that already originated as integer cents wherever possible, so
	this becomes a no-op in practice.
	"""
	epsilon = 1e-7 if amount >= 0 else -1e-7
	return round(amount * 100 + epsilon * 100)


def format_tax_percent(percent: float | None) -> str:
	if percent is None:
		return ""
	return f"{percent:.2f}"


def fdms_date_time(d: datetime) -> str:
	"""Local wall-clock time, no timezone offset, no trailing 'Z'."""
	return f"{d.year:04d}-{d.month:02d}-{d.day:02d}T{d.hour:02d}:{d.minute:02d}:{d.second:02d}"


def fdms_date(d: datetime) -> str:
	return f"{d.year:04d}-{d.month:02d}-{d.day:02d}"


def concatenate_receipt_taxes(taxes: list[ReceiptTax]) -> str:
	ordered = sorted(taxes, key=lambda t: t.tax_id)
	parts = []
	for t in ordered:
		parts.append(format_tax_percent(t.tax_percent))
		parts.append(str(to_cents(t.tax_amount)))
		parts.append(str(to_cents(t.sales_amount_with_tax)))
	return "".join(parts)


def receipt_signing_string(device_id: int, receipt: Receipt, previous_receipt_hash: str | None) -> str:
	"""deviceID + RECEIPTTYPE + CURRENCY + receiptGlobalNo + receiptDate +
	total-in-cents + taxes(sorted by taxID) + previousReceiptHash (empty for
	the first receipt of the fiscal day). Pure concatenation, no delimiters.
	"""
	return "".join(
		[
			str(device_id),
			receipt.receipt_type.upper(),
			receipt.receipt_currency.upper(),
			str(receipt.receipt_global_no),
			receipt.receipt_date,
			str(to_cents(receipt.receipt_total)),
			concatenate_receipt_taxes(receipt.receipt_taxes),
			previous_receipt_hash or "",
		]
	)


def fiscal_day_signing_string(
	device_id: int, fiscal_day_no: int, fiscal_day_date: str, counters: list[FiscalDayCounter]
) -> str:
	"""deviceID + fiscalDayNo + fiscalDayDate(YYYY-MM-DD, the date the day was
	OPENED - never recomputed at close time) + counters, sorted by fixed
	counter-type priority, then currency, then taxID, then money-type order.
	Zero-value counters are excluded.
	"""
	non_zero = [c for c in counters if to_cents(c.fiscal_counter_value) != 0]
	ordered = sorted(
		non_zero,
		key=lambda c: (
			_COUNTER_ORDER.get(c.fiscal_counter_type, 99),
			c.fiscal_counter_currency,
			c.fiscal_counter_tax_id if c.fiscal_counter_tax_id is not None else -1,
			_MONEY_TYPE_ORDER.get(c.fiscal_counter_money_type, 99),
		),
	)
	parts = []
	for c in ordered:
		parts.append(c.fiscal_counter_type.upper())
		parts.append(c.fiscal_counter_currency.upper())
		if c.fiscal_counter_tax_percent is not None:
			parts.append(format_tax_percent(c.fiscal_counter_tax_percent))
		if c.fiscal_counter_money_type:
			parts.append(c.fiscal_counter_money_type.upper())
		parts.append(str(to_cents(c.fiscal_counter_value)))
	return f"{device_id}{fiscal_day_no}{fiscal_day_date}" + "".join(parts)
