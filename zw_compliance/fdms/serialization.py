"""Typed (de)serialisation for the handful of dataclasses that cross the
Storage/Journal boundary as JSON. `dataclasses.asdict` handles the
dict-ification direction generically; reconstruction needs to know the
target type, hence the explicit `*_from_dict` functions below.
"""

from __future__ import annotations

import dataclasses
from typing import Optional

from .types import (
	BuyerData,
	CreditDebitNote,
	FiscalDayCounter,
	FiscalDayState,
	Payment,
	PreparedReceipt,
	Receipt,
	ReceiptLine,
	ReceiptTax,
	RedValidationError,
	SignatureData,
)


def state_to_dict(state: FiscalDayState) -> dict:
	return dataclasses.asdict(state)


def prepared_to_dict(prepared: PreparedReceipt) -> dict:
	return dataclasses.asdict(prepared)


def _signature_data_from_dict(d: Optional[dict]) -> Optional[SignatureData]:
	return SignatureData(**d) if d else None


def _buyer_data_from_dict(d: Optional[dict]) -> Optional[BuyerData]:
	return BuyerData(**d) if d else None


def _credit_debit_note_from_dict(d: Optional[dict]) -> Optional[CreditDebitNote]:
	return CreditDebitNote(**d) if d else None


def _receipt_from_dict(d: dict) -> Receipt:
	return Receipt(
		receipt_type=d["receipt_type"],
		receipt_currency=d["receipt_currency"],
		receipt_counter=d["receipt_counter"],
		receipt_global_no=d["receipt_global_no"],
		invoice_no=d["invoice_no"],
		receipt_date=d["receipt_date"],
		receipt_lines_tax_inclusive=d["receipt_lines_tax_inclusive"],
		receipt_lines=[ReceiptLine(**x) for x in d["receipt_lines"]],
		receipt_taxes=[ReceiptTax(**x) for x in d["receipt_taxes"]],
		receipt_payments=[Payment(**x) for x in d["receipt_payments"]],
		receipt_total=d["receipt_total"],
		receipt_device_signature=_signature_data_from_dict(d.get("receipt_device_signature")),
		buyer_data=_buyer_data_from_dict(d.get("buyer_data")),
		receipt_notes=d.get("receipt_notes"),
		credit_debit_note=_credit_debit_note_from_dict(d.get("credit_debit_note")),
		receipt_print_form=d.get("receipt_print_form"),
	)


def fiscal_day_state_from_dict(d: dict) -> FiscalDayState:
	return FiscalDayState(
		fiscal_day_no=d["fiscal_day_no"],
		fiscal_day_date=d["fiscal_day_date"],
		receipt_counter=d["receipt_counter"],
		receipt_global_no=d["receipt_global_no"],
		counters=[FiscalDayCounter(**x) for x in d.get("counters", [])],
		previous_receipt_hash=d.get("previous_receipt_hash"),
		previous_receipt_date=d.get("previous_receipt_date"),
		red_errors=[RedValidationError(**x) for x in d.get("red_errors", [])],
	)


def prepared_receipt_from_dict(d: dict) -> PreparedReceipt:
	return PreparedReceipt(
		receipt=_receipt_from_dict(d["receipt"]),
		date=d["date"],
		state_after=fiscal_day_state_from_dict(d["state_after"]),
	)
