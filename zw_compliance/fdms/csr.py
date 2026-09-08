"""CSR building for FDMS device registration/renewal.

The TS SDK hand-rolls a DER writer (src/core/asn1.ts, csr.ts) because
browser/Android/HSM signers can't export key material, so the SDK must
build the CertificationRequestInfo bytes itself and only ask the Signer for
a raw signature over them. In this Python port we always hold the EC
private key locally (via `cryptography`), so we build the PKCS#10 request
with `cryptography.x509.CertificateSigningRequestBuilder` instead of a hand
port of the DER writer - it produces an equivalent, standards-correct CSR
(CN=ZIMRA-{serial}-{deviceId:010d}, secp256r1, ecdsa-with-SHA256, no
attributes) more safely than re-deriving ASN.1 encoding rules by hand.

If a true HSM-backed Signer is ever needed, add a signer.sign()-based DER
CSR builder here without touching the rest of fdms/ - device.py and
registration.py only call `build_csr()` / `device_common_name()`.
"""

from __future__ import annotations

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey


def device_common_name(serial_number: str, device_id: int, client_prefix: str = "ZIMRA") -> str:
	return f"{client_prefix}-{serial_number}-{device_id:010d}"


def build_csr(private_key: EllipticCurvePrivateKey, common_name: str) -> str:
	"""Return a PEM-encoded PKCS#10 CertificateSigningRequest signed by
	`private_key`, subject CN=`common_name`, no extensions/attributes.
	"""
	csr = (
		x509.CertificateSigningRequestBuilder()
		.subject_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, common_name)]))
		.sign(private_key, hashes.SHA256())
	)
	return csr.public_bytes(serialization.Encoding.PEM).decode("ascii")
