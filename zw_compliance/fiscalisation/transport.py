"""HTTP transport for :class:`zw_compliance.fdms.client.FdmsClient`, over ``requests``."""

from __future__ import annotations

import requests

from zw_compliance.fdms.client import TransportRequest, TransportResponse
from zw_compliance.fdms.errors import TransportError


class RequestsTransport:
	def __init__(self, cert: tuple[str, str] | None = None, verify: bool | str = True):
		"""``cert`` is the device ``(certificate_path, key_path)`` for mutual TLS;
		``verify`` is ``True`` (the certifi CA bundle) or a CA bundle path."""
		self.cert = cert
		self.verify = verify
		self.session = requests.Session()

	def request(self, req: TransportRequest) -> TransportResponse:
		try:
			response = self.session.request(
				req.method,
				req.url,
				headers=req.headers,
				data=req.body.encode("utf-8") if req.body is not None else None,
				cert=self.cert,
				verify=self.verify,
				timeout=req.timeout,
			)
		except requests.exceptions.RequestException as e:
			raise TransportError(f"{type(e).__name__}: {e}") from e
		return TransportResponse(
			status=response.status_code,
			headers={k.lower(): v for k, v in response.headers.items()},
			text=response.text,
		)

	def close(self) -> None:
		self.session.close()
