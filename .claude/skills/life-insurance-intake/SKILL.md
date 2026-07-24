---
name: life-insurance-intake
description: Specialty extractor for life and disability prospects — Term, Whole, Universal, Final Expense, individual DI. Extracts proposed insured(s), owner, beneficiaries, DOB, gender, tobacco/health status, occupation/income, coverage amount, purpose of coverage, existing in-force coverage, spouse/dependents, and follow-up needs. Produces `account` (often "<Name> Life Insurance"), `contacts`, per-product `opportunities`, and `facts` for `crm-intake-writer`. Never produces guaranteed-class language or carrier-binding statements. Use whenever the input describes life or individual disability insurance needs.
---

# Life Insurance Intake

The life / individual DI specialty extractor. Feeds `crm-intake-writer`
with the right account shape and surfaces the compliance-sensitive
fields underwriters care about.

## When to use

- Life insurance fact-finder, prospect note, or call summary.
- Disability income / IDI fact-finder.
- "John needs $500k of 20-year term" type Slack messages.
- Estate-planning or buy-sell life insurance discussions tied to a
  commercial account.
- Final expense / burial insurance inquiries.

Hand off to:

- `benefits-intake` for group life under group benefits.
- `personal-lines-intake` for an existing home/auto household that's
  adding life as a cross-sell — produce both: a personal-lines payload
  for the household plus a life payload here for the life opportunity.

## Required output (subset of intake payload)

```json
{
  "account": {
    "account_name": "Joseph Washington Life Insurance",
    "account_type": "Life Insurance",
    "account_status": "Urgent",
    "primary_household_name": "Joseph Washington Household",
    "address": "1234 Oak Lane",
    "city": "Decatur",
    "state": "GA",
    "zip": "30030",
    "phone": "(404) 555-0142",
    "email": "joseph.washington@example.com",
    "tags": ["prospect", "life", "estate-planning"]
  },

  "proposed_insureds": [
    {
      "full_name": "Joseph Washington",
      "date_of_birth": "1981-04-12",
      "gender": "Male",
      "state_of_residence": "GA",
      "occupation": "Software engineer",
      "annual_income": 145000,
      "tobacco_status": "Never",
      "tobacco_last_use_date": null,
      "height_in": 70,
      "weight_lb": 185,
      "health_notes": "Hypertension controlled with medication; annual physicals current.",
      "prescriptions": ["lisinopril"],
      "us_citizen": true
    }
  ],

  "owner": {
    "same_as_insured": true,
    "name": "Joseph Washington",
    "relationship_to_insured": "Self"
  },

  "beneficiaries": [
    {"name": "Marie Washington", "relationship": "Spouse", "percent": 100, "type": "Primary"},
    {"name": "Washington Family Trust", "relationship": "Trust", "percent": 100, "type": "Contingent"}
  ],

  "coverage_request": {
    "product_type_preference": "Term",
    "term_length_years": 20,
    "face_amount": 750000,
    "purpose_of_coverage": "Income replacement + mortgage payoff",
    "permanent_consideration": false,
    "rider_interest": ["Waiver of Premium", "Accelerated Death Benefit"]
  },

  "existing_coverage": [
    {"carrier": "Employer Group", "type": "Group Term", "face": 290000, "premium_paid_by": "Employer", "purpose": "Workplace benefit"}
  ],

  "household": {
    "spouse": {
      "name": "Marie Washington",
      "date_of_birth": "1983-09-22",
      "tobacco_status": "Never",
      "interested_in_coverage": true,
      "coverage_target": 500000
    },
    "dependents": [
      {"name": "child 1", "age": 8, "coverage_interest": "Juvenile / Final Expense rider"},
      {"name": "child 2", "age": 5}
    ]
  },

  "preliminary_underwriting_class": {
    "tentative_class": "Standard Plus",
    "rationale": "BP medication may move from Preferred to Standard Plus depending on carrier and most recent labs.",
    "caveat": "PRELIMINARY ONLY — not a quote, not an offer, not a guarantee of class or rate. Confirmation requires application, paramed, MIB, Rx, and APS review."
  },

  "carrier_fit_candidates": [
    {"carrier": "Symetra", "why": "Generous BP table on controlled hypertension"},
    {"carrier": "Pacific Life", "why": "Competitive 20-year term up to $1M"}
  ],

  "opportunities": [
    {
      "opportunity_name": "Joseph Washington Life Insurance - 20-Year Term - 2026",
      "line_of_business": "Life",
      "stage": "Discovery",
      "product_subtype": "20-Year Term",
      "face_amount": 750000,
      "proposed_effective_date": null,
      "opportunity_type": "New Business"
    },
    {
      "opportunity_name": "Marie Washington Life Insurance - 20-Year Term - 2026",
      "line_of_business": "Life",
      "stage": "Discovery",
      "product_subtype": "20-Year Term",
      "face_amount": 500000,
      "opportunity_type": "New Business"
    }
  ],

  "missing_information": [
    {"item": "Most recent BP labs (within 12 months)", "why_needed": "Tentative class refinement"},
    {"item": "Current prescription dosage and start date", "why_needed": "Underwriting"},
    {"item": "Existing-policy in-force statement", "why_needed": "Replacement assessment + compliance"}
  ],

  "compliance_caveats": [
    "All class and rate references are PRELIMINARY only.",
    "No replacement of existing in-force coverage without full disclosure and signed comparison.",
    "Sensitive health information is `restricted` — do not echo into Slack channels."
  ],

  "facts": [
    {"entity": "Joseph Washington", "entity_type": "Contact", "fact_label": "Date of Birth", "fact_value": "1981-04-12", "sensitivity": "restricted", "source": "life fact-finder"},
    {"entity": "Joseph Washington", "entity_type": "Contact", "fact_label": "Tobacco Status", "fact_value": "Never", "sensitivity": "restricted", "source": "life fact-finder"},
    {"entity": "Joseph Washington", "entity_type": "Contact", "fact_label": "Annual Income", "fact_value": "$145,000", "sensitivity": "restricted", "source": "life fact-finder"}
  ]
}
```

## Field extraction notes

- **`account_name`** — `"<Name> Life Insurance"` is the canonical pattern.
  If a household account already exists, link via `primary_household_name`
  so retrieval can traverse.
- **`proposed_insureds`** — one row per person to be insured. Each gets a
  Contact in the CRM.
- **`owner` vs `insured`** — capture even when the same; estate-planning
  cases often have trust ownership.
- **`beneficiaries`** — capture full picture; percentages must sum to 100
  within Primary and within Contingent.
- **Health / tobacco / Rx → `sensitivity: restricted`.**
- **`preliminary_underwriting_class`** — always preliminary, never
  guaranteed.

## Compliance hard rules

1. **Never produce guaranteed-class or guaranteed-rate language.** Anything
   resembling "you will qualify for Preferred Plus" is prohibited.
   Acceptable phrasing: "preliminary indication", "tentative class",
   "subject to underwriting".
2. **No replacement statements without disclosure.** If `existing_coverage`
   is present and the new product would replace it, surface the
   replacement form requirement in `compliance_caveats`.
3. **No MIB / Rx / APS guessing.** Don't synthesize medical data; only use
   what the source provided.
4. **Restricted-by-default for health, financial, and biometric data.**
   DOB, income, height/weight, BP, prescriptions, lab values, mental
   health notes → restricted.
5. **No carrier appetite invention.** Carrier fit is a suggestion ranked
   by table-backed underwriting hints, never a guarantee. Defer to
   `carrier-appetite` for actual carrier matching.
6. **Juvenile / minor coverage** — require owner=parent/guardian and flag
   for explicit consent capture.

## Handoff

Return the JSON above to `crm-intake-writer`. The note body it produces
should mark `audience: "internal"` whenever restricted health/financial
detail is included.

## References

- `docs/agency-memory-plan.md`
- `docs/hermes-builder-spec.md` — Life workflow rules
  insurance fields (`policyLifeType`, `lifeAnnualPremium`,
  `dateOfBirth`)
- `crm-intake-writer`
- `carrier-appetite` — for grounded carrier matching
