"""Sales Invoice / POS Invoice → ZIMRA receipt draft.

Mapping rules (spec v7.2 references in brackets):

* **Receipt type**: ``is_debit_note`` → DebitNote, ``is_return`` → CreditNote,
  otherwise FiscalInvoice [§5.4.6]. ERPNext already stores credit notes with
  negative amounts, which is exactly what ZIMRA requires [RCPT022/028/040].
* **Currency and amounts** come from the transaction currency, never the
  company currency (``net_amount``, ``grand_total``, ``payments.amount``).
* **Lines** are tax-inclusive [§4.7]. Each line total is the item's
  ``net_amount`` - which ERPNext has already reduced by item and document
  discounts - grossed up at the line's ZIMRA tax rate. Rounding differences
  against the invoice's grand total are spread across the lines by largest
  remainder, so the receipt total always equals the invoice total.
* **Taxes** are matched through *Zimra Tax Mapping*: the line's Item Tax
  Template first, then the invoice's Sales Taxes and Charges Template, then the
  VAT rate actually charged. The rate ERPNext charged must equal ZIMRA's rate.
* **Payments**: POS payment rows, with change given back to the customer taken
  off cash ("send amount without change", §4.7 Payment); allocated advances and
  any unpaid balance use the money types set in *Zimra Settings*.
* **Buyer** data is sent only for buyers with a TIN [RCPT043].
* **Credit and debit notes** reference the original fiscal receipt [§4.7 CreditDebitNote].
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import flt, get_fullname, getdate

from zw_compliance.fdms.money import q2, split_proportionally, to_decimal
from zw_compliance.fdms.types import (
	Address,
	Buyer,
	Contacts,
	CreditDebitNote,
	LineInput,
	PaymentInput,
	ReceiptDraft,
	UserInfo,
)

from .constants import RECEIPT
from .taxes import company_mappings

ZERO = Decimal(0)
HUNDRED = Decimal(100)
CENT = Decimal("0.01")

MODE_OF_PAYMENT_TYPES = {"Cash": "Cash", "Bank": "BankTransfer", "Phone": "MobileWallet", "General": "Other"}


class FiscalisationError(frappe.ValidationError):
	"""An invoice cannot be fiscalised as entered."""


def fail(message: str) -> None:
	frappe.throw(message, FiscalisationError, title=_("ZIMRA Fiscalisation"))


def dec(value, precision: int = 2) -> Decimal:
	"""A Frappe float field as an exact Decimal at the field's precision."""
	return to_decimal(flt(value, precision))


def receipt_type_of(doc) -> str:
	if doc.get("is_debit_note"):
		return "DebitNote"
	if doc.get("is_return"):
		return "CreditNote"
	return "FiscalInvoice"


@dataclass
class Draft:
	receipt: ReceiptDraft
	original_receipt: object | None = None  # Zimra Fiscal Receipt doc of the credited/debited receipt
	skipped_lines: list[str] = field(default_factory=list)
	mapping_by_tax: dict = field(default_factory=dict)


# -- taxes -------------------------------------------------------------------


class TaxResolver:
	def __init__(self, doc, receipt_date):
		self.doc = doc
		self.receipt_date = getdate(receipt_date)
		self.mappings = company_mappings(doc.company)
		if not self.mappings:
			fail(
				_(
					"Company {0} has no ZIMRA tax mappings. Sync the fiscal device configuration first."
				).format(frappe.bold(doc.company))
			)
		self.by_item_template = {m.item_tax_template: m for m in self.mappings if m.item_tax_template}
		self.by_sales_template = {m.sales_tax_template: m for m in self.mappings if m.sales_tax_template}
		self.output_accounts = {m.output_account for m in self.mappings if m.output_account}
		self.doc_rates = {row.account_head: flt(row.rate) for row in doc.get("taxes") or []}

	def check_document_charges(self) -> None:
		"""Every tax row must be a ZIMRA-mapped VAT row charged on the net total.

		Other charges (freight, levies entered as "Actual" amounts...) are not
		receipt lines, so the receipt could not add up to the invoice total.
		"""
		for row in self.doc.get("taxes") or []:
			if row.account_head not in self.output_accounts:
				if flt(row.tax_amount):
					fail(
						_(
							"Row {0} of Taxes and Charges ({1}) is not a ZIMRA tax. Add such charges as invoice items instead."
						).format(row.idx, row.description or row.account_head)
					)
			elif row.charge_type != "On Net Total":
				fail(
					_(
						"Row {0} of Taxes and Charges: ZIMRA VAT must be charged 'On Net Total', not '{1}'."
					).format(row.idx, row.charge_type)
				)

	def effective_rate(self, item, account: str | None) -> float:
		if not account or account not in self.doc_rates:
			return 0.0
		item_rates = json.loads(item.get("item_tax_rate") or "{}")
		return flt(item_rates.get(account, self.doc_rates[account]))

	def for_item(self, item):
		mapping = None
		if item.get("item_tax_template"):
			mapping = self.by_item_template.get(item.item_tax_template)
		if not mapping and self.doc.get("taxes_and_charges"):
			mapping = self.by_sales_template.get(self.doc.taxes_and_charges)
		if not mapping:
			charged = {self.effective_rate(item, a) for a in self.output_accounts if a in self.doc_rates}
			charged.discard(0.0)
			if len(charged) == 1:
				rate = charged.pop()
				candidates = [
					m
					for m in self.mappings
					if not m.is_exempt and abs(flt(m.zimra_tax_percent) - rate) < 0.0001
				]
				if len(candidates) == 1:
					mapping = candidates[0]
		if not mapping:
			fail(
				_(
					"Row {0} ({1}): cannot tell which ZIMRA tax applies. Give the item an Item Tax Template that is linked in Zimra Tax Mapping."
				).format(item.idx, item.item_name or item.item_code)
			)
		expected = 0.0 if mapping.is_exempt else flt(mapping.zimra_tax_percent)
		charged = self.effective_rate(item, mapping.output_account)
		if abs(charged - expected) > 0.0001:
			fail(
				_("Row {0} ({1}): VAT is charged at {2}% but ZIMRA tax {3} is {4}%.").format(
					item.idx, item.item_name or item.item_code, charged, mapping.zimra_tax_name, expected
				)
			)
		if (mapping.valid_from and getdate(mapping.valid_from) > self.receipt_date) or (
			mapping.valid_till and getdate(mapping.valid_till) < self.receipt_date
		):
			fail(
				_(
					"ZIMRA tax {0} is not valid on {1} (RCPT025). Sync the fiscal device configuration."
				).format(mapping.zimra_tax_name, self.receipt_date)
			)
		return mapping


# -- lines -------------------------------------------------------------------


def hs_code_for(item) -> str | None:
	code = frappe.db.get_value("Item", item.item_code, "zimra_hs_code") if item.get("item_code") else None
	if code:
		return code
	group = item.get("item_group") or (
		frappe.db.get_value("Item", item.item_code, "item_group") if item.get("item_code") else None
	)
	if not group:
		return None
	bounds = frappe.db.get_value("Item Group", group, ["lft", "rgt"], as_dict=True)
	if not bounds:
		return None
	for row in frappe.get_all(
		"Item Group",
		filters={"lft": ["<=", bounds.lft], "rgt": [">=", bounds.rgt], "zimra_hs_code": ["is", "set"]},
		fields=["zimra_hs_code"],
		order_by="lft desc",
		limit=1,
	):
		return row.zimra_hs_code
	return None


def build_lines(doc, resolver: TaxResolver) -> tuple[list[LineInput], list[str], dict]:
	resolver.check_document_charges()
	lines: list[LineInput] = []
	skipped: list[str] = []
	mapping_by_tax = {}
	for item in doc.items:
		mapping = resolver.for_item(item)
		mapping_by_tax[mapping.zimra_tax_id] = mapping
		percent = None if mapping.is_exempt else to_decimal(flt(mapping.zimra_tax_percent, 2))
		gross = q2(dec(item.net_amount) * (HUNDRED + (percent or ZERO)) / HUNDRED)
		name = (item.item_name or item.item_code or "").strip()
		if gross == 0:
			# Free-of-charge lines carry no fiscal value and ZIMRA rejects
			# zero-priced sale lines (RCPT022).
			skipped.append(name)
			continue
		quantity = abs(dec(item.qty, 6)) or Decimal(1)  # zero-qty debit notes (rate adjustments), RCPT023
		lines.append(
			LineInput(
				name=name,
				quantity=quantity,
				total=gross,
				tax_id=mapping.zimra_tax_id,
				tax_percent=percent,
				hs_code=hs_code_for(item),
			)
		)
	if not lines:
		fail(_("The invoice has no items with a value to fiscalise."))

	grand_total = dec(doc.grand_total)
	difference = grand_total - sum((l.total for l in lines), ZERO)
	if difference:
		tolerance = CENT * max(len(lines), 1)
		if abs(difference) > tolerance:
			fail(
				_(
					"Invoice total {0} differs from the ZIMRA line totals by {1}. Check discounts and taxes: ZIMRA receipts cannot carry charges that are not invoice lines."
				).format(grand_total, difference)
			)
		for line, share in zip(
			lines, split_proportionally(difference, [l.total for l in lines]), strict=True
		):
			line.total = q2(line.total + share)
	return lines, skipped, mapping_by_tax


# -- payments ----------------------------------------------------------------


def money_type_for(mode_of_payment: str, settings) -> str:
	for row in settings.get("payment_mode_mapping") or []:
		if row.mode_of_payment == mode_of_payment:
			return row.money_type
	mode_type = frappe.get_cached_value("Mode of Payment", mode_of_payment, "type")
	return MODE_OF_PAYMENT_TYPES.get(mode_type, "Other")


def build_payments(doc, total: Decimal, settings) -> list[PaymentInput]:
	amounts: dict[str, Decimal] = {}

	def add(money_type: str, amount: Decimal) -> None:
		if amount:
			amounts[money_type] = amounts.get(money_type, ZERO) + amount

	if doc.doctype == "POS Invoice" or doc.get("is_pos"):
		rows = [
			(money_type_for(r.mode_of_payment, settings), dec(r.amount)) for r in doc.get("payments") or []
		]
		# Change handed back is not revenue ("send amount without change", §4.7
		# Payment), and neither is cash rounding paid on top of the total
		# (POS Invoice follows Global Defaults rounding; see validation.py).
		change = dec(doc.get("change_amount"))
		for i, (money_type, amount) in enumerate(rows):
			if change > 0 and money_type == "Cash" and amount > 0:
				taken = min(amount, change)
				rows[i] = (money_type, amount - taken)
				change -= taken
		rounding = dec(doc.get("rounding_adjustment"))
		if rounding:
			for i, (money_type, amount) in enumerate(rows):
				if money_type == "Cash" and amount:
					rows[i] = (money_type, amount - rounding)
					break
		for money_type, amount in rows:
			add(money_type, amount)

	remainder = total - sum(amounts.values(), ZERO)
	if remainder:
		advance = ZERO
		if not doc.get("is_return") and (doc.get("party_account_currency") or doc.currency) == doc.currency:
			advance = min(max(dec(doc.get("total_advance")), ZERO), max(remainder, ZERO))
		add(settings.advance_money_type or "BankTransfer", advance)
		add(settings.unpaid_money_type or "Credit", remainder - advance)
	if not amounts:
		amounts[settings.unpaid_money_type or "Credit"] = total
	return [PaymentInput(m, q2(a)) for m, a in amounts.items()]


# -- buyer, notes, references --------------------------------------------------


def _digits(value: str | None) -> str:
	return re.sub(r"[\s\-]", "", value or "")


def buyer_tin(doc) -> str:
	return _digits(doc.get("tax_id") or frappe.db.get_value("Customer", doc.customer, "tax_id"))


def build_buyer(doc) -> Buyer | None:
	tin = buyer_tin(doc)
	if not tin:
		return None  # walk-in / B2C sale: no buyer block (RCPT043)
	customer = (
		frappe.db.get_value("Customer", doc.customer, ["zimra_trade_name", "zimra_vat_number"], as_dict=True)
		or {}
	)
	address = None
	if doc.get("customer_address"):
		a = frappe.db.get_value(
			"Address",
			doc.customer_address,
			["address_line1", "address_line2", "city", "state", "county"],
			as_dict=True,
		)
		if a and a.address_line1 and a.city and (a.state or a.county):
			address = Address(
				province=a.state or a.county,
				city=a.city,
				street=a.address_line1,
				house_no=a.address_line2 or None,
			)
	contacts = None
	if doc.get("contact_email") or doc.get("contact_mobile"):
		contacts = Contacts(
			phone_no=(doc.get("contact_mobile") or None) and doc.contact_mobile[:20],
			email=doc.get("contact_email") or None,
		)
	return Buyer(
		register_name=(doc.customer_name or doc.customer)[:250],
		tin=tin,
		trade_name=(customer.get("zimra_trade_name") or None),
		vat_number=_digits(customer.get("zimra_vat_number")) or None,
		address=address,
		contacts=contacts,
	)


def receipt_notes(doc) -> str | None:
	notes = (doc.get("zimra_receipt_notes") or "").strip()
	if not notes:
		remarks = (doc.get("remarks") or "").strip()
		if remarks and remarks != _("No Remarks") and remarks != "No Remarks":
			notes = remarks
	return notes[:500] or None


def original_receipt(doc):
	"""The fiscal receipt credited or debited by ``doc``."""
	if not doc.get("return_against"):
		fail(_("Credit and debit notes must be made against the original invoice (Return Against)."))
	original = frappe.db.get_value(
		doc.doctype, doc.return_against, ["zimra_fiscal_receipt", "is_consolidated"], as_dict=True
	)
	if not original:
		fail(_("Original invoice {0} was not found.").format(doc.return_against))
	if original.is_consolidated:
		fail(
			_("{0} consolidates POS invoices. Make the return from the original POS Invoice.").format(
				doc.return_against
			)
		)
	if not original.zimra_fiscal_receipt:
		fail(
			_(
				"Original invoice {0} was never fiscalised with ZIMRA, so it cannot be credited or debited."
			).format(doc.return_against)
		)
	return frappe.get_doc(RECEIPT, original.zimra_fiscal_receipt)


def credit_debit_reference(receipt) -> CreditDebitNote:
	return CreditDebitNote(
		receipt_id=int(receipt.fdms_receipt_id) if receipt.fdms_receipt_id else None,
		device_id=receipt.device_id,
		receipt_global_no=receipt.receipt_global_no,
		fiscal_day_no=receipt.fiscal_day_no,
	)


def default_print_form(doc, device) -> str:
	if device.print_form:
		return device.print_form
	return "Receipt48" if doc.doctype == "POS Invoice" or doc.get("is_pos") else "InvoiceA4"


def build_draft(doc, device, settings, receipt_date) -> Draft:
	rtype = receipt_type_of(doc)
	lines, skipped, mapping_by_tax = build_lines(doc, TaxResolver(doc, receipt_date))
	total = sum((l.total for l in lines), ZERO)
	original = original_receipt(doc) if rtype != "FiscalInvoice" else None
	draft = ReceiptDraft(
		receipt_type=rtype,
		currency=doc.currency,
		invoice_no=doc.name,
		lines=lines,
		payments=build_payments(doc, total, settings),
		print_form=default_print_form(doc, device),
		buyer=build_buyer(doc),
		notes=receipt_notes(doc),
		credit_debit_note=credit_debit_reference(original) if original else None,
		user=UserInfo(doc.owner[:100], (get_fullname(doc.owner) or doc.owner)[:250]),
	)
	return Draft(draft, original, skipped, mapping_by_tax)
