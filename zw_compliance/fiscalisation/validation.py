"""Checks run before a Sales/POS Invoice is submitted.

The principle: an invoice that ZIMRA would flag must never be submitted.
``before_submit`` builds the exact receipt the invoice will produce and runs
every locally checkable FDMS rule over it (see
:func:`zw_compliance.fdms.receipt.validate_receipt`), plus the rules that need
ERPNext data: allowed currency, buyer TIN format, and the credit/debit note
rules against the original receipt (RCPT033, RCPT035, RCPT036, RCPT042).
Problems are reported in business language while the user can still fix them.
"""

from __future__ import annotations

import re
from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import add_months, flt, get_datetime

from zw_compliance.fdms.errors import ReceiptValidationError, describe
from zw_compliance.fdms.receipt import build_receipt

from . import builder
from .constants import DEVICE, RECEIPT, REJECTED, SETTINGS
from .device import device_now, is_vat_payer, require_active, resolve_device, warn_if_certificate_expired

ZERO = Decimal(0)


def is_fiscalisable(doc) -> bool:
	"""False for documents ZIMRA must not receive a second time: consolidated
	POS invoices (and their consolidated returns) were fiscalised as POS Invoices."""
	return not doc.get("is_consolidated")


def allowed_currencies(settings) -> list[str]:
	return [c.strip().upper() for c in (settings.allowed_currencies or "").split(",") if c.strip()]


def validate_invoice(doc, method=None) -> None:
	"""``validate`` hook: cheap normalisation on every save."""
	if not is_fiscalisable(doc) or not frappe.db.exists(
		DEVICE, {"company": doc.company, "status": ["!=", "Disabled"]}
	):
		return
	# Receipt total must equal the line totals (RCPT019); ERPNext's cash rounding
	# would add an amount that is not a receipt line, so it is switched off. This
	# hook runs after ERPNext has computed the totals, hence the recalculation.
	# POS Invoice has no such field (it follows Global Defaults); its rounding is
	# absorbed into the cash payment instead, see builder.build_payments.
	if doc.meta.get_field("disable_rounded_total") and (
		flt(doc.get("rounding_adjustment")) or not doc.get("disable_rounded_total")
	):
		doc.disable_rounded_total = 1
		doc.calculate_taxes_and_totals()


def before_submit(doc, method=None) -> None:
	if not is_fiscalisable(doc):
		return
	device_name = resolve_device(doc)
	if not device_name:
		return
	check_invoice(doc, frappe.get_doc(DEVICE, device_name))


def check_invoice(doc, device) -> builder.Draft:
	"""Raise if ``doc`` cannot be fiscalised on ``device``; return the draft."""
	require_active(device)
	warn_if_certificate_expired(device)
	settings = frappe.get_cached_doc(SETTINGS)
	currencies = allowed_currencies(settings)
	if doc.currency.upper() not in currencies:
		builder.fail(
			_("{0} is not a ZIMRA fiscal currency for this business. Allowed: {1} (Zimra Settings).").format(
				frappe.bold(doc.currency), ", ".join(currencies)
			)
		)
	if doc.get("is_cash_or_non_trade_discount") and doc.get("discount_amount"):
		builder.fail(
			_(
				"A cash / non-trade discount lowers the total without lowering VAT, which a ZIMRA receipt cannot express. Apply the discount on Net Total instead."
			)
		)
	tin = builder.buyer_tin(doc)
	if tin and not re.fullmatch(r"\d{10}", tin):
		builder.fail(_("Customer TIN {0} must be 10 digits (ZIMRA RCPT043).").format(frappe.bold(tin)))
	vat = (
		builder._digits(frappe.db.get_value("Customer", doc.customer, "zimra_vat_number"))
		if doc.customer
		else ""
	)
	if vat and not re.fullmatch(r"\d{9}", vat):
		builder.fail(_("Customer VAT number {0} must be 9 digits.").format(frappe.bold(vat)))

	now = device_now(device)
	draft = builder.build_draft(doc, device, settings, now)
	try:
		receipt = build_receipt(
			draft.receipt, counter=1, global_no=1, date=now, is_vat_payer=is_vat_payer(device)
		)
	except ReceiptValidationError as e:
		entry = describe(e.code)
		builder.fail(
			_("ZIMRA would reject this invoice ({0}): {1}.").format(e.code, e.detail)
			+ (f"<br><br>{entry.fix}" if entry and entry.fix else "")
		)
	if draft.original_receipt:
		check_against_original(doc, receipt, draft.original_receipt)
	return draft


def check_against_original(doc, receipt, original) -> None:
	"""Credit/debit note rules that need the original receipt."""
	if original.receipt_currency != receipt.currency:
		builder.fail(
			_("A credit or debit note must use the original invoice's currency {0} (RCPT042).").format(
				original.receipt_currency
			)
		)
	if get_datetime(original.receipt_date) < add_months(receipt.date, -12):
		builder.fail(
			_(
				"The original invoice is more than 12 months old and can no longer be credited or debited (RCPT033)."
			)
		)
	original_taxes = {
		(t.tax_id, Decimal(str(t.tax_percent)) if not t.is_exempt else None) for t in original.taxes
	}
	for tax in receipt.taxes:
		key = (tax.tax_id, None if tax.tax_percent is None else Decimal(str(tax.tax_percent)))
		if key not in original_taxes:
			builder.fail(
				_(
					"ZIMRA tax {0} was not on the original invoice; a credit or debit note may only use the original's taxes (RCPT036)."
				).format(tax.tax_id)
			)
	if receipt.receipt_type == "CreditNote":
		history = frappe.get_all(
			RECEIPT,
			filters={"original_receipt": original.name, "sync_status": ["!=", REJECTED]},
			fields=["receipt_type", "receipt_total"],
		)
		balance = Decimal(str(original.receipt_total))
		for h in history:
			balance += Decimal(str(h.receipt_total))  # credit notes are negative, debit notes positive
		if balance + receipt.total < 0:
			builder.fail(
				_(
					"This credit note ({0}) exceeds what is left to credit on the original invoice ({1}) (RCPT035)."
				).format(-receipt.total, balance)
			)
