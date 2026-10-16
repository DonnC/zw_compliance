# Copyright (c) 2026, Donald Chinhuru and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document

RECEIPT_CODE_LENGTHS = (4, 8)  # spec v7.2 RCPT048


class ZimraHSNCode(Document):
	def validate(self):
		self.hsn_code = (self.hsn_code or "").strip()
		if not re.fullmatch(r"\d+", self.hsn_code):
			frappe.throw(_("An HS code contains digits only."))
		if self.description:
			self.description = self.description.upper()
		self.code_length = len(self.hsn_code)
		self.usable_on_receipt = int(self.code_length in RECEIPT_CODE_LENGTHS)
