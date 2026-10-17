"""Global receipt numbering: when receiptGlobalNo starts again at 1."""

from __future__ import annotations

import json

import frappe

from zw_compliance.fiscalisation import numbering
from zw_compliance.fiscalisation.day import request_close

from .fixtures import ITEM, FiscalisationTestCase, make_invoice

try:
	from frappe.tests import UnitTestCase
except ImportError:  # Frappe v15
	from frappe.tests.utils import FrappeTestCase as UnitTestCase


def _device(policy=None, limit=None, last=0):
	return frappe._dict(
		device_name="Till", global_number_reset=policy, global_number_limit=limit, last_receipt_global_no=last
	)


class TestNumberingPolicy(UnitTestCase):
	def test_near_limit_is_the_default_and_restarts_past_ninety_percent(self):
		self.assertEqual(numbering.reset_policy(_device()), "Near Limit")
		self.assertFalse(numbering.restarts_in_new_day(_device(limit=100, last=90)))
		self.assertTrue(numbering.restarts_in_new_day(_device(limit=100, last=91)))
		self.assertFalse(numbering.restarts_in_new_day(_device(last=1_000_000)))
		self.assertTrue(numbering.restarts_in_new_day(_device(last=numbering.MAX_GLOBAL_NO)))

	def test_every_fiscal_day_restarts_once_a_number_was_used(self):
		self.assertTrue(numbering.restarts_in_new_day(_device("Every Fiscal Day", last=1)))
		self.assertFalse(numbering.restarts_in_new_day(_device("Every Fiscal Day", last=0)))

	def test_never_never_restarts_and_refuses_at_the_limit(self):
		self.assertFalse(numbering.restarts_in_new_day(_device("Never", limit=10, last=10)))
		numbering.require_number_available(_device("Never", limit=10, last=9))
		self.assertRaises(
			frappe.ValidationError, numbering.require_number_available, _device("Never", limit=10, last=10)
		)
		numbering.require_number_available(_device(limit=10, last=10))  # Near Limit rolls over instead

	def test_blank_or_oversized_limits_mean_the_largest_number(self):
		self.assertEqual(numbering.limit(_device()), numbering.MAX_GLOBAL_NO)
		self.assertEqual(numbering.limit(_device(limit=0)), numbering.MAX_GLOBAL_NO)
		self.assertEqual(numbering.limit(_device(limit=5)), 5)


class TestGlobalNumbering(FiscalisationTestCase):
	def setUp(self):
		self.addCleanup(self._set, None, 0)

	def _set(self, policy, limit):
		frappe.db.set_value(
			"Zimra Fiscal Device",
			self.device.name,
			{"global_number_reset": policy, "global_number_limit": limit},
			update_modified=False,
		)

	def _last(self) -> int:
		return frappe.db.get_value("Zimra Fiscal Device", self.device.name, "last_receipt_global_no") or 0

	def _close_current_day(self):
		day = frappe.db.get_value("Zimra Fiscal Device", self.device.name, "current_fiscal_day")
		request_close(day)
		self._settle()

	def _settle(self):
		self.sync()
		self.fdms.settle()
		self.sync()

	def _assert_accepted(self, *receipts):
		for receipt in receipts:
			receipt.reload()
			self.assertEqual(receipt.sync_status, "Submitted", receipt.last_error)
			self.assertFalse(receipt.validation_errors, [e.as_dict() for e in receipt.validation_errors])

	def test_every_fiscal_day_starts_each_new_day_at_one(self):
		self._set("Every Fiscal Day", 0)
		before = self.receipt_of(make_invoice([(ITEM, 1, 10)]))
		self._close_current_day()
		first = self.receipt_of(make_invoice([(ITEM, 1, 10)]))
		second = self.receipt_of(make_invoice([(ITEM, 1, 10)]))
		self.assertEqual((first.fiscal_day_no, first.receipt_global_no), (before.fiscal_day_no + 1, 1))
		self.assertEqual(second.receipt_global_no, 2)
		self.sync()
		self._assert_accepted(before, first, second)

	def test_reaching_the_limit_mid_day_closes_the_day_and_restarts(self):
		self.receipt_of(make_invoice([(ITEM, 1, 10)]))
		self._set("Near Limit", self._last() + 1)
		at_limit = self.receipt_of(make_invoice([(ITEM, 1, 10)]))
		restarted = self.receipt_of(make_invoice([(ITEM, 1, 10)]))
		self.assertEqual(restarted.receipt_global_no, 1)
		self.assertEqual(restarted.receipt_counter, 1)
		self.assertEqual(restarted.fiscal_day_no, at_limit.fiscal_day_no + 1)
		self.assertFalse(json.loads(restarted.payload).get("previousReceiptHash"))
		old_day = frappe.get_doc("Zimra Fiscal Day", at_limit.fiscal_day)
		self.assertEqual(old_day.status, "Close Requested")

		self._settle()  # old day's receipts, its close, then the new day
		self.sync()
		self._assert_accepted(at_limit, restarted)
		self.assertEqual(frappe.db.get_value("Zimra Fiscal Day", at_limit.fiscal_day, "status"), "Closed")

	def test_a_credit_note_after_a_restart_refers_to_the_original_day(self):
		from erpnext.controllers.sales_and_purchase_return import make_return_doc

		self._set("Every Fiscal Day", 0)
		invoice = make_invoice([(ITEM, 1, 10)])
		original = self.receipt_of(invoice)
		self.sync()
		self._close_current_day()
		self.receipt_of(make_invoice([(ITEM, 1, 10)]))  # global number 1 again, in the new day

		note = make_return_doc("Sales Invoice", invoice.name)
		note.zimra_receipt_notes = "Goods returned"
		note.insert(ignore_permissions=True)
		note.submit()
		credit = self.receipt_of(note)
		reference = json.loads(credit.payload)["creditDebitNote"]
		self.assertEqual(credit.original_receipt, original.name)
		self.assertEqual(
			(reference["receiptGlobalNo"], reference["fiscalDayNo"]),
			(original.receipt_global_no, original.fiscal_day_no),
		)
		self.sync()
		self._assert_accepted(credit)

	def test_never_refuses_at_the_limit_without_using_a_number(self):
		self.receipt_of(make_invoice([(ITEM, 1, 10)]))
		last = self._last()
		self._set("Never", last)
		self.assertRaises(frappe.ValidationError, make_invoice, [(ITEM, 1, 10)])
		self.assertEqual(self._last(), last)

	def test_receipts_with_one_global_number_in_two_days_are_both_kept(self):
		self._set("Every Fiscal Day", 0)
		self.receipt_of(make_invoice([(ITEM, 1, 10)]))
		self._close_current_day()
		a = self.receipt_of(make_invoice([(ITEM, 1, 10)]))
		self._close_current_day()
		b = self.receipt_of(make_invoice([(ITEM, 1, 10)]))
		self.assertEqual((a.receipt_global_no, b.receipt_global_no), (1, 1))
		self.assertNotEqual(a.fiscal_day_no, b.fiscal_day_no)
