"""End-to-end check against the zimra-fdms SDK simulator (real HTTPS + mTLS).

Start the simulator, then run this against a development site::

    npx zimra-fdms-simulator@0.4.1 --port 8443 --ca /tmp/zimra-sim-ca.pem
    bench --site <site> execute zw_compliance.fiscalisation.tests.e2e.run \\
        --kwargs "{'url': 'https://127.0.0.1:8443', 'ca': '/tmp/zimra-sim-ca.pem'}"

Everything runs in one transaction that is rolled back at the end, so the
site is left as it was. Steps:

1. register a device (CSR -> certificate over HTTPS) and getConfig;
2. day 1: a walk-in sale and a business sale -> submitted in order, server
   signatures verified -> close -> Closed, FDMS day signature verified;
3. day 2: a sale and a credit note against it -> submitted -> close.

Known divergences of the SDK simulator (zimra-fdms 0.4.1) from the spec,
reported by this script and not treated as failures:

* ``receiptServerSignature`` is computed over ``receiptGlobalNo‖serverDate‖hash``,
  not ``receiptDeviceSignature‖receiptID‖serverDate`` (§13.2.2). The script
  verifies the simulator's signature over its own string, which proves the
  certificate selection and signature checks, then reports the divergence;
* ``fiscalDayServerSignature`` is always null (§13.3.2 expects a signature);
* credit-note counters are accumulated with the opposite sign to §6, so a
  day holding a spec-correct (negative) credit note is refused with
  ``BadCertificateSignature``.

The in-process simulator (``simulator.py``) follows the spec on all three and
is what the test-suite uses.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import frappe

from . import fixtures as fx

SIMULATOR_CN_DIVERGENCE = "BadCertificateSignature"


def _say(message: str) -> None:
	print(f"  - {message}")


def _wait_closed(device_name: str, day_name: str, seconds: float = 15) -> str:
	from zw_compliance.fiscalisation.sync import _run

	deadline = time.monotonic() + seconds
	while time.monotonic() < deadline:
		_run(device_name)
		status = frappe.db.get_value("Zimra Fiscal Day", day_name, "status")
		if status in ("Closed", "Close Failed"):
			return status
		time.sleep(0.5)
	return frappe.db.get_value("Zimra Fiscal Day", day_name, "status")


def _simulator_signature_valid(device, receipt) -> bool:
	"""Check the SDK simulator's receipt signature over its own (non-spec) string."""
	from zw_compliance.fdms.verify import select_certificate, verify_signature
	from zw_compliance.fiscalisation.device import connect

	with connect(device, mtls=False) as client:
		chain, _valid = client.get_server_certificate(receipt.server_certificate_thumbprint)
	certificate = select_certificate(chain, receipt.server_certificate_thumbprint)
	server_date = frappe.utils.get_datetime(receipt.server_date).strftime("%Y-%m-%dT%H:%M:%S")
	canonical = f"{receipt.receipt_global_no}{server_date}{receipt.device_signature_hash}"
	return bool(
		certificate and verify_signature(certificate, canonical, {"signature": receipt.server_signature})
	)


def _check_submitted(device, invoices, divergences: set) -> None:
	for invoice in invoices:
		invoice.reload()
		receipt = frappe.get_doc("Zimra Fiscal Receipt", invoice.zimra_fiscal_receipt)
		errors = [f"{e.code} ({e.colour})" for e in receipt.validation_errors]
		assert receipt.sync_status == "Submitted", (
			f"{invoice.name}: {receipt.sync_status} {receipt.last_error}"
		)
		assert not errors, f"{invoice.name}: {errors}"
		if not receipt.server_signature_verified:
			assert _simulator_signature_valid(device, receipt), f"{invoice.name}: FDMS signature invalid"
			divergences.add("receipt server signature string (§13.2.2)")
		_say(
			f"{receipt.receipt_type} {invoice.name}: global no {receipt.receipt_global_no}, "
			f"FDMS id {receipt.fdms_receipt_id}, code {receipt.verification_code}"
		)


def run(url: str, ca: str, device_id: int | None = None) -> None:
	"""``device_id`` defaults to a fresh one per run: the simulator remembers devices."""
	device_id = device_id or 10000 + int(time.time()) % 900000
	from erpnext.controllers.sales_and_purchase_return import make_return_doc

	from zw_compliance.fiscalisation import device as lifecycle
	from zw_compliance.fiscalisation.day import request_close
	from zw_compliance.fiscalisation.sync import _run

	with open(ca, encoding="utf-8") as fh:
		ca_pem = fh.read()
	frappe.flags.zimra_transport_factory = None
	with patch.object(frappe.db, "commit", lambda *a, **k: None):
		try:
			print(f"ZIMRA end-to-end against {url}")
			fx.ensure_fiscal_year()
			fx.ensure_company()
			fx.configure_settings()
			fx.ensure_customer(fx.WALK_IN)
			fx.ensure_customer(fx.BUSINESS, tax_id=fx.BUSINESS_TIN, vat="220000777")
			device = frappe.get_doc(
				{
					"doctype": "Zimra Fiscal Device",
					"device_name": "Simulator Till",
					"company": fx.COMPANY,
					"environment": "Test",
					"api_url": url,
					"server_ca_certificate": ca_pem,
					"device_id": device_id,
					"serial_no": f"ZWE2E-{device_id}",
					"model_name": "ERPNext",
					"model_version": "v1",
					"activation_key": "E2E00001",
					"is_default": 1,
					# The simulator does not implement verifyTaxpayerInformation (§4.1).
					"status": "Taxpayer Verified",
				}
			).insert(ignore_permissions=True)
			lifecycle.register(device.name)
			device.reload()
			_say(f"registered {device.name}: {device.taxpayer_name}, VAT {device.vat_number or '-'}")
			_say(f"certificate valid till {device.certificate_valid_till}")

			standard = max(
				frappe.get_all(
					"Zimra Tax Mapping",
					filters={"company": fx.COMPANY, "is_exempt": 0},
					fields=["zimra_tax_id", "zimra_tax_percent"],
				),
				key=lambda m: m.zimra_tax_percent,
			)
			fx.ensure_item(fx.ITEM, "84713000")

			day1 = [
				fx.make_invoice([(fx.ITEM, 2, 115)], tax_id=standard.zimra_tax_id),
				fx.make_invoice([(fx.ITEM, 1, 49.99)], customer=fx.BUSINESS, tax_id=standard.zimra_tax_id),
			]
			divergences: set[str] = set()
			_run(device.name)
			_check_submitted(device, day1, divergences)
			first_day = frappe.db.get_value(
				"Zimra Fiscal Receipt", day1[0].zimra_fiscal_receipt, "fiscal_day"
			)
			request_close(first_day)
			status = _wait_closed(device.name, first_day)
			day = frappe.get_doc("Zimra Fiscal Day", first_day)
			assert status == "Closed", f"day 1 {status}: {day.closing_error_code} {day.last_error}"
			if not day.server_signature_verified:
				divergences.add("fiscal day server signature missing (§13.3.2)")
			_say(f"day {day.fiscal_day_no} closed")

			sale = fx.make_invoice([(fx.ITEM, 3, 10)], tax_id=standard.zimra_tax_id)
			note = make_return_doc("Sales Invoice", sale.name)
			note.zimra_receipt_notes = "Returned (end-to-end check)"
			note.insert(ignore_permissions=True)
			note.submit()
			_run(device.name)
			_check_submitted(device, [sale, note], divergences)
			second_day = frappe.db.get_value("Zimra Fiscal Receipt", note.zimra_fiscal_receipt, "fiscal_day")
			request_close(second_day)
			status = _wait_closed(device.name, second_day)
			day = frappe.get_doc("Zimra Fiscal Day", second_day)
			if status == "Closed":
				_say(f"day {day.fiscal_day_no} with a credit note closed")
			elif status == "Close Failed" and day.closing_error_code == SIMULATOR_CN_DIVERGENCE:
				divergences.add("credit-note counter sign (§6): close refused with BadCertificateSignature")
				_say(f"day {day.fiscal_day_no} with a credit note: refused by the simulator (divergence)")
			else:
				raise AssertionError(f"day 2 {status}: {day.closing_error_code} {day.last_error}")
			for item in sorted(divergences):
				print(f"  ! simulator divergence: {item}")
			print("PASSED")
		finally:
			frappe.db.rollback()
