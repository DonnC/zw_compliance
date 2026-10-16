"""Certificate signing requests for registerDevice / issueCertificate (spec §4.2, §12).

The subject CN must be ``ZIMRA-<serial no>-<device id zero-padded to 10>``,
e.g. ``ZIMRA-SN: 001-0000000187``. Other subject fields are optional and are
deliberately omitted: if present they must match ZIMRA's values exactly, so
leaving them out removes a way to be rejected.
"""

from __future__ import annotations

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

from .signer import PrivateKey


def device_common_name(serial_number: str, device_id: int) -> str:
	return f"ZIMRA-{serial_number}-{device_id:010d}"


def build_csr(private_key: PrivateKey, serial_number: str, device_id: int) -> str:
	"""PEM PKCS#10 request signed with SHA-256 (ecdsa-with-SHA256 or SHA256WithRSA)."""
	subject = x509.Name(
		[x509.NameAttribute(x509.NameOID.COMMON_NAME, device_common_name(serial_number, device_id))]
	)
	csr = x509.CertificateSigningRequestBuilder().subject_name(subject).sign(private_key, hashes.SHA256())
	return csr.public_bytes(serialization.Encoding.PEM).decode("ascii")


def certificate_valid_till(certificate_pem: str):
	"""Naive UTC expiry of a device certificate."""
	cert = x509.load_pem_x509_certificate(certificate_pem.encode("ascii"))
	expiry = getattr(cert, "not_valid_after_utc", None)
	return expiry.replace(tzinfo=None) if expiry else cert.not_valid_after
