"""Conformance test: checks this Python port against zimra-fdms's own
published vectors (vectors/zimra-fdms-vectors.json in
https://github.com/munashe-chivandire/zimra-fdms, MIT licensed, copied
verbatim into this directory). Canonical strings and hashes must match
byte for byte; ECDSA signatures are verified against the vectors'
publicKeyPem rather than compared, since ECDSA signing is randomised.

Run standalone, no Frappe site required:
    python -m pytest zw_compliance/fdms/tests/test_vectors.py
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from zw_compliance.fdms.device import _build_receipt_taxes
from zw_compliance.fdms.qr import receipt_qr_data
from zw_compliance.fdms.signer import sha256_base64
from zw_compliance.fdms.signing import fiscal_day_signing_string, receipt_signing_string
from zw_compliance.fdms.types import (
	FiscalDayCounter,
	Payment,
	Receipt,
	ReceiptLine,
	ReceiptTax,
)

VECTORS_PATH = Path(__file__).parent / "zimra-fdms-vectors.json"
VECTORS = json.loads(VECTORS_PATH.read_text())
PUBLIC_KEY = serialization.load_pem_public_key(VECTORS["publicKeyPem"].encode())


def _verify(data: str, signature_b64: str) -> None:
	PUBLIC_KEY.verify(base64.b64decode(signature_b64), data.encode("utf-8"), ec.ECDSA(hashes.SHA256()))


def _receipt_line_from_wire(d: dict) -> ReceiptLine:
	return ReceiptLine(
		receipt_line_type=d["receiptLineType"],
		receipt_line_no=d["receiptLineNo"],
		receipt_line_hs_code=d.get("receiptLineHSCode"),
		receipt_line_name=d["receiptLineName"],
		receipt_line_price=d.get("receiptLinePrice"),
		receipt_line_quantity=d["receiptLineQuantity"],
		receipt_line_total=d["receiptLineTotal"],
		tax_code=d.get("taxCode"),
		tax_percent=d.get("taxPercent"),
		tax_id=d["taxID"],
	)


def _receipt_tax_from_wire(d: dict) -> ReceiptTax:
	return ReceiptTax(
		tax_id=d["taxID"],
		tax_amount=d["taxAmount"],
		sales_amount_with_tax=d["salesAmountWithTax"],
		tax_code=d.get("taxCode"),
		tax_percent=d.get("taxPercent"),
	)


def _receipt_from_wire(d: dict) -> Receipt:
	return Receipt(
		receipt_type=d["receiptType"],
		receipt_currency=d["receiptCurrency"],
		receipt_counter=d["receiptCounter"],
		receipt_global_no=d["receiptGlobalNo"],
		invoice_no=d["invoiceNo"],
		receipt_date=d["receiptDate"],
		receipt_lines_tax_inclusive=d["receiptLinesTaxInclusive"],
		receipt_lines=[_receipt_line_from_wire(l) for l in d["receiptLines"]],
		receipt_taxes=[_receipt_tax_from_wire(t) for t in d["receiptTaxes"]],
		receipt_payments=[
			Payment(money_type_code=p["moneyTypeCode"], payment_amount=p["paymentAmount"])
			for p in d["receiptPayments"]
		],
		receipt_total=d["receiptTotal"],
	)


def _counter_from_wire(d: dict) -> FiscalDayCounter:
	return FiscalDayCounter(
		fiscal_counter_type=d["fiscalCounterType"],
		fiscal_counter_currency=d["fiscalCounterCurrency"],
		fiscal_counter_value=d["fiscalCounterValue"],
		fiscal_counter_tax_percent=d.get("fiscalCounterTaxPercent"),
		fiscal_counter_tax_id=d.get("fiscalCounterTaxID"),
		fiscal_counter_money_type=d.get("fiscalCounterMoneyType"),
	)


@pytest.mark.parametrize("vector", VECTORS["receipts"], ids=lambda v: v["name"])
def test_receipt_canonical_string_hash_and_signature(vector):
	receipt = _receipt_from_wire(vector["receipt"])
	canonical = receipt_signing_string(vector["deviceId"], receipt, vector["previousReceiptHash"])
	assert canonical == vector["canonical"]
	assert sha256_base64(canonical.encode("utf-8")) == vector["hash"]
	_verify(canonical, vector["signature"])


@pytest.mark.parametrize("vector", VECTORS["fiscalDays"], ids=lambda v: v["name"])
def test_fiscal_day_canonical_string_hash_and_signature(vector):
	counters = [_counter_from_wire(c) for c in vector["counters"]]
	canonical = fiscal_day_signing_string(vector["deviceId"], vector["fiscalDayNo"], vector["fiscalDayDate"], counters)
	assert canonical == vector["canonical"]
	assert sha256_base64(canonical.encode("utf-8")) == vector["hash"]
	_verify(canonical, vector["signature"])


@pytest.mark.parametrize("vector", VECTORS["qr"], ids=lambda v: v["name"])
def test_qr_data(vector):
	from datetime import datetime

	date = datetime.fromisoformat(vector["receiptDate"])
	result = receipt_qr_data(
		vector["qrUrl"], vector["deviceId"], date, vector["receiptGlobalNo"], vector["deviceSignatureBase64"]
	)
	assert result == vector["expected"]


@pytest.mark.parametrize("vector", VECTORS["sha256"], ids=lambda v: v["input"][:20])
def test_sha256(vector):
	assert sha256_base64(vector["input"].encode("utf-8")) == vector["sha256Base64"]


@pytest.mark.parametrize("vector", VECTORS["taxes"], ids=lambda v: v["name"])
def test_receipt_taxes(vector):
	lines = [_receipt_line_from_wire(l) for l in vector["lines"]]
	result = _build_receipt_taxes(lines, vector["taxInclusive"])
	expected = [_receipt_tax_from_wire(t) for t in vector["expected"]]
	assert [(t.tax_id, t.tax_percent, t.tax_code, t.tax_amount, t.sales_amount_with_tax) for t in result] == [
		(t.tax_id, t.tax_percent, t.tax_code, t.tax_amount, t.sales_amount_with_tax) for t in expected
	]
