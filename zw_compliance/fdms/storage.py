"""Storage protocol, ported from src/core/storage.ts.

Exactly 3 keys are ever used by the whole system: "day-state",
"pending-submit", "last-receipt-global-no". Values are JSON blobs that
embed the deviceId themselves, so a single Storage instance is assumed to
serve one device (true for this app: one Zimra Settings singleton per site).

Contract: `set` MUST be atomic for its key - the crash-safety of the whole
pending-submit / reconcile() design depends on a reader never observing a
half-written value, and every write must actually reach durable storage
before `set` returns (no buffering, no "eventually flushed" caching). See
zw_compliance.zimra.storage.FrappeFileStorage for the concrete
write-then-atomic-rename implementation used in this app.
"""

from __future__ import annotations

from typing import Optional, Protocol


class Storage(Protocol):
	def get(self, key: str) -> Optional[str]: ...

	def set(self, key: str, value: str) -> None: ...

	def delete(self, key: str) -> None: ...


class MemoryStorage:
	"""In-memory Storage, for tests only - not crash-safe, not durable."""

	def __init__(self):
		self._data: dict[str, str] = {}

	def get(self, key: str) -> Optional[str]:
		return self._data.get(key)

	def set(self, key: str, value: str) -> None:
		self._data[key] = value

	def delete(self, key: str) -> None:
		self._data.pop(key, None)


def guarded_storage(inner: Storage, guard) -> Storage:
	"""Wrap `inner` so `guard(key)` can raise inside set()/delete() before
	delegating - used to simulate crash-at-write-N in reconciliation tests.
	"""

	class _Guarded:
		def get(self, key: str) -> Optional[str]:
			return inner.get(key)

		def set(self, key: str, value: str) -> None:
			guard("set", key)
			inner.set(key, value)

		def delete(self, key: str) -> None:
			guard("delete", key)
			inner.delete(key)

	return _Guarded()


KEY_DAY_STATE = "day-state"
KEY_PENDING = "pending-submit"
KEY_LAST_GLOBAL = "last-receipt-global-no"
