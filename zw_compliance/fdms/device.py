"""FiscalDevice - the fiscal-day/receipt state machine, ported from
src/core/device.ts. This is the orchestrator every other module in this
package feeds into.

Sync, not async (unlike the TS source) - Frappe request handlers and
background jobs are sync. See the module docstrings of csr.py and signer.py
for the other deliberate deviations from a literal 1:1 port.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional

from .clock import Clock, ServerCorrectedClock
from .errors import FdmsApiError, is_network_error
from .money import amount_to_cents, cents_to_amount
from .qr import receipt_qr_data
from .serialization import (
	fiscal_day_state_from_dict,
	prepared_receipt_from_dict,
	prepared_to_dict,
	state_to_dict,
)
from .signer import EcdsaSignatureFormat, Signer, sign_canonical_string
from .signing import fdms_date, fdms_date_time, fiscal_day_signing_string, receipt_signing_string, to_cents
from .storage import KEY_DAY_STATE, KEY_LAST_GLOBAL, KEY_PENDING, Storage
from .transport import FdmsClient, FdmsClientOptions, Transport
from .types import (
	CreditDebitNote,
	DeviceIdentity,
	FiscalDayCounter,
	FiscalDayState,
	GetConfigResponse,
	GetStatusResponse,
	Payment,
	PingResponse,
	PreparedReceipt,
	Receipt,
	ReceiptLine,
	ReceiptTax,
	RedValidationError,
	SubmittedReceipt,
)

_SALE_TYPE_BY_RECEIPT_TYPE = {
	"FiscalInvoice": ("SaleByTax", "SaleTaxByTax"),
	"CreditNote": ("CreditNoteByTax", "CreditNoteTaxByTax"),
	"DebitNote": ("DebitNoteByTax", "DebitNoteTaxByTax"),
}


class DayNotClosableError(Exception):
	def __init__(self, fiscal_day_no: int, red_errors: list[RedValidationError]):
		detail = "; ".join(f"global no {e.receipt_global_no}: {e.code}" for e in red_errors)
		super().__init__(f"Fiscal day {fiscal_day_no} cannot be closed by the device: {detail}")
		self.fiscal_day_no = fiscal_day_no
		self.red_errors = red_errors


class PendingSubmitError(Exception):
	def __init__(self, receipt_global_no: int):
		super().__init__(
			f"Receipt {receipt_global_no} was submitted but its outcome is unknown. "
			"Call reconcile() before issuing another receipt."
		)
		self.receipt_global_no = receipt_global_no


@dataclass
class ReconcileResult:
	action: str  # "none" | "confirmed" | "resubmitted" | "rejected"
	receipt_global_no: Optional[int] = None
	response: Optional[dict] = None
	error: Optional[BaseException] = None


@dataclass
class FiscalDeviceOptions:
	environment: str = "test"
	base_url: Optional[str] = None
	timeout_ms: int = 30000
	retries: int = 2
	signature_format: EcdsaSignatureFormat = "der"
	on_server_date: Optional[Callable[[datetime, float], None]] = None


def _parse_fdms_datetime(text: str) -> datetime:
	return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S")


class FiscalDevice:
	def __init__(
		self,
		device: DeviceIdentity,
		signer: Signer,
		transport: Transport,
		storage: Storage,
		clock: Optional[Clock] = None,
		options: Optional[FiscalDeviceOptions] = None,
	):
		self.device = device
		self.signer = signer
		self.storage = storage
		self.options = options or FiscalDeviceOptions()
		self.clock: ServerCorrectedClock = clock if isinstance(clock, ServerCorrectedClock) else ServerCorrectedClock(clock)
		self.http = FdmsClient(
			device,
			transport,
			FdmsClientOptions(
				environment=self.options.environment,
				base_url=self.options.base_url,
				timeout_ms=self.options.timeout_ms,
				retries=self.options.retries,
				on_server_date=self._on_server_date,
			),
		)
		self.state: Optional[FiscalDayState] = None
		self.config: Optional[GetConfigResponse] = None

	def _on_server_date(self, server_date: datetime, round_trip_ms: float) -> None:
		self.clock.learn(server_date, round_trip_ms)
		if self.options.on_server_date:
			self.options.on_server_date(server_date, round_trip_ms)

	# -- state accessors --------------------------------------------------

	def get_state(self) -> Optional[FiscalDayState]:
		return copy.deepcopy(self.state)

	def restore_state(self, state: FiscalDayState) -> None:
		self.state = copy.deepcopy(state)

	def _require_day(self) -> FiscalDayState:
		if self.state is None:
			raise ValueError("No fiscal day state - call openDay() or restoreState() first.")
		return self.state

	# -- persistence --------------------------------------------------------

	def _persist_state(self) -> None:
		if self.state is None:
			return
		payload = {
			"deviceId": self.device.device_id,
			"savedAt": datetime.now().isoformat(),
			"state": state_to_dict(self.state),
		}
		self.storage.set(KEY_DAY_STATE, json.dumps(payload))
		last = self._last_issued_global_no()
		if last is None or self.state.receipt_global_no > last:
			self.storage.set(
				KEY_LAST_GLOBAL,
				json.dumps({"deviceId": self.device.device_id, "lastReceiptGlobalNo": self.state.receipt_global_no}),
			)

	def load_persisted_state(self) -> None:
		if self.state is not None:
			return
		raw = self.storage.get(KEY_DAY_STATE)
		if not raw:
			return
		payload = json.loads(raw)
		if payload.get("deviceId") != self.device.device_id:
			raise ValueError("Persisted fiscal day state belongs to a different device.")
		self.state = fiscal_day_state_from_dict(payload["state"])

	def clear_persisted_state(self) -> None:
		self.storage.delete(KEY_DAY_STATE)

	def _last_issued_global_no(self) -> Optional[int]:
		raw = self.storage.get(KEY_LAST_GLOBAL)
		if not raw:
			return None
		payload = json.loads(raw)
		if payload.get("deviceId") != self.device.device_id:
			return None
		return payload.get("lastReceiptGlobalNo")

	def _pending_receipt(self) -> Optional[PreparedReceipt]:
		raw = self.storage.get(KEY_PENDING)
		if not raw:
			return None
		return prepared_receipt_from_dict(json.loads(raw))

	def pending_receipt(self) -> Optional[PreparedReceipt]:
		return self._pending_receipt()

	def _refuse_pending(self) -> None:
		pending = self._pending_receipt()
		if pending:
			raise PendingSubmitError(pending.receipt.receipt_global_no)

	def discard_pending(self) -> None:
		self.storage.delete(KEY_PENDING)

	# -- reconcile ------------------------------------------------------

	def reconcile(self) -> ReconcileResult:
		self.load_persisted_state()
		pending = self._pending_receipt()
		if not pending:
			return ReconcileResult(action="none")
		receipt_global_no = pending.receipt.receipt_global_no
		status = self.get_status()
		if (status.last_receipt_global_no or 0) >= receipt_global_no:
			self._commit_prepared(pending, None)
			return ReconcileResult(action="confirmed", receipt_global_no=receipt_global_no)
		try:
			response = self.http.request(
				"POST", self.http.device_path("SubmitReceipt"), {"receipt": _receipt_to_wire(pending.receipt)}
			)
			self._commit_prepared(pending, response)
			return ReconcileResult(action="resubmitted", receipt_global_no=receipt_global_no, response=response)
		except FdmsApiError as error:
			self.storage.delete(KEY_PENDING)
			return ReconcileResult(action="rejected", receipt_global_no=receipt_global_no, error=error)

	# -- basic endpoints --------------------------------------------------

	def get_config(self) -> GetConfigResponse:
		data = self.http.request("GET", self.http.device_path("GetConfig"))
		self.config = _config_from_wire(data)
		return self.config

	def get_status(self) -> GetStatusResponse:
		data = self.http.request("GET", self.http.device_path("GetStatus"))
		return _status_from_wire(data)

	def ping(self) -> PingResponse:
		data = self.http.request("POST", self.http.device_path("Ping"))
		return PingResponse(operation_id=data["operationID"], reporting_frequency=data.get("reportingFrequency"))

	def renew_certificate(self, private_key) -> dict:
		from .csr import build_csr, device_common_name

		common_name = device_common_name(self.device.serial_number, self.device.device_id)
		csr_pem = build_csr(private_key, common_name)
		data = self.http.request(
			"POST", self.http.device_path("IssueCertificate"), {"certificateRequest": csr_pem}
		)
		return {"csrPem": csr_pem, "certificatePem": data["certificate"], "operationId": data["operationID"]}

	# -- fiscal day ---------------------------------------------------------

	def open_day(
		self,
		fiscal_day_no: Optional[int] = None,
		opened: Optional[datetime] = None,
		last_receipt_global_no: Optional[int] = None,
	):
		self._refuse_pending()
		status = self.get_status()  # side effect: teaches the clock the server offset
		opened = opened or self.clock.now()
		if status.fiscal_day_status != "FiscalDayClosed":
			raise ValueError(f"Cannot open a fiscal day while status is {status.fiscal_day_status}")

		data = self.http.request(
			"POST",
			self.http.device_path("OpenDay"),
			{"fiscalDayNo": fiscal_day_no, "fiscalDayOpened": fdms_date_time(opened)},
		)

		last_global = max(
			status.last_receipt_global_no or 0,
			last_receipt_global_no or 0,
			self._last_issued_global_no() or 0,
		)
		self.state = FiscalDayState(
			fiscal_day_no=data["fiscalDayNo"],
			fiscal_day_date=fdms_date(opened),
			receipt_counter=0,
			receipt_global_no=last_global,
			counters=[],
		)
		self._persist_state()
		return {"operationId": data["operationID"], "fiscalDayNo": data["fiscalDayNo"]}

	def close_day(self, force: bool = False) -> dict:
		self._refuse_pending()
		s = self._require_day()
		if not force and s.red_errors:
			raise DayNotClosableError(s.fiscal_day_no, s.red_errors)

		canonical = fiscal_day_signing_string(self.device.device_id, s.fiscal_day_no, s.fiscal_day_date, s.counters)
		signature = sign_canonical_string(self.signer, canonical, self.options.signature_format)

		counters = [c for c in s.counters if to_cents(c.fiscal_counter_value) != 0]
		body = {
			"fiscalDayNo": s.fiscal_day_no,
			"fiscalDayCounters": [_counter_to_wire(c) for c in counters],
			"fiscalDayDeviceSignature": {"hash": signature.hash, "signature": signature.signature},
			"receiptCounter": s.receipt_counter,
		}
		data = self.http.request("POST", self.http.device_path("CloseDay"), body)
		# Persisted state (counters+date) is deliberately kept - CloseDay
		# completes asynchronously server-side; clear_persisted_state() is
		# called separately once GetStatus reports FiscalDayClosed.
		self.state = None
		return {"operationId": data["operationID"]}

	# -- receipts -------------------------------------------------------

	def sign_receipt(self, receipt_input) -> PreparedReceipt:
		s = self._require_day()
		receipt_type = receipt_input.receipt_type or "FiscalInvoice"
		inclusive = receipt_input.lines_tax_inclusive if receipt_input.lines_tax_inclusive is not None else True

		date = receipt_input.receipt_date or self.clock.now()
		if receipt_input.receipt_date is None and s.previous_receipt_date:
			if fdms_date_time(date) <= s.previous_receipt_date:
				date = _parse_fdms_datetime(s.previous_receipt_date) + timedelta(seconds=1)

		lines: list[ReceiptLine] = []
		for i, line_input in enumerate(receipt_input.lines):
			price_cents = amount_to_cents(line_input.price, f"lines[{i}].price")
			total_cents = round(price_cents * line_input.quantity)
			# receiptLineType is not part of the canonical signing string
			# (only totals/taxes feed that), so this is a free display/
			# reporting distinction: a negative-total line - an item-level
			# discount that already nets out in the price, or a synthesised
			# document-level discount line from zimra.receipt_builder - is
			# reported as "Discount" rather than "Sale".
			line_type = "Discount" if total_cents < 0 else "Sale"
			lines.append(
				ReceiptLine(
					receipt_line_type=line_type,
					receipt_line_no=i + 1,
					receipt_line_hs_code=line_input.hs_code or None,
					receipt_line_name=line_input.name,
					receipt_line_price=cents_to_amount(price_cents),
					receipt_line_quantity=line_input.quantity,
					receipt_line_total=cents_to_amount(total_cents),
					tax_code=line_input.tax_code or None,
					tax_percent=line_input.tax_percent,
					tax_id=line_input.tax_id,
				)
			)

		taxes = _build_receipt_taxes(lines, inclusive)

		lines_total_cents = sum(to_cents(l.receipt_line_total) for l in lines)
		receipt_total_cents = lines_total_cents + (
			0 if inclusive else sum(to_cents(t.tax_amount) for t in taxes)
		)
		receipt_total = cents_to_amount(receipt_total_cents)

		payments = []
		payments_total_cents = 0
		for i, p in enumerate(receipt_input.payments):
			p_cents = amount_to_cents(p.amount, f"payments[{i}].amount")
			payments_total_cents += p_cents
			payments.append(Payment(money_type_code=p.money_type, payment_amount=cents_to_amount(p_cents)))

		if payments_total_cents != receipt_total_cents:
			raise ValueError(
				f"Payments ({cents_to_amount(payments_total_cents)}) do not equal receipt total ({receipt_total})"
			)

		unsigned = Receipt(
			receipt_type=receipt_type,
			receipt_currency=receipt_input.currency,
			receipt_counter=s.receipt_counter + 1,
			receipt_global_no=s.receipt_global_no + 1,
			invoice_no=receipt_input.invoice_no,
			buyer_data=receipt_input.buyer or None,
			receipt_notes=receipt_input.notes or None,
			receipt_date=fdms_date_time(date),
			credit_debit_note=receipt_input.credit_debit_note or None,
			receipt_lines_tax_inclusive=inclusive,
			receipt_lines=lines,
			receipt_taxes=taxes,
			receipt_payments=payments,
			receipt_total=receipt_total,
			receipt_print_form="Receipt48",
		)

		canonical = receipt_signing_string(self.device.device_id, unsigned, s.previous_receipt_hash)
		signature = sign_canonical_string(self.signer, canonical, self.options.signature_format)
		unsigned.receipt_device_signature = signature

		state_after = copy.deepcopy(s)
		state_after.receipt_counter += 1
		state_after.receipt_global_no += 1
		state_after.previous_receipt_hash = signature.hash
		state_after.previous_receipt_date = fdms_date_time(date)
		_accumulate_counters(state_after.counters, unsigned)

		return PreparedReceipt(receipt=unsigned, date=date.isoformat(), state_after=state_after)

	def apply_prepared(self, prepared: PreparedReceipt) -> None:
		s = self._require_day()
		if prepared.receipt.receipt_global_no != s.receipt_global_no + 1:
			raise ValueError(
				f"applyPrepared sequence mismatch: expected {s.receipt_global_no + 1}, "
				f"got {prepared.receipt.receipt_global_no}"
			)
		self.state = copy.deepcopy(prepared.state_after)
		self._persist_state()

	def submit_prepared(self, prepared: PreparedReceipt) -> SubmittedReceipt:
		self._refuse_pending()
		self.storage.set(KEY_PENDING, json.dumps(prepared_to_dict(prepared)))
		try:
			response = self.http.request(
				"POST", self.http.device_path("SubmitReceipt"), {"receipt": _receipt_to_wire(prepared.receipt)}
			)
		except FdmsApiError:
			self.storage.delete(KEY_PENDING)
			raise
		self._commit_prepared(prepared, response)
		return SubmittedReceipt(receipt=prepared.receipt, response=response, qr_data=self.qr_data(prepared))

	def submit_receipt(self, receipt_input) -> SubmittedReceipt:
		self._refuse_pending()
		return self.submit_prepared(self.sign_receipt(receipt_input))

	def _commit_prepared(self, prepared: PreparedReceipt, response: Optional[dict]) -> None:
		if self.state is None or self.state.receipt_global_no < prepared.receipt.receipt_global_no:
			self.state = copy.deepcopy(prepared.state_after)
		for v in (response or {}).get("validationErrors") or []:
			colour = (v.get("validationErrorColor") or "").lower()
			if colour == "red":
				self.state.red_errors.append(
					RedValidationError(
						receipt_global_no=prepared.receipt.receipt_global_no,
						receipt_counter=prepared.receipt.receipt_counter,
						code=v.get("validationErrorCode") or "?",
						description=v.get("validationErrorDescription"),
					)
				)
		self._persist_state()
		self.storage.delete(KEY_PENDING)

	def qr_data(self, prepared: PreparedReceipt) -> Optional[str]:
		if not self.config or not self.config.qr_url:
			return None
		return receipt_qr_data(
			self.config.qr_url,
			self.device.device_id,
			datetime.fromisoformat(prepared.date),
			prepared.receipt.receipt_global_no,
			prepared.receipt.receipt_device_signature.signature,
		)


# -- pure helpers, module-level so tests can exercise them directly --------


def _build_receipt_taxes(lines: list[ReceiptLine], tax_inclusive: bool) -> list[ReceiptTax]:
	groups: dict[tuple, dict] = {}
	order: list[tuple] = []
	for line in lines:
		key = (line.tax_id, line.tax_percent)
		if key not in groups:
			groups[key] = {"tax_code": line.tax_code, "tax_percent": line.tax_percent, "tax_id": line.tax_id, "tax_cents": 0, "sales_cents": 0}
			order.append(key)
		g = groups[key]
		total_cents = to_cents(line.receipt_line_total)
		rate = (line.tax_percent or 0) / 100
		if tax_inclusive:
			tax_cents = round(total_cents - total_cents / (1 + rate)) if rate else 0
			sales_delta = total_cents
		else:
			tax_cents = round(total_cents * rate)
			sales_delta = total_cents + tax_cents
		g["tax_cents"] += tax_cents
		g["sales_cents"] += sales_delta

	result = [
		ReceiptTax(
			tax_code=groups[key]["tax_code"],
			tax_percent=groups[key]["tax_percent"],
			tax_id=groups[key]["tax_id"],
			tax_amount=cents_to_amount(groups[key]["tax_cents"]),
			sales_amount_with_tax=cents_to_amount(groups[key]["sales_cents"]),
		)
		for key in order
	]
	result.sort(key=lambda t: t.tax_id)
	return result


def _accumulate_counters(counters: list[FiscalDayCounter], receipt: Receipt) -> None:
	sign = -1 if receipt.receipt_type == "CreditNote" else 1
	sale_type, tax_type = _SALE_TYPE_BY_RECEIPT_TYPE[receipt.receipt_type]

	def upsert(match, create, delta):
		for c in counters:
			if match(c):
				c.fiscal_counter_value = cents_to_amount(to_cents(c.fiscal_counter_value) + to_cents(delta))
				return
		new_counter = create()
		new_counter.fiscal_counter_value = cents_to_amount(to_cents(delta))
		counters.append(new_counter)

	for t in receipt.receipt_taxes:
		upsert(
			lambda c, t=t: c.fiscal_counter_type == sale_type
			and c.fiscal_counter_currency == receipt.receipt_currency
			and c.fiscal_counter_tax_id == t.tax_id,
			lambda t=t: FiscalDayCounter(
				fiscal_counter_type=sale_type,
				fiscal_counter_currency=receipt.receipt_currency,
				fiscal_counter_tax_id=t.tax_id,
				fiscal_counter_tax_percent=t.tax_percent,
				fiscal_counter_value=0,
			),
			sign * t.sales_amount_with_tax,
		)
		upsert(
			lambda c, t=t: c.fiscal_counter_type == tax_type
			and c.fiscal_counter_currency == receipt.receipt_currency
			and c.fiscal_counter_tax_id == t.tax_id,
			lambda t=t: FiscalDayCounter(
				fiscal_counter_type=tax_type,
				fiscal_counter_currency=receipt.receipt_currency,
				fiscal_counter_tax_id=t.tax_id,
				fiscal_counter_tax_percent=t.tax_percent,
				fiscal_counter_value=0,
			),
			sign * t.tax_amount,
		)

	for p in receipt.receipt_payments:
		upsert(
			lambda c, p=p: c.fiscal_counter_type == "BalanceByMoneyType"
			and c.fiscal_counter_currency == receipt.receipt_currency
			and c.fiscal_counter_money_type == p.money_type_code,
			lambda p=p: FiscalDayCounter(
				fiscal_counter_type="BalanceByMoneyType",
				fiscal_counter_currency=receipt.receipt_currency,
				fiscal_counter_money_type=p.money_type_code,
				fiscal_counter_value=0,
			),
			sign * p.payment_amount,
		)


def _counter_to_wire(c: FiscalDayCounter) -> dict:
	d = {
		"fiscalCounterType": c.fiscal_counter_type,
		"fiscalCounterCurrency": c.fiscal_counter_currency,
		"fiscalCounterValue": c.fiscal_counter_value,
	}
	if c.fiscal_counter_tax_percent is not None:
		d["fiscalCounterTaxPercent"] = c.fiscal_counter_tax_percent
	if c.fiscal_counter_tax_id is not None:
		d["fiscalCounterTaxID"] = c.fiscal_counter_tax_id
	if c.fiscal_counter_money_type is not None:
		d["fiscalCounterMoneyType"] = c.fiscal_counter_money_type
	return d


def _receipt_to_wire(r: Receipt) -> dict:
	d = {
		"receiptType": r.receipt_type,
		"receiptCurrency": r.receipt_currency,
		"receiptCounter": r.receipt_counter,
		"receiptGlobalNo": r.receipt_global_no,
		"invoiceNo": r.invoice_no,
		"receiptDate": r.receipt_date,
		"receiptLinesTaxInclusive": r.receipt_lines_tax_inclusive,
		"receiptLines": [
			{
				"receiptLineType": l.receipt_line_type,
				"receiptLineNo": l.receipt_line_no,
				"receiptLineHSCode": l.receipt_line_hs_code,
				"receiptLineName": l.receipt_line_name,
				"receiptLinePrice": l.receipt_line_price,
				"receiptLineQuantity": l.receipt_line_quantity,
				"receiptLineTotal": l.receipt_line_total,
				"taxCode": l.tax_code,
				"taxPercent": l.tax_percent,
				"taxID": l.tax_id,
			}
			for l in r.receipt_lines
		],
		"receiptTaxes": [
			{
				"taxCode": t.tax_code,
				"taxPercent": t.tax_percent,
				"taxID": t.tax_id,
				"taxAmount": t.tax_amount,
				"salesAmountWithTax": t.sales_amount_with_tax,
			}
			for t in r.receipt_taxes
		],
		"receiptPayments": [
			{"moneyTypeCode": p.money_type_code, "paymentAmount": p.payment_amount} for p in r.receipt_payments
		],
		"receiptTotal": r.receipt_total,
		"receiptDeviceSignature": {
			"hash": r.receipt_device_signature.hash,
			"signature": r.receipt_device_signature.signature,
		},
	}
	if r.buyer_data:
		d["buyerData"] = {k: v for k, v in vars(r.buyer_data).items() if v is not None}
	if r.receipt_notes:
		d["receiptNotes"] = r.receipt_notes
	if r.credit_debit_note:
		d["creditDebitNote"] = {k: v for k, v in vars(r.credit_debit_note).items() if v is not None}
	if r.receipt_print_form:
		d["receiptPrintForm"] = r.receipt_print_form
	return d


def _config_from_wire(data: dict) -> GetConfigResponse:
	from .types import TaxDefinition

	return GetConfigResponse(
		operation_id=data["operationID"],
		tax_payer_name=data["taxPayerName"],
		tax_payer_tin=data["taxPayerTIN"],
		vat_number=data.get("vatNumber"),
		device_serial_no=data.get("deviceSerialNo"),
		device_branch_name=data.get("deviceBranchName"),
		device_branch_address=data.get("deviceBranchAddress"),
		device_branch_contacts=data.get("deviceBranchContacts"),
		device_operating_mode=data.get("deviceOperatingMode"),
		tax_payer_day_max_hrs=data.get("taxPayerDayMaxHrs"),
		applicable_taxes=[
			TaxDefinition(
				tax_id=t["taxID"],
				tax_name=t["taxName"],
				tax_percent=t.get("taxPercent"),
				valid_from=t.get("validFrom"),
				valid_till=t.get("validTill"),
			)
			for t in data.get("applicableTaxes", [])
		],
		certificate_valid_till=data.get("certificateValidTill"),
		qr_url=data.get("qrUrl"),
		taxpayer_day_end_notification_hrs=data.get("taxpayerDayEndNotificationHrs"),
	)


def _status_from_wire(data: dict) -> GetStatusResponse:
	return GetStatusResponse(
		operation_id=data["operationID"],
		fiscal_day_status=data["fiscalDayStatus"],
		fiscal_day_reconciliation_mode=data.get("fiscalDayReconciliationMode"),
		fiscal_day_server_signature=data.get("fiscalDayServerSignature"),
		fiscal_day_closed=data.get("fiscalDayClosed"),
		fiscal_day_counter=data.get("fiscalDayCounter"),
		last_receipt_global_no=data.get("lastReceiptGlobalNo"),
		last_fiscal_day_no=data.get("lastFiscalDayNo"),
		fiscal_day_closing_error_code=data.get("fiscalDayClosingErrorCode"),
		fiscal_day_document_quantities=data.get("fiscalDayDocumentQuantities"),
	)
