---
name: zimbabwe-tax-law
description: Zimbabwean VAT and fiscalisation law around zw_compliance, with each claim graded by how well it is sourced. Use for tax rates, VAT Act and statutory instrument references, input tax claims, buyer data duties, ZWG and USD invoicing, public notices, or wording legal claims in docs and posts.
---

# Zimbabwean tax law for this app

This is engineering context, not legal advice. Facts here were last checked on 2026-11-07. Tax law in Zimbabwe changes through Finance Acts, statutory instruments and ZIMRA public notices, often at the start of a year, so check for anything newer before you rely on a fact.

## How to treat a legal claim

1. Find the primary text: the Act, the statutory instrument on https://zimlii.org or https://www.veritaszim.net, or the notice on https://www.zimra.co.zw.
2. Cite it by name and number in the code or doc.
3. If you only have a secondary source, say so. Never present it as settled.
4. A legal change that affects receipts needs a **Compliance** changelog entry. See [[zimra-fdms-api]].

Grades below: **Primary** means confirmed in a primary text. **Reported** means consistent secondary reports, with the primary text not read. **Unconfirmed** means sources conflict or there is only one.

## Standing rule: no rates in code

Tax rates and tax IDs come from ZIMRA through getConfig `applicableTaxes`. Tax IDs are arbitrary, for example 513, not 1, 2, 3. A rate change reaches the app through a device's next configuration, so a code change should not be needed. Never hardcode a rate, and never copy one from the spec's examples.

| Topic | Fact | Grade |
|---|---|---|
| Standard VAT | 15.5% from 1 January 2026, raised from 15% by the Finance Act 2025. Many websites still say 15% | Reported, by several sources including a Zimbabwean law firm and EY. One page gives a conflicting Act number, so confirm it |
| Rate transition | ZIMRA Public Notice 7 of 2026, dated 9 February 2026, covers the changeover by VAT category in TaRMS. Category A: 15% for December 2025 supplies, 15.5% for January 2026 | Reported |
| Governing Act | Value Added Tax Act [Chapter 23:12] | Reported |
| Fiscal regulations | VAT (Fiscalised Recording of Taxable Transactions) Regulations, SI 104 of 2010, amended by SI 99 of 2011 | Reported |
| Device categories | Amended by SI 148 of 2016, effective 1 January 2017 | Primary, listed on ZimLII; read its text before quoting detail |
| Connection to ZIMRA | SI 153 of 2016 requires fiscal devices to connect to the ZIMRA server | Reported |
| Buyer details | Public Notice 30 of 2025: VAT-registered operators upgrade devices by 31 May 2025 so buyer details reach FDMS | Reported |
| Input tax | Claims allowed only on fiscalised tax invoices meeting section 20(4) of the VAT Act. From 1 January 2026, manual input tax schedules are not accepted and FDMS-validated invoices are required | Reported |
| Penalty | Sources describe denial of the buyer's deduction and audits. No fixed fine for a non-fiscalised invoice was found | Unconfirmed |
| E-invoicing | One third-party site says B2B e-invoicing has been mandatory since 1 January 2024 | Unconfirmed; do not repeat it |

## Why this matters in the product

- A buyer's VAT claim depends on the seller's fiscal receipt validating, so a Red or missing receipt can cost the customer money. Treat receipt defects as serious.
- `zw_compliance` sends a buyer block only when the customer has a TIN (RCPT043). TIN is 10 digits and VAT number is 9 (§4.1 field lengths). If a later notice requires more on B2C sales, that is a Compliance change.
- A receipt carries its own currency and counters are kept per currency (§6), so amounts come from the invoice's transaction currency, not the company's base currency. ZWG (Zimbabwe Gold) is not in Frappe's currency list, and the app creates it. The spec's ZWL examples are historical.
- Purchase invoices are not fiscalised; that is the supplier's duty.

## Wording public claims

- Do not call the app ZIMRA certified or approved. Neither has been established.
- Credit Shaun Chivandire's zimra-fdms SDK when describing the work.

## Sources

- [ZIMRA fiscalised devices FAQ](https://www.zimra.co.zw/16-tax/company/1023-fiscalised-electronic-registers)
- [SI 148 of 2016 on ZimLII](https://old.zimlii.org/zw/legislation/si/2016/148)
- [EY post-budget tax spotlight, November 2025](https://www.ey.com/content/dam/ey-unified-site/ey-com/en-zw/documents/ey-zw-post-budget-tax-spotlight-november-2025.pdf)
- [VAT changes for 2026, MMM law firm](https://www.mmmlawfirm.co.zw/taxes-in-zimbabwe-value-added-tax-changes-for-2026/)
- [Lookuptax: VAT rate 15.5%](https://lookuptax.com/tax-changes/zimbabwe/vat-rate-15-5)
- [Fiscal Requirements: buyer detail upgrade](https://www.fiscal-requirements.com/news/3942-zimbabwe-mandates-fiscal-device-upgrade-for-buyer-detail-transmission)
- [RTC Suite: buyer details and TaRMS integration](https://rtcsuite.com/zimbabwe-completes-fiscalisation-overhaul-mandatory-buyer-detail-transmission-and-tarms-fdms-integration-effective-31-may-2025/)
