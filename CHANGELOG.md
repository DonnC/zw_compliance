# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/). Changes that
affect what is signed, numbered or sent to ZIMRA are listed under
**Compliance**.

## [0.1.0] - unreleased

First implementation of the ZIMRA *Fiscal Device Gateway API* v7.2 (online
mode). This replaces the earlier prototype entirely.

### Compliance

- Receipt and fiscal-day signatures built to §13.2.1 / §13.3.1. They are verified against the specification's worked examples and against vectors from ZIMRA's live test environment.
- Credit notes are negative (amounts, payments, counters) per RCPT022/028/040 and §6.
- Tax is computed per tax group (RCPT026), with half-even rounding in `Decimal`.
- Every locally checkable §8.2.1 rule is enforced before submit. This includes the credit and debit note rules RCPT015/033/034/035/036/042, buyer data (RCPT043) and HS codes (RCPT047/048).
- Receipts are numbered, chained and signed inside the invoice's database transaction under a device row lock, then sent strictly in order (§9 req. 6, 8, 12–14).
- Fiscal-day close is blocked while Grey or Red receipts exist (§4.11). Counters are recomputed from the signed payloads before signing.
- The application refuses to uninstall while receipts are unsent (§9 req. 22).
- Verification code and QR per §11; Receipt48, InvoiceA4 and Z/X report views per §10.

### Added

- Multi-device support with taxpayer verification, registration, configuration, certificate renewal (manual or automatic) and ping.
- ERPNext tax set-up from ZIMRA's tax table: accounts, sales and purchase templates (standard rate as default), Item Tax Templates, mapping.
- Automatic day opening and closing, end-of-day notices, and maximum-length rollover.
- Reports: Fiscal Day Summary, Fiscal Receipt Register, Sync Exceptions, VAT Summary, Fiscalisation Reconciliation, Device Status. Workspace with number cards and charts.
- Remote JSON API (`zw_compliance.api.v1.fiscalisation`) with an idempotent `client_reference`.
- Workspace in two parts: *Compliance health* (unfiscalised invoices, awaiting, rejected, flagged, sync errors, pending closes, fiscal-day hours left, certificate days left, attention lists) and *Sales and VAT* (fiscal sales and output VAT per currency, receipt breakdowns).
- Demo data (`zw_compliance.fiscalisation.demo.seed` / `wipe`): three weeks of history through the real flow against the built-in simulator, isolated in its own company and fully removable.
- A new sale or **Send Now** retries a connection failure immediately instead of waiting out the back-off, so the queue drains as soon as the line is back.
- In-process FDMS for tests (port of the zimra-fdms simulator), plus an end-to-end check against the zimra-fdms simulator over mutual TLS. CI on ERPNext v16 (manual trigger for now).

### Removed

- Prototype modules (`zimra/`, file-based journal and queue).
- Property setters and non-ZIMRA custom fields shipped by the prototype. They stay on existing sites but are no longer installed. Legacy `custom_zimra_*` fields are migrated to the new fields by a patch.
