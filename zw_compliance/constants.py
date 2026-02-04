# Copyright (c) 2026, Donald Chinhuru and contributors
# For license information, please see license.txt

class ZimraConstants:
    # Receipt Types
    FISCAL_INVOICE = "FiscalInvoice"
    CREDIT_NOTE = "CreditNote"
    DEBIT_NOTE = "DebitNote"

    # Money Types
    CASH = "Cash"
    CARD = "Card"
    MOBILE_WALLET = "MobileWallet"
    COUPON = "Coupon"
    CREDIT = "Credit"
    BANK_TRANSFER = "BankTransfer"
    OTHER = "Other"

    # Fiscal Day Statuses
    DAY_CLOSED = "FiscalDayClosed"
    DAY_OPENED = "FiscalDayOpened"
    DAY_INITIATED = "FiscalDayCloseInitiated"
    DAY_FAILED = "FiscalDayCloseFailed"

    # Line Types
    LINE_SALE = "Sale"
    LINE_DISCOUNT = "Discount"

    # Print Forms
    FORM_A4 = "InvoiceA4"
    FORM_48 = "Receipt48"

    # Error Codes
    ERR_HS_CODE_MANDATORY = "RCPT047"
    ERR_HS_CODE_INVALID = "RCPT048"
    ERR_GLOBAL_NOT_SEQUENTIAL = "RCPT012"

    # system constants
    SI = "Sales Invoice"
    PI = "POS Invoice"

    # custom fields
    CUSTOM_FIELD_HSN_CODE = "custom_zimra_hsn_code"

    _DAY_STATUS_MAPPER_ = {
        "Closed": DAY_CLOSED,
        "Opened": DAY_OPENED,
        "Close Initiated": DAY_INITIATED,
        "Close Failed": DAY_FAILED
    }

    def day_status_mapper(self, status:str):
        return self._DAY_STATUS_MAPPER_.get(status, self.DAY_FAILED)