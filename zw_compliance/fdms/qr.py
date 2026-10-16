"""Receipt QR code and verification code (spec §11, §10.3 fields [45]-[48]).

QR content: ``qrUrl/`` + deviceID (10 digits) + receiptDate (``ddMMyyyy``) +
receiptGlobalNo (10 digits) + receiptQrData, where receiptQrData is the first
16 hexadecimal characters of the MD5 of the receipt device signature. The same
16 characters, in four groups of four separated by ``-``, are printed as the
receipt's verification code (e.g. ``4C8B-E276-6333-0417``).
"""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime


def receipt_qr_data(device_signature_b64: str) -> str:
	"""receiptQrData: first 16 hex chars (upper case, as in the §11 examples) of
	MD5 over the raw device signature bytes."""
	return hashlib.md5(base64.b64decode(device_signature_b64)).hexdigest()[:16].upper()


def receipt_qr_url(
	qr_url: str, device_id: int, receipt_date: datetime, receipt_global_no: int, qr_data: str
) -> str:
	base = qr_url if qr_url.endswith("/") else f"{qr_url}/"
	return f"{base}{device_id:010d}{receipt_date:%d%m%Y}{receipt_global_no:010d}{qr_data}"


def verification_code(qr_data: str) -> str:
	"""``4C8BE27663330417`` → ``4C8B-E276-6333-0417``."""
	if len(qr_data) != 16:
		raise ValueError("receiptQrData must be 16 characters")
	return "-".join(qr_data[i : i + 4] for i in range(0, 16, 4))
