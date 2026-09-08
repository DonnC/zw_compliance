"""Frappe glue layer: wires zw_compliance.fdms (the pure-Python ZIMRA FDMS
engine) to a Frappe site - file-backed durable state, mTLS transport,
key material from Frappe File docs, and ERPNext document -> ReceiptInput
mapping. See zw_compliance.zimra.connector.ZimraConnector for the single
entry point everything else (doc hooks, whitelisted API, scheduled tasks)
calls into.
"""
