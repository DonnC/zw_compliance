---
name: zw-fiscal-support
description: Diagnose zw_compliance problems on a site. Use for a rejected, Red, Grey or Yellow receipt, a stuck queue, a fiscal day that will not close, certificate or 401 errors, an invoice refused at submit, or any RCPT, DEV or FISC error code.
---

# Fiscal support

## Rules

- Investigate read-only first.
- Never edit a `Zimra Fiscal Receipt`, a `Zimra Fiscal Day` or a signed payload to clear a problem. Signed data cannot be fixed by editing, and it may be needed for a ZIMRA audit.
- Never skip or renumber a receipt.
- Never fix a stuck queue on production by SQL. Find the cause, and escalate to ZIMRA if the portal is needed.
- Never paste private keys, activation keys or a real buyer's TIN into an issue.
- A demo device (`is_demo`) never contacts ZIMRA and the scheduler ignores it. Its old Retrying or Pending Open rows are not faults.

## Where to look

| Tool | Answers |
|---|---|
| **Fiscalisation** workspace | Overall health |
| **Sync Exceptions** report | Anything not accepted or flagged, with the fix |
| **Fiscalisation Reconciliation** report | Invoices with a missing or different receipt |
| **Device Status** report | Certificate days left, day age, last sync, queue depth |
| `zw_compliance/fdms/errors.py` | Every code, with a plain fix |
| Error Log doctype | Exceptions from background jobs |

Read-only state check, using the real column names:

    select name, status, last_sync, certificate_valid_till from `tabZimra Fiscal Device`;
    select fiscal_device, fiscal_day_no, status, last_error from `tabZimra Fiscal Day` where status <> 'Closed';
    select name, sync_status, attempts, last_error_code, worst_colour from `tabZimra Fiscal Receipt`
      where sync_status <> 'Submitted' order by fiscal_day_no, receipt_global_no limit 20;

Run them with `bench --site <site> mariadb`.

## Common cases

**Queue not moving, receipts Retrying.** Read `last_error_code` first. An empty code means ZIMRA is unreachable: this is the designed behaviour and sales continue. Check the network and that the scheduler and workers are running (`bench doctor`). A new sale lifts the back-off for connection failures, and **Send Now** lifts it for any Retrying receipt. A code means ZIMRA answered and is refusing the receipt, so retrying alone will not help: for example RCPT01 means the day is closed or closing at ZIMRA, and HTTP 401 is a certificate problem.

**Queue stopped at a Rejected receipt.** Only RCPT02 or HTTP 400 lands here. Everything behind it waits. RCPT02 means an invalid structure, which is likely a bug: inspect `payload` and fix the code. The receipt must still be resolved in order.

**Grey receipt.** A previous receipt is missing, so look for a gap in the sequence. It usually clears once the missing one arrives. If it cannot, the day needs the ZIMRA portal.

**Red receipt.** Permanent, and blocks automatic close. Look up the code, fix the cause so that it cannot recur, and tell the manager the day must be closed in the ZIMRA portal or by a ZIMRA officer (§2.3). If validation should have caught it, that is a bug: add the rule to `validate_receipt` with a test.

**Yellow receipt.** Minor. The day still closes. RCPT014, RCPT031 and RCPT041 usually mean a clock problem or a day past its length.

**Day will not close.** In order, check: unsent or Rejected receipts in that day; Grey or Red receipts; earlier days still closing; counters that differ from the recomputation, which is logged; and `closing_error_code`, where `CountersMismatch`, `MissingReceipts`, `ReceiptsWithValidationErrors` and `BadCertificateSignature` are the §5.4.9 values. **Retry Close** can repeat a close without limit (§2.3).

**HTTP 401.** The certificate was revoked, expired or not issued to this device (§7.3). If close to expiry, use **Renew Certificate**. An expired certificate cannot renew itself over mutual TLS, so the device needs a new one from ZIMRA. How queued receipts behave after that has not been confirmed live; test it before promising an outcome.

**DEV01, DEV04, DEV05, DEV06.** Device inactive, model blacklisted, taxpayer inactive, or model and version not registered. These are ZIMRA account matters, not code.

**Invoice refused at submit.** This is usually deliberate: the app refuses what ZIMRA would mark Red. Read the message and fix the master data, such as the HS code, tax mapping, buyer TIN, credit note reason or allowed currency.

**Wrong VAT rate.** Do not edit code. Rates come from getConfig, so run the device's configuration and check the tax mappings. See [[zimbabwe-tax-law]].
