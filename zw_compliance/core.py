# Copyright (c) 2026, Donald Chinhuru and contributors
# For license information, please see license.txt

import os
import json
from datetime import datetime
import base64

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.backends import default_backend
import hashlib

import frappe

from zw_compliance.constants import ZimraConstants as ZC


class ZimraConnector:
    def __init__(self):
        self.settings = frappe.get_doc("Zimra Settings")
        self.base_url = "https://fdmsapi.zimra.co.zw" if not self.settings.is_test else "https://fdmsapitest.zimra.co.zw"
        self.headers = {
            "DeviceModelName": self.settings.device_model_name,
            "DeviceModelVersionNo": self.settings.device_model_version,
            "Content-Type": "application/json"
        }

    def archive_certificate(self, file_url, cert_type, expiry_date):
        """Renames expired certificates using the requested YYYYMMDD format."""
        if not file_url: return
        old_filename = file_url.split('/')[-1]
        old_path = frappe.get_site_path('private', 'files', old_filename)
        
        new_filename = f"exp_zimra_{cert_type}_{self.settings.device_id}_{expiry_date}.pem"
        new_path = frappe.get_site_path('private', 'files', new_filename)
        
        if os.path.exists(old_path):
            os.rename(old_path, new_path)
            frappe.msgprint(f"Archived old certificate to {new_filename}")

    def save_new_certificate(self, cert_content, key_pem=None):
        """Saves new certs with date-stamped filenames: zimra_TYPE_ID_YYYYMMDD.pem."""
        date_str = datetime.now().strftime("%Y%m%d")
        cert_name = f"zimra_CERT_{self.settings.device_id}_{date_str}.pem"
        
        _cert_file = frappe.get_doc({
            "doctype": "File", "file_name": cert_name, "is_private": 1, "content": cert_content
        }).insert()
        self.settings.device_certificate = _cert_file.file_url

        if key_pem:
            key_name = f"zimra_KEY_{self.settings.device_id}_{date_str}.pem"
            _key_file = frappe.get_doc({
                "doctype": "File", "file_name": key_name, "is_private": 1, "content": key_pem
            }).insert()
            self.settings.private_key = _key_file.file_url
        
        self.settings.save()

    def fiscalise_invoice(self, doc):
        """
        Consolidated submission logic for Invoices, POS, and Returns.
        """
        # 1. Determine Receipt Type
        receipt_type = ZC.FISCAL_INVOICE
        if doc.is_return:
            # Credit Note if negative (refund), Debit Note if positive (addition)
            receipt_type = ZC.CREDIT_NOTE if doc.base_grand_total < 0 else ZC.DEBIT_NOTE
            self.validate_return(doc) # Mandatory RCPT033 check

        # 2. Daily Sequence Management
        self.settings.receipt_counter += 1
        self.settings.global_receipt_no += 1
        self.settings.save()

        # 3. Build Payload
        payload = {
            "receiptType": receipt_type,
            "receiptCurrency": doc.currency,
            "receiptCounter": self.settings.receipt_counter,
            "receiptGlobalNo": self.settings.global_receipt_no,
            "invoiceNo": doc.name, # Unique taxpayer reference
            "receiptDate": get_datetime(doc.posting_date).strftime('%Y-%m-%dT%H:%M:%S'),
            "receiptLinesTaxInclusive": True,
            "receiptLines": self.get_lines(doc),
            "receiptTaxes": self.get_aggregated_taxes(doc),
            "receiptPayments": self.get_payments(doc),
            "receiptTotal": doc.base_grand_total,
            # Use Receipt48 for POS, else InvoiceA4
            "receiptPrintForm": ZC.FORM_48 if doc.is_pos else ZC.FORM_A4,
            "receiptNotes": doc.remarks or "Standard Transaction" # Mandatory for returns
        }

        # 4. Attach Return Reference
        if doc.is_return:
            payload["creditDebitNote"] = self.get_return_reference(doc)

        # 5. Cryptographic Chaining and Transmission
        payload["receiptDeviceSignature"] = self.generate_signature(payload)
        return self.transmit_to_zimra(doc, payload)

    def validate_return(self, doc):
        """
        Enforces Rule RCPT033: Returns must be within 12 months of original invoice.
        """
        orig_date = frappe.db.get_value("Sales Invoice", doc.return_against, "posting_date")
        if not orig_date:
            frappe.throw(f"Original invoice {doc.return_against} not found.")
            
        if get_datetime(orig_date) < add_months(now_datetime(), -12):
            frappe.throw(f"Return denied (RCPT033): Original invoice is older than 12 months.")

    def get_return_reference(self, doc):
        """
        Maps ZIMRA metadata from the original invoice for return tracking.
        """
        orig = frappe.get_doc("Sales Invoice", doc.return_against)
        return {
            "receiptID": int(orig.zimra_receipt_id),
            "deviceID": self.settings.device_id,
            "receiptGlobalNo": orig.zimra_global_no,
            "fiscalDayNo": orig.zimra_fiscal_day_no
        }

    def get_aggregated_taxes(self, lines):
        """Groups individual lines into the receiptTaxes summary."""
        summary = {}
        for l in lines:
            tid = l["taxID"]
            if tid not in summary:
                summary[tid] = {"taxID": tid, "taxPercent": l["taxPercent"], "taxCode": l["taxCode"], "taxAmount": 0, "salesAmountWithTax": 0}
            
            # taxAmount = SUM (lineTotal) * taxPercent / (1 + taxPercent)
            tax_val = 0
            if l["taxPercent"] > 0:
                tax_val = l["receiptLineTotal"] * (l["taxPercent"] / (100 + l["taxPercent"]))
            
            summary[tid]["taxAmount"] += tax_val
            summary[tid]["salesAmountWithTax"] += l["receiptLineTotal"]

        return [{
            "taxCode": v["taxCode"], "taxPercent": v["taxPercent"], "taxID": v["taxID"],
            "taxAmount": round(v["taxAmount"], 2), "salesAmountWithTax": round(v["salesAmountWithTax"], 2)
        } for v in summary.values()]

    def get_return_reference(self, doc):
        """Fetches ZIMRA metadata from the original ERPNext invoice."""
        orig_name = doc.return_against
        orig_data = frappe.db.get_value("Sales Invoice", orig_name, 
            ["zimra_receipt_id", "zimra_global_no", "zimra_fiscal_day_no"], as_dict=True)
        
        if not orig_data:
            frappe.throw(f"Original invoice {orig_name} not found or not fiscalised.")
            
        # ZIMRA requires either receiptID OR (deviceID + globalNo + dayNo).
        return {
            "receiptID": int(orig_data.zimra_receipt_id),
            "deviceID": self.settings.device_id,
            "receiptGlobalNo": orig_data.zimra_global_no,
            "fiscalDayNo": orig_data.zimra_fiscal_day_no
        }


    def get_lines(self, doc):
        """
        Builds the receiptLines array by identifying the specific tax applied 
        to each individual item in the ERPNext Sales Invoice.
        """
        receipt_lines = []
        is_vat_payer = bool(self.settings.vat_number) # Taxpayer is VAT registered if VAT number exists
        
        for i, item in enumerate(doc.items):
            # 1. Identify the applied tax for this specific item
            # ERPNext handles item-level taxes via 'Item Tax Template' or granularly in the taxes table.
            # We lookup the mapping using the Account Head or the Template name.
            applied_tax_template = item.item_tax_template or doc.taxes_and_charges
            
            tax_mapping = frappe.db.get_value(
                "Zimra Tax Mapping", 
                {"erpnext_tax_template": applied_tax_template}, 
                ["zimra_tax_id", "zimra_tax_percent", "zimra_tax_code"], 
                as_dict=True
            )

            if not tax_mapping:
                frappe.log_error(f"ZIMRA Mapping Missing for {applied_tax_template}", "Zimra Tax Error")
                frappe.throw(f"Item {item.item_code}: No ZIMRA Tax Mapping found for template {applied_tax_template}.")

            # 2. HS Code Retrieval and Validation
            # HS Code is mandatory for VAT registered operators (National Harmonized System).
            hs_code = (frappe.db.get_value("Item", item.item_code, "hs_code") or "").replace(".", "")

            # TODO: call self.get_hsn_code(doc)
            
            if is_vat_payer:
                if not hs_code:
                    frappe.throw(f"Item {item.item_code}: National HS Code is mandatory for VAT-registered operators.")
                
                # Length validation: 8 digits for 0%/exempt; 4 or 8 digits for standard rates.
                if tax_mapping.zimra_tax_percent == 0 and len(hs_code) != 8:
                    frappe.throw(f"Item {item.item_code}: 0% or Exempt lines require an 8-digit HS Code.")
                elif tax_mapping.zimra_tax_percent > 0 and len(hs_code) not in:
                    frappe.throw(f"Item {item.item_code}: Standard tax lines require a 4 or 8-digit HS Code.")

            # 3. Determine Line Type
            # Standard items are 'Sale'. Negative values (discounts) are 'Discount' line types.
            line_type = ZC.LINE_SALE
            if item.base_amount < 0:
                line_type = ZC.LINE_DISCOUNT

            # 4. Construct the ReceiptLine object
            receipt_lines.append({
                "receiptLineType": line_type,
                "receiptLineNo": i + 1,
                "receiptLineHSCode": hs_code,
                "receiptLineName": item.item_name[:200], # ZIMRA max length 200 chars
                "receiptLinePrice": abs(item.base_rate),
                "receiptLineQuantity": abs(item.qty),
                "receiptLineTotal": item.base_amount, # Price * Quantity
                "taxID": tax_mapping.zimra_tax_id,
                "taxPercent": tax_mapping.zimra_tax_percent,
                "taxCode": tax_mapping.zimra_tax_code
            })
            
        return receipt_lines


    def create_zimra_receipt_log(self, doc, payload, response):
        """
        Creates a 'Zimra Receipt Log' to facilitate asynchronous aggregation for closeDay.
        """
        log = frappe.get_doc({
            "doctype": "Zimra Receipt Log",
            "invoice": doc.name,
            "receipt_id": response.get("receiptID"),
            "receipt_type": payload.get("receiptType"),
            "currency": doc.currency,
            "total_amount": doc.grand_total,
            "is_fiscalised": 1,
            "receipt_hash": payload["receiptDeviceSignature"]["hash"],
            "raw_zimra_response": frappe.as_json(response)
        })

        # Capture tax details per ID for counters
        for tax in payload["receiptTaxes"]:
            log.append("details_table", {
                "tax_id": tax["taxID"],
                "tax_percent": tax["taxPercent"],
                "tax_amount": tax["taxAmount"],
                "sales_amount_with_tax": tax["salesAmountWithTax"]
            })
        
        log.insert(ignore_permissions=True)


    def get_buyer_data(self, doc):
        """Captures full buyer profile for B2B compliance."""
        customer = frappe.get_doc("Customer", doc.customer)
        return {
            "buyerRegisterName": customer.customer_name,
            "buyerTIN": customer.tax_id or "",
            "VATNumber": customer.vat_id or "",
            "buyerAddress": {"province": customer.state or "Harare", "city": customer.city or "", "street": customer.address_line1 or "", "houseNo": "0"},
            "buyerContacts": {"phoneNo": customer.mobile_no or "", "email": customer.email_id or ""}
        }

    def update_invoice_record(self, doc, payload, response, money_type):
        """Updates ERPNext invoice and creates the local Receipt Log."""
        doc.db_set({
            "zimra_receipt_id": response["receiptID"],
            "zimra_is_fiscalised": 1,
            "zimra_fiscal_date": response["serverDate"]
        })
        # Logic to generate QR and local Log entry as shown in previous implementation
        pass

    def verify_taxpayer_information(self):
        """Step 1: Public endpoint to verify taxpayer."""
        payload = {
            "deviceID": self.settings.device_id,
            "activationKey": self.settings.activation_key,
            "deviceSerialNo": self.settings.serial_no
        }
        res = requests.post(f"{self.base_url}/verifyTaxpayerInformation", json=payload, headers=self.headers)
        if res.status_code == 200:
            data = res.json()
            self.settings.taxpayer_name = data.get("taxPayerName")
            self.settings.taxpayer_tin = data.get("taxPayerTIN")
            self.settings.save()
            return data
        frappe.throw(f"Verification failed: {res.text}")

    def get_cert_paths(self):
        """Retrieves paths for private files."""
        if not self.settings.device_certificate or not self.settings.private_key:
            self.log_error("Certificates not found in Zimra Settings.")
        
        cert_path = frappe.get_site_path('private', 'files', self.settings.device_certificate.split('/')[-1])
        key_path = frappe.get_site_path('private', 'files', self.settings.private_key.split('/')[-1])
        return (cert_path, key_path)

    def sign_data(self, data_string):
        """Generates SHA256 hash and ECDSA signature."""
        _, key_path = self.get_cert_paths()
        with open(key_path, "rb") as key_file:
            private_key = serialization.load_pem_private_key(key_file.read(), password=None)
        
        data_bytes = data_string.encode('utf-8')
        digest = hashlib.sha256(data_bytes).digest()
        signature = private_key.sign(digest, ec.ECDSA(hashes.SHA256()))
        
        return {
            "hash": base64.b64encode(digest).decode('utf-8'),
            "signature": base64.b64encode(signature).decode('utf-8')
        }

    def register_device(self):
        """Step 2: Generate CSR, register, and save cert as file."""
        cn = f"ZIMRA-{self.settings.serial_no}-{str(self.settings.device_id).zfill(10)}"
        private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
        
        # Save Private Key locally first
        key_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        )
        
        csr = serialization.x509.CertificateSigningRequestBuilder().subject_name(serialization.x509.Name([
            serialization.x509.NameAttribute(serialization.NameOID.COMMON_NAME, cn),
            serialization.x509.NameAttribute(serialization.NameOID.COUNTRY_NAME, "ZW"),
            serialization.x509.NameAttribute(serialization.NameOID.ORGANIZATION_NAME, "Zimbabwe Revenue Authority"),
        ])).sign(private_key, hashes.SHA256(), default_backend())

        payload = {
            "deviceID": self.settings.device_id,
            "activationKey": self.settings.activation_key,
            "certificateRequest": csr.public_bytes(serialization.Encoding.PEM).decode('utf-8')
        }

        try:
            res = requests.post(f"{self.settings.api_url}/registerDevice", json=payload, headers=self.headers)
            res.raise_for_status()
            data = res.json()
            
            # Concrete implementation: Save Certificate and Key to Private Files
            cert_filename = f"zimra_cert_{self.settings.device_id}.pem"
            key_filename = f"zimra_key_{self.settings.device_id}.pem"
            
            _file_cert = frappe.get_doc({
                "doctype": "File", "file_name": cert_filename, "is_private": 1,
                "content": data["certificate"]
            }).insert()
            
            _file_key = frappe.get_doc({
                "doctype": "File", "file_name": key_filename, "is_private": 1,
                "content": key_pem.decode('utf-8')
            }).insert()

            self.settings.db_set("device_certificate", _file_cert.file_url)
            self.settings.db_set("private_key", _file_key.file_url)
            return data
        except Exception as e:
            self.log_and_throw(str(e), "Registration Failed")

    def get_config(self):
        """Retrieves device config and syncs taxes."""
        params = {"deviceID": self.settings.device_id}
        res = requests.get(f"{self.base_url}/getConfig", params=params, headers=self.headers, cert=self.get_cert_files())
        if res.status_code == 200:
            config = res.json()
            self.sync_erpnext_taxes(config.get("applicableTaxes", []))
            self.settings.qr_url = config.get("qrUrl")
            self.settings.save()
            return config
        return res.json()

    def get_status(self):
        """Retrieves and updates the local status of the fiscal day."""
        params = {"deviceID": self.settings.device_id}
        try:
            res = requests.get(f"{self.settings.api_url}/getStatus", params=params, headers=self.headers, cert=self.get_cert_paths())
            res.raise_for_status()
            data = res.json()
            self.settings.db_set("fiscal_day_status", data.get("fiscalDayStatus"))
            return data
        except Exception as e:
            self.log_and_throw(str(e), "Status Update Failed")

    def ping(self):
        """Reports online status and reschedules based on ZIMRA frequency."""
        try:
            res = requests.post(
                f"{self.settings.api_url}/ping", 
                json={"deviceID": self.settings.device_id}, 
                headers=self.headers, 
                cert=self.get_cert_paths()
            )
            data = res.json()
            # Dynamic rescheduling based on reportingFrequency
            freq = data.get("reportingFrequency", 30)
            frappe.enqueue(
                "frappe_zimra.frappe_zimra.api.scheduled_ping", 
                enqueue_after_serve=True, 
                delay=freq * 60
            )
            return data
        except Exception as e:
            frappe.log_error(title="ZIMRA Ping Failed", message=str(e))

    def issue_certificate(self, is_renewal=False):
        """Generates new CSR, fetches certificate, and archives old one if renewing."""
        if is_renewal:
            self.archive_certificate(self.settings.device_certificate, "CERT", self.settings.certificate_valid_till.replace("-", ""))
        
        cn = f"ZIMRA-{self.settings.serial_no}-{str(self.settings.device_id).zfill(10)}"
        private_key = ec.generate_private_key(ec.SECP256R1())
        key_pem = private_key.private_bytes(encoding=serialization.Encoding.PEM, format=serialization.PrivateFormat.TraditionalOpenSSL, encryption_algorithm=serialization.NoEncryption())
        
        csr = x509.CertificateSigningRequestBuilder().subject_name(x509.Name([
            x509.NameAttribute(x509.NameOID.COMMON_NAME, cn),
            x509.NameAttribute(x509.NameOID.COUNTRY_NAME, "ZW"),
            x509.NameAttribute(x509.NameOID.ORGANIZATION_NAME, "Zimbabwe Revenue Authority"),
        ])).sign(private_key, hashes.SHA256())

        payload = {
            "deviceID": self.settings.device_id,
            "certificateRequest": csr.public_bytes(serialization.Encoding.PEM).decode('utf-8')
        }
        
        endpoint = "issueCertificate" if is_renewal else "registerDevice"
        if not is_renewal: payload["activationKey"] = self.settings.activation_key

        res = requests.post(f"{self.settings.api_url}/{endpoint}", json=payload, headers=self.headers)
        if res.status_code == 200:

    def get_daily_counters(self):
        """Aggregates all logs for the current fiscal day."""
        logs = frappe.get_all("Zimra Receipt Log", 
            filters={"creation": [">", frappe.utils.today()], "is_fiscalised": 1},
            fields=["name", "currency", "money_type_code", "total_amount", "receipt_type"]
        )

        counters = []
        # Groupings
        by_tax = {} # (Type, Currency, TaxID, TaxPercent)
        by_money = {} # (Currency, MoneyType)

        for log in logs:
            # Aggregate Payments
            m_key = (log.currency, log.money_type_code)
            by_money[m_key] = by_money.get(m_key, 0) + log.total_amount

            # Aggregate Taxes from child table
            details = frappe.get_all("Zimra Receipt Tax Detail", filters={"parent": log.name}, fields=["*"])
            for d in details:
                # Determine Counter Type based on Receipt Type
                if log.receipt_type == "FiscalInvoice":
                    sale_type, tax_type = "SaleByTax", "SaleTaxByTax"
                elif log.receipt_type == "CreditNote":
                    sale_type, tax_type = "CreditNoteByTax", "CreditNoteTaxByTax"
                else:
                    sale_type, tax_type = "DebitNoteByTax", "DebitNoteTaxByTax"

                k_sale = (sale_type, log.currency, d.tax_id, d.tax_percent)
                k_tax = (tax_type, log.currency, d.tax_id, d.tax_percent)
                
                by_tax[k_sale] = by_tax.get(k_sale, 0) + d.sales_amount_with_tax
                by_tax[k_tax] = by_tax.get(k_tax, 0) + d.tax_amount

        # Build final array for ZIMRA closeDay
        for (ctype, curr, tid, tper), val in by_tax.items():
            if val == 0: continue
            counters.append({
                "fiscalCounterType": ctype, "fiscalCounterCurrency": curr,
                "fiscalCounterTaxID": tid, "fiscalCounterTaxPercent": tper, "fiscalCounterValue": val
            })
        
        for (curr, mtype), val in by_money.items():
            if val == 0: continue
            counters.append({
                "fiscalCounterType": "BalanceByMoneyType", "fiscalCounterCurrency": curr,
                "fiscalCounterMoneyType": mtype, "fiscalCounterValue": val
            })
        
        return counters

    def sync_erpnext_taxes(self, zimra_taxes):
        """Automated sync with ERPNext Sales Tax Templates."""
        for tax in zimra_taxes:
            tax_name = f"ZIMRA {tax['taxName']} - {tax['taxID']}"
            rate = tax.get("taxPercent", 0)
            
            if not frappe.db.exists("Sales Taxes and Charges Template", tax_name):
                doc = frappe.get_doc({
                    "doctype": "Sales Taxes and Charges Template",
                    "title": tax_name,
                    "taxes": [{
                        "charge_type": "On Net Total",
                        "account_head": self.settings.default_tax_account,
                        "rate": rate,
                        "description": tax['taxName']
                    }]
                })
                doc.insert()
            else:
                # Update rate if ZIMRA changed it
                doc = frappe.get_doc("Sales Taxes and Charges Template", tax_name)
                if doc.taxes.rate != rate:
                    doc.taxes.rate = rate
                    doc.save()

       '''
       --- start of new logic of sync taxes
       I suggest the following format for clarity and auditability:

      Accounts: VAT [Rate]% ([ZIMRA Code]) - [Company Abbr]
      
      Example: VAT 15% (A) - ZIM
      
      Templates: ZIMRA - [Description] ([ZIMRA Code])
      
      Example: ZIMRA - Standard Rate (A)
       '''   
    def sync_device_config(self):
        """
        Fetches config from ZIMRA and auto-provisions ERPNext accounts/templates
        without making assumptions about COA numbering or structure.
        """
        config_data = self.call_api("getConfig")
        tax_categories = config_data.get("taxCategories", [])
        
        company = self.settings.company
        company_abbr = frappe.get_value("Company", company, "abbr")

        for tax in tax_categories:
            tax_id = tax.get("taxId")
            tax_code = tax.get("taxCode") # e.g., 'A'
            tax_rate = flt(tax.get("taxPercent"))
            tax_desc = tax.get("taxName")

            # 1. Check if we already have a mapping for this ZIMRA Tax ID
            # This is safer than checking account names which might change
            mapping = self.get_existing_mapping(tax_id)
            
            if mapping and mapping.erpnext_account:
                account_head = mapping.erpnext_account
            else:
                # 2. Create the Account (COA Agnostic)
                account_name = f"VAT {tax_rate}% ({tax_code})"
                account_head = self.ensure_tax_account(account_name, tax_rate, company, company_abbr)
                
                # 3. Save/Update Mapping
                self.update_tax_mapping(tax_id, tax_code, tax_rate, account_head)

            # 4. Create/Sync the Sales Tax Template
            template_name = f"ZIMRA - {tax_desc} ({tax_code})"
            self.ensure_tax_template(template_name, account_head, tax_rate, company)

        frappe.msgprint("ZIMRA Tax Configuration synced successfully.")

    def get_existing_mapping(self, tax_id):
        """Finds mapping by ZIMRA ID to handle cases where names were changed in ERPNext"""
        name = frappe.db.get_value("Zimra Tax Mapping", {"zimra_tax_id": tax_id}, "name")
        if name:
            return frappe.get_doc("Zimra Tax Mapping", name)
        return None

    def ensure_tax_account(self, account_name, rate, company, abbr):
        """Creates a tax account under the correct parent group without assuming numbers"""
        
        # Check if an account with this name and company already exists
        # This handles cases where account might be '2210 - VAT 15% (A) - ZIM' or just 'VAT 15% (A) - ZIM'
        existing = frappe.db.sql("""
            SELECT name FROM `tabAccount` 
            WHERE (account_name = %s OR name LIKE %s)
            AND company = %s LIMIT 1
        """, (account_name, f"%{account_name}%", company))

        if existing:
            return existing[0][0]

        # Find the correct parent: Root Type 'Liability' and Account Type 'Tax'
        # We look for a group account that is usually 'Duties and Taxes'
        parent = frappe.db.get_value("Account", {
            "root_type": "Liability",
            "is_group": 1,
            "account_type": "Tax",
            "company": company
        }, "name")

        # Fallback to a search by name if the 'account_type' isn't set on the group
        if not parent:
            parent = frappe.db.get_value("Account", {
                "account_name": ["like", "%Duties and Taxes%"],
                "company": company,
                "is_group": 1
            }, "name")

        if not parent:
            frappe.throw(f"Could not find a 'Duties and Taxes' group account for company {company}. Please create one first.")

        new_acc = frappe.get_doc({
            "doctype": "Account",
            "account_name": account_name,
            "parent_account": parent,
            "account_type": "Tax",
            "tax_rate": rate,
            "company": company
        })
        new_acc.insert()
        return new_acc.name

    def update_tax_mapping(self, tax_id, tax_code, rate, account_head):
        """The 'Translator' source of truth"""
        mapping_name = f"ZIMRA-ID-{tax_id}"
        if frappe.db.exists("Zimra Tax Mapping", mapping_name):
            doc = frappe.get_doc("Zimra Tax Mapping", mapping_name)
        else:
            doc = frappe.new_doc("Zimra Tax Mapping")
            doc.name = mapping_name

        doc.update({
            "zimra_tax_id": tax_id,
            "zimra_tax_code": tax_code,
            "zimra_tax_percent": rate,
            "erpnext_account": account_head
        })
        doc.save()

    def ensure_tax_template(self, title, account_head, rate, company):
        """Auto-creates the user-facing tax template"""
        if not frappe.db.exists("Sales Taxes and Charges Template", {"title": title, "company": company}):
            template = frappe.get_doc({
                "doctype": "Sales Taxes and Charges Template",
                "title": title,
                "company": company,
                "taxes": [{
                    "charge_type": "On Net Total",
                    "account_head": account_head,
                    "description": title,
                    "rate": rate
                }]
            })
            template.insert()

    # --- end of new logic of sync taxes


    def open_day(self):
        """Concrete implementation of openDay."""
        status_data = self.get_status()
        if status_data.get("fiscalDayStatus") == "FiscalDayOpened":
            return
        
        self.settings.fiscal_day_no = (self.settings.fiscal_day_no or 0) + 1
        payload = {
            "deviceID": self.settings.device_id,
            "fiscalDayOpened": datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
            "fiscalDayNo": self.settings.fiscal_day_no
        }
        
        res = requests.post(f"{self.settings.api_url}/openDay", json=payload, headers=self.headers, cert=self.get_cert_paths())
        if res.status_code == 200:
            self.settings.db_set("receipt_counter", 0)
            self.settings.db_set("fiscal_day_status", "FiscalDayOpened")
            self.settings.save()
        else:
            self.log_error(res.text, "Open Day Failed")

    def close_day(self):
        """Asynchronous call to close fiscal day with daily counters."""
        # Logic to aggregate daily totals into fiscalCounters array
        # This requires querying Sales Invoices submitted today
        counters = self.get_daily_counters()
        
        payload = {
            "deviceID": self.settings.device_id,
            "fiscalDayNo": self.settings.fiscal_day_no,
            "fiscalDayCounters": counters,
            "receiptCounter": self.settings.receipt_counter,
            "fiscalDayDeviceSignature": self.generate_day_signature(counters)
        }
        res = requests.post(f"{self.base_url}/closeDay", json=payload, headers=self.headers, cert=self.get_cert_files())
        return res.json()

   def get_hsn_code(self, item_doc):
       """
       Hierarchical lookup: Item -> Item Group -> Error.
       """
       # 1. Check the specific Item first
       hsn_code = item_doc.zimra_hsn_code
       
       # 2. Fallback to Item Group if missing on Item
       if not hsn_code:
           hsn_code = frappe.db.get_value("Item Group", item_doc.item_group, "zimra_hsn_code")
       
       # 3. Validation and Flagging
       if not hsn_code:
           # HS code is strictly mandatory for VAT payers (RCPT047) [4, 5]
           if bool(self.settings.vat_number):
               frappe.throw(f"Item {item_doc.item_code} or its Item Group is missing a mandatory ZIMRA HS Code.")
           else:
               frappe.msgprint(f"Warning: No HSN code configured for {item_doc.item_code}.")
               
       return hsn_code

    def generate_day_signature(self, counters):
        """Strict concatenation: deviceID || fiscalDayNo || fiscalDayDate || fiscalDayCounters."""
        date_str = datetime.now().strftime('%Y-%m-%d')
        sig_base = f"{self.settings.device_id}{self.settings.fiscal_day_no}{date_str}"
        
        # Sorted counters: Type (ASC), Currency (ASC), TaxID/MoneyType (ASC)
        sorted_counters = sorted(counters, key=lambda x: (x['fiscalCounterType'], x['fiscalCounterCurrency']))
        
        counter_str = ""
        for c in sorted_counters:
            val_cents = int(c['fiscalCounterValue'] * 100)
            c_type = c['fiscalCounterType'].upper()
            if "MONEYTYPE" in c_type:
                counter_str += f"{c_type}{c['fiscalCounterCurrency'].upper()}{c['fiscalCounterMoneyType'].upper()}{val_cents}"
            else:
                counter_str += f"{c_type}{c['fiscalCounterCurrency'].upper()}{c['fiscalCounterTaxPercent']:.2f}{val_cents}"
        
        return self.sign_data(sig_base + counter_str)