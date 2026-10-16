"""``after_install`` / ``after_migrate`` hooks."""

from __future__ import annotations

import csv
import os

import frappe
from frappe.utils import now

from ..constants import HSN_CODE, RECEIPT, SETTINGS, UNSENT, ZIMRA_MANAGER, ZIMRA_USER
from .custom_fields import make_custom_fields

ROLES = {
	ZIMRA_MANAGER: "Sets up ZIMRA fiscal devices and runs fiscal days.",
	ZIMRA_USER: "Views and prints fiscal receipts, days and reports.",
}


def after_install() -> None:
	after_migrate()


def after_migrate() -> None:
	make_roles()
	make_custom_fields()
	seed_settings()
	seed_hs_codes()


def before_uninstall() -> None:
	"""Refuse to uninstall while receipts are waiting for ZIMRA (spec §9 req. 22).
	Demo devices hold no tax records and are ignored."""
	real_devices = frappe.get_all("Zimra Fiscal Device", filters={"is_demo": 0}, pluck="name") or [""]
	unsent = frappe.db.count(
		RECEIPT, {"sync_status": ["in", list(UNSENT)], "fiscal_device": ["in", real_devices]}
	)
	rejected = frappe.db.count(RECEIPT, {"sync_status": "Rejected", "fiscal_device": ["in", real_devices]})
	if unsent or rejected:
		frappe.throw(
			frappe._(
				"{0} fiscal receipt(s) have not been accepted by ZIMRA yet ({1} rejected). "
				"ZIMRA forbids removing the fiscal application while invoices are unsent: "
				"resolve them first (ZIMRA Sync Exceptions report)."
			).format(unsent + rejected, rejected),
			title=frappe._("Unsent Fiscal Receipts"),
		)


def seed_settings() -> None:
	"""Store the documented defaults of Zimra Settings that were never saved.

	A Single's defaults live only in its meta until the document is first
	saved; storing them keeps scheduled jobs and the first desk save
	consistent with what the form shows.
	"""
	stored = {
		field
		for (field,) in frappe.db.sql(
			"select field from `tabSingles` where doctype = %s and value is not null and value != ''",
			SETTINGS,
		)
	}
	for field in frappe.get_meta(SETTINGS).fields:
		if field.default is None or field.fieldtype in ("Table", "Table MultiSelect"):
			continue
		if field.fieldname not in stored:
			frappe.db.set_single_value(SETTINGS, field.fieldname, field.default)


def make_roles() -> None:
	for role, description in ROLES.items():
		if not frappe.db.exists("Role", role):
			frappe.get_doc(
				{"doctype": "Role", "role_name": role, "desk_access": 1, "description": description}
			).insert(ignore_permissions=True)


def _csv_path() -> str:
	return frappe.get_app_path("zw_compliance", "data", "zimra_hsn_codes.csv")


def seed_hs_codes() -> None:
	"""Load ZIMRA's national HS codes; idempotent and fast (bulk insert)."""
	path = _csv_path()
	if not os.path.exists(path):
		return
	frappe.db.sql(
		f"""update `tab{HSN_CODE}` set code_length = char_length(hsn_code),
		usable_on_receipt = if(char_length(hsn_code) in (4, 8), 1, 0)
		where code_length is null or code_length = 0"""
	)
	existing = set(frappe.get_all(HSN_CODE, pluck="name"))
	with open(path, encoding="utf-8") as fh:
		rows = [
			r for r in csv.DictReader(fh) if r["hsn_code"].strip() and r["hsn_code"].strip() not in existing
		]
	if not rows:
		return
	stamp, user = now(), "Administrator"
	values = []
	for r in rows:
		code = r["hsn_code"].strip()
		values.append(
			(
				code,
				code,
				(r["description"] or "").strip().upper(),
				len(code),
				int(len(code) in (4, 8)),
				stamp,
				stamp,
				user,
				user,
				0,
			)
		)
	frappe.db.bulk_insert(
		HSN_CODE,
		[
			"name",
			"hsn_code",
			"description",
			"code_length",
			"usable_on_receipt",
			"creation",
			"modified",
			"owner",
			"modified_by",
			"docstatus",
		],
		values,
		ignore_duplicates=True,
	)
