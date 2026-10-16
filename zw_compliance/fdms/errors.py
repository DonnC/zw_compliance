"""FDMS error and validation codes (spec §8, §5.4.9, §5.4.11).

``CATALOGUE`` is transcribed from the specification: ``message`` is the
spec's own text; ``colour`` and ``needs_previous`` come from the validation
table in §8.2.1. ``fix`` is this app's plain-language guidance for the person
who has to act on it.
"""

from __future__ import annotations

from dataclasses import dataclass

REQUEST = "request"  # request refused (HTTP 4xx, §8.2)
VALIDATION = "validation"  # receipt accepted but flagged (§8.2.1)
DAY_CLOSE = "day_close"  # FiscalDayProcessingError (§5.4.9)
FILE = "file"  # FileProcessingError (§5.4.11)


@dataclass(frozen=True)
class FdmsCode:
	code: str
	kind: str
	message: str
	fix: str
	colour: str | None = None  # Grey / Yellow / Red, validation codes only
	needs_previous: bool = False  # §8.2.1 "requires previous receipt to be present"


def _v(code, colour, needs_previous, message, fix):
	return FdmsCode(code, VALIDATION, message, fix, colour, needs_previous)


def _r(code, message, fix):
	return FdmsCode(code, REQUEST, message, fix)


_CODES = [
	# §8.2 request errors
	_r(
		"DEV01",
		"Device not found or not active",
		"Check the device ID and that the device is active on the ZIMRA portal.",
	),
	_r(
		"DEV02",
		"Activation key is incorrect",
		"Re-enter the 8-character activation key exactly as issued by ZIMRA.",
	),
	_r(
		"DEV03",
		"Certificate request is invalid",
		"Regenerate the certificate request; the device serial number or ID may not match ZIMRA's records.",
	),
	_r("DEV04", "Device model is blacklisted", "This device model may not be used. Contact ZIMRA."),
	_r("DEV05", "Taxpayer is not active", "The taxpayer registration is not active. Contact ZIMRA."),
	_r(
		"DEV06",
		"Device model and version is not registered in FDMS",
		"Use the device model name and version exactly as registered with ZIMRA.",
	),
	_r("DEV07", "Username is already used", "Choose another username."),
	_r("DEV08", "Provided user information is not correct", "Check the user details sent to FDMS."),
	_r("DEV09", "Security code is not valid", "Request a new security code."),
	_r("DEV10", "Password is not valid", "Use a password that meets ZIMRA's rules."),
	_r("DEV11", "User credentials are incorrect", "Check the username and password."),
	_r("DEV12", "Token is not valid", "Log in again."),
	_r("DEV13", "User is not confirmed", "Confirm the user before logging in."),
	_r("DEV14", "Email or phone number is not valid or doesn't exist", "Check the contact details."),
	_r("DEV15", "Email or phone number already confirmed", "No action needed."),
	_r(
		"RCPT01",
		"Submitting receipt is not allowed. Fiscal day is closed or fiscal day close initiated",
		"Wait for the fiscal day to close, open a new day, then resubmit.",
	),
	_r(
		"RCPT02",
		"Submit receipt failed. The receipt structure invalid or field requirements not satisfied",
		"A receipt field is missing or too long. Contact the system administrator with the support code.",
	),
	_r("FISC01", "Open day is not allowed", "The previous fiscal day is not closed yet. Close it first."),
	_r(
		"FISC03",
		"Closing day is not allowed. Close day is in progress",
		"Wait for the current close to finish.",
	),
	_r("FISC04", "Closing day is not allowed. Fiscal day not opened", "Open a fiscal day first."),
	_r("FILE01", "File is too big. Allowed file size: 3 MB", "Split the file into parts smaller than 3 MB."),
	_r(
		"FILE02", "File structure invalid or field requirements not satisfied", "Regenerate the offline file."
	),
	_r("FILE03", "Device operating mode is not Offline", "File upload is only for devices in Offline mode."),
	_r(
		"FILE04",
		"File sent for already closed day or closing in progress",
		"The day is already closed in FDMS.",
	),
	_r(
		"FILE05",
		"Device id in input parameters and file header are different",
		"Regenerate the offline file for this device.",
	),
	# §8.2.1 receipt validation errors
	_v(
		"RCPT010",
		"Red",
		False,
		"Wrong currency code is used",
		"Use a currency registered in FDMS and valid on the receipt date.",
	),
	_v(
		"RCPT011",
		"Red",
		True,
		"Receipt counter is not sequential.",
		"A receipt in today's sequence is missing; resubmit pending receipts in order.",
	),
	_v(
		"RCPT012",
		"Red",
		True,
		"Receipt global number is not sequential.",
		"A receipt in the device's sequence is missing; resubmit pending receipts in order.",
	),
	_v(
		"RCPT013",
		"Red",
		False,
		"Invoice number is not unique",
		"The invoice number was already used for this taxpayer.",
	),
	_v(
		"RCPT014",
		"Yellow",
		False,
		"Receipt date is earlier than fiscal day opening date",
		"Check the device clock.",
	),
	_v(
		"RCPT015",
		"Red",
		False,
		"Credited/debited invoice data is not provided",
		"Credit and debit notes must reference the original fiscal invoice.",
	),
	_v("RCPT016", "Red", False, "No receipt lines provided", "Add at least one item."),
	_v("RCPT017", "Red", False, "Taxes information is not provided", "Every line needs a ZIMRA tax."),
	_v("RCPT018", "Red", False, "Payment information is not provided", "Record at least one payment."),
	_v(
		"RCPT019",
		"Red",
		False,
		"Invoice total amount is not equal to sum of all invoice lines",
		"Line totals must add up to the invoice total.",
	),
	_v(
		"RCPT020",
		"Red",
		False,
		"Invoice signature is not valid",
		"The device signature does not verify. Contact the system administrator with the support code.",
	),
	_v(
		"RCPT021",
		"Red",
		False,
		"VAT tax is used in invoice while taxpayer is not VAT taxpayer",
		"A non-VAT taxpayer may not charge VAT.",
	),
	_v(
		"RCPT022",
		"Red",
		False,
		"Invoice sales line price must be greater than 0 (less than 0 for Credit note), discount line price must be less than 0 for Invoice",
		"Remove zero-priced lines; check credit note signs.",
	),
	_v(
		"RCPT023",
		"Red",
		False,
		"Invoice line quantity, must be positive",
		"Quantities must be greater than zero.",
	),
	_v(
		"RCPT024",
		"Red",
		False,
		"Invoice line total is not equal to unit price * quantity",
		"Line total must equal price multiplied by quantity.",
	),
	_v(
		"RCPT025",
		"Red",
		False,
		"Invalid tax is used",
		"Use a tax returned by ZIMRA for this device, valid on the receipt date. Re-sync the device configuration.",
	),
	_v(
		"RCPT026",
		"Red",
		False,
		"Incorrectly calculated tax amount",
		"Tax must equal the tax-inclusive total * rate / (100 + rate).",
	),
	_v(
		"RCPT027",
		"Red",
		False,
		"Incorrectly calculated total sales amount (including tax)",
		"Tax table totals must match the lines.",
	),
	_v(
		"RCPT028",
		"Red",
		False,
		"Payment amount must be greater than or equal 0 (less than or equal to 0 for Credit note)",
		"Check payment signs.",
	),
	_v(
		"RCPT029",
		"Red",
		False,
		"Credited/debited invoice information provided for regular invoice",
		"Only credit and debit notes may reference another invoice.",
	),
	_v(
		"RCPT030",
		"Red",
		True,
		"Invoice date is earlier than previously submitted receipt date",
		"Receipt times must always increase; check the device clock.",
	),
	_v(
		"RCPT031",
		"Yellow",
		False,
		"Invoice is submitted with the future date",
		"The device clock is ahead of ZIMRA's; correct the server time.",
	),
	_v(
		"RCPT032",
		"Red",
		False,
		"Credit / debit note refers to non-existing invoice",
		"The original invoice was never accepted by FDMS.",
	),
	_v(
		"RCPT033",
		"Red",
		False,
		"Credited/debited invoice is issued more than 12 months ago",
		"Invoices older than 12 months cannot be credited or debited.",
	),
	_v(
		"RCPT034",
		"Red",
		False,
		"Note for credit/debit note is not provided",
		"Enter the reason for the credit or debit note.",
	),
	_v(
		"RCPT035",
		"Red",
		False,
		"Total credit note amount exceeds original invoice amount",
		"Credits may not exceed the original invoice less earlier credits.",
	),
	_v(
		"RCPT036",
		"Red",
		False,
		"Credit/debit note uses other taxes than are used in the original invoice",
		"Only taxes on the original invoice may be used.",
	),
	_v(
		"RCPT037",
		"Red",
		False,
		"Invoice total amount is not equal to sum of all invoice lines and taxes applied",
		"Exclusive line totals plus tax must equal the total.",
	),
	_v(
		"RCPT038",
		"Red",
		False,
		"Invoice total amount is not equal to sum of sales amount including tax in tax table",
		"Tax table totals must add up to the invoice total.",
	),
	_v(
		"RCPT039",
		"Red",
		False,
		"Invoice total amount is not equal to sum of all payment amounts",
		"Payments must add up to the invoice total.",
	),
	_v(
		"RCPT040",
		"Red",
		False,
		"Invoice total amount must be greater than or equal to 0 (less than or equal to 0 for Credit note)",
		"Check the invoice total sign.",
	),
	_v(
		"RCPT041",
		"Yellow",
		False,
		"Invoice is issued after fiscal day end",
		"Close the fiscal day before its maximum length is reached.",
	),
	_v(
		"RCPT042",
		"Red",
		False,
		"Credit/debit note uses other currency than is used in the original invoice",
		"Use the original invoice's currency.",
	),
	_v(
		"RCPT043",
		"Red",
		False,
		"Mandatory buyer data fields are not provided",
		"Buyer data needs both the registered name and the TIN.",
	),
	_v(
		"RCPT047",
		"Red",
		False,
		"HS code must be sent if taxpayer is a VAT payer",
		"Set a ZIMRA HS code on the item or its item group.",
	),
	_v(
		"RCPT048",
		"Red",
		False,
		"HS code length must be 4 or 8 digits if taxpayer is not VAT payer, 4 or 8 digits if taxpayer is VAT payer and applied tax percent is bigger than 0, 8 digits if taxpayer is VAT payer and applied tax percent is equal to 0 or is empty",
		"Use an 8-digit HS code for zero-rated and exempt items.",
	),
	# §5.4.9 fiscal day processing errors
	FdmsCode(
		"BadCertificateSignature",
		DAY_CLOSE,
		"Close day is not allowed. Bad certificate signature is used.",
		"The day report signature does not verify. Contact the system administrator.",
	),
	FdmsCode(
		"MissingReceipts",
		DAY_CLOSE,
		'Close day is not allowed. There are missing receipts in fiscal day ("Grey" validation error).',
		"Submit the missing receipts, then close again.",
	),
	FdmsCode(
		"ReceiptsWithValidationErrors",
		DAY_CLOSE,
		'Close day is not allowed. There are receipts with validation errors in fiscal day ("Red" validation error).',
		"Red receipts block automatic closing; ZIMRA or the supplier portal must close the day.",
	),
	FdmsCode(
		"CountersMismatch",
		DAY_CLOSE,
		"Close day is not allowed. There are mismatches between counters.",
		"Day totals differ from FDMS's; recompute counters and resubmit the close.",
	),
	# §5.4.11 file processing errors
	FdmsCode(
		"IncorrectFileFormat",
		FILE,
		"File structure is incorrect, elements sequence is incorrect.",
		"Regenerate the offline file.",
	),
	FdmsCode(
		"FileSentForClosedDay", FILE, "Fiscal day for which file is sent is closed.", "No action needed."
	),
	FdmsCode(
		"FileExceededAllowedWaitingTime",
		FILE,
		"Previous file in a sequence is missing, and maximum allowed waiting time for previous file to be received has exceeded.",
		"Resend the missing file.",
	),
]

CATALOGUE: dict[str, FdmsCode] = {c.code: c for c in _CODES}


def describe(code: str | None) -> FdmsCode | None:
	return CATALOGUE.get(code or "")


def support_code(code: str | None, operation_id: str | None) -> str:
	"""Short reference a user can quote to ZIMRA support: ``RCPT020-0HNOABB4T00A``."""
	op = (operation_id or "").split(":")[0][:12].upper()
	label = code or "FDMS"
	return f"{label}-{op}" if op else label


class FdmsError(Exception):
	"""Base class for every error raised by this package."""


class TransportError(FdmsError):
	"""Network-level failure: FDMS could not be reached or did not answer."""

	retryable = True


class FdmsApiError(FdmsError):
	"""FDMS answered with an HTTP error (RFC 7807 ProblemDetails, §8)."""

	def __init__(self, status: int, problem: dict | None = None, operation_id: str | None = None):
		self.status = status
		self.problem = problem or {}
		self.operation_id = operation_id or self.problem.get("operationID")
		self.error_code = self.problem.get("errorCode")
		entry = describe(self.error_code)
		text = self.problem.get("detail") or self.problem.get("title") or (entry.message if entry else None)
		super().__init__(
			f"FDMS HTTP {status}"
			+ (f" {self.error_code}" if self.error_code else "")
			+ (f": {text}" if text else "")
		)

	@property
	def retryable(self) -> bool:
		"""5xx/502: "The fiscal device should retry to send message later." (§8.1)."""
		return self.status >= 500 or self.status in (408, 429)

	@property
	def is_authentication_error(self) -> bool:
		"""401: certificate not issued by FDMS, revoked, expired or for another device (§7.3)."""
		return self.status == 401

	@property
	def support_code(self) -> str:
		return support_code(self.error_code or f"HTTP{self.status}", self.operation_id)

	@property
	def fix(self) -> str | None:
		entry = describe(self.error_code)
		if entry:
			return entry.fix
		if self.is_authentication_error:
			return "The device certificate is invalid, expired or revoked. Renew or re-register the device."
		return None


class ReceiptValidationError(FdmsError):
	"""A receipt would be flagged by FDMS; it is refused before signing."""

	def __init__(self, code: str, detail: str):
		self.code = code
		self.detail = detail
		super().__init__(f"{code}: {detail}")
