"""Fiscal device lifecycle, FDMS connections and sequencing locks.

Lifecycle (``Zimra Fiscal Device.status``)::

    Draft --verify_taxpayer--> Taxpayer Verified --register--> Registered --configure--> Active
                                                                        (renew_certificate any time)

* ``verify_taxpayer`` calls verifyTaxpayerInformation (spec §4.1) so the user
  can confirm the device belongs to the right taxpayer *before* registering.
* ``register`` creates a P-256 key, sends the CSR with the activation key
  (§4.2) and stores the key encrypted (see :mod:`.credentials`).
* ``configure`` calls getConfig and getStatus (§4.4, §4.5), stores the
  taxpayer/branch details printed on receipts (§9 req. 3), provisions ERPNext
  taxes, and continues ZIMRA's receipt numbering (§9 req. 6).

Sequencing: every operation that allocates a receipt number or changes a
fiscal day takes a row lock on the device (``SELECT ... FOR UPDATE``), so
concurrent workers on any number of hosts are serialised by the database.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime, now_datetime

from zw_compliance.fdms.client import FdmsClient, ServerTime
from zw_compliance.fdms.clock import corrected_now, learn_offset
from zw_compliance.fdms.csr import build_csr, certificate_valid_till
from zw_compliance.fdms.signer import generate_private_key, private_key_to_pem
from zw_compliance.fdms.types import Address, Contacts, DeviceConfig, DeviceIdentity

from . import credentials
from .constants import (
	DAY,
	DAY_OPENED,
	DEVICE,
	DEVICE_ACTIVE,
	DEVICE_DISABLED,
	DEVICE_DRAFT,
	DEVICE_REGISTERED,
	DEVICE_VERIFIED,
)
from .transport import RequestsTransport

# -- connections --------------------------------------------------------------


class _ClockLearner:
	def __init__(self, device):
		self.offset = device.clock_offset_ms or 0
		self.samples = device.clock_samples or 0
		self.changed = False

	def __call__(self, observed: ServerTime):
		self.offset, self.samples = learn_offset(
			self.offset, min(self.samples, 1000), observed.server_utc, observed.round_trip_ms, datetime.now()
		)
		self.changed = True


@contextlib.contextmanager
def connect(device, *, mtls: bool = True):
	"""Yield an :class:`FdmsClient` for ``device`` (a Zimra Fiscal Device doc).

	Tests and the simulator end-to-end check inject a transport through
	``frappe.flags.zimra_transport_factory(device, mtls)``.
	"""
	learner = _ClockLearner(device)
	identity = DeviceIdentity(device.device_id, device.serial_no, device.model_name, device.model_version)
	environment = "production" if device.environment == "Production" else "test"
	base_url = device.api_url if device.environment == "Test" and device.api_url else None

	def client_for(transport):
		return FdmsClient(
			identity, transport, environment=environment, base_url=base_url, on_server_time=learner
		)

	try:
		factory = frappe.flags.get("zimra_transport_factory")
		if factory:
			yield client_for(factory(device, mtls))
		elif device.get("is_demo"):
			frappe.throw(
				_("{0} holds demo data and never connects to ZIMRA.").format(device.name),
				title=_("Demo Device"),
			)
		else:
			ca = device.server_ca_certificate if device.environment == "Test" else None
			with credentials.ca_bundle(ca) as verify:
				if mtls:
					if not device.certificate:
						frappe.throw(
							_("Fiscal device {0} is not registered with ZIMRA yet.").format(device.name)
						)
					with credentials.mtls_files(device.name, device.certificate) as cert:
						transport = RequestsTransport(cert, verify)
						try:
							yield client_for(transport)
						finally:
							transport.close()
				else:
					transport = RequestsTransport(None, verify)
					try:
						yield client_for(transport)
					finally:
						transport.close()
	finally:
		if learner.changed:
			frappe.db.set_value(
				DEVICE,
				device.name,
				{"clock_offset_ms": learner.offset, "clock_samples": learner.samples},
				update_modified=False,
			)


def device_now(device) -> datetime:
	"""Local time corrected to FDMS's clock (avoids RCPT030/RCPT031).

	``frappe.flags.zimra_clock`` replaces the clock; only the demo seeder sets
	it, to replay past days against the in-process FDMS."""
	if frappe.flags.zimra_clock:
		return frappe.flags.zimra_clock()
	return corrected_now(device.clock_offset_ms or 0)


def lock_device(name: str):
	"""Load the device with a row lock held until the transaction ends."""
	return frappe.get_doc(DEVICE, name, for_update=True)


def is_vat_payer(device) -> bool:
	return bool((device.vat_number or "").strip())


def require_active(device) -> None:
	if device.status != DEVICE_ACTIVE:
		frappe.throw(
			_("ZIMRA fiscal device {0} is {1}. Complete its setup before issuing invoices.").format(
				frappe.bold(device.device_name), device.status
			),
			title=_("Fiscal Device Not Active"),
		)
	if device.certificate_valid_till and get_datetime(device.certificate_valid_till) <= now_datetime():
		frappe.throw(
			_(
				"The ZIMRA certificate of fiscal device {0} expired on {1}. ZIMRA refuses all requests from an expired certificate; re-register the device."
			).format(frappe.bold(device.device_name), device.certificate_valid_till),
			title=_("Fiscal Device Certificate Expired"),
		)


def resolve_device(doc) -> str | None:
	"""The fiscal device that must fiscalise ``doc``.

	A device listing the invoice's POS Profile wins; otherwise the company's
	default device. Returns ``None`` when the company has no fiscal device at
	all (fiscalisation not applicable). If the company does have devices but
	none applies, the invoice is refused rather than silently left unfiscalised.
	"""
	devices = frappe.get_all(
		DEVICE,
		filters={"company": doc.company, "status": ["!=", DEVICE_DISABLED]},
		fields=["name", "is_default"],
	)
	if not devices:
		return None
	names = {d.name for d in devices}
	pos_profile = doc.get("pos_profile")
	if pos_profile:
		for row in frappe.get_all(
			"Zimra Device POS Profile",
			filters={"pos_profile": pos_profile, "parenttype": DEVICE},
			pluck="parent",
		):
			if row in names:
				return row
	defaults = [d.name for d in devices if d.is_default]
	if defaults:
		return defaults[0]
	frappe.throw(
		_(
			"Company {0} has ZIMRA fiscal devices, but none is the default device or linked to POS Profile {1}. Mark a default device or add the POS Profile to a device."
		).format(frappe.bold(doc.company), frappe.bold(pos_profile or _("(none)"))),
		title=_("No Fiscal Device"),
	)


# -- lifecycle ---------------------------------------------------------------


def _format_address(address: Address | None) -> str:
	if not address:
		return ""
	# §10.3 [6]: "Displayed in this order: houseNumber, street, city, province"
	return ", ".join(p for p in (address.house_no, address.street, address.city, address.province) if p)


def _apply_branch(device, info) -> None:
	device.taxpayer_name = info.taxpayer_name
	device.taxpayer_tin = info.taxpayer_tin
	device.vat_number = info.vat_number
	device.branch_name = info.branch_name
	device.branch_address = _format_address(info.branch_address)
	contacts: Contacts | None = info.branch_contacts
	device.branch_email = contacts.email if contacts else None
	device.branch_phone = contacts.phone_no if contacts else None


def verify_taxpayer(name: str) -> dict:
	device = frappe.get_doc(DEVICE, name)
	if device.status not in (DEVICE_DRAFT, DEVICE_VERIFIED):
		frappe.throw(_("This device is already registered."))
	with connect(device, mtls=False) as client:
		info = client.verify_taxpayer_information(credentials.activation_key(name))
	_apply_branch(device, info)
	device.status = DEVICE_VERIFIED
	device.save()
	return {
		"taxpayer_name": info.taxpayer_name,
		"taxpayer_tin": info.taxpayer_tin,
		"vat_number": info.vat_number,
	}


def register(name: str) -> None:
	device = frappe.get_doc(DEVICE, name)
	if device.status != DEVICE_VERIFIED:
		frappe.throw(_("Verify the taxpayer details before registering the device."))
	key = generate_private_key()
	with connect(device, mtls=False) as client:
		certificate, _operation = client.register_device(
			credentials.activation_key(name), build_csr(key, device.serial_no, device.device_id)
		)
	credentials.store_private_key(name, private_key_to_pem(key))
	device.reload()
	device.certificate = certificate
	device.certificate_valid_till = certificate_valid_till(certificate)
	device.certificate_issued_on = now_datetime()
	device.status = DEVICE_REGISTERED
	device.save()
	configure(name)


def apply_config(device, config: DeviceConfig) -> bool:
	"""Store getConfig data on the device. Returns True when the VAT
	registration changed, which forces the open fiscal day to be closed
	before more receipts are issued (§4.4 vatNumber, §9 req. 9)."""
	vat_before = (device.vat_number or "").strip()
	_apply_branch(device, config)
	device.operating_mode = config.operating_mode
	device.day_max_hours = config.day_max_hours
	device.day_end_notification_hours = config.day_end_notification_hours
	device.qr_url = config.qr_url
	if config.certificate_valid_till:
		device.certificate_valid_till = config.certificate_valid_till
	device.last_config_sync = now_datetime()
	device.set("applicable_taxes", [])
	for tax in config.applicable_taxes:
		device.append(
			"applicable_taxes",
			{
				"tax_id": tax.tax_id,
				"tax_name": tax.tax_name,
				"tax_percent": float(tax.tax_percent or 0),
				"is_exempt": int(tax.tax_percent is None),
				"valid_from": tax.valid_from,
				"valid_till": tax.valid_till,
			},
		)
	return vat_before != (device.vat_number or "").strip()


def configure(name: str) -> None:
	"""getConfig + getStatus; activates a registered device."""
	from .taxes import provision_taxes

	device = frappe.get_doc(DEVICE, name)
	if device.status not in (DEVICE_REGISTERED, DEVICE_ACTIVE):
		frappe.throw(_("Register the device before syncing its configuration."))
	with connect(device) as client:
		config = client.get_config()
		status = client.get_status()
	device.reload()
	if config.operating_mode and config.operating_mode != "Online":
		frappe.throw(
			_(
				"ZIMRA has set this device to {0} mode. This app currently fiscalises in Online mode only."
			).format(config.operating_mode)
		)
	vat_changed = apply_config(device, config)
	if device.status == DEVICE_REGISTERED:
		if status.fiscal_day_status != "FiscalDayClosed":
			frappe.throw(
				_(
					"ZIMRA reports fiscal day {0} as {1} on this device. Close it from the software that opened it, or from the ZIMRA portal, then try again."
				).format(status.last_fiscal_day_no, status.fiscal_day_status),
				title=_("Fiscal Day Still Open"),
			)
		# §9 req. 6: continue the device's numbering. GetStatus.lastReceiptGlobalNo
		# is the receipt with the latest *date*, not the highest number (observed
		# live, zimra-fdms 0.3.1), so a number entered from the software that used
		# the device before is honoured when larger.
		device.last_fiscal_day_no = status.last_fiscal_day_no or 0
		device.last_receipt_global_no = max(
			status.last_receipt_global_no or 0, device.previous_last_receipt_global_no or 0
		)
		device.status = DEVICE_ACTIVE
	device.save()
	provision_taxes(device.company, config.applicable_taxes)
	if vat_changed and device.current_fiscal_day:
		day = frappe.get_doc(DAY, device.current_fiscal_day)
		if day.status == DAY_OPENED:
			from .day import request_close

			request_close(day.name, reason=_("VAT registration changed"))


def renew_certificate(name: str) -> None:
	"""issueCertificate (§4.3) with a fresh key; the old certificate authenticates the request."""
	device = frappe.get_doc(DEVICE, name)
	if not device.certificate:
		frappe.throw(_("The device has no certificate to renew; register it instead."))
	key = generate_private_key()
	with connect(device) as client:
		certificate, _operation = client.issue_certificate(build_csr(key, device.serial_no, device.device_id))
	credentials.store_private_key(name, private_key_to_pem(key))
	device.reload()
	device.certificate = certificate
	device.certificate_valid_till = certificate_valid_till(certificate)
	device.certificate_issued_on = now_datetime()
	device.save()


def ping(name: str) -> int | None:
	"""Report the device online (§4.13) and schedule the next report."""
	device = frappe.get_doc(DEVICE, name)
	with connect(device) as client:
		frequency = client.ping()
	frequency = frequency or device.reporting_frequency or 5
	now = now_datetime()
	frappe.db.set_value(
		DEVICE,
		name,
		{
			"last_ping": now,
			"reporting_frequency": frequency,
			"next_ping_at": add_to_date(now, minutes=frequency),
		},
		update_modified=False,
	)
	return frequency


def certificate_days_left(device) -> int | None:
	if not device.certificate_valid_till:
		return None
	return (get_datetime(device.certificate_valid_till) - now_datetime()) // timedelta(days=1)
