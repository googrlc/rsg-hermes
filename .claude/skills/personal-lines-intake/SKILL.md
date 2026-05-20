---
name: personal-lines-intake
description: Specialty extractor for personal-lines households — Home, Auto, Umbrella, Renters, Dwelling Fire, Boat/RV/Motorcycle. Extracts the named insured + spouse/household members + drivers, vehicles, homes/properties, mortgagees/lienholders, current carrier + premium + renewal date, prior claims, and cross-sell openings (life, umbrella, valuables). Produces `account` (Household), `contacts`, per-LOB `opportunities`, and `facts` for `crm-intake-writer`. Use whenever the input describes a household, family, individual auto/home insured, or PL prospect.
---

# Personal Lines Intake

The household specialty extractor. Feeds `crm-intake-writer` with a
household-shaped payload.

## When to use

- A homeowner/renter/auto insured or prospect.
- Personal lines fact-finder, application, or quote proposal.
- Email or transcript describing a household ("Joseph and his wife
  Marie, two kids, one car, one home in Decatur…").
- Auto + Home bundle scenarios.
- Personal umbrella prospects.

Hand off to:

- `commercial-risk-intake` if the source is a business.
- `life-insurance-intake` if it's life / disability only.
- `benefits-intake` if it's group health.

## Required output (subset of intake payload)

```json
{
  "account": {
    "account_name": "Joseph Washington Household",
    "primary_named_insured": "Joseph Washington",
    "account_type": "Personal Lines",
    "account_status": "Urgent",
    "address": "1234 Oak Lane",
    "city": "Decatur",
    "state": "GA",
    "zip": "30030",
    "mailing_address": null,
    "phone": "(404) 555-0142",
    "email": "joseph.washington@example.com",
    "marital_status": "Married",
    "household_member_count": 4,
    "preferred_contact": "Phone",
    "current_carriers": [
      {"line": "Auto", "carrier": "Progressive", "premium": 1820, "renewal": "2026-08-15"},
      {"line": "Home", "carrier": "Travelers", "premium": 1450, "renewal": "2026-09-01"}
    ],
    "tags": ["prospect", "personal-lines", "bundle-opportunity"]
  },

  "contacts": [
    {
      "full_name": "Joseph Washington",
      "first_name": "Joseph",
      "last_name": "Washington",
      "household_role": "Primary",
      "date_of_birth": "1981-04-12",
      "phone": "(404) 555-0142",
      "email": "joseph.washington@example.com",
      "primary_contact": true
    },
    {
      "full_name": "Marie Washington",
      "first_name": "Marie",
      "last_name": "Washington",
      "household_role": "Spouse",
      "date_of_birth": "1983-09-22",
      "phone": "(404) 555-0173",
      "email": "marie.washington@example.com"
    }
  ],

  "drivers": [
    {
      "name": "Joseph Washington",
      "household_role": "Primary",
      "license_state": "GA",
      "license_number_on_file": true,
      "violations_3y": 0,
      "accidents_3y": 0,
      "good_student": false
    },
    {
      "name": "Marie Washington",
      "household_role": "Spouse",
      "license_state": "GA",
      "license_number_on_file": true,
      "violations_3y": 1,
      "accidents_3y": 0
    }
  ],

  "vehicles": [
    {
      "year": 2021,
      "make": "Toyota",
      "model": "RAV4",
      "vin_on_file": true,
      "garaging_zip": "30030",
      "annual_miles": 12000,
      "use": "Commute",
      "coverage_request": {"bi": "100/300", "pd": "100", "comp_deductible": 500, "coll_deductible": 500}
    }
  ],

  "homes": [
    {
      "address": "1234 Oak Lane, Decatur, GA 30030",
      "year_built": 1996,
      "construction": "Frame",
      "roof_material": "Asphalt Shingle",
      "roof_year": 2018,
      "square_footage": 2100,
      "dwelling_limit_requested": 380000,
      "personal_property_pct": 50,
      "deductible": 1000,
      "mortgagee": "Wells Fargo",
      "loan_number_on_file": false,
      "pool": false,
      "trampoline": false,
      "dogs": false,
      "prior_claims": []
    }
  ],

  "umbrella_consideration": {
    "interested": true,
    "target_limit": "$1M",
    "underlying_required": ["Auto 250/500", "Home Liability $500K"]
  },

  "coverage_needs": ["Auto", "Home", "Umbrella"],

  "opportunities": [
    {
      "opportunity_name": "Joseph Washington Household - Auto - 08/15/2026",
      "line_of_business": "Auto",
      "stage": "Discovery",
      "proposed_effective_date": "2026-08-15",
      "opportunity_type": "Remarket",
      "current_carrier": "Progressive",
      "current_premium": 1820
    },
    {
      "opportunity_name": "Joseph Washington Household - Home - 09/01/2026",
      "line_of_business": "Home",
      "stage": "Discovery",
      "proposed_effective_date": "2026-09-01",
      "opportunity_type": "Remarket",
      "current_carrier": "Travelers",
      "current_premium": 1450
    },
    {
      "opportunity_name": "Joseph Washington Household - Umbrella - 2026",
      "line_of_business": "Umbrella",
      "stage": "Discovery",
      "opportunity_type": "Cross-Sell",
      "target_limit": "$1M"
    }
  ],

  "cross_sell_opportunities": [
    {"lob": "Life", "reason": "Two kids in household, mortgage outstanding"},
    {"lob": "Valuable Items", "reason": "Mentioned wedding ring + camera gear"}
  ],

  "missing_information": [
    {"item": "Driver license numbers", "why_needed": "Auto quoting (mark restricted)"},
    {"item": "Home replacement-cost estimate", "why_needed": "Dwelling limit validation"},
    {"item": "5-year loss runs / CLUE report", "why_needed": "Carrier appetite"}
  ],

  "facts": [
    {"entity": "Joseph Washington Household", "entity_type": "Account", "fact_label": "Address", "fact_value": "1234 Oak Lane, Decatur, GA 30030", "sensitivity": "standard", "source": "fact-finder"},
    {"entity": "Joseph Washington", "entity_type": "Contact", "fact_label": "Phone", "fact_value": "(404) 555-0142", "sensitivity": "standard", "source": "fact-finder"},
    {"entity": "Joseph Washington", "entity_type": "Contact", "fact_label": "Date of Birth", "fact_value": "1981-04-12", "sensitivity": "restricted", "source": "fact-finder"},
    {"entity": "Marie Washington", "entity_type": "Contact", "fact_label": "Phone", "fact_value": "(404) 555-0173", "sensitivity": "standard", "source": "fact-finder"}
  ]
}
```

## Field extraction notes

- **`account_name`** — use `"<Last Name> Household"` for married/family
  cases, `"<Full Name>"` for single-person households.
- **`primary_named_insured`** — the person whose name goes on the policy.
- **`household_role`** — `Primary`, `Spouse`, `Child`, `Parent`,
  `Roommate`, `Other`.
- **Drivers vs. Contacts** — every driver gets a contact row (if they're a
  household member) plus an entry in `drivers[]`. A driver who isn't a
  household member (e.g. a college student kid) still gets a contact.
- **Vehicles** — VIN goes restricted; don't echo it in summaries unless
  explicitly asked.
- **Homes** — multiple properties are allowed; each gets its own row in
  `homes[]` and may map to its own Dwelling Fire opportunity if rented.
- **`current_carriers`** + premium + renewal — required for remarket math
  and `revenue-sentinel` x-date alerts.
- **Cross-sell** — always look for Life, Umbrella, Valuable Items,
  Boat/RV/Motorcycle, Renters (for adult children), Earthquake/Flood.

## Hard rules

1. **One opportunity per LOB.** Auto, Home, Umbrella, Dwelling Fire,
   Boat each get their own row.
2. **DOB, DL #, VIN, SSN → `sensitivity: restricted`.** Never echo in
   broad summaries.
3. **No invented driver violations or accidents.** Pull from CLUE/MVR or
   client statement; if not available, leave 0 and add to
   `missing_information`.
4. **No invented home replacement cost.** Leave dwelling_limit_requested
   null until validated by a tool or client conversation.
5. **Bundle bias toward retention.** When existing client adds a vehicle
   or buys a home, prefer creating a Cross-Sell Opportunity over a Service
   Request unless it's strictly an endorsement.
6. **Renters for adult kids — separate household.** Don't conflate.

## Handoff

Return the JSON above to `crm-intake-writer`, which will assemble the
final upsert payload and ask for the approval token.

## References

- `docs/agency-memory-plan.md`
- `hermes-training/espocrm/field_dictionary.md` — Account / Contact household fields
- `crm-intake-writer`
- `revenue-sentinel` — picks up x-date opportunities from `current_carriers`
