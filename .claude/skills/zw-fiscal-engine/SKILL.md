---
name: zw-fiscal-engine
description: How to change the zw_compliance fiscal engine safely. Use before editing the outbox, sync job, fiscal day logic, numbering, validation, the receipt builder, counters, device lifecycle or scheduler, or when reasoning about outages, ordering, locking, retries or rollback.
---

# The fiscal engine

Read `docs/ARCHITECTURE.md` first. For the rules being implemented, use [[zimra-fdms-api]].

## Priorities, in order

1. **Never break the sequence.** A missing, duplicated or reordered number is a permanent defect.
2. **Never stop sales.** Nothing ZIMRA does or fails to do may block an invoice. Outages can last for days.
3. **Online in real time.** A receipt reaches ZIMRA seconds after submit when it is reachable.
4. **The ERPNext way.** Native documents, permissions, reports and print formats.

A sale may be refused before submit only because ZIMRA would reject the receipt as Red (bad data), never because ZIMRA is unavailable. If a change adds a `frappe.throw` in the submit path, ask whether it depends on ZIMRA or on time passing. If it does, it must warn instead. Examples: `device.warn_if_certificate_expired` warns, and `day.day_for_receipt` rolls over whatever `auto_open_day` says.

## Layers

- `zw_compliance/fdms/` is pure protocol. It must never import Frappe. CI runs it in a bare virtualenv.
- `zw_compliance/fiscalisation/` maps ERPNext to the protocol.

## The transaction

`zw_compliance/fiscalisation/outbox.py` runs in `on_submit`, inside the invoice's transaction:

1. `device.lock_device` takes `SELECT ... FOR UPDATE`. This serialises numbering across workers and hosts.
2. `day.day_for_receipt` picks or opens the day, also locked.
3. The date floor applies: later than the day's opening, the day's last receipt and the device's latest receipt in any day. FDMS flags same-second and earlier dates as RCPT030. After one future-dated receipt, every later one must be later still.
4. Build, number, sign, derive the QR code, insert the receipt as Queued, update day counters and mirror fields.
5. `sync.enqueue_sync` registers with `frappe.db.after_commit`.

Rules that follow:

- Never call FDMS inside this transaction. A later rollback would leave ZIMRA holding a receipt with no invoice.
- Everything signed derives from local data, so a rollback burns no number.
- Do not hold the device lock across network calls.
- Do not call `frappe.db.commit()` in submit-path code.

## The sync job

`sync.sync_device` takes a per-device filelock, and `enqueue_sync` de-duplicates by job ID `zimra-sync::<device>`. `sync._run` then does three things in a loop: `_open_day`, `_submit_receipts`, then `_close_day` and `_poll_closing`.

| FDMS answer | Action |
|---|---|
| Accepted | Submitted, verify the signature, store colours |
| Error code RCPT02 (`NOT_RETRYABLE`), or HTTP 400 | Rejected, alert managers; the queue stops here |
| Anything else, including network errors, 401, 422 and 5xx | Retrying with back-off capped at 30 minutes (`_backoff_minutes`), alert at attempts 3 and 10; the queue stops here |

- The queue always stops at the first failure, because skipping would make every later receipt Grey.
- `sync.retry_now` lifts the back-off when a new sale arrives, since a sale suggests the line may be back.
- Lost answers are safe: resubmitting the same global number and hash returns the original receiptID (§4.7).
- Only GET and Ping retry at transport level.

## Fiscal days

`Pending Open` is created locally, and the opening goes out later (§9 req. 10). `day._must_roll_over` uses `taxPayerDayMaxHrs` minus the close margin. Past it, the day becomes Close Requested and the next sale opens a new local day. During an outage, a stack of Close Requested days is normal; they close in order later.

- A close waits until every receipt is sent. Grey or Red blocks it and tells managers to use the ZIMRA portal.
- `day.recompute_counters` rebuilds counters from signed payloads before close and must match the kept counters.
- Sign closeDay over the day's opening date, and clear the day state only on FiscalDayClosed.
- getStatus `lastReceiptGlobalNo` is the latest-dated receipt and can under-report. Never take numbering from it.

## Numbering

`zw_compliance/fiscalisation/numbering.py` restarts only in `day.open_local_day` or when an empty, unannounced day takes its first sale. Receipts are identified by device, fiscal day and global number together, because numbers can repeat across a restart. At the limit, the day closes and the next sale opens a new day at 1. No policy may stop invoicing.

## Money

Use `Decimal` with half-even rounding through `fdms.money.ROUNDING`. Never use `float` or `round()` on an amount before signing. Compute tax on the group's sum (RCPT026), not per line. `fdms.receipt.exact_price` omits the optional price when it would be inexact (RCPT024).

## Known open questions

Do not assume these. Test them against ZIMRA's test environment.

- What ZIMRA does with receipts queued and signed under an old key after a device is re-registered.
- Whether per-group tax rounding matches the live service on many-line invoices.

## Checklist for a change

1. Quote the spec section, and put it in a code comment such as `# §13.2.1`.
2. Add a test, using the spec's own example where one exists.
3. Ask what happens at the hard limit, mid-day, offline, and on rollback.
4. Update `docs/COMPLIANCE.md`, and add a **Compliance** changelog entry if what is signed, numbered or sent changes.
5. Keep behaviour identical in the fzimra Android SDK where it applies.

## Tests

- `python -m pytest` for the protocol, in seconds.
- `bench --site <site> run-tests --app zw_compliance`, with `--module` to narrow it.
- `FiscalisationTestCase` in `zw_compliance/fiscalisation/tests/fixtures.py` makes `frappe.db.commit` a no-op and provides `self.sync()` and `self.fdms.faults` (`drop_connections`, `server_errors`, `drop_after_submit`, `date_skew_ms`, `refuse_close`). Tests in a class share state, so a test that leaves a day closing needs its own class.
- `zw_compliance/fiscalisation/tests/simulator.py` follows the spec. The zimra-fdms 0.4.1 simulator differs in three known ways listed in `docs/COMPLIANCE.md`.
