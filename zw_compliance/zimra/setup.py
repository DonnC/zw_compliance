"""Device bootstrap: registration and post-registration tax/config sync.
Split out from connector.py because registration happens *before* the
device has a certificate, so it can't go through ZimraConnector.device()
(which requires one to already exist).
"""

from __future__ import annotations

from datetime import datetime

import frappe
from frappe import _
from frappe.utils import flt

from zw_compliance.fdms.registration import register_device as fdms_register_device
from zw_compliance.fdms.types import DeviceIdentity

from .signer import generate_device_key_pair
from .transport import RequestsTransport


def _save_private_file(file_name: str, content: str) -> str:
	doc = frappe.get_doc({"doctype": "File", "file_name": file_name, "is_private": 1, "content": content})
	doc.insert(ignore_permissions=True)
	return doc.file_url


def register_device(settings) -> dict:
	"""Step 1 of onboarding: generate a device key pair, submit a CSR with
	the activation key from the FDMS device portal, and persist the
	returned certificate + private key as private Files.
	"""
	if not settings.activation_key:
		frappe.throw(_("Set an Activation Key from the FDMS device portal before registering."))

	identity = DeviceIdentity(
		device_id=settings.device_id,
		serial_number=settings.serial_no,
		model_name=settings.model_name or "Server",
		model_version=settings.model_version or "v1",
	)
	environment = "production" if settings.environment == "Production" else "test"
	private_key_pem, private_key = generate_device_key_pair()
	transport = RequestsTransport()  # Public endpoint - ordinary TLS, no client cert yet

	result = fdms_register_device(
		identity, settings.activation_key, private_key, transport, environment=environment, base_url=settings.api_url or None
	)

	stamp = datetime.now().strftime("%Y%m%d%H%M%S")
	cert_url = _save_private_file(f"zimra_cert_{settings.device_id}_{stamp}.pem", result.certificate_pem)
	key_url = _save_private_file(f"zimra_key_{settings.device_id}_{stamp}.pem", private_key_pem)

	settings.db_set("device_certificate", cert_url)
	settings.db_set("private_key", key_url)
	settings.reload()
	return {"operationId": result.operation_id, "commonName": result.common_name}


def renew_certificate(settings) -> dict:
	"""Renew the device certificate (same key, new certificate) via FDMS's
	IssueCertificate, over mTLS using the *current* (not-yet-expired)
	certificate. If the certificate has already expired, this call will
	fail - mTLS needs a certificate FDMS still trusts to authenticate the
	renewal request in the first place - and the device must be re-onboarded
	through ZIMRA directly. That's an FDMS protocol constraint, not
	something this app can route around.
	"""
	from .connector import ZimraConnector

	connector = ZimraConnector(settings)
	device = connector.device()
	private_key = connector._file_content(settings.private_key)
	from cryptography.hazmat.primitives import serialization

	key = serialization.load_pem_private_key(private_key.encode(), password=None)

	result = device.renew_certificate(key)

	stamp = datetime.now().strftime("%Y%m%d%H%M%S")
	cert_url = _save_private_file(f"zimra_cert_{settings.device_id}_{stamp}.pem", result["certificatePem"])
	settings.db_set({"device_certificate": cert_url, "last_certificate_renewal": frappe.utils.now_datetime()})
	settings.reload()

	# Refresh certificate_valid_till from FDMS now that the new cert is live
	# (sync_config builds its own fresh ZimraConnector against the new cert).
	sync_config(settings)
	return {"operationId": result["operationId"]}


def sync_config(settings) -> dict:
	"""Step 2: pull taxpayer info + applicable taxes from FDMS (now that the
	device has a certificate) and provision matching ERPNext tax accounts,
	Sales Taxes and Charges Templates, and Zimra Tax Mapping rows.
	"""
	from .connector import ZimraConnector

	connector = ZimraConnector(settings)
	config = connector.device().get_config()

	settings.db_set(
		{
			"taxpayer_name": config.tax_payer_name,
			"taxpayer_tin": config.tax_payer_tin,
			"vat_number": config.vat_number,
			"device_operating_mode": config.device_operating_mode,
			"qr_url": config.qr_url,
			"certificate_valid_till": config.certificate_valid_till,
		}
	)
	settings.reload()

	created = []
	for tax in config.applicable_taxes:
		created.append(_sync_tax(settings, tax))
	return {"taxpayerName": config.tax_payer_name, "taxesSynced": len(created)}


def _sync_tax(settings, tax) -> str:
	"""Provisions everything "the ERPNext way" for one ZIMRA tax rate:
	an Output VAT liability account + Sales Taxes and Charges Template +
	Item Tax Template for the sales side (what receipt_builder.py maps
	receipt lines against), and an Input VAT asset account + Purchase
	Taxes and Charges Template + Item Tax Template for the purchase side -
	FDMS never sees purchases, but a business dealing with suppliers still
	needs the same VAT rate applied correctly for input tax credit and
	VAT return reconciliation, with the GL split into a separate account
	from output VAT. All of this is additive/idempotent: re-running
	sync_config only creates what's missing and never touches an existing
	mapping's manually-adjusted links.
	"""
	company = settings.company
	company_abbr = frappe.get_cached_value("Company", company, "abbr")
	rate = flt(tax.tax_percent or 0)
	label = f"{tax.tax_name} ({rate}%)" if tax.tax_percent is not None else tax.tax_name

	existing_name = frappe.db.get_value("Zimra Tax Mapping", {"zimra_tax_id": tax.tax_id})
	mapping = frappe.get_doc("Zimra Tax Mapping", existing_name) if existing_name else frappe.new_doc("Zimra Tax Mapping")

	# --- Sales side (output VAT) - what receipt_builder.py maps against ---
	output_account = mapping.get("erpnext_account") or _ensure_tax_account(
		f"VAT {rate}% - {company_abbr}" if tax.tax_percent is not None else f"VAT Exempt - {company_abbr}",
		rate,
		company,
		root_type="Liability",
		group_hint="Duties and Taxes",
	)
	sales_template = _ensure_sales_tax_template(f"ZIMRA - {label}", output_account, rate, company)
	sales_item_template = _ensure_item_tax_template(f"ZIMRA - {label}", output_account, rate, company)

	# --- Purchase side (input VAT) - separate account, same rate ---
	input_account = mapping.get("erpnext_input_account") or _ensure_tax_account(
		f"Input VAT {rate}% - {company_abbr}" if tax.tax_percent is not None else f"Input VAT Exempt - {company_abbr}",
		rate,
		company,
		root_type="Asset",
		group_hint="Duties and Taxes",
	)
	purchase_template = _ensure_purchase_tax_template(f"ZIMRA - {label} (Input)", input_account, rate, company)
	purchase_item_template = _ensure_item_tax_template(f"ZIMRA - {label} (Input)", input_account, rate, company)

	mapping.update(
		{
			"zimra_tax_id": tax.tax_id,
			"zimra_tax_percent": tax.tax_percent,
			"zimra_tax_name": tax.tax_name,
			"erpnext_tax_template": sales_template,
			"erpnext_item_tax_template": sales_item_template,
			"erpnext_account": output_account,
			"erpnext_purchase_tax_template": purchase_template,
			"erpnext_purchase_item_tax_template": purchase_item_template,
			"erpnext_input_account": input_account,
		}
	)
	mapping.save(ignore_permissions=True)
	return sales_template


def _ensure_tax_account(account_name: str, rate: float, company: str, root_type: str, group_hint: str) -> str:
	existing = frappe.db.get_value("Account", {"account_name": account_name, "company": company})
	if existing:
		return existing

	parent = frappe.db.get_value(
		"Account", {"root_type": root_type, "is_group": 1, "account_type": "Tax", "company": company}
	)
	if not parent:
		parent = frappe.db.get_value(
			"Account",
			{"account_name": ["like", f"%{group_hint}%"], "company": company, "is_group": 1, "root_type": root_type},
		)
	if not parent:
		# Fall back to the root_type's own top-level group rather than hard
		# failing - most Zimbabwean CoAs won't have a pre-existing
		# asset-side "Duties and Taxes" group for input VAT.
		parent = frappe.db.get_value("Account", {"root_type": root_type, "is_group": 1, "parent_account": ["is", "not set"]})
	if not parent:
		frappe.throw(
			_("Could not find a '{0}' group account under {1} for company {2}. Create one first.").format(
				group_hint, root_type, company
			)
		)

	account = frappe.get_doc(
		{
			"doctype": "Account",
			"account_name": account_name,
			"parent_account": parent,
			"account_type": "Tax",
			"tax_rate": rate,
			"company": company,
		}
	)
	account.insert(ignore_permissions=True)
	return account.name


def _ensure_sales_tax_template(title: str, account_head: str, rate: float, company: str) -> str:
	existing = frappe.db.get_value("Sales Taxes and Charges Template", {"title": title, "company": company})
	if existing:
		return existing
	template = frappe.get_doc(
		{
			"doctype": "Sales Taxes and Charges Template",
			"title": title,
			"company": company,
			"taxes": [
				{"charge_type": "On Net Total", "account_head": account_head, "description": title, "rate": rate}
			],
		}
	)
	template.insert(ignore_permissions=True)
	return template.name


def _ensure_purchase_tax_template(title: str, account_head: str, rate: float, company: str) -> str:
	existing = frappe.db.get_value("Purchase Taxes and Charges Template", {"title": title, "company": company})
	if existing:
		return existing
	template = frappe.get_doc(
		{
			"doctype": "Purchase Taxes and Charges Template",
			"title": title,
			"company": company,
			"taxes": [
				{"charge_type": "On Net Total", "account_head": account_head, "description": title, "rate": rate}
			],
		}
	)
	template.insert(ignore_permissions=True)
	return template.name


def _ensure_item_tax_template(title: str, account_head: str, rate: float, company: str) -> str:
	"""Item Tax Template is shared by Sales and Purchase Invoice Items alike
	(item.item_tax_template) - the GL treatment comes from which account the
	template's row points at, so sales and purchase need their own template
	even at the same rate.
	"""
	existing = frappe.db.get_value("Item Tax Template", {"title": title, "company": company})
	if existing:
		return existing
	template = frappe.get_doc(
		{
			"doctype": "Item Tax Template",
			"title": title,
			"company": company,
			"taxes": [{"tax_type": account_head, "tax_rate": rate}],
		}
	)
	template.insert(ignore_permissions=True)
	return template.name
