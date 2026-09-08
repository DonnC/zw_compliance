"""Transport protocol and FdmsClient, ported from src/core/transport.ts.

The core has no TLS/certificate logic - mTLS is the platform adapter's job
(zw_compliance.zimra.transport.RequestsTransport). This module only knows
about headers, JSON bodies, endpoint paths, retry rules and error mapping.
"""

from __future__ import annotations

import json as json_module
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Callable, Optional, Protocol

from .errors import FdmsApiError, is_network_error
from .types import DeviceIdentity


@dataclass
class TransportRequest:
	method: str  # "GET" | "POST"
	url: str
	headers: dict[str, str]
	body: Optional[str] = None
	timeout_ms: int = 30000


@dataclass
class TransportResponse:
	status: int
	headers: dict[str, str]  # lower-cased header names
	text: str


class Transport(Protocol):
	def request(self, req: TransportRequest) -> TransportResponse: ...


@dataclass
class FdmsClientOptions:
	environment: str = "test"  # "test" | "production"
	base_url: Optional[str] = None
	timeout_ms: int = 30000
	on_server_date: Optional[Callable[[datetime, float], None]] = None
	retries: int = 2


class FdmsClient:
	def __init__(self, device: DeviceIdentity, transport: Transport, options: FdmsClientOptions | None = None):
		self.device = device
		self.transport = transport
		self.options = options or FdmsClientOptions()
		from .types import FDMS_BASE_URLS

		self.base_url = (self.options.base_url or FDMS_BASE_URLS[self.options.environment]).rstrip("/")

	def device_path(self, endpoint: str) -> str:
		return f"/Device/v1/{self.device.device_id}/{endpoint}"

	def public_path(self, endpoint: str) -> str:
		return f"/Public/v1/{self.device.device_id}/{endpoint}"

	def request(self, method: str, path: str, body: Optional[dict] = None) -> Optional[dict]:
		headers = {
			"Accept": "application/json",
			"DeviceModelName": self.device.model_name,
			"DeviceModelVersion": self.device.model_version,
		}
		body_text = None
		if body is not None:
			body_text = json_module.dumps(body)
			headers["Content-Type"] = "application/json"

		idempotent = method == "GET" or path.endswith("/Ping")
		attempts = (1 + self.options.retries) if idempotent else 1

		last_error: Optional[BaseException] = None
		start = time.monotonic()
		for attempt in range(1, attempts + 1):
			try:
				response = self.transport.request(
					TransportRequest(
						method=method,
						url=self.base_url + path,
						headers=headers,
						body=body_text,
						timeout_ms=self.options.timeout_ms,
					)
				)
				elapsed_ms = (time.monotonic() - start) * 1000
				self._note_server_date(response, elapsed_ms)
				return self._handle_response(response)
			except FdmsApiError:
				raise
			except Exception as error:  # noqa: BLE001 - transport-layer failures only
				last_error = error
				if attempt < attempts and is_network_error(error):
					backoff = 200 * (2 ** (attempt - 1)) + random.random() * 200
					time.sleep(backoff / 1000)
					continue
				raise
		if last_error:
			raise last_error
		return None

	def _note_server_date(self, response: TransportResponse, elapsed_ms: float) -> None:
		date_header = response.headers.get("date")
		if not date_header or not self.options.on_server_date:
			return
		try:
			parsed = parsedate_to_datetime(date_header)
		except (TypeError, ValueError):
			return
		if parsed.tzinfo is not None:
			parsed = parsed.astimezone().replace(tzinfo=None)
		self.options.on_server_date(parsed, elapsed_ms)

	def _handle_response(self, response: TransportResponse) -> Optional[dict]:
		if not (200 <= response.status < 300):
			problem = None
			try:
				problem = json_module.loads(response.text) if response.text else None
			except ValueError:
				problem = None
			operation_id = response.headers.get("operationid")
			raise FdmsApiError(response.status, problem, operation_id)
		return json_module.loads(response.text) if response.text else None
