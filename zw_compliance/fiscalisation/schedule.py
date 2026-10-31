"""Scheduled maintenance, wired in ``hooks.scheduler_events``.

* :func:`every_minute` - ping devices that are due (§4.13) and restart the sync
  for devices with receipts or day messages waiting.
* :func:`every_five_minutes` - end-of-day notices (§9 req. 5), automatic close
  requests at the configured time or before the maximum day length (§9 req. 4),
  optional proactive day opening.
* :func:`daily` - certificate expiry warnings and opt-in renewal (§4.3, §9 req. 16).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import frappe
from frappe import _
from frappe.utils import get_datetime, get_time, now_datetime

from .constants import (
	DAY,
	DAY_ACCEPTING,
	DAY_CLOSE_INITIATED,
	DAY_CLOSE_REQUESTED,
	DAY_PENDING_OPEN,
	DEVICE,
	DEVICE_ACTIVE,
	RECEIPT,
	SETTINGS,
	UNSENT,
)
from .notifications import notify_managers
from .sync import enqueue_sync


def _active_devices() -> list[str]:
	return frappe.get_all(DEVICE, filters={"status": DEVICE_ACTIVE, "is_demo": 0}, pluck="name")


def _run_safely(title: str, fn, *args) -> None:
	try:
		fn(*args)
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title=title)


def every_minute() -> None:
	settings = frappe.get_cached_doc(SETTINGS)
	now = now_datetime()
	for name in _active_devices():
		if settings.ping_enabled:
			next_ping = frappe.db.get_value(DEVICE, name, "next_ping_at")
			if not next_ping or get_datetime(next_ping) <= now:
				frappe.enqueue(
					"zw_compliance.fiscalisation.device.ping",
					queue="short",
					name=name,
					job_id=f"zimra-ping::{name}",
				)
		waiting = frappe.db.exists(
			RECEIPT, {"fiscal_device": name, "sync_status": ["in", list(UNSENT)]}
		) or frappe.db.exists(
			DAY,
			{
				"fiscal_device": name,
				"status": ["in", [DAY_PENDING_OPEN, DAY_CLOSE_REQUESTED, DAY_CLOSE_INITIATED]],
			},
		)
		if waiting:
			enqueue_sync(name)
			frappe.db.commit()  # fires the after-commit enqueue


def _day_end(day, device) -> datetime | None:
	if not device.day_max_hours or not day.fiscal_day_opened:
		return None
	return get_datetime(day.fiscal_day_opened) + timedelta(hours=device.day_max_hours)


def _maintain_device(name: str, settings, now: datetime) -> None:
	from .day import mark_close_requested, open_local_day

	device = frappe.get_doc(DEVICE, name)
	day = frappe.get_doc(DAY, device.current_fiscal_day) if device.current_fiscal_day else None

	if day and day.status in DAY_ACCEPTING and day.status != DAY_PENDING_OPEN:
		end = _day_end(day, device)
		margin = timedelta(hours=settings.close_margin_hours or 0)
		notice = timedelta(hours=device.day_end_notification_hours or 0)
		if end and not day.end_notice_sent and now >= end - notice:
			notify_managers(
				device,
				_("Fiscal day {0} ends at {1}").format(day.fiscal_day_no, frappe.format(end, "Datetime")),
				_(
					"ZIMRA allows a fiscal day of at most {0} hours. Close the day before then; invoices issued later are flagged (RCPT041)."
				).format(device.day_max_hours),
				reference=day,
			)
			day.db_set("end_notice_sent", 1, update_modified=False)
		due_by_time = False
		if settings.auto_close_time:
			close_at = datetime.combine(now.date(), get_time(settings.auto_close_time))
			due_by_time = now >= close_at and get_datetime(day.fiscal_day_opened) < close_at
		due_by_length = end and now >= end - margin
		if due_by_time or due_by_length:
			frappe.get_doc(DEVICE, name, for_update=True)
			day.reload()
			if day.status in DAY_ACCEPTING:
				mark_close_requested(day, _("Closed automatically (Zimra Settings)"))
				enqueue_sync(name)

	if settings.day_start_time and now.time() >= get_time(settings.day_start_time):
		latest = frappe.get_doc(DAY, device.current_fiscal_day) if device.current_fiscal_day else None
		if not latest or (
			latest.status not in DAY_ACCEPTING and get_datetime(latest.fiscal_day_opened).date() < now.date()
		):
			locked = frappe.get_doc(DEVICE, name, for_update=True)
			open_local_day(locked, now.replace(microsecond=0))
			enqueue_sync(name)


def every_five_minutes() -> None:
	settings = frappe.get_cached_doc(SETTINGS)
	now = now_datetime()
	for name in _active_devices():
		_run_safely(
			_("ZIMRA day maintenance failed for {0}").format(name), _maintain_device, name, settings, now
		)


def _certificate_care(name: str, settings) -> None:
	from .device import certificate_days_left, renew_certificate

	device = frappe.get_doc(DEVICE, name)
	days = certificate_days_left(device)
	if days is None or days > (settings.certificate_warning_days or 30):
		return
	if days <= 0:
		notify_managers(
			device,
			_("ZIMRA certificate of {0} has expired").format(device.device_name),
			_(
				"Invoices are still fiscalised, but ZIMRA refuses every request from an expired certificate, so they queue until ZIMRA issues the device a new certificate."
			),
		)
		return
	if settings.auto_renew_certificate:
		renew_certificate(name)
		notify_managers(
			device,
			_("ZIMRA certificate of {0} renewed").format(device.device_name),
			_("A new certificate was issued automatically."),
		)
	else:
		notify_managers(
			device,
			_("ZIMRA certificate of {0} expires in {1} days").format(device.device_name, days),
			_(
				"Renew it from the fiscal device (Actions > Renew Certificate), or enable automatic renewal in Zimra Settings."
			),
		)


def daily() -> None:
	settings = frappe.get_cached_doc(SETTINGS)
	for name in _active_devices():
		_run_safely(
			_("ZIMRA certificate check failed for {0}").format(name), _certificate_care, name, settings
		)
