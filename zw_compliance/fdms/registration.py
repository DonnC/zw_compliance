"""Device registration, ported from src/core/registration.ts.

Unlike the TS SDK (which asks an abstract Signer to sign the CSR because it
may not hold exportable key material), this port always has direct access
to the EC private key, so registration builds the CSR straight from it via
fdms.csr - see the module docstring in csr.py for the rationale.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey

from .csr import build_csr, device_common_name
from .errors import FdmsApiError
from .transport import FdmsClient, FdmsClientOptions, Transport, TransportRequest
from .types import FDMS_BASE_URLS, DeviceIdentity


@dataclass
class RegisteredDevice:
	certificate_pem: str
	csr_pem: str
	common_name: str
	operation_id: str


def register_device(
	device: DeviceIdentity,
	activation_key: str,
	private_key: EllipticCurvePrivateKey,
	transport: Transport,
	environment: str = "test",
	base_url: Optional[str] = None,
	client_prefix: str = "ZIMRA",
) -> RegisteredDevice:
	common_name = device_common_name(device.serial_number, device.device_id, client_prefix)
	csr_pem = build_csr(private_key, common_name)
	client = FdmsClient(device, transport, FdmsClientOptions(environment=environment, base_url=base_url))
	body = {"certificateRequest": csr_pem, "activationKey": activation_key}
	data = client.request("POST", client.public_path("RegisterDevice"), body)
	return RegisteredDevice(
		certificate_pem=data["certificate"],
		csr_pem=csr_pem,
		common_name=common_name,
		operation_id=data["operationID"],
	)


def get_server_certificate(
	transport: Transport,
	environment: str = "test",
	thumbprint: Optional[str] = None,
	base_url: Optional[str] = None,
	timeout_ms: int = 30000,
) -> list[str]:
	"""Raw, unauthenticated call - no DeviceModelName/Version headers, no
	retry, no per-device path (global, not /Device/v1/{id}/... or
	/Public/v1/{id}/...).
	"""
	root = (base_url or FDMS_BASE_URLS[environment]).rstrip("/")
	query = f"?{urlencode({'thumbprint': thumbprint})}" if thumbprint else ""
	url = f"{root}/Public/v1/GetServerCertificate{query}"
	response = transport.request(TransportRequest(method="GET", url=url, headers={}, timeout_ms=timeout_ms))
	if not (200 <= response.status < 300):
		raise FdmsApiError(response.status, None)
	return json.loads(response.text)["certificate"]
