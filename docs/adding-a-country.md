# Adding or updating a country

A country pack is one YAML file: `jurisdictions/<code>/pack.yaml`. Start from
`jurisdictions/pt/pack.yaml` (complete) or `jurisdictions/generic/pack.yaml` (minimal).

## Sections

| Section | Used by | Required? |
|---|---|---|
| `country`, `code`, `currency`, `tax_year`, `verified`, `disclaimer`, `sources` | everything | yes |
| `documents` | intake checklist, document recognition, coverage | yes |
| `transaction_categories` | bank statement summaries | yes |
| `investment_options` | wealth advisor | yes |
| `income_tax` | estimate_income_tax, estimate_net_salary, retirement_savings_benefit | for tax figures |
| `capital_income` | capital_income_tax | for tax figures |
| `property` | property_purchase_costs | optional |
| `banking`, `salary`, `retirement`, `calendar` | jurisdiction_facts | optional |
| `official_sources` | fetch_official_page allowlist (government domains are always allowed) | recommended |

Without `income_tax`, tax tools return `available: false` and agents label tax reasoning as indicative.

The document checklist drives the offline intake and is a reference for the model interviewer, which
may adapt it to the user's situation.

## Document checklist entries

```yaml
- id: payslips                 # stable id
  name: Payslips (last 3 months)
  local_name: Recibos de vencimento
  where: "Your employer's HR/payroll portal"      # where to get it
  why: "Exact gross pay, deductions..."            # shown to the user
  domains: [tax, cashflow, property]               # goals it helps with
  essential_for: [tax, cashflow]                   # asked for explicitly for these goals
  facts_question: "Your gross monthly salary..."   # what to type instead of the document
  keywords: [recibo de vencimento, payslip]        # recognition (file name and content)
```

Domains: `tax`, `debt`, `cashflow`, `wealth`, `retirement`, `property`, `protection`.

## Income tax model

The tax engine supports progressive brackets, surcharges, joint filing by income splitting,
a specific deduction for employment income (or social security, if higher), simplified-regime
coefficients for self-employment, a minimum-income floor, per-category deductions with rates and
caps, a sliding global cap, a young-people exemption and flat-rate special regimes. If a country
needs something else, add it to `plugins/tax_tools.py` driven by new pack keys, with tests.

## Checklist when updating figures

1. Change the values and the `verified` date; add the sources.
2. Update the expected values in `tests/test_tax_tools.py` from published tables or hand calculations.
3. `python -m unittest discover -s tests -t .`
