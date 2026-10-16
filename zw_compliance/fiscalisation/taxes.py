"""ERPNext tax set-up derived from ZIMRA's applicable taxes (getConfig, spec §4.4).

For every ZIMRA tax the company gets, the ERPNext way:

* **Sales**: an Item Tax Template and a Sales Taxes and Charges Template on the
  company's output VAT account (ERPNext's own Zimbabwe set-up account "VAT"
  is reused when it exists);
* **Purchases**: an Item Tax Template and a Purchase Taxes and Charges Template
  on an "Input VAT" account under Tax Assets, so supplier invoices carry the
  same rates for input tax and VAT returns. FDMS never sees purchases;
* a **Zimra Tax Mapping** tying the ZIMRA tax ID to all of the above. Receipt
  lines are matched to ZIMRA taxes through it (see :mod:`.builder`).

Zero-rated and exempt taxes post to the same VAT account at 0%; their
templates, not the account, tell them apart. Provisioning is additive and
idempotent: existing mappings keep any links a user has changed.
"""

from __future__ import annotations

import frappe
from erpnext.setup.setup_wizard.operations.taxes_setup import get_or_create_account

from zw_compliance.fdms.types import Tax

from .constants import TAX_MAPPING

OUTPUT_ACCOUNT = {"account_name": "VAT", "root_type": "Liability"}
INPUT_ACCOUNT = {"account_name": "Input VAT", "root_type": "Asset"}


def default_print_code(tax: Tax) -> str:
	"""Codes used in ZIMRA's own receipt examples (§10.1): VT, NV, EX."""
	if tax.tax_percent is None:
		return "EX"
	return "VT" if tax.tax_percent > 0 else "NV"


def _title(tax: Tax, suffix: str = "") -> str:
	return f"ZIMRA {tax.tax_name}{suffix}"[:140]


def _rate(tax: Tax) -> float:
	return float(tax.tax_percent or 0)


def _ensure_item_tax_template(company: str, title: str, account: str, rate: float) -> str:
	name = frappe.db.get_value("Item Tax Template", {"title": title, "company": company})
	if name:
		return name
	doc = frappe.get_doc(
		{
			"doctype": "Item Tax Template",
			"title": title,
			"company": company,
			"taxes": [{"tax_type": account, "tax_rate": rate}],
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def _ensure_charges_template(doctype: str, company: str, title: str, account: str, rate: float) -> str:
	name = frappe.db.get_value(doctype, {"title": title, "company": company})
	if name:
		return name
	row = {
		"charge_type": "On Net Total",
		"account_head": account,
		"description": title,
		"rate": rate,
		"category": "Total",
		"cost_center": frappe.get_cached_value("Company", company, "cost_center"),
	}
	if doctype == "Purchase Taxes and Charges Template":
		row["add_deduct_tax"] = "Add"
	doc = frappe.get_doc({"doctype": doctype, "title": title, "company": company, "taxes": [row]})
	doc.insert(ignore_permissions=True)
	return doc.name


def provision_taxes(company: str, taxes: list[Tax]) -> list[str]:
	"""Create whatever is missing for ``taxes``; returns the mapping names."""
	if not taxes:
		return []
	output_account = get_or_create_account(company, dict(OUTPUT_ACCOUNT)).name
	input_account = get_or_create_account(company, dict(INPUT_ACCOUNT)).name
	names = []
	for tax in taxes:
		rate = _rate(tax)
		existing = frappe.db.get_value(TAX_MAPPING, {"company": company, "zimra_tax_id": tax.tax_id})
		mapping = frappe.get_doc(TAX_MAPPING, existing) if existing else frappe.new_doc(TAX_MAPPING)
		mapping.company = company
		mapping.zimra_tax_id = tax.tax_id
		mapping.zimra_tax_name = tax.tax_name
		mapping.zimra_tax_percent = rate
		mapping.is_exempt = int(tax.tax_percent is None)
		mapping.valid_from = tax.valid_from
		mapping.valid_till = tax.valid_till
		mapping.print_code = mapping.print_code or default_print_code(tax)
		mapping.output_account = mapping.output_account or output_account
		mapping.input_account = mapping.input_account or input_account
		mapping.item_tax_template = mapping.item_tax_template or _ensure_item_tax_template(
			company, _title(tax), mapping.output_account, rate
		)
		mapping.sales_tax_template = mapping.sales_tax_template or _ensure_charges_template(
			"Sales Taxes and Charges Template", company, _title(tax), mapping.output_account, rate
		)
		mapping.purchase_item_tax_template = mapping.purchase_item_tax_template or _ensure_item_tax_template(
			company, _title(tax, " (Input)"), mapping.input_account, rate
		)
		mapping.purchase_tax_template = mapping.purchase_tax_template or _ensure_charges_template(
			"Purchase Taxes and Charges Template",
			company,
			_title(tax, " (Input)"),
			mapping.input_account,
			rate,
		)
		mapping.save(ignore_permissions=True)
		names.append(mapping.name)
	_ensure_default_templates(company)
	return names


def _ensure_default_templates(company: str) -> None:
	"""Make the standard-rated templates the company default, unless the
	business already chose a default; ERPNext's ``set_taxes`` then applies VAT
	to every new sales and purchase document without anyone picking it."""
	standard = frappe.get_all(
		TAX_MAPPING,
		filters={"company": company, "is_exempt": 0, "zimra_tax_percent": [">", 0]},
		fields=["sales_tax_template", "purchase_tax_template"],
		order_by="zimra_tax_percent desc",
		limit=1,
	)
	if not standard:
		return
	for doctype, template in (
		("Sales Taxes and Charges Template", standard[0].sales_tax_template),
		("Purchase Taxes and Charges Template", standard[0].purchase_tax_template),
	):
		if template and not frappe.db.exists(doctype, {"company": company, "is_default": 1}):
			frappe.db.set_value(doctype, template, "is_default", 1)


def company_mappings(company: str) -> list:
	return frappe.get_all(
		TAX_MAPPING,
		filters={"company": company},
		fields=[
			"name",
			"zimra_tax_id",
			"zimra_tax_name",
			"zimra_tax_percent",
			"is_exempt",
			"print_code",
			"valid_from",
			"valid_till",
			"item_tax_template",
			"sales_tax_template",
			"output_account",
		],
	)
