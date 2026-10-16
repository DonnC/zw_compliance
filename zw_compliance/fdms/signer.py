"""Device key handling and signing (spec §12, §13.1).

ZIMRA accepts ECC ECDSA on secp256r1 (preferred) or RSA 2048 device keys
(§4.2). Signatures are ASN.1 DER ECDSA-with-SHA256, or SHA256withRSA
(PKCS#1 v1.5). The hash sent next to the signature is the base64 SHA-256 of
the same canonical string (§5.3, §13.1).
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

from .types import SignatureData

PrivateKey = ec.EllipticCurvePrivateKey | rsa.RSAPrivateKey


def generate_private_key() -> ec.EllipticCurvePrivateKey:
	"""A new secp256r1 key, ZIMRA's first preference (§4.2)."""
	return ec.generate_private_key(ec.SECP256R1())


def private_key_to_pem(key: PrivateKey) -> str:
	return key.private_bytes(
		encoding=serialization.Encoding.PEM,
		format=serialization.PrivateFormat.PKCS8,
		encryption_algorithm=serialization.NoEncryption(),
	).decode("ascii")


def load_private_key(pem: str) -> PrivateKey:
	key = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
	if isinstance(key, ec.EllipticCurvePrivateKey):
		if not isinstance(key.curve, ec.SECP256R1):
			raise ValueError("ZIMRA only accepts ECC keys on the secp256r1 (P-256) curve.")
		return key
	if isinstance(key, rsa.RSAPrivateKey):
		if key.key_size < 2048:
			raise ValueError("ZIMRA requires RSA keys of at least 2048 bits.")
		return key
	raise ValueError("Unsupported device key type; use ECC P-256 or RSA 2048.")


def sha256_base64(data: str | bytes) -> str:
	raw = data.encode("utf-8") if isinstance(data, str) else data
	return base64.b64encode(hashlib.sha256(raw).digest()).decode("ascii")


def sign(key: PrivateKey, canonical: str) -> SignatureData:
	"""Hash and sign a canonical string (§13.1)."""
	data = canonical.encode("utf-8")
	if isinstance(key, ec.EllipticCurvePrivateKey):
		raw = key.sign(data, ec.ECDSA(hashes.SHA256()))  # DER-encoded
	else:
		raw = key.sign(data, padding.PKCS1v15(), hashes.SHA256())
	return SignatureData(hash=sha256_base64(data), signature=base64.b64encode(raw).decode("ascii"))
