"""File-backed Storage implementation of fdms.storage.Storage.

Deliberately NOT a Frappe "File" doctype attachment - this state (fiscal
day counters, hash chain, pending-submit marker) is written on the hot
path of every receipt and must be durable and atomic per write, which is
best served by a plain file with a write-temp-then-atomic-rename pattern
(same approach the zimra-fdms TS SDK's own FileStorage uses), not the ORM.

Every `set()` writes through to disk synchronously before returning - no
buffering, no deferred flush - because the crash-safety of the whole
pending-submit / reconcile() design in fdms.device depends on it.
"""

from __future__ import annotations

import os
import tempfile
from typing import Optional

import frappe


def _state_dir() -> str:
	path = frappe.utils.get_site_path("private", "files", "zimra_state")
	os.makedirs(path, exist_ok=True)
	return path


def _safe_filename(key: str) -> str:
	# The only keys ever used are fdms.storage.KEY_DAY_STATE / KEY_PENDING /
	# KEY_LAST_GLOBAL, but keep this defensive against path traversal anyway.
	return key.replace("/", "_").replace("..", "_") + ".json"


class FrappeFileStorage:
	def __init__(self, namespace: Optional[str] = None):
		"""`namespace` isolates multiple devices sharing one site (not used
		today - Zimra Settings is a Single doctype, one device per site -
		but keeps this class correct if that ever changes).
		"""
		self.dir = _state_dir()
		if namespace:
			self.dir = os.path.join(self.dir, namespace)
			os.makedirs(self.dir, exist_ok=True)

	def _path(self, key: str) -> str:
		return os.path.join(self.dir, _safe_filename(key))

	def get(self, key: str) -> Optional[str]:
		path = self._path(key)
		if not os.path.exists(path):
			return None
		with open(path, "r", encoding="utf-8") as f:
			return f.read()

	def set(self, key: str, value: str) -> None:
		path = self._path(key)
		fd, tmp_path = tempfile.mkstemp(dir=self.dir, prefix=".tmp-")
		try:
			with os.fdopen(fd, "w", encoding="utf-8") as f:
				f.write(value)
				f.flush()
				os.fsync(f.fileno())
			os.replace(tmp_path, path)  # atomic on POSIX
		except BaseException:
			if os.path.exists(tmp_path):
				os.remove(tmp_path)
			raise

	def delete(self, key: str) -> None:
		path = self._path(key)
		if os.path.exists(path):
			os.remove(path)
