# Copyright (c) 2026, Donald Chinhuru and contributors
# For license information, please see license.txt

"""Scheduled tasks: ping, scheduled close-day, offline-queue auto-flush,
and certificate renewal warnings/auto-renewal. See hooks.py
`scheduler_events`.
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime, nowtime

from zw_compliance.zimra import setup as zimra_setup
from zw_compliance.zimra.connector import ZimraConnector


def _settings():
	return frappe.get_cached_doc("Zimra Settings")


def _device_ready(settings) -> bool:
	return bool(settings.device_id and settings.device_certificate and settings.private_key)


def scheduled_ping():
	settings = _settings()
	if not settings.ping_enabled or not _device_ready(settings):
		return
	try:
		ZimraConnector(settings).ping()
	except Exception:
		frappe.log_error(title="ZIMRA Ping Failed")


def scheduled_close_day():
	"""Runs every 5 minutes; only acts once local time passes the
	configured auto_close_day_time, and only if a day is actually open.
	"""
	settings = _settings()
	if not settings.auto_close_day_time or not _device_ready(settings):
		return
	if settings.fiscal_day_status != "FiscalDayOpened":
		return
	if nowtime() < str(settings.auto_close_day_time):
		return
	try:
		ZimraConnector(settings).close_day()
	except Exception:
		frappe.log_error(title="ZIMRA Scheduled Close Day Failed")


def scheduled_flush_offline_queue():
	"""Runs every 5 minutes: retries any receipts journalled while FDMS was
	unreachable, and escalates if the oldest one is getting close to
	ZIMRA's 72-hour offline grace window.
	"""
	settings = _settings()
	if not settings.auto_flush_offline_queue or not _device_ready(settings):
		return

	connector = ZimraConnector(settings)
	queue = connector.queue()
	if queue.size == 0:
		return

	alert_hours = settings.offline_queue_alert_hours or 48
	oldest_ms = queue.oldest_pending_age_ms
	if oldest_ms and oldest_ms > alert_hours * 3600 * 1000:
		_notify_system_managers(
			"ZIMRA Offline Queue Ageing",
			f"{queue.size} receipt(s) have been queued offline for over {alert_hours} hours. "
			"ZIMRA's grace window for offline receipts is 72 hours - check connectivity to FDMS urgently.",
		)

	try:
		result = queue.flush()
		if result.error:
			frappe.log_error(
				title="ZIMRA Offline Queue Flush Incomplete",
				message=f"Submitted {len(result.submitted)}, {result.remaining} still pending: {result.error}",
			)
	except Exception:
		frappe.log_error(title="ZIMRA Offline Queue Flush Failed")


def check_certificate_renewal():
	"""Runs daily: warns as the device certificate approaches expiry, and
	renews it automatically if `Auto Renew Certificate` is enabled (opt-in -
	see Zimra Settings > Automation).
	"""
	settings = _settings()
	if not settings.device_id or not settings.certificate_valid_till:
		return

	warning_days = settings.certificate_renewal_warning_days or 30
	valid_till = frappe.utils.get_datetime(settings.certificate_valid_till)
	days_left = (valid_till - now_datetime()).days

	if days_left < 0:
		_notify_system_managers(
			"ZIMRA Certificate Expired",
			f"The ZIMRA device certificate expired on {valid_till}. Fiscalisation is blocked until it is renewed. "
			"An expired certificate cannot be auto-renewed over mTLS - renew it via the FDMS device portal / ZIMRA.",
		)
		return

	if days_left > warning_days:
		return

	if settings.auto_renew_certificate:
		try:
			zimra_setup.renew_certificate(settings)
			_notify_system_managers(
				"ZIMRA Certificate Auto-Renewed",
				f"The ZIMRA device certificate was renewed automatically ({days_left} day(s) remained before expiry).",
			)
		except Exception:
			frappe.log_error(title="ZIMRA Certificate Auto-Renewal Failed")
			_notify_system_managers(
				"ZIMRA Certificate Auto-Renewal Failed",
				f"Automatic renewal failed with {days_left} day(s) left before expiry - see Error Log. "
				"Renew manually from the ZIMRA menu on Zimra Settings.",
			)
	else:
		_notify_system_managers(
			"ZIMRA Certificate Expiring Soon",
			f"The ZIMRA device certificate expires on {valid_till} ({days_left} day(s) left). "
			"Renew it from Zimra Settings > ZIMRA > Renew Certificate, or enable Auto Renew Certificate.",
		)


def _notify_system_managers(subject: str, message: str):
	frappe.log_error(title=subject, message=message)
	for user in frappe.get_all("Has Role", filters={"role": "System Manager", "parenttype": "User"}, pluck="parent"):
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"subject": subject,
				"for_user": user,
				"type": "Alert",
				"email_content": message,
			}
		).insert(ignore_permissions=True)
