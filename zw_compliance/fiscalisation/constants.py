"""Names shared across the fiscalisation package."""

MODULE = "ZIMRA Fiscalisation"

ZIMRA_MANAGER = "ZIMRA Manager"
ZIMRA_USER = "ZIMRA User"
MANAGER_ROLES = ("System Manager", ZIMRA_MANAGER)

DEVICE = "Zimra Fiscal Device"
DAY = "Zimra Fiscal Day"
RECEIPT = "Zimra Fiscal Receipt"
SETTINGS = "Zimra Settings"
TAX_MAPPING = "Zimra Tax Mapping"
HSN_CODE = "Zimra HSN Code"

INVOICE_DOCTYPES = ("Sales Invoice", "POS Invoice")

# Device lifecycle (Zimra Fiscal Device.status)
DEVICE_DRAFT = "Draft"
DEVICE_VERIFIED = "Taxpayer Verified"
DEVICE_REGISTERED = "Registered"
DEVICE_ACTIVE = "Active"
DEVICE_DISABLED = "Disabled"

# Fiscal day lifecycle (Zimra Fiscal Day.status)
DAY_PENDING_OPEN = "Pending Open"
DAY_OPENED = "Opened"
DAY_CLOSE_REQUESTED = "Close Requested"
DAY_CLOSE_INITIATED = "Close Initiated"
DAY_CLOSE_FAILED = "Close Failed"
DAY_CLOSED = "Closed"
# Days that still accept receipts locally.
DAY_ACCEPTING = (DAY_PENDING_OPEN, DAY_OPENED, DAY_CLOSE_FAILED)

# Receipt synchronisation (Zimra Fiscal Receipt.sync_status)
QUEUED = "Queued"
SUBMITTED = "Submitted"
RETRYING = "Retrying"
REJECTED = "Rejected"
UNSENT = (QUEUED, RETRYING)

# Invoice mirror (Sales/POS Invoice.zimra_status)
NOT_APPLICABLE = "Not Applicable"
VIA_POS = "Fiscalised via POS"

BLOCKING_COLOURS = ("Grey", "Red")  # block the day close, spec §4.11
