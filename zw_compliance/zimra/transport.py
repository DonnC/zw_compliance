"""mTLS Transport implementation of fdms.transport.Transport, over `requests`.

The fdms.transport.FdmsClient this backs already applies FDMS's own retry
rules (idempotent-only) - this class is intentionally a thin, single-attempt
HTTP call per request() invocation.
"""

from __future__ import annotations

from typing import Optional

import requests

from zw_compliance.fdms.errors import TransportError
from zw_compliance.fdms.transport import TransportRequest, TransportResponse


class RequestsTransport:
	def __init__(self, cert_path: Optional[str] = None, key_path: Optional[str] = None, ca_path: Optional[str] = None):
		"""`cert_path`/`key_path` are the device's mTLS client certificate and
		private key (required for /Device/v1/* calls; omit for /Public/v1/*
		calls like RegisterDevice, which use ordinary TLS). `ca_path` pins
		FDMS's server certificate chain if provided, otherwise the system
		trust store is used.
		"""
		self.cert = (cert_path, key_path) if cert_path and key_path else None
		self.verify = ca_path if ca_path else True

	def request(self, req: TransportRequest) -> TransportResponse:
		try:
			response = requests.request(
				method=req.method,
				url=req.url,
				headers=req.headers,
				data=req.body,
				cert=self.cert,
				verify=self.verify,
				timeout=req.timeout_ms / 1000,
			)
		except requests.exceptions.RequestException as error:
			raise TransportError(str(error), cause=error)
		return TransportResponse(
			status=response.status_code,
			headers={k.lower(): v for k, v in response.headers.items()},
			text=response.text,
		)
