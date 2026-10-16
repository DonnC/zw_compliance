# Copyright (c) 2026, Donald Chinhuru and Contributors
# See license.txt

import frappe

from zw_compliance.testing import UnitTestCase


class UnitTestZimraHSNCode(UnitTestCase):
	def make(self, code):
		return frappe.get_doc({"doctype": "Zimra HSN Code", "hsn_code": code, "description": "test item"})

	def test_receipt_usable_codes_are_four_or_eight_digits(self):
		for code, usable in (("8471", 1), ("84713000", 1), ("847130", 0), ("84", 0)):
			doc = self.make(code)
			doc.validate()
			self.assertEqual((doc.code_length, doc.usable_on_receipt), (len(code), usable))
			self.assertEqual(doc.description, "TEST ITEM")

	def test_non_digit_codes_are_rejected(self):
		self.assertRaises(frappe.ValidationError, self.make("8471.30").validate)
