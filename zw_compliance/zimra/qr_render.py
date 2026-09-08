"""Server-side QR PNG rendering for the print-format HTML field
(custom_zimra_qr_code_view). Rendered once at fiscalisation time and cached
as a data: URI on the invoice, rather than regenerated on every print.
"""

from __future__ import annotations

import base64
from io import BytesIO


def qr_data_uri(data: str) -> str:
	import qrcode

	img = qrcode.make(data, border=2)
	buf = BytesIO()
	img.save(buf, format="PNG")
	encoded = base64.b64encode(buf.getvalue()).decode("ascii")
	return f"data:image/png;base64,{encoded}"


def qr_html(qr_data: str, verification_code: str | None = None) -> str:
	img = qr_data_uri(qr_data)
	caption = f"<div style='font-size:10px;text-align:center;'>{verification_code}</div>" if verification_code else ""
	return (
		f"<div style='text-align:center;'>"
		f"<img src='{img}' style='width:120px;height:120px;' alt='ZIMRA verification QR'/>"
		f"{caption}</div>"
	)
