"""Background synchronisation of a fiscal device with FDMS.

One runner per device at a time (a file lock per device, plus job
de-duplication). Each run, in order and until nothing is left to do:

1. **Open** the earliest day not yet announced: getConfig (§9 req. 2), getStatus,
   then openDay with the device's own fiscalDayNo (§4.6). A lost openDay
   answer is recovered by reading getStatus.
2. **Submit** queued receipts one at a time in receiptGlobalNo order, never
   skipping one (§9 req. 13). Re-sending a receipt is safe: FDMS answers a
   duplicate (same deviceID, receiptGlobalNo and hash) with the original
   receiptID (§4.7). Network errors and HTTP 5xx are retried with back-off;
   a receipt FDMS refuses as malformed is marked Rejected and the queue
   stops, because skipping it would break the chain.
3. **Close** a day whose close was requested, once every receipt is accepted
   and none is Grey or Red (§4.11): counters are recomputed from the signed
   receipts, the day report is signed (§13.3.1) and closeDay is sent.
4. **Poll** a closing day with getStatus until FiscalDayClosed or
   FiscalDayCloseFailed, and verify FDMS's day signature (§13.3.2).

Work is committed after every step, so progress survives a crash.
"""

from __future__ import annotations

import json
import time

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime, now_datetime
from frappe.utils.background_jobs import is_job_enqueued

from zw_compliance.fdms.errors import FdmsApiError, FdmsError, TransportError, describe
from zw_compliance.fdms.signer import sign
from zw_compliance.fdms.signing import fiscal_day_server_string, fiscal_day_string, receipt_server_string
from zw_compliance.fdms.verify import select_certificate, verify_signature

from . import credentials
from .constants import (
	BLOCKING_COLOURS,
	DAY,
	DAY_CLOSE_FAILED,
	DAY_CLOSE_INITIATED,
	DAY_CLOSE_REQUESTED,
	DAY_CLOSED,
	DAY_OPENED,
	DAY_PENDING_OPEN,
	DEVICE,
	DEVICE_ACTIVE,
	RECEIPT,
	REJECTED,
	RETRYING,
	SUBMITTED,
	UNSENT,
)
from .day import counters_from_rows, counters_to_rows, recompute_counters
from .device import apply_config, connect
from .notifications import notify_managers

RUN_SECONDS = 240
NOT_RETRYABLE = {"RCPT02"}  # receipt structure invalid: re-sending cannot help


def job_id(device_name: str) -> str:
	return f"zimra-sync::{device_name}"


def enqueue_sync(device_name: str) -> None:
	"""Run the sync job for ``device_name`` after the current transaction commits."""

	def _enqueue():
		if frappe.flags.in_test and not frappe.flags.zimra_sync_in_test:
			return
		if frappe.flags.zimra_sync_inline:
			return  # the caller (demo seeder) runs the sync itself
		if is_job_enqueued(job_id(device_name)):
			return  # a queued or running job will pick this up
		frappe.enqueue(
			"zw_compliance.fiscalisation.sync.sync_device",
			queue="short",
			timeout=RUN_SECONDS + 60,
			job_id=job_id(device_name),
			device_name=device_name,
			now=bool(frappe.flags.in_test),
		)

	frappe.db.after_commit.add(_enqueue)


def retry_now(device_name: str, connection_failures_only: bool = False) -> None:
	"""Lift the back-off of Retrying receipts so the next run sends them at once.

	Called when a new receipt is signed: a fresh sale is the best sign that the
	business is trading, and the connection may well be back. Because receipts
	are sent strictly in order, a head receipt waiting out its back-off would
	otherwise hold every later one back. ``connection_failures_only`` keeps the
	back-off of receipts FDMS answered with an error code.
	"""
	condition = "and ifnull(last_error_code, '') = ''" if connection_failures_only else ""
	frappe.db.sql(
		f"""update `tab{RECEIPT}` set next_attempt_at = null
		where fiscal_device = %s and sync_status = %s and next_attempt_at is not null {condition}""",
		(device_name, RETRYING),
	)


def sync_device(device_name: str) -> None:
	from frappe.utils.synchronization import filelock

	try:
		with filelock(f"zimra-sync-{frappe.scrub(device_name)}", timeout=1):
			_run(device_name)
	except Exception as e:
		if type(e).__name__ == "LockTimeoutError":
			return  # another runner is working on this device
		raise


def _run(device_name: str) -> None:
	device = frappe.get_doc(DEVICE, device_name)
	if device.status != DEVICE_ACTIVE:
		return
	deadline = time.monotonic() + RUN_SECONDS
	error = None
	try:
		with connect(device) as client:
			while time.monotonic() < deadline:
				progressed = _open_day(device_name, client)
				progressed |= _submit_receipts(device_name, client, deadline)
				progressed |= _close_day(device_name, client)
				progressed |= _poll_closing(device_name, client)
				frappe.db.commit()
				if not progressed:
					break
	except (TransportError, FdmsApiError) as e:
		error = str(e)
	except Exception:
		frappe.db.rollback()
		error = frappe.get_traceback()
		frappe.log_error(title=_("ZIMRA sync failed for {0}").format(device_name))
	frappe.db.set_value(
		DEVICE, device_name, {"last_sync": now_datetime(), "last_sync_error": error}, update_modified=False
	)
	frappe.db.commit()


# -- 1. opening ---------------------------------------------------------------


def _open_day(device_name: str, client) -> bool:
	pending = frappe.get_all(
		DAY,
		filters={
			"fiscal_device": device_name,
			"open_operation_id": ["is", "not set"],
			"status": ["in", [DAY_PENDING_OPEN, DAY_CLOSE_REQUESTED]],
		},
		fields=["name", "fiscal_day_no"],
		order_by="fiscal_day_no asc",
		limit=1,
	)
	if not pending:
		return False
	day = frappe.get_doc(DAY, pending[0].name)
	earlier_open = frappe.db.exists(
		DAY,
		{
			"fiscal_device": device_name,
			"fiscal_day_no": ["<", day.fiscal_day_no],
			"status": ["!=", DAY_CLOSED],
		},
	)
	if earlier_open:
		return False  # FDMS holds one open day at a time; the earlier day must close first

	config = client.get_config()
	device = frappe.get_doc(DEVICE, device_name)
	vat_changed = apply_config(device, config)
	device.flags.ignore_permissions = True
	device.save()
	if vat_changed and day.receipt_counter:
		notify_managers(
			device,
			_("VAT registration changed for {0}").format(device.device_name),
			_(
				"ZIMRA reports a VAT registration change. Close fiscal day {0} and open a new one before issuing more invoices (§9)."
			).format(day.fiscal_day_no),
		)

	status = client.get_status()
	if (
		status.fiscal_day_status in ("FiscalDayOpened", "FiscalDayCloseFailed")
		and status.last_fiscal_day_no == day.fiscal_day_no
	):
		operation = status.operation_id  # opened earlier; the answer was lost
	elif status.fiscal_day_status == "FiscalDayClosed":
		_number, operation = client.open_day(get_datetime(day.fiscal_day_opened), day.fiscal_day_no)
	else:
		return False  # ZIMRA is still closing the previous day
	updates = {"open_operation_id": operation or "-", "last_error": None}
	if day.status == DAY_PENDING_OPEN:
		updates["status"] = DAY_OPENED
	day.db_set(updates)
	return True


# -- 2. receipts --------------------------------------------------------------


def _backoff_minutes(attempts: int) -> float:
	return min(0.5 * 2 ** max(attempts - 1, 0), 30)


def _mirror(receipt, **values) -> None:
	frappe.db.set_value(receipt.reference_doctype, receipt.reference_name, values, update_modified=False)


def _submit_receipts(device_name: str, client, deadline: float) -> bool:
	progressed = False
	queue = frappe.get_all(
		RECEIPT,
		filters={"fiscal_device": device_name, "sync_status": ["in", [*UNSENT, REJECTED]]},
		fields=["name", "sync_status", "next_attempt_at", "fiscal_day"],
		# global numbers may restart at 1 in a new day, so the day comes first
		order_by="fiscal_day_no asc, receipt_global_no asc",
	)
	for row in queue:
		if row.sync_status == REJECTED:
			break  # strict order: nothing after a rejected receipt may be sent
		if row.next_attempt_at and get_datetime(row.next_attempt_at) > now_datetime():
			break
		if not frappe.db.get_value(DAY, row.fiscal_day, "open_operation_id"):
			break  # its day has not been opened with ZIMRA yet
		if time.monotonic() > deadline:
			break
		receipt = frappe.get_doc(RECEIPT, row.name)
		try:
			result = client.submit_receipt_payload(json.loads(receipt.payload))
		except (TransportError, FdmsApiError) as e:
			_record_failure(receipt, e)
			frappe.db.commit()
			break
		_record_success(receipt, result, client)
		frappe.db.commit()
		progressed = True
	return progressed


def _record_failure(receipt, error: FdmsError) -> None:
	code = getattr(error, "error_code", None)
	rejected = isinstance(error, FdmsApiError) and (code in NOT_RETRYABLE or error.status == 400)
	attempts = (receipt.attempts or 0) + 1
	receipt.db_set(
		{
			"sync_status": REJECTED if rejected else RETRYING,
			"attempts": attempts,
			"last_error": str(error)[:1000],
			"last_error_code": code,
			"next_attempt_at": None
			if rejected
			else add_to_date(now_datetime(), minutes=_backoff_minutes(attempts)),
		}
	)
	_mirror(receipt, zimra_status=REJECTED if rejected else RETRYING)
	if rejected or attempts in (3, 10):
		device = frappe.get_doc(DEVICE, receipt.fiscal_device)
		fix = getattr(error, "fix", None) or ""
		notify_managers(
			device,
			_("ZIMRA rejected receipt {0}").format(receipt.reference_name)
			if rejected
			else _("ZIMRA receipt {0} not yet accepted").format(receipt.reference_name),
			f"{error}. {fix}",
			reference=receipt,
		)


def _verify_server_signature(client, canonical: str, signature: dict) -> bool:
	try:
		thumbprint = signature.get("certificateThumbprint")
		key = f"zimra-fdms-certificate::{client.base_url}::{thumbprint or 'current'}"
		chain = frappe.cache.get_value(key)
		if not chain:
			chain, _valid = client.get_server_certificate(thumbprint)
			frappe.cache.set_value(key, chain, expires_in_sec=86400)
		certificate = select_certificate(chain, thumbprint)
		return bool(certificate and verify_signature(certificate, canonical, signature))
	except Exception:
		return False


def _record_success(receipt, result, client) -> None:
	errors = []
	for v in result.validation_errors:
		entry = describe(v.code)
		errors.append(
			{
				"code": v.code,
				"colour": v.colour or (entry.colour if entry else None),
				"description": v.description or (entry.message if entry else ""),
				"fix": entry.fix if entry else None,
			}
		)
	colours = {e["colour"] for e in errors}
	worst = next((c for c in ("Red", "Grey", "Yellow") if c in colours), None)
	signature = result.server_signature or {}
	verified = _verify_server_signature(
		client,
		receipt_server_string(receipt.device_signature, result.receipt_id, result.server_date),
		signature,
	)
	receipt.set("validation_errors", errors)
	receipt.update(
		{
			"sync_status": SUBMITTED,
			"fdms_receipt_id": str(result.receipt_id),
			"server_date": result.server_date,
			"submitted_on": now_datetime(),
			"server_signature": signature.get("signature"),
			"server_certificate_thumbprint": signature.get("certificateThumbprint"),
			"server_signature_verified": int(verified),
			"worst_colour": worst,
			"last_error": None,
			"last_error_code": None,
			"next_attempt_at": None,
			"attempts": (receipt.attempts or 0) + 1,
			"response": json.dumps(
				{
					"operationID": result.operation_id,
					"receiptID": result.receipt_id,
					"receiptServerSignature": signature,
					"validationErrors": [e for e in errors],
				},
				indent=1,
			),
		}
	)
	receipt.flags.ignore_permissions = True
	receipt.flags.from_sync = True
	receipt.save()
	_mirror(receipt, zimra_status=SUBMITTED, zimra_fdms_receipt_id=str(result.receipt_id))
	day = frappe.get_doc(DAY, receipt.fiscal_day)
	day.db_set(
		{
			"pending_receipts": max((day.pending_receipts or 0) - 1, 0),
			"blocking_receipts": (day.blocking_receipts or 0) + (1 if worst in BLOCKING_COLOURS else 0),
		},
		update_modified=False,
	)
	if worst in BLOCKING_COLOURS:
		device = frappe.get_doc(DEVICE, receipt.fiscal_device)
		notify_managers(
			device,
			_("ZIMRA flagged receipt {0} {1}").format(receipt.reference_name, worst),
			"; ".join(f"{e['code']}: {e['description']}" for e in errors)
			+ _(". A {0} receipt stops the fiscal day from closing automatically.").format(worst),
			reference=receipt,
		)


# -- 3. closing ---------------------------------------------------------------


def _close_day(device_name: str, client) -> bool:
	candidates = frappe.get_all(
		DAY,
		filters={
			"fiscal_device": device_name,
			"status": DAY_CLOSE_REQUESTED,
			"open_operation_id": ["is", "set"],
		},
		pluck="name",
		order_by="fiscal_day_no asc",
		limit=1,
	)
	if not candidates:
		return False
	day = frappe.get_doc(DAY, candidates[0])
	if frappe.db.exists(RECEIPT, {"fiscal_day": day.name, "sync_status": ["in", [*UNSENT, REJECTED]]}):
		return False  # wait until every receipt of the day is with ZIMRA
	if frappe.db.exists(RECEIPT, {"fiscal_day": day.name, "worst_colour": ["in", list(BLOCKING_COLOURS)]}):
		message = _(
			"Fiscal day {0} holds Grey or Red receipts. ZIMRA does not allow the device to close it; it must be closed in the ZIMRA portal."
		).format(day.fiscal_day_no)
		if day.last_error != message:
			day.db_set("last_error", message)
			notify_managers(
				frappe.get_doc(DEVICE, device_name),
				_("Fiscal day {0} cannot be closed").format(day.fiscal_day_no),
				message,
				reference=day,
			)
		return False

	counters = recompute_counters(day.name)
	ours = {c.key(): c.value for c in counters if c.value}
	kept = {c.key(): c.value for c in counters_from_rows(day.counters) if c.value}
	if kept != ours:
		frappe.log_error(
			title=_("ZIMRA counter mismatch on {0}").format(day.name),
			message=_(
				"Running counters differed from counters recomputed from the signed receipts; the recomputed counters were used."
			),
		)
	# FDMS reports its own counters for an open day; comparing first turns a
	# CountersMismatch round trip into an immediate, explained warning.
	status = client.get_status()
	if status.counters:
		theirs = {c.key(): c.value for c in status.counters if c.value}
		if theirs != ours:
			day.db_set(
				"last_error",
				_(
					"ZIMRA's counters for this day differ from the receipts issued; the close may fail with CountersMismatch."
				),
			)
	device = frappe.get_doc(DEVICE, device_name)
	signature = sign(
		credentials.private_key(device_name),
		fiscal_day_string(
			device.device_id, day.fiscal_day_no, get_datetime(day.fiscal_day_date).date(), counters
		),
	)
	try:
		operation = client.close_day(day.fiscal_day_no, counters, signature, day.receipt_counter or 0)
	except FdmsApiError as e:
		if e.error_code == "FISC03":  # a close is already in progress
			day.db_set("status", DAY_CLOSE_INITIATED)
			return True
		if e.retryable:
			raise
		day.db_set(
			{"status": DAY_CLOSE_FAILED, "closing_error_code": e.error_code, "last_error": str(e)[:1000]}
		)
		notify_managers(
			device,
			_("Fiscal day {0} could not be closed").format(day.fiscal_day_no),
			f"{e}. {e.fix or ''}",
			reference=day,
		)
		return True
	day.set("counters", counters_to_rows(counters))
	day.update(
		{
			"status": DAY_CLOSE_INITIATED,
			"close_operation_id": operation,
			"device_signature_hash": signature.hash,
			"device_signature": signature.signature,
			"last_error": None,
		}
	)
	day.flags.ignore_permissions = True
	day.save()
	return True


# -- 4. polling ---------------------------------------------------------------


def _poll_closing(device_name: str, client) -> bool:
	closing = frappe.get_all(
		DAY,
		filters={"fiscal_device": device_name, "status": DAY_CLOSE_INITIATED},
		pluck="name",
		order_by="fiscal_day_no asc",
		limit=1,
	)
	if not closing:
		return False
	day = frappe.get_doc(DAY, closing[0])
	status = client.get_status()
	if (
		status.fiscal_day_status == "FiscalDayClosed"
		and (status.last_fiscal_day_no or 0) >= day.fiscal_day_no
	):
		device = frappe.get_doc(DEVICE, device_name)
		signature = status.server_signature or {}
		verified = False
		if status.fiscal_day_closed and status.reconciliation_mode:
			canonical = fiscal_day_server_string(
				device.device_id,
				day.fiscal_day_no,
				get_datetime(day.fiscal_day_date).date(),
				status.fiscal_day_closed,
				status.reconciliation_mode,
				status.counters
				if status.reconciliation_mode.upper() == "MANUAL"
				else counters_from_rows(day.counters),
				day.device_signature,
			)
			verified = _verify_server_signature(client, canonical, signature)
		day.db_set(
			{
				"status": DAY_CLOSED,
				"fiscal_day_closed": status.fiscal_day_closed or now_datetime(),
				"reconciliation_mode": status.reconciliation_mode,
				"server_signature": signature.get("signature"),
				"server_signature_verified": int(verified),
				"closing_error_code": None,
				"last_error": None,
			}
		)
		return True
	refused = status.fiscal_day_status == "FiscalDayCloseFailed" or (
		# Also seen (zimra-fdms simulator): the day reported Opened again with
		# a closing error code. Either way FDMS refused this close.
		status.fiscal_day_status == "FiscalDayOpened"
		and status.closing_error_code
		and status.last_fiscal_day_no == day.fiscal_day_no
	)
	if refused:
		code = status.closing_error_code
		entry = describe(code)
		day.db_set(
			{
				"status": DAY_CLOSE_FAILED,
				"closing_error_code": code,
				"last_error": entry.message if entry else code,
			}
		)
		notify_managers(
			frappe.get_doc(DEVICE, device_name),
			_("ZIMRA could not close fiscal day {0}").format(day.fiscal_day_no),
			f"{code}: {entry.message if entry else ''} {entry.fix if entry else ''}",
			reference=day,
		)
		return True
	return False
