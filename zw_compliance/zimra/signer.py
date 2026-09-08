"""Signer implementation of fdms.signer.Signer, over a PEM EC private key
loaded from a Frappe private File attachment.
"""

from __future__ import annotations

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey


class PemFileSigner:
	def __init__(self, private_key_pem: str):
		key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
		if not isinstance(key, EllipticCurvePrivateKey):
			raise ValueError("ZIMRA device private key must be an EC (secp256r1) key.")
		self.private_key: EllipticCurvePrivateKey = key

	def sign(self, data: bytes) -> bytes:
		return self.private_key.sign(data, ec.ECDSA(hashes.SHA256()))

	def public_key_spki(self) -> bytes:
		return self.private_key.public_key().public_bytes(
			serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
		)


def generate_device_key_pair() -> tuple[str, EllipticCurvePrivateKey]:
	"""Generate a new secp256r1 key pair for device registration/renewal.
	Returns (private_key_pem, private_key_object).
	"""
	key = ec.generate_private_key(ec.SECP256R1())
	pem = key.private_bytes(
		encoding=serialization.Encoding.PEM,
		format=serialization.PrivateFormat.PKCS8,
		encryption_algorithm=serialization.NoEncryption(),
	).decode("ascii")
	return pem, key
