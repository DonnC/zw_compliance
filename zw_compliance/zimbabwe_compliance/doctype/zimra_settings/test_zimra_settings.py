# Copyright (c) 2026, Donald Chinhuru and Contributors
# See license.txt

import frappe
from frappe.tests import UnitTestCase


class UnitTestZimraSettings(UnitTestCase):
	"""
	Unit tests for ZimraSettings - deliberately UnitTestCase, not
	IntegrationTestCase: these tests only call validate() in memory and
	never insert a document, so they don't need IntegrationTestCase's
	automatic test-record generation for linked doctypes (Company ->
	Fiscal Year ...), which fails on sites carrying a Fiscal Year record
	that overlaps erpnext's own standard test fixtures - a pre-existing
	site data issue unrelated to this app.
	"""

	def test_allowed_currencies_is_normalised_on_validate(self):
		# validate() (not save()) so this never touches the DB - the live
		# singleton on a real site may not have all mandatory fields set yet.
		settings = frappe.get_single("Zimra Settings")
		settings.allowed_currencies = " usd ,zwg,, usd"
		settings.validate()
		self.assertEqual(settings.allowed_currencies, "USD,ZWG,USD")

	def test_blank_allowed_currencies_does_not_raise(self):
		settings = frappe.get_single("Zimra Settings")
		settings.allowed_currencies = None
		settings.validate()  # should not throw - only non-blank values are normalised
