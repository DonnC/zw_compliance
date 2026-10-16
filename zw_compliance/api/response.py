"""The response envelope shared by every API endpoint.

Success::

    {"message": {"ok": true, "data": {...}}}

Failure (HTTP 4xx/5xx)::

    {
        "message": {
            "ok": false,
            "error": {"code": "VALIDATION_ERROR", "message": "...", "zimra_code": "RCPT047"},
        }
    }

``message`` is Frappe's standard wrapper for method results. Error codes are
stable identifiers clients can branch on; messages are for people.
"""

from __future__ import annotations

import functools
import re

import frappe
from frappe.utils import strip_html

ERRORS = {
	frappe.PermissionError: (403, "PERMISSION_DENIED"),
	frappe.DoesNotExistError: (404, "NOT_FOUND"),
	frappe.DuplicateEntryError: (409, "DUPLICATE"),
	frappe.ValidationError: (422, "VALIDATION_ERROR"),
}
SAVEPOINT = "zw_compliance_api"
_ZIMRA_CODE = re.compile(r"\b(RCPT\d{2,3}|DEV\d{2}|FISC\d{2})\b")


class ApiError(Exception):
	def __init__(self, code: str, message: str, status: int = 422):
		super().__init__(message)
		self.code, self.status = code, status


def _message(error: Exception) -> str:
	text = str(error) or ""
	if not text and frappe.local.message_log:
		text = frappe.parse_json(frappe.local.message_log[-1]).get("message", "")
	return strip_html(text).strip() or error.__class__.__name__


def endpoint(fn):
	"""Wrap an API function: envelope on success; on failure, undo the
	function's own writes (savepoint) and return the error envelope."""

	@functools.wraps(fn)
	def wrapper(*args, **kwargs):
		kwargs.pop("cmd", None)
		frappe.db.savepoint(SAVEPOINT)
		try:
			data = fn(*args, **kwargs)
			frappe.local.message_log = []
			return {"ok": True, "data": data}
		except Exception as e:
			frappe.db.rollback(save_point=SAVEPOINT)
			if isinstance(e, ApiError):
				status, code = e.status, e.code
			else:
				status, code = next(
					((s, c) for cls, (s, c) in ERRORS.items() if isinstance(e, cls)), (500, "INTERNAL_ERROR")
				)
				if status == 500:
					frappe.log_error(title=f"zw_compliance API: {fn.__name__}")
			message = _message(e) if status != 500 else "Unexpected server error; it has been logged."
			frappe.local.message_log = []
			frappe.local.response.http_status_code = status
			error = {"code": code, "message": message}
			if match := _ZIMRA_CODE.search(message):
				error["zimra_code"] = match.group(1)
			return {"ok": False, "error": error}

	return wrapper
