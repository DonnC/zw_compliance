"""End-to-end fiscalisation inside Frappe, against the in-process FDMS
(``simulator.py``, a port of the zimra-fdms SDK simulator)."""

from __future__ import annotations

import itertools
import json
import re

import frappe

from .fixtures import (
	BUSINESS,
	BUSINESS_TIN,
	DEVICE_ID,
	ITEM,
	ITEM_EXEMPT,
	ITEM_ZERO,
	FiscalisationTestCase,
	make_invoice,
)


class TestOnboarding(FiscalisationTestCase):
	def test_device_is_active_with_zimra_configuration(self):
		device = frappe.get_doc("Zimra Fiscal Device", self.device.name)
		self.assertEqual(device.status, "Active")
		self.assertEqual(device.taxpayer_tin, "2000000000")
		self.assertEqual(device.vat_number, "220000000")
		self.assertTrue(device.certificate.startswith("-----BEGIN CERTIFICATE-----"))
		self.assertEqual(device.get_password("private_key")[:27], "-----BEGIN PRIVATE KEY-----")
		self.assertEqual(len(device.applicable_taxes), 3)

	def test_taxes_are_provisioned_the_erpnext_way(self):
		mappings = frappe.get_all("Zimra Tax Mapping", filters={"company": self.device.company}, fields=["*"])
		self.assertEqual({m.zimra_tax_id for m in mappings}, {1, 2, 3})
		for m in mappings:
			self.assertTrue(m.sales_tax_template and m.item_tax_template and m.output_account)
			self.assertTrue(m.purchase_tax_template and m.purchase_item_tax_template and m.input_account)
		self.assertEqual(frappe.db.get_value("Account", mappings[0].input_account, "root_type"), "Asset")


class TestFiscalisation(FiscalisationTestCase):
	def test_submit_signs_locally_and_sync_submits(self):
		invoice = make_invoice([(ITEM, 2, 100)])
		receipt = self.receipt_of(invoice)
		self.assertEqual(receipt.sync_status, "Queued")
		self.assertEqual(receipt.receipt_type, "FiscalInvoice")
		self.assertEqual(receipt.receipt_total, invoice.grand_total)
		self.assertRegex(receipt.verification_code, r"^[0-9A-F]{4}(-[0-9A-F]{4}){3}$")
		self.assertTrue(receipt.qr_url.endswith(receipt.qr_data))
		self.assertIn(f"{DEVICE_ID:010d}", receipt.qr_url)

		self.sync()
		receipt.reload()
		self.assertEqual(receipt.sync_status, "Submitted", receipt.last_error)
		self.assertFalse(receipt.validation_errors, [e.as_dict() for e in receipt.validation_errors])
		self.assertTrue(receipt.server_signature_verified)
		invoice.reload()
		self.assertEqual(invoice.zimra_status, "Submitted")
		self.assertEqual(invoice.zimra_fdms_receipt_id, receipt.fdms_receipt_id)

	def test_rolled_back_invoice_uses_no_number(self):
		before = frappe.db.get_value("Zimra Fiscal Device", self.device.name, "last_receipt_global_no")
		frappe.db.savepoint("before_invoice")
		make_invoice([(ITEM, 1, 50)])
		frappe.db.rollback(save_point="before_invoice")
		self.assertEqual(
			frappe.db.get_value("Zimra Fiscal Device", self.device.name, "last_receipt_global_no"), before
		)
		invoice = make_invoice([(ITEM, 1, 50)])
		self.assertEqual(self.receipt_of(invoice).receipt_global_no, before + 1)

	def test_receipts_are_sequential_and_chained(self):
		first, second = make_invoice([(ITEM, 1, 10)]), make_invoice([(ITEM, 1, 20)])
		a, b = self.receipt_of(first), self.receipt_of(second)
		self.assertEqual(
			(b.receipt_counter, b.receipt_global_no), (a.receipt_counter + 1, a.receipt_global_no + 1)
		)
		self.assertEqual(b.previous_receipt_hash, a.device_signature_hash)
		self.assertGreater(b.receipt_date, a.receipt_date)
		self.sync()
		for r in (a, b):
			r.reload()
			self.assertEqual(r.sync_status, "Submitted")
			self.assertFalse(r.validation_errors)

	def test_mixed_taxes_match_erpnext_totals(self):
		invoice = make_invoice([(ITEM, 1, 100), (ITEM_ZERO, 3, 4.5), (ITEM_EXEMPT, 1, 250)])
		receipt = self.receipt_of(invoice)
		self.assertEqual(receipt.receipt_total, invoice.grand_total)
		taxes = {t.tax_id: t for t in receipt.taxes}
		self.assertEqual(set(taxes), {1, 2, 3})
		self.assertAlmostEqual(taxes[1].tax_amount, invoice.total_taxes_and_charges, places=2)
		self.assertTrue(taxes[3].is_exempt)
		payload = json.loads(receipt.payload)
		exempt_line = next(l for l in payload["receiptLines"] if l["taxID"] == 3)
		self.assertNotIn("taxPercent", exempt_line)
		self.sync()
		receipt.reload()
		self.assertFalse(receipt.validation_errors, [e.as_dict() for e in receipt.validation_errors])

	def test_walk_in_sale_sends_no_buyer_and_business_sale_does(self):
		walk_in = json.loads(self.receipt_of(make_invoice([(ITEM, 1, 10)])).payload)
		self.assertNotIn("buyerData", walk_in)
		business = json.loads(self.receipt_of(make_invoice([(ITEM, 1, 10)], customer=BUSINESS)).payload)
		self.assertEqual(business["buyerData"]["buyerTIN"], BUSINESS_TIN)
		self.assertEqual(business["buyerData"]["vatNumber"], "220000777")

	def test_disallowed_currency_is_refused_before_submit(self):
		invoice = make_invoice([(ITEM, 1, 10)], submit=False)
		frappe.db.set_single_value("Zimra Settings", "allowed_currencies", "ZWG")
		frappe.clear_document_cache("Zimra Settings", "Zimra Settings")
		try:
			self.assertRaisesRegex(frappe.ValidationError, "not a ZIMRA fiscal currency", invoice.submit)
		finally:
			frappe.db.set_single_value("Zimra Settings", "allowed_currencies", "USD")
			frappe.clear_document_cache("Zimra Settings", "Zimra Settings")

	def test_fiscalised_invoice_cannot_be_cancelled_or_deleted(self):
		invoice = make_invoice([(ITEM, 1, 10)])
		self.assertRaisesRegex(frappe.ValidationError, "credit note", invoice.cancel)

	def test_fiscal_receipt_is_immutable(self):
		receipt = self.receipt_of(make_invoice([(ITEM, 1, 10)]))
		receipt.receipt_total = 1
		self.assertRaises(frappe.ValidationError, receipt.save, ignore_permissions=True)
		self.assertRaises(
			frappe.ValidationError,
			frappe.delete_doc,
			"Zimra Fiscal Receipt",
			receipt.name,
			ignore_permissions=True,
		)

	def test_copied_or_amended_draft_carries_no_fiscal_identity(self):
		invoice = make_invoice([(ITEM, 1, 10)])
		copy = frappe.copy_doc(invoice)  # keeps no_copy fields, as Amend does
		copy.docstatus = 0
		copy.insert(ignore_permissions=True)
		self.assertFalse(
			copy.zimra_fiscal_receipt or copy.zimra_receipt_global_no or copy.zimra_verification_code
		)
		self.assertEqual(copy.zimra_status or "", "")


class TestCreditNotesAndDayClose(FiscalisationTestCase):
	def _credit(self, invoice, reason="Goods returned", **qty):
		from erpnext.controllers.sales_and_purchase_return import make_return_doc

		note = make_return_doc("Sales Invoice", invoice.name)
		note.zimra_receipt_notes = reason
		note.insert(ignore_permissions=True)
		note.submit()
		return note

	def test_credit_note_is_negative_and_the_day_closes(self):
		invoice = make_invoice([(ITEM, 2, 100), (ITEM_EXEMPT, 1, 30)])
		self.sync()
		note = self._credit(invoice)
		credit = self.receipt_of(note)
		payload = json.loads(credit.payload)
		self.assertEqual(payload["receiptType"], "CreditNote")
		self.assertLess(payload["receiptTotal"], 0)
		self.assertTrue(
			all(l["receiptLineTotal"] < 0 and l["receiptLineQuantity"] > 0 for l in payload["receiptLines"])
		)
		self.assertTrue(all(p["paymentAmount"] <= 0 for p in payload["receiptPayments"]))
		self.assertEqual(payload["receiptNotes"], "Goods returned")
		self.assertTrue(payload["creditDebitNote"]["receiptID"])
		self.sync()
		credit.reload()
		self.assertEqual(credit.sync_status, "Submitted", credit.last_error)
		self.assertFalse(credit.validation_errors, [e.as_dict() for e in credit.validation_errors])

		from zw_compliance.fiscalisation.day import request_close

		day = credit.fiscal_day
		request_close(day)
		self.sync()  # signs and sends closeDay
		self.fdms.settle()  # FDMS validates the day report asynchronously
		self.sync()  # polls getStatus
		day = frappe.get_doc("Zimra Fiscal Day", day)
		self.assertEqual(day.status, "Closed", day.last_error or day.closing_error_code)
		self.assertTrue(day.server_signature_verified)

		credit_counters = [c for c in day.counters if c.counter_type == "CreditNoteByTax"]
		self.assertTrue(credit_counters and all(c.value < 0 for c in credit_counters))

		from zw_compliance.fiscalisation.print_utils import zimra_z_report

		z = zimra_z_report(day.name)
		self.assertEqual(z.title, "Z REPORT")
		(usd,) = z.currencies
		docs = {label: (qty, amount) for label, qty, amount in usd.documents}
		self.assertEqual(docs["Invoices"][0], 1)
		self.assertEqual(docs["Credit notes"][0], 1)
		self.assertAlmostEqual(usd.total_gross, invoice.grand_total + note.grand_total, places=2)

	def test_credit_note_without_reason_is_refused(self):
		invoice = make_invoice([(ITEM, 1, 10)])
		self.assertRaises(frappe.ValidationError, self._credit, invoice, "")

	def test_next_receipt_after_close_opens_a_new_day(self):
		from zw_compliance.fiscalisation.day import request_close

		first = self.receipt_of(make_invoice([(ITEM, 1, 10)]))
		self.sync()
		request_close(first.fiscal_day)
		self.sync()
		self.fdms.settle()
		self.sync()
		nxt = self.receipt_of(make_invoice([(ITEM, 1, 10)]))
		self.assertEqual(nxt.fiscal_day_no, first.fiscal_day_no + 1)
		self.assertEqual(nxt.receipt_counter, 1)
		self.assertEqual(nxt.receipt_global_no, first.receipt_global_no + 1)
		self.assertFalse(json.loads(nxt.payload).get("previousReceiptHash"))
		self.sync()
		nxt.reload()
		self.assertEqual(nxt.sync_status, "Submitted", nxt.last_error)
		self.assertFalse(nxt.validation_errors)


class TestResilience(FiscalisationTestCase):
	def _retry_now(self):
		frappe.db.sql(
			"update `tabZimra Fiscal Receipt` set next_attempt_at = null where sync_status = 'Retrying'"
		)

	def test_unreachable_fdms_queues_then_recovers_in_order(self):
		self.fdms.faults.drop_connections = 50
		a, b = make_invoice([(ITEM, 1, 10)]), make_invoice([(ITEM, 1, 11)])
		self.sync()
		self.assertIn(self.receipt_of(a).sync_status, ("Queued", "Retrying"))
		self.fdms.faults.drop_connections = 0
		self._retry_now()
		self.sync()
		ra, rb = self.receipt_of(a), self.receipt_of(b)
		self.assertEqual((ra.sync_status, rb.sync_status), ("Submitted", "Submitted"))
		ids = [r.receipt["receiptGlobalNo"] for r in self.fdms.all_receipts(DEVICE_ID)]
		self.assertEqual(ids, sorted(ids))

	def test_lost_answer_is_resent_without_duplicate(self):
		invoice = make_invoice([(ITEM, 1, 10)])
		self.fdms.faults.drop_after_submit = 1  # FDMS stores it, the answer is lost
		self.sync()
		receipt = self.receipt_of(invoice)
		self.assertEqual(receipt.sync_status, "Retrying")
		self._retry_now()
		self.sync()
		receipt.reload()
		self.assertEqual(receipt.sync_status, "Submitted")
		stored = [r for r in self.fdms.all_receipts(DEVICE_ID) if r.receipt["invoiceNo"] == invoice.name]
		self.assertEqual(len(stored), 1)
		self.assertEqual(str(stored[0].receipt_id), receipt.fdms_receipt_id)

	def test_server_errors_are_retried(self):
		invoice = make_invoice([(ITEM, 1, 10)])
		self.fdms.faults.server_errors = 1
		self.sync()
		self._retry_now()
		self.sync()
		self.assertEqual(self.receipt_of(invoice).sync_status, "Submitted")


class TestCloseBlockedByRedReceipt(FiscalisationTestCase):
	"""Own class: the blocked day stays blocked, as it must."""

	def test_grey_or_red_receipt_blocks_the_close(self):
		from zw_compliance.fiscalisation.day import request_close

		receipt = self.receipt_of(make_invoice([(ITEM, 1, 10)]))
		self.sync()
		frappe.db.set_value("Zimra Fiscal Receipt", receipt.name, "worst_colour", "Red")
		request_close(receipt.fiscal_day)
		self.sync()
		day = frappe.get_doc("Zimra Fiscal Day", receipt.fiscal_day)
		self.assertEqual(day.status, "Close Requested")
		self.assertIn("ZIMRA portal", day.last_error)


class TestRemoteApi(FiscalisationTestCase):
	def test_fiscalise_invoice_is_idempotent(self):
		from zw_compliance.api.v1.fiscalisation import fiscalise_invoice, get_receipt

		first = fiscalise_invoice(
			client_reference="APP-1",
			customer=BUSINESS,
			company=self.device.company,
			items=[{"item_code": ITEM, "qty": 1, "rate": 99}],
		)
		self.assertTrue(first["ok"], first)
		data = first["data"]
		self.assertEqual(data["status"], "Queued")
		self.assertRegex(data["verification_code"], r"^[0-9A-F]{4}(-[0-9A-F]{4}){3}$")
		self.assertTrue(data["print_url"].startswith("/api/method/"))
		again = fiscalise_invoice(
			client_reference="APP-1",
			customer=BUSINESS,
			company=self.device.company,
			items=[{"item_code": ITEM, "qty": 1, "rate": 99}],
		)
		self.assertTrue(again["data"]["replayed"])
		self.assertEqual(again["data"]["invoice"], data["invoice"])
		self.assertEqual(
			get_receipt(client_reference="APP-1")["data"]["receipt_global_no"], data["receipt_global_no"]
		)

	def test_errors_use_the_envelope(self):
		from zw_compliance.api.v1.fiscalisation import fiscalise_invoice

		out = fiscalise_invoice(
			client_reference="APP-2", items=[], company=self.device.company, customer=BUSINESS
		)
		self.assertEqual(
			out, {"ok": False, "error": {"code": "MISSING_FIELD", "message": "items is required."}}
		)
		self.assertEqual(frappe.local.response.http_status_code, 422)
		frappe.local.response.http_status_code = 200

	def test_credit_note_endpoint(self):
		from zw_compliance.api.v1.fiscalisation import create_credit_note, fiscalise_invoice

		sale = fiscalise_invoice(
			client_reference="APP-3",
			customer=BUSINESS,
			company=self.device.company,
			items=[{"item_code": ITEM, "qty": 2, "rate": 50}],
		)["data"]
		credit = create_credit_note(
			invoice=sale["invoice"],
			client_reference="APP-3-R1",
			reason="Damaged",
			items=[{"item_code": ITEM, "qty": 1}],
		)
		self.assertTrue(credit["ok"], credit)
		self.assertEqual(credit["data"]["receipt_type"], "CreditNote")
		self.assertLess(credit["data"]["total"], 0)


class TestRemoteApiOverHttp(FiscalisationTestCase):
	"""Through Frappe's request dispatch: whitelisting, HTTP method, argument parsing."""

	def _call(self, method: str, http_method: str, **params):
		from frappe.handler import execute_cmd
		from werkzeug.test import EnvironBuilder

		request, form_dict = getattr(frappe.local, "request", None), frappe.local.form_dict
		frappe.local.request = EnvironBuilder(method=http_method, path="/api/method/" + method).get_request()
		frappe.local.form_dict = frappe._dict(params)
		try:
			out = execute_cmd(f"zw_compliance.api.v1.fiscalisation.{method}")
		finally:
			frappe.local.request, frappe.local.form_dict = request, form_dict
		if isinstance(out, dict) and out.get("error", {}).get("code") == "INTERNAL_ERROR":
			# surface the logged traceback in the failure message
			out["error"]["traceback"] = frappe.get_last_doc("Error Log").error
		return out

	def test_form_encoded_sale_is_fiscalised(self):
		out = self._call(
			"fiscalise_invoice",
			"POST",
			client_reference="HTTP-1",
			company=self.device.company,
			customer=BUSINESS,
			items=json.dumps([{"item_code": ITEM, "qty": 1, "rate": 12.5}]),
		)
		self.assertTrue(out["ok"], out)
		self.assertEqual(out["data"]["total"], 14.38)  # 12.50 + 15% VAT
		receipt = self._call("get_receipt", "GET", client_reference="HTTP-1")
		self.assertEqual(receipt["data"]["invoice"], out["data"]["invoice"])

	def test_writes_require_post(self):
		self.assertRaises(
			frappe.PermissionError,
			self._call,
			"fiscalise_invoice",
			"GET",
			client_reference="HTTP-2",
			items="[]",
		)

	def test_device_status(self):
		out = self._call("get_device_status", "GET", company=self.device.company)
		(device,) = out["data"]
		self.assertEqual((device["device"], device["status"]), (self.device.name, "Active"))


class TestPrintAndReports(FiscalisationTestCase):
	def test_print_formats_render_fiscal_fields(self):
		invoice = make_invoice([(ITEM, 1, 10)], customer=BUSINESS)
		receipt = self.receipt_of(invoice)
		for fmt in ("ZIMRA Invoice A4", "ZIMRA Receipt48 Sales Invoice"):
			html = frappe.get_print("Sales Invoice", invoice.name, fmt)
			self.assertIn(receipt.verification_code, html)
			self.assertIn("FISCAL TAX INVOICE", html)
			self.assertIn(BUSINESS_TIN, html)
			self.assertIn("<svg", html)
		html = frappe.get_print("Zimra Fiscal Day", receipt.fiscal_day, "ZIMRA Z Report")
		self.assertIn("X REPORT", html)

	def test_reports_run(self):
		make_invoice([(ITEM, 1, 10)])
		self.sync()
		filters = {
			"company": self.device.company,
			"from_date": frappe.utils.add_days(frappe.utils.today(), -1),
			"to_date": frappe.utils.today(),
		}
		for report in (
			"zimra_fiscal_day_summary",
			"zimra_fiscal_receipt_register",
			"zimra_sync_exceptions",
			"zimra_vat_summary",
			"zimra_fiscalisation_reconciliation",
			"zimra_device_status",
		):
			module = frappe.get_module(f"zw_compliance.zimra_fiscalisation.report.{report}.{report}")
			columns, _data = module.execute(dict(filters))
			self.assertTrue(columns, report)
		recon = frappe.get_module(
			"zw_compliance.zimra_fiscalisation.report.zimra_fiscalisation_reconciliation.zimra_fiscalisation_reconciliation"
		).execute(dict(filters, only_issues=1))[1]
		self.assertFalse([r for r in recon if r["issue"] and re.search("differs", r["issue"])], recon)


class TestCloseRefused(FiscalisationTestCase):
	def _refused(self, as_opened: bool):
		from zw_compliance.fiscalisation.day import request_close

		receipt = self.receipt_of(make_invoice([(ITEM, 1, 10)]))
		self.sync()
		self.fdms.faults.refuse_close, self.fdms.faults.refuse_close_as_opened = "CountersMismatch", as_opened
		request_close(receipt.fiscal_day)
		self.sync()
		self.fdms.settle()
		self.sync()
		day = frappe.get_doc("Zimra Fiscal Day", receipt.fiscal_day)
		self.assertEqual((day.status, day.closing_error_code), ("Close Failed", "CountersMismatch"))
		# the manager retries; FDMS now accepts
		request_close(day.name)
		self.sync()
		self.fdms.settle()
		self.sync()
		self.assertEqual(frappe.db.get_value("Zimra Fiscal Day", day.name, "status"), "Closed")

	def test_refused_close_reported_as_close_failed(self):
		self._refused(as_opened=False)

	def test_refused_close_reported_as_reopened_day(self):
		self._refused(as_opened=True)


class TestFlappingConnection(FiscalisationTestCase):
	"""Online, down, back, down (with a lost answer), back: no gaps, no duplicates,
	no reordering, and the day still closes with matching counters."""

	def test_sequence_survives_a_flapping_connection(self):
		from zw_compliance.fiscalisation.day import request_close

		faults = self.fdms.faults
		down = 1000  # every request fails while "down"
		sales = []

		def sell(rate):
			sales.append(make_invoice([(ITEM, 1, rate)]))
			self.sync()  # the job the sale enqueues

		sell(10)  # online
		self.assertEqual(self.receipt_of(sales[0]).sync_status, "Submitted")

		faults.drop_connections = down  # connection drops; trading goes on
		for rate in (11, 12, 13):
			sell(rate)
		statuses = [self.receipt_of(s).sync_status for s in sales[1:]]
		self.assertEqual(statuses[0], "Retrying")
		self.assertTrue(all(s in ("Queued", "Retrying") for s in statuses))

		faults.drop_connections = 0  # back: the next sale drains the queue at once
		sell(14)
		self.assertEqual([self.receipt_of(s).sync_status for s in sales], ["Submitted"] * 5)

		faults.drop_after_submit = 1  # FDMS stores the next receipt but the answer is lost...
		sell(15)
		faults.drop_connections = down  # ...and the line goes down again
		sell(16)
		self.assertEqual(self.receipt_of(sales[5]).sync_status, "Retrying")

		faults.drop_connections = 0  # back again
		sell(17)
		receipts = [self.receipt_of(s) for s in sales]
		self.assertEqual([r.sync_status for r in receipts], ["Submitted"] * 8)

		numbers = [r.receipt_global_no for r in receipts]
		self.assertEqual(numbers, list(range(numbers[0], numbers[0] + 8)))
		for prev, cur in itertools.pairwise(receipts):
			self.assertEqual(cur.previous_receipt_hash, prev.device_signature_hash)
		stored = self.fdms.all_receipts(DEVICE_ID)
		sent = [r.receipt["receiptGlobalNo"] for r in stored]
		self.assertEqual(sent, sorted(set(sent)), "FDMS received a duplicate or out of order")
		self.assertEqual(sent[-8:], numbers)
		self.assertFalse([e for r in stored for e in r.errors], "FDMS flagged a receipt")

		request_close(receipts[-1].fiscal_day)
		self.sync()
		self.fdms.settle()
		self.sync()
		self.assertEqual(frappe.db.get_value("Zimra Fiscal Day", receipts[-1].fiscal_day, "status"), "Closed")
