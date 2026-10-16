"""Fiscalisation at submit time: the transactional outbox.

When a Sales or POS Invoice is submitted, inside the *same database
transaction* as the invoice:

1. the fiscal device row is locked (``SELECT ... FOR UPDATE``);
2. the receipt is numbered (receiptCounter, receiptGlobalNo), dated, chained to
   the previous receipt's hash and signed with the device key (§13.2.1);
3. its QR code and verification code are derived from the signature (§11), so
   the fiscal receipt can be printed immediately;
4. a *Zimra Fiscal Receipt* is stored with status Queued and the day's
   counters are updated.

Nothing is sent to ZIMRA inside the transaction. After commit, the sync job
(:mod:`.sync`) sends queued receipts one by one in receiptGlobalNo order
(§9 req. 13-14), normally within seconds. If the transaction rolls back for any
reason, the receipt, its number and the counters roll back with it: no number
is ever burned and no receipt reaches ZIMRA without its invoice.
"""

from __future__ import annotations

from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import get_datetime

from zw_compliance.fdms.qr import receipt_qr_data, receipt_qr_url, verification_code
from zw_compliance.fdms.receipt import build_receipt, counters_for, merge_counters
from zw_compliance.fdms.signer import sign
from zw_compliance.fdms.signing import receipt_string
from zw_compliance.fdms.wire import receipt_to_wire

from . import credentials
from .constants import NOT_APPLICABLE, QUEUED, RECEIPT, SETTINGS, VIA_POS
from .day import counters_from_rows, counters_to_rows, day_for_receipt
from .device import device_now, is_vat_payer, lock_device, require_active, resolve_device
from .validation import check_against_original, is_fiscalisable

MIRROR_FIELDS = (
	"zimra_fiscal_device",
	"zimra_fiscal_receipt",
	"zimra_status",
	"zimra_receipt_counter",
	"zimra_receipt_global_no",
	"zimra_fiscal_day_no",
	"zimra_receipt_date",
	"zimra_verification_code",
	"zimra_qr_data",
	"zimra_fdms_receipt_id",
)


def fiscalise(doc, method=None) -> None:
	"""``on_submit`` hook for Sales Invoice and POS Invoice."""
	if not is_fiscalisable(doc):
		doc.db_set("zimra_status", VIA_POS, update_modified=False)
		return
	device_name = resolve_device(doc)
	if not device_name:
		doc.db_set("zimra_status", NOT_APPLICABLE, update_modified=False)
		return
	if frappe.db.exists(RECEIPT, {"reference_doctype": doc.doctype, "reference_name": doc.name}):
		return  # already fiscalised (re-entrant submit)

	from . import builder

	device = lock_device(device_name)
	require_active(device)
	settings = frappe.get_cached_doc(SETTINGS)
	now = device_now(device)
	day = day_for_receipt(device, now)

	# RCPT014 / RCPT030: after the day opened and strictly after the device's
	# previous receipt - in any day. Observed live (zimra-fdms 0.3.0/0.3.1): FDMS
	# rejects same-second receipts, and after a future-dated receipt every later
	# one must still be dated after it, or the day is bricked with a Red RCPT030.
	last_device_receipt = frappe.get_all(
		RECEIPT,
		filters={"fiscal_device": device.name},
		fields=["receipt_date"],
		order_by="receipt_date desc",
		limit=1,
	)
	bounds = [
		get_datetime(v)
		for v in (
			day.fiscal_day_opened,
			day.last_receipt_date,
			last_device_receipt[0].receipt_date if last_device_receipt else None,
		)
		if v
	]
	floor = max(bounds) if bounds else None
	if floor and now <= floor:
		now = floor + timedelta(seconds=1)

	draft = builder.build_draft(doc, device, settings, now)
	receipt = build_receipt(
		draft.receipt,
		counter=(day.receipt_counter or 0) + 1,
		global_no=(device.last_receipt_global_no or 0) + 1,
		date=now,
		is_vat_payer=is_vat_payer(device),
	)
	if draft.original_receipt:
		check_against_original(doc, receipt, draft.original_receipt)

	previous_hash = day.last_receipt_hash if receipt.counter > 1 else None  # §13.2.1 field 8
	receipt.device_signature = sign(
		credentials.private_key(device.name), receipt_string(device.device_id, receipt, previous_hash)
	)
	qr_data = receipt_qr_data(receipt.device_signature.signature)
	payload = receipt_to_wire(receipt)

	record = frappe.get_doc(
		{
			"doctype": RECEIPT,
			"reference_doctype": doc.doctype,
			"reference_name": doc.name,
			"company": doc.company,
			"fiscal_device": device.name,
			"fiscal_day": day.name,
			"fiscal_day_no": day.fiscal_day_no,
			"receipt_type": receipt.receipt_type,
			"receipt_currency": receipt.currency,
			"receipt_total": float(receipt.total),
			"receipt_print_form": receipt.print_form,
			"invoice_no": receipt.invoice_no,
			"receipt_counter": receipt.counter,
			"receipt_global_no": receipt.global_no,
			"receipt_date": receipt.date,
			"qr_data": qr_data,
			"verification_code": verification_code(qr_data),
			"qr_url": receipt_qr_url(
				device.qr_url, device.device_id, receipt.date, receipt.global_no, qr_data
			),
			"sync_status": QUEUED,
			"attempts": 0,
			"previous_receipt_hash": previous_hash,
			"device_signature_hash": receipt.device_signature.hash,
			"device_signature": receipt.device_signature.signature,
			"payload": frappe.as_json(payload, indent=1),
			# Seller block exactly as it must be printed (§10.3 [2]-[8], [21]-[22]).
			"taxpayer_name": device.taxpayer_name,
			"taxpayer_tin": device.taxpayer_tin,
			"vat_number": device.vat_number,
			"branch_name": device.branch_name,
			"branch_address": device.branch_address,
			"branch_email": device.branch_email,
			"branch_phone": device.branch_phone,
			"device_id": device.device_id,
			"device_serial_no": device.serial_no,
			"taxes": [
				{
					"tax_id": t.tax_id,
					"tax_name": draft.mapping_by_tax[t.tax_id].zimra_tax_name
					if t.tax_id in draft.mapping_by_tax
					else None,
					"tax_percent": float(t.tax_percent or 0),
					"is_exempt": int(t.tax_percent is None),
					"tax_amount": float(t.tax_amount),
					"sales_amount_with_tax": float(t.sales_amount_with_tax),
					"net_amount": float(t.sales_amount_with_tax - t.tax_amount),
				}
				for t in receipt.taxes
			],
			"payments": [{"money_type": p.money_type, "amount": float(p.amount)} for p in receipt.payments],
		}
	)
	if draft.original_receipt:
		original = draft.original_receipt
		record.update(
			{
				"original_receipt": original.name,
				"original_invoice_no": original.invoice_no,
				"original_receipt_global_no": original.receipt_global_no,
				"original_receipt_date": original.receipt_date,
				"original_device_serial_no": original.device_serial_no,
			}
		)
	record.flags.ignore_permissions = True
	record.insert()

	day.set(
		"counters", counters_to_rows(merge_counters(counters_from_rows(day.counters), counters_for(receipt)))
	)
	day.receipt_counter = receipt.counter
	day.last_receipt_global_no = receipt.global_no
	day.last_receipt_hash = receipt.device_signature.hash
	day.last_receipt_date = receipt.date
	day.pending_receipts = (day.pending_receipts or 0) + 1
	day.flags.ignore_permissions = True
	day.save()
	device.db_set("last_receipt_global_no", receipt.global_no, update_modified=False)

	doc.db_set(
		{
			"zimra_fiscal_device": device.name,
			"zimra_fiscal_receipt": record.name,
			"zimra_status": QUEUED,
			"zimra_receipt_counter": receipt.counter,
			"zimra_receipt_global_no": receipt.global_no,
			"zimra_fiscal_day_no": day.fiscal_day_no,
			"zimra_receipt_date": receipt.date,
			"zimra_verification_code": record.verification_code,
			"zimra_qr_data": record.qr_url,
		},
		update_modified=False,
	)
	if draft.skipped_lines:
		frappe.msgprint(
			_("Free-of-charge lines are not sent to ZIMRA: {0}").format(", ".join(draft.skipped_lines)),
			alert=True,
		)

	from .sync import enqueue_sync, retry_now

	retry_now(device.name, connection_failures_only=True)
	enqueue_sync(device.name)
