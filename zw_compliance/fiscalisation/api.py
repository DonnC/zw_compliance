"""Whitelisted actions behind the desk buttons. Every action is limited to
System Manager and ZIMRA Manager and is re-checked here, server side."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import now_datetime

from . import device as lifecycle
from .constants import (
	DAY,
	DAY_ACCEPTING,
	DEVICE,
	DEVICE_ACTIVE,
	DEVICE_DISABLED,
	MANAGER_ROLES,
	RECEIPT,
	REJECTED,
	UNSENT,
)
from .day import request_close as _request_close
from .sync import enqueue_sync, retry_now


def _manager():
	frappe.only_for(MANAGER_ROLES)


@frappe.whitelist(methods=["POST"])
def verify_taxpayer(device: str) -> dict:
	_manager()
	return lifecycle.verify_taxpayer(device)


@frappe.whitelist(methods=["POST"])
def register_device(device: str) -> None:
	_manager()
	lifecycle.register(device)


@frappe.whitelist(methods=["POST"])
def sync_configuration(device: str) -> None:
	_manager()
	lifecycle.configure(device)


@frappe.whitelist(methods=["POST"])
def renew_certificate(device: str) -> None:
	_manager()
	lifecycle.renew_certificate(device)


@frappe.whitelist(methods=["POST"])
def set_enabled(device: str, enabled: int) -> None:
	_manager()
	doc = frappe.get_doc(DEVICE, device)
	if int(enabled):
		if doc.status != DEVICE_DISABLED:
			return
		doc.status = DEVICE_ACTIVE if doc.certificate else "Draft"
	else:
		if frappe.db.exists(RECEIPT, {"fiscal_device": device, "sync_status": ["in", [*UNSENT, REJECTED]]}):
			frappe.throw(
				_("Receipts of this device are still waiting for ZIMRA. Disable it once they are submitted.")
			)
		doc.status = DEVICE_DISABLED
	doc.save()


@frappe.whitelist(methods=["POST"])
def sync_now(device: str) -> None:
	_manager()
	retry_now(device)
	enqueue_sync(device)


@frappe.whitelist(methods=["POST"])
def open_day(device: str) -> str:
	_manager()
	from .day import open_local_day
	from .device import device_now, lock_device, require_active

	doc = lock_device(device)
	require_active(doc)
	if doc.current_fiscal_day and frappe.db.get_value(DAY, doc.current_fiscal_day, "status") in DAY_ACCEPTING:
		frappe.throw(_("Fiscal day is already open."))
	day = open_local_day(doc, device_now(doc))
	enqueue_sync(device)
	return day.name


@frappe.whitelist(methods=["POST"])
def request_close(day: str) -> None:
	_manager()
	_request_close(day, _("Close requested by {0}").format(frappe.session.user))


@frappe.whitelist()
def device_summary(device: str) -> dict:
	"""Numbers for the device form headline."""
	frappe.has_permission(DEVICE, "read", device, throw=True)
	doc = frappe.get_doc(DEVICE, device)
	day = (
		frappe.db.get_value(
			DAY,
			doc.current_fiscal_day,
			["name", "fiscal_day_no", "status", "fiscal_day_opened", "receipt_counter", "blocking_receipts"],
			as_dict=True,
		)
		if doc.current_fiscal_day
		else None
	)
	return {
		"status": doc.status,
		"day": day,
		"queued": frappe.db.count(RECEIPT, {"fiscal_device": device, "sync_status": ["in", list(UNSENT)]}),
		"rejected": frappe.db.count(RECEIPT, {"fiscal_device": device, "sync_status": REJECTED}),
		"certificate_days_left": lifecycle.certificate_days_left(doc),
		"last_sync": doc.last_sync,
		"last_sync_error": doc.last_sync_error,
		"now": now_datetime(),
	}
