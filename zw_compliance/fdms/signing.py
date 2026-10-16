"""Canonical strings hashed and signed by the device and by FDMS (spec §13).

Every field is converted to text and concatenated with no separator (§13.1).
A single wrong character makes FDMS mark the receipt RCPT020 (Red) or reject
the day close with BadCertificateSignature, so this module is covered by the
spec's own worked examples in ``fdms/tests/test_spec_vectors.py``.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from .money import to_cents
from .types import FISCAL_COUNTER_TYPES, MONEY_TYPES, FiscalCounter, Receipt, ReceiptTax


def fdms_datetime(value: datetime) -> str:
	"""``YYYY-MM-DDTHH:mm:ss``, 24h local wall-clock time, no zone (§13.2.1 field 5)."""
	return value.strftime("%Y-%m-%dT%H:%M:%S")


def fdms_date(value: date) -> str:
	"""``YYYY-MM-DD`` (§13.3.1 field 3)."""
	return value.strftime("%Y-%m-%d")


def format_percent(percent: Decimal | None) -> str:
	"""Tax percent as signed: ``15`` → ``"15.00"``, ``14.5`` → ``"14.50"``,
	``0`` → ``"0.00"``, exempt (not sent) → ``""`` (§13.2.1 field 7)."""
	if percent is None:
		return ""
	return f"{Decimal(percent):.2f}"


def _tax_sort_key(tax: ReceiptTax) -> tuple:
	# "Taxes are ordered by taxID in ascending order and taxCode in alphabetical
	# order (if taxCode is empty it is ordered before A letter)." §13.2.1 field 7
	return (tax.tax_id, tax.tax_code or "")


def receipt_taxes_string(taxes: list[ReceiptTax]) -> str:
	return "".join(
		f"{t.tax_code or ''}{format_percent(t.tax_percent)}"
		f"{to_cents(t.tax_amount, 'taxAmount')}{to_cents(t.sales_amount_with_tax, 'salesAmountWithTax')}"
		for t in sorted(taxes, key=_tax_sort_key)
	)


def receipt_string(device_id: int, receipt: Receipt, previous_receipt_hash: str | None) -> str:
	"""Receipt device-signature input, §13.2.1.

	deviceID ‖ RECEIPTTYPE ‖ CURRENCY ‖ receiptGlobalNo ‖ receiptDate ‖
	receiptTotal(cents) ‖ receiptTaxes ‖ previousReceiptHash (omitted for the
	first receipt of the fiscal day).
	"""
	return "".join(
		(
			str(device_id),
			receipt.receipt_type.upper(),
			receipt.currency.upper(),
			str(receipt.global_no),
			fdms_datetime(receipt.date),
			str(to_cents(receipt.total, "receiptTotal")),
			receipt_taxes_string(receipt.taxes),
			previous_receipt_hash or "",
		)
	)


def receipt_server_string(device_signature: str, receipt_id: int, server_date: datetime) -> str:
	"""Input of FDMS's receipt signature, §13.2.2: deviceSignature ‖ receiptID ‖ serverDate."""
	return f"{device_signature}{receipt_id}{fdms_datetime(server_date)}"


def _counter_sort_key(counter: FiscalCounter) -> tuple:
	# §13.3.1: by fiscalCounterType (enum order), currency (alphabetical),
	# then taxID / moneyType ascending (money types in enum order, §5.4.5).
	return (
		FISCAL_COUNTER_TYPES.index(counter.counter_type),
		counter.currency.upper(),
		counter.tax_id if counter.tax_id is not None else -1,
		MONEY_TYPES.index(counter.money_type) if counter.money_type else -1,
	)


def sorted_counters(counters: list[FiscalCounter]) -> list[FiscalCounter]:
	"""Non-zero counters in signature order (zero counters are neither signed nor sent, §4.11)."""
	return sorted((c for c in counters if c.value != 0), key=_counter_sort_key)


def counters_string(counters: list[FiscalCounter]) -> str:
	parts = []
	for c in sorted_counters(counters):
		qualifier = (
			c.money_type.upper() if c.counter_type == "BalanceByMoneyType" else format_percent(c.tax_percent)
		)
		parts.append(
			f"{c.counter_type.upper()}{c.currency.upper()}{qualifier}{to_cents(c.value, 'fiscalCounterValue')}"
		)
	return "".join(parts)


def fiscal_day_string(
	device_id: int, fiscal_day_no: int, fiscal_day_date: date, counters: list[FiscalCounter]
) -> str:
	"""Fiscal-day device-signature input, §13.3.1.

	``fiscal_day_date`` is the date the day was *opened*, never the close date.
	"""
	return f"{device_id}{fiscal_day_no}{fdms_date(fiscal_day_date)}{counters_string(counters)}"


def fiscal_day_server_string(
	device_id: int,
	fiscal_day_no: int,
	fiscal_day_date: date,
	fiscal_day_updated: datetime,
	reconciliation_mode: str,
	counters: list[FiscalCounter],
	device_signature: str | None,
) -> str:
	"""Input of FDMS's fiscal-day signature, §13.3.2.

	The device signature is excluded when the day was closed manually.
	"""
	mode = reconciliation_mode.upper()
	return "".join(
		(
			str(device_id),
			str(fiscal_day_no),
			fdms_date(fiscal_day_date),
			fdms_datetime(fiscal_day_updated),
			mode,
			counters_string(counters),
			"" if mode == "MANUAL" else (device_signature or ""),
		)
	)
