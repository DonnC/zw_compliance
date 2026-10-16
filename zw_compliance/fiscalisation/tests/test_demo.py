"""The demo seeder and the workspace data it feeds."""

from __future__ import annotations

import json
from unittest.mock import patch

import frappe

from zw_compliance.fiscalisation import dashboard, demo
from zw_compliance.testing import IntegrationTestCase


class TestDemo(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls._no_commit = patch.object(frappe.db, "commit", lambda *a, **k: None)
		cls._no_commit.start()
		cls.addClassCleanup(cls._no_commit.stop)
		demo.seed(days=2)

	def test_history_is_fiscalised_and_days_are_closed(self):
		days = frappe.get_all(
			"Zimra Fiscal Day",
			filters={"company": demo.COMPANY},
			fields=["status", "server_signature_verified"],
			order_by="fiscal_day_no",
		)
		self.assertGreaterEqual(len(days), 2)
		self.assertEqual(days[-1].status, "Opened")  # today
		self.assertTrue(all(d.status == "Closed" and d.server_signature_verified for d in days[:-1]))
		statuses = frappe.get_all(
			"Zimra Fiscal Receipt", filters={"company": demo.COMPANY}, pluck="sync_status"
		)
		self.assertEqual(sum(s != "Submitted" for s in statuses), 2)  # sold while the line was down
		self.assertFalse(
			frappe.db.sql(
				"""select e.name from `tabZimra Validation Error` e
				join `tabZimra Fiscal Receipt` r on r.name = e.parent where r.company = %s""",
				demo.COMPANY,
			)
		)

	def test_demo_device_never_reaches_the_network(self):
		from zw_compliance.fiscalisation.device import connect

		device = frappe.get_doc("Zimra Fiscal Device", f"ZFD-{demo.DEVICE_ID}")
		self.assertTrue(device.is_demo)
		with self.assertRaises(frappe.ValidationError), connect(device):
			pass

	def test_workspace_cards_and_charts(self):
		filters = json.dumps({"company": demo.COMPANY})
		self.assertEqual(dashboard.unfiscalised_invoices(filters)["value"], 0)
		self.assertGreater(dashboard.certificate_days_left(filters)["value"], 300)
		self.assertLessEqual(dashboard.fiscal_day_hours_left(filters)["value"], 24)
		chart = json.dumps({"name": "test", "filters_json": filters})
		sales = dashboard.fiscal_sales(no_cache=1, chart=chart)
		self.assertEqual(sales["datasets"][0]["name"], "USD")
		self.assertGreater(sum(sales["datasets"][0]["values"]), 0)
		vat = dashboard.output_vat_by_tax(no_cache=1, chart=chart)
		self.assertTrue(vat["labels"])

	def test_wipe_removes_everything(self):
		demo.wipe()
		self.assertFalse(frappe.db.exists("Company", demo.COMPANY))
		self.assertFalse(frappe.db.count("Zimra Fiscal Receipt", {"company": demo.COMPANY}))
		self.assertFalse(frappe.db.count("GL Entry", {"company": demo.COMPANY}))
		self.assertFalse(frappe.db.exists("Customer", {"name": ["like", f"{demo.PREFIX}%"]}))
		demo.seed(days=1)  # and it can be seeded again
