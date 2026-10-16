"""Remote fiscalisation API, v1.

For clients that sell outside the ERPNext desk - a web shop, a mobile app, an
external POS - and need ZIMRA fiscal receipts. A sale sent here becomes an
ordinary submitted **Sales Invoice** (so accounting, stock and permissions
work exactly as in the desk) and is fiscalised by the same transactional
outbox. The response carries what a client must print or show: the fiscal
numbers, the verification code and the QR content.

Endpoints (POST, JSON body, token or session authentication)::

    zw_compliance.api.v1.fiscalisation.fiscalise_invoice
    zw_compliance.api.v1.fiscalisation.create_credit_note
    zw_compliance.api.v1.fiscalisation.get_receipt          (GET also accepted)
    zw_compliance.api.v1.fiscalisation.get_device_status    (GET also accepted)

Idempotency: every write takes a ``client_reference`` unique per sale. Sending
the same reference again returns the already-fiscalised document instead of
creating a second one (``"replayed": true``), so a client may safely retry
after a timeout.

See ``docs/API.md`` for full request and response examples.
"""

from __future__ import annotations

from urllib.parse import urlencode

import frappe
from frappe import _
from frappe.utils import flt

from zw_compliance.api.response import ApiError, endpoint
from zw_compliance.fiscalisation.constants import DEVICE, RECEIPT, UNSENT

INVOICE = "Sales Invoice"


# -- serialisers -----------------------------------------------------------------


def _iso(value) -> str | None:
	return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else None)


def _print_url(invoice) -> str:
	fmt = "ZIMRA Receipt48 Sales Invoice" if invoice.is_pos else "ZIMRA Invoice A4"
	return "/api/method/frappe.utils.print_format.download_pdf?" + urlencode(
		{"doctype": INVOICE, "name": invoice.name, "format": fmt}
	)


def receipt_data(invoice, replayed: bool = False) -> dict:
	"""The minimal, uniform receipt representation returned by every endpoint."""
	receipt = frappe.get_doc(RECEIPT, invoice.zimra_fiscal_receipt) if invoice.zimra_fiscal_receipt else None
	data = {
		"invoice": invoice.name,
		"client_reference": invoice.get("zimra_client_reference"),
		"status": invoice.zimra_status,
		"currency": invoice.currency,
		"total": flt(invoice.grand_total, 2),
		"replayed": replayed,
	}
	if receipt:
		data.update(
			{
				"receipt_type": receipt.receipt_type,
				"fiscal_day_no": receipt.fiscal_day_no,
				"receipt_counter": receipt.receipt_counter,
				"receipt_global_no": receipt.receipt_global_no,
				"receipt_date": _iso(receipt.receipt_date),
				"verification_code": receipt.verification_code,
				"qr_code": receipt.qr_url,
				"zimra_receipt_id": receipt.fdms_receipt_id or None,
				"validation_errors": [
					{"code": e.code, "colour": e.colour, "message": e.description}
					for e in receipt.validation_errors
				],
				"print_url": _print_url(invoice),
			}
		)
	return data


# -- helpers -----------------------------------------------------------------


def _find(client_reference: str):
	name = frappe.db.get_value(INVOICE, {"zimra_client_reference": client_reference, "docstatus": 1})
	return frappe.get_doc(INVOICE, name) if name else None


def _require(value, field: str):
	if value in (None, "", []):
		raise ApiError("MISSING_FIELD", _("{0} is required.").format(field))
	return value


def _parse_list(value, field: str) -> list[dict]:
	if isinstance(value, str):
		value = frappe.parse_json(value)
	if value is None:
		return []
	if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
		raise ApiError("INVALID_FIELD", _("{0} must be a list of objects.").format(field))
	return value


def _check_reference(client_reference: str) -> str:
	ref = str(_require(client_reference, "client_reference")).strip()
	if len(ref) > 140:
		raise ApiError("INVALID_FIELD", _("client_reference is limited to 140 characters."))
	return ref


# -- endpoints -----------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
@endpoint
def fiscalise_invoice(
	client_reference: str,
	items: list | str,
	customer: str | None = None,
	company: str | None = None,
	pos_profile: str | None = None,
	currency: str | None = None,
	payments: list | str | None = None,
	taxes_and_charges: str | None = None,
	notes: str | None = None,
) -> dict:
	"""Create, submit and fiscalise a Sales Invoice.

	``items``: ``[{"item_code": str, "qty": number, "rate": number (optional,
	price list rate if omitted), "item_tax_template": str (optional)}]``.
	``payments``: ``[{"mode_of_payment": str, "amount": number}]``; omit for a
	sale on account. With payments, a ``pos_profile`` is required.
	"""
	ref = _check_reference(client_reference)
	if existing := _find(ref):
		return receipt_data(existing, replayed=True)

	lines = _parse_list(items, "items")
	if not lines:
		raise ApiError("MISSING_FIELD", _("items is required."))
	paid = _parse_list(payments, "payments")

	invoice = frappe.new_doc(INVOICE)
	if pos_profile:
		profile = frappe.get_cached_doc("POS Profile", pos_profile)
		company = company or profile.company
		customer = customer or profile.customer
		currency = currency or profile.currency
	elif paid:
		raise ApiError("MISSING_FIELD", _("pos_profile is required when payments are sent."))
	invoice.company = _require(company or frappe.defaults.get_user_default("Company"), "company")
	invoice.customer = _require(customer, "customer")
	# explicit, else the customer's billing currency, else the company's - never
	# a price list's, which may differ from the receivable account's currency
	invoice.currency = (
		currency
		or frappe.db.get_value("Customer", invoice.customer, "default_currency")
		or frappe.get_cached_value("Company", invoice.company, "default_currency")
	)
	invoice.zimra_client_reference = ref
	invoice.zimra_receipt_notes = notes
	if pos_profile:
		invoice.pos_profile = pos_profile
		invoice.is_pos = 1
	for row in lines:
		invoice.append(
			"items",
			{
				"item_code": _require(row.get("item_code"), "items[].item_code"),
				"qty": flt(_require(row.get("qty"), "items[].qty")),
				"item_tax_template": row.get("item_tax_template"),
			},
		)
	invoice.set_missing_values()
	for doc_row, row in zip(invoice.items, lines, strict=True):
		if row.get("rate") is not None:
			doc_row.price_list_rate = doc_row.rate = flt(row["rate"])
			doc_row.discount_percentage = doc_row.discount_amount = 0
	if taxes_and_charges:
		invoice.taxes_and_charges = taxes_and_charges
		invoice.set("taxes", [])
	invoice.set_taxes()
	if paid:
		invoice.set("payments", [])
		for p in paid:
			invoice.append(
				"payments",
				{
					"mode_of_payment": _require(p.get("mode_of_payment"), "payments[].mode_of_payment"),
					"amount": flt(p.get("amount")),
				},
			)
	invoice.calculate_taxes_and_totals()
	invoice.insert()
	invoice.submit()
	return receipt_data(invoice)


@frappe.whitelist(methods=["POST"])
@endpoint
def create_credit_note(
	invoice: str,
	client_reference: str,
	reason: str,
	items: list | str | None = None,
	refund_mode_of_payment: str | None = None,
) -> dict:
	"""Credit (return) a fiscalised Sales Invoice, in full or in part.

	``items``: ``[{"item_code": str, "qty": number}]`` with positive quantities
	to return; omit to return everything. ``reason`` is printed on the credit
	note and sent to ZIMRA (mandatory, RCPT034).
	"""
	from erpnext.controllers.sales_and_purchase_return import make_return_doc

	ref = _check_reference(client_reference)
	if existing := _find(ref):
		return receipt_data(existing, replayed=True)
	original = frappe.get_doc(INVOICE, invoice)
	original.check_permission("read")
	if not original.zimra_fiscal_receipt:
		raise ApiError("NOT_FISCALISED", _("Invoice {0} has not been fiscalised.").format(invoice))

	note = make_return_doc(INVOICE, invoice)
	wanted = {r["item_code"]: flt(r.get("qty")) for r in _parse_list(items, "items")}
	if wanted:
		unknown = set(wanted) - {r.item_code for r in note.items}
		if unknown:
			raise ApiError(
				"INVALID_FIELD", _("Not on the original invoice: {0}").format(", ".join(sorted(unknown)))
			)
		kept = []
		for row in note.items:
			if row.item_code in wanted and wanted[row.item_code] > 0:
				take = min(wanted[row.item_code], abs(row.qty))
				wanted[row.item_code] -= take
				row.qty = -take
				kept.append(row)
		note.set("items", kept)
	note.zimra_client_reference = ref
	note.zimra_receipt_notes = _require(reason, "reason")
	note.calculate_taxes_and_totals()
	if note.is_pos:
		mode = refund_mode_of_payment or (note.payments[0].mode_of_payment if note.payments else None)
		note.set(
			"payments",
			[{"mode_of_payment": _require(mode, "refund_mode_of_payment"), "amount": note.grand_total}],
		)
		note.calculate_taxes_and_totals()
	note.insert()
	note.submit()
	return receipt_data(note)


@frappe.whitelist(methods=["GET", "POST"])
@endpoint
def get_receipt(invoice: str | None = None, client_reference: str | None = None) -> dict:
	"""Fiscal receipt of a Sales Invoice, by name or by client reference.

	Poll this until ``status`` is ``Submitted`` when the first answer was
	``Queued`` (ZIMRA is normally reached within seconds)."""
	if client_reference:
		doc = _find(str(client_reference).strip())
		if not doc:
			raise ApiError(
				"NOT_FOUND", _("No invoice with client_reference {0}.").format(client_reference), 404
			)
	else:
		doc = frappe.get_doc(INVOICE, _require(invoice, "invoice or client_reference"))
	doc.check_permission("read")
	return receipt_data(doc)


@frappe.whitelist(methods=["GET", "POST"])
@endpoint
def get_device_status(company: str | None = None) -> list[dict]:
	"""Readiness of each fiscal device: can it fiscalise now, and is anything waiting?"""
	filters = {"company": company} if company else {}
	out = []
	for d in frappe.get_list(
		DEVICE,
		filters=filters,
		fields=[
			"name",
			"device_name",
			"company",
			"status",
			"current_fiscal_day",
			"last_sync",
			"last_sync_error",
			"certificate_valid_till",
		],
	):
		day = (
			frappe.db.get_value(
				"Zimra Fiscal Day", d.current_fiscal_day, ["fiscal_day_no", "status"], as_dict=True
			)
			if d.current_fiscal_day
			else None
		)
		out.append(
			{
				"device": d.name,
				"name": d.device_name,
				"company": d.company,
				"status": d.status,
				"fiscal_day_no": day.fiscal_day_no if day else None,
				"fiscal_day_status": day.status if day else None,
				"waiting_for_zimra": frappe.db.count(
					RECEIPT, {"fiscal_device": d.name, "sync_status": ["in", list(UNSENT)]}
				),
				"rejected": frappe.db.count(RECEIPT, {"fiscal_device": d.name, "sync_status": "Rejected"}),
				"last_sync": _iso(d.last_sync),
				"last_sync_error": d.last_sync_error,
				"certificate_valid_till": _iso(d.certificate_valid_till),
			}
		)
	return out
