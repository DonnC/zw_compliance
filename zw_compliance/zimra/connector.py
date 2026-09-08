"""ZimraConnector: the single entry point everything else in this app calls
into for ZIMRA fiscalisation - document hooks, whitelisted desk API, and
scheduled tasks. Wires a fdms.FiscalDevice from Zimra Settings and drives
the ERPNext-facing side effects (receipt logs, custom field updates, print
QR).

Sequence integrity: every operation that can issue or submit a receipt
number goes through `with device_lock(): ... device.reconcile() ...` so
concurrent Frappe workers never race on receiptGlobalNo (RCPT012), and a
half-finished submit from a previous request is always settled before a new
number is issued.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import now_datetime

from zw_compliance.fdms.device import DayNotClosableError, FiscalDevice, FiscalDeviceOptions, PendingSubmitError
from zw_compliance.fdms.errors import FdmsApiError
from zw_compliance.fdms.queue import OfflineReceiptQueue
from zw_compliance.fdms.types import DeviceIdentity

from . import fiscal_day, qr_render
from .journal import FrappeFileJournal
from .lock import device_lock
from .receipt_builder import build_receipt_input
from .signer import PemFileSigner
from .storage import FrappeFileStorage
from .transport import RequestsTransport

CERTIFICATE_TITLE = "ZIMRA Certificate"


class ZimraConnector:
	def __init__(self, settings=None):
		self.settings = settings or frappe.get_cached_doc("Zimra Settings")
		self._device_obj: FiscalDevice | None = None
		self._queue: OfflineReceiptQueue | None = None

	# -- construction -------------------------------------------------------

	@staticmethod
	def _file_path(file_url):
		if not file_url:
			return None
		return frappe.get_site_path(file_url.lstrip("/"))

	@staticmethod
	def _file_content(file_url):
		path = ZimraConnector._file_path(file_url)
		if not path:
			return None
		with open(path, "r", encoding="utf-8") as f:
			return f.read()

	@property
	def environment(self) -> str:
		return "production" if self.settings.environment == "Production" else "test"

	def _identity(self) -> DeviceIdentity:
		return DeviceIdentity(
			device_id=self.settings.device_id,
			serial_number=self.settings.serial_no,
			model_name=self.settings.model_name or "Server",
			model_version=self.settings.model_version or "v1",
		)

	def check_certificate(self, raise_on_problem: bool = True) -> str | None:
		"""Certificate-lifecycle guard, checked once per connector - not per
		receipt - so the hot path stays fast. Returns a problem message
		(and optionally raises) rather than letting fiscalisation fail deep
		inside an FDMS call with a cryptic mTLS error.
		"""
		if not self.settings.device_certificate or not self.settings.private_key:
			message = _("This ZIMRA device has not been registered yet - no certificate on file.")
		elif self.settings.certificate_valid_till and frappe.utils.get_datetime(
			self.settings.certificate_valid_till
		) < now_datetime():
			message = _("The ZIMRA device certificate expired on {0}. Renew it before fiscalising.").format(
				self.settings.certificate_valid_till
			)
		else:
			return None
		if raise_on_problem:
			frappe.throw(message, title=CERTIFICATE_TITLE)
		return message

	def device(self) -> FiscalDevice:
		if self._device_obj:
			return self._device_obj
		self.check_certificate()
		signer = PemFileSigner(self._file_content(self.settings.private_key))
		transport = RequestsTransport(
			cert_path=self._file_path(self.settings.device_certificate),
			key_path=self._file_path(self.settings.private_key),
			ca_path=self._file_path(self.settings.server_certificate),
		)
		storage = FrappeFileStorage()
		options = FiscalDeviceOptions(
			environment=self.environment,
			base_url=self.settings.api_url or None,
			signature_format=self.settings.signature_format or "der",
		)
		self._device_obj = FiscalDevice(self._identity(), signer, transport, storage, options=options)
		self._device_obj.load_persisted_state()
		return self._device_obj

	def queue(self) -> OfflineReceiptQueue:
		if not self._queue:
			self._queue = OfflineReceiptQueue(self.device(), FrappeFileJournal())
		return self._queue

	# -- fiscal day -----------------------------------------------------

	def open_day(self):
		with device_lock():
			device = self.device()
			device.reconcile()
			result = device.open_day()
			opened_at = now_datetime()
			fiscal_day.record_day_opened(self.settings, result["fiscalDayNo"], opened_at)
			frappe.db.set_value(
				"Zimra Settings",
				None,
				{"fiscal_day_no": result["fiscalDayNo"], "fiscal_day_status": "FiscalDayOpened", "receipt_counter": 0},
			)
			return result

	def close_day(self, force: bool = False):
		with device_lock():
			device = self.device()
			device.reconcile()
			counters = list(device.get_state().counters) if device.get_state() else []
			fiscal_day_no = device.get_state().fiscal_day_no if device.get_state() else self.settings.fiscal_day_no
			document_counts = self._document_counts_for_day()
			try:
				result = device.close_day(force=force)
			except DayNotClosableError:
				frappe.throw(
					_(
						"Fiscal day {0} holds a receipt with a Red validation error and cannot be closed by "
						"this device. Contact ZIMRA or use the FDMS test portal to force-close it."
					).format(fiscal_day_no),
					title="ZIMRA Day Not Closable",
				)
			fiscal_day.record_day_closed(self.settings, fiscal_day_no, counters, document_counts)
			return result

	def _document_counts_for_day(self) -> dict:
		if not self.settings.current_fiscal_day and not self.settings.fiscal_day_no:
			return {}
		counts = frappe.db.get_all(
			"Zimra Receipt Log",
			filters={"creation": [">=", frappe.utils.today()], "is_fiscalised": 1},
			fields=["receipt_type", "count(name) as qty"],
			group_by="receipt_type",
		)
		return {row.receipt_type: row.qty for row in counts}

	def ensure_day_open(self):
		device = self.device()
		device.load_persisted_state()
		if device.state is None:
			if not self.settings.auto_open_day:
				frappe.throw(_("No open ZIMRA fiscal day, and Auto Open Fiscal Day is disabled."))
			self.open_day()

	# -- receipts -----------------------------------------------------------

	def fiscalise(self, doc):
		if doc.get("custom_zimra_is_fiscalised"):
			return
		self.check_certificate()
		with device_lock():
			device = self.device()
			device.reconcile()
			self.ensure_day_open()
			receipt_input = build_receipt_input(doc, self.settings)

			queue = self.queue()
			submitted = queue.submit_or_enqueue(receipt_input)

			if submitted is None:
				# Journalled for later - offline / FDMS unreachable. Mark the
				# document so it isn't re-queued, but do not claim fiscalised.
				doc.db_set("custom_zimra_fiscalisation_mode", "Queued (Offline)")
				pending = queue.pending_items[-1] if queue.pending_items else None
				self._create_receipt_log(doc, pending, response=None, fiscalised=False)
				return

			self._apply_submitted(doc, submitted)
			frappe.db.set_value(
				"Zimra Settings",
				None,
				{
					"global_receipt_no": submitted.receipt.receipt_global_no,
					"receipt_counter": submitted.receipt.receipt_counter,
				},
			)

	def _apply_submitted(self, doc, submitted):
		response = submitted.response or {}
		verification_code = (response.get("receiptServerSignature") or {}).get("signature", "")[-16:]
		qr_html = qr_render.qr_html(submitted.qr_data, verification_code) if submitted.qr_data else None

		doc.db_set(
			{
				"custom_zimra_is_fiscalised": 1,
				"custom_zimra_fiscalisation_mode": "New Submission",
				"custom_zimra_receipt_id": response.get("receiptID"),
				"custom_zimra_global_no": submitted.receipt.receipt_global_no,
				"custom_zimra_receipt_counter": submitted.receipt.receipt_counter,
				"custom_zimra_fiscal_day_no": self.settings.fiscal_day_no,
				"custom_zimra_fiscal_date": response.get("serverDate"),
				"custom_zimra_qr_code": submitted.qr_data,
				"custom_zimra_verification_code": verification_code,
				"custom_zimra_server_signature": (response.get("receiptServerSignature") or {}).get("signature"),
			}
		)
		if qr_html:
			doc.db_set("custom_zimra_qr_code_view", qr_html)

		self._create_receipt_log(doc, submitted.receipt, response, fiscalised=True, qr_data=submitted.qr_data)

	def _create_receipt_log(self, doc, prepared_or_receipt, response, fiscalised: bool, qr_data=None):
		receipt = prepared_or_receipt.receipt if hasattr(prepared_or_receipt, "receipt") else prepared_or_receipt
		log = frappe.get_doc(
			{
				"doctype": "Zimra Receipt Log",
				"reference_doctype": doc.doctype,
				"reference_docname": doc.name,
				"receipt_id": (response or {}).get("receiptID"),
				"receipt_global_no": getattr(receipt, "receipt_global_no", None),
				"receipt_counter": getattr(receipt, "receipt_counter", None),
				"receipt_type": getattr(receipt, "receipt_type", None),
				"currency": doc.currency,
				"total_amount": doc.base_grand_total,
				"is_fiscalised": 1 if fiscalised else 0,
				"device_signature_hash": receipt.receipt_device_signature.hash if getattr(receipt, "receipt_device_signature", None) else None,
				"device_signature": receipt.receipt_device_signature.signature if getattr(receipt, "receipt_device_signature", None) else None,
				"qr_data": qr_data,
				"raw_zimra_response": frappe.as_json(response) if response else None,
			}
		)
		for tax in getattr(receipt, "receipt_taxes", []) or []:
			log.append(
				"table_tax_details",
				{
					"tax_id": tax.tax_id,
					"tax_percent": tax.tax_percent,
					"tax_amount": tax.tax_amount,
					"net_amount": tax.sales_amount_with_tax - tax.tax_amount,
					"sales_amount_with_tax": tax.sales_amount_with_tax,
				},
			)
		for v in (response or {}).get("validationErrors") or []:
			log.append(
				"validation_errors",
				{
					"code": v.get("validationErrorCode"),
					"colour": v.get("validationErrorColor"),
					"description": v.get("validationErrorDescription"),
				},
			)
		log.insert(ignore_permissions=True)
		return log

	# -- device ops -----------------------------------------------------

	def ping(self):
		device = self.device()
		return device.ping()

	def get_status(self):
		device = self.device()
		status = device.get_status()
		frappe.db.set_value("Zimra Settings", None, {"last_status_sync": now_datetime()})
		return status
