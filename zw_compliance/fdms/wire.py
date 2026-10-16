"""JSON (de)serialisation for the Fiscal Device Gateway API.

Field names follow ZIMRA's live Swagger (``/swagger/index.html`` on the test
environment), which is what the gateway actually binds - e.g. the buyer VAT
number is ``vatNumber`` (the PDF table writes ``VATNumber``). Optional fields
are omitted rather than sent as ``null``; an exempt tax omits ``taxPercent``
as the spec requires (§4.7 ReceiptLine/ReceiptTax).

Amounts are sent as JSON numbers produced from exact Decimals; ``_num``
guarantees the textual number round-trips to the same Decimal.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal

from .money import to_decimal
from .signing import fdms_datetime, sorted_counters
from .types import (
	Address,
	Buyer,
	Contacts,
	CreditDebitNote,
	DayStatus,
	DeviceConfig,
	FiscalCounter,
	Payment,
	Receipt,
	ReceiptLine,
	ReceiptTax,
	SignatureData,
	SubmitReceiptResult,
	Tax,
	TaxpayerInfo,
	UserInfo,
	ValidationError,
)


def _num(value: Decimal):
	if value == value.to_integral_value():
		return int(value)
	as_float = float(value)
	if Decimal(repr(as_float)) != value.normalize():
		raise ValueError(f"{value} cannot be represented exactly as a JSON number")
	return as_float


def _compact(data: dict) -> dict:
	return {k: v for k, v in data.items() if v is not None and v != {} and v != ""}


def _address(a: Address | None) -> dict | None:
	if not a:
		return None
	return (
		_compact(
			{
				"province": a.province,
				"city": a.city,
				"street": a.street,
				"houseNo": a.house_no,
				"district": a.district,
			}
		)
		or None
	)


def _contacts(c: Contacts | None) -> dict | None:
	if not c:
		return None
	return _compact({"phoneNo": c.phone_no, "email": c.email}) or None


def receipt_to_wire(r: Receipt) -> dict:
	data = {
		"receiptType": r.receipt_type,
		"receiptCurrency": r.currency,
		"receiptCounter": r.counter,
		"receiptGlobalNo": r.global_no,
		"invoiceNo": r.invoice_no,
		"buyerData": _compact(
			{
				"buyerRegisterName": r.buyer.register_name,
				"buyerTradeName": r.buyer.trade_name,
				"vatNumber": r.buyer.vat_number,
				"buyerTIN": r.buyer.tin,
				"buyerContacts": _contacts(r.buyer.contacts),
				"buyerAddress": _address(r.buyer.address),
			}
		)
		if r.buyer
		else None,
		"receiptNotes": r.notes,
		"receiptDate": fdms_datetime(r.date),
		"creditDebitNote": _compact(
			{
				"receiptID": r.credit_debit_note.receipt_id,
				"deviceID": r.credit_debit_note.device_id,
				"receiptGlobalNo": r.credit_debit_note.receipt_global_no,
				"fiscalDayNo": r.credit_debit_note.fiscal_day_no,
			}
		)
		if r.credit_debit_note
		else None,
		"receiptLinesTaxInclusive": r.lines_tax_inclusive,
		"receiptLines": [
			_compact(
				{
					"receiptLineType": l.line_type,
					"receiptLineNo": l.line_no,
					"receiptLineHSCode": l.hs_code,
					"receiptLineName": l.name,
					"receiptLinePrice": None if l.price is None else _num(l.price),
					"receiptLineQuantity": _num(l.quantity),
					"receiptLineTotal": _num(l.total),
					"taxCode": l.tax_code,
					"taxPercent": None if l.tax_percent is None else _num(l.tax_percent),
					"taxID": l.tax_id,
				}
			)
			for l in r.lines
		],
		"receiptTaxes": [
			_compact(
				{
					"taxCode": t.tax_code,
					"taxPercent": None if t.tax_percent is None else _num(t.tax_percent),
					"taxID": t.tax_id,
					"taxAmount": _num(t.tax_amount),
					"salesAmountWithTax": _num(t.sales_amount_with_tax),
				}
			)
			for t in r.taxes
		],
		"receiptPayments": [
			{"moneyTypeCode": p.money_type, "paymentAmount": _num(p.amount)} for p in r.payments
		],
		"receiptTotal": _num(r.total),
		"receiptPrintForm": r.print_form,
		"receiptDeviceSignature": {"hash": r.device_signature.hash, "signature": r.device_signature.signature}
		if r.device_signature
		else None,
		"userInfo": {"username": r.user.username, "userFullName": r.user.full_name} if r.user else None,
	}
	# receiptTotal 0 and receiptLinesTaxInclusive False are legitimate values; keep them.
	return {k: v for k, v in data.items() if v is not None}


def _dec(value) -> Decimal | None:
	return None if value is None else to_decimal(value)


def receipt_from_wire(data: dict) -> Receipt:
	"""Inverse of :func:`receipt_to_wire`, used to re-read stored payloads."""
	buyer = data.get("buyerData")
	cdn = data.get("creditDebitNote")
	user = data.get("userInfo")
	sig = data.get("receiptDeviceSignature")
	return Receipt(
		receipt_type=data["receiptType"],
		currency=data["receiptCurrency"],
		counter=data["receiptCounter"],
		global_no=data["receiptGlobalNo"],
		invoice_no=data["invoiceNo"],
		date=parse_datetime(data["receiptDate"]),
		lines=[
			ReceiptLine(
				line_type=l["receiptLineType"],
				line_no=l["receiptLineNo"],
				name=l["receiptLineName"],
				quantity=to_decimal(l["receiptLineQuantity"]),
				total=to_decimal(l["receiptLineTotal"]),
				tax_id=l["taxID"],
				tax_percent=_dec(l.get("taxPercent")),
				price=_dec(l.get("receiptLinePrice")),
				hs_code=l.get("receiptLineHSCode"),
				tax_code=l.get("taxCode"),
			)
			for l in data["receiptLines"]
		],
		taxes=[
			ReceiptTax(
				tax_id=t["taxID"],
				tax_percent=_dec(t.get("taxPercent")),
				tax_amount=to_decimal(t["taxAmount"]),
				sales_amount_with_tax=to_decimal(t["salesAmountWithTax"]),
				tax_code=t.get("taxCode"),
			)
			for t in data["receiptTaxes"]
		],
		payments=[
			Payment(p["moneyTypeCode"], to_decimal(p["paymentAmount"])) for p in data["receiptPayments"]
		],
		total=to_decimal(data["receiptTotal"]),
		print_form=data.get("receiptPrintForm", "Receipt48"),
		lines_tax_inclusive=data["receiptLinesTaxInclusive"],
		buyer=Buyer(
			register_name=buyer.get("buyerRegisterName"),
			tin=buyer.get("buyerTIN"),
			trade_name=buyer.get("buyerTradeName"),
			vat_number=buyer.get("vatNumber"),
			address=parse_address(buyer.get("buyerAddress")),
			contacts=parse_contacts(buyer.get("buyerContacts")),
		)
		if buyer
		else None,
		notes=data.get("receiptNotes"),
		credit_debit_note=CreditDebitNote(
			receipt_id=cdn.get("receiptID"),
			device_id=cdn.get("deviceID"),
			receipt_global_no=cdn.get("receiptGlobalNo"),
			fiscal_day_no=cdn.get("fiscalDayNo"),
		)
		if cdn
		else None,
		user=UserInfo(user["username"], user["userFullName"]) if user else None,
		device_signature=SignatureData(sig["hash"], sig["signature"]) if sig else None,
	)


def counters_to_wire(counters: list[FiscalCounter]) -> list[dict]:
	"""Non-zero counters in signature order (§4.11: zero counters must not be sent)."""
	out = []
	for c in sorted_counters(counters):
		row = {"fiscalCounterType": c.counter_type, "fiscalCounterCurrency": c.currency}
		if c.counter_type == "BalanceByMoneyType":
			row["fiscalCounterMoneyType"] = c.money_type
		else:
			row["fiscalCounterTaxID"] = c.tax_id
			if c.tax_percent is not None:  # exempt: "this field must not be provided"
				row["fiscalCounterTaxPercent"] = _num(c.tax_percent)
		row["fiscalCounterValue"] = _num(c.value)
		out.append(row)
	return out


def counters_from_wire(rows: list[dict] | None) -> list[FiscalCounter]:
	return [
		FiscalCounter(
			counter_type=r["fiscalCounterType"],
			currency=r["fiscalCounterCurrency"],
			value=to_decimal(r["fiscalCounterValue"]),
			tax_id=r.get("fiscalCounterTaxID"),
			tax_percent=_dec(r.get("fiscalCounterTaxPercent")),
			money_type=r.get("fiscalCounterMoneyType"),
		)
		for r in rows or []
	]


_DATETIME = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})")


def parse_datetime(value: str | None) -> datetime | None:
	"""FDMS local datetimes; fractional seconds and any zone suffix are dropped."""
	if not value:
		return None
	m = _DATETIME.match(value)
	if not m:
		raise ValueError(f"unrecognised FDMS datetime {value!r}")
	return datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}")


def parse_date(value: str | None) -> date | None:
	if not value:
		return None
	return date.fromisoformat(value[:10])


def parse_address(data: dict | None) -> Address | None:
	if not data:
		return None
	return Address(
		province=data.get("province"),
		city=data.get("city"),
		street=data.get("street"),
		house_no=data.get("houseNo"),
		district=data.get("district"),
	)


def parse_contacts(data: dict | None) -> Contacts | None:
	if not data:
		return None
	return Contacts(phone_no=data.get("phoneNo"), email=data.get("email"))


def parse_taxpayer_info(data: dict) -> TaxpayerInfo:
	return TaxpayerInfo(
		operation_id=data.get("operationID"),
		taxpayer_name=data.get("taxPayerName"),
		taxpayer_tin=data.get("taxPayerTIN"),
		vat_number=data.get("vatNumber") or None,
		branch_name=data.get("deviceBranchName"),
		branch_address=parse_address(data.get("deviceBranchAddress")),
		branch_contacts=parse_contacts(data.get("deviceBranchContacts")),
	)


def parse_config(data: dict) -> DeviceConfig:
	return DeviceConfig(
		operation_id=data.get("operationID"),
		taxpayer_name=data.get("taxPayerName"),
		taxpayer_tin=data.get("taxPayerTIN"),
		vat_number=data.get("vatNumber") or None,
		device_serial_no=data.get("deviceSerialNo"),
		branch_name=data.get("deviceBranchName"),
		branch_address=parse_address(data.get("deviceBranchAddress")),
		branch_contacts=parse_contacts(data.get("deviceBranchContacts")),
		operating_mode=data.get("deviceOperatingMode"),
		day_max_hours=data.get("taxPayerDayMaxHrs"),
		day_end_notification_hours=data.get("taxpayerDayEndNotificationHrs"),
		applicable_taxes=[
			Tax(
				tax_id=t["taxID"],
				tax_name=t.get("taxName"),
				tax_percent=_dec(t.get("taxPercent")),
				valid_from=parse_date(t.get("validFrom") or t.get("taxValidFrom")),
				valid_till=parse_date(t.get("validTill") or t.get("taxValidTill")),
			)
			for t in data.get("applicableTaxes") or []
		],
		certificate_valid_till=parse_date(data.get("certificateValidTill")),
		qr_url=data.get("qrUrl"),
	)


def parse_status(data: dict) -> DayStatus:
	return DayStatus(
		operation_id=data.get("operationID"),
		fiscal_day_status=data.get("fiscalDayStatus"),
		reconciliation_mode=data.get("fiscalDayReconciliationMode"),
		server_signature=data.get("fiscalDayServerSignature"),
		fiscal_day_closed=parse_datetime(data.get("fiscalDayClosed")),
		closing_error_code=data.get("fiscalDayClosingErrorCode"),
		last_receipt_global_no=data.get("lastReceiptGlobalNo"),
		last_fiscal_day_no=data.get("lastFiscalDayNo"),
		# Swagger names it fiscalDayCounter, the PDF fiscalDayCounters.
		counters=counters_from_wire(data.get("fiscalDayCounters") or data.get("fiscalDayCounter")),
	)


def parse_submit_result(data: dict) -> SubmitReceiptResult:
	return SubmitReceiptResult(
		operation_id=data.get("operationID"),
		receipt_id=data["receiptID"],
		server_date=parse_datetime(data["serverDate"]),
		server_signature=data.get("receiptServerSignature") or {},
		validation_errors=[
			ValidationError(
				code=v.get("validationErrorCode") or "",
				colour=v.get("validationErrorColor") or "",
				description=v.get("validationErrorDescription") or "",
			)
			for v in data.get("validationErrors") or []
		],
	)
