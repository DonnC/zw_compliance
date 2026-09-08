"""Receipt verification QR data, ported from src/core/qr.ts.

Format: {qrUrl with trailing slash}{deviceId:010d}{ddMMyyyy}{receiptGlobalNo:010d}{md5-16hex}
The MD5 is computed over the *decoded raw bytes* of the device signature
(the `signature` field, not the `hash` field), truncated to the first 16
hex characters (8 bytes). Scanning target in production: https://fdms.zimra.co.zw
"""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime


def receipt_qr_data(
	qr_url: str,
	device_id: int,
	receipt_date: datetime,
	receipt_global_no: int,
	device_signature_base64: str,
) -> str:
	sig_bytes = base64.b64decode(device_signature_base64)
	md5_16 = hashlib.md5(sig_bytes).hexdigest()[:16]
	dd_mm_yyyy = f"{receipt_date.day:02d}{receipt_date.month:02d}{receipt_date.year:04d}"
	base = qr_url if qr_url.endswith("/") else qr_url + "/"
	return f"{base}{device_id:010d}{dd_mm_yyyy}{receipt_global_no:010d}{md5_16}"
