---
name: zw-frappe-erpnext
description: Frappe and ERPNext conventions for zw_compliance across v15 and v16. Use when adding doctypes, custom fields, hooks, patches, workspaces, reports, number cards, print formats, bench commands, permissions or tests, or when something behaves differently between Frappe versions.
---

# Frappe and ERPNext in this app

## First rule

Read the framework source in `apps/frappe` and `apps/erpnext`, not blog posts or memory. Check the installed version first with `grep __version__ apps/frappe/frappe/__init__.py`. "16.0.0-dev" can be an old development snapshot and is not the same as released `version-16`.

To check another branch without installing it:

    gh api 'repos/frappe/frappe/contents/<path>?ref=version-15' -q .content | base64 -d

## Versions

The app supports v15 and later, and `requires-python` is 3.10. CI tests `version-16` only, on Python 3.14 and Node 24, and runs by hand from the Actions tab. Passing here does not prove v15.

| Area | v15 | July 2025 develop snapshot | Released v16 |
|---|---|---|---|
| Sidebar | `parent_page` only | Adds `app`, and link items `type`, `link_type`, `link_to` | Separate `Workspace Sidebar` and `Desktop Icon` records, with collapsible sections |
| Test base | `frappe.tests.utils.FrappeTestCase` on older releases | `frappe.tests.IntegrationTestCase` | Same |
| ERPNext test data | `erpnext.setup.utils.before_tests` | Same | `BootStrapTestData`, on importing `erpnext.tests.utils` |

The app ships no `Workspace Sidebar` or `Desktop Icon` records yet. Add them when the bench moves to released v16, and test on a real site.

Branch on behaviour with `try`/`except ImportError`, as `zw_compliance/testing.py` does. Do not compare version strings.

Known v16 traps already handled: `get_all` refuses SQL functions as fields, customers must use non-group Customer Groups, and POS Invoice Merge Log needs `posting_time`.

## Hooks in `zw_compliance/hooks.py`

Invoice events run in this order: `before_insert`, `validate`, `before_submit`, `on_submit`, with `before_cancel`, `on_cancel` and `on_trash` as guards. `on_cancel` is needed as well because the Merge Log cancels with `ignore_validate`.

`app_home` is required: the app switcher otherwise opens `workspaces[0]` from an unsorted query, which would be "Fiscal Operations".

## Fields and data

- Custom fields live in `zw_compliance/fiscalisation/setup/custom_fields.py`, each with `module = "ZIMRA Fiscalisation"`, so uninstall removes them. Never export them as fixtures.
- Mirror fields are `read_only`, `no_copy` and `allow_on_submit`. `guards.clear_fiscal_fields` also clears them on insert, because Amend copies `no_copy` fields.
- Fiscal records (`Zimra Fiscal Receipt`, `Zimra Fiscal Day`) are written only by code with `ignore_permissions`. Never add write permission.
- Never read `item_wise_tax_detail`: its format differs between versions. Recompute from `net_amount` and `item_tax_rate`.
- Patches go in `zw_compliance/patches/v1_0/` and in `zw_compliance/patches.txt` under `post_model_sync`. They must be safe to run on a site in any state. Frappe keeps some report properties on re-import, so a patch may be needed to change them.

## Workspaces

- A Workspace name is a global primary key and also a route, so use a distinctive name such as "Fiscal Setup", not "Setup".
- Renaming means a new folder, a patch that deletes the old record, and updating `add_to_apps_screen` and `app_home`.
- Blocks match widgets by the widget's **label**. A mismatch renders nothing and shows no error. `zw_compliance/fiscalisation/tests/test_workspace.py` guards this.
- A parent's children start collapsed until a child is open.

## Bench commands

`zw_compliance/commands.py` exposes a `commands` list, which Frappe finds through `<app>.commands`. Keep imports there light because bench loads the file on every command, and import the real work inside the function. Prefix names with `zw-`, because all apps share one namespace.

## ERPNext behaviour that matters

- A POS Invoice is fiscalised. The consolidated Sales Invoice is not (`validation.is_fiscalisable`), because it would report the sale twice.
- `disable_rounded_total` is forced, and cash discounts are refused because a receipt line cannot express them.
- Fiscalised invoices cannot be cancelled. Issue a credit note.

## Permissions

Roles are ZIMRA Manager and ZIMRA User. Whitelisted actions check roles explicitly with `frappe.only_for`. User-facing strings go through `_()` or `__()`, in business language; ZIMRA's names belong in code and payloads.

## Style and checks

Use tabs and ruff, configured in `pyproject.toml` with a line length of 110. Run `ruff check` and `ruff format`.

After changing doctypes, hooks, workspaces or patches, run `bench --site <site> migrate` and `bench --site <site> clear-cache`, then the tests.

Before writing a new test file, check whether one with that name already exists.
