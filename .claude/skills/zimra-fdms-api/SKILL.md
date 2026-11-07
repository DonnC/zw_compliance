---
name: zimra-fdms-api
description: ZIMRA Fiscal Device Gateway API v7.2 facts for zw_compliance. Use when reading, changing or reviewing anything that is signed, numbered, counted, printed or sent to ZIMRA FDMS, including receipts, fiscal days, counters, QR codes, certificates and RCPT, DEV, FISC or FILE error codes.
---

# ZIMRA FDMS API

## Sources, strongest first

1. The specification: `docs/Fiscal Device Gateway API v7.2 - clients.pdf`. It is git-ignored, so a clone may not have it. Extract it with `pdftotext -layout`. Quote it before you change behaviour.
2. ZIMRA's live Swagger: https://fdmsapitest.zimra.co.zw/swagger/index.html
3. `docs/COMPLIANCE.md`: requirement to code to test, judgement calls, live findings.
4. The zimra-fdms TypeScript SDK at https://github.com/DonnC/zimra-fdms by Shaun Chivandire. Its findings came from ZIMRA's live test environment, but check any unsourced claim against the spec.

Section numbers follow the headings in the PDF body. The table of contents is one lower from §4.8 onwards: closeDay is §4.11 in the body and §4.10 in the contents.

## Reading the spec

- When the spec explicitly allows something, offer it. Do not remove it because a motivating sentence elsewhere suggests a narrower intent.
- Never add an option that can stop a business issuing receipts. Ask what happens at the hard limit, mid-day and offline.
- The PDF has faults: "Error! Reference source not found" in §11, and its examples use ZWL and rates of 15% and 14.5%. Those are illustrations of format, not current rates or currencies.
- RCPT044 to RCPT046 do not exist in v7.2. The catalogue jumps from RCPT043 to RCPT047.

## Facts that break receipts when wrong

**Environments (§7.2).** Test `https://fdmsapitest.zimra.co.zw`, production `https://fdmsapi.zimra.co.zw`. Mutual TLS is required except for verifyTaxpayerInformation, registerDevice and getServerCertificate (§7.3). Synchronous calls time out at 30 seconds (§7.4). closeDay and submitFile are asynchronous (§4).

**Headers (§4).** The PDF says `DeviceModelVersionNo`; the Swagger says `DeviceModelVersion`. We send both.

**Hash (§13.1).** Concatenate with no separator, SHA-256 it, then sign. RSA signs the concatenated line and ECC signs the hash.

**Receipt string (§13.2.1), in this order:**
`deviceID`, `receiptType` upper case, `receiptCurrency` upper case, `receiptGlobalNo`, `receiptDate` as `YYYY-MM-DDTHH:mm:ss` local time, `receiptTotal` in cents, then the tax lines, then `previousReceiptHash`.

- Each tax line is `taxCode||taxPercent||taxAmount||salesAmountWithTax`, ordered by taxID then taxCode, amounts in cents.
- `taxPercent` always has two decimals: 15 becomes `15.00`, 0 becomes `0.00`. Exempt has no percent and contributes an empty value, which is different from `0.00`.
- `previousReceiptHash` is left out for the first receipt of a fiscal day.

**Fiscal day string (§13.3.1):** `deviceID`, `fiscalDayNo`, `fiscalDayDate`, then the counters. `fiscalDayDate` is the date the day **opened**, not the date it closed.

- Only non-zero counters are included, in cents, text in upper case.
- Order: counter type, currency, then taxID or money type.
- A counter with no tax percent has an empty value in that position.

**FDMS countersignatures.** Receipt: `receiptDeviceSignature||receiptID||serverDate` (§13.2.2). Day: `deviceID||fiscalDayNo||fiscalDayDate||fiscalDayUpdated||reconciliationMode||fiscalDayCounters||fiscalDayDeviceSignature` (§13.3.2). `reconciliationMode` is AUTO or MANUAL, and the device signature is left out when the day was closed manually. Verification lives in `zw_compliance/fdms/verify.py`.

**QR (§11).** URL is `qrUrl` + `/` + deviceID (10 digits) + date as `ddMMyyyy` + receiptGlobalNo (10 digits) + receiptQrData. `receiptQrData` is the first 16 characters of the MD5 hash of the device signature in hexadecimal format. The verification code printed on the receipt is those 16 characters in groups of four, for example `4C8B-E276-6333-0417`.

**Keys (§12).** Generate them on the device, store them encrypted, and never let the private key leave. The CSR common name is `ZIMRA-<serial>-<deviceID>`.

## Numbering (§9 req. 6, 7, 8)

- `receiptGlobalNo` runs from 1 and continues across fiscal days. A restart is allowed only with the first receipt of a new fiscal day.
- `receiptCounter` runs from 1 within a day and resets when the day closes.

## Sign rules on credit notes (§6, RCPT022, RCPT028, RCPT040)

Credit notes are negative: sale price < 0, payments ≤ 0, total ≤ 0, and counters add the negative values. The SDK sends positive values; the spec's own example hash proves negative is correct. Debit notes are positive. Discount lines are negative on an invoice.

## Validation colours (§8.2.1)

| Colour | Meaning | Effect on close |
|---|---|---|
| Grey | Previous receipt missing, a gap in the chain. Revalidated as later receipts arrive | Blocks automatic close |
| Yellow | Minor: RCPT014, RCPT031, RCPT041 | Day still closes |
| Red | Major | Blocks automatic close |

Rules needing the previous receipt: RCPT011, RCPT012, RCPT030. The full catalogue with fix text is in `zw_compliance/fdms/errors.py`.

HS codes: RCPT047 requires one when the taxpayer is a VAT payer. RCPT048 requires 4 or 8 digits, and exactly 8 for a VAT payer when the tax percent is 0 or empty.

## Fiscal day states (§3.1)

Closed → Opened → CloseInitiated → Closed or CloseFailed. A new day may open only from Closed. In CloseInitiated, FDMS refuses new invoices and new close requests. A CloseFailed day can be corrected and resubmitted without limit. If that never succeeds, the supplier closes it in the Public Portal or a ZIMRA officer does (§2.3). A device cannot clear a Grey or Red day by itself.

## Online mode timing (§2.2, §9 req. 10 to 14)

The opening message and receipts may be delayed without a connection, but the opening must arrive before the day's receipts, and both before the close. Receipts go one by one in ascending global number, never skipped. A failed submission is fixed and repeated.

## Not implemented

Offline mode (§4.9, §4.10, §9 req. 17 to 21), users management (§4.14) and getStockList (§4.15). Do not claim support for them.
