# Copyright (c) 2026, Donald Chinhuru and contributors
# For license information, please see license.txt

import frappe

@frappe.whitelist()
def scheduled_open_day():
    connector = ZimraConnector()
    connector.open_day()

@frappe.whitelist()
def scheduled_close_day():
    connector = ZimraConnector()
    connector.close_day()

@frappe.whitelist()
def scheduled_ping():
    ZimraConnector().ping()

@frappe.whitelist()
def check_certificate_renewal():
    """Triggered by cron; checks if certificate expires within 30 days."""
    settings = frappe.get_doc("Zimra Settings")
    if not settings.certificate_valid_till: return
    
    expiry = frappe.utils.get_datetime(settings.certificate_valid_till)
    if expiry < (datetime.now() + timedelta(days=30)):
        frappe.log_error("ZIMRA Certificate expiring soon. Initiating renewal.")
        ZimraConnector().issue_certificate(is_renewal=True)

# hook logic
def fiscalise_invoice_hook(doc, method):
    if doc.zimra_is_fiscalised:
        return

    if doc.doctype == "Sales Invoice":
        # Identify source POS Invoices via item-level links [5]
        source_pos_ids = [item.pos_invoice for item in doc.items if item.get("pos_invoice")]
        
        if source_pos_ids:
            pos_data = frappe.db.get_value("POS Invoice", source_pos_ids, 
                ["zimra_is_fiscalised", "zimra_receipt_id", "zimra_server_signature", 
                 "zimra_qr_code", "zimra_verification_code", "zimra_receipt_counter", 
                 "zimra_global_no", "zimra_fiscal_date"], as_dict=True)

            if pos_data and pos_data.zimra_is_fiscalised:
                # Inherit data and explicitly flag the mode
                doc.db_set({
                    "zimra_is_fiscalised": 1,
                    "zimra_fiscalisation_mode": "Inherited from POS",
                    "zimra_receipt_id": pos_data.zimra_receipt_id,
                    "zimra_server_signature": pos_data.zimra_server_signature,
                    "zimra_qr_code": pos_data.zimra_qr_code,
                    "zimra_verification_code": pos_data.zimra_verification_code,
                    "zimra_receipt_counter": pos_data.zimra_receipt_counter,
                    "zimra_global_no": pos_data.zimra_global_no,
                    "zimra_fiscal_date": pos_data.zimra_fiscal_date
                })
                
                # Update the ZIMRA Receipt Log to reflect inheritance [1, 4]
                update_log_with_mode(doc, "Inherited from POS")
                return

    # Trigger fresh submission for standard B2B or new POS Invoices [7, 8]
    connector = ZimraConnector()
    connector.fiscalise_invoice(doc)

def update_log_with_mode(doc, mode):
    """
    Creates an immutable record in the Zimra Receipt Log for inherited invoices.
    Explicitly flags the 'Inherited from POS' mode to prevent double-counting 
    in financial totals while maintaining document quantity counts [3, 6].
    """
    # 1. Initialize the Log Record (The 'Carbon-Copy Slip')
    # We adoption the identity of the original POS transaction [7]
    log = frappe.get_doc({
        "doctype": "Zimra Receipt Log",
        "invoice": doc.name,
        "receipt_id": doc.zimra_receipt_id,
        "receipt_type": "FiscalInvoice", 
        "currency": doc.currency,
        "total_amount": doc.base_grand_total,
        "is_fiscalised": 1,
        "zimra_fiscal_day_no": doc.zimra_fiscal_day_no,
        "zimra_fiscalisation_mode": mode, # Set to 'Inherited from POS'
        "receipt_hash": doc.zimra_server_signature, # Use signature as a reference
        "posting_date": doc.posting_date or nowdate()
    })

    # 2. Populate Raw Tax Details (Child Table)
    # We use the Zimra Tax Mapping to translate ERPNext heads to numeric ZIMRA IDs [8, 9]
    for item in doc.items:
        # Determine the correct mapping for this specific item [9]
        tax_template = item.item_tax_template or doc.taxes_and_charges
        tax_mapping = frappe.get_value("Zimra Tax Mapping", 
            {"erpnext_tax_template": tax_template}, 
            ["zimra_tax_id", "zimra_tax_percent"], as_dict=True)

        if tax_mapping:
            # Calculate raw values per item to ensure the log is unchangeable [2, 10]
            # Formulate: Gross = Net + Tax [11, 12]
            log.append("taxes", {
                "tax_id": tax_mapping.zimra_tax_id,
                "tax_percent": tax_mapping.zimra_tax_percent,
                "net_amount": item.base_net_amount,
                "tax_amount": item.base_amount - item.base_net_amount,
                "sales_amount_with_tax": item.base_amount
            })

    # 3. Insert into the Digital Vault
    # Saving raw values ensures historical integrity for Z-Reports [2, 13]
    log.insert(ignore_permissions=True)
    
    # Note: No need to commit here if called within an on_submit hook, 
    # as Frappe handles the transaction wrapper.

# -------------------------