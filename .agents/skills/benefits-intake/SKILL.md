---
name: benefits-intake
description: Specialty extractor for group benefits and Medicare prospects — Group Health, Dental, Vision, Group Life, Group Disability, AFLAC/Supplemental, and individual Medicare (Advantage, Supplement, PDP). Extracts company/contact, employee/eligible counts, current carrier + renewal date, contribution strategy, benefits offered, census status, decision maker, pain points, and requested lines (for group); or DOB, AEP/SEP date, county, providers, Rx list, current plan (for Medicare). Produces `account`, `contacts`, per-line `opportunities`, and `facts` for `crm-intake-writer`. Use whenever the input is group benefits or Medicare.
---

# Benefits Intake

The group benefits + Medicare specialty extractor. One skill, two shapes
— pick by signal.

## When to use

**Group benefits** signals:

- "We have 15 employees and want a health quote."
- Renewal letter from BCBS / Aetna / Cigna / UHC / Humana group.
- "Looking at switching off Kaiser."
- ICHRA / level-funded / fully-insured talk.
- Adding dental / vision / group life / voluntary AFLAC.

**Medicare** signals:

- AEP (annual enrollment period) discussions.
- "Turning 65 in November."
- Medicare Advantage / Medicare Supplement (Med Supp) / Part D / PDP.
- IRMAA, Plan G, Plan N, MAPD.

Hand off to:

- `personal-lines-intake` if they only have an individual under-65 health
  question (RSG does not currently write off-exchange individual health
  beyond Medicare; surface a referral note instead).
- `life-insurance-intake` if it's individual term/whole — group life
  stays here, individual life stays there.
- `commercial-risk-intake` for the P&C side of the same employer
  (separate account or related-account link).

---

## Output A — Group Benefits

```json
{
  "shape": "group_benefits",
  "account": {
    "account_name": "JB Noble Benefits",
    "legal_name": "JB Noble Construction LLC",
    "dba": "JB Noble",
    "fein": "12-3456789",
    "entity_type": "LLC",
    "industry": "Construction",
    "address": "100 Industrial Pkwy",
    "city": "Marietta",
    "state": "GA",
    "zip": "30060",
    "phone": "(770) 555-0100",
    "email": "hr@jbnoble.com",
    "website": "jbnoble.com",
    "employee_count_total": 42,
    "eligible_employee_count": 38,
    "currently_enrolled_count": 31,
    "industry_segment": "Commercial Contractor",
    "account_type": "Group Benefits",
    "account_status": "Renewing",
    "tags": ["group-benefits", "renewal", "construction"]
  },

  "decision_maker": {
    "full_name": "Yvette Carter",
    "title": "Director of People Operations",
    "phone": "(770) 555-0107",
    "email": "yvette.carter@jbnoble.com",
    "decision_authority": "Final approver"
  },

  "additional_contacts": [
    {"full_name": "Ron Noble", "title": "CEO", "decision_authority": "Owner sign-off"}
  ],

  "current_plan": {
    "carrier": "BCBS GA",
    "plan_type": "Fully Insured PPO",
    "renewal_date": "2027-01-01",
    "contribution_strategy": "ER 70 / EE 30 for EE only; 50/50 for dep tiers",
    "current_total_monthly_premium": 38420.00,
    "current_annual_premium": 461040.00,
    "ancillary_in_place": ["Dental", "Vision", "Group Life"]
  },

  "renewal_indication": {
    "received": true,
    "increase_pct": 18.4,
    "new_annual_premium": 545911.00,
    "client_reaction": "Looking to remarket"
  },

  "requested_lines": ["Group Medical", "Dental", "Vision", "Group Life", "STD", "LTD"],

  "census_status": {
    "received": false,
    "format_expected": "Carrier census template + de-identified claims if self-funded talk",
    "due_date": "2026-09-15"
  },

  "pain_points": [
    "Premium increase too steep year-over-year",
    "Network adequacy in rural Georgia counties",
    "Voluntary AFLAC participation low"
  ],

  "opportunities": [
    {
      "opportunity_name": "JB Noble Benefits - Group Medical Renewal - 01/01/2027",
      "line_of_business": "Group Health",
      "stage": "Discovery",
      "proposed_effective_date": "2027-01-01",
      "opportunity_type": "Renewal",
      "current_carrier": "BCBS GA",
      "current_premium": 461040.00,
      "renewal_indication_pct": 18.4
    },
    {
      "opportunity_name": "JB Noble Benefits - Dental - 01/01/2027",
      "line_of_business": "Dental",
      "stage": "Discovery",
      "proposed_effective_date": "2027-01-01",
      "opportunity_type": "Renewal"
    }
  ],

  "missing_information": [
    {"item": "Current carrier renewal letter PDF", "why_needed": "Indication verification"},
    {"item": "Census file", "why_needed": "Quote alternates"},
    {"item": "Most recent 12 months of large claims (if level-funded path)", "why_needed": "Self-funded feasibility"}
  ],

  "compliance_caveats": [
    "ACA reporting status — confirm 1094/1095 obligations for 50+ FTE.",
    "ERISA SPD distribution — verify on file.",
    "No PHI shared in Slack channels — always route to secure portal."
  ],

  "facts": [
    {"entity": "JB Noble Construction LLC", "entity_type": "Account", "fact_label": "EIN", "fact_value": "12-3456789", "sensitivity": "restricted", "source": "renewal letter"},
    {"entity": "JB Noble Construction LLC", "entity_type": "Account", "fact_label": "Eligible Employees", "fact_value": "38", "sensitivity": "standard", "source": "renewal letter"},
    {"entity": "Yvette Carter", "entity_type": "Contact", "fact_label": "Title", "fact_value": "Director of People Operations", "sensitivity": "standard", "source": "renewal letter"}
  ]
}
```

---

## Output B — Medicare

```json
{
  "shape": "medicare",
  "account": {
    "account_name": "Walter Brooks",
    "account_type": "Medicare",
    "account_status": "Urgent",
    "address": "55 Magnolia Dr",
    "city": "Savannah",
    "state": "GA",
    "zip": "31401",
    "phone": "(912) 555-0188",
    "email": "walter.brooks@example.com",
    "preferred_contact": "Phone",
    "tags": ["prospect", "medicare", "aging-in"]
  },

  "contacts": [
    {
      "full_name": "Walter Brooks",
      "date_of_birth": "1961-11-04",
      "household_role": "Primary",
      "phone": "(912) 555-0188",
      "email": "walter.brooks@example.com",
      "primary_contact": true,
      "aep_sep_date": "2026-12-07",
      "days_until_65": 0,
      "medicare_part_a_status": "Enrolled",
      "medicare_part_b_status": "Enrolled effective 2026-12-01",
      "irmaa_applies": false,
      "us_citizen": true
    }
  ],

  "current_coverage": {
    "current_plan_type": "None",
    "transitioning_from": "Employer group at retirement",
    "termination_date": "2026-11-30"
  },

  "preferences": {
    "plan_type_preference": "Medicare Supplement (Plan G)",
    "drug_plan_needed": true,
    "drug_list": ["metformin 500mg BID", "atorvastatin 20mg QD"],
    "preferred_providers": ["Memorial Health Savannah", "Dr. Patel — Primary Care"],
    "preferred_pharmacies": ["CVS Savannah - Whitaker St"],
    "travel_considerations": "Spends winters in Florida — needs nationwide network"
  },

  "county_footprint": {
    "county": "Chatham",
    "state": "GA"
  },

  "opportunities": [
    {
      "opportunity_name": "Walter Brooks - Medicare Supplement Plan G - 12/01/2026",
      "line_of_business": "Medicare",
      "stage": "Discovery",
      "product_subtype": "Medicare Supplement Plan G",
      "proposed_effective_date": "2026-12-01",
      "opportunity_type": "New Business"
    },
    {
      "opportunity_name": "Walter Brooks - PDP (Part D) - 12/01/2026",
      "line_of_business": "Medicare",
      "stage": "Discovery",
      "product_subtype": "PDP",
      "proposed_effective_date": "2026-12-01",
      "opportunity_type": "New Business"
    }
  ],

  "compliance_caveats": [
    "Scope of Appointment (SOA) required before any plan-specific discussion.",
    "PECL (Permission to Contact Lead) required for outbound calls.",
    "Marketing material must be CMS-approved — do not improvise plan details.",
    "No marketing during Part B effective date window without proper election period."
  ],

  "missing_information": [
    {"item": "Signed Scope of Appointment", "why_needed": "CMS compliance"},
    {"item": "Complete Rx list with doses", "why_needed": "PDP plan match"},
    {"item": "Medicare Part B effective date confirmation", "why_needed": "Election period eligibility"}
  ],

  "facts": [
    {"entity": "Walter Brooks", "entity_type": "Contact", "fact_label": "Date of Birth", "fact_value": "1961-11-04", "sensitivity": "restricted", "source": "intake call"},
    {"entity": "Walter Brooks", "entity_type": "Contact", "fact_label": "Part B Effective", "fact_value": "2026-12-01", "sensitivity": "restricted", "source": "intake call"},
    {"entity": "Walter Brooks", "entity_type": "Contact", "fact_label": "County", "fact_value": "Chatham, GA", "sensitivity": "standard", "source": "intake call"}
  ]
}
```

## Hard rules

1. **Group health, dental, vision, group life, STD, LTD each get their
   own per-LOB Opportunity.** Never bundle.
2. **Medicare prospects need a separate Medicare account** distinct from
   any household / commercial account they're tied to.
3. **PHI is restricted — full stop.** Diagnoses, claims, Rx, lab values,
   any health condition → `sensitivity: restricted` and never echoed in
   shared Slack channels.
4. **CMS rules govern Medicare** — surface SOA, PECL, and election-period
   requirements in `compliance_caveats` whenever the input lacks them.
5. **No invented carriers, plans, formularies, or premiums.** Pull from
   `medicare_master_plan_index`, `medicare_plans`, `medicare_carriers`
   (Supabase) via the appropriate reader skill — do not synthesize plan
   details from training data.
6. **Renewal indication ≠ renewal premium.** Mark "indication" until the
   final rate sheet is on file; carriers commonly revise.
7. **ACA, ERISA, COBRA compliance flags** belong in `compliance_caveats`,
   not the note body.

## Handoff

Return one of the two shapes above to `crm-intake-writer`. The intake
writer will produce one Opportunity per requested LOB.

## References

- `docs/agency-memory-plan.md`
- `docs/hermes-supabase-domain-map.md` — Medicare tables
  (`medicare_master_plan_index`, `medicare_plans`, `medicare_carriers`,
  `medicare_underwriting_rules`, `medicare_county_footprints`,
  `medicare_provider_registry`, `medicare_medical_rx_matrix`)
  (`aepSepDate`, `daysUntil65`, `irmaApplies`,
  `policyMedicarePlanType`)
- `crm-intake-writer`
- `carrier-appetite`
