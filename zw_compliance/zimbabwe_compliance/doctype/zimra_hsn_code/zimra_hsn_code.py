# Copyright (c) 2026, Donald Chinhuru and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class ZimraHSNCode(Document):
	def before_save(self):
		if self.description:
			self.description = self.description.upper()
