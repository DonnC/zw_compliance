"""Data model for the ZIMRA Fiscal Device Gateway API (spec v7.2).

Python-side names are snake_case; :mod:`zw_compliance.fdms.wire` is the single
place that maps them to the camelCase JSON the gateway expects. All amounts are
:class:`decimal.Decimal` (see :mod:`zw_compliance.fdms.money`). Datetimes are
naive local wall-clock values: the spec transmits "local time without time
zone information" everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

FDMS_BASE_URLS = {
	"test": "https://fdmsapitest.zimra.co.zw",  # spec §7.2
	"production": "https://fdmsapi.zimra.co.zw",
}

# Enums, spec §5.4. Order matters where noted: it is the "enum order" used for
# sorting in the fiscal-day signature (§13.3.1).
RECEIPT_TYPES = ("FiscalInvoice", "CreditNote", "DebitNote")  # §5.4.6
RECEIPT_LINE_TYPES = ("Sale", "Discount")  # §5.4.7
RECEIPT_PRINT_FORMS = ("Receipt48", "InvoiceA4")  # §5.4.8
MONEY_TYPES = ("Cash", "Card", "MobileWallet", "Coupon", "Credit", "BankTransfer", "Other")  # §5.4.5
FISCAL_COUNTER_TYPES = (  # §5.4.4
	"SaleByTax",
	"SaleTaxByTax",
	"CreditNoteByTax",
	"CreditNoteTaxByTax",
	"DebitNoteByTax",
	"DebitNoteTaxByTax",
	"BalanceByMoneyType",
)
FISCAL_DAY_STATUSES = (  # §5.4.2
	"FiscalDayClosed",
	"FiscalDayOpened",
	"FiscalDayCloseInitiated",
	"FiscalDayCloseFailed",
)
VALIDATION_COLOURS = ("Grey", "Yellow", "Red")  # §8.2.1

ReceiptType = Literal["FiscalInvoice", "CreditNote", "DebitNote"]
ReceiptLineType = Literal["Sale", "Discount"]
ReceiptPrintForm = Literal["Receipt48", "InvoiceA4"]
MoneyType = Literal["Cash", "Card", "MobileWallet", "Coupon", "Credit", "BankTransfer", "Other"]


@dataclass(frozen=True)
class DeviceIdentity:
	device_id: int
	serial_number: str
	model_name: str
	model_version: str


@dataclass
class Address:  # §5.1
	province: str | None = None
	city: str | None = None
	street: str | None = None
	house_no: str | None = None
	district: str | None = None


@dataclass
class Contacts:  # §5.2
	phone_no: str | None = None
	email: str | None = None


@dataclass
class Buyer:  # §4.7 Buyer
	register_name: str
	tin: str
	trade_name: str | None = None
	vat_number: str | None = None
	address: Address | None = None
	contacts: Contacts | None = None


@dataclass
class CreditDebitNote:  # §4.7 CreditDebitNote
	receipt_id: int | None = None
	device_id: int | None = None
	receipt_global_no: int | None = None
	fiscal_day_no: int | None = None


@dataclass
class UserInfo:  # Swagger ReceiptDto.userInfo
	username: str
	full_name: str


@dataclass
class SignatureData:  # §5.3
	hash: str  # base64 SHA-256
	signature: str  # base64 signature


@dataclass
class LineInput:
	"""One receipt line before numbering and tax aggregation.

	``total`` is tax inclusive and already carries the receipt's sign (negative
	for credit-note sale lines, negative for invoice discount lines - RCPT022).
	``quantity`` is always positive (RCPT023).
	"""

	name: str
	quantity: Decimal
	total: Decimal
	tax_id: int
	tax_percent: Decimal | None  # None = exempt (field omitted on the wire)
	hs_code: str | None = None
	line_type: ReceiptLineType = "Sale"
	tax_code: str | None = None


@dataclass
class PaymentInput:
	money_type: MoneyType
	amount: Decimal  # signed like the receipt total (RCPT028)


@dataclass
class ReceiptDraft:
	"""Everything needed to number, sign and submit a receipt."""

	receipt_type: ReceiptType
	currency: str
	invoice_no: str
	lines: list[LineInput]
	payments: list[PaymentInput]
	print_form: ReceiptPrintForm = "Receipt48"
	buyer: Buyer | None = None
	notes: str | None = None
	credit_debit_note: CreditDebitNote | None = None
	user: UserInfo | None = None


@dataclass
class ReceiptLine:
	line_type: ReceiptLineType
	line_no: int
	name: str
	quantity: Decimal
	total: Decimal
	tax_id: int
	tax_percent: Decimal | None
	price: Decimal | None = None
	hs_code: str | None = None
	tax_code: str | None = None


@dataclass
class ReceiptTax:
	tax_id: int
	tax_percent: Decimal | None
	tax_amount: Decimal
	sales_amount_with_tax: Decimal
	tax_code: str | None = None


@dataclass
class Payment:
	money_type: MoneyType
	amount: Decimal


@dataclass
class Receipt:
	receipt_type: ReceiptType
	currency: str
	counter: int
	global_no: int
	invoice_no: str
	date: datetime
	lines: list[ReceiptLine]
	taxes: list[ReceiptTax]
	payments: list[Payment]
	total: Decimal
	print_form: ReceiptPrintForm = "Receipt48"
	lines_tax_inclusive: bool = True
	buyer: Buyer | None = None
	notes: str | None = None
	credit_debit_note: CreditDebitNote | None = None
	user: UserInfo | None = None
	device_signature: SignatureData | None = None


@dataclass
class FiscalCounter:  # §4.11 FiscalDayCounter
	counter_type: str
	currency: str
	value: Decimal
	tax_id: int | None = None
	tax_percent: Decimal | None = None
	money_type: MoneyType | None = None

	def key(self) -> tuple:
		return (self.counter_type, self.currency, self.tax_id, self.tax_percent, self.money_type)


@dataclass
class Tax:  # §4.4 Tax
	tax_id: int
	tax_name: str
	tax_percent: Decimal | None
	valid_from: date | None
	valid_till: date | None = None


@dataclass
class TaxpayerInfo:  # §4.1 output
	operation_id: str
	taxpayer_name: str
	taxpayer_tin: str
	vat_number: str | None
	branch_name: str
	branch_address: Address | None
	branch_contacts: Contacts | None


@dataclass
class DeviceConfig:  # §4.4 output
	operation_id: str
	taxpayer_name: str
	taxpayer_tin: str
	vat_number: str | None
	device_serial_no: str
	branch_name: str
	branch_address: Address | None
	branch_contacts: Contacts | None
	operating_mode: str
	day_max_hours: int
	day_end_notification_hours: int
	applicable_taxes: list[Tax]
	certificate_valid_till: date | None
	qr_url: str


@dataclass
class ValidationError:  # Swagger ValidationError
	code: str
	colour: str
	description: str


@dataclass
class DayStatus:  # §4.5 output
	operation_id: str
	fiscal_day_status: str
	reconciliation_mode: str | None = None
	server_signature: dict | None = None
	fiscal_day_closed: datetime | None = None
	closing_error_code: str | None = None
	last_receipt_global_no: int | None = None
	last_fiscal_day_no: int | None = None
	counters: list[FiscalCounter] = field(default_factory=list)


@dataclass
class SubmitReceiptResult:  # §4.7 output
	operation_id: str
	receipt_id: int
	server_date: datetime
	server_signature: dict
	validation_errors: list[ValidationError] = field(default_factory=list)
