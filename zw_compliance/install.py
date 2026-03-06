import frappe
import csv
import os

def after_install():
    csv_path = frappe.get_app_path("zw_compliance", "data", "zimra_hsn_codes.csv")
    
    if not os.path.exists(csv_path):
        frappe.msgprint("CSV file not found in data folder")
        return

    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not frappe.db.exists("Zimra HSN Code", row['hsn_code']):
                doc = frappe.get_doc({
                    "doctype": "Zimra HSN Code",
                    "hsn_code": row['hsn_code'],
                    "description": row['description']
                })
                doc.insert(ignore_permissions=True)
    
    frappe.db.commit()
    print("ZIMRA HSN Codes seeded successfully!")