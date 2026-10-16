"""HTTP transport for :class:`zw_compliance.fdms.client.FdmsClient`, over ``requests``."""

from __future__ import annotations

import ssl

import requests
from requests.adapters import HTTPAdapter

from zw_compliance.fdms.client import TransportRequest, TransportResponse
from zw_compliance.fdms.errors import TransportError


class _TrustedCaAdapter(HTTPAdapter):
	"""TLS for the Test environment's *Trusted CA* override (simulators).

	Python 3.13+ verifies with ``VERIFY_X509_STRICT``, which also requires
	certificate extensions (e.g. Authority Key Identifier) that development
	CAs such as the zimra-fdms simulator's leave out. The chain is still
	verified against the given CA and the hostname is still checked; only the
	strict profile is relaxed, and only for this override. ZIMRA's own
	servers are always verified with the default, strict context.
	"""

	def __init__(self, cafile: str, **kwargs):
		self.cafile = cafile
		super().__init__(**kwargs)

	def init_poolmanager(self, *args, **kwargs):
		context = ssl.create_default_context(cafile=self.cafile)
		context.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)
		kwargs["ssl_context"] = context
		return super().init_poolmanager(*args, **kwargs)


class RequestsTransport:
	def __init__(self, cert: tuple[str, str] | None = None, verify: bool | str = True):
		"""``cert`` is the device ``(certificate_path, key_path)`` for mutual TLS;
		``verify`` is ``True`` (the certifi CA bundle) or a CA bundle path, the
		latter only for the Test environment's *Trusted CA* override."""
		self.cert = cert
		self.verify = verify
		self.session = requests.Session()
		if isinstance(verify, str):
			self.session.mount("https://", _TrustedCaAdapter(verify))

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
