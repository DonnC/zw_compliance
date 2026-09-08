"""Wire types for FDMS Device/v1 and Public/v1, ported from src/core/types.ts.

Field names are JSON-wire-exact (camelCase) - these dataclasses are (de)
serialised straight to/from the FDMS JSON payloads, so do not rename fields
to snake_case here even though the rest of this codebase is snake_case.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

FDMS_BASE_URLS = {
	"test": "https://fdmsapitest.zimra.co.zw",
	"production": "https://fdmsapi.zimra.co.zw",
}

ReceiptType = Literal["FiscalInvoice", "CreditNote", "DebitNote"]
ReceiptLineType = Literal["Sale", "Discount"]
MoneyType = Literal["Cash", "Card", "MobileWallet", "Coupon", "Credit", "BankTransfer", "Other"]
FiscalCounterType = Literal[
	"SaleByTax",
	"SaleTaxByTax",
	"CreditNoteByTax",
	"CreditNoteTaxByTax",
	"DebitNoteByTax",
	"DebitNoteTaxByTax",
	"BalanceByMoneyType",
	"PayoutByTax",
	"PayoutTaxByTax",
]
FiscalDayStatus = Literal[
	"FiscalDayClosed", "FiscalDayOpened", "FiscalDayCloseInitiated", "FiscalDayCloseFailed"
]
DeviceOperatingMode = Literal["Online", "Offline"]
EcdsaSignatureFormat = Literal["p1363", "der"]


@dataclass
class DeviceIdentity:
	device_id: int
	serial_number: str
	model_name: str
	model_version: str


@dataclass
class TaxDefinition:
	tax_id: int
	tax_name: str
	tax_percent: Optional[float]
	valid_from: str
	valid_till: Optional[str] = None


@dataclass
class AddressDto:
	province: Optional[str] = None
	city: Optional[str] = None
	street: Optional[str] = None
	house_no: Optional[str] = None


@dataclass
class GetConfigResponse:
	operation_id: str
	tax_payer_name: str
	tax_payer_tin: str
	vat_number: Optional[str]
	device_serial_no: str
	device_branch_name: str
	device_branch_address: Optional[dict]
	device_branch_contacts: Optional[dict]
	device_operating_mode: DeviceOperatingMode
	tax_payer_day_max_hrs: int
	applicable_taxes: list[TaxDefinition]
	certificate_valid_till: str
	qr_url: str
	taxpayer_day_end_notification_hrs: Optional[int] = None


@dataclass
class SignatureData:
	hash: str
	signature: str


@dataclass
class FiscalDayCounter:
	fiscal_counter_type: FiscalCounterType
	fiscal_counter_currency: str
	fiscal_counter_value: float
	fiscal_counter_tax_percent: Optional[float] = None
	fiscal_counter_tax_id: Optional[int] = None
	fiscal_counter_money_type: Optional[MoneyType] = None


@dataclass
class GetStatusResponse:
	operation_id: str
	fiscal_day_status: FiscalDayStatus
	fiscal_day_reconciliation_mode: Optional[str] = None
	fiscal_day_server_signature: Optional[SignatureData] = None
	fiscal_day_closed: Optional[str] = None
	fiscal_day_counter: Optional[int] = None
	last_receipt_global_no: Optional[int] = None
	last_fiscal_day_no: Optional[int] = None
	fiscal_day_closing_error_code: Optional[str] = None
	fiscal_day_document_quantities: Optional[dict] = None


@dataclass
class OpenDayResponse:
	operation_id: str
	fiscal_day_no: int


@dataclass
class CloseDayResponse:
	operation_id: str


@dataclass
class ReceiptLine:
	receipt_line_type: ReceiptLineType
	receipt_line_no: int
	receipt_line_name: str
	receipt_line_quantity: float
	receipt_line_total: float
	tax_id: int
	receipt_line_hs_code: Optional[str] = None
	receipt_line_price: Optional[float] = None
	tax_code: Optional[str] = None
	tax_percent: Optional[float] = None


@dataclass
class ReceiptTax:
	tax_id: int
	tax_amount: float
	sales_amount_with_tax: float
	tax_code: Optional[str] = None
	tax_percent: Optional[float] = None


@dataclass
class Payment:
	money_type_code: MoneyType
	payment_amount: float


@dataclass
class CreditDebitNote:
	receipt_id: Optional[int] = None
	device_id: Optional[int] = None
	receipt_global_no: Optional[int] = None
	fiscal_day_no: Optional[int] = None


@dataclass
class BuyerData:
	buyer_register_name: Optional[str] = None
	buyer_trade_name: Optional[str] = None
	vat_number: Optional[str] = None
	buyer_tin: Optional[str] = None
	buyer_contacts: Optional[dict] = None
	buyer_address: Optional[dict] = None


@dataclass
class Receipt:
	receipt_type: ReceiptType
	receipt_currency: str
	receipt_counter: int
	receipt_global_no: int
	invoice_no: str
	receipt_date: str
	receipt_lines_tax_inclusive: bool
	receipt_lines: list[ReceiptLine]
	receipt_taxes: list[ReceiptTax]
	receipt_payments: list[Payment]
	receipt_total: float
	receipt_device_signature: Optional[SignatureData] = None
	buyer_data: Optional[BuyerData] = None
	receipt_notes: Optional[str] = None
	credit_debit_note: Optional[CreditDebitNote] = None
	receipt_print_form: Optional[str] = None


@dataclass
class ValidationError:
	validation_error_code: Optional[str] = None
	validation_error_color: Optional[str] = None
	validation_error_description: Optional[str] = None


@dataclass
class SubmitReceiptResponse:
	operation_id: str
	receipt_id: int
	server_date: str
	receipt_server_signature: Optional[SignatureData] = None
	validation_errors: list[ValidationError] = field(default_factory=list)


@dataclass
class PingResponse:
	operation_id: str
	reporting_frequency: Optional[int] = None


@dataclass
class ApiProblemDetails:
	type: Optional[str] = None
	title: Optional[str] = None
	status: Optional[int] = None
	detail: Optional[str] = None
	error_code: Optional[str] = None
	extra: dict = field(default_factory=dict)


@dataclass
class ReceiptLineInput:
	name: str
	price: object  # int major units, float (must be whole) or fdms.money.Money
	quantity: float
	tax_id: int
	tax_percent: Optional[float] = None
	tax_code: Optional[str] = None
	hs_code: Optional[str] = None


@dataclass
class PaymentInput:
	money_type: MoneyType
	amount: object  # int major units, float (must be whole) or fdms.money.Money


@dataclass
class ReceiptInput:
	currency: str
	invoice_no: str
	lines: list[ReceiptLineInput]
	payments: list[PaymentInput]
	receipt_type: ReceiptType = "FiscalInvoice"
	lines_tax_inclusive: bool = True
	receipt_date: Optional[object] = None  # datetime, explicit dates trusted verbatim
	notes: Optional[str] = None
	buyer: Optional[BuyerData] = None
	credit_debit_note: Optional[CreditDebitNote] = None


@dataclass
class RedValidationError:
	receipt_global_no: int
	receipt_counter: int
	code: str
	description: Optional[str] = None


@dataclass
class FiscalDayState:
	fiscal_day_no: int
	fiscal_day_date: str
	receipt_counter: int
	receipt_global_no: int
	counters: list[FiscalDayCounter] = field(default_factory=list)
	previous_receipt_hash: Optional[str] = None
	previous_receipt_date: Optional[str] = None
	red_errors: list[RedValidationError] = field(default_factory=list)


@dataclass
class PreparedReceipt:
	receipt: Receipt
	date: str
	state_after: FiscalDayState


@dataclass
class SubmittedReceipt:
	receipt: Receipt
	response: Optional[SubmitReceiptResponse]
	qr_data: Optional[str] = None
