"""Fiscal receipts were unique per device and global number. Global numbers
may now start again at 1 in a new fiscal day, so a receipt is unique per
device, fiscal day and global number. The new key is added before the old one
is dropped, so receipts are never left without a unique key."""

import frappe

TABLE = "tabZimra Fiscal Receipt"
OLD = "unique_device_global_no"


def execute():
	frappe.db.add_unique(
		"Zimra Fiscal Receipt",
		["fiscal_device", "fiscal_day_no", "receipt_global_no"],
		"unique_device_day_global_no",
	)
	if not frappe.db.has_index(TABLE, OLD):
		return
	if frappe.db.db_type == "postgres":
		frappe.db.sql_ddl(f'ALTER TABLE "{TABLE}" DROP CONSTRAINT IF EXISTS "{OLD}"')
	else:
		frappe.db.sql_ddl(f"ALTER TABLE `{TABLE}` DROP INDEX `{OLD}`")
