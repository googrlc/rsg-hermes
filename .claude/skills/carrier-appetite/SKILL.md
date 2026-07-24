---
name: carrier-appetite
description: Match a risk to carriers RSG actually has appointments with — by line of business, class code, state, premium size, and risk-specific knockouts — using the Supabase `carrier_appetite` / `appetite_carrier_profiles` tables. Returns a ranked carrier list with rationale and disqualifications. Never invents carrier appetite or rate data. Use whenever the user asks "who writes this?", "carrier fit for X?", "where should we submit this risk?", or when `commercial-risk-intake` / `life-insurance-intake` / `benefits-intake` need carrier candidates.
---

# Carrier Appetite

The "who writes this?" skill. Grounded entirely in RSG's recorded
appetite tables — no carrier name comes out of training data.

## When to use

- "Who writes pollution for water/wastewater contractors in GA?"
- "Carrier fit for a roofer doing residential?"
- "What carriers will look at this trucking risk?"
- `commercial-risk-intake` needs `target_carriers_for_proposal_builder`.
- `renewal-review` recommended `REMARKET_FULL` and needs a carrier list.

Do **not** use this skill for:

- Quoting / binding (that's the producer + the carrier system, not Hermes).
- Rate calculations (we don't have rate tables; never guess).
- Personal-lines carrier matching beyond what `appetite_carrier_profiles`
  records.

## Inputs

```json
{
  "risk_profile": {
    "lines_of_business": ["Workers Compensation", "General Liability", "Contractors Pollution Liability"],
    "industry": "Construction",
    "naics": "237110",
    "class_codes": {"wc": "5403", "iso_gl": "98305"},
    "operations_summary": "Bypass pumping for water/wastewater treatment plants",
    "state": "GA",
    "states_of_operation": ["GA", "AL", "FL"],
    "annual_revenue": 335000,
    "annual_payroll": 80000,
    "employee_count": 2,
    "vehicle_count": 1,
    "premium_estimate_band": "5k-25k",
    "loss_history": {"claims_5y": 0, "incurred_5y": 0},
    "key_exposures": ["pollution", "mobile equipment"],
    "knockouts_to_check": ["pollution exclusion", "subcontracted work %"]
  }
}
```

## Workflow

1. **Filter carriers by LOB.** Query `appetite_carrier_profiles` for
   carriers writing each requested LOB.
2. **Filter by state.** Drop carriers with no GA filing.
3. **Filter by class code.** Match WC class, ISO GL class, NAICS as
   available.
4. **Filter by premium size.** Some carriers won't write under $X or
   over $Y. Match `premium_estimate_band` to the carrier's
   `min_premium` / `max_premium`.
5. **Apply knockouts.** Pollution exposure, subcontractor %, USL&H,
   commercial fleet — check exclusions / appetite flags per carrier.
6. **Rank** by appetite tier (Sweet Spot → Standard → Niche →
   Hard-To-Place) and recency of placement success when available.
7. **Annotate with rationale.**

## Output shape

```json
{
  "action": "carrier_appetite_match",
  "risk_summary": "3D Pumps LLC — bypass pumping for water/wastewater, GA, $335K rev, $80K payroll, 2 employees, 1 vehicle, no losses.",
  "lines_evaluated": ["WC", "GL", "CPL"],
  "ranked_carriers_by_lob": {
    "Workers Compensation": [
      {
        "carrier": "AmTrust",
        "appetite_tier": "Sweet Spot",
        "rationale": "Writes WC for small construction operations <$500K payroll in GA. Class 5403 acceptable.",
        "min_premium": 1500,
        "knockouts_checked": ["payroll cap OK", "owner exclusion OK", "no loss penalty"],
        "source": "appetite_carrier_profiles row id 47"
      },
      {
        "carrier": "EMPLOYERS",
        "appetite_tier": "Standard",
        "rationale": "WC specialist for small business; GA filed; class code on appetite list.",
        "source": "appetite_carrier_profiles row id 12"
      }
    ],
    "General Liability": [
      {
        "carrier": "Shield Commercial",
        "appetite_tier": "Sweet Spot",
        "rationale": "Quote 656137 already obtained; small contractors GL program.",
        "source": "carrier_appetite row id 88, quote 656137"
      }
    ],
    "Contractors Pollution Liability": [
      {
        "carrier": "Ironshore",
        "appetite_tier": "Niche",
        "rationale": "Writes site pollution / contractors pollution for water/wastewater operations; underwriter referral.",
        "knockouts_checked": ["mobile equipment OK", "limited spec required"],
        "source": "appetite_carrier_profiles row id 132"
      },
      {
        "carrier": "RT Specialty (Wholesale)",
        "appetite_tier": "Hard-To-Place",
        "rationale": "Wholesale path for CPL when retail markets decline.",
        "source": "appetite_carrier_profiles row id 156"
      }
    ]
  },
  "disqualified_carriers": [
    {
      "carrier": "Travelers",
      "lob": "Workers Compensation",
      "reason": "Carrier appetite excludes contractors with payroll under $250K."
    },
    {
      "carrier": "Hartford",
      "lob": "Contractors Pollution Liability",
      "reason": "Carrier does not write standalone pollution; only as endorsement to GL package they decline."
    }
  ],
  "submission_strategy": {
    "primary_path": "Bind GL with Shield Commercial; submit WC to AmTrust + EMPLOYERS in parallel; refer CPL to Ironshore retail with RT Specialty as wholesale backup.",
    "timeline": "Submit within 5 business days of receiving signed app + loss runs.",
    "missing_information": [
      {"item": "Signed ACORD 125/126 (or carrier app)", "why_needed": "All submissions"},
      {"item": "Loss runs 5-year, currently valued", "why_needed": "WC + GL + CPL"},
      {"item": "Driver MVRs", "why_needed": "Auto if added"},
      {"item": "Resume / experience modifier", "why_needed": "WC class 5403 underwriting"}
    ]
  },
  "compliance_caveats": [
    "Appetite ≠ binding authority; carrier underwriter has final say.",
    "Premium estimate band is unconfirmed; carrier may decline mid-quote based on undisclosed exposures.",
    "Wholesale submissions may require admitted/non-admitted disclosures."
  ]
}
```

## Hard rules

1. **Never name a carrier RSG doesn't have an appointment with.** Only
   surface carriers present in `appetite_carrier_profiles` /
   `carrier_appetite`.
2. **Never invent appetite tiers.** Use only the recorded tier values:
   `Sweet Spot`, `Standard`, `Niche`, `Hard-To-Place`, `Wholesale`,
   `Excluded`.
3. **Never invent rates.** This skill does not produce premium estimates
   — only premium-band fit filters. The actual rate comes from the
   carrier system.
4. **Always cite the source row.** Every carrier recommendation lists
   the Supabase table + row id. No source = no recommendation.
5. **Always include `disqualified_carriers` when relevant.** A "who
   writes this" answer is incomplete if it doesn't mention the obvious
   names that don't write the class.
6. **Surface wholesale paths separately.** Don't blend wholesale and
   retail in the same ranking — they have different placement
   timelines.
7. **Restricted-by-default for client class codes when posted to broad
   Slack channels.** Class code 5403 = "Construction." Don't publish a
   broader interpretation tied to a named client without checking
   `sensitivity`.
8. **Read-only.** This skill produces no CRM or Supabase writes.

## Handoff

The output feeds:

- `proposal-builder` — to build the submission packet for each ranked
  carrier.
- `commercial-risk-intake` — to populate
  `target_carriers_for_proposal_builder` on the underwriting summary.
- `renewal-review` — to fulfill the `REMARKET_FULL` recommendation.

## References

- `docs/agency-memory-plan.md`
- `docs/hermes-supabase-domain-map.md` — `carrier_appetite`,
  `appetite_carrier_profiles`
  fields
- `proposal-builder`
- `commercial-risk-intake`
- `renewal-review`

## Save to the document library

When you produce a carrier-fit / appetite summary worth keeping, file it in
**Agent OS → Documents** (under the client's folder) + Holographic Memory:

```bash
hermes --doc-add \
  --doc-title "<client> — Carrier Appetite (<LOB>)" \
  --doc-account "<CRM account name>" \
  --doc-type appetite \
  --doc-file <path>          # or pipe the summary via stdin
```

Or POST `/api/documents/save`: `{ "title", "content", "account_name",
"doc_type": "appetite", "source": "carrier-appetite" }`. For a generic
appetite note not tied to one client, drop `account_name` and pass
`--doc-folder "Carrier Appetite"` instead.
