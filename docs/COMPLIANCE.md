# Compliance matrix

How `zw_compliance` meets ZIMRA's *Fiscal Device Gateway API* v7.2 (online
mode). For each requirement this lists where it is enforced and which test
proves it. Section numbers (§) follow the headings in the body of the
specification. Its table of contents numbers §4.8 onwards one lower: closeDay
is §4.11 in the body and §4.10 in the contents.

Test locations:
- `fdms:` → `zw_compliance/fdms/tests/` (pytest, no Frappe)
- `int:` → `zw_compliance/fiscalisation/tests/` (Frappe, in-process FDMS)
- `e2e` → `zw_compliance/fiscalisation/tests/e2e.py` against the zimra-fdms simulator over HTTPS with mutual TLS

## 1. Device requirements (§9)

| Req. | Requirement | Enforced in | Test |
|---|---|---|---|
| 1 | Open a fiscal day before issuing receipts | `day.day_for_receipt` opens a day (locally, under the device lock) before the first receipt | int: `test_submit_signs_locally_and_sync_submits`, `test_next_receipt_after_close_opens_a_new_day` |
| 2 | getConfig before opening a day | `sync._open_day` calls getConfig, then getStatus, then openDay | int: all sync tests; e2e |
| 3 | Store and print the taxpayer and branch data from getConfig | `device.apply_config`; snapshot on each `Zimra Fiscal Receipt`; print templates read the snapshot | int: `test_device_is_active_with_zimra_configuration`, `test_print_formats_render_fiscal_fields` |
| 4 | Do not exceed `taxPayerDayMaxHrs` | `day._must_roll_over` (limit − margin) closes the day and rolls the next receipt into a new one; `schedule._maintain_device` auto-closes | int: `test_day_past_max_hours_is_closed_and_a_new_day_opened`, `test_day_is_closed_before_the_maximum_length`, `test_day_is_closed_at_the_configured_time` |
| 5 | Warn before the day end (`taxpayerDayEndNotificationHrs`) | `schedule._maintain_device`: Notification Log and realtime alert to the notify role | int: `test_notice_before_the_day_end_is_sent_once` |
| 6 | `receiptGlobalNo` sequential from 1, continuing across days | `outbox.fiscalise` under `SELECT … FOR UPDATE`; unique (device, fiscal day, globalNo) | int: `test_receipts_are_sequential_and_chained`, `test_next_receipt_after_close_opens_a_new_day` |
| 7 | Global number may restart on the first receipt of a new day | `numbering.py`, applied only when `day.open_local_day` creates a day (or an empty, unannounced day takes its first sale); per-device policy Near Limit (default) / Every Fiscal Day / Never | int: `test_numbering.TestGlobalNumbering`; unit: `TestNumberingPolicy` |
| 8 | `receiptCounter` sequential within a day, reset on a new day | `outbox.fiscalise` | int: `test_next_receipt_after_close_opens_a_new_day` |
| 9 | No VAT for non-VAT taxpayers; VAT status change mid-day forces a close | `fdms.receipt.validate_receipt` (RCPT021); `device.apply_config` returns a VAT change, which requests a close | fdms: `test_rcpt021_non_vat_payer_cannot_charge_vat` |
| 10 | openDay sent immediately, may be delayed without connection | The sync job is enqueued right after commit; Pending Open days are announced once reachable | int: `test_unreachable_fdms_queues_then_recovers_in_order` |
| 11 | Receipts sent only after the day is opened | `sync._submit_receipts` skips receipts whose day has no `open_operation_id` | int: resilience tests |
| 12 | Send only finished receipts | Enqueued `after_commit` of the submitted invoice | int: `test_rolled_back_invoice_uses_no_number` |
| 13 | Send one by one, ascending, never skipping; repeat failed submissions | `sync._submit_receipts` stops at the first failure or rejection; a new sale lifts the back-off (`sync.retry_now`) | int: `test_sequence_survives_a_flapping_connection`, `test_unreachable_fdms_queues_then_recovers_in_order`, `test_lost_answer_is_resent_without_duplicate`, `test_server_errors_are_retried` |
| 14 | Send immediately if online and nothing is waiting, else queue | Outbox plus immediate job, every-minute safety net | int: resilience tests; e2e |
| 15 | Update counters per receipt and reset on a new day | `day` counters updated in `outbox`, recomputed from signed payloads before close | fdms: counter tests; int: `test_credit_note_is_negative_and_the_day_closes` |
| 16 | Renew the certificate before expiry | `schedule._certificate_care`: warnings from `certificate_warning_days`; opt-in auto-renew; manual **Renew Certificate** | int: `test_warning_when_close_to_expiry`, `test_automatic_renewal_issues_a_new_certificate_and_key`, `test_expired_certificate_is_reported` |
| 17–21 | Offline file mode | Not implemented (online mode only); see *Known gaps* | n/a |
| 22 | No uninstall with unsent invoices | `setup.install.before_uninstall` | int: `test_uninstall_is_refused_while_receipts_are_unsent` |

## 2. Receipt validation (§8.2.1)

Every rule that does not need FDMS's own data is checked **before submit**, by
building the receipt in `validation.check_invoice` and running
`fdms.receipt.validate_receipt` on it. So ERPNext refuses, with a plain
message, any invoice ZIMRA would flag. A Red receipt cannot be withdrawn and
blocks the day close.

| Code | Colour | Rule | Enforced in | Test |
|---|---|---|---|---|
| RCPT010 | Red | Currency code | `validation` (allowed currencies), `validate_receipt` (ISO 4217) | int: `test_disallowed_currency_is_refused_before_submit`; fdms: `test_currency_must_be_iso` |
| RCPT011 | Red | Receipt counter sequential | `outbox` (locked) | int: `test_receipts_are_sequential_and_chained` |
| RCPT012 | Red | Global number sequential | `outbox` (locked), unique key | int: `test_receipts_are_sequential_and_chained`, `test_rolled_back_invoice_uses_no_number` |
| RCPT013 | Red | Invoice number unique | `invoiceNo` = ERPNext document name; unique on `Zimra Fiscal Receipt` | schema |
| RCPT014 | Yellow | Not earlier than the day opening | `outbox` date floor | int: sequencing tests |
| RCPT015 | Red | Credited/debited invoice provided | `builder.credit_debit_reference`, `validate_receipt` | fdms: `test_rcpt015_*`; int: credit and debit note tests |
| RCPT016 | Red | Lines provided | `validate_receipt` | fdms: `test_rcpt016_lines_required` |
| RCPT017 | Red | Taxes provided | `fdms.receipt.compute_taxes` always emits the tax table | fdms: `test_invoice_with_mixed_taxes` |
| RCPT018 | Red | Payments provided | `builder.build_payments` (unpaid → Settings money type), `validate_receipt` | fdms: `test_rcpt018_payments_required`; int: `test_credit_sale_reports_the_unpaid_balance` |
| RCPT019 | Red | Total = sum of lines | `builder.build_lines` reconciles to `grand_total`; `validate_receipt` | int: `test_discounted_line_reconciles_to_grand_total` |
| RCPT020 | Red | Device signature valid | `fdms.signing` + `fdms.signer` | fdms: spec vectors; int: in-process FDMS verifies; e2e |
| RCPT021 | Red | No VAT for non-VAT payers | `validate_receipt` | fdms: `test_rcpt021_*` |
| RCPT022 | Red | Sale price > 0 (< 0 on credit notes); discount < 0 | `validate_receipt`; credit notes are negative | fdms: `test_rcpt022_*`; int: `test_credit_note_is_negative_and_the_day_closes` |
| RCPT023 | Red | Quantity positive | `builder` (absolute quantity; 1 for value-only notes), `validate_receipt` | fdms: `test_rcpt023_quantity_must_be_positive` |
| RCPT024 | Red | Line total = price × quantity | `fdms.receipt.exact_price` omits the optional price when it is inexact | fdms: `test_exact_price_is_omitted_when_inexact` |
| RCPT025 | Red | Valid tax | Taxes come only from mappings provisioned from getConfig, checked for validity dates | int: `test_taxes_are_provisioned_the_erpnext_way` |
| RCPT026 | Red | Tax amount | Computed on the tax group's sum, half-even | fdms: `test_tax_is_computed_on_the_group_not_per_line`, `test_half_cent_ties_round_to_even` |
| RCPT027 | Red | Sales amount with tax | `compute_taxes` | fdms: `test_invoice_with_mixed_taxes` |
| RCPT028 | Red | Payments ≥ 0 (≤ 0 on credit notes) | `builder`, `validate_receipt` | fdms: `test_rcpt028_credit_note_payments_negative` |
| RCPT029 | Red | No reference on a plain invoice | `validate_receipt` | fdms: `test_rcpt029_*` |
| RCPT030 | Red | Not earlier than the previous receipt | `outbox` date floor (day **and** device-wide) | int: `test_receipts_are_sequential_and_chained` |
| RCPT031 | Yellow | Not future-dated | `fdms.clock` server-corrected time | fdms: `test_clock.py` |
| RCPT032 | Red | Reference must exist | Only fiscalised originals can be referenced (`builder.original_receipt`) | int: credit and debit note tests |
| RCPT033 | Red | Original within 12 months | `validation.check_against_original` | int: `test_rcpt033_original_older_than_twelve_months` |
| RCPT034 | Red | Note mandatory on credit/debit notes | Mandatory field on the invoice, `validate_receipt` | fdms: `test_rcpt034_*`; int: `test_credit_note_without_reason_is_refused` |
| RCPT035 | Red | Credits ≤ original | `check_against_original` (running balance) | int: `test_rcpt035_credit_exceeding_the_original`, `test_partial_credits_up_to_the_original_are_accepted` |
| RCPT036 | Red | Only the original's taxes | `check_against_original` | int: `test_rcpt036_tax_not_on_the_original` |
| RCPT037 | Red | Total = lines + taxes | Lines are tax-inclusive (`receiptLinesTaxInclusive = true`) | fdms: receipt tests |
| RCPT038 | Red | Total = sum of tax table sales amounts | `compute_taxes`, `validate_receipt` | fdms: `test_invoice_with_mixed_taxes` |
| RCPT039 | Red | Total = sum of payments | `builder.build_payments` (change and rounding absorbed in cash), `validate_receipt` | fdms: `test_rcpt039_*`; int: `test_cash_sale_with_change_reports_net_cash`, `test_pos_invoice_is_fiscalised_and_consolidation_is_not` |
| RCPT040 | Red | Total ≥ 0 (≤ 0 on credit notes) | `validate_receipt` | fdms: credit note tests |
| RCPT041 | Yellow | Not after the day end | Rollover before `taxPayerDayMaxHrs` | int: `test_day_past_max_hours_is_closed_and_a_new_day_opened` |
| RCPT042 | Red | Same currency as the original | `check_against_original` (ERPNext also copies the currency on returns) | code review |
| RCPT043 | Red | Buyer name and TIN when a buyer is sent | Buyer sent only when the customer has a TIN; TIN 10 digits, VAT 9 | fdms: `test_rcpt043_*`; int: `test_walk_in_sale_sends_no_buyer_and_business_sale_does`, `test_invalid_buyer_tin_is_refused` |
| RCPT047 | Red | HS code for VAT payers | `builder.hs_code_for` (item, then item group); `validate_receipt` | fdms: `test_rcpt047_*`; int: `test_hs_code_is_mandatory_for_a_vat_registered_seller`, `test_item_group_hs_code_is_inherited` |
| RCPT048 | Red | HS code length 4/8 | `validate_receipt`; `Zimra HSN Code.usable_on_receipt` filters the item link | fdms: `test_rcpt048_hs_code_length`; doctype test |

## 3. Fiscal day close (§4.11, §13.3)

| Rule | Enforced in | Test |
|---|---|---|
| No close with Grey or Red receipts | `sync._close_day` waits and explains (ZIMRA portal action needed) | int: `test_grey_or_red_receipt_blocks_the_close` |
| Counters: non-zero only, sorted, signed (§13.3.1) | `fdms.signing.counters_string`, `fiscal_day_string` | fdms: `test_fiscal_day_example_hash_and_counter_segments`, `test_balance_counters_sort_by_money_type_enum_order` |
| Credit-note counters negative (§6) | `fdms.receipt.counters_for` | fdms: `test_credit_note_is_negative_and_counts_negatively`; int: `test_credit_note_is_negative_and_the_day_closes` |
| Day date = the day's opening date | `sync._close_day` signs over `fiscal_day_date` | int: close tests |
| Refused close is reported and can be retried | `sync._poll_closing`, **Retry Close** | int: `test_refused_close_reported_as_close_failed`, `test_refused_close_reported_as_reopened_day` |
| FDMS day signature verified (§13.3.2) | `sync._poll_closing` | int: `test_credit_note_is_negative_and_the_day_closes` |

## 4. Views (§10, §11)

| View | Print format | Test |
|---|---|---|
| Receipt48 (§10.1) | *ZIMRA Receipt48* (POS Invoice), *ZIMRA Receipt48 Sales Invoice* | int: `test_print_formats_render_fiscal_fields`, POS Invoice test |
| InvoiceA4 (§10.2) | *ZIMRA Invoice A4* (Sales Invoice, POS Invoice) | int: `test_print_formats_render_fiscal_fields` |
| Z / X report (§10.4) | *ZIMRA Z Report* on Zimra Fiscal Day | int: `test_credit_note_is_negative_and_the_day_closes`, `test_print_formats_render_fiscal_fields` |
| QR and verification code (§11) | `fdms.qr`; rendered as SVG | fdms: `test_qr_examples`, `test_verification_code_grouping` |

Fields are numbered in the templates (`templates/zimra/*.html`) after the §10.3 and §10.5 field lists.

## Judgement calls

Where the specification is ambiguous, or the published Swagger and the live
service differ from the PDF, these choices were made.

1. **`taxCode` is omitted.** It is optional. The PDF includes it in the signed tax block; the zimra-fdms SDK observed it absent on live. Omitting it makes both readings produce the same string. The printed tax letter comes from the mapping's *Print Code*.
2. **Tax-inclusive lines** (`receiptLinesTaxInclusive = true`), always. ERPNext's net amounts are converted to gross per tax group.
3. **Tax per group, half-even.** RCPT026 validates the tax of the group, so tax is computed once on the group's gross total. Half-even is Frappe's default and .NET's (FDMS is an ASP.NET service). The zimra-fdms SDK rounded per line in its live run. Per line can differ by a cent on many-line invoices, and this has not been confirmed live.
4. **Credit notes are negative** (amounts, payments, counters), per RCPT022/028/040 and §6, and verified against the specification's own credit-note example hash (§13.2.1, example 2). The zimra-fdms SDK sends positive values.
5. **The QR hexadecimal is upper case**, per the §11 example.
6. **Both version headers.** The PDF names the header `DeviceModelVersionNo`; the live Swagger names it `DeviceModelVersion`. Both are sent.
7. **The buyer VAT field is `vatNumber`** (Swagger), not `buyerVATNumber`.
8. **Global number restarts are a per-device choice** (§9 req. 7). The default, Near Limit, keeps one continuous series for the audit trail and restarts only in the first new day past 90% of the limit; reaching the limit mid-day closes the day so invoicing never stops. Every Fiscal Day and Never are offered for taxpayers who need them. A restart only ever happens in a new day, and receipts are identified by fiscal day and global number together.
9. **Local opening, delayed message.** A new day is created locally on the first sale and announced as soon as possible (§9 req. 10). If the previous day is still closing, receipts may be issued in the new day before ZIMRA has acknowledged it. They are sent only after (§9 req. 11).
10. **Refused close, two forms.** §4.11 says the day "remains opened, and its status is changed to FiscalDayCloseFailed". Both readings are handled: status FiscalDayCloseFailed, or FiscalDayOpened with a closing error code.
11. **Rejected receipts stop the queue.** A receipt FDMS refuses outright (RCPT01/RCPT02, HTTP 400) is not skipped, because skipping would make every later receipt Grey. Managers are alerted and the queue resumes once it is fixed.
12. **Cash and non-trade discounts are refused.** They reduce the total without reducing VAT, which no receipt line can express. Discounts on net total are fully supported.

## Lessons from live testing

The zimra-fdms SDK was exercised against ZIMRA's live test environment. Its
changelog findings applied here:

| Finding | Applied in |
|---|---|
| closeDay is signed over the day's *opening* date, not the closing date | `sync._close_day` |
| Day state is cleared only on FiscalDayClosed, never on closeDay acceptance | `sync._poll_closing` |
| getStatus `lastReceiptGlobalNo` is the latest-*dated* receipt and can under-report | Numbering is never taken from it; `configure` takes the maximum of it and the previous software's last number |
| Same-second or earlier dates give RCPT030; after one future-dated receipt, every later one must be later still | Device-wide date floor in `outbox` |
| Red receipts block the close | Close blocked on Grey and Red, with an explanation |
| Only GET and Ping are safe to retry at transport level | `fdms.client` retries idempotent calls only; submitReceipt relies on §4.7 idempotency instead |
| getStatus returns server counters while a day is open | Cross-checked before close |
| Tax IDs are arbitrary (e.g. 513), not 1-2-3 | Mappings keyed on ZIMRA tax ID from getConfig |

## Simulator divergences

Both simulators used in testing were checked against the specification.
- The in-process FDMS (`tests/simulator.py`) follows the specification.
- The zimra-fdms 0.4.1 simulator differs in three places. The end-to-end check reports these and does not count them as failures:
  - **Receipt countersignature.** It signs `receiptGlobalNo‖serverDate‖hash`; §13.2.2 specifies `receiptDeviceSignature‖receiptID‖serverDate`. The e2e check verifies its signature over its own string, to prove the certificate handling.
  - **Day countersignature.** `fiscalDayServerSignature` is always null (§13.3.2).
  - **Credit-note counter sign.** It negates credit-note counters again, so a day that holds a spec-correct credit note is refused with `BadCertificateSignature`. An invoice-only day closes.

## Known gaps

- **Offline mode** (§4.9–4.10, §9 req. 17–21) is not implemented. The app runs in online mode and queues while disconnected.
- **Users management** (§4.14) and **getStockList** (§4.15) are not used.
- **Per-group vs per-line tax rounding** has not been confirmed against the live service (judgement call 3).
