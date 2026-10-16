"""Alerts to the people responsible for fiscalisation (the role in Zimra Settings)."""

from __future__ import annotations

import frappe
from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification

from .constants import SETTINGS, ZIMRA_MANAGER


def recipients() -> list[str]:
	role = frappe.db.get_single_value(SETTINGS, "notify_role") or ZIMRA_MANAGER
	users = frappe.get_all(
		"Has Role", filters={"role": role, "parenttype": "User"}, pluck="parent", distinct=True
	)
	return [
		u for u in users if u not in ("Administrator", "Guest") and frappe.db.get_value("User", u, "enabled")
	]


def notify_managers(device, subject: str, message: str, reference=None) -> None:
	users = recipients()
	doc = reference or device
	if users:
		enqueue_create_notification(
			users,
			{
				"type": "Alert",
				"subject": subject,
				"email_content": message,
				"document_type": doc.doctype,
				"document_name": doc.name,
				"from_user": "Administrator",
			},
		)
	frappe.publish_realtime(
		"zimra_fiscal_alert",
		{"subject": subject, "message": message, "device": device.name},
		after_commit=True,
	)
