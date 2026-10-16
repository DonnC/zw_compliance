"""Receipt assembly, local enforcement of FDMS validation rules (§4.7, §8.2.1)
and the fiscal counter table (§6)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal as D

import pytest

from zw_compliance.fdms.errors import ReceiptValidationError
from zw_compliance.fdms.receipt import build_receipt, compute_taxes, counters_for, exact_price, merge_counters
from zw_compliance.fdms.types import (
	Buyer,
	CreditDebitNote,
	FiscalCounter,
	LineInput,
	PaymentInput,
	ReceiptDraft,
	ReceiptLine,
)

WHEN = datetime(2026, 10, 1, 9, 30, 0)
VAT = D("15")


def line(total, qty="1", tax_id=1, percent=VAT, hs="12345678", **kw):
	return LineInput(
		name=kw.pop("name", "Item"),
		quantity=D(qty),
		total=D(total),
		tax_id=tax_id,
		tax_percent=percent,
		hs_code=hs,
		**kw,
	)


def draft(lines, payments, rtype="FiscalInvoice", **kw):
	return ReceiptDraft(
		receipt_type=rtype,
		currency="USD",
		invoice_no=kw.pop("invoice_no", "SINV-0001"),
		lines=lines,
		payments=payments,
		**kw,
	)


def build(d, vat=True):
	return build_receipt(d, counter=1, global_no=1, date=WHEN, is_vat_payer=vat)


def rejected(code, d, vat=True):
	with pytest.raises(ReceiptValidationError) as exc:
		build(d, vat)
	assert exc.value.code == code, exc.value


# -- assembly --------------------------------------------------------------


def test_invoice_with_mixed_taxes():
	r = build(
		draft(
			[
				line("115.00"),
				line("23.00", qty="2"),
				line("10.00", tax_id=2, percent=D("0")),
				line("5.00", tax_id=3, percent=None),
			],
			[PaymentInput("Cash", D("100.00")), PaymentInput("Card", D("53.00"))],
		)
	)
	assert r.total == D("153.00")
	assert [l.line_no for l in r.lines] == [1, 2, 3, 4]
	assert r.lines[1].price == D("11.5")
	vat = next(t for t in r.taxes if t.tax_id == 1)
	assert vat.sales_amount_with_tax == D("138.00")
	assert vat.tax_amount == D("18.00")  # 138 * 15 / 115, computed on the group (RCPT026)
	assert [t.tax_amount for t in r.taxes if t.tax_id != 1] == [D("0"), D("0")]


def test_tax_is_computed_on_the_group_not_per_line():
	lines = [ReceiptLine("Sale", i, "x", D(1), D("0.10"), 1, VAT) for i in range(1, 4)]
	(tax,) = compute_taxes(lines)
	# per line: round(0.10*15/115)=0.01 each → 0.03; on the group: round(0.30*15/115)=0.04
	assert tax.tax_amount == D("0.04")


@pytest.mark.parametrize(
	"gross,expected", [("0.01", "0.00"), ("0.03", "0.02"), ("0.05", "0.02"), ("0.07", "0.04")]
)
def test_half_cent_ties_round_to_even(gross, expected):
	# With a (synthetic) 100% rate tax = gross / 2, so odd cents give exact
	# half-cent ties: banker's rounding sends them to the even cent.
	lines = [ReceiptLine("Sale", 1, "x", D(1), D(gross), 1, D("100"))]
	assert compute_taxes(lines)[0].tax_amount == D(expected)


@pytest.mark.parametrize(
	"total,qty,expected",
	[("10.00", "1", D("10.00")), ("23.00", "2", D("11.5")), ("10.00", "3", None), ("600.00", "0.555", None)],
)
def test_exact_price_is_omitted_when_inexact(total, qty, expected):
	assert exact_price(D(total), D(qty)) == expected


def test_credit_note_is_negative_and_counts_negatively():
	r = build(
		draft(
			[line("-115.00")],
			[PaymentInput("Cash", D("-115.00"))],
			rtype="CreditNote",
			notes="Goods returned",
			credit_debit_note=CreditDebitNote(receipt_id=48377),
		)
	)
	assert r.total == D("-115.00")
	assert r.lines[0].price == D("-115.00")
	counters = {c.counter_type: c.value for c in counters_for(r)}
	assert counters == {
		"CreditNoteByTax": D("-115.00"),
		"CreditNoteTaxByTax": D("-15.00"),
		"BalanceByMoneyType": D("-115.00"),
	}


def test_debit_note_counters():
	r = build(
		draft(
			[line("23.00")],
			[PaymentInput("BankTransfer", D("23.00"))],
			rtype="DebitNote",
			notes="Price adjustment",
			credit_debit_note=CreditDebitNote(receipt_id=1),
		)
	)
	types = {c.counter_type for c in counters_for(r)}
	assert types == {"DebitNoteByTax", "DebitNoteTaxByTax", "BalanceByMoneyType"}


def test_counters_merge_by_key():
	a = [FiscalCounter("SaleByTax", "USD", D("10"), 1, VAT)]
	b = [FiscalCounter("SaleByTax", "USD", D("5"), 1, VAT), FiscalCounter("SaleByTax", "ZWG", D("7"), 1, VAT)]
	merged = merge_counters(a, b)
	assert [(c.currency, c.value) for c in merged] == [("USD", D("15")), ("ZWG", D("7"))]


def test_discount_line_on_invoice():
	r = build(
		draft([line("100.00"), line("-10.00", line_type="Discount")], [PaymentInput("Cash", D("90.00"))])
	)
	assert r.total == D("90.00")
	assert r.taxes[0].sales_amount_with_tax == D("90.00")


# -- local enforcement of FDMS rules --------------------------------------


def test_rcpt016_lines_required():
	rejected("RCPT016", draft([], [PaymentInput("Cash", D("0"))]))


def test_rcpt018_payments_required():
	rejected("RCPT018", draft([line("10.00")], []))


def test_rcpt039_payments_must_equal_total():
	rejected("RCPT039", draft([line("10.00")], [PaymentInput("Cash", D("9.99"))]))


def test_rcpt022_invoice_sale_line_must_be_positive():
	rejected("RCPT022", draft([line("0.00")], [PaymentInput("Cash", D("0"))]))


def test_rcpt022_credit_note_sale_line_must_be_negative():
	rejected(
		"RCPT022",
		draft(
			[line("10.00")],
			[PaymentInput("Cash", D("10.00"))],
			rtype="CreditNote",
			notes="x",
			credit_debit_note=CreditDebitNote(receipt_id=1),
		),
	)


def test_rcpt022_invoice_discount_must_be_negative():
	rejected(
		"RCPT022",
		draft([line("10.00"), line("1.00", line_type="Discount")], [PaymentInput("Cash", D("11.00"))]),
	)


def test_rcpt023_quantity_must_be_positive():
	rejected("RCPT023", draft([line("10.00", qty="0")], [PaymentInput("Cash", D("10.00"))]))


def test_rcpt028_credit_note_payments_negative():
	rejected(
		"RCPT028",
		draft(
			[line("-10.00")],
			[PaymentInput("Cash", D("10.00")), PaymentInput("Card", D("-20.00"))],
			rtype="CreditNote",
			notes="x",
			credit_debit_note=CreditDebitNote(receipt_id=1),
		),
	)


def test_rcpt034_notes_mandatory_for_credit_note():
	rejected(
		"RCPT034",
		draft(
			[line("-10.00")],
			[PaymentInput("Cash", D("-10.00"))],
			rtype="CreditNote",
			credit_debit_note=CreditDebitNote(receipt_id=1),
		),
	)


def test_rcpt015_reference_mandatory_for_credit_note():
	rejected(
		"RCPT015", draft([line("-10.00")], [PaymentInput("Cash", D("-10.00"))], rtype="CreditNote", notes="x")
	)


def test_rcpt015_reference_needs_id_or_device_triple():
	rejected(
		"RCPT015",
		draft(
			[line("-10.00")],
			[PaymentInput("Cash", D("-10.00"))],
			rtype="CreditNote",
			notes="x",
			credit_debit_note=CreditDebitNote(receipt_global_no=4),
		),
	)


def test_rcpt029_invoice_cannot_reference_receipt():
	rejected(
		"RCPT029",
		draft(
			[line("10.00")],
			[PaymentInput("Cash", D("10.00"))],
			credit_debit_note=CreditDebitNote(receipt_id=1),
		),
	)


def test_rcpt021_non_vat_payer_cannot_charge_vat():
	rejected("RCPT021", draft([line("10.00", hs=None)], [PaymentInput("Cash", D("10.00"))]), vat=False)


def test_non_vat_payer_zero_rated_without_hs_code_is_fine():
	build(draft([line("10.00", percent=D("0"), hs=None)], [PaymentInput("Cash", D("10.00"))]), vat=False)


def test_rcpt047_hs_code_mandatory_for_vat_payer():
	rejected("RCPT047", draft([line("10.00", hs=None)], [PaymentInput("Cash", D("10.00"))]))


@pytest.mark.parametrize(
	"hs,percent,ok",
	[
		("1234", VAT, True),
		("12345678", VAT, True),
		("123456", VAT, False),
		("1234", D("0"), False),
		("1234", None, False),
		("12345678", None, True),
		("12A4", VAT, False),
	],
)
def test_rcpt048_hs_code_length(hs, percent, ok):
	d = draft([line("10.00", hs=hs, percent=percent)], [PaymentInput("Cash", D("10.00"))])
	if ok:
		build(d)
	else:
		rejected("RCPT048", d)


def test_rcpt043_buyer_needs_name_and_tin():
	rejected(
		"RCPT043",
		draft(
			[line("10.00")], [PaymentInput("Cash", D("10.00"))], buyer=Buyer(register_name="ABC Ltd", tin="")
		),
	)
	rejected(
		"RCPT043",
		draft(
			[line("10.00")],
			[PaymentInput("Cash", D("10.00"))],
			buyer=Buyer(register_name="ABC Ltd", tin="12345"),
		),
	)


def test_tax_code_all_or_none():
	rejected("RCPT02", draft([line("10.00", tax_code="A"), line("5.00")], [PaymentInput("Cash", D("15.00"))]))


def test_currency_must_be_iso():
	with pytest.raises(ReceiptValidationError) as exc:
		build_receipt(
			ReceiptDraft("FiscalInvoice", "US", "X", [line("1.00")], [PaymentInput("Cash", D("1.00"))]),
			counter=1,
			global_no=1,
			date=WHEN,
			is_vat_payer=True,
		)
	assert exc.value.code == "RCPT010"


def test_receipt_date_drops_microseconds():
	r = build_receipt(
		draft([line("1.00")], [PaymentInput("Cash", D("1.00"))]),
		counter=1,
		global_no=1,
		date=WHEN.replace(microsecond=999),
		is_vat_payer=True,
	)
	assert r.date.microsecond == 0
