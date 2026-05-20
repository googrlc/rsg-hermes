---
name: commercial-risk-intake
description: Specialty extractor for commercial-lines prospects and submissions — businesses with GL, BOP, Workers Comp, Commercial Auto, Property, Inland Marine, Pollution, Professional Liability, Cyber, or Umbrella exposure. Extracts business identity (legal name, DBA, FEIN, entity type, NAICS), operations, financial scale (revenue, payroll, employee/vehicle/equipment count), current carriers, renewal dates, principals, and per-LOB opportunity needs. Produces the `account`, `contacts`, `opportunities`, and `facts` blocks consumed by `crm-intake-writer`. Use whenever the input describes a business/commercial risk.
---

# Commercial Risk Intake

The commercial-lines specialty extractor. Feeds `crm-intake-writer` with a
clean account-shaped payload for any business, contractor, trucker,
professional services firm, restaurant, manufacturer, etc.

## When to use

- Underwriting summary for a business.
- Application or ACORD-style submission.
- Quote proposal from a commercial carrier.
- Email or transcript describing a business prospect.
- Loss runs / driver schedules / vehicle schedules / equipment lists.
- Carrier portal screenshots about a business.

Hand off to:

- `personal-lines-intake` if the input is actually a household.
- `benefits-intake` if it's group health / dental / vision / Medicare.
- `life-insurance-intake` if it's life / disability.

## Required output (subset of intake payload)

```json
{
  "account": {
    "account_name": "3D Pumps LLC",
    "legal_name": "3D Pumps LLC",
    "dba": null,
    "fein": "33-3725730",
    "entity_type": "LLC",
    "industry": "Construction",
    "naics": "237110",
    "sic": null,
    "address": "503 S Evelyn Pl NW",
    "city": "Atlanta",
    "state": "GA",
    "zip": "30318",
    "mailing_address": null,
    "phone": null,
    "email": null,
    "website": null,
    "operations_summary": "Bypass pumping for water/wastewater treatment plants",
    "years_in_business": null,
    "annual_revenue": 335000,
    "estimated_payroll": 80000,
    "employee_count": 2,
    "subcontractor_use": "Occasional",
    "vehicle_count": 1,
    "equipment_summary": "1 trailer-mounted bypass pump, hoses, generators",
    "account_type": "Prospect",
    "account_status": "Urgent",
    "current_carriers": [
      {"line": "General Liability", "carrier": "None", "premium": null, "renewal": null}
    ],
    "tags": ["prospect", "commercial", "contractor", "water-infrastructure"]
  },

  "contacts": [
    {
      "full_name": "Jarod Denero Mattison",
      "first_name": "Jarod",
      "last_name": "Mattison",
      "role": "Sole Member",
      "phone": "(770) 780-8848",
      "email": "jarod.mattison@gmail.com",
      "relationship_to_account": "Principal",
      "ownership_pct": 100,
      "primary_contact": true
    }
  ],

  "coverage_needs": [
    "General Liability",
    "Workers Compensation",
    "Commercial Auto",
    "Inland Marine",
    "Contractors Pollution Liability",
    "Umbrella / Excess"
  ],

  "opportunities": [
    {
      "opportunity_name": "3D Pumps LLC - General Liability - 05/19/2026",
      "line_of_business": "General Liability",
      "stage": "Quoting",
      "quote_number": "656137",
      "carrier": "Shield Commercial",
      "premium": 1533.00,
      "fees": 477.32,
      "total": 2010.32,
      "proposed_effective_date": "2026-05-19",
      "opportunity_type": "New Business",
      "package_name": "3D Pumps LLC - Commercial Insurance Submission - 05/19/2026"
    },
    {
      "opportunity_name": "3D Pumps LLC - Workers Compensation - 05/19/2026",
      "line_of_business": "Workers Compensation",
      "stage": "Discovery",
      "wc_class_code": "6319",
      "estimated_payroll": 80000,
      "proposed_effective_date": "2026-05-19",
      "opportunity_type": "New Business",
      "package_name": "3D Pumps LLC - Commercial Insurance Submission - 05/19/2026"
    }
  ],

  "underwriting_flags": [
    {"flag": "Pollution exposure from wastewater bypass pumping", "severity": "high", "lob": "Pollution"},
    {"flag": "Subcontractor use occasional — verify COIs", "severity": "medium", "lob": "GL"}
  ],

  "missing_information": [
    {"item": "Years in business", "why_needed": "WC and GL underwriting"},
    {"item": "Loss runs (3-5 years)", "why_needed": "Carrier appetite review"},
    {"item": "Driver MVRs", "why_needed": "Commercial Auto quoting"}
  ],

  "facts": [
    {"entity": "3D Pumps LLC", "entity_type": "Account", "fact_label": "EIN", "fact_value": "33-3725730", "sensitivity": "restricted", "source": "underwriting summary"},
    {"entity": "3D Pumps LLC", "entity_type": "Account", "fact_label": "Annual Revenue", "fact_value": "$335,000", "sensitivity": "standard", "source": "underwriting summary"},
    {"entity": "3D Pumps LLC", "entity_type": "Account", "fact_label": "Estimated Payroll", "fact_value": "$80,000", "sensitivity": "standard", "source": "underwriting summary"},
    {"entity": "Jarod Denero Mattison", "entity_type": "Contact", "fact_label": "Phone", "fact_value": "(770) 780-8848", "sensitivity": "standard", "source": "underwriting summary"},
    {"entity": "Jarod Denero Mattison", "entity_type": "Contact", "fact_label": "Email", "fact_value": "jarod.mattison@gmail.com", "sensitivity": "standard", "source": "underwriting summary"}
  ]
}
```

## Field extraction notes

- **`account_name`** — use legal name unless DBA is the day-to-day brand.
  When both exist, set `legal_name` and `dba` separately.
- **`fein`** — preserve formatting (`XX-XXXXXXX`). Mark restricted.
- **`entity_type`** — pick from `Sole Proprietor`, `LLC`, `Corporation`,
  `S-Corp`, `Partnership`, `Non-Profit`, `Other`. Don't invent.
- **`industry`** — pick from the canonical enum in
  `hermes-training/espocrm/field_dictionary.md` (50+ values).
- **`naics`** — 6-digit code if available; leave null if you only have a
  description.
- **`operations_summary`** — one sentence, plain English, no marketing
  language. "Bypass pumping for water/wastewater treatment plants" — yes.
  "Innovative leader in water infrastructure solutions" — no.
- **`coverage_needs`** — explicit list of LOBs to quote, even if only some
  have quote numbers yet. This drives the per-LOB opportunity expansion.
- **`current_carriers`** — for renewal targeting and remarket math.
- **`underwriting_flags`** — facts the carrier underwriter will care about.
  Each gets a severity and the LOB it affects most.

## LOB-specific add-ons

When the relevant LOB is in scope, populate these fields on the matching
opportunity rather than the account:

| LOB | Extra fields |
|-----|--------------|
| Workers Compensation | `wc_class_code`, `estimated_payroll`, `owner_payroll`, `subcontractor_use` |
| Commercial Auto | `vehicle_count`, `radius_of_operation`, `dot_number`, `mc_number`, `driver_count`, `cargo_type` |
| Commercial Property | `building_value`, `bpp_value`, `square_footage`, `construction_type`, `protection_class`, `address_of_risk` |
| Inland Marine | `equipment_schedule_value`, `transit_limit` |
| Contractors Pollution Liability | `key_exposure`, `mobile_equipment`, `disposal_practices` |
| Professional Liability | `professional_services_offered`, `revenue_by_service`, `prior_acts_date` |
| Cyber | `record_count`, `data_types`, `pos_systems`, `mfa_in_place` |
| Umbrella / Excess | `target_limit`, `underlying_limits_summary` |
| BOP | `combined_gl_property_summary`, `class_code` |

## Hard rules

1. **One opportunity per LOB.** Always. If the source lists six LOBs,
   produce six opportunity rows.
2. **No invented financials.** No revenue, payroll, vehicle counts, or
   premiums that aren't in the source.
3. **No fabricated carriers or quote numbers.** If a quote number isn't
   in the source, leave it null and set stage to `Discovery`.
4. **FEIN goes restricted.** Always.
5. **Use canonical industry/entity/LOB enums.** Don't paraphrase.
6. **Flag pollution / environmental / professional exposures explicitly.**
   These determine carrier appetite — see `carrier-appetite`.

## Handoff

Return the JSON above. `crm-intake-writer` will wrap it with classification,
note, source, and duplicate_search blocks, then route through
`crm-upsert-planner` once approved.

## References

- `docs/agency-memory-plan.md`
- `hermes-training/espocrm/field_dictionary.md` — Account / Opportunity field enums
- `crm-intake-writer`
- `carrier-appetite` — for which carriers will write the risk
