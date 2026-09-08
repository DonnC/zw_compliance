"""Offline receipt queue, ported from src/core/queue.ts.

Covers ZIMRA's 72-hour offline grace window: receipts are numbered,
hash-chained and signed at sale time (device.sign_receipt) and appended to
a journal; flush() submits them in strict FIFO order and moves a commit
cursor. A crash between append and applyPrepared leaves a journal entry
whose stateAfter is ahead of the device's persisted state; the next
ensure_loaded() detects that and calls device.restore_state() to catch up,
so a receipt number is never reused or skipped.

The TS SDK also carries a QueueStorage-adapter and an "upgrade legacy
unsigned 0.3.x snapshots" code path for backward compatibility with
existing installs. This is a new module with no prior installs to migrate,
so that compatibility branch is deliberately not ported - every journal
entry here is always an already-signed PreparedReceipt.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .device import FiscalDevice
from .errors import FdmsApiError, is_network_error
from .journal import Journal, JournalEntry, MemoryJournal
from .serialization import prepared_receipt_from_dict, prepared_to_dict
from .types import PreparedReceipt, SubmittedReceipt


@dataclass
class PendingItem:
	cursor: int
	item: PreparedReceipt


@dataclass
class FlushResult:
	submitted: list[SubmittedReceipt]
	remaining: int
	error: Optional[BaseException] = None


class OfflineReceiptQueue:
	def __init__(self, device: FiscalDevice, journal: Optional[Journal] = None):
		self.device = device
		self.journal: Journal = journal or MemoryJournal()
		self._pending: list[PendingItem] = []
		self._loaded = False

	@property
	def size(self) -> int:
		return len(self._pending)

	@property
	def pending_items(self) -> list[PreparedReceipt]:
		return [p.item for p in self._pending]

	@property
	def oldest_pending_age_ms(self) -> Optional[float]:
		if not self._pending:
			return None
		head = self._pending[0].item
		try:
			sale_time = datetime.fromisoformat(head.date)
		except (TypeError, ValueError):
			return None
		return (datetime.now() - sale_time).total_seconds() * 1000

	def _ensure_loaded(self) -> None:
		if self._loaded:
			return
		entries: list[JournalEntry] = self.journal.read_from(self.journal.committed())
		self._pending = [
			PendingItem(cursor=e.cursor, item=prepared_receipt_from_dict(json.loads(e.record))) for e in entries
		]
		self._loaded = True

		if self._pending:
			last = self._pending[-1].item
			state = self.device.get_state()
			if state and last.state_after.receipt_global_no > state.receipt_global_no:
				self.device.restore_state(last.state_after)

	def _take(self, prepared: PreparedReceipt) -> None:
		cursor = self.journal.committed() + len(self._pending)
		self.journal.append(json.dumps(prepared_to_dict(prepared)))  # 1. append first
		self.device.apply_prepared(prepared)  # 2. then advance device state
		self._pending.append(PendingItem(cursor=cursor, item=prepared))

	def enqueue(self, receipt_input) -> PreparedReceipt:
		self._ensure_loaded()
		prepared = self.device.sign_receipt(receipt_input)
		self._take(prepared)
		return prepared

	def submit_or_enqueue(self, receipt_input) -> Optional[SubmittedReceipt]:
		self._ensure_loaded()
		if self._pending:
			result = self.flush()
			if result.remaining > 0:
				self.enqueue(receipt_input)
				return None
		prepared = self.device.sign_receipt(receipt_input)
		try:
			return self.device.submit_prepared(prepared)
		except FdmsApiError:
			raise
		except Exception as error:  # noqa: BLE001 - network failure -> journal it
			if not is_network_error(error):
				raise
			self.device.discard_pending()
			self._take(prepared)
			return None

	def _submit_one(self, item: PreparedReceipt) -> SubmittedReceipt:
		marker = self.device.pending_receipt()
		if marker and marker.receipt.receipt_global_no == item.receipt.receipt_global_no:
			result = self.device.reconcile()
			if result.action == "rejected":
				raise result.error
			response = result.response if result.action == "resubmitted" else {}
			return SubmittedReceipt(receipt=item.receipt, response=response, qr_data=self.device.qr_data(item))
		return self.device.submit_prepared(item)

	def flush(self) -> FlushResult:
		self._ensure_loaded()
		submitted: list[SubmittedReceipt] = []
		while self._pending:
			head = self._pending[0]
			try:
				submitted.append(self._submit_one(head.item))
				self.journal.commit(head.cursor + 1)
				self._pending.pop(0)
			except Exception as error:  # noqa: BLE001 - stop at first failure, strict FIFO
				return FlushResult(submitted=submitted, remaining=len(self._pending), error=error)
		return FlushResult(submitted=submitted, remaining=0)
