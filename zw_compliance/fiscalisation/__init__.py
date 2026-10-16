"""ZIMRA fiscalisation for ERPNext.

The Frappe-facing service layer on top of :mod:`zw_compliance.fdms`:

* :mod:`.validation` - checks run before an invoice can be submitted
* :mod:`.builder` - maps a Sales/POS Invoice to a ZIMRA receipt
* :mod:`.outbox` - signs and records the receipt in the invoice's own transaction
* :mod:`.sync` - background job that sends receipts and day messages to ZIMRA, in order
* :mod:`.device` - device lifecycle (verify, register, configure, renew) and locking
* :mod:`.schedule` - scheduled maintenance (ping, end of day, certificates)
* :mod:`.guards` - stops fiscalised documents being cancelled, deleted or re-used
* :mod:`.taxes` - provisions ERPNext tax accounts and templates from ZIMRA's taxes
* :mod:`.print_utils` - Jinja helpers for the ZIMRA print formats
* :mod:`.api` - whitelisted actions for the desk
* :mod:`.setup` - custom fields, roles and master data

See ``docs/ARCHITECTURE.md`` for how these fit together.
"""
