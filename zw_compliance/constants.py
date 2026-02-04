# Copyright (c) 2026, Donald Chinhuru and contributors
# For license information, please see license.txt

class ZimraConstants:
    # Receipt Types
    FISCAL_INVOICE = "FiscalInvoice"
    CREDIT_NOTE = "CreditNote"
    DEBIT_NOTE = "DebitNote"

    # Money Types
    MT_CASH = "Cash"
    MT_CARD = "Card"
    MT_MOBILE_WALLET = "MobileWallet"
    MT_COUPON = "Coupon"
    MT_CREDIT = "Credit"
    MT_BANK_TRANSFER = "BankTransfer"
    MT_OTHER = "Other"

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
    CUSTOM_FIELD_VAT_ID = "custom_zimra_vat_id"
    CUSTOM_FIELD_FISCAL_MODE = "custom_zimra_fiscalisation_mode"
    CUSTOM_FIELD_FISCAL_DATE = "custom_zimra_fiscal_date"
    CUSTOM_FIELD_IS_FISCALISED = "custom_zimra_is_fiscalised"
    CUSTOM_FIELD_VERIFICIATION_MODE = "custom_zimra_verification_code"
    CUSTOM_FIELD_GLOBAL_NO = "custom_zimra_global_no"
    CUSTOM_FIELD_RECEIPT_COUNTER = "custom_zimra_receipt_counter"
    CUSTOM_FIELD_RECEIPT_ID = "custom_zimra_receipt_id"
    CUSTOM_FIELD_QR_CODE = "custom_zimra_qr_code"
    CUSTOM_FIELD_SERVER_SIGN = "custom_zimra_server_signature"
    CUSTOM_FIELD_HTML_QR_VIEW = "custom_zimra_qr_code_view"


    _DAY_STATUS_MAPPER_ = {
        "Closed": DAY_CLOSED,
        "Opened": DAY_OPENED,
        "Close Initiated": DAY_INITIATED,
        "Close Failed": DAY_FAILED
    }

    def day_status_mapper(self, status:str):
        return self._DAY_STATUS_MAPPER_.get(status, self.DAY_FAILED)