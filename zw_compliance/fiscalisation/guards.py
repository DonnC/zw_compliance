"""Protection of fiscalised documents.

A receipt accepted by ZIMRA cannot be withdrawn: the only correction is a
credit or debit note (§4.7). So a fiscalised Sales or POS Invoice may not be
cancelled or deleted, and ZIMRA identifiers must never travel to a copy of an
invoice (Frappe copies ``no_copy`` fields when *amending*, so they are cleared
explicitly on insert).
"""

from __future__ import annotations

import frappe
from frappe import _

from .outbox import MIRROR_FIELDS


def clear_fiscal_fields(doc, method=None) -> None:
	"""``before_insert``: a new document is never fiscalised yet."""
	for field in MIRROR_FIELDS:
		if doc.get(field):
			doc.set(field, None)
	if doc.get("amended_from") and doc.get("zimra_client_reference"):
		doc.zimra_client_reference = None  # an API reference identifies one sale only


def prevent_cancel(doc, method=None) -> None:
	"""``before_cancel`` and ``on_cancel`` (the latter also runs when ERPNext
	cancels with ``ignore_validate``, e.g. from a POS Invoice Merge Log)."""
	if doc.get("zimra_fiscal_receipt"):
		frappe.throw(
			_(
				"{0} {1} has been fiscalised with ZIMRA (receipt {2}) and cannot be cancelled. Issue a credit note instead."
			).format(_(doc.doctype), frappe.bold(doc.name), doc.zimra_fiscal_receipt),
			title=_("Fiscalised Document"),
		)


def prevent_delete(doc, method=None) -> None:
	if doc.get("zimra_fiscal_receipt"):
		frappe.throw(
			_("{0} {1} has been fiscalised with ZIMRA and cannot be deleted.").format(
				_(doc.doctype), doc.name
			)
		)
