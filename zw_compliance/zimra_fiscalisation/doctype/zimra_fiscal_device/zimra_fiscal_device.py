# Copyright (c) 2026, Donald Chinhuru and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class ZimraFiscalDevice(Document):
	def validate(self):
		if self.device_id is not None and self.device_id <= 0:
			frappe.throw(_("Device ID must be a positive number."))
		for field in ("serial_no", "model_name", "model_version", "device_name"):
			if self.get(field):
				self.set(field, self.get(field).strip())
		if self.environment == "Production":
			self.api_url = None
			self.server_ca_certificate = None
		self._validate_pos_profiles()
		if self.is_default:
			frappe.db.set_value(
				"Zimra Fiscal Device",
				{"company": self.company, "is_default": 1, "name": ["!=", self.name]},
				"is_default",
				0,
			)

	def _validate_pos_profiles(self):
		seen = set()
		for row in self.pos_profiles:
			if row.pos_profile in seen:
				frappe.throw(_("POS Profile {0} is listed twice.").format(frappe.bold(row.pos_profile)))
			seen.add(row.pos_profile)
			if frappe.db.get_value("POS Profile", row.pos_profile, "company") != self.company:
				frappe.throw(
					_("POS Profile {0} belongs to another company.").format(frappe.bold(row.pos_profile))
				)
			other = frappe.db.get_value(
				"Zimra Device POS Profile",
				{
					"pos_profile": row.pos_profile,
					"parenttype": "Zimra Fiscal Device",
					"parent": ["!=", self.name],
				},
				"parent",
			)
			if other:
				frappe.throw(
					_("POS Profile {0} already uses fiscal device {1}.").format(
						frappe.bold(row.pos_profile), other
					)
				)

	def on_trash(self):
		if frappe.db.exists("Zimra Fiscal Receipt", {"fiscal_device": self.name}):
			frappe.throw(_("This device has issued fiscal receipts; disable it instead of deleting it."))
