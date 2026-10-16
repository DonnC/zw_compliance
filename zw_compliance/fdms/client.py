"""Fiscal Device Gateway API client (spec §4, §7, §8).

Transport-agnostic: the caller supplies a :class:`Transport` that owns TLS
(mutual TLS with the device certificate for ``/Device`` endpoints, §7.3).

Retries: only idempotent calls (GETs and ``ping``) are retried here, and only
on network failures. ``submitReceipt`` is safe to *re*-send later - FDMS
returns the original receiptID for the same deviceID, receiptGlobalNo and
hash (§4.7) - but that is the outbox's decision, not the transport's.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Protocol
from urllib.parse import quote

from .errors import FdmsApiError, TransportError
from .signing import fdms_datetime
from .types import (
	FDMS_BASE_URLS,
	DayStatus,
	DeviceConfig,
	DeviceIdentity,
	FiscalCounter,
	Receipt,
	SignatureData,
	SubmitReceiptResult,
	TaxpayerInfo,
)
from .wire import (
	counters_to_wire,
	parse_config,
	parse_status,
	parse_submit_result,
	parse_taxpayer_info,
	receipt_to_wire,
)

DEFAULT_TIMEOUT = 30  # seconds, spec §7.4


@dataclass
class TransportRequest:
	method: str
	url: str
	headers: dict[str, str]
	body: str | None = None
	timeout: float = DEFAULT_TIMEOUT


@dataclass
class TransportResponse:
	status: int
	headers: dict[str, str]  # lower-case names
	text: str


class Transport(Protocol):
	def request(self, req: TransportRequest) -> TransportResponse:
		"""Perform one HTTP request; raise :class:`TransportError` on network failure."""
		...


@dataclass
class ServerTime:
	"""FDMS's clock as seen in a response ``Date`` header (UTC) plus the round trip."""

	server_utc: datetime
	round_trip_ms: float


class FdmsClient:
	def __init__(
		self,
		device: DeviceIdentity,
		transport: Transport,
		*,
		environment: str = "test",
		base_url: str | None = None,
		timeout: float = DEFAULT_TIMEOUT,
		retries: int = 2,
		on_server_time: Callable[[ServerTime], None] | None = None,
	):
		self.device = device
		self.transport = transport
		self.base_url = (base_url or FDMS_BASE_URLS[environment]).rstrip("/")
		self.timeout = timeout
		self.retries = retries
		self.on_server_time = on_server_time

	# -- plumbing -----------------------------------------------------------

	def _headers(self, with_body: bool) -> dict[str, str]:
		headers = {
			"Accept": "application/json",
			"DeviceModelName": self.device.model_name,
			# The PDF (§4) names this header DeviceModelVersionNo; ZIMRA's live
			# Swagger names it DeviceModelVersion. Both are sent.
			"DeviceModelVersionNo": self.device.model_version,
			"DeviceModelVersion": self.device.model_version,
		}
		if with_body:
			headers["Content-Type"] = "application/json"
		return headers

	def _call(self, method: str, path: str, body: dict | None = None, *, idempotent: bool | None = None):
		idempotent = method == "GET" if idempotent is None else idempotent
		attempts = 1 + (self.retries if idempotent else 0)
		payload = None if body is None else json.dumps(body, separators=(",", ":"))
		for attempt in range(1, attempts + 1):
			started = time.monotonic()
			try:
				response = self.transport.request(
					TransportRequest(
						method,
						self.base_url + path,
						self._headers(payload is not None),
						payload,
						self.timeout,
					)
				)
			except TransportError:
				if attempt == attempts:
					raise
				time.sleep(0.25 * 2 ** (attempt - 1))
				continue
			self._note_server_time(response, (time.monotonic() - started) * 1000)
			if 200 <= response.status < 300:
				return json.loads(response.text) if response.text else {}
			try:
				problem = json.loads(response.text) if response.text else None
			except ValueError:
				problem = {"detail": response.text[:500]}
			error = FdmsApiError(
				response.status,
				problem if isinstance(problem, dict) else None,
				response.headers.get("operationid"),
			)
			if error.retryable and idempotent and attempt < attempts:
				time.sleep(0.25 * 2 ** (attempt - 1))
				continue
			raise error
		raise TransportError("unreachable")  # pragma: no cover

	def _note_server_time(self, response: TransportResponse, round_trip_ms: float) -> None:
		value = response.headers.get("date")
		if not value or not self.on_server_time:
			return
		try:
			server = parsedate_to_datetime(value)
		except (TypeError, ValueError):
			return
		if server.tzinfo is not None:
			server = server.astimezone(tz=None).replace(tzinfo=None)  # to local naive
		self.on_server_time(ServerTime(server, round_trip_ms))

	def _device(self, endpoint: str) -> str:
		return f"/Device/v1/{self.device.device_id}/{endpoint}"

	def _public(self, endpoint: str) -> str:
		return f"/Public/v1/{self.device.device_id}/{endpoint}"

	# -- public endpoints (no client certificate, §7.3) -----------------------

	def verify_taxpayer_information(self, activation_key: str) -> TaxpayerInfo:
		"""§4.1 - confirm which taxpayer the device belongs to before registering."""
		data = self._call(
			"POST",
			self._public("VerifyTaxpayerInformation"),
			{"activationKey": activation_key, "deviceSerialNo": self.device.serial_number},
			idempotent=True,
		)
		return parse_taxpayer_info(data)

	def register_device(self, activation_key: str, csr_pem: str) -> tuple[str, str]:
		"""§4.2 - returns ``(certificate_pem, operation_id)``."""
		data = self._call(
			"POST",
			self._public("RegisterDevice"),
			{"activationKey": activation_key, "certificateRequest": csr_pem},
		)
		return data["certificate"], data.get("operationID")

	def get_server_certificate(self, thumbprint: str | None = None) -> tuple[list[str], str | None]:
		"""§4.12 - FDMS signing certificate chain and its validity (UTC)."""
		query = f"?thumbprint={quote(thumbprint)}" if thumbprint else ""
		data = self._call("GET", f"/Public/v1/GetServerCertificate{query}")
		return list(data.get("certificate") or []), data.get("certificateValidTill")

	# -- device endpoints (mutual TLS) ----------------------------------------

	def issue_certificate(self, csr_pem: str) -> tuple[str, str]:
		"""§4.3 - renew the device certificate; returns ``(certificate_pem, operation_id)``."""
		data = self._call("POST", self._device("IssueCertificate"), {"certificateRequest": csr_pem})
		return data["certificate"], data.get("operationID")

	def get_config(self) -> DeviceConfig:
		return parse_config(self._call("GET", self._device("GetConfig")))

	def get_status(self) -> DayStatus:
		return parse_status(self._call("GET", self._device("GetStatus")))

	def open_day(self, fiscal_day_opened: datetime, fiscal_day_no: int | None) -> tuple[int, str]:
		"""§4.6 - returns ``(fiscal_day_no, operation_id)``."""
		body = {"fiscalDayOpened": fdms_datetime(fiscal_day_opened)}
		if fiscal_day_no is not None:
			body["fiscalDayNo"] = fiscal_day_no
		data = self._call("POST", self._device("OpenDay"), body)
		return data["fiscalDayNo"], data.get("operationID")

	def submit_receipt(self, receipt: Receipt) -> SubmitReceiptResult:
		if receipt.device_signature is None:
			raise ValueError("receipt must be signed before submission")
		data = self._call("POST", self._device("SubmitReceipt"), {"receipt": receipt_to_wire(receipt)})
		return parse_submit_result(data)

	def submit_receipt_payload(self, payload: dict) -> SubmitReceiptResult:
		"""Submit an already-serialised, signed receipt exactly as stored."""
		data = self._call("POST", self._device("SubmitReceipt"), {"receipt": payload})
		return parse_submit_result(data)

	def close_day(
		self,
		fiscal_day_no: int,
		counters: list[FiscalCounter],
		signature: SignatureData,
		receipt_counter: int,
	) -> str:
		"""§4.11 - initiate the (asynchronous) close; returns the operation ID."""
		data = self._call(
			"POST",
			self._device("CloseDay"),
			{
				"fiscalDayNo": fiscal_day_no,
				"fiscalDayCounters": counters_to_wire(counters),
				"fiscalDayDeviceSignature": {"hash": signature.hash, "signature": signature.signature},
				"receiptCounter": receipt_counter,
			},
		)
		return data.get("operationID")

	def ping(self) -> int | None:
		"""§4.13 - returns the reporting frequency in minutes."""
		data = self._call("POST", self._device("Ping"), idempotent=True)
		return data.get("reportingFrequency")
