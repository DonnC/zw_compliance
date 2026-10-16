"""Conformance against the zimra-fdms SDK's published vectors
(``zimra-fdms-vectors.json``, MIT licence, copied verbatim).

Those vectors were verified against ZIMRA's live test environment. They are
used here with two documented adaptations:

* The SDK sends ``taxCode`` but leaves it out of the signed tax block, while the
  spec (§13.2.1) includes it. This app never sends ``taxCode``, which makes
  both readings identical, so the vectors are replayed without it.
* The SDK's credit-note vector uses positive amounts and no credited-receipt
  reference or notes, contrary to RCPT040/RCPT022/RCPT028/RCPT015/RCPT034.
  It is excluded; credit notes are covered by the spec's own example in
  ``test_spec_vectors.py``.

ECDSA signatures are randomised, so the vectors' signatures are verified
against the published public key rather than compared.
"""

from __future__ import annotations

import base64
import json
from datetime import date, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from zw_compliance.fdms.money import to_decimal
from zw_compliance.fdms.qr import receipt_qr_data, receipt_qr_url
from zw_compliance.fdms.receipt import compute_taxes
from zw_compliance.fdms.signer import sha256_base64
from zw_compliance.fdms.signing import fiscal_day_string, receipt_string
from zw_compliance.fdms.wire import counters_from_wire, receipt_from_wire

VECTORS = json.loads((Path(__file__).parent / "zimra-fdms-vectors.json").read_text())
PUBLIC_KEY = serialization.load_pem_public_key(VECTORS["publicKeyPem"].encode())


def _strip_tax_codes(raw: dict) -> dict:
	raw = json.loads(json.dumps(raw))
	for row in raw["receiptLines"] + raw["receiptTaxes"]:
		row.pop("taxCode", None)
	return raw


def _verify(canonical: str, signature_b64: str) -> None:
	PUBLIC_KEY.verify(base64.b64decode(signature_b64), canonical.encode(), ec.ECDSA(hashes.SHA256()))


RECEIPTS = [v for v in VECTORS["receipts"] if v["receipt"]["receiptType"] != "CreditNote"]


@pytest.mark.parametrize("vector", RECEIPTS, ids=lambda v: v["name"])
def test_receipt_canonical_string(vector):
	receipt = receipt_from_wire(_strip_tax_codes(vector["receipt"]))
	canonical = receipt_string(vector["deviceId"], receipt, vector["previousReceiptHash"])
	assert canonical == vector["canonical"]
	assert sha256_base64(canonical) == vector["hash"]
	_verify(canonical, vector["signature"])


@pytest.mark.parametrize("vector", RECEIPTS, ids=lambda v: v["name"])
def test_receipt_tax_table_matches_lines(vector):
	receipt = receipt_from_wire(_strip_tax_codes(vector["receipt"]))
	if not receipt.lines_tax_inclusive:
		pytest.skip("this app always submits tax-inclusive receipts")
	assert compute_taxes(receipt.lines) == receipt.taxes


@pytest.mark.parametrize("vector", VECTORS["fiscalDays"], ids=lambda v: v["name"])
def test_fiscal_day_canonical_string(vector):
	counters = counters_from_wire(vector["counters"])
	canonical = fiscal_day_string(
		vector["deviceId"], vector["fiscalDayNo"], date.fromisoformat(vector["fiscalDayDate"]), counters
	)
	assert canonical == vector["canonical"]
	assert sha256_base64(canonical) == vector["hash"]
	_verify(canonical, vector["signature"])


@pytest.mark.parametrize("vector", VECTORS["qr"], ids=lambda v: v["name"])
def test_qr(vector):
	"""The SDK prints lower-case hex; this app follows the spec's upper-case
	examples (§11). Hex is case-insensitive, so compare accordingly."""
	qr_data = receipt_qr_data(vector["deviceSignatureBase64"])
	url = receipt_qr_url(
		vector["qrUrl"],
		vector["deviceId"],
		datetime.fromisoformat(vector["receiptDate"]),
		vector["receiptGlobalNo"],
		qr_data,
	)
	assert url.lower() == vector["expected"].lower()
	assert qr_data == qr_data.upper()


@pytest.mark.parametrize("vector", VECTORS["sha256"], ids=lambda v: v["input"][:20] or "empty")
def test_sha256(vector):
	assert sha256_base64(vector["input"]) == vector["sha256Base64"]


@pytest.mark.parametrize("vector", VECTORS["taxes"], ids=lambda v: v["name"])
def test_tax_vectors(vector):
	from zw_compliance.fdms.types import ReceiptLine

	if not vector["taxInclusive"]:
		pytest.skip("this app always submits tax-inclusive receipts")
	lines = [
		ReceiptLine(
			l["receiptLineType"],
			l["receiptLineNo"],
			l["receiptLineName"],
			to_decimal(l["receiptLineQuantity"]),
			to_decimal(l["receiptLineTotal"]),
			l["taxID"],
			None if l["taxPercent"] is None else to_decimal(l["taxPercent"]),
		)
		for l in vector["lines"]
	]
	got = {(t.tax_id, t.tax_amount, t.sales_amount_with_tax) for t in compute_taxes(lines)}
	expected = {
		(e["taxID"], to_decimal(e["taxAmount"]), to_decimal(e["salesAmountWithTax"]))
		for e in vector["expected"]
	}
	assert got == expected
