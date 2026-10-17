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
- In-process FDMS for tests (port of the zimra-fdms simulator), plus an end-to-end check against the zimra-fdms simulator over mutual TLS.
- App logo, shown in the desk and on the Frappe v15+ apps screen (for ZIMRA, System and Accounts managers).
- ZWG (Zimbabwe Gold) currency is created on install and migrate when missing; Frappe's own list only has ZWL. An existing ZWG record is left untouched.
- Tests for document-level discounts (on Net Total and on Grand Total): receipt total, line totals and VAT match ERPNext to the cent.

### Changed

- CI runs only when started by hand (Actions → CI → Run workflow); the automatic triggers are kept, commented out. It tests ERPNext v16 only (Python 3.14, Node 24).
- The app supports ERPNext v15 and later.
- README: logo, social preview as the main image, a screenshots section, installation from `github.com/DonnC/zw_compliance`.

### Fixed

- Workspace number cards and charts did not render: blocks referred to widgets by document name instead of label. A test now checks every block.
- Number cards and money charts used a possibly stale server-side default company; they now follow the company selected in the browser.
- Report formatters failed on rows without data, which misaligned the Fiscal Receipt Register's columns.
- The Fiscal Receipt Register and Fiscal Day Summary had total rows that added day numbers, counters and amounts in different currencies. They are removed, with a patch for existing sites.
- ERPNext v16 compatibility:
  - fresh test sites get ERPNext's base data (v15 `before_tests` hook, v16 `BootStrapTestData`);
  - customers use non-group Customer Groups;
  - the Z report no longer passes SQL functions as `get_all` fields;
  - POS Invoice Merge Log tests set `posting_time`.
- On Python 3.13+, TLS to a development CA (the Test environment's *Trusted CA* override, e.g. the zimra-fdms simulator) failed strict X.509 checks. That override now verifies chain and hostname without the strict profile; ZIMRA's servers keep strict verification.

### Removed

- Prototype modules (`zimra/`, file-based journal and queue).
- Property setters and non-ZIMRA custom fields shipped by the prototype. They stay on existing sites but are no longer installed. Legacy `custom_zimra_*` fields are migrated to the new fields by a patch.
