# zw_compliance

Zimbabwe compliance for ERPNext. ZIMRA fiscalisation is built; payroll and NSSA are next.

## Skills

Load the matching skill from `.claude/skills/` before working:

| Skill | Use for |
|---|---|
| `zimra-fdms-api` | Anything signed, numbered, counted, printed or sent to ZIMRA |
| `zimbabwe-tax-law` | Rates, Acts, statutory instruments, public notices, legal claims |
| `zw-fiscal-engine` | Outbox, sync, fiscal days, numbering, validation, outages |
| `zw-frappe-erpnext` | Doctypes, hooks, patches, workspaces, reports, tests, v15 and v16 |
| `zw-fiscal-support` | Diagnosing a rejected receipt, stuck queue or failed close |
| `zw-new-pillar` | Payroll, NSSA, ZIMDEF and other new areas |

## Rules

- Never stop sales because ZIMRA is unavailable.
- Never break the receipt sequence or edit a signed record.
- `zw_compliance/fdms/` never imports Frappe.
- No tax rates in code. They come from getConfig.
- State law only from a source you can cite, and say how strong it is.
- Do not call the app ZIMRA certified.

## Workflow

- Branch from `develop` and open pull requests against it. `main` takes merges from `develop`.
- Do not add AI attribution to commits, pull requests or files.
- See `CONTRIBUTING.md`, and `docs/DEVELOPMENT.md` for tests and releases.

## Commands

    python -m pytest
    bench --site <site> run-tests --app zw_compliance
    ruff check zw_compliance && ruff format zw_compliance
    bench --site <site> zw-demo-seed --days 21
