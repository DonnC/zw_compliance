# Security policy

`zw_compliance` holds each fiscal device's private key and signs tax records
on the business's behalf. Security problems are treated as compliance
problems.

## Reporting a vulnerability

Please **do not open a public issue**. Email **donnclab@gmail.com** with
"zw_compliance security" in the subject. Include the affected version, the
steps to reproduce, and the impact as you understand it. You will get an
answer within five working days, and credit in the release notes if you
want it.

Supported: the latest release on each supported ERPNext major version (v15, v16).

## How secrets are handled

| Secret | Where | Who can see it |
|---|---|---|
| Device private key (ECC P-256) | Generated on the server; Frappe Password field (encrypted with the site's `encryption_key`) | Nobody in the UI. Permission level 1, granted to ZIMRA Manager and System Manager, who see only that it is set |
| Activation key | Password field, permission level 1 | As above |
| Device certificate | Long Text, permission level 1 | ZIMRA Manager, System Manager |

- During a request to ZIMRA, the key and certificate are written to `sites/<site>/private/zimra/` with mode `0600` and removed when the request ends.
- ZIMRA's TLS certificate is verified against the standard CA bundle used by `requests` (certifi). The *Trusted CA* override applies to the **Test** environment only.
- FDMS's signatures on receipts and fiscal days are verified against ZIMRA's certificate chain.

Operators should:
- back up the site's `encryption_key` (`site_config.json`) securely, since without it the device key cannot be recovered and the device must be re-registered;
- keep `sites/<site>/private` off any web-served or shared volume;
- restrict ZIMRA Manager and System Manager to people who run fiscal devices;
- give remote-API integrations a dedicated user with only the permissions listed in [docs/API.md](docs/API.md).

## Integrity of fiscal records

- Fiscal receipts are read-only for every role and cannot be deleted. The signed fields are checked for changes on every save.
- Fiscalised invoices cannot be cancelled or deleted.
- Numbering happens under a database row lock, inside the invoice's transaction.

A way around any of these is a security issue. Please report it as above.
