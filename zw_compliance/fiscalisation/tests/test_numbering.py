"""Global receipt numbering: when receiptGlobalNo starts again at 1."""

from __future__ import annotations

import json
from unittest.mock import patch

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
		self.assertTrue(numbering.restarts_in_new_day(_device(limit=100, last=100)))
		self.assertFalse(numbering.restarts_in_new_day(_device(last=1_000_000)))
		self.assertTrue(numbering.restarts_in_new_day(_device(last=numbering.MAX_GLOBAL_NO)))

	def test_numbers_continue_across_days_with_the_default_limit(self):
		self.assertFalse(numbering.restarts_in_new_day(_device(last=1)))
		self.assertFalse(numbering.restarts_in_new_day(_device(last=1_900_000_000)))

	def test_every_fiscal_day_restarts_once_a_number_was_used(self):
		self.assertTrue(numbering.restarts_in_new_day(_device("Every Fiscal Day", last=1)))
		self.assertFalse(numbering.restarts_in_new_day(_device("Every Fiscal Day", last=0)))

	def test_unknown_policies_are_treated_as_near_limit(self):
		self.assertEqual(numbering.reset_policy(_device("Never")), "Near Limit")
		self.assertTrue(numbering.restarts_in_new_day(_device("Never", limit=10, last=10)))

	def test_blank_or_oversized_limits_mean_the_largest_number(self):
		self.assertEqual(numbering.limit(_device()), numbering.MAX_GLOBAL_NO)
		self.assertEqual(numbering.limit(_device(limit=0)), numbering.MAX_GLOBAL_NO)
		self.assertEqual(numbering.limit(_device(limit=5)), 5)


class TestGlobalNumbering(FiscalisationTestCase):
	def setUp(self):
		self._settle()  # start from a device with nothing queued or closing
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

	def _sale(self):
		return self.receipt_of(make_invoice([(ITEM, 1, 10)]))

	def _restart_at_next_sale(self):
		"""Put the device at its limit, so the next sale starts a new day at 1."""
		self._set("Near Limit", self._last())

	def _settle(self):
		"""Sync until every receipt is sent and every closing day is closed."""
		for _ in range(10):
			self.sync()
			self.fdms.settle()
			self.sync()
			pending = frappe.db.count(
				"Zimra Fiscal Receipt",
				{
					"fiscal_device": self.device.name,
					"sync_status": ["in", ["Queued", "Retrying", "Submitting"]],
				},
			) + frappe.db.count(
				"Zimra Fiscal Day",
				{"fiscal_device": self.device.name, "status": ["in", ["Close Requested", "Close Initiated"]]},
			)
			if not pending:
				return
		self.fail("the device did not settle")

	def _assert_accepted(self, *receipts):
		for receipt in receipts:
			receipt.reload()
			self.assertEqual(receipt.sync_status, "Submitted", receipt.last_error)
			self.assertFalse(receipt.validation_errors, [e.as_dict() for e in receipt.validation_errors])

	def test_numbers_continue_across_a_day_close_by_default(self):
		first = self._sale()
		request_close(first.fiscal_day)
		self._settle()
		nxt = self._sale()
		self.assertEqual(nxt.fiscal_day_no, first.fiscal_day_no + 1)
		self.assertEqual(nxt.receipt_global_no, first.receipt_global_no + 1)

	def test_near_limit_restarts_in_the_first_new_day_past_ninety_percent(self):
		first = self._sale()
		self._set("Near Limit", first.receipt_global_no + 1)  # first is past 90% of the limit
		same_day = self._sale()
		self.assertEqual(
			(same_day.fiscal_day_no, same_day.receipt_global_no),
			(first.fiscal_day_no, first.receipt_global_no + 1),
		)
		request_close(first.fiscal_day)
		self._settle()
		restarted = self._sale()
		self.assertEqual((restarted.fiscal_day_no, restarted.receipt_global_no), (first.fiscal_day_no + 1, 1))
		self.sync()
		self._assert_accepted(first, same_day, restarted)

	def test_reaching_the_limit_mid_day_closes_the_day_and_restarts(self):
		self._sale()
		self._set("Near Limit", self._last() + 1)
		at_limit = self._sale()
		restarted = self._sale()
		self.assertEqual(restarted.receipt_global_no, 1)
		self.assertEqual(restarted.receipt_counter, 1)
		self.assertEqual(restarted.fiscal_day_no, at_limit.fiscal_day_no + 1)
		self.assertFalse(json.loads(restarted.payload).get("previousReceiptHash"))
		self.assertEqual(
			frappe.db.get_value("Zimra Fiscal Day", at_limit.fiscal_day, "status"), "Close Requested"
		)

		self._settle()  # old day's receipts, its close, then the new day
		self.sync()
		self._assert_accepted(at_limit, restarted)
		self.assertEqual(frappe.db.get_value("Zimra Fiscal Day", at_limit.fiscal_day, "status"), "Closed")

	def test_a_credit_note_after_a_restart_refers_to_the_original_day(self):
		from erpnext.controllers.sales_and_purchase_return import make_return_doc

		invoice = make_invoice([(ITEM, 1, 10)])
		original = self.receipt_of(invoice)
		self._restart_at_next_sale()
		self.assertEqual(self._sale().receipt_global_no, 1)
		self._set(None, 0)
		self._settle()

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

	def test_every_fiscal_day_starts_each_new_day_at_one(self):
		self._set("Every Fiscal Day", 0)
		before = self._sale()
		request_close(before.fiscal_day)
		self._settle()
		first = self._sale()
		second = self._sale()
		self.assertEqual((first.fiscal_day_no, first.receipt_global_no), (before.fiscal_day_no + 1, 1))
		self.assertEqual(second.receipt_global_no, 2)
		self.sync()
		self._assert_accepted(before, first, second)

	def test_every_fiscal_day_also_rolls_over_at_the_limit(self):
		self._set("Every Fiscal Day", 0)
		self._sale()
		self._restart_at_next_sale()
		self._set("Every Fiscal Day", self._last())
		at_limit_day = frappe.db.get_value("Zimra Fiscal Device", self.device.name, "current_fiscal_day")
		restarted = self._sale()
		self.assertEqual(restarted.receipt_global_no, 1)
		self.assertNotEqual(restarted.fiscal_day, at_limit_day)
		self.assertEqual(frappe.db.get_value("Zimra Fiscal Day", at_limit_day, "status"), "Close Requested")

	def test_managers_are_told_when_the_limit_closes_a_day(self):
		self._sale()
		self._restart_at_next_sale()
		day = frappe.db.get_value("Zimra Fiscal Device", self.device.name, "current_fiscal_day")
		with patch("zw_compliance.fiscalisation.notifications.notify_managers") as notify:
			self._sale()
		notify.assert_called_once()
		self.assertEqual(notify.call_args.kwargs["reference"].name, day)
		comments = frappe.get_all(
			"Comment",
			filters={"reference_doctype": "Zimra Fiscal Day", "reference_name": day},
			pluck="content",
		)
		self.assertTrue(any("global receipt number limit" in c for c in comments), comments)

	def test_receipts_with_one_global_number_in_two_days_are_both_kept(self):
		self._sale()
		self._restart_at_next_sale()
		a = self._sale()
		self._restart_at_next_sale()  # the limit is now 1
		b = self._sale()
		self.assertEqual((a.receipt_global_no, b.receipt_global_no), (1, 1))
		self.assertEqual(b.fiscal_day_no, a.fiscal_day_no + 1)
