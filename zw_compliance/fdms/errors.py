"""FDMS error catalogue, ported verbatim from src/core/errors.ts.

`observed=True` entries were actually seen against ZIMRA's live test
environment while building the zimra-fdms SDK; `observed=False` entries are
sourced from ZIMRA's documentation only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

ErrorColour = Literal["Red", "Yellow", "Grey"]


@dataclass(frozen=True)
class ErrorCatalogueEntry:
	code: str
	colour: ErrorColour
	cause: str
	fix: str
	observed: bool


ERROR_CATALOGUE: dict[str, ErrorCatalogueEntry] = {
	"RCPT010": ErrorCatalogueEntry(
		"RCPT010",
		"Red",
		"The receipt device signature does not verify against the device certificate.",
		"The canonical signing string or the hash chain (previousReceiptHash) is wrong. "
		"Re-check field order/formatting in signing.py against the SDK's signing.ts.",
		observed=False,
	),
	"RCPT011": ErrorCatalogueEntry(
		"RCPT011",
		"Red",
		"The receipt currency is not one of the currencies configured for this device.",
		"Only submit in a currency returned by GetConfig for this device.",
		observed=False,
	),
	"RCPT012": ErrorCatalogueEntry(
		"RCPT012",
		"Red",
		"receiptGlobalNo is not exactly one more than the highest number FDMS has for this device.",
		"Usually caused by opening a day off a stale GetStatus.lastReceiptGlobalNo, or two "
		"processes issuing receipts concurrently. Always take max(server, locally persisted) "
		"and serialise fiscalisation per device.",
		observed=True,
	),
	"RCPT013": ErrorCatalogueEntry(
		"RCPT013",
		"Red",
		"receiptCounter is not sequential within the fiscal day.",
		"receiptCounter must increment by 1 per receipt since the day was opened; do not "
		"reset or skip it outside of openDay().",
		observed=False,
	),
	"RCPT014": ErrorCatalogueEntry(
		"RCPT014",
		"Red",
		"Receipt line/tax totals do not reconcile with receiptTotal.",
		"Verify per-line tax rounding is done per-line (not summed then rounded once), and "
		"that receiptTotal matches the sum of line totals (+taxes if tax-exclusive).",
		observed=False,
	),
	"RCPT020": ErrorCatalogueEntry(
		"RCPT020",
		"Grey",
		"BadCertificateSignature - signature is not valid ASN.1 DER ECDSA.",
		"FDMS rejects raw IEEE P1363 (r||s) signatures. Ensure signatureFormat='der' end to end.",
		observed=True,
	),
	"RCPT021": ErrorCatalogueEntry(
		"RCPT021",
		"Grey",
		"The signing certificate does not match the device's registered certificate.",
		"Confirm the private key/certificate pair on Zimra Settings matches the last "
		"successful registration/renewal.",
		observed=False,
	),
	"RCPT030": ErrorCatalogueEntry(
		"RCPT030",
		"Red",
		"receiptDate is not strictly later than the previous receipt's date.",
		"Do not backdate receipts; if bulk-importing, ensure timestamps are strictly "
		"increasing per device. A day holding this error cannot be closed by the device.",
		observed=True,
	),
	"RCPT031": ErrorCatalogueEntry(
		"RCPT031",
		"Yellow",
		"receiptDate is ahead of FDMS server time.",
		"Accepted with a warning; rely on the ServerCorrectedClock offset rather than the "
		"device's local clock for new receipt timestamps.",
		observed=True,
	),
	"RCPT033": ErrorCatalogueEntry(
		"RCPT033",
		"Red",
		"Credit/Debit note references an invoice older than the return window.",
		"ZIMRA requires the referenced original invoice to be within 12 months of the note.",
		observed=False,
	),
	"RCPT047": ErrorCatalogueEntry(
		"RCPT047",
		"Red",
		"Mandatory National HS Code missing for a VAT-registered operator's receipt line.",
		"Populate the HS Code on the Item, or fall back to the Item Group's HS Code.",
		observed=False,
	),
	"RCPT048": ErrorCatalogueEntry(
		"RCPT048",
		"Red",
		"HS Code length is invalid for the line's tax rate.",
		"0%/exempt lines require an 8-digit HS Code; standard-rate lines require 4 or 8 digits.",
		observed=False,
	),
	"DEV02": ErrorCatalogueEntry(
		"DEV02",
		"Grey",
		"The device certificate is about to expire.",
		"Call renewCertificate() well before certificateValidTill.",
		observed=False,
	),
	"BadCertificateSignature": ErrorCatalogueEntry(
		"BadCertificateSignature",
		"Grey",
		"CloseDay signature invalid - FDMS signs over the date the fiscal day was opened, "
		"which GetStatus never reports back.",
		"Persist fiscalDayDate at openDay() time and reuse it verbatim for the CloseDay "
		"signing string; never recompute it as 'today' at close time.",
		observed=True,
	),
	"ReceiptsWithValidationErrors": ErrorCatalogueEntry(
		"ReceiptsWithValidationErrors",
		"Grey",
		"The fiscal day holds at least one receipt with a Red validation error.",
		"The device cannot close this day; only ZIMRA can close/reset it. Use the FDMS test "
		"portal's force-close during development, or contact ZIMRA in production.",
		observed=True,
	),
}

FDMS_ERROR_HINTS: dict[str, str] = {code: entry.cause for code, entry in ERROR_CATALOGUE.items()}


@dataclass
class Explanation:
	code: str
	colour: str
	cause: str
	fix: str
	day_still_closable: bool
	support_code: str


def support_code_for(code: str, operation_id: Optional[str] = None) -> str:
	op = (operation_id or "").split(":")[0][:12].upper()
	return f"{code}-{op}" if op else code


def explain_code(code: str, operation_id: Optional[str] = None) -> Explanation:
	entry = ERROR_CATALOGUE.get(code)
	return Explanation(
		code=code,
		colour=entry.colour if entry else "Unknown",
		cause=entry.cause if entry else "No explanation on file for this code.",
		fix=entry.fix if entry else "Quote the support code to ZIMRA or the zw_compliance maintainers.",
		day_still_closable=(entry.colour != "Red") if entry else True,
		support_code=support_code_for(code, operation_id),
	)


def explain_validation_error(validation_error, operation_id: Optional[str] = None) -> Explanation:
	code = validation_error.validation_error_code or "?"
	ex = explain_code(code, operation_id)
	colour = validation_error.validation_error_color
	if ex.colour == "Unknown" and colour:
		ex = Explanation(
			code=ex.code,
			colour=colour,
			cause=ex.cause,
			fix=ex.fix,
			day_still_closable=colour.lower() != "red",
			support_code=ex.support_code,
		)
	description = validation_error.validation_error_description
	if description and ex.cause.startswith("No explanation"):
		ex = Explanation(
			code=ex.code,
			colour=ex.colour,
			cause=description,
			fix=ex.fix,
			day_still_closable=ex.day_still_closable,
			support_code=ex.support_code,
		)
	return ex


class FdmsApiError(Exception):
	"""Raised for any FDMS HTTP response outside [200, 300)."""

	def __init__(self, status: int, problem: Optional[dict] = None, operation_id: Optional[str] = None):
		self.status = status
		self.problem = problem or {}
		self.operation_id = operation_id
		detail = self.problem.get("detail") or self.problem.get("title")
		error_code = self.problem.get("errorCode")
		parts = [detail or f"FDMS request failed with HTTP {status}"]
		if error_code:
			parts.append(f"(errorCode: {error_code})")
		message = " ".join(parts)
		super().__init__(message)
		self.error_code = error_code

	@property
	def hint(self) -> Optional[str]:
		if self.error_code:
			return FDMS_ERROR_HINTS.get(self.error_code)
		return None

	@property
	def support_code(self) -> str:
		return support_code_for(self.error_code or f"HTTP{self.status}", self.operation_id)

	def explain(self) -> Explanation:
		return explain_code(self.error_code or f"HTTP{self.status}", self.operation_id)


class TransportError(Exception):
	def __init__(self, message: str, cause: Optional[BaseException] = None):
		super().__init__(message)
		self.cause = cause


_NETWORK_ERRNOS = {"ECONNREFUSED", "ECONNRESET", "ENOTFOUND", "ETIMEDOUT", "EAI_AGAIN"}


def is_network_error(err: BaseException) -> bool:
	if isinstance(err, TransportError):
		return True
	code = getattr(err, "errno_name", None) or getattr(err, "code", None)
	if isinstance(code, str) and code in _NETWORK_ERRNOS:
		return True
	message = str(err).lower()
	return "timed out" in message or "network request failed" in message
