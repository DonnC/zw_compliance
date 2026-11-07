"""The single workspace became Fiscalisation with child pages; drop the old one."""

import frappe


def execute():
	frappe.delete_doc("Workspace", "ZIMRA Fiscalisation", force=True, ignore_missing=True)
