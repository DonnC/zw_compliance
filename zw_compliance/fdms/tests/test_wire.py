"""JSON mapping: camelCase names from ZIMRA's live Swagger, omission rules."""

from datetime import datetime
from decimal import Decimal as D

from zw_compliance.fdms.receipt import build_receipt
from zw_compliance.fdms.types import (
	Address,
	Buyer,
	Contacts,
	CreditDebitNote,
	FiscalCounter,
	LineInput,
	PaymentInput,
	ReceiptDraft,
	SignatureData,
	UserInfo,
)
from zw_compliance.fdms.wire import (
	counters_to_wire,
	parse_config,
	parse_status,
	receipt_from_wire,
	receipt_to_wire,
)


def _credit_note():
	r = build_receipt(
		ReceiptDraft(
			receipt_type="CreditNote",
			currency="usd",
			invoice_no="SINV-RET-0001",
			lines=[
				LineInput("Shoes", D("3"), D("-34.50"), 1, D("15"), "64039900"),
				LineInput("Bread", D("1"), D("-1.20"), 3, None, "19059000"),
			],
			payments=[PaymentInput("Cash", D("-35.70"))],
			print_form="InvoiceA4",
			buyer=Buyer(
				"ABC Ltd",
				"2000123456",
				"ABC Shop",
				"220000123",
				Address("Harare", "Harare", "Samora Machel", "12"),
				Contacts("0772000000", "a@b.zw"),
			),
			notes="Damaged goods",
			credit_debit_note=CreditDebitNote(receipt_id=987654),
			user=UserInfo("cashier@shop.zw", "Tendai Moyo"),
		),
		counter=7,
		global_no=1031,
		date=datetime(2026, 10, 2, 14, 5, 9),
		is_vat_payer=True,
	)
	r.device_signature = SignatureData("aGFzaA==", "c2ln")
	return r


def test_receipt_wire_names_and_omissions():
	w = receipt_to_wire(_credit_note())
	assert w["receiptCurrency"] == "USD"
	assert w["receiptDate"] == "2026-10-02T14:05:09"
	assert w["buyerData"] == {
		"buyerRegisterName": "ABC Ltd",
		"buyerTradeName": "ABC Shop",
		"vatNumber": "220000123",
		"buyerTIN": "2000123456",
		"buyerContacts": {"phoneNo": "0772000000", "email": "a@b.zw"},
		"buyerAddress": {"province": "Harare", "city": "Harare", "street": "Samora Machel", "houseNo": "12"},
	}
	assert w["creditDebitNote"] == {"receiptID": 987654}
	assert w["userInfo"] == {"username": "cashier@shop.zw", "userFullName": "Tendai Moyo"}
	shoes, bread = w["receiptLines"]
	assert shoes == {
		"receiptLineType": "Sale",
		"receiptLineNo": 1,
		"receiptLineHSCode": "64039900",
		"receiptLineName": "Shoes",
		"receiptLinePrice": -11.5,
		"receiptLineQuantity": 3,
		"receiptLineTotal": -34.5,
		"taxPercent": 15,
		"taxID": 1,
	}
	assert "taxPercent" not in bread  # exempt: must not be provided
	assert w["receiptTotal"] == -35.7
	assert w["receiptPayments"] == [{"moneyTypeCode": "Cash", "paymentAmount": -35.7}]
	assert "taxCode" not in shoes


def test_receipt_round_trips_through_wire():
	original = _credit_note()
	restored = receipt_from_wire(receipt_to_wire(original))
	assert restored == original


def test_counters_to_wire_skips_zero_and_exempt_percent():
	rows = counters_to_wire(
		[
			FiscalCounter("SaleTaxByTax", "USD", D("0"), 2, D("0")),
			FiscalCounter("SaleByTax", "USD", D("10.00"), 3, None),
			FiscalCounter("BalanceByMoneyType", "USD", D("10.00"), money_type="Cash"),
		]
	)
	assert rows == [
		{
			"fiscalCounterType": "SaleByTax",
			"fiscalCounterCurrency": "USD",
			"fiscalCounterTaxID": 3,
			"fiscalCounterValue": 10,
		},
		{
			"fiscalCounterType": "BalanceByMoneyType",
			"fiscalCounterCurrency": "USD",
			"fiscalCounterMoneyType": "Cash",
			"fiscalCounterValue": 10,
		},
	]


def test_parse_config_and_status():
	config = parse_config(
		{
			"operationID": "op",
			"taxPayerName": "Shop",
			"taxPayerTIN": "2000000001",
			"vatNumber": "220000001",
			"deviceSerialNo": "SN1",
			"deviceBranchName": "Harare",
			"deviceBranchAddress": {"province": "Harare", "city": "Harare", "street": "1st", "houseNo": "1"},
			"deviceOperatingMode": "Online",
			"taxPayerDayMaxHrs": 24,
			"taxpayerDayEndNotificationHrs": 2,
			"applicableTaxes": [
				{"taxID": 1, "taxPercent": 15.0, "taxName": "VAT 15%", "validFrom": "2023-01-01T00:00:00"},
				{"taxID": 3, "taxName": "Exempt", "validFrom": "2023-01-01"},
			],
			"certificateValidTill": "2027-01-01T00:00:00",
			"qrUrl": "https://fdmstest.zimra.co.zw",
		}
	)
	assert config.day_max_hours == 24
	assert config.applicable_taxes[0].tax_percent == D("15")
	assert config.applicable_taxes[1].tax_percent is None
	status = parse_status(
		{
			"operationID": "op",
			"fiscalDayStatus": "FiscalDayClosed",
			"fiscalDayClosed": "2026-10-02T23:01:02.123",
			"lastReceiptGlobalNo": 9,
		}
	)
	assert status.fiscal_day_closed == datetime(2026, 10, 2, 23, 1, 2)
	assert status.last_receipt_global_no == 9
