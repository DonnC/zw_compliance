"""Test data for fiscalisation integration tests.

Everything is created on demand inside the test transaction (see
:class:`FiscalisationTestCase`), so nothing is left behind on the site. The
fixtures do not rely on ERPNext's own test records, whose dependency chain
(Company -> Fiscal Year ...) collides with real data on development sites.
"""

from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.utils import getdate, today

from zw_compliance.testing import IntegrationTestCase

from .simulator import FakeFdms

COMPANY = "_Test ZW Company"
ABBR = "_TZW"
DEVICE_ID = 9001
SERIAL = "ZWTEST-01"
WALK_IN = "_Test ZW Walk-in"
BUSINESS = "_Test ZW Business"
BUSINESS_TIN = "2000123456"
ITEM = "_Test ZW Laptop"
ITEM_ZERO = "_Test ZW Maize Meal"
ITEM_EXEMPT = "_Test ZW Tuition"
GROUP = "_Test ZW Group"


def ensure_fiscal_year() -> None:
	if frappe.db.sql(
		"select name from `tabFiscal Year` where %s between year_start_date and year_end_date", today()
	):
		return
	year = getdate(today()).year
	frappe.get_doc(
		{
			"doctype": "Fiscal Year",
			"year": f"_Test ZW FY {year}",
			"year_start_date": f"{year}-01-01",
			"year_end_date": f"{year}-12-31",
		}
	).insert(ignore_permissions=True)


def ensure_company() -> None:
	if frappe.db.exists("Company", COMPANY):
		return
	frappe.get_doc(
		{
			"doctype": "Company",
			"company_name": COMPANY,
			"abbr": ABBR,
			"default_currency": "USD",
			"country": "Zimbabwe",
			"chart_of_accounts": "Standard",
		}
	).insert(ignore_permissions=True)


def ensure_customer(name: str, tax_id: str | None = None, vat: str | None = None) -> str:
	if not frappe.db.exists("Customer", name):
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": name,
				"customer_group": "All Customer Groups",
				"territory": "All Territories",
				"tax_id": tax_id,
				"zimra_vat_number": vat,
			}
		).insert(ignore_permissions=True)
	return name


def ensure_hs_code(code: str) -> str:
	if not frappe.db.exists("Zimra HSN Code", code):
		frappe.get_doc({"doctype": "Zimra HSN Code", "hsn_code": code, "description": "test"}).insert(
			ignore_permissions=True
		)
	return code


def ensure_item(code: str, hs_code: str | None, item_tax_template: str | None = None) -> str:
	if not frappe.db.exists("Item Group", GROUP):
		frappe.get_doc(
			{"doctype": "Item Group", "item_group_name": GROUP, "parent_item_group": "All Item Groups"}
		).insert(ignore_permissions=True)
	if not frappe.db.exists("Item", code):
		item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": code,
				"item_name": code.replace("_Test ZW ", ""),
				"item_group": GROUP,
				"stock_uom": "Nos",
				"is_stock_item": 0,
				"zimra_hs_code": ensure_hs_code(hs_code) if hs_code else None,
			}
		)
		if item_tax_template:
			item.append("taxes", {"item_tax_template": item_tax_template})
		item.insert(ignore_permissions=True)
	return code


def configure_settings() -> None:
	settings = frappe.get_doc("Zimra Settings")
	settings.allowed_currencies = "USD"
	settings.unpaid_money_type = "Credit"
	settings.advance_money_type = "BankTransfer"
	settings.auto_open_day = 1
	settings.close_margin_hours = 1
	settings.set("payment_mode_mapping", [])
	settings.save(ignore_permissions=True)


def mapping(tax_id: int):
	return frappe.get_doc("Zimra Tax Mapping", {"company": COMPANY, "zimra_tax_id": tax_id})


def onboard_device(fdms: FakeFdms):
	"""Draft -> Taxpayer Verified -> Registered -> Active, against the fake FDMS."""
	from zw_compliance.fiscalisation import device as lifecycle

	name = f"ZFD-{DEVICE_ID}"
	if not frappe.db.exists("Zimra Fiscal Device", name):
		frappe.get_doc(
			{
				"doctype": "Zimra Fiscal Device",
				"device_name": "Test Till",
				"company": COMPANY,
				"environment": "Test",
				"device_id": DEVICE_ID,
				"serial_no": SERIAL,
				"model_name": "ERPNext",
				"model_version": "v1",
				"activation_key": "ABCD1234",
				"is_default": 1,
			}
		).insert(ignore_permissions=True)
	lifecycle.verify_taxpayer(name)
	lifecycle.register(name)
	return frappe.get_doc("Zimra Fiscal Device", name)


def make_invoice(items, customer=WALK_IN, tax_id: int = 1, submit: bool = True, **fields):
	"""``items``: ``[(item_code, qty, rate)]``; taxes come from the ZIMRA tax template of ``tax_id``."""
	invoice = frappe.new_doc("Sales Invoice")
	invoice.update({"company": COMPANY, "customer": customer, "currency": "USD", **fields})
	for code, qty, rate in items:
		invoice.append("items", {"item_code": code, "qty": qty, "rate": rate})
	invoice.taxes_and_charges = mapping(tax_id).sales_tax_template
	invoice.set_missing_values()
	for row, (_code, _qty, rate) in zip(invoice.items, items, strict=True):
		row.price_list_rate = row.rate = rate
	invoice.set("taxes", [])
	invoice.set_taxes()
	invoice.calculate_taxes_and_totals()
	invoice.insert(ignore_permissions=True)
	if submit:
		invoice.submit()
	return invoice


class FiscalisationTestCase(IntegrationTestCase):
	"""Sets up a company, a fake FDMS and an Active fiscal device.

	The sync job commits as it goes (correct in production); commits are
	disabled here so the class cleanup rolls every change back.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls._no_commit = patch.object(frappe.db, "commit", lambda *a, **k: None)
		cls._no_commit.start()
		cls.addClassCleanup(cls._no_commit.stop)
		cls.fdms = FakeFdms()
		frappe.flags.zimra_transport_factory = cls.fdms.factory()
		ensure_fiscal_year()
		ensure_company()
		configure_settings()
		ensure_customer(WALK_IN)
		ensure_customer(BUSINESS, tax_id=BUSINESS_TIN, vat="220000777")
		cls.device = onboard_device(cls.fdms)
		ensure_item(ITEM, "84713000")
		ensure_item(ITEM_ZERO, "11022000", mapping(2).item_tax_template)
		ensure_item(ITEM_EXEMPT, "49019900", mapping(3).item_tax_template)

	@classmethod
	def tearDownClass(cls):
		frappe.flags.zimra_transport_factory = None
		super().tearDownClass()

	def sync(self):
		from zw_compliance.fiscalisation.sync import _run

		_run(self.device.name)

	def receipt_of(self, invoice):
		invoice.reload()
		return frappe.get_doc("Zimra Fiscal Receipt", invoice.zimra_fiscal_receipt)
