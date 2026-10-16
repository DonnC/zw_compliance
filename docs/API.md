# Remote fiscalisation API (v1)

For systems that sell outside the ERPNext desk, such as a web shop, a mobile
app or a third-party till, and need ZIMRA fiscal receipts. A sale sent here
becomes an ordinary submitted **Sales Invoice**. It is fiscalised exactly
like one entered in the desk, so accounting, stock, reports and permissions
behave the same.

## Conventions

**Base path:** `/api/method/zw_compliance.api.v1.fiscalisation.<endpoint>`

**Authentication:** any Frappe method works. For servers, use an API key of
a dedicated integration user:

```
Authorization: token <api_key>:<api_secret>
```

The integration user needs:
- permission to create, submit and read **Sales Invoice** for the company (e.g. *Accounts User*);
- read on **Zimra Fiscal Receipt** (*Accounts User* has it).

Give it a restricted user permission on Company if it sells for only one.

**Requests:** JSON body (`Content-Type: application/json`) or form fields.
Lists may be sent as JSON strings in form posts. Writes accept `POST` only;
reads accept `GET` or `POST`.

**Responses:** Frappe wraps every method result in `message`. Inside it,
every endpoint answers in one of two shapes:

```json
{"message": {"ok": true, "data": { … }}}
```

```json
{"message": {"ok": false, "error": {"code": "VALIDATION_ERROR", "message": "…", "zimra_code": "RCPT047"}}}
```

`code` is stable and meant for your program to branch on. `message` is for
people. `zimra_code` is present when the error is a ZIMRA rule.

| HTTP | `code` | When |
|---|---|---|
| 422 | `MISSING_FIELD` | A required field is absent or empty |
| 422 | `INVALID_FIELD` | A field has the wrong shape or value |
| 422 | `NOT_FISCALISED` | Credit note requested against an invoice that has no fiscal receipt |
| 422 | `VALIDATION_ERROR` | ERPNext or ZIMRA rules refuse the document (see `zimra_code`) |
| 403 | `PERMISSION_DENIED` | The user may not do this |
| 404 | `NOT_FOUND` | No such invoice or reference |
| 409 | `DUPLICATE` | A unique value already exists |
| 500 | `INTERNAL_ERROR` | Unexpected. Logged in Error Log; safe to retry with the same `client_reference` |

On any error the request's own changes are undone. Nothing is half-saved.

**Idempotency:** every write takes a `client_reference`, your own unique ID
for the sale (≤ 140 characters). Sending the same reference again returns
the document already created, with `"replayed": true`, instead of creating
a second one. After a timeout, always retry with the same reference.

## The receipt object

Returned by every endpoint that deals with a sale:

```json
{
  "invoice": "ACC-SINV-2026-00412",
  "client_reference": "WEB-10031",
  "status": "Submitted",
  "currency": "USD",
  "total": 230.0,
  "replayed": false,
  "receipt_type": "FiscalInvoice",
  "fiscal_day_no": 87,
  "receipt_counter": 14,
  "receipt_global_no": 5121,
  "receipt_date": "2026-10-16T10:42:07",
  "verification_code": "3B10-0CE2-9533-6A61",
  "qr_code": "https://fdmstest.zimra.co.zw/00000123451610202600000051213B100CE295336A61",
  "zimra_receipt_id": "9871234",
  "validation_errors": [],
  "print_url": "/api/method/frappe.utils.print_format.download_pdf?doctype=Sales+Invoice&name=ACC-SINV-2026-00412&format=ZIMRA+Invoice+A4"
}
```

| Field | Meaning |
|---|---|
| `status` | `Queued` (signed, on its way to ZIMRA), `Retrying` (ZIMRA unreachable; automatic), `Submitted` (accepted), `Rejected` (refused; needs attention in ERPNext) |
| `receipt_type` | `FiscalInvoice`, `CreditNote` or `DebitNote` |
| `total` | Invoice total in `currency`; negative for credit notes |
| `verification_code`, `qr_code` | Print both: the QR encodes `qr_code` and the customer can verify the receipt with ZIMRA. They are final as soon as the receipt exists, even while `Queued` |
| `zimra_receipt_id` | ZIMRA's ID, once `Submitted` |
| `validation_errors` | ZIMRA's findings: `[{"code": "RCPT031", "colour": "Yellow", "message": "…"}]` |
| `print_url` | PDF in ZIMRA's layout (needs the same authentication) |

The fields after `replayed` are present once the invoice has a fiscal receipt.
Through this API that is always the case, unless the company has no fiscal
device.

## Endpoints

### `fiscalise_invoice`, POST

Create, submit and fiscalise a sale.

| Field | Type | |
|---|---|---|
| `client_reference` | string | **required**, idempotency key |
| `items` | list | **required**: `[{"item_code", "qty", "rate"?, "item_tax_template"?}]`. Without `rate` the price list applies |
| `customer` | string | Customer name. Defaults to the POS Profile's customer |
| `company` | string | Defaults to the POS Profile's company, then the user's default |
| `pos_profile` | string | Required when `payments` are sent; selects warehouse, price list and fiscal device |
| `currency` | string | Defaults to the customer's billing currency, then the company currency. Must be allowed in Zimra Settings |
| `payments` | list | `[{"mode_of_payment", "amount"}]`. Omit for a sale on account (reported to ZIMRA as unpaid) |
| `taxes_and_charges` | string | Sales Taxes and Charges Template. Default: the company's default (standard-rated VAT) |
| `notes` | string | Printed on the receipt |

```http
POST /api/method/zw_compliance.api.v1.fiscalisation.fiscalise_invoice
Content-Type: application/json
Authorization: token 5f0c…:a91e…

{
  "client_reference": "WEB-10031",
  "pos_profile": "Online Store",
  "customer": "Tafadzwa Moyo",
  "items": [{"item_code": "LAPTOP-14", "qty": 1, "rate": 200}],
  "payments": [{"mode_of_payment": "EcoCash", "amount": 230}]
}
```

Response: the [receipt object](#the-receipt-object).

### `create_credit_note`, POST

Credit a fiscalised invoice, in full or in part.

| Field | Type | |
|---|---|---|
| `invoice` | string | **required**, the original Sales Invoice |
| `client_reference` | string | **required**, idempotency key of this credit note |
| `reason` | string | **required**, printed and sent to ZIMRA (RCPT034) |
| `items` | list | `[{"item_code", "qty"}]` with positive quantities to return; omit to return everything |
| `refund_mode_of_payment` | string | For POS sales; defaults to the original's first payment mode |

```json
{"invoice": "ACC-SINV-2026-00412", "client_reference": "WEB-10031-R1", "reason": "Damaged on delivery",
 "items": [{"item_code": "LAPTOP-14", "qty": 1}]}
```

Response: the receipt object of the credit note (`receipt_type`
`CreditNote`, negative `total`).

ZIMRA's rules are enforced and returned as `VALIDATION_ERROR`:
- same currency (RCPT042);
- original at most 12 months old (RCPT033);
- only the original's taxes (RCPT036);
- in total no more than the original (RCPT035).

### `get_receipt`, GET or POST

`invoice` **or** `client_reference`. Returns the receipt object. Poll it when
the first answer was `Queued` and you need `Submitted`. ZIMRA is normally
reached within seconds; no polling is needed just to print.

```http
GET /api/method/zw_compliance.api.v1.fiscalisation.get_receipt?client_reference=WEB-10031
```

### `get_device_status`, GET or POST

Optional `company`. Whether each fiscal device can fiscalise now, and
whether anything is waiting:

```json
{"ok": true, "data": [{
  "device": "ZFD-12345", "name": "Main Till", "company": "Acme (Pvt) Ltd", "status": "Active",
  "fiscal_day_no": 87, "fiscal_day_status": "Opened",
  "waiting_for_zimra": 0, "rejected": 0,
  "last_sync": "2026-10-16T10:42:09", "last_sync_error": null,
  "certificate_valid_till": "2027-03-01T00:00:00"
}]}
```

## Client checklist

- Generate `client_reference` before the first attempt and keep it with the sale.
- Treat `ok: false` with `INTERNAL_ERROR`, or a network error, as retryable with the same reference. Any other error needs a change to the request.
- Print `verification_code` and a QR of `qr_code` on the customer's slip, or use `print_url`.
- Do not wait for `Submitted` to hand over the receipt. ZIMRA allows the device to send it after printing (§9 req. 12, 14).
