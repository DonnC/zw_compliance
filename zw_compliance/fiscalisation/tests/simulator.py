"""An in-process FDMS for integration tests.

A Python port of the zimra-fdms SDK's simulator
(``src/simulator/server.ts`` and ``ca.ts``, MIT licence), which replays
behaviour observed against ZIMRA's test environment. Kept from it:

* a real certificate authority: device certificates are issued from the CSR
  the app sends, and every device request is authenticated by that
  certificate (issuer and ``ZIMRA-<serial>-<device id>`` CN), as mutual TLS does;
* the same fault model, consumed as faults fire: ``drop_connections``,
  ``server_errors`` (HTTP 503), ``date_skew_ms`` and ``drop_after_submit``
  (FDMS accepts the receipt but the answer is lost);
* the GetStatus quirk: ``lastReceiptGlobalNo`` is the receipt with the
  *latest date*, not the highest number;
* the asynchronous CloseDay: accepted now, settled later (here by
  :meth:`FakeFdms.settle`, the analogue of the simulator's timer).

Aligned with the published specification v7.2 where the SDK simulator differs:

* validation codes and colours follow spec §8.2.1 (the simulator uses earlier
  numbering, e.g. RCPT010 for signatures, where the spec has RCPT020);
* credit-note amounts are negative and counters add them as they are (§6);
  the SDK simulator negates them again, so a spec-correct credit note fails its
  day close with BadCertificateSignature;
* a gap in the sequence is Grey (previous receipt missing, §8.2.1) and a
  re-sent receipt with the same globalNo and hash returns the original
  receiptID (§4.7);
* VerifyTaxpayerInformation (§4.1), closeDay refusal while Grey/Red receipts
  exist (§4.11) and FDMS countersignatures in the §13.2.2 / §13.3.2 format.

Checks are written independently of ``zw_compliance.fdms.receipt`` so the
fake is an oracle for the app, not a mirror of it.

Portions derived from zimra-fdms, Copyright (c) 2026 Shaun Chivandire,
used under the MIT licence (https://github.com/munashe-chivandire/zimra-fdms).
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from email.utils import format_datetime
from urllib.parse import urlparse

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from zw_compliance.fdms.client import TransportRequest, TransportResponse
from zw_compliance.fdms.errors import TransportError

CENT = Decimal("0.01")
MONEY_TYPES = ("Cash", "Card", "MobileWallet", "Coupon", "Credit", "BankTransfer", "Other")
COUNTER_ORDER = (
	"SaleByTax",
	"SaleTaxByTax",
	"CreditNoteByTax",
	"CreditNoteTaxByTax",
	"DebitNoteByTax",
	"DebitNoteTaxByTax",
	"BalanceByMoneyType",
)


def _b64sha256(text: str) -> str:
	return base64.b64encode(hashlib.sha256(text.encode()).digest()).decode()


def _cents(value) -> int:
	return int((Decimal(str(value)) * 100).to_integral_value())


def _pct(value) -> str:
	return "" if value is None else f"{Decimal(str(value)):.2f}"


class _HttpError(Exception):
	def __init__(self, status: int, code: str, detail: str):
		super().__init__(detail)
		self.status, self.code = status, code


# -- certificate authority (ca.ts) ---------------------------------------------


class SimulatorCa:
	def __init__(self):
		self.key = ec.generate_private_key(ec.SECP256R1())
		name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "zw_compliance FDMS simulator CA")])
		now = datetime.utcnow() - timedelta(minutes=1)
		self.certificate = (
			x509.CertificateBuilder()
			.subject_name(name)
			.issuer_name(name)
			.public_key(self.key.public_key())
			.serial_number(x509.random_serial_number())
			.not_valid_before(now)
			.not_valid_after(now + timedelta(days=3650))
			.add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
			.sign(self.key, hashes.SHA256())
		)

	@property
	def pem(self) -> str:
		return self.certificate.public_bytes(serialization.Encoding.PEM).decode()

	@property
	def thumbprint(self) -> str:
		"""SHA-1 hex, upper case, as FDMS reports it."""
		return hashlib.sha1(self.certificate.public_bytes(serialization.Encoding.DER)).hexdigest().upper()

	def issue_from_csr(self, csr_pem: str, days: int = 365) -> tuple[str, str]:
		"""Issue a device certificate; reject a CSR whose signature does not verify."""
		csr = x509.load_pem_x509_csr(csr_pem.encode())
		if not csr.is_signature_valid:
			raise ValueError("CSR signature does not verify")
		cn = csr.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
		if not cn:
			raise ValueError("CSR has no CN")
		now = datetime.utcnow() - timedelta(minutes=1)
		cert = (
			x509.CertificateBuilder()
			.subject_name(csr.subject)
			.issuer_name(self.certificate.subject)
			.public_key(csr.public_key())
			.serial_number(x509.random_serial_number())
			.not_valid_before(now)
			.not_valid_after(now + timedelta(days=days))
			.add_extension(x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
			.sign(self.key, hashes.SHA256())
		)
		return cert.public_bytes(serialization.Encoding.PEM).decode(), cn[0].value

	def sign(self, text: str) -> dict:
		sig = self.key.sign(text.encode(), ec.ECDSA(hashes.SHA256()))
		return {
			"hash": _b64sha256(text),
			"signature": base64.b64encode(sig).decode(),
			"certificateThumbprint": self.thumbprint,
		}

	def authenticate(self, certificate_pem: str | None):
		"""What the TLS layer establishes: a certificate this CA issued. Returns it."""
		if not certificate_pem:
			raise _HttpError(401, "Unauthorized", "No client certificate")
		cert = x509.load_pem_x509_certificate(certificate_pem.encode())
		try:
			self.key.public_key().verify(
				cert.signature, cert.tbs_certificate_bytes, ec.ECDSA(cert.signature_hash_algorithm)
			)
		except InvalidSignature as e:
			raise _HttpError(401, "Unauthorized", "Client certificate not issued by FDMS") from e
		expiry = getattr(cert, "not_valid_after_utc", None)
		if (expiry.replace(tzinfo=None) if expiry else cert.not_valid_after) < datetime.utcnow():
			raise _HttpError(401, "Unauthorized", "Client certificate expired")
		return cert


# -- state ---------------------------------------------------------------------


@dataclass
class Faults:
	drop_connections: int = 0
	server_errors: int = 0
	date_skew_ms: int = 0
	drop_after_submit: int = 0
	# Refuse the next close with this error code, reported the spec way
	# (FiscalDayCloseFailed) or, with refuse_close_as_opened, the zimra-fdms
	# simulator way (FiscalDayOpened plus a closing error code).
	refuse_close: str | None = None
	refuse_close_as_opened: bool = False


@dataclass
class SimReceipt:
	receipt: dict
	receipt_id: int
	errors: list[dict]
	server_date: str


@dataclass
class SimDay:
	fiscal_day_no: int
	opened: str
	receipts: list[SimReceipt] = field(default_factory=list)
	counters: dict = field(default_factory=dict)
	closed: str | None = None
	device_signature: str | None = None
	server_signature: dict | None = None


@dataclass
class SimDevice:
	device_id: int
	serial_no: str
	certificate_pem: str | None = None
	status: str = "FiscalDayClosed"
	closing_error_code: str | None = None
	days: list[SimDay] = field(default_factory=list)
	pending_close: dict | None = None

	@property
	def common_name(self) -> str:
		return f"ZIMRA-{self.serial_no}-{self.device_id:010d}"


class _Transport:
	def __init__(self, fdms: FakeFdms, certificate_pem: str | None):
		self.fdms = fdms
		self.certificate_pem = certificate_pem

	def request(self, req: TransportRequest) -> TransportResponse:
		return self.fdms.handle(req, self.certificate_pem)


class FakeFdms:
	def __init__(
		self,
		*,
		taxes: list[dict] | None = None,
		vat_number: str | None = "220000000",
		day_max_hours: int = 24,
		day_end_notification_hours: int = 2,
		future_tolerance: timedelta = timedelta(minutes=5),
		activation_keys: list[str] | None = None,
		qr_url: str = "https://fdmstest.zimra.co.zw",
		taxpayer_name: str = "Simulated Taxpayer",
		taxpayer_tin: str = "2000000000",
		branch_name: str = "Simulator Branch",
		branch_address: dict | None = None,
		branch_contacts: dict | None = None,
	):
		self.taxpayer_name, self.taxpayer_tin, self.branch_name = taxpayer_name, taxpayer_tin, branch_name
		self.branch_address = branch_address or {
			"province": "Harare",
			"city": "Harare",
			"street": "Samora Machel Ave",
			"houseNo": "1",
		}
		self.branch_contacts = branch_contacts or {"phoneNo": "0242000000", "email": "sim@example.invalid"}
		self.ca = SimulatorCa()
		self.devices: dict[int, SimDevice] = {}
		self.faults = Faults()
		self.clock = datetime.now  # local wall clock; the demo seeder replays past days
		self.log: list[dict] = []
		self.taxes = taxes or [
			{"taxID": 1, "taxName": "Standard rated 15%", "taxPercent": 15},
			{"taxID": 2, "taxName": "Zero rated 0%", "taxPercent": 0},
			{"taxID": 3, "taxName": "Exempt", "taxPercent": None},
		]
		self.vat_number = vat_number
		self.day_max_hours = day_max_hours
		self.day_end_notification_hours = day_end_notification_hours
		self.future_tolerance = future_tolerance
		self.activation_keys = activation_keys
		self.qr_url = qr_url
		self._op = 0
		self._receipt_ids = 1000

	def transport(self, certificate_pem: str | None = None) -> _Transport:
		return _Transport(self, certificate_pem)

	def factory(self):
		"""``frappe.flags.zimra_transport_factory`` for a Zimra Fiscal Device."""
		return lambda device, mtls: self.transport(device.certificate if mtls else None)

	def all_receipts(self, device_id: int) -> list[SimReceipt]:
		return [r for d in self.devices[device_id].days for r in d.receipts]

	# -- plumbing -------------------------------------------------------------

	def handle(self, req: TransportRequest, certificate_pem: str | None) -> TransportResponse:
		path = urlparse(req.url).path
		entry = {"method": req.method, "path": path, "status": 0}
		self.log.append(entry)
		if self.faults.drop_connections > 0:
			self.faults.drop_connections -= 1
			raise TransportError("connection reset (simulated)")
		self._op += 1
		operation_id = f"SIM{self._op:06d}"
		headers = {
			"content-type": "application/json",
			"date": format_datetime(
				datetime.now(timezone.utc) + timedelta(milliseconds=self.faults.date_skew_ms), usegmt=True
			),
			"operationid": operation_id,
		}

		def respond(status, body):
			entry["status"] = status
			return TransportResponse(status, headers, json.dumps(body) if body is not None else "")

		if self.faults.server_errors > 0:
			self.faults.server_errors -= 1
			return respond(503, {"title": "Service Unavailable", "status": 503})
		try:
			body = json.loads(req.body) if req.body else None
			out, drop = self._route(req.method, path, body, certificate_pem, operation_id)
		except _HttpError as e:
			return respond(
				e.status, {"title": e.code, "status": e.status, "errorCode": e.code, "detail": str(e)}
			)
		if drop:
			raise TransportError("connection reset after the request was processed (simulated)")
		return respond(200, out)

	def _route(self, method, path, body, certificate_pem, op):
		parts = [p for p in path.split("/") if p]
		if parts[:2] == ["Public", "v1"]:
			if parts[2:3] == ["GetServerCertificate"] and method == "GET":
				return {"certificate": [self.ca.pem], "certificateValidTill": "2036-01-01T00:00:00"}, False
			device_id, endpoint = int(parts[2]), parts[3]
			if endpoint == "VerifyTaxpayerInformation" and method == "POST":
				return self._verify_taxpayer(device_id, body, op), False
			if endpoint == "RegisterDevice" and method == "POST":
				return self._register(device_id, body, op), False
			raise _HttpError(404, "NotFound", path)
		if parts[:2] != ["Device", "v1"]:
			raise _HttpError(404, "NotFound", path)
		device_id, endpoint = int(parts[2]), parts[3]
		device = self.devices.get(device_id)
		if not device:
			raise _HttpError(422, "DEV01", f"Device {device_id} is not registered")
		cert = self.ca.authenticate(certificate_pem)
		cn = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
		if cn != device.common_name:
			raise _HttpError(401, "Unauthorized", f"Certificate CN {cn} does not match device {device_id}")
		public_key = cert.public_key()
		handler = {
			("GET", "GetConfig"): lambda: self._config(device, op),
			("GET", "GetStatus"): lambda: self._status(device, op),
			("POST", "Ping"): lambda: {"operationID": op, "reportingFrequency": 5},
			("POST", "OpenDay"): lambda: self._open_day(device, body, op),
			("POST", "SubmitReceipt"): lambda: self._submit(device, body["receipt"], public_key, op),
			("POST", "CloseDay"): lambda: self._close_day(device, body, op),
			("POST", "IssueCertificate"): lambda: self._issue(device, body, op),
		}.get((method, endpoint))
		if not handler:
			raise _HttpError(404, "NotFound", path)
		out = handler()
		if endpoint == "SubmitReceipt" and self.faults.drop_after_submit > 0:
			self.faults.drop_after_submit -= 1
			return out, True
		return out, False

	# -- public endpoints -------------------------------------------------------

	def _check_key(self, key: str):
		ok = (
			any(k.lower() == key.lower() for k in self.activation_keys)
			if self.activation_keys
			else len(key) == 8
		)
		if not ok:
			raise _HttpError(422, "DEV02", "Activation key is incorrect")

	def _branch(self):
		return {
			"taxPayerName": self.taxpayer_name,
			"taxPayerTIN": self.taxpayer_tin,
			**({"vatNumber": self.vat_number} if self.vat_number else {}),
			"deviceBranchName": self.branch_name,
			"deviceBranchAddress": self.branch_address,
			"deviceBranchContacts": self.branch_contacts,
		}

	def _verify_taxpayer(self, device_id, body, op):
		self._check_key(body.get("activationKey", ""))
		return {"operationID": op, **self._branch()}

	def _register(self, device_id, body, op):
		self._check_key(body.get("activationKey", ""))
		try:
			cert, cn = self.ca.issue_from_csr(body["certificateRequest"])
		except ValueError as e:
			raise _HttpError(422, "DEV03", str(e)) from e
		prefix, _, number = cn.rpartition("-")
		if (
			not prefix.startswith("ZIMRA-")
			or not number.isdigit()
			or int(number) != device_id
			or len(number) != 10
		):
			raise _HttpError(422, "DEV03", f"CSR CN {cn} is not ZIMRA-<serial>-{device_id:010d}")
		device = self.devices.get(device_id) or SimDevice(device_id, prefix[len("ZIMRA-") :])
		device.certificate_pem = cert
		self.devices[device_id] = device
		return {"operationID": op, "certificate": cert}

	# -- device endpoints ---------------------------------------------------------

	def _issue(self, device, body, op):
		cert, _cn = self.ca.issue_from_csr(body["certificateRequest"])
		device.certificate_pem = cert
		return {"operationID": op, "certificate": cert}

	def _config(self, device, op):
		return {
			"operationID": op,
			**self._branch(),
			"deviceSerialNo": device.serial_no,
			"deviceOperatingMode": "Online",
			"taxPayerDayMaxHrs": self.day_max_hours,
			"taxpayerDayEndNotificationHrs": self.day_end_notification_hours,
			"applicableTaxes": [{"validFrom": "2024-01-01T00:00:00", **t} for t in self.taxes],
			"certificateValidTill": (datetime.utcnow() + timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%S"),
			"qrUrl": self.qr_url,
		}

	def _status(self, device, op):
		receipts = [r for d in device.days for r in d.receipts]
		latest = max(receipts, key=lambda r: r.receipt["receiptDate"], default=None)  # the observed quirk
		day = device.days[-1] if device.days else None
		out = {
			"operationID": op,
			"fiscalDayStatus": device.status,
			"lastReceiptGlobalNo": latest.receipt["receiptGlobalNo"] if latest else None,
			"lastFiscalDayNo": day.fiscal_day_no if day else None,
			"fiscalDayClosingErrorCode": device.closing_error_code,
		}
		if day and device.status == "FiscalDayClosed" and day.closed:
			out.update(
				{
					"fiscalDayClosed": day.closed,
					"fiscalDayReconciliationMode": "Auto",
					"fiscalDayServerSignature": day.server_signature,
				}
			)
		return {k: v for k, v in out.items() if v is not None}

	def _open_day(self, device, body, op):
		if device.status != "FiscalDayClosed":
			raise _HttpError(422, "FISC01", "Open day is not allowed")
		last = device.days[-1].fiscal_day_no if device.days else 0
		number = body.get("fiscalDayNo") or last + 1
		if number != last + 1:
			raise _HttpError(422, "FISC01", f"fiscalDayNo must be {last + 1}")
		device.days.append(SimDay(number, body["fiscalDayOpened"]))
		device.status = "FiscalDayOpened"
		device.closing_error_code = None
		return {"operationID": op, "fiscalDayNo": number}

	def _submit(self, device, r, public_key, op):
		if device.status not in ("FiscalDayOpened", "FiscalDayCloseFailed"):
			raise _HttpError(422, "RCPT01", "Fiscal day is closed or close initiated")
		for field_name in (
			"receiptType",
			"receiptCurrency",
			"receiptCounter",
			"receiptGlobalNo",
			"invoiceNo",
			"receiptDate",
			"receiptLines",
			"receiptTaxes",
			"receiptPayments",
			"receiptTotal",
			"receiptDeviceSignature",
		):
			if field_name not in r:
				raise _HttpError(422, "RCPT02", f"{field_name} is missing")
		day = device.days[-1]
		all_receipts = [x for d in device.days for x in d.receipts]
		for x in all_receipts:  # §4.7: same globalNo and hash = same receipt
			if (
				x.receipt["receiptGlobalNo"] == r["receiptGlobalNo"]
				and x.receipt["receiptDeviceSignature"]["hash"] == r["receiptDeviceSignature"]["hash"]
			):
				return self._answer(x, op)

		errors = []

		def err(code, colour, text):
			errors.append(
				{
					"validationErrorCode": code,
					"validationErrorColor": colour,
					"validationErrorDescription": text,
				}
			)

		previous = day.receipts[-1] if day.receipts else None
		# The global number follows the device's previous receipt, or starts again
		# at 1 with the first receipt of a fiscal day.
		highest = all_receipts[-1].receipt["receiptGlobalNo"] if all_receipts else 0
		if r["receiptCounter"] != len(day.receipts) + 1:
			err(
				"RCPT011",
				"Grey" if r["receiptCounter"] > len(day.receipts) + 1 else "Red",
				"Receipt counter is not sequential.",
			)
		if r["receiptGlobalNo"] != highest + 1 and not (r["receiptGlobalNo"] == 1 and not day.receipts):
			err(
				"RCPT012",
				"Grey" if r["receiptGlobalNo"] > highest + 1 else "Red",
				"Receipt global number is not sequential.",
			)
		if previous and r["receiptDate"] <= previous.receipt["receiptDate"]:
			err("RCPT030", "Red", "Invoice date is earlier than previously submitted receipt date")
		if r["receiptDate"] < day.opened:
			err("RCPT014", "Yellow", "Receipt date is earlier than fiscal day opening date")
		if r["receiptDate"] > (self.clock() + self.future_tolerance).strftime("%Y-%m-%dT%H:%M:%S"):
			err("RCPT031", "Yellow", "Invoice is submitted with the future date")
		if r["receiptDate"] > (
			datetime.fromisoformat(day.opened) + timedelta(hours=self.day_max_hours)
		).strftime("%Y-%m-%dT%H:%M:%S"):
			err("RCPT041", "Yellow", "Invoice is issued after fiscal day end")

		# §13.2.1 signature over the canonical string, chained to the previous receipt.
		taxes = sorted(r["receiptTaxes"], key=lambda t: (t["taxID"], t.get("taxCode") or ""))
		canonical = (
			f"{device.device_id}{r['receiptType'].upper()}{r['receiptCurrency'].upper()}{r['receiptGlobalNo']}{r['receiptDate']}{_cents(r['receiptTotal'])}"
			+ "".join(
				f"{t.get('taxCode') or ''}{_pct(t.get('taxPercent'))}{_cents(t['taxAmount'])}{_cents(t['salesAmountWithTax'])}"
				for t in taxes
			)
			+ (previous.receipt["receiptDeviceSignature"]["hash"] if previous else "")
		)
		signature = r["receiptDeviceSignature"]
		valid = signature.get("hash") == _b64sha256(canonical)
		if valid:
			try:
				public_key.verify(
					base64.b64decode(signature["signature"]), canonical.encode(), ec.ECDSA(hashes.SHA256())
				)
			except InvalidSignature:
				valid = False
		if not valid:
			err("RCPT020", "Red", "Invoice signature is not valid")

		# Totals and taxes (§4.7 validation rules).
		lines, credit = r["receiptLines"], r["receiptType"] == "CreditNote"
		line_sum = sum(_cents(l["receiptLineTotal"]) for l in lines)
		if not lines:
			err("RCPT016", "Red", "No receipt lines provided")
		if not r["receiptTaxes"]:
			err("RCPT017", "Red", "Taxes information is not provided")
		if not r["receiptPayments"]:
			err("RCPT018", "Red", "Payment information is not provided")
		if r["receiptLinesTaxInclusive"] and _cents(r["receiptTotal"]) != line_sum:
			err("RCPT019", "Red", "Invoice total amount is not equal to sum of all invoice lines")
		if _cents(r["receiptTotal"]) != sum(_cents(t["salesAmountWithTax"]) for t in r["receiptTaxes"]):
			err(
				"RCPT038",
				"Red",
				"Invoice total amount is not equal to sum of sales amount including tax in tax table",
			)
		if _cents(r["receiptTotal"]) != sum(_cents(p["paymentAmount"]) for p in r["receiptPayments"]):
			err("RCPT039", "Red", "Invoice total amount is not equal to sum of all payment amounts")
		if (credit and _cents(r["receiptTotal"]) > 0) or (not credit and _cents(r["receiptTotal"]) < 0):
			err("RCPT040", "Red", "Invoice total amount has the wrong sign")
		for p in r["receiptPayments"]:
			if (credit and _cents(p["paymentAmount"]) > 0) or (not credit and _cents(p["paymentAmount"]) < 0):
				err("RCPT028", "Red", "Payment amount has the wrong sign")
		known = {t["taxID"]: t.get("taxPercent") for t in self.taxes}
		groups = {}
		for l in lines:
			if l["receiptLineQuantity"] <= 0:
				err("RCPT023", "Red", "Invoice line quantity, must be positive")
			if "receiptLinePrice" in l and _cents(
				Decimal(str(l["receiptLinePrice"])) * Decimal(str(l["receiptLineQuantity"]))
			) != _cents(l["receiptLineTotal"]):
				err("RCPT024", "Red", "Invoice line total is not equal to unit price * quantity")
			sale_ok = (l["receiptLineTotal"] < 0) if credit else (l["receiptLineTotal"] > 0)
			if l["receiptLineType"] == "Sale" and not sale_ok:
				err("RCPT022", "Red", "Invoice sales line price has the wrong sign")
			if l["taxID"] not in known or known[l["taxID"]] != l.get("taxPercent"):
				err("RCPT025", "Red", "Invalid tax is used")
			if self.vat_number is None and (l.get("taxPercent") or 0) > 0:
				err("RCPT021", "Red", "VAT tax is used in invoice while taxpayer is not VAT taxpayer")
			if self.vat_number:
				hs = l.get("receiptLineHSCode")
				if not hs:
					err("RCPT047", "Red", "HS code must be sent if taxpayer is a VAT payer")
				elif len(hs) not in ((8,) if not l.get("taxPercent") else (4, 8)):
					err("RCPT048", "Red", "HS code length is invalid")
			groups.setdefault((l["taxID"], l.get("taxPercent")), 0)
			groups[(l["taxID"], l.get("taxPercent"))] += _cents(l["receiptLineTotal"])
		for t in r["receiptTaxes"]:
			gross = groups.get((t["taxID"], t.get("taxPercent")), 0)
			p = Decimal(str(t.get("taxPercent") or 0))
			expected = (
				int((Decimal(gross) * p / (100 + p)).quantize(Decimal(1), rounding="ROUND_HALF_EVEN"))
				if p
				else 0
			)
			if _cents(t["taxAmount"]) != expected:
				err("RCPT026", "Red", "Incorrectly calculated tax amount")
			if _cents(t["salesAmountWithTax"]) != gross:
				err("RCPT027", "Red", "Incorrectly calculated total sales amount (including tax)")
		if r["receiptType"] in ("CreditNote", "DebitNote"):
			if not r.get("receiptNotes"):
				err("RCPT034", "Red", "Note for credit/debit note is not provided")
			ref = r.get("creditDebitNote")
			if not ref:
				err("RCPT015", "Red", "Credited/debited invoice data is not provided")
			else:
				original = next(
					(
						x
						for d in device.days
						for x in d.receipts
						if x.receipt_id == ref.get("receiptID")
						or (
							x.receipt["receiptGlobalNo"] == ref.get("receiptGlobalNo")
							and d.fiscal_day_no == ref.get("fiscalDayNo")
							and ref.get("deviceID") == device.device_id
						)
					),
					None,
				)
				if not original:
					err("RCPT032", "Red", "Credit / debit note refers to non-existing invoice")
				elif original.receipt["receiptCurrency"] != r["receiptCurrency"]:
					err(
						"RCPT042",
						"Red",
						"Credit/debit note uses other currency than is used in the original invoice",
					)
		elif r.get("creditDebitNote"):
			err("RCPT029", "Red", "Credited/debited invoice information provided for regular invoice")
		buyer = r.get("buyerData")
		if buyer and not (buyer.get("buyerRegisterName") and buyer.get("buyerTIN")):
			err("RCPT043", "Red", "Mandatory buyer data fields are not provided")

		self._receipt_ids += 1
		stored = SimReceipt(r, self._receipt_ids, errors, self.clock().strftime("%Y-%m-%dT%H:%M:%S"))
		day.receipts.append(stored)
		self._count(day, r)
		return self._answer(stored, op)

	def _answer(self, stored: SimReceipt, op: str) -> dict:
		# §13.2.2: receiptDeviceSignature || receiptID || serverDate
		canonical = (
			f"{stored.receipt['receiptDeviceSignature']['signature']}{stored.receipt_id}{stored.server_date}"
		)
		return {
			"operationID": op,
			"receiptID": stored.receipt_id,
			"serverDate": stored.server_date,
			"receiptServerSignature": self.ca.sign(canonical),
			"validationErrors": stored.errors,
		}

	def _count(self, day: SimDay, r: dict) -> None:
		"""§6: values are added as sent; credit notes are already negative."""
		by_tax, tax_by_tax = {
			"FiscalInvoice": ("SaleByTax", "SaleTaxByTax"),
			"CreditNote": ("CreditNoteByTax", "CreditNoteTaxByTax"),
			"DebitNote": ("DebitNoteByTax", "DebitNoteTaxByTax"),
		}[r["receiptType"]]
		cur = r["receiptCurrency"]
		for t in r["receiptTaxes"]:
			for kind, amount in ((by_tax, t["salesAmountWithTax"]), (tax_by_tax, t["taxAmount"])):
				key = (kind, cur, t["taxID"], t.get("taxPercent"), None)
				day.counters[key] = day.counters.get(key, 0) + _cents(amount)
		for p in r["receiptPayments"]:
			key = ("BalanceByMoneyType", cur, None, None, p["moneyTypeCode"])
			day.counters[key] = day.counters.get(key, 0) + _cents(p["paymentAmount"])

	def _counter_string(self, counters: dict) -> str:
		items = [(k, v) for k, v in counters.items() if v]
		items.sort(
			key=lambda kv: (
				COUNTER_ORDER.index(kv[0][0]),
				kv[0][1],
				kv[0][2] if kv[0][2] is not None else -1,
				MONEY_TYPES.index(kv[0][4]) if kv[0][4] else -1,
			)
		)
		return "".join(
			f"{k[0].upper()}{k[1].upper()}{k[4].upper() if k[0] == 'BalanceByMoneyType' else _pct(k[3])}{v}"
			for k, v in items
		)

	def _close_day(self, device, body, op):
		if device.status not in ("FiscalDayOpened", "FiscalDayCloseFailed"):
			code = "FISC03" if device.status == "FiscalDayCloseInitiated" else "FISC04"
			raise _HttpError(422, code, "Closing day is not allowed")
		day = device.days[-1]
		if body["fiscalDayNo"] != day.fiscal_day_no:
			raise _HttpError(422, "FISC04", "fiscalDayNo is not the open day")
		if any(e["validationErrorColor"] in ("Grey", "Red") for x in day.receipts for e in x.errors):
			raise _HttpError(422, "ReceiptsWithValidationErrors", "Fiscal day holds Grey or Red receipts")
		device.status = "FiscalDayCloseInitiated"
		device.closing_error_code = None
		device.pending_close = body
		return {"operationID": op}

	def settle(self) -> None:
		"""Finish asynchronous closes (the simulator's ``closeDelayMs`` timer)."""
		for device in self.devices.values():
			body = device.pending_close
			if not body:
				continue
			device.pending_close = None
			day = device.days[-1]
			canonical = (
				f"{device.device_id}{day.fiscal_day_no}{day.opened[:10]}{self._counter_string(day.counters)}"
			)
			sig = body.get("fiscalDayDeviceSignature") or {}
			cert = x509.load_pem_x509_certificate(device.certificate_pem.encode())
			valid = sig.get("hash") == _b64sha256(canonical)
			if valid:
				try:
					cert.public_key().verify(
						base64.b64decode(sig["signature"]), canonical.encode(), ec.ECDSA(hashes.SHA256())
					)
				except InvalidSignature:
					valid = False
			sent = {
				(
					c["fiscalCounterType"],
					c["fiscalCounterCurrency"],
					c.get("fiscalCounterTaxID"),
					c.get("fiscalCounterTaxPercent"),
					c.get("fiscalCounterMoneyType"),
				): _cents(c["fiscalCounterValue"])
				for c in body.get("fiscalDayCounters", [])
			}
			ours = {k: v for k, v in day.counters.items() if v}
			if self.faults.refuse_close:
				device.closing_error_code, self.faults.refuse_close = self.faults.refuse_close, None
				device.status = (
					"FiscalDayOpened" if self.faults.refuse_close_as_opened else "FiscalDayCloseFailed"
				)
			elif not valid:
				device.status, device.closing_error_code = "FiscalDayCloseFailed", "BadCertificateSignature"
			elif body.get("receiptCounter") != len(day.receipts) or sent != ours:
				device.status, device.closing_error_code = "FiscalDayCloseFailed", "CountersMismatch"
			else:
				device.status = "FiscalDayClosed"
				day.closed = self.clock().strftime("%Y-%m-%dT%H:%M:%S")
				day.device_signature = sig["signature"]
				# §13.3.2
				day.server_signature = self.ca.sign(
					f"{device.device_id}{day.fiscal_day_no}{day.opened[:10]}{day.closed}AUTO{self._counter_string(day.counters)}{sig['signature']}"
				)
