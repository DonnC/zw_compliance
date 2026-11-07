---
name: zw-new-pillar
description: How to add a new compliance area to zw_compliance, such as payroll, PAYE, NSSA, ZIMDEF or returns. Use when planning or starting any Zimbabwean compliance feature outside fiscalisation.
---

# Adding a compliance pillar

## Start from the law

No rate, threshold, ceiling, band or due date is written from memory, in code or in this file. Before any design:

1. Open an issue that names the legal source: the Act, statutory instrument, ZIMRA public notice or NSSA notice.
2. Read the primary text, record its number and effective date, and check for anything newer. See [[zimbabwe-tax-law]].
3. Rates, bands and ceilings change, so store them as dated records that a user can update, never as constants.

## Check what exists first

Before building, read what ERPNext, HRMS and this app already provide, and any existing SDK. Reuse them where they are correct and say where you deviate.

## Layout

Each pillar follows the fiscalisation shape:

| Part | Fiscalisation example | New pillar |
|---|---|---|
| Pure rules, no Frappe | `zw_compliance/fdms/` | A new package, tested with plain pytest |
| Frappe service layer | `zw_compliance/fiscalisation/` | A new package |
| Frappe module | `zw_compliance/zimra_fiscalisation/` | A new module, added to `zw_compliance/modules.txt` |
| Shared masters | `zw_compliance/zimbabwe_compliance/` | Reuse it |

## Sidebar

Add one parent workspace with child pages, as *Fiscalisation* does, and distinctive names such as "Payroll Setup". Add it only when it has working content. PAYE is also ZIMRA, so name a pillar by what it does, not by the authority.

## Standards carried over

- Never stop the business because an outside system is down.
- Follow the checklist in [[zw-fiscal-engine]] and the conventions in [[zw-frappe-erpnext]].
- Keep a requirement-to-code-to-test matrix like `docs/COMPLIANCE.md`.
- Agree scope, deliver a phase with tests and a changelog entry, then move on.
