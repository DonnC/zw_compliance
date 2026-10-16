# Copyright (c) 2026, Donald Chinhuru and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document


class ZimraSettings(Document):
	def validate(self):
		codes = []
		for code in (self.allowed_currencies or "").split(","):
			code = code.strip().upper()
			if not code:
				continue
			if not re.fullmatch(r"[A-Z]{3}", code):
				frappe.throw(_("{0} is not an ISO 4217 currency code.").format(frappe.bold(code)))
			if not frappe.db.exists("Currency", code):
				frappe.throw(_("Currency {0} does not exist.").format(frappe.bold(code)))
			if code not in codes:
				codes.append(code)
		if not codes:
			frappe.throw(_("Allow at least one currency."))
		self.allowed_currencies = ",".join(codes)

		seen = set()
		for row in self.payment_mode_mapping:
			if row.mode_of_payment in seen:
				frappe.throw(
					_("Mode of Payment {0} is mapped twice.").format(frappe.bold(row.mode_of_payment))
				)
			seen.add(row.mode_of_payment)
		if self.close_margin_hours is not None and self.close_margin_hours < 0:
			frappe.throw(_("Close margin cannot be negative."))
