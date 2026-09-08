"""Maps a submitted Sales Invoice / POS Invoice to fdms.types.ReceiptInput.

This is the ERPNext-specific translation layer: tax mapping, HS code
validation, payment-mode mapping, buyer data and return/credit-debit-note
handling. Everything downstream (signing, counters, hash chaining) lives in
zw_compliance.fdms and does not know ERPNext exists.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_months, get_datetime, now_datetime

from zw_compliance.fdms.money import cents
from zw_compliance.fdms.types import BuyerData, CreditDebitNote, PaymentInput, ReceiptInput, ReceiptLineInput

RETURN_WINDOW_MONTHS = 12


class FiscalisationError(frappe.ValidationError):
	pass


def build_receipt_input(doc, settings) -> ReceiptInput:
	_validate_currency(doc, settings)

	receipt_type = "FiscalInvoice"
	credit_debit_note = None
	if doc.is_return:
		receipt_type = "CreditNote" if doc.base_grand_total < 0 else "DebitNote"
		credit_debit_note = _build_credit_debit_note(doc)

	lines = _build_lines(doc, settings)
	lines += _build_discount_lines(doc, lines)

	return ReceiptInput(
		currency=doc.currency,
		invoice_no=doc.name,
		lines=lines,
		payments=_build_payments(doc, settings),
		receipt_type=receipt_type,
		lines_tax_inclusive=True,
		notes=(doc.get("remarks") or None),
		buyer=_build_buyer(doc),
		credit_debit_note=credit_debit_note,
	)


def _validate_currency(doc, settings):
	allowed = [c.strip().upper() for c in (settings.allowed_currencies or "").split(",") if c.strip()]
	if allowed and doc.currency.upper() not in allowed:
		frappe.throw(
			_("{0} is not an allowed ZIMRA fiscalisation currency for this device. Allowed: {1}").format(
				doc.currency, ", ".join(allowed)
			),
			FiscalisationError,
		)


def _tax_mapping_for(doc, item):
	item_template = item.item_tax_template
	doc_template = doc.taxes_and_charges
	if not item_template and not doc_template:
		frappe.throw(
			_("Item {0}: no tax template applied, so it cannot be mapped to a ZIMRA tax code.").format(
				item.item_code
			),
			FiscalisationError,
		)

	mapping = None
	if item_template:
		mapping = frappe.db.get_value(
			"Zimra Tax Mapping",
			{"erpnext_item_tax_template": item_template},
			["zimra_tax_id", "zimra_tax_percent", "zimra_tax_code"],
			as_dict=True,
		)
	if not mapping and doc_template:
		mapping = frappe.db.get_value(
			"Zimra Tax Mapping",
			{"erpnext_tax_template": doc_template},
			["zimra_tax_id", "zimra_tax_percent", "zimra_tax_code"],
			as_dict=True,
		)
	if not mapping:
		frappe.throw(
			_("Item {0}: no ZIMRA Tax Mapping found for tax template {1}.").format(
				item.item_code, item_template or doc_template
			),
			FiscalisationError,
		)
	return mapping


def _hs_code_for(item_code, item_group):
	link = frappe.db.get_value("Item", item_code, "custom_zimra_hsn_code")
	if not link:
		link = frappe.db.get_value("Item Group", item_group, "custom_zimra_hsn_code")
	if not link:
		return None
	return (frappe.db.get_value("Zimra HSN Code", link, "hsn_code") or link).replace(".", "")


def _build_lines(doc, settings) -> list[ReceiptLineInput]:
	is_vat_payer = bool(settings.vat_number)
	lines: list[ReceiptLineInput] = []

	for item in doc.items:
		mapping = _tax_mapping_for(doc, item)
		hs_code = _hs_code_for(item.item_code, item.item_group)

		if is_vat_payer:
			if not hs_code:
				frappe.throw(
					_("Item {0}: a National HS Code is mandatory for VAT-registered operators (RCPT047).").format(
						item.item_code
					),
					FiscalisationError,
				)
			tax_percent = mapping.zimra_tax_percent or 0
			valid_lengths = (8,) if tax_percent == 0 else (4, 8)
			if len(hs_code) not in valid_lengths:
				frappe.throw(
					_("Item {0}: HS Code {1} must be {2} digits for this tax rate (RCPT048).").format(
						item.item_code, hs_code, " or ".join(str(n) for n in valid_lengths)
					),
					FiscalisationError,
				)

		qty = abs(item.qty)
		net_rate = abs(item.base_rate)
		tax_percent = mapping.zimra_tax_percent or 0
		# Receipts are submitted tax-inclusive; ERPNext's item.base_rate is
		# net of tax, so the line price sent to FDMS is grossed up by the
		# mapped ZIMRA tax rate. This assumes a single tax rate per line,
		# which holds for the standard ZIMRA VAT categories (A/B/C/D...).
		gross_rate = net_rate * (1 + tax_percent / 100)

		lines.append(
			ReceiptLineInput(
				name=(item.item_name or item.item_code)[:200],
				price=cents(f"{gross_rate:.2f}"),
				quantity=qty,
				tax_id=mapping.zimra_tax_id,
				tax_percent=mapping.zimra_tax_percent,
				tax_code=mapping.zimra_tax_code,
				hs_code=hs_code,
			)
		)
	return lines


def _line_total_cents(line: ReceiptLineInput) -> int:
	"""Mirrors fdms.device.sign_receipt's own price*quantity rounding, so the
	proration below lines up with the totals the engine will actually compute."""
	price_cents = line.price.cents if hasattr(line.price, "cents") else round(line.price * 100)
	return round(price_cents * line.quantity)


def _proportional_split(total_cents: int, weights: list[int]) -> list[int]:
	"""Split `total_cents` across `weights` proportionally, largest-remainder
	method, so the parts sum to exactly `total_cents` (no cent lost/gained
	to rounding)."""
	weight_sum = sum(weights)
	if weight_sum == 0 or total_cents == 0:
		return [0] * len(weights)
	shares = [total_cents * w // weight_sum for w in weights]
	remainder = total_cents - sum(shares)
	order = sorted(range(len(weights)), key=lambda i: weights[i], reverse=True) or [0]
	step = 1 if remainder > 0 else -1
	i = 0
	while remainder != 0:
		idx = order[i % len(order)]
		shares[idx] += step
		remainder -= step
		i += 1
	return shares


def _build_discount_lines(doc, item_lines: list[ReceiptLineInput]) -> list[ReceiptLineInput]:
	"""ERPNext's "Additional Discount" (apply_discount_on Net/Grand Total) is
	a totals-section adjustment - it never gets written back into item rows,
	so a receipt built purely from doc.items would overstate the total by
	base_discount_amount and the payments-must-equal-total check in
	fdms.device.sign_receipt would fail. Represent it as one negative
	"Discount" line per tax group present on the invoice, prorated by each
	group's share of the pre-discount total (largest-remainder rounding so
	the parts sum exactly), so the receipt total still balances to
	doc.base_grand_total regardless of which tax rates are involved.
	"""
	discount_amount = abs(doc.get("base_discount_amount") or 0)
	if not discount_amount or not item_lines:
		return []
	discount_cents = round(discount_amount * 100)

	groups: dict[tuple, dict] = {}
	order: list[tuple] = []
	for line in item_lines:
		key = (line.tax_id, line.tax_percent, line.tax_code)
		if key not in groups:
			groups[key] = {"weight": 0}
			order.append(key)
		groups[key]["weight"] += _line_total_cents(line)

	weights = [groups[k]["weight"] for k in order]
	shares = _proportional_split(discount_cents, weights)

	discount_lines = []
	for key, share_cents in zip(order, shares):
		if share_cents == 0:
			continue
		tax_id, tax_percent, tax_code = key
		discount_lines.append(
			ReceiptLineInput(
				name=_("Discount"),
				price=cents(f"{-share_cents / 100:.2f}"),
				quantity=1,
				tax_id=tax_id,
				tax_percent=tax_percent,
				tax_code=tax_code,
				hs_code=None,
			)
		)
	return discount_lines


def _build_payments(doc, settings) -> list[PaymentInput]:
	mode_map = {
		row.erpnext_mode: row.zimra_money_type for row in (settings.table_payment_mapping or [])
	}
	doc_payments = doc.get("payments") or []

	if not doc_payments:
		# Invoice on account (no immediate payment recorded) - ZIMRA still
		# requires receiptPayments to sum to receiptTotal.
		return [PaymentInput(money_type="Credit", amount=cents(f"{abs(doc.base_grand_total):.2f}"))]

	payments = []
	for row in doc_payments:
		money_type = mode_map.get(row.mode_of_payment)
		if not money_type:
			frappe.throw(
				_("Mode of Payment {0} has no ZIMRA Payment Mode Mapping configured.").format(row.mode_of_payment),
				FiscalisationError,
			)
		amount = abs(row.get("base_amount") or row.amount)
		payments.append(PaymentInput(money_type=money_type, amount=cents(f"{amount:.2f}")))
	return payments


def _build_buyer(doc) -> BuyerData | None:
	if not doc.customer:
		return None
	customer = frappe.db.get_value(
		"Customer", doc.customer, ["customer_name", "tax_id", "custom_zimra_vat_id"], as_dict=True
	)
	if not customer:
		return None
	return BuyerData(
		buyer_register_name=customer.customer_name,
		buyer_tin=customer.tax_id or None,
		vat_number=customer.custom_zimra_vat_id or None,
	)


def _build_credit_debit_note(doc) -> CreditDebitNote:
	if not doc.return_against:
		frappe.throw(_("A return must reference the original invoice (return_against)."), FiscalisationError)

	original = frappe.db.get_value(
		"Sales Invoice",
		doc.return_against,
		["posting_date", "custom_zimra_receipt_id", "custom_zimra_global_no", "custom_zimra_fiscal_day_no", "custom_zimra_is_fiscalised"],
		as_dict=True,
	)
	if not original or not original.custom_zimra_is_fiscalised:
		frappe.throw(
			_("Original invoice {0} was not found or was never fiscalised with ZIMRA.").format(doc.return_against),
			FiscalisationError,
		)
	if get_datetime(original.posting_date) < add_months(now_datetime(), -RETURN_WINDOW_MONTHS):
		frappe.throw(
			_("Return denied (RCPT033): original invoice {0} is older than {1} months.").format(
				doc.return_against, RETURN_WINDOW_MONTHS
			),
			FiscalisationError,
		)

	return CreditDebitNote(
		receipt_id=int(original.custom_zimra_receipt_id) if original.custom_zimra_receipt_id else None,
		receipt_global_no=original.custom_zimra_global_no,
		fiscal_day_no=original.custom_zimra_fiscal_day_no,
	)
