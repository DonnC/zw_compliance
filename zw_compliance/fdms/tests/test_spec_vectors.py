"""Conformance against the worked examples printed in ZIMRA's specification
(Fiscal Device Gateway API v7.2, §11 and §13).

Only examples that are internally consistent in the PDF are used. Several
printed examples (§13.2.1 FiscalInvoice 1-2, CreditNote 1, DebitNote 1-2)
do not reproduce their own printed hashes from their printed inputs, so they
cannot serve as vectors; CreditNote example 2 and the fiscal-day example do.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal as D

from zw_compliance.fdms.qr import receipt_qr_url, verification_code
from zw_compliance.fdms.signer import sha256_base64
from zw_compliance.fdms.signing import counters_string, receipt_string
from zw_compliance.fdms.types import FiscalCounter, Payment, Receipt, ReceiptLine, ReceiptTax

PREVIOUS_HASH = "hNVJXP/ACOiE8McD3pKsDlqBXpuaUqQOfPnMyfZWI9k="


def test_credit_note_example_2_signature_input_and_hash():
	"""§13.2.1 CreditNote example No 2: negative amounts, exempt / 0% / 14.5% taxes."""
	taxes = [
		ReceiptTax(1, None, D("0.00"), D("-7.00")),
		ReceiptTax(2, D("0"), D("0.00"), D("-10.00")),
		ReceiptTax(3, D("14.5"), D("-3.00"), D("-23.00")),
	]
	receipt = Receipt(
		receipt_type="CreditNote",
		currency="USD",
		counter=1,
		global_no=85,
		invoice_no="CN-85",
		date=datetime(2020, 9, 19, 9, 23, 7),
		lines=[ReceiptLine("Sale", 1, "x", D(1), D("-40.35"), 1, None)],
		taxes=taxes,
		payments=[Payment("Cash", D("-40.35"))],
		total=D("-40.35"),
	)
	canonical = receipt_string(322, receipt, PREVIOUS_HASH)
	assert (
		canonical == "322CREDITNOTEUSD852020-09-19T09:23:07-40350-7000.000-100014.50-300-2300" + PREVIOUS_HASH
	)
	assert sha256_base64(canonical) == "F9/QB0vhxQlEF2nk+oebwP8V+qBcNlOFvoTeE/1QxPc="


def test_fiscal_day_example_hash_and_counter_segments():
	"""§13.3.1 example. The printed concatenation (including its "USDLCASH"
	typo) reproduces the printed hash, which pins the hashing step; the counter
	segments are then checked one by one against our formatter."""
	printed = (
		"321842019-09-23SALEBYTAXZWL2300000SALEBYTAXZWL0.001200000SALEBYTAXUSD14.502500"
		"SALEBYTAXZWL15.001200SALETAXBYTAXUSD15.00250SALETAXBYTAXZWL15.00230000"
		"BALANCEBYMONEYTYPEUSDLCASH3700BALANCEBYMONEYTYPEZWLCASH2000000BALANCEBYMONEYTYPEZWLCARD1500000"
	)
	assert sha256_base64(printed) == "OdT8lLI0JXhXl1XQgr64Zb1ltFDksFXThVxqM6O8xZE="

	cases = [
		(FiscalCounter("SaleByTax", "ZWL", D("23000.00"), 1, None), "SALEBYTAXZWL2300000"),
		(FiscalCounter("SaleByTax", "ZWL", D("12000.00"), 2, D("0")), "SALEBYTAXZWL0.001200000"),
		(FiscalCounter("SaleByTax", "USD", D("25.00"), 3, D("14.5")), "SALEBYTAXUSD14.502500"),
		(FiscalCounter("SaleTaxByTax", "ZWL", D("2300.00"), 4, D("15")), "SALETAXBYTAXZWL15.00230000"),
		(
			FiscalCounter("BalanceByMoneyType", "ZWL", D("15000.00"), money_type="Card"),
			"BALANCEBYMONEYTYPEZWLCARD1500000",
		),
		(
			FiscalCounter("BalanceByMoneyType", "USD", D("37.00"), money_type="Cash"),
			"BALANCEBYMONEYTYPEUSDCASH3700",
		),
	]
	for counter, expected in cases:
		assert counters_string([counter]) == expected


def test_balance_counters_sort_by_money_type_enum_order():
	"""§13.3.1 / §5.4.5: Cash (0) sorts before Card (1) within a currency."""
	counters = [
		FiscalCounter("BalanceByMoneyType", "ZWL", D("150"), money_type="Card"),
		FiscalCounter("BalanceByMoneyType", "ZWL", D("200"), money_type="Cash"),
	]
	assert counters_string(counters) == "BALANCEBYMONEYTYPEZWLCASH20000BALANCEBYMONEYTYPEZWLCARD15000"


def test_qr_examples():
	"""§11 examples No 1 and No 2."""
	assert (
		receipt_qr_url(
			"https://invoice.zimra.co.zw", 321, datetime(2023, 4, 3), 1112223331, "4C8BE27663330417"
		)
		== "https://invoice.zimra.co.zw/0000000321030420231112223331" + "4C8BE27663330417"
	)
	assert (
		receipt_qr_url("https://invoice.zimra.co.zw/", 322, datetime(2023, 4, 4), 1332, "C10B0476B3B14678")
		== "https://invoice.zimra.co.zw/0000000322040420230000001332" + "C10B0476B3B14678"
	)


def test_verification_code_grouping():
	"""§10.3 [46]: QR data shown in four-character groups."""
	assert verification_code("4C8BE27663330417") == "4C8B-E276-6333-0417"


def test_tax_percent_formatting_rules():
	"""§13.2.1 field 7 examples: 15 → 15.00, 14.5 → 14.50, 0 → 0.00, exempt → empty."""
	from zw_compliance.fdms.signing import format_percent

	assert format_percent(D("15")) == "15.00"
	assert format_percent(D("14.5")) == "14.50"
	assert format_percent(D("0")) == "0.00"
	assert format_percent(None) == ""


def test_tax_block_includes_tax_code_and_sorts_empty_code_first():
	"""§13.2.1 field 7: taxCode‖taxPercent‖taxAmount‖salesAmountWithTax, ordered by
	taxID then taxCode, empty taxCode before "A"."""
	from zw_compliance.fdms.signing import receipt_taxes_string

	taxes = [
		ReceiptTax(3, D("15"), D("3.00"), D("23.00"), "D"),
		ReceiptTax(3, D("15"), D("1.50"), D("11.50"), "C"),
		ReceiptTax(1, None, D("0"), D("25.00"), "A"),
	]
	assert receipt_taxes_string(taxes) == "A02500C15.001501150D15.003002300"
	assert receipt_taxes_string(
		[ReceiptTax(1, D("15"), D("1"), D("2"), None), ReceiptTax(1, D("15"), D("1"), D("2"), "A")]
	) == ("15.00100200" + "A15.00100200")


def test_fiscal_day_date_is_formatted_iso():
	from zw_compliance.fdms.signing import fiscal_day_string

	assert fiscal_day_string(321, 84, date(2019, 9, 23), []) == "321842019-09-23"
