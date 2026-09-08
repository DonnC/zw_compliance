"""Append-only journal abstraction behind the offline receipt queue, ported
from src/core/journal.ts (the QueueStorage-adapter / legacy-unsigned-entry
upgrade path from the TS SDK's 0.3.x compatibility layer is intentionally
NOT ported here - this is a fresh module with nothing to migrate from; see
fdms/queue.py's docstring).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class JournalEntry:
	cursor: int
	record: str


class Journal(Protocol):
	def append(self, record: str) -> None: ...

	def read_from(self, cursor: int) -> list[JournalEntry]: ...

	def commit(self, cursor: int) -> None: ...

	def committed(self) -> int: ...


class MemoryJournal:
	"""In-memory Journal, for tests only - not durable across restarts."""

	def __init__(self):
		self._records: list[str] = []
		self._cursor: int = 0

	def append(self, record: str) -> None:
		self._records.append(record)

	def read_from(self, cursor: int) -> list[JournalEntry]:
		return [JournalEntry(cursor=i, record=r) for i, r in enumerate(self._records) if i >= cursor]

	def commit(self, cursor: int) -> None:
		self._cursor = max(self._cursor, cursor)

	def committed(self) -> int:
		return self._cursor
