"""The Fiscal Receipt Register and Fiscal Day Summary shipped with a total row
that added up day numbers, counters and amounts in different currencies.
Frappe keeps ``add_total_row`` on existing sites when a report is re-imported,
so it is corrected here once."""

import frappe


def execute():
	for report in ("ZIMRA Fiscal Receipt Register", "ZIMRA Fiscal Day Summary"):
		if frappe.db.exists("Report", report):
			frappe.db.set_value("Report", report, "add_total_row", 0, update_modified=False)
