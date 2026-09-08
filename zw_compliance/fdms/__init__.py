"""Pure-Python port of zimra-fdms (https://www.zimrasdk.com/) src/core.

No Frappe imports here, deliberately - this package has no I/O side effects
beyond what is injected through the Signer / Transport / Storage protocols,
so it can be tested and reused outside of a Frappe site (see fdms/tests/).

The Frappe-specific wiring (file-backed storage, mTLS transport over
`requests`, key material loaded from Frappe File docs, mapping ERPNext
documents to ReceiptInput) lives in zw_compliance.zimra, one layer up.
"""
