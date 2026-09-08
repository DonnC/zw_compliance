# Copyright (c) 2026, Donald Chinhuru and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class ZimraSettings(Document):
	def validate(self):
		self._validate_allowed_currencies()

	def _validate_allowed_currencies(self):
		if not self.allowed_currencies:
			return
		codes = [c.strip().upper() for c in self.allowed_currencies.split(",") if c.strip()]
		if not codes:
			frappe.throw(_("Allowed Currencies must list at least one ISO currency code."))
		self.allowed_currencies = ",".join(codes)
