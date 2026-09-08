"""File-backed Journal implementation of fdms.journal.Journal, for the
offline receipt queue. Records are appended as newline-delimited JSON to an
append-only file (O(1) append, never rewritten, matching the ordering
guarantee fdms.queue.OfflineReceiptQueue.take() relies on); the commit
cursor is a small separate file written atomically via FrappeFileStorage's
temp-then-rename pattern, so ZIMRA's 72-hour offline grace window survives
a Frappe worker restart.
"""

from __future__ import annotations

import os

from zw_compliance.fdms.journal import JournalEntry

from .storage import FrappeFileStorage

_CURSOR_KEY = "offline-queue-cursor"
_JOURNAL_FILENAME = "offline-queue.jsonl"


class FrappeFileJournal:
	def __init__(self):
		self._cursor_storage = FrappeFileStorage()
		self._journal_path = os.path.join(self._cursor_storage.dir, _JOURNAL_FILENAME)

	def append(self, record: str) -> None:
		with open(self._journal_path, "a", encoding="utf-8") as f:
			f.write(record.replace("\n", " ") + "\n")
			f.flush()
			os.fsync(f.fileno())

	def read_from(self, cursor: int) -> list[JournalEntry]:
		if not os.path.exists(self._journal_path):
			return []
		with open(self._journal_path, "r", encoding="utf-8") as f:
			lines = f.readlines()
		return [JournalEntry(cursor=i, record=line.rstrip("\n")) for i, line in enumerate(lines) if i >= cursor]

	def commit(self, cursor: int) -> None:
		current = self.committed()
		self._cursor_storage.set(_CURSOR_KEY, str(max(current, cursor)))

	def committed(self) -> int:
		raw = self._cursor_storage.get(_CURSOR_KEY)
		return int(raw) if raw else 0
