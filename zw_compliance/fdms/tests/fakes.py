"""Lightweight fakes for exercising fdms.device / fdms.queue state-machine
logic in pure-Python unit tests, without a Frappe site or the Node
zimra-fdms simulator. These fakes do NOT replicate ZIMRA's own validation
rules (RCPT0xx etc) - that behaviour is instead exercised against the real
zimra-fdms-simulator in the Frappe-level integration tests (see
zw_compliance/zimra/tests). These fakes only stand in for "a well-behaved
FDMS" so device.py/queue.py's own bookkeeping (sequence numbers, hash
chaining, pending-marker crash safety) can be tested in isolation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from zw_compliance.fdms.storage import Storage
from zw_compliance.fdms.transport import Transport, TransportRequest, TransportResponse


class FakeSigner:
	def __init__(self):
		self.private_key = ec.generate_private_key(ec.SECP256R1())

	def sign(self, data: bytes) -> bytes:
		return self.private_key.sign(data, ec.ECDSA(hashes.SHA256()))

	def public_key_spki(self) -> bytes:
		from cryptography.hazmat.primitives import serialization

		return self.private_key.public_key().public_bytes(
			serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
		)


@dataclass
class FakeFdmsServer:
	"""In-memory stand-in for a single device's FDMS state, just enough to
	drive FiscalDevice through open/submit/close/reconcile flows.
	"""

	status: str = "FiscalDayClosed"
	fiscal_day_no: int = 0
	last_receipt_global_no: int = 0
	next_receipt_id: int = 1
	drop_next_submit_response: bool = False
	fail_next_submit_before_receiving: bool = False
	reject_next_submit: bool = False
	validation_errors_next_submit: list[dict] = field(default_factory=list)
	received_receipts: list[dict] = field(default_factory=list)
	config_currencies: list[str] = field(default_factory=lambda: ["USD", "ZWG"])

	def handle(self, method: str, path: str, body: Optional[dict]) -> dict:
		if path.endswith("/GetStatus"):
			return {
				"operationID": "op-status",
				"fiscalDayStatus": self.status,
				"lastReceiptGlobalNo": self.last_receipt_global_no,
			}
		if path.endswith("/GetConfig"):
			return {
				"operationID": "op-config",
				"taxPayerName": "Test Co",
				"taxPayerTIN": "1000000000",
				"vatNumber": "VAT1",
				"deviceSerialNo": "TEST01",
				"deviceBranchName": "HQ",
				"deviceBranchAddress": None,
				"deviceBranchContacts": None,
				"deviceOperatingMode": "Online",
				"taxPayerDayMaxHrs": 24,
				"applicableTaxes": [],
				"certificateValidTill": "2030-01-01T00:00:00",
				"qrUrl": "https://fdmstest.zimra.co.zw",
			}
		if path.endswith("/OpenDay"):
			self.status = "FiscalDayOpened"
			self.fiscal_day_no = (body.get("fiscalDayNo") or self.fiscal_day_no + 1)
			return {"operationID": "op-open", "fiscalDayNo": self.fiscal_day_no}
		if path.endswith("/SubmitReceipt"):
			if self.reject_next_submit:
				self.reject_next_submit = False
				from zw_compliance.fdms.errors import FdmsApiError

				raise FdmsApiError(400, {"detail": "rejected", "errorCode": "RCPT099"}, "op-reject")
			if self.fail_next_submit_before_receiving:
				self.fail_next_submit_before_receiving = False
				from zw_compliance.fdms.errors import TransportError

				raise TransportError("simulated connection dropped before FDMS received the request")
			receipt = body["receipt"]
			self.received_receipts.append(receipt)
			self.last_receipt_global_no = max(self.last_receipt_global_no, receipt["receiptGlobalNo"])
			receipt_id = self.next_receipt_id
			self.next_receipt_id += 1
			errors = self.validation_errors_next_submit
			self.validation_errors_next_submit = []
			if self.drop_next_submit_response:
				self.drop_next_submit_response = False
				from zw_compliance.fdms.errors import TransportError

				raise TransportError("simulated dropped connection")
			return {
				"operationID": "op-submit",
				"receiptID": receipt_id,
				"serverDate": "2026-01-01T00:00:00",
				"validationErrors": errors,
			}
		if path.endswith("/CloseDay"):
			self.status = "FiscalDayClosed"
			return {"operationID": "op-close"}
		if path.endswith("/Ping"):
			return {"operationID": "op-ping", "reportingFrequency": 30}
		raise AssertionError(f"unhandled fake FDMS path: {path}")


class FakeTransport(Transport):
	def __init__(self, server: FakeFdmsServer):
		self.server = server

	def request(self, req: TransportRequest) -> TransportResponse:
		from urllib.parse import urlparse

		path = urlparse(req.url).path
		body = json.loads(req.body) if req.body else None
		data = self.server.handle(req.method, path, body)
		return TransportResponse(status=200, headers={}, text=json.dumps(data))


class MemoryStorage(Storage):
	def __init__(self):
		self._data: dict[str, str] = {}

	def get(self, key: str):
		return self._data.get(key)

	def set(self, key: str, value: str) -> None:
		self._data[key] = value

	def delete(self, key: str) -> None:
		self._data.pop(key, None)
