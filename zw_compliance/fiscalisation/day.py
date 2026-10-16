"""Fiscal day bookkeeping.

A fiscal day is opened *locally* the moment it is needed (status
"Pending Open") and the openDay message follows from the sync job; the spec
allows the opening message to be delayed as long as it reaches FDMS before the
day's receipts (§2.2, §9 req. 10-11). This keeps invoicing working while
ZIMRA is briefly unreachable.

Counters are kept on the day as receipts are signed (§6, §9 req. 15) and are
recomputed from the stored receipts before the close is signed; the close is
refused if the two disagree.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

from zw_compliance.fdms.receipt import counters_for, merge_counters
from zw_compliance.fdms.types import FiscalCounter
from zw_compliance.fdms.wire import receipt_from_wire

from .constants import (
	DAY,
	DAY_ACCEPTING,
	DAY_CLOSE_FAILED,
	DAY_CLOSE_REQUESTED,
	DAY_OPENED,
	DAY_PENDING_OPEN,
	DEVICE,
	RECEIPT,
	REJECTED,
	SETTINGS,
)


def counters_from_rows(rows) -> list[FiscalCounter]:
	return [
		FiscalCounter(
			counter_type=r.counter_type,
			currency=r.currency,
			value=Decimal(str(r.value)).quantize(Decimal("0.01")),
			tax_id=r.tax_id if r.counter_type != "BalanceByMoneyType" else None,
			tax_percent=None
			if r.counter_type == "BalanceByMoneyType" or r.is_exempt
			else Decimal(str(r.tax_percent)).quantize(Decimal("0.01")),
			money_type=r.money_type or None,
		)
		for r in rows
	]


def counters_to_rows(counters: list[FiscalCounter]) -> list[dict]:
	return [
		{
			"counter_type": c.counter_type,
			"currency": c.currency,
			"tax_id": c.tax_id,
			"tax_percent": float(c.tax_percent or 0),
			"is_exempt": int(c.counter_type != "BalanceByMoneyType" and c.tax_percent is None),
			"money_type": c.money_type,
			"value": float(c.value),
		}
		for c in counters
	]


def recompute_counters(day_name: str) -> list[FiscalCounter]:
	"""Counters rebuilt from every receipt of the day as signed."""
	counters: list[FiscalCounter] = []
	for payload in frappe.get_all(
		RECEIPT,
		filters={"fiscal_day": day_name, "sync_status": ["!=", REJECTED]},
		pluck="payload",
		order_by="receipt_counter asc",
	):
		counters = merge_counters(counters, counters_for(receipt_from_wire(frappe.parse_json(payload))))
	return counters


def _must_roll_over(day, device, now) -> bool:
	"""True when the day is too close to ZIMRA's maximum length (§9 req. 4, RCPT041)."""
	if not device.day_max_hours:
		return False
	margin = frappe.db.get_single_value(SETTINGS, "close_margin_hours") or 0
	limit = get_datetime(day.fiscal_day_opened) + timedelta(hours=max(device.day_max_hours - margin, 0))
	return now >= limit


def open_local_day(device, now):
	"""Create the next fiscal day (Pending Open). Caller holds the device lock."""
	number = (device.last_fiscal_day_no or 0) + 1
	day = frappe.get_doc(
		{
			"doctype": DAY,
			"fiscal_device": device.name,
			"company": device.company,
			"fiscal_day_no": number,
			"status": DAY_PENDING_OPEN,
			"fiscal_day_opened": now,
			"fiscal_day_date": now.date(),
			"receipt_counter": 0,
		}
	)
	day.insert(ignore_permissions=True)
	device.db_set({"current_fiscal_day": day.name, "last_fiscal_day_no": number}, update_modified=False)
	return day


def day_for_receipt(device, now):
	"""The locked fiscal day the next receipt belongs to, opening one if needed."""
	day = (
		frappe.get_doc(DAY, device.current_fiscal_day, for_update=True) if device.current_fiscal_day else None
	)
	if day and day.status == DAY_PENDING_OPEN and not day.receipt_counter and not day.open_operation_id:
		# Not yet announced to ZIMRA and still empty: its opening time is the first sale.
		day.db_set({"fiscal_day_opened": now, "fiscal_day_date": now.date()}, update_modified=False)
		return day
	if day and day.status in DAY_ACCEPTING:
		if not _must_roll_over(day, device, now):
			return day
		mark_close_requested(day, _("Maximum fiscal day length reached"))
	if not frappe.db.get_single_value(SETTINGS, "auto_open_day"):
		frappe.throw(
			_(
				"No ZIMRA fiscal day is open on {0}. Open one from the fiscal device, or enable automatic opening in Zimra Settings."
			).format(frappe.bold(device.device_name)),
			title=_("Fiscal Day Not Open"),
		)
	return open_local_day(device, now)


def mark_close_requested(day, reason: str | None = None) -> None:
	day.db_set(
		{
			"status": DAY_CLOSE_REQUESTED,
			"close_requested_on": now_datetime(),
			"close_requested_by": frappe.session.user,
			"closing_error_code": None,
		}
	)
	if reason:
		day.add_comment("Info", reason)


def request_close(day_name: str, reason: str | None = None) -> None:
	"""Ask for the day to be closed; the sync job closes it once every receipt is in."""
	from .sync import enqueue_sync

	day = frappe.get_doc(DAY, day_name)
	frappe.get_doc(DEVICE, day.fiscal_device, for_update=True)  # serialise with receipt signing
	day.reload()
	if day.status not in (DAY_OPENED, DAY_PENDING_OPEN, DAY_CLOSE_FAILED):
		frappe.throw(
			_("Fiscal day {0} is {1} and cannot be closed now.").format(day.fiscal_day_no, day.status)
		)
	mark_close_requested(day, reason)
	enqueue_sync(day.fiscal_device)
