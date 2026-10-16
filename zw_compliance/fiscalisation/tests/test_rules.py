"""Business rules, POS, permissions and the purchase side."""

from __future__ import annotations

import json
from datetime import timedelta

import frappe
from frappe.utils import flt, now_datetime

from .fixtures import (
	ABBR,
	COMPANY,
	GROUP,
	ITEM,
	WALK_IN,
	FiscalisationTestCase,
	ensure_customer,
	ensure_item,
	make_invoice,
	mapping,
)

CASH = "Cash"
POS_PROFILE = "_Test ZW POS"
SUPPLIER = "_Test ZW Supplier"


def ensure_pos_profile() -> str:
	if not frappe.db.exists("Mode of Payment", CASH):
		frappe.get_doc({"doctype": "Mode of Payment", "mode_of_payment": CASH, "type": "Cash"}).insert(
			ignore_permissions=True
		)
	mop = frappe.get_doc("Mode of Payment", CASH)
	if not any(a.company == COMPANY for a in mop.accounts):
		mop.append("accounts", {"company": COMPANY, "default_account": f"Cash - {ABBR}"})
		mop.save(ignore_permissions=True)
	if not frappe.db.exists("POS Profile", POS_PROFILE):
		frappe.get_doc(
			{
				"doctype": "POS Profile",
				"name": POS_PROFILE,
				"company": COMPANY,
				"currency": "USD",
				"warehouse": f"Stores - {ABBR}",
				"customer": WALK_IN,
				"selling_price_list": frappe.db.get_value("Price List", {"selling": 1}),
				"write_off_account": f"Write Off - {ABBR}",
				"write_off_cost_center": f"Main - {ABBR}",
				"cost_center": f"Main - {ABBR}",
				"taxes_and_charges": mapping(1).sales_tax_template,
				"payments": [{"mode_of_payment": CASH, "default": 1}],
			}
		).insert(ignore_permissions=True)
	return POS_PROFILE


def make_user(email: str, roles: list[str]) -> str:
	if not frappe.db.exists("User", email):
		user = frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0}
		)
		user.insert(ignore_permissions=True)
		user.add_roles(*roles)
	return email


class TestInvoiceRules(FiscalisationTestCase):
	def test_hs_code_is_mandatory_for_a_vat_registered_seller(self):
		ensure_item("_Test ZW No HS", None)
		frappe.db.set_value("Item Group", GROUP, "zimra_hs_code", None)
		invoice = make_invoice([("_Test ZW No HS", 1, 10)], submit=False)
		self.assertRaisesRegex(frappe.ValidationError, "RCPT047|HS code", invoice.submit)

	def test_item_group_hs_code_is_inherited(self):
		ensure_item("_Test ZW Group HS", None)
		frappe.db.set_value("Item Group", GROUP, "zimra_hs_code", "84713000")
		try:
			invoice = make_invoice([("_Test ZW Group HS", 1, 10)])
		finally:
			frappe.db.set_value("Item Group", GROUP, "zimra_hs_code", None)
		line = json.loads(self.receipt_of(invoice).payload)["receiptLines"][0]
		self.assertEqual(line["receiptLineHSCode"], "84713000")

	def test_invalid_buyer_tin_is_refused(self):
		ensure_customer("_Test ZW Bad TIN", tax_id="12345")
		invoice = make_invoice([(ITEM, 1, 10)], customer="_Test ZW Bad TIN", submit=False)
		self.assertRaisesRegex(frappe.ValidationError, "10 digits", invoice.submit)

	def test_rounded_total_is_disabled_on_fiscal_invoices(self):
		invoice = make_invoice([(ITEM, 3, 3.33)], submit=False)
		self.assertTrue(invoice.disable_rounded_total)
		self.assertEqual(flt(invoice.rounding_adjustment), 0)

	def test_discounted_line_reconciles_to_grand_total(self):
		invoice = make_invoice([(ITEM, 3, 33.33)], submit=False)
		invoice.items[0].discount_percentage = 7.5
		invoice.items[0].rate = flt(33.33 * 0.925, 2)
		invoice.calculate_taxes_and_totals()
		invoice.save(ignore_permissions=True)
		invoice.submit()
		receipt = self.receipt_of(invoice)
		payload = json.loads(receipt.payload)
		self.assertAlmostEqual(
			sum(l["receiptLineTotal"] for l in payload["receiptLines"]), invoice.grand_total, places=2
		)
		self.assertAlmostEqual(payload["receiptTotal"], invoice.grand_total, places=2)
		self.sync()
		receipt.reload()
		self.assertFalse(receipt.validation_errors, [e.as_dict() for e in receipt.validation_errors])

	def test_debit_note_is_positive_and_references_the_original(self):
		original = make_invoice([(ITEM, 1, 100)])
		note = make_invoice(
			[(ITEM, 1, 5)],
			is_debit_note=1,
			return_against=original.name,
			zimra_receipt_notes="Price correction",
		)
		payload = json.loads(self.receipt_of(note).payload)
		self.assertEqual(payload["receiptType"], "DebitNote")
		self.assertGreater(payload["receiptTotal"], 0)
		self.assertEqual(
			payload["creditDebitNote"]["receiptGlobalNo"], self.receipt_of(original).receipt_global_no
		)
		self.sync()
		self.assertEqual(self.receipt_of(note).sync_status, "Submitted")


class TestDayRollover(FiscalisationTestCase):
	def test_day_past_max_hours_is_closed_and_a_new_day_opened(self):
		first = self.receipt_of(make_invoice([(ITEM, 1, 10)]))
		self.sync()
		frappe.db.set_value("Zimra Fiscal Device", self.device.name, "day_max_hours", 24)
		frappe.db.set_value(
			"Zimra Fiscal Day", first.fiscal_day, "fiscal_day_opened", now_datetime() - timedelta(hours=30)
		)
		second = self.receipt_of(make_invoice([(ITEM, 1, 10)]))
		self.assertEqual(second.fiscal_day_no, first.fiscal_day_no + 1)
		self.assertEqual(
			frappe.db.get_value("Zimra Fiscal Day", first.fiscal_day, "status"), "Close Requested"
		)
		self.sync()
		self.fdms.settle()
		self.sync()
		self.assertEqual(frappe.db.get_value("Zimra Fiscal Day", first.fiscal_day, "status"), "Closed")
		second.reload()
		self.assertEqual(second.sync_status, "Submitted", second.last_error)


class TestPointOfSale(FiscalisationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_pos_profile()

	def test_cash_sale_with_change_reports_net_cash(self):
		invoice = make_invoice(
			[(ITEM, 1, 57.5)], submit=False, is_pos=1, pos_profile=POS_PROFILE, update_stock=0
		)
		invoice.set("payments", [{"mode_of_payment": CASH, "amount": 100}])
		invoice.calculate_taxes_and_totals()
		invoice.save(ignore_permissions=True)
		invoice.submit()
		self.assertGreater(invoice.change_amount, 0)
		payload = json.loads(self.receipt_of(invoice).payload)
		self.assertEqual(
			payload["receiptPayments"], [{"moneyTypeCode": "Cash", "paymentAmount": payload["receiptTotal"]}]
		)
		self.sync()
		self.assertFalse(self.receipt_of(invoice).validation_errors)

	def test_credit_sale_reports_the_unpaid_balance(self):
		invoice = make_invoice([(ITEM, 1, 40)])
		payload = json.loads(self.receipt_of(invoice).payload)
		self.assertEqual(
			payload["receiptPayments"],
			[{"moneyTypeCode": "Credit", "paymentAmount": payload["receiptTotal"]}],
		)


class TestPermissions(FiscalisationTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")

	def test_zimra_user_cannot_run_device_actions(self):
		from zw_compliance.fiscalisation import api

		frappe.set_user(make_user("zimra.user@example.com", ["ZIMRA User"]))
		self.assertRaises(frappe.PermissionError, api.sync_now, self.device.name)
		self.assertRaises(frappe.PermissionError, api.renew_certificate, self.device.name)

	def test_nobody_may_write_a_fiscal_receipt(self):
		receipt = self.receipt_of(make_invoice([(ITEM, 1, 10)]))
		frappe.set_user(make_user("zimra.manager@example.com", ["ZIMRA Manager", "System Manager"]))
		doc = frappe.get_doc("Zimra Fiscal Receipt", receipt.name)
		self.assertTrue(doc.has_permission("read"))
		self.assertFalse(doc.has_permission("write"))
		self.assertFalse(frappe.has_permission("Zimra Fiscal Receipt", "create"))
		self.assertFalse(doc.has_permission("delete"))

	def test_device_secrets_are_hidden_from_zimra_users(self):
		frappe.set_user(make_user("zimra.user@example.com", ["ZIMRA User"]))
		meta = frappe.get_meta("Zimra Fiscal Device")
		for field in ("private_key", "activation_key", "certificate"):
			self.assertGreaterEqual(meta.get_field(field).permlevel, 1, field)
		self.assertNotIn(1, meta.get_permlevel_access("read"))

	def test_remote_api_respects_document_permissions(self):
		from zw_compliance.api.v1.fiscalisation import fiscalise_invoice

		frappe.set_user(make_user("zimra.user@example.com", ["ZIMRA User"]))
		out = fiscalise_invoice(
			client_reference="PERM-1",
			company=COMPANY,
			customer=WALK_IN,
			items=[{"item_code": ITEM, "qty": 1, "rate": 1}],
		)
		self.assertEqual(out["error"]["code"], "PERMISSION_DENIED")
		self.assertEqual(frappe.local.response.http_status_code, 403)
		frappe.local.response.http_status_code = 200


class TestPurchaseSide(FiscalisationTestCase):
	def test_supplier_invoice_books_input_vat_and_is_not_fiscalised(self):
		if not frappe.db.exists("Supplier", SUPPLIER):
			frappe.get_doc(
				{
					"doctype": "Supplier",
					"supplier_name": SUPPLIER,
					"supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0}),
				}
			).insert(ignore_permissions=True)
		vat = mapping(1)
		bill = frappe.new_doc("Purchase Invoice")
		bill.update(
			{
				"company": COMPANY,
				"supplier": SUPPLIER,
				"currency": "USD",
				"taxes_and_charges": vat.purchase_tax_template,
			}
		)
		bill.append(
			"items",
			{"item_code": ITEM, "qty": 1, "rate": 100, "expense_account": f"Cost of Goods Sold - {ABBR}"},
		)
		bill.set_missing_values()
		bill.items[0].rate = 100
		bill.set("taxes", [])
		bill.set_taxes()
		bill.calculate_taxes_and_totals()
		bill.insert(ignore_permissions=True)
		bill.submit()
		gl = frappe.get_all(
			"GL Entry", filters={"voucher_no": bill.name, "account": vat.input_account}, fields=["debit"]
		)
		self.assertEqual([g.debit for g in gl], [flt(100 * vat.zimra_tax_percent / 100, 2)])
		self.assertFalse(frappe.db.exists("Zimra Fiscal Receipt", {"reference_name": bill.name}))
		self.assertTrue(
			frappe.db.get_value(
				"Purchase Taxes and Charges Template", vat.purchase_tax_template, "is_default"
			)
		)


class TestPosInvoice(FiscalisationTestCase):
	"""The POS Invoice flow: fiscalised at the till; the consolidated Sales
	Invoice created at closing is not fiscalised again."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_pos_profile()
		frappe.db.set_single_value("POS Settings", "invoice_type", "POS Invoice")
		frappe.get_doc(
			{
				"doctype": "POS Opening Entry",
				"company": COMPANY,
				"pos_profile": POS_PROFILE,
				"user": "Administrator",
				"period_start_date": now_datetime(),
				"posting_date": frappe.utils.today(),
				"balance_details": [{"mode_of_payment": CASH, "opening_amount": 0}],
			}
		).submit()

	def _pos_sale(self, qty, rate, paid):
		invoice = frappe.new_doc("POS Invoice")
		invoice.update(
			{
				"company": COMPANY,
				"pos_profile": POS_PROFILE,
				"is_pos": 1,
				"customer": WALK_IN,
				"update_stock": 0,
			}
		)
		invoice.append("items", {"item_code": ITEM, "qty": qty, "rate": rate})
		invoice.set_missing_values()
		invoice.items[0].price_list_rate = invoice.items[0].rate = rate
		invoice.set("taxes", [])
		invoice.set_taxes()
		invoice.set("payments", [{"mode_of_payment": CASH, "amount": paid}])
		invoice.calculate_taxes_and_totals()
		invoice.insert(ignore_permissions=True)
		invoice.submit()
		return invoice

	def test_pos_invoice_is_fiscalised_and_consolidation_is_not(self):
		sale = self._pos_sale(1, 19.99, 25)
		receipt = self.receipt_of(sale)
		payload = json.loads(receipt.payload)
		self.assertEqual(payload["receiptPrintForm"], "Receipt48")
		self.assertAlmostEqual(payload["receiptTotal"], sale.grand_total, places=2)
		self.assertEqual(sum(p["paymentAmount"] for p in payload["receiptPayments"]), payload["receiptTotal"])
		self.sync()
		self.assertFalse(self.receipt_of(sale).validation_errors)

		before = frappe.db.count("Zimra Fiscal Receipt")
		log = frappe.new_doc("POS Invoice Merge Log")
		log.update({"posting_date": frappe.utils.today(), "customer": WALK_IN, "company": COMPANY})
		log.append(
			"pos_invoices",
			{
				"pos_invoice": sale.name,
				"customer": WALK_IN,
				"posting_date": sale.posting_date,
				"grand_total": sale.grand_total,
			},
		)
		log.insert(ignore_permissions=True)
		log.submit()
		consolidated = frappe.get_doc("Sales Invoice", log.consolidated_invoice)
		self.assertTrue(consolidated.is_consolidated)
		self.assertFalse(consolidated.zimra_fiscal_receipt)
		self.assertEqual(frappe.db.count("Zimra Fiscal Receipt"), before)
		html = frappe.get_print("POS Invoice", sale.name, "ZIMRA Receipt48")
		self.assertIn(receipt.verification_code, html)


class TestUninstall(FiscalisationTestCase):
	def test_uninstall_is_refused_while_receipts_are_unsent(self):
		from zw_compliance.fiscalisation.setup.install import before_uninstall

		make_invoice([(ITEM, 1, 10)])
		self.assertRaisesRegex(frappe.ValidationError, "unsent", before_uninstall)
		self.sync()
		before_uninstall()  # everything accepted: allowed


class TestCreditNoteRules(FiscalisationTestCase):
	"""RCPT033/035/036: checked against the original fiscal receipt before submit."""

	def _note(self, invoice, **changes):
		from erpnext.controllers.sales_and_purchase_return import make_return_doc

		note = make_return_doc("Sales Invoice", invoice.name)
		note.zimra_receipt_notes = "Return"
		note.update(changes)
		if "taxes_and_charges" in changes:
			note.set("taxes", [])
			note.set_taxes()
		note.calculate_taxes_and_totals()
		note.insert(ignore_permissions=True)
		return note

	def test_rcpt033_original_older_than_twelve_months(self):
		invoice = make_invoice([(ITEM, 1, 10)])
		receipt = self.receipt_of(invoice)
		frappe.db.set_value(
			"Zimra Fiscal Receipt", receipt.name, "receipt_date", frappe.utils.add_months(now_datetime(), -13)
		)
		self.assertRaisesRegex(frappe.ValidationError, "RCPT033", self._note(invoice).submit)

	def test_rcpt035_credit_exceeding_the_original(self):
		invoice = make_invoice([(ITEM, 2, 10)])
		frappe.db.set_value("Zimra Fiscal Receipt", self.receipt_of(invoice).name, "receipt_total", 5)
		self.assertRaisesRegex(frappe.ValidationError, "RCPT035", self._note(invoice).submit)

	def test_rcpt036_tax_not_on_the_original(self):
		invoice = make_invoice([(ITEM, 1, 10)])
		note = self._note(invoice, taxes_and_charges=mapping(2).sales_tax_template)
		self.assertRaisesRegex(frappe.ValidationError, "RCPT036", note.submit)

	def test_partial_credits_up_to_the_original_are_accepted(self):
		invoice = make_invoice([(ITEM, 2, 10)])
		for _ in range(2):
			note = self._note(invoice)
			note.items[0].qty = -1
			note.calculate_taxes_and_totals()
			note.save(ignore_permissions=True)
			note.submit()
		self.sync()
		totals = frappe.get_all(
			"Zimra Fiscal Receipt",
			filters={"original_receipt": self.receipt_of(invoice).name},
			fields=["receipt_total", "sync_status"],
		)
		self.assertEqual(len(totals), 2)
		self.assertAlmostEqual(sum(t.receipt_total for t in totals), -invoice.grand_total, places=2)
		self.assertTrue(all(t.sync_status == "Submitted" for t in totals))
