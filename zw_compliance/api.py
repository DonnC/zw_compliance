# Copyright (c) 2026, Donald Chinhuru and contributors
# For license information, please see license.txt

"""Whitelisted endpoints for the desk UI: device setup wizard and manual
fiscal-day/queue operations. All of these are thin wrappers around
zw_compliance.zimra - see that package for the actual logic.
"""

from __future__ import annotations

import frappe

from zw_compliance.zimra import setup as zimra_setup
from zw_compliance.zimra.connector import ZimraConnector


def _settings():
	return frappe.get_doc("Zimra Settings")


@frappe.whitelist()
def register_device():
	frappe.only_for("System Manager")
	return zimra_setup.register_device(_settings())


@frappe.whitelist()
def sync_config():
	frappe.only_for("System Manager")
	return zimra_setup.sync_config(_settings())


@frappe.whitelist()
def renew_certificate():
	frappe.only_for("System Manager")
	return zimra_setup.renew_certificate(_settings())


@frappe.whitelist()
def open_day():
	frappe.only_for("System Manager")
	return ZimraConnector().open_day()


@frappe.whitelist()
def close_day(force: bool = False):
	frappe.only_for("System Manager")
	return ZimraConnector().close_day(force=frappe.utils.cint(force))


@frappe.whitelist()
def get_status():
	frappe.only_for("System Manager")
	status = ZimraConnector().get_status()
	return {
		"fiscalDayStatus": status.fiscal_day_status,
		"lastReceiptGlobalNo": status.last_receipt_global_no,
		"lastFiscalDayNo": status.last_fiscal_day_no,
	}


@frappe.whitelist()
def ping():
	frappe.only_for("System Manager")
	result = ZimraConnector().ping()
	return {"reportingFrequency": result.reporting_frequency}


@frappe.whitelist()
def flush_offline_queue():
	frappe.only_for("System Manager")
	connector = ZimraConnector()
	result = connector.queue().flush()
	return {"submitted": len(result.submitted), "remaining": result.remaining, "error": str(result.error) if result.error else None}


@frappe.whitelist()
def queue_status():
	frappe.only_for("System Manager")
	queue = ZimraConnector().queue()
	return {"size": queue.size, "oldestPendingAgeMs": queue.oldest_pending_age_ms}
