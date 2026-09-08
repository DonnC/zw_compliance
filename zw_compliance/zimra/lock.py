"""Cross-process device lock used to serialise fiscalisation.

The FDMS sequence-integrity rule (RCPT012) requires receiptGlobalNo to be
issued strictly one-at-a-time per device. Frappe can run multiple
gunicorn/RQ workers on one site, so incrementing the counter and submitting
to FDMS is NOT atomic across workers unless serialised. An flock on a file
under the same private state directory as FrappeFileStorage is simple,
requires no extra service, and works across processes on one host - which
is exactly the deployment shape here (Zimra Settings is a Single doctype:
one device per site).
"""

from __future__ import annotations

import contextlib
import fcntl
import os

import frappe


@contextlib.contextmanager
def device_lock(timeout_seconds: int = 30):
	path = frappe.utils.get_site_path("private", "files", "zimra_state")
	os.makedirs(path, exist_ok=True)
	lock_path = os.path.join(path, ".device.lock")
	fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
	try:
		_flock_with_timeout(fd, timeout_seconds)
		yield
	finally:
		fcntl.flock(fd, fcntl.LOCK_UN)
		os.close(fd)


def _flock_with_timeout(fd: int, timeout_seconds: int) -> None:
	import time

	deadline = time.monotonic() + timeout_seconds
	while True:
		try:
			fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
			return
		except BlockingIOError:
			if time.monotonic() >= deadline:
				frappe.throw("Could not acquire the ZIMRA device lock - another fiscalisation is in progress.")
			time.sleep(0.1)
