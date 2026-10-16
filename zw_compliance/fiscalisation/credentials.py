"""Device key custody.

The device private key is stored in the device's ``private_key`` Password
field, i.e. encrypted at rest with the site's encryption key (spec §12.1:
"Device should ... securely store them in encrypted form, never letting
private key to go outside of device"). It is decrypted only in memory, and,
because mutual TLS through ``requests`` needs file paths, written to a 0600
file under ``sites/<site>/private/zimra`` for the duration of a single
session, then removed.
"""

from __future__ import annotations

import contextlib
import os
import tempfile

import frappe
from frappe.utils.password import get_decrypted_password, set_encrypted_password

from zw_compliance.fdms.signer import PrivateKey, load_private_key

from .constants import DEVICE

_MASK = "*" * 8


def store_private_key(device_name: str, pem: str) -> None:
	set_encrypted_password(DEVICE, device_name, pem, "private_key")
	frappe.db.set_value(DEVICE, device_name, "private_key", _MASK, update_modified=False)


def private_key_pem(device_name: str) -> str:
	pem = get_decrypted_password(DEVICE, device_name, "private_key", raise_exception=False)
	if not pem:
		frappe.throw(
			frappe._("Fiscal device {0} has no private key. Register the device first.").format(device_name)
		)
	return pem


def private_key(device_name: str) -> PrivateKey:
	return load_private_key(private_key_pem(device_name))


def activation_key(device_name: str) -> str:
	key = get_decrypted_password(DEVICE, device_name, "activation_key", raise_exception=False)
	if not key:
		frappe.throw(frappe._("Enter the activation key issued by ZIMRA for this device."))
	return key.strip()


@contextlib.contextmanager
def mtls_files(device_name: str, certificate_pem: str):
	"""Yield ``(cert_path, key_path)`` for one TLS session; files are deleted afterwards."""
	folder = frappe.get_site_path("private", "zimra")
	os.makedirs(folder, mode=0o700, exist_ok=True)
	paths = []
	try:
		for content in (certificate_pem, private_key_pem(device_name)):
			fd, path = tempfile.mkstemp(dir=folder, prefix=".tls-", suffix=".pem")
			paths.append(path)
			os.fchmod(fd, 0o600)
			with os.fdopen(fd, "w") as fh:
				fh.write(content)
		yield paths[0], paths[1]
	finally:
		for path in paths:
			with contextlib.suppress(FileNotFoundError):
				os.remove(path)


@contextlib.contextmanager
def ca_bundle(pem: str | None):
	"""Yield a CA file path for a Test-environment override server, else ``True`` (the certifi CA bundle)."""
	if not pem:
		yield True
		return
	folder = frappe.get_site_path("private", "zimra")
	os.makedirs(folder, mode=0o700, exist_ok=True)
	fd, path = tempfile.mkstemp(dir=folder, prefix=".ca-", suffix=".pem")
	try:
		with os.fdopen(fd, "w") as fh:
			fh.write(pem)
		yield path
	finally:
		with contextlib.suppress(FileNotFoundError):
			os.remove(path)
