"""Verification of FDMS signatures (spec §13.2.2, §13.3.2, §4.12).

FDMS countersigns every accepted receipt (``receiptServerSignature``) and
every closed fiscal day (``fiscalDayServerSignature``). The device "should
store" them as FDMS's confirmation; verifying them proves the acknowledgement
genuinely came from ZIMRA. The signing certificate is fetched with
``getServerCertificate`` and selected by its SHA-1 thumbprint (§5.3
SignatureDataEx).
"""

from __future__ import annotations

import base64
import hashlib

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

from .signer import sha256_base64


def certificate_thumbprints(certificate_pem: str) -> set[str]:
	"""SHA-1 thumbprint of a certificate in every textual form FDMS might use."""
	der = x509.load_pem_x509_certificate(certificate_pem.encode("ascii")).public_bytes(
		serialization.Encoding.DER
	)
	# SHA-1 is the thumbprint algorithm the spec prescribes (§5.3 SignatureDataEx); it is an identifier, not a security control.
	digest = hashlib.sha1(der).digest()
	return {digest.hex().lower(), digest.hex().upper(), base64.b64encode(digest).decode("ascii")}


def select_certificate(chain: list[str], thumbprint: str | None) -> str | None:
	"""The certificate in ``chain`` matching ``thumbprint`` (else the leaf)."""
	if not chain:
		return None
	if not thumbprint:
		return chain[0]
	for pem in chain:
		if thumbprint in certificate_thumbprints(pem):
			return pem
	return None


def verify_signature(certificate_pem: str, canonical: str, signature: dict) -> bool:
	"""True when ``signature`` (SignatureData/Ex dict) signs ``canonical`` with the
	certificate's key and its ``hash`` is the SHA-256 of ``canonical``."""
	if not signature or not signature.get("signature"):
		return False
	if signature.get("hash") and signature["hash"] != sha256_base64(canonical):
		return False
	public_key = x509.load_pem_x509_certificate(certificate_pem.encode("ascii")).public_key()
	raw = base64.b64decode(signature["signature"])
	data = canonical.encode("utf-8")
	try:
		if isinstance(public_key, ec.EllipticCurvePublicKey):
			public_key.verify(raw, data, ec.ECDSA(hashes.SHA256()))
		elif isinstance(public_key, rsa.RSAPublicKey):
			public_key.verify(raw, data, padding.PKCS1v15(), hashes.SHA256())
		else:
			return False
	except InvalidSignature:
		return False
	return True
