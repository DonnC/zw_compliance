# Architecture

Technical detail for anyone modifying `zw_compliance/fdms/` or
`zw_compliance/zimra/`, or porting this engine somewhere else. If you just
want to use the app, see the [README](../README.md).

## 1. Why `fdms/` has no Frappe imports

`fdms/` is a byte-exact reimplementation of ZIMRA's FDMS protocol: canonical
signing strings, the receipt-to-receipt hash chain, fiscal-day counter
accumulation, ASN.1 DER signature encoding, the offline-queue crash-safety
protocol. A single off-by-one-field, wrong sort order, or float-rounding
difference here produces a receipt FDMS silently signs wrong (accepted, but
un-auditable) or rejects outright (RCPT010, RCPT012, RCPT030...). Keeping
this package free of Frappe means:

- It can be tested with plain `pytest`, fast, with no site/database.
- It can be checked against
  [zimra-fdms](https://www.zimrasdk.com/)'s own published conformance
  vectors (`fdms/tests/test_vectors.py`, `fdms/tests/zimra-fdms-vectors.json`)
  byte-for-byte, independent of anything ERPNext-specific.
- It stays reusable if this protocol logic is ever needed outside ERPNext.

Everything that touches Frappe - the site, the database, File attachments,
background jobs - lives one layer up, in `zw_compliance/zimra/`.

## 2. Deliberate deviations from the reference TypeScript SDK

`fdms/` is a faithful port of
[zimra-fdms](https://github.com/munashe-chivandire/zimra-fdms)'s `src/core/`
for everything ZIMRA validates byte-exact: money/cents handling, canonical
signing strings, tax rounding, counter accumulation and sort order, the hash
chain, QR format, and the offline-queue append-then-apply ordering. Three
things were deliberately *not* ported 1:1, because a mature Python crypto
library does the job more safely than hand-porting hand-rolled TypeScript:

1. **CSR building** (`fdms/csr.py`). The TS SDK hand-rolls a DER writer
   (`asn1.ts`/`csr.ts`) because browser/Android/HSM signers can't export key
   material - it has to build the `CertificationRequestInfo` bytes itself
   and only ask an abstract `Signer` for a raw signature over them. This
   Python port always holds the EC private key locally (via `cryptography`),
   so `fdms/csr.py` builds the PKCS#10 request with
   `cryptography.x509.CertificateSigningRequestBuilder` instead: same
   result (CN=`ZIMRA-{serial}-{deviceId:010d}`, secp256r1,
   ecdsa-with-SHA256, no attributes), fewer places a hand-rolled ASN.1
   encoder could be subtly wrong.
2. **Sync, not async.** The TS SDK is async because it targets browsers and
   Android. Frappe request handlers and background jobs are sync, so every
   `fdms/` method is a plain synchronous call.
3. **The `Signer` protocol still exists** (`fdms/signer.py`) even though
   `zimra/` always has direct key access, so a future HSM/cloud-KMS-backed
   signer remains possible without touching `device.py`, and tests can
   inject a fake signer (`fdms/tests/fakes.py`).

Everything else - `signing.py`, `device.py`'s counter/tax maths, `queue.py`,
`journal.py`, `qr.py`, `errors.py`, `clock.py` - is a direct, careful port.

## 3. Canonical signing strings

The single most safety-critical part of the port. Two canonical strings
exist, both pure concatenation with **no delimiters**, and both must match
FDMS's own construction byte-for-byte or the ECDSA signature simply won't
verify.

### Receipt signing string (`fdms/signing.py:receipt_signing_string`)

```
deviceID + RECEIPTTYPE(upper) + CURRENCY(upper) + receiptGlobalNo
  + receiptDate("YYYY-MM-DDTHH:mm:ss", LOCAL time, no offset)
  + receiptTotal(integer cents)
  + taxes(sorted by taxID ascending: [taxPercent("15.00" or "" if exempt)]
      + taxAmount(cents) + salesAmountWithTax(cents), concatenated)
  + previousReceiptHash(base64)   -- omitted (empty) for the first receipt
                                      of the fiscal day
```

### Fiscal-day (CloseDay) signing string (`fiscal_day_signing_string`)

```
deviceID + fiscalDayNo + fiscalDayDate("YYYY-MM-DD")
  + counters(sorted, zero-value counters excluded):
      counterType(upper) + currency(upper)
        + [taxPercent("15.00") if present]
        + [moneyType(upper) if present]
        + counterValue(cents)
```

Counters sort by a **fixed priority table**, not alphabetically:

```
SaleByTax=1, SaleTaxByTax=2, CreditNoteByTax=3, CreditNoteTaxByTax=4,
DebitNoteByTax=5, DebitNoteTaxByTax=6, BalanceByMoneyType=7
```
then by currency (ascending string), then taxID (ascending), then - for
`BalanceByMoneyType` rows - by money-type **enum order** (also fixed, not
alphabetical): `Cash=0, Card=1, MobileWallet=2, Coupon=3, Credit=4,
BankTransfer=5, Other=6`.

**`fiscalDayDate` is the date the day was *opened*, not "today".** FDMS
signs CloseDay over that date, and `GetStatus` never reports it back - so
`FiscalDevice.open_day()` persists `fdms_date(opened)` into the fiscal-day
state and `close_day()` reuses it verbatim. Recomputing it as "now" at
close time is exactly the `BadCertificateSignature` bug the reference SDK's
error catalogue documents from live testing.

### Hash chain

`previous_receipt_hash` on the *next* receipt is the **`hash`** field of the
previous receipt's `SignatureData` (base64 SHA-256 of its canonical
string) - **not** the ECDSA `signature` field. Mixing these up produces a
chain FDMS accepts syntactically but which doesn't verify against the
receipt it claims to follow.

## 4. Money: integer cents, always

`fdms/money.py` represents every amount as integer cents internally.
`amount_to_cents()` accepts a `Money`, or a whole number of major units - a
fractional `float` is a **hard error**, never silently rounded, because
`11.5` and `11.499999...` are the same IEEE-754 float and FDMS signs over
the cents. Sub-unit amounts must come in as an exact decimal string via
`money.cents("11.50")`. `zimra/receipt_builder.py` follows this rule too:
ERPNext rates are formatted to a fixed 2-decimal string and parsed with
`cents()` rather than passed through as raw floats.

Per-line tax is rounded **independently per receipt line**, then
accumulated into the tax-group total - not summed first and rounded once.
This is a one-cent-level detail that `fdms/tests/test_vectors.py`'s tax
vectors pin exactly.

## 5. The `FiscalDevice` state machine

`fdms/device.py` owns three pieces of state, always accessed together:

- `FiscalDayState` - `fiscal_day_no`, `fiscal_day_date`, `receipt_counter`
  (resets to 0 per day), `receipt_global_no` (never resets), the hash chain
  (`previous_receipt_hash`), running `counters`, and any `red_errors`
  encountered this day (which block `close_day()` unless `force=True`).
- A **pending-submit marker**, written to `Storage` *before* every
  `SubmitReceipt`/`OpenDay`/`CloseDay`/`IssueCertificate` call (these are
  the only FDMS calls that never get automatic HTTP retries - see below)
  and cleared only once the outcome is known. If a process dies mid-call,
  the marker survives; `reconcile()` (called at the top of every
  `ZimraConnector` operation) asks `GetStatus` whether the receipt actually
  arrived, resubmits the identical signed receipt if not, and never issues
  a duplicate or skips a number.
- The **durable state store** itself (`Storage` protocol - `zimra/storage.py`
  in this app), holding exactly three keys: `day-state`, `pending-submit`,
  `last-receipt-global-no`.

`open_day()`'s `receiptGlobalNo` is `max(GetStatus.lastReceiptGlobalNo,
locally-persisted-highest-issued, any explicit override)` - **never** the
server value alone. This is the concrete guard against RCPT012
(non-sequential global number): a stale or lagging `GetStatus` response can
never cause a number to be reused.

### Why not just retry on failure?

`FdmsClient` (`fdms/transport.py`) only auto-retries **idempotent** calls
(`GET`, and `Ping`) with jittered exponential backoff. `SubmitReceipt`,
`OpenDay`, `CloseDay` and `IssueCertificate` get exactly one attempt at the
transport layer - retrying a non-idempotent call blindly risks a duplicate
receipt if the first attempt actually succeeded and only the *response* was
lost. Reliability for those calls comes entirely from the pending-marker +
`reconcile()` pattern above, which distinguishes "FDMS never saw this" from
"FDMS has it, I just don't know the answer" before doing anything else.

## 6. Offline queue (`fdms/queue.py`, `fdms/journal.py`)

Covers ZIMRA's documented 72-hour offline grace window. A receipt is
**signed at sale time** (numbered, hash-chained, ECDSA-signed) regardless
of connectivity, then either submitted immediately or appended to a durable
append-only journal. Two orderings matter:

- **`journal.append()` happens before `device.apply_prepared()`.** If the
  process dies in between, the journal has an entry whose `stateAfter` is
  ahead of the device's persisted state; the next load detects that gap and
  calls `restore_state()` to catch up - so a receipt number is never reused
  or silently skipped.
- **`flush()` is strict FIFO and stops at the first failure.** The hash
  chain requires receipts to reach FDMS in the order they were signed;
  there is no reordering or skip-ahead.

In this app, `zimra/journal.py`'s `FrappeFileJournal` backs this with a
newline-delimited-JSON append-only file plus a separately, atomically
written commit-cursor file - append is O(1) and the journal file itself is
never rewritten.

## 7. Concurrency: the device lock

FDMS enforces receipt numbers strictly one-at-a-time per device. A Frappe
site can run several gunicorn/RQ workers, so "read the counter, increment
it, submit" is not atomic across processes unless something serialises it.
`zimra/lock.py` wraps every counter-issuing `ZimraConnector` operation
(`fiscalise`, `open_day`, `close_day`) in an `flock` on a file under the
same private state directory the durable storage uses - simple, needs no
extra service, and is sufficient for the deployment shape here (one ZIMRA
device per site, one host's worker processes sharing a filesystem).

## 8. QR verification data

`fdms/qr.py`: `{qrUrl}/{deviceId:010d}{ddMMyyyy}{receiptGlobalNo:010d}{md5[:16 hex]}`.
The MD5 is computed over the **decoded raw bytes of the ECDSA signature**
(the `signature` field, not the `hash` field) - the reverse of which field
feeds the hash chain (§3). `receiptDate` here uses day-month-year order,
unlike the `YYYY-MM-DD` used in signing strings - two different date
formats in the same protocol, both load-bearing.

## 9. Errors and support codes

`fdms/errors.py` carries a small catalogue (`ERROR_CATALOGUE`) of known FDMS
codes with colour (Red/Yellow/Grey), cause and fix, transcribed from the
reference SDK's own catalogue (which flags which entries were actually
observed against ZIMRA's live test environment vs. sourced from
documentation only). `Red` validation errors block `close_day()` (device
cannot close a day holding one - only ZIMRA can); `Yellow` are warnings;
`Grey` are request-level rejections. `explain_validation_error()` maps a raw
FDMS `ValidationError` onto this catalogue and is what populates the `Zimra
Validation Error` child doctype rows on a Receipt Log / Fiscal Day.

## 10. Clock correction

FDMS's `Date` response header resolves to whole seconds and represents when
the response was *sent* - roughly half a round trip before it's received.
`fdms/clock.py`'s `ServerCorrectedClock` folds every response's date into an
exponential moving average offset (`estimate = serverDate + rtt/2 -
localNow`, weight capped at 1/8 once warmed up past 8 samples), so a
terminal with a drifted local clock still produces `receiptDate` values FDMS
accepts (avoiding RCPT031, "receipt date ahead of server time").

## 11. ERPNext → ReceiptInput mapping (`zimra/receipt_builder.py`)

The one place ERPNext's data model is translated into FDMS's:

- **Tax**: each line's `Item Tax Template` (falling back to the document's
  `Taxes and Charges Template`) is looked up in `Zimra Tax Mapping` for a
  ZIMRA `taxID`/`taxPercent`/`taxCode`. Missing mappings are a hard error
  before any network call.
- **Line price**: receipts are submitted tax-inclusive
  (`receiptLinesTaxInclusive: true`). ERPNext's `item.base_rate` is net of
  tax, so the line price sent to FDMS is grossed up by the mapped tax rate
  (`net_rate * (1 + tax_percent/100)`). This assumes one tax rate per line,
  which holds for ZIMRA's standard VAT categories.
- **HS codes**: hierarchical Item → Item Group lookup via `Zimra HSN Code`,
  mandatory for VAT-registered operators (8 digits for 0%/exempt lines, 4 or
  8 for standard rates - RCPT047/RCPT048).
- **Payments**: `Sales Invoice Payment`/POS Invoice payments map through
  `Zimra Payment Mode Mapping`; an invoice with no payment rows (on
  account) is submitted as a single `Credit` payment for the full total,
  since FDMS requires `receiptPayments` to sum to `receiptTotal`.
- **Returns**: `is_return` invoices become `CreditNote` (negative grand
  total) or `DebitNote`, with a `creditDebitNote` reference built from the
  original invoice's stored ZIMRA identifiers, and RCPT033's 12-month
  return window enforced client-side before submission.
- **POS → Sales Invoice inheritance**: a Sales Invoice consolidated from an
  already-fiscalised POS Invoice inherits that receipt's identity
  (`doc_events.py`) instead of being fiscalised a second time - ZIMRA sees
  one receipt per sale, not one per ERPNext document.

## 12. Testing strategy

- **`fdms/tests/test_vectors.py`** - conformance vectors published by the
  reference SDK, replayed against this port. Canonical strings and hashes
  are compared byte-for-byte; ECDSA signatures are verified against the
  vectors' public key rather than compared (ECDSA signing is randomised).
  Run with plain `pytest`, no Frappe required.
- **`fdms/tests/test_device.py`, `test_queue.py`** - state-machine and
  crash-safety behaviour (pending markers, `reconcile()`, offline
  journalling) against `fdms/tests/fakes.py`'s in-memory fake FDMS server -
  these fakes do *not* replicate FDMS's own validation rules, only enough
  "well-behaved FDMS" behaviour to exercise the bookkeeping.
- **The zimra-fdms simulator** (`npx zimra-fdms-simulator`) - real mutual
  TLS, and replays the actual validation/error behaviour observed against
  ZIMRA's live test environment (RCPT010-031, the asynchronous CloseDay
  settling to `FiscalDayClosed` or bouncing with `BadCertificateSignature`,
  dropped connections, skewed clocks). This is the right tool for
  Frappe-level integration tests that exercise a full register → open day →
  submit → close day cycle without needing real ZIMRA test credentials, and
  is intended to be wired into this app's `bench run-tests` suite - see the
  README's Development section for how to point `Zimra Settings` at it.
