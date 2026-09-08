# Copyright (c) 2026, Donald Chinhuru and contributors
# For license information, please see license.txt

"""Pure-logic tests for the document-level discount proration in
receipt_builder.py - no DB/site state needed, so plain UnitTestCase."""

from __future__ import annotations

from frappe.tests import UnitTestCase

from zw_compliance.zimra.receipt_builder import _proportional_split
from zw_compliance.zimra.receipt_builder import _build_discount_lines
from zw_compliance.fdms.types import ReceiptLineInput
from zw_compliance.fdms.money import cents


class UnitTestReceiptBuilderDiscounts(UnitTestCase):
	def test_proportional_split_sums_exactly(self):
		for total, weights in [(1000, [3, 3, 4]), (100, [1, 1, 1]), (1, [1, 1, 1]), (0, [1, 2]), (-500, [2, 3])]:
			shares = _proportional_split(total, weights)
			self.assertEqual(sum(shares), total)
			self.assertEqual(len(shares), len(weights))

	def test_proportional_split_empty_weights_is_zero(self):
		self.assertEqual(_proportional_split(500, [0, 0]), [0, 0])

	def test_discount_lines_prorate_across_tax_groups_and_balance(self):
		lines = [
			ReceiptLineInput(name="A", price=cents("100.00"), quantity=1, tax_id=1, tax_percent=15),
			ReceiptLineInput(name="B", price=cents("50.00"), quantity=1, tax_id=2, tax_percent=0),
		]
		doc = {"base_discount_amount": 15.0}

		class _Doc(dict):
			def get(self, k, default=None):
				return dict.get(self, k, default)

		discount_lines = _build_discount_lines(_Doc(doc), lines)
		total_discount_cents = sum(round(dl.price.cents * dl.quantity) for dl in discount_lines)
		self.assertEqual(total_discount_cents, -1500)
		# split 2/3 - 1/3 by weight (100 vs 50)
		self.assertEqual(len(discount_lines), 2)

	def test_no_discount_lines_when_no_additional_discount(self):
		lines = [ReceiptLineInput(name="A", price=cents("100.00"), quantity=1, tax_id=1, tax_percent=15)]

		class _Doc(dict):
			def get(self, k, default=None):
				return dict.get(self, k, default)

		self.assertEqual(_build_discount_lines(_Doc({}), lines), [])
