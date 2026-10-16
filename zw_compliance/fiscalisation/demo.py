"""Demo data: weeks of realistic fiscal history, produced by the real flow.

Fills the ZIMRA Fiscalisation workspace and reports without a ZIMRA
connection. Nothing is shortcut: invoices are submitted through ERPNext,
signed by the outbox and synced to the in-process FDMS (``tests/simulator.py``)
with fiscal days opened and closed every day. Past days are replayed through
the clock hook ``frappe.flags.zimra_clock``.

Everything lives in its own company, *ZW Demo Traders*, on a device marked
**Demo Device**: it never connects to ZIMRA and the scheduler ignores it. ::

    bench --site <site> execute zw_compliance.fiscalisation.demo.seed
    bench --site <site> execute zw_compliance.fiscalisation.demo.wipe

``seed`` wipes any earlier demo first. ``wipe`` removes every record the demo
created; fiscal records are removed directly, because their normal
protections forbid deletion.
"""

from __future__ import annotations

import random
from datetime import datetime, time, timedelta

import frappe
from frappe.utils import add_days, getdate, now_datetime

from .constants import DAY, DAY_ACCEPTING, DEVICE, RECEIPT

COMPANY = "ZW Demo Traders"
ABBR = "ZWDT"
DEVICE_ID = 990001
PREFIX = "ZW Demo"
ZWG_RATE = 26.75  # ZWG per USD, demo only
CASH, ECOCASH = f"{PREFIX} Cash", f"{PREFIX} EcoCash"
POS_PROFILE = f"{PREFIX} Till"

CUSTOMERS = [
	# name, TIN, VAT number, billing currency
	(f"{PREFIX} Walk-in Customer", None, None, None),
	(f"{PREFIX} Mbare Hardware (Pvt) Ltd", "2000456781", "220045678", None),
	(f"{PREFIX} Chitungwiza Schools Trust", "2000789123", None, None),
	(f"{PREFIX} Bulawayo Agro Supplies", "2000334455", "220033445", None),
	(f"{PREFIX} Harare Wholesalers (ZWG)", "2000998877", "220099887", "ZWG"),
]
ITEMS = [
	# name, HS code candidates (first usable wins), price USD, ZIMRA tax: standard / zero / exempt
	("Laptop 14 inch", ["84713000"], 520.0, "standard"),
	("Smartphone", ["85171300"], 185.0, "standard"),
	("Cement 50kg", ["25232900"], 11.5, "standard"),
	("Cooking Oil 2L", ["15079010"], 4.2, "standard"),
	("Maize Meal 10kg", ["11022029", "11022000"], 7.8, "zero"),
	("Fresh Milk 2L", ["04012000", "04012010"], 2.6, "zero"),
	("Medicines Pack", ["30049000", "30049090", "30049010"], 15.0, "exempt"),
]


# -- seeding ---------------------------------------------------------------------


class _Clock:
	def __init__(self):
		self.now = now_datetime()

	def __call__(self) -> datetime:
		return self.now


def seed(days: int = 21, seed_value: int = 2026) -> None:
	"""Create ``days`` days of fiscal history ending today."""
	from .tests.simulator import FakeFdms

	if frappe.db.exists("Company", COMPANY):
		wipe()
	rng = random.Random(seed_value)
	clock = _Clock()
	fdms = FakeFdms(
		taxpayer_name="ZW Demo Traders (Private) Limited",
		taxpayer_tin="2000123450",
		branch_name="Harare Head Office",
		vat_number="220012345",
	)
	fdms.clock = clock
	flags = {"zimra_transport_factory": fdms.factory(), "zimra_clock": clock, "zimra_sync_inline": True}
	frappe.flags.update(flags)
	try:
		clock.now = _at(add_days(now_datetime(), -days), time(7, 30))
		_masters()
		device = _device()
		_pos_profile()
		print(f"Demo: {device} registered; replaying {days} days...")
		today = getdate(now_datetime())
		for offset in range(days, -1, -1):
			date = getdate(add_days(today, -offset))
			if date.weekday() == 6:
				continue  # closed on Sundays
			_trade_day(device, fdms, clock, rng, date, is_today=offset == 0)
			frappe.db.commit()
		print(f"Demo: {frappe.db.count(RECEIPT, {'company': COMPANY})} fiscal receipts created.")
		print("Open the ZIMRA Fiscalisation workspace and pick company 'ZW Demo Traders'.")
	except Exception:
		frappe.db.rollback()
		raise
	finally:
		for key in flags:
			frappe.flags.pop(key, None)


def _at(day, at: time) -> datetime:
	return datetime.combine(getdate(day), at)


def _sync(device: str) -> None:
	from .sync import _run

	_run(device)


def _masters() -> None:
	from .tests.fixtures import ensure_fiscal_year

	ensure_fiscal_year()
	frappe.get_doc(
		{
			"doctype": "Company",
			"company_name": COMPANY,
			"abbr": ABBR,
			"default_currency": "USD",
			"country": "Zimbabwe",
			"chart_of_accounts": "Standard",
			"tax_id": "2000123450",
		}
	).insert(ignore_permissions=True)
	receivable = frappe.get_doc(
		{
			"doctype": "Account",
			"account_name": "Debtors ZWG",
			"company": COMPANY,
			"parent_account": f"Accounts Receivable - {ABBR}",
			"account_type": "Receivable",
			"account_currency": "ZWG",
		}
	).insert(ignore_permissions=True)
	for name, tin, vat, currency in CUSTOMERS:
		customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": name,
				"customer_group": "All Customer Groups",
				"territory": "All Territories",
				"customer_type": "Company" if tin else "Individual",
				"tax_id": tin,
				"zimra_vat_number": vat,
				"default_currency": currency,
			}
		)
		if currency == "ZWG":
			customer.append("accounts", {"company": COMPANY, "account": receivable.name})
		customer.insert(ignore_permissions=True)
	for mode, kind in ((CASH, "Cash"), (ECOCASH, "Phone")):
		frappe.get_doc(
			{
				"doctype": "Mode of Payment",
				"mode_of_payment": mode,
				"type": kind,
				"accounts": [{"company": COMPANY, "default_account": f"Cash - {ABBR}"}],
			}
		).insert(ignore_permissions=True)


def _hs_code(candidates: list[str]) -> str:
	for code in candidates:
		if frappe.db.get_value("Zimra HSN Code", code, "usable_on_receipt"):
			return code
	code = candidates[0]
	frappe.get_doc(
		{"doctype": "Zimra HSN Code", "hsn_code": code, "description": f"{PREFIX} placeholder"}
	).insert(ignore_permissions=True)
	return code


def _device() -> str:
	from . import device as lifecycle

	name = f"ZFD-{DEVICE_ID}"
	frappe.get_doc(
		{
			"doctype": DEVICE,
			"device_name": "Harare Head Office Till",
			"company": COMPANY,
			"environment": "Test",
			"device_id": DEVICE_ID,
			"serial_no": "ZWDEMO-0001",
			"model_name": "ERPNext",
			"model_version": "v1",
			"activation_key": "DEMO2026",
			"is_default": 1,
			"is_demo": 1,
		}
	).insert(ignore_permissions=True)
	lifecycle.verify_taxpayer(name)
	lifecycle.register(name)  # provisions accounts, templates and mappings
	templates = {
		m.zimra_tax_percent if not m.is_exempt else None: m.item_tax_template
		for m in frappe.get_all(
			"Zimra Tax Mapping",
			filters={"company": COMPANY},
			fields=["zimra_tax_percent", "is_exempt", "item_tax_template"],
		)
	}
	for item_name, hs_codes, price, kind in ITEMS:
		item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": f"{PREFIX} {item_name}",
				"item_name": item_name,
				"item_group": "All Item Groups",
				"stock_uom": "Nos",
				"is_stock_item": 0,
				"standard_rate": price,
				"zimra_hs_code": _hs_code(hs_codes),
			}
		)
		if kind == "zero":
			item.append("taxes", {"item_tax_template": templates.get(0.0)})
		elif kind == "exempt":
			item.append("taxes", {"item_tax_template": templates.get(None)})
		item.insert(ignore_permissions=True)
	return name


def _pos_profile() -> None:
	from .taxes import company_mappings

	standard = max(
		(m for m in company_mappings(COMPANY) if not m.is_exempt), key=lambda m: m.zimra_tax_percent
	)
	frappe.get_doc(
		{
			"doctype": "POS Profile",
			"name": POS_PROFILE,
			"company": COMPANY,
			"currency": "USD",
			"warehouse": f"Stores - {ABBR}",
			"customer": CUSTOMERS[0][0],
			"selling_price_list": frappe.db.get_value("Price List", {"selling": 1}),
			"write_off_account": f"Write Off - {ABBR}",
			"write_off_cost_center": f"Main - {ABBR}",
			"cost_center": f"Main - {ABBR}",
			"taxes_and_charges": standard.sales_tax_template,
			"payments": [{"mode_of_payment": CASH, "default": 1}, {"mode_of_payment": ECOCASH}],
		}
	).insert(ignore_permissions=True)


def _sale(rng: random.Random, clock: _Clock) -> frappe.model.document.Document:
	customer, _tin, _vat, currency = rng.choices(CUSTOMERS, weights=[8, 3, 2, 2, 1])[0]
	lines = rng.sample(ITEMS, k=rng.randint(1, 3))
	walk_in = customer == CUSTOMERS[0][0]
	invoice = frappe.new_doc("Sales Invoice")
	invoice.update(
		{
			"company": COMPANY,
			"customer": customer,
			"currency": currency or "USD",
			"set_posting_time": 1,
			"posting_date": clock.now.date(),
			"posting_time": clock.now.time().replace(microsecond=0),
			"due_date": add_days(clock.now.date(), 30),
		}
	)
	if currency == "ZWG":
		invoice.conversion_rate = 1 / ZWG_RATE
	if walk_in:
		invoice.update({"is_pos": 1, "pos_profile": POS_PROFILE})
	factor = ZWG_RATE if currency == "ZWG" else 1
	for item_name, _hs, price, _kind in lines:
		qty = rng.choice([1, 1, 2, 3, 5]) if price < 50 else 1
		invoice.append(
			"items", {"item_code": f"{PREFIX} {item_name}", "qty": qty, "rate": round(price * factor, 2)}
		)
	invoice.set_missing_values()
	if currency == "ZWG":
		invoice.conversion_rate = 1 / ZWG_RATE
	for row, (_name, _hs, price, _kind) in zip(invoice.items, lines, strict=True):
		row.price_list_rate = row.rate = round(price * factor, 2)
		if rng.random() < 0.15:
			row.discount_percentage = 5
			row.rate = round(row.price_list_rate * 0.95, 2)
	invoice.set("taxes", [])
	invoice.set_taxes()
	invoice.calculate_taxes_and_totals()
	if walk_in:
		mode = rng.choices([CASH, ECOCASH], weights=[3, 2])[0]
		paid = invoice.grand_total
		if mode == CASH and rng.random() < 0.6:
			paid = float(int(paid) + rng.choice([1, 5, 10]))  # change is given
		invoice.set("payments", [{"mode_of_payment": mode, "amount": paid}])
		invoice.calculate_taxes_and_totals()
	invoice.insert(ignore_permissions=True)
	invoice.submit()
	return invoice


def _credit_note(invoice, clock: _Clock, reason: str):
	from erpnext.controllers.sales_and_purchase_return import make_return_doc

	note = make_return_doc("Sales Invoice", invoice.name)
	note.update(
		{
			"set_posting_time": 1,
			"posting_date": clock.now.date(),
			"posting_time": clock.now.time().replace(microsecond=0),
			"zimra_receipt_notes": reason,
		}
	)
	note.set("items", note.items[:1])
	note.items[0].qty = -1
	note.calculate_taxes_and_totals()
	if note.is_pos:
		note.set(
			"payments", [{"mode_of_payment": note.payments[0].mode_of_payment, "amount": note.grand_total}]
		)
		note.calculate_taxes_and_totals()
	note.insert(ignore_permissions=True)
	note.submit()
	return note


def _trade_day(device: str, fdms, clock: _Clock, rng: random.Random, date, is_today: bool) -> None:
	from .day import open_local_day, request_close
	from .device import lock_device

	clock.now = _at(date, time(7, 55))
	if is_today:
		clock.now = min(clock.now, now_datetime() - timedelta(hours=3))
	locked = lock_device(device)
	current = (
		frappe.db.get_value(DAY, locked.current_fiscal_day, "status") if locked.current_fiscal_day else None
	)
	if current not in DAY_ACCEPTING:
		open_local_day(locked, clock.now)

	end = now_datetime() - timedelta(minutes=5) if is_today else _at(date, time(17, 30))
	count = rng.randint(4, 9) if is_today else rng.randint(6, 14)
	step = max((end - clock.now) / (count + 1), timedelta(minutes=1))
	sales = []
	for i in range(count):
		clock.now = clock.now + step + timedelta(seconds=rng.randint(0, 59))
		if is_today and i >= count - 2:
			fdms.faults.drop_connections = 10**6  # the line drops: the last sales wait to be sent
		sales.append(_sale(rng, clock))
		_sync(device)
		if not is_today and sales and rng.random() < 0.08:
			clock.now += timedelta(minutes=3)
			_credit_note(
				rng.choice(sales),
				clock,
				rng.choice(["Damaged goods returned", "Wrong item supplied", "Customer returned item"]),
			)
			_sync(device)
	fdms.faults.drop_connections = 0
	if is_today:
		return  # today's day stays open
	clock.now = _at(date, time(18, 0))
	request_close(frappe.db.get_value(DEVICE, device, "current_fiscal_day"))
	_sync(device)
	fdms.settle()
	_sync(device)


# -- wiping ----------------------------------------------------------------------


def _delete(doctype: str, filters: dict) -> list[str]:
	"""Delete rows and their child rows directly (no hooks)."""
	names = frappe.get_all(doctype, filters=filters, pluck="name")
	if not names:
		return []
	for field in frappe.get_meta(doctype).get_table_fields():
		frappe.db.delete(field.options, {"parent": ["in", names], "parenttype": doctype})
	frappe.db.delete(doctype, {"name": ["in", names]})
	frappe.db.delete("Version", {"ref_doctype": doctype, "docname": ["in", names]})
	frappe.db.delete("Comment", {"reference_doctype": doctype, "reference_name": ["in", names]})
	return names


def _forget_history() -> None:
	frappe.db.delete("Version", {"docname": ["like", f"{PREFIX}%"]})
	frappe.db.delete("Comment", {"reference_name": ["like", f"{PREFIX}%"]})


def wipe() -> None:
	"""Remove everything :func:`seed` created."""
	from frappe.utils.password import delete_all_passwords_for

	if not frappe.db.exists("Company", COMPANY):
		print("Demo: nothing to wipe.")
		return
	fiscal_docs = []
	fiscal_docs += _delete(RECEIPT, {"company": COMPANY})
	fiscal_docs += _delete(DAY, {"company": COMPANY})
	for device in frappe.get_all(DEVICE, filters={"company": COMPANY}, pluck="name"):
		delete_all_passwords_for(DEVICE, device)
		fiscal_docs.append(device)
	_delete(DEVICE, {"company": COMPANY})
	frappe.db.delete("Notification Log", {"document_name": ["in", fiscal_docs or [""]]})

	for doctype in ("Sales Invoice", "POS Invoice", "Payment Entry", "Journal Entry"):
		_delete(doctype, {"company": COMPANY})
	for doctype in ("GL Entry", "Payment Ledger Entry"):
		frappe.db.delete(doctype, {"company": COMPANY})

	_delete("POS Profile", {"company": COMPANY})
	_delete("Zimra Tax Mapping", {"company": COMPANY})
	items = frappe.get_all("Item", filters={"item_code": ["like", f"{PREFIX} %"]}, pluck="name")
	for item in items:
		frappe.delete_doc("Item", item, ignore_permissions=True, force=True)
	for doctype in (
		"Item Tax Template",
		"Sales Taxes and Charges Template",
		"Purchase Taxes and Charges Template",
	):
		_delete(doctype, {"company": COMPANY})
	for customer in frappe.get_all("Customer", filters={"name": ["like", f"{PREFIX} %"]}, pluck="name"):
		frappe.delete_doc("Customer", customer, ignore_permissions=True, force=True)
	for mode in (CASH, ECOCASH):
		if frappe.db.exists("Mode of Payment", mode):
			frappe.delete_doc("Mode of Payment", mode, ignore_permissions=True, force=True)
	frappe.db.delete("Zimra HSN Code", {"description": f"{PREFIX.upper()} PLACEHOLDER"})
	frappe.delete_doc("Company", COMPANY, ignore_permissions=True, force=True)
	_forget_history()
	frappe.db.commit()
	print("Demo: removed.")
