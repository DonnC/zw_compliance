"""Sales never stop because ZIMRA cannot be reached, however long it lasts."""

from __future__ import annotations

import json
from datetime import timedelta

import frappe
from frappe.utils import add_days, now_datetime

from .fixtures import ITEM, FiscalisationTestCase, make_invoice


class TestOutageResilience(FiscalisationTestCase):
	def setUp(self):
		self.addCleanup(frappe.db.set_single_value, "Zimra Settings", "auto_open_day", 1)
		valid_till = frappe.db.get_value("Zimra Fiscal Device", self.device.name, "certificate_valid_till")
		self.addCleanup(
			frappe.db.set_value, "Zimra Fiscal Device", self.device.name, "certificate_valid_till", valid_till
		)

	def test_a_day_past_its_length_rolls_over_even_without_automatic_opening(self):
		first = self.receipt_of(make_invoice([(ITEM, 1, 10)]))
		frappe.db.set_value(
			"Zimra Fiscal Day", first.fiscal_day, "fiscal_day_opened", now_datetime() - timedelta(hours=30)
		)
		frappe.db.set_value("Zimra Fiscal Device", self.device.name, "day_max_hours", 24)
		frappe.db.set_single_value("Zimra Settings", "auto_open_day", 0)

		self.fdms.faults.drop_connections = 100  # ZIMRA is down throughout
		nxt = self.receipt_of(make_invoice([(ITEM, 1, 10)]))
		self.assertEqual(nxt.fiscal_day_no, first.fiscal_day_no + 1)
		self.assertEqual(nxt.sync_status, "Queued")
		self.assertEqual(
			frappe.db.get_value("Zimra Fiscal Day", first.fiscal_day, "status"), "Close Requested"
		)
		self.fdms.faults.drop_connections = 0

	def test_an_expired_certificate_warns_but_does_not_stop_sales(self):
		frappe.db.set_value(
			"Zimra Fiscal Device", self.device.name, "certificate_valid_till", add_days(now_datetime(), -1)
		)
		frappe.local.message_log = []
		invoice = make_invoice([(ITEM, 1, 10)])
		receipt = self.receipt_of(invoice)
		self.assertEqual(invoice.docstatus, 1)
		self.assertEqual(receipt.sync_status, "Queued")
		messages = [
			json.loads(m).get("message", "") if isinstance(m, str) else m.get("message", "")
			for m in frappe.local.message_log
		]
		self.assertTrue(any("expired" in m for m in messages), messages)
