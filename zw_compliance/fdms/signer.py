"""Signer protocol and DER/P1363 ECDSA conversion, ported from src/core/signer.ts.

FDMS requires ASN.1 DER-encoded ECDSA-P256-over-SHA256 signatures and
rejects raw IEEE P1363 (r||s) signatures with RCPT020/BadCertificateSignature.

Kept as a protocol (rather than hardcoding `cryptography` calls into
device.py) so a future HSM/KMS-backed signer is possible and so tests can
inject a fake signer - unlike the TS SDK this is a *sync* protocol, since
Frappe request handlers and background jobs are sync.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Literal, Protocol

from .types import SignatureData

EcdsaSignatureFormat = Literal["p1363", "der"]


class Signer(Protocol):
	def sign(self, data: bytes) -> bytes:
		"""ASN.1 DER ECDSA P-256 signature over SHA-256(data). Hashes internally;
		callers pass the raw message bytes, not a pre-computed digest."""
		...

	def public_key_spki(self) -> bytes:
		"""DER SubjectPublicKeyInfo of the matching public key, for CSR building."""
		...


def _encode_der_integer(b: bytes) -> bytes:
	i = 0
	while i < len(b) - 1 and b[i] == 0:
		i += 1
	v = b[i:]
	if v[0] & 0x80:
		v = b"\x00" + v
	return bytes([0x02, len(v)]) + v


def p1363_to_der(p1363: bytes) -> bytes:
	half = len(p1363) // 2
	r = _encode_der_integer(p1363[:half])
	s = _encode_der_integer(p1363[half:])
	return bytes([0x30, len(r) + len(s)]) + r + s


def der_to_p1363(der: bytes, byte_length: int = 32) -> bytes:
	i = 0
	assert der[i] == 0x30
	i += 1
	if der[i] & 0x80:
		i += 1 + (der[i] & 0x7F)
	else:
		i += 1

	def read_int() -> bytes:
		nonlocal i
		assert der[i] == 0x02
		i += 1
		length = der[i]
		i += 1
		v = der[i : i + length]
		i += length
		while len(v) > byte_length and v[0] == 0:
			v = v[1:]
		out = bytearray(byte_length)
		out[byte_length - len(v) :] = v
		return bytes(out)

	return read_int() + read_int()


def sha256_base64(data: bytes) -> str:
	return base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")


def sign_canonical_string(signer: Signer, canonical: str, format: EcdsaSignatureFormat = "der") -> SignatureData:
	data = canonical.encode("utf-8")
	der_signature = signer.sign(data)
	signature = der_signature if format == "der" else der_to_p1363(der_signature)
	return SignatureData(
		hash=sha256_base64(data),
		signature=base64.b64encode(signature).decode("ascii"),
	)
