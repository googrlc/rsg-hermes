---
name: carrier-appetite
description: Match a risk to carriers RSG actually has appointments with — commercial AND personal lines — by line of business, state, premium size, exclusions, and knockouts, using the Supabase `carrier_appetite`, `carriers`, and `carrier_contacts` tables. Returns a ranked carrier list with rationale, confidence, underwriter contact, and disqualifications. Never invents carrier appetite or rate data. Use this skill whenever anyone asks "who writes this?", "carrier fit for X?", "where do we submit this?", "who should I quote this HO3 with?", "does anybody write [class/risk]?", or when `commercial-risk-intake`, `personal-lines-intake`, `life-insurance-intake`, `benefits-intake`, or `renewal-review` need carrier candidates. Also use for remarketing a renewal, checking whether a carrier is still on appetite, or logging where a risk actually landed.
---

# Carrier Appetite

The "who writes this?" skill. Grounded entirely in RSG's recorded
appetite tables — **no carrier name comes out of training data.**

Serves two audiences with different needs:

- **Lamar** — commercial, mid-to-large, complex remarkets. Wants the
  full ranked list and the submission strategy.
- **Gretchen** — personal lines, first-line service. Wants a fast,
  confident answer she can act on without pinging Lamar, plus a clear
  line for when she *must* escalate.

---

## Ground truth: what's actually in the database

Read this before writing a single query. The tables are not what the
old version of this skill assumed.

| Table | Rows | Role |
|---|---|---|
| `carrier_appetite` | 74 | **Primary source.** LOB-level appetite rows. |
| `carriers` | 123 | Appointment roster + `appetite_can_write` / `appetite_cannot_write` arrays + `underwriting_hotline`. |
| `carrier_contacts` | 142 | Underwriter / marketing rep contacts, joined via `carrier_id`. |
| `appetite_records` | 9 | Raw ingest staging. Not query-ready. |
| `appetite_code_definitions` | 6 | Class-code definitions. Thin. |
| `appetite_carrier_profiles` | **0** | **EMPTY — deprecated. Do not query, do not cite.** |

### The classification tables (separate subsystem — know they exist)

RSG has a fully populated class-code system. It is **not** joined to
appetite yet, but it is real and it is not empty:

| Table | Rows |
|---|---|
| `naics_codes` | 2,126 |
| `gl_class_codes` | 1,154 |
| `wc_class_codes` | 499 |
| `sic_codes` | 445 |
| `naics_wc_mappings` | 174 |
| `naics_gl_mappings` | 151 |
| `operations_to_codes` | 57 |
| `code_bundles` | 0 (empty) |

Plus views `vw_classification_resolved`, `vw_classification_payload`,
`vw_naics_candidate_expansion`.

**This skill does not own class-code lookup.** Classification belongs to
a separate `class-code-lookup` skill. If a user asks "what class code is
X?", answer from these tables or hand off — do not answer from
`carrier_appetite`, which has almost no codes on it.

### `carrier_appetite` real columns

`id` (uuid), `carrier_name`, `carrier_id`, `lob`, `appetite_level`,
`min_premium`, `max_premium`, `states_approved` (array),
`key_requirements` (array), `exclusions` (array), `class_codes` (array),
`notes`, `details` (jsonb), `source`, `source_document`, `confidence`,
`effective_date`, `active`, `updated_by`, `created_at`, `updated_at`.

### Known data gaps — plan around these, don't pretend they're fixed

- **73 of 74 rows have no `class_codes`.** This is a *missing join*, not
  missing data — the codes exist in `wc_class_codes` / `gl_class_codes`
  / `naics_codes`, they've just never been linked to appetite rows.
  Until that link exists, class-code matching is an *annotation and
  tiebreaker*, never a filter. Filtering on class code returns an empty
  set. Say "not linked yet," not "we don't have class codes" — the
  second is false and it makes RSG sound less equipped than it is.
- **58 of 74 rows are `confidence = 'unverified'`.** Every answer must
  carry a confidence signal. See "Confidence and staleness" below.
- **32 of 74 rows have no `carrier_id`.** The join to
  `carrier_contacts` fails for ~43% of rows. Fall back to
  `carriers.underwriting_hotline`, then to "no contact on file."
- **16 rows have no `states_approved`.** Treat as unknown, not as
  "all states." Flag it.
- **16 rows have `appetite_level = null`.** Report as `unrated`.
- No Contractors Pollution Liability row exists. If asked, say so and
  route to the wholesale path.

---

## Controlled vocabularies — use these exact values

### `appetite_level` (the only valid values)

| DB value | Display | Meaning |
|---|---|---|
| `preferred` | Preferred | Target class. Lead with these. |
| `standard` | Standard | Will write, normal terms. |
| `non-standard` | Non-Standard | Will look, expect surcharge or restriction. |
| `null` | Unrated | Appetite not yet recorded. Verify before submitting. |

Ranking order: `preferred` → `standard` → `non-standard` → `unrated`.

**Never emit** `Sweet Spot`, `Niche`, `Hard-To-Place`, `Wholesale`, or
`Excluded` as appetite levels. Those values do not exist in the data.
Wholesale is a *path*, not a tier — see the no-fit section.

### `confidence`

`verified` (16 rows) | `unverified` (58 rows). No other values.

### LOB normalization

`lob` values are inconsistently named. Normalize the user's request
before matching, and match **case-insensitively with alias expansion.**

| User says | Match these `lob` values |
|---|---|
| Workers Comp, WC, Workers Compensation | `Workers Comp`, `Workers Comp - Ghost Policy` |
| GL, General Liability | `General Liability`, `Contractor GL - Standard`, `Contractor GL - Plus`, `Contractor GL - Advantage` |
| Commercial Auto, Fleet | `Commercial Auto`, `Preferred Auto` |
| Trucking | `Trucking - Long Haul`, `Trucking - Cargo`, `Trucking - Physical Damage` |
| BOP, Package | `BOP`, `Business Policy`, `Middle Market Commercial` |
| Home, HO, Homeowners | `Homeowners`, `Homeowners Property`, `High Value Home`, `Condo` |
| Personal Auto | `Personal Auto`, `Preferred Auto` |
| Umbrella | `Umbrella`, `Personal Umbrella`, `Excess Liability` |
| Landlord, Rental, DP3 | `Landlord`, `Investor Property` |
| Flood | `Private Flood`, `Commercial Flood` |
| Professional, E&O | `Professional Liability`, `Medical Professional Liability` |
| Life | `Term Life`, `Whole Life` |

If a requested LOB maps to nothing, say so plainly and go to the no-fit
path. Do not substitute a "close enough" LOB.

---

## The Carrier Hub portal — the human write path

The Carrier Hub (`rsg-carrierhub`) reads the **same three tables this
skill reads.** There is one source of truth, not two.

- Browser calls `/api/carriers`
- That endpoint runs in `server.ts` on the tailnet-only box using the
  Supabase **service role** key
- It returns `carriers` joined with `carrier_contacts` and
  `carrier_appetite`
- Access is gated by **Tailscale network membership**, not per-user
  login. Public Supabase stays RLS-locked
- Commission data comes separately via the `portal_carrier_commissions`
  view over `commission_rules`
- The portal **writes back** — `saveCarrier()` POSTs edits

### What this means for the skill

1. **This skill stays read-only. The portal is how data gets fixed.**
   When a run hits unverified, unrated, or missing data, the correct
   advice is "open the carrier in the Carrier Hub and fix it there" —
   not "run an UPDATE." Gretchen can do this without database access.

   Appetite rows are editable in `CarrierDrawer.tsx`, including
   `class_codes` and `confidence`. A **MARK VERIFIED** toggle sits on
   each row in edit mode.

   *Historical note (fixed 2026-07-25):* `confidence` was previously
   hardcoded to `'unverified'` on save, with no UI control and no way to
   edit an existing row. That is why 58 of 74 rows read unverified — it
   was mechanically impossible to promote one. If a future run sees
   unverified counts stuck despite verification work, check that this
   regression hasn't returned.
2. **Never suggest editing `src/data/carriers.ts`.** That file is a
   dead hardcoded fallback superseded by `/api/carriers`. Editing it
   creates a second, silently-wrong copy of the carrier directory.
3. If the portal and this skill ever disagree, they're reading the same
   rows — the difference is caching, not truth. Say so.

---

## Modes

Pick the mode from the request. When ambiguous, default to **QUICK**
and offer to expand — nobody reads 60 lines of JSON on a phone.

### Mode: QUICK (default)

Trigger: any bare "who writes X?" question, mobile context, or a
request during a live call.

Output is **plain text, under 8 lines.** No JSON.

```
Bypass pumping contractor, GA, WC — 3 markets:

1. AmTrust — Preferred, verified, $1.5K min. GA filed.
2. EMPLOYERS — Standard, unverified (check first).
3. Pie — Non-Standard, unverified. Backup only.

Not: Travelers (excludes payroll under $250K).
⚠ No class codes on file for any of these — appetite is LOB-level only.
```

### Mode: FULL

Trigger: "build the submission," remarketing a renewal, a handoff to
`proposal-builder`, or an explicit ask for detail.

Emits the JSON shape below plus a short prose summary on top.

### Mode: PL (Gretchen's lane)

Trigger: any personal lines LOB, or Gretchen is the requester.

Same as QUICK, plus a mandatory **action line** telling her whether she
can proceed alone:

- **Green — proceed.** `confidence = verified` AND `appetite_level` in
  (`preferred`, `standard`) AND state is in `states_approved`.
- **Yellow — proceed, verify at quote.** `unverified` but
  `preferred`/`standard`, and nothing in `exclusions` matches the risk.
  She quotes it; if the carrier balks, that's a data-correction event
  (log it, see feedback loop).
- **Red — escalate to Lamar.** Any of: `non-standard`, `unrated`,
  a matching exclusion, no `states_approved` on file, estimated premium
  above `max_premium`, or a high-value home / coastal / prior-loss risk.

Always print the color. Gretchen should never have to infer it.

---

## Workflow

1. **Normalize the LOB** using the alias table above.
2. **Query `carrier_appetite`** where `active IS TRUE` and normalized
   `lob` matches. Case-insensitive.
3. **Filter by state** — `state = ANY(states_approved)`. Rows with a
   null/empty `states_approved` are **kept but flagged** as
   `state_unconfirmed`, never silently dropped and never assumed
   nationwide.
4. **Filter by premium band** — drop rows where the estimate falls
   below `min_premium` or above `max_premium`. Rows with both null pass
   through flagged as `band_unknown`.
5. **Apply exclusions** — scan the `exclusions` array against the risk's
   known exposures. A match is a hard disqualification; record it in
   `disqualified_carriers` with the exact exclusion string.
6. **Check `key_requirements`** — these become the
   `missing_information` checklist, not disqualifiers.
7. **Annotate class codes** — if `class_codes` is populated (rare),
   note the match as a confidence boost. If empty, add the caveat that
   *this carrier's* appetite is recorded at LOB level only. If the user
   supplied a NAICS or class code, you may still resolve and echo it
   from the classification tables so the submission packet carries the
   right code — just don't present it as carrier-verified appetite.
8. **Cross-check `carriers`** — if the carrier appears in
   `appetite_cannot_write` for this class, disqualify it even if
   `carrier_appetite` says otherwise. `carriers` wins on conflict.
9. **Attach a contact** — join `carrier_contacts` on `carrier_id`,
   preferring `is_primary = true`. Fall back to
   `carriers.underwriting_hotline`. Then "no contact on file."
10. **Rank** by `appetite_level`, then `confidence` (verified first),
    then `updated_at` descending.
11. **Emit** in the requested mode.

---

## Confidence and staleness

78% of appetite rows are unverified. Never present an unverified row as
settled fact.

- Every carrier line carries its `confidence` value inline.
- If **all** returned carriers are `unverified`, open the response with:
  *"⚠ No verified appetite data for this LOB — confirm with the
  underwriter before you promise anything to the client."*
- If `updated_at` is older than **180 days**, append
  `(stale — last touched YYYY-MM-DD)`. Currently zero rows trip this;
  the oldest is 2026-03-30. That will change. Keep the check.
- If `effective_date` is in the future, exclude the row and note it.
- Never average, interpolate, or infer appetite across carriers.

---

## No-fit and declination path

When step 5 leaves zero carriers, the answer is **not** an empty list.
Return, in order:

1. **State it plainly.** "No appointed carrier on file writes
   [LOB] for [risk] in [state]."
2. **Show the near-misses** — carriers that matched LOB and state but
   failed on premium band or exclusion, with the specific reason. This
   is often actionable (restructure the limits, split the coverage).
3. **Wholesale path.** Wholesale is not an appetite tier. If RSG has a
   wholesale relationship recorded in `carriers` (check `segment` and
   `general_agent`), name it. If not, say "no wholesale relationship on
   file for this line" — do not invent RT Specialty, Burns & Wilcox, or
   anyone else.
4. **Log the gap.** A no-fit is a business signal: it's either a
   missing appointment or a class RSG shouldn't be chasing. Record it
   (see feedback loop) so the pattern surfaces instead of evaporating.
5. **Never** soften a no-fit into a maybe to be helpful. A wasted
   submission costs more than an honest no.

---

## Feedback loop: close the ring on placements

The appetite data will rot unless outcomes flow back into it. This is
the highest-leverage addition to the skill.

**Status: the table below does not exist yet.** Proposed DDL — review
before applying:

```sql
create table if not exists appetite_placement_outcomes (
  id uuid primary key default gen_random_uuid(),
  account_name        text not null,
  lob                 text not null,
  state               text,
  carrier_recommended text,
  carrier_submitted   text,
  carrier_bound       text,
  appetite_row_id     uuid references carrier_appetite(id),
  outcome             text not null check (outcome in
                        ('bound','declined','quoted_not_bound',
                         'no_market','client_withdrew')),
  decline_reason      text,
  premium_bound       numeric,
  recommended_rank    integer,
  recorded_by         text,
  created_at          timestamptz not null default now()
);
create index on appetite_placement_outcomes (lob, state);
create index on appetite_placement_outcomes (carrier_bound);
```

### Write rules

- Write **one row per submission attempt**, not per risk.
- Trigger the write when a quote is bound, declined, or abandoned —
  `renewal-review` and `commercial-risk-intake` should both call it.
- `recommended_rank` records where this skill ranked the carrier that
  actually won. That's the accuracy metric.

### Read rules — what to do with the data

- **Silent decline pattern:** 2+ declines from the same carrier on the
  same LOB within 90 days → flag that `carrier_appetite` row for
  re-verification and drop it a tier in ranking until reviewed.
- **Rank accuracy:** if the bound carrier was routinely ranked 3rd or
  worse, the ranking logic is wrong — surface it, don't bury it.
- **Verification promotion:** a `bound` outcome is evidence. Suggest
  flipping `confidence` from `unverified` to `verified` on that row
  (with a human confirming — never auto-write to `carrier_appetite`).

---

## Output shape (Mode: FULL)

```json
{
  "action": "carrier_appetite_match",
  "mode": "FULL",
  "risk_summary": "3D Pumps LLC — bypass pumping, GA, $335K rev, $80K payroll, 2 employees, no losses.",
  "lines_evaluated": ["Workers Comp", "General Liability"],
  "data_confidence_banner": "2 of 5 matched carriers are verified. Confirm before quoting.",
  "ranked_carriers_by_lob": {
    "Workers Comp": [
      {
        "carrier": "AmTrust",
        "appetite_level": "preferred",
        "confidence": "verified",
        "rationale": "GA filed; small construction payroll band.",
        "min_premium": 1500,
        "max_premium": null,
        "states_approved_match": true,
        "class_code_match": "none_on_file",
        "exclusions_checked": ["no matching exclusion"],
        "key_requirements": ["signed app", "5-yr loss runs"],
        "contact": {"name": "…", "email": "…", "source": "carrier_contacts"},
        "source": "carrier_appetite id <uuid>, updated 2026-07-22"
      }
    ]
  },
  "disqualified_carriers": [
    {
      "carrier": "Travelers",
      "lob": "Workers Comp",
      "reason": "exclusions[] contains 'payroll under $250K'",
      "source": "carrier_appetite id <uuid>"
    }
  ],
  "flags": ["class_codes_absent_on_all_rows", "state_unconfirmed: 1 carrier"],
  "submission_strategy": {
    "primary_path": "…",
    "timeline": "Submit within 5 business days of signed app + loss runs.",
    "missing_information": [
      {"item": "Signed ACORD 125/126", "why_needed": "All submissions"},
      {"item": "5-year loss runs, currently valued", "why_needed": "WC + GL"}
    ]
  },
  "compliance_caveats": [
    "Appetite ≠ binding authority; the underwriter has final say.",
    "Premium bands filter fit only — this skill produces no rates.",
    "Wholesale submissions may require admitted/non-admitted disclosure."
  ]
}
```

---

## Hard rules

1. **Never name a carrier that isn't in `carrier_appetite` or
   `carriers`.** No training-data carriers. Ever.
2. **Never query or cite `appetite_carrier_profiles`.** It is empty.
3. **Use only the real `appetite_level` values:** `preferred`,
   `standard`, `non-standard`, or report `unrated` for null.
4. **Never invent rates.** Premium bands are fit filters, not quotes.
5. **Always cite the source row** — table name + uuid + `updated_at`.
   No source, no recommendation.
6. **Always surface `confidence`.** Unverified data gets a warning, not
   a confident tone.
7. **Never filter on class code.** 73/74 appetite rows have none —
   filtering deletes the result set. Resolving a code from the
   classification tables is fine; gating carriers on it is not.
8. **Never treat a null `states_approved` as nationwide.** Flag it.
9. **`carriers.appetite_cannot_write` overrides `carrier_appetite`.**
   Conflict goes to the more restrictive answer.
10. **Always include `disqualified_carriers`** when an obvious market
    was ruled out. "Why not Travelers?" is a question you answer before
    it's asked.
11. **Client class codes are restricted by default.** Do not publish a
    named client tied to a class interpretation into a broad
    **NextCloud Talk** room without checking `sensitivity`. (Slack is
    retired — no Slack references anywhere.)
12. **Read-only against `carrier_appetite` and `carriers`.** The only
    write this skill performs is to `appetite_placement_outcomes`, and
    only once that table exists.

---

## Handoff

Output feeds:

- `proposal-builder` — submission packet per ranked carrier.
- `commercial-risk-intake` — populates
  `target_carriers_for_proposal_builder`.
- `personal-lines-intake` / Gretchen's workflow — PL mode green/yellow/red.
- `renewal-review` — fulfills a `REMARKET_FULL` recommendation.

Receives from:

- `class-code-lookup` (not yet built) — resolves a description or NAICS
  into WC/GL class codes before this skill runs. Until it exists, query
  `wc_class_codes` / `gl_class_codes` / `naics_*_mappings` directly and
  keep it to a lookup, not a full classification workflow.

---

## Data-quality backlog

Surfacing these is part of the skill's job, not a separate project.
When a run trips one, mention it once, briefly, at the end:

1. **Link `carrier_appetite.class_codes` to the existing classification
   tables.** Highest-value fix by a wide margin — it's the missing
   bridge between 4,400 populated codes and 74 carrier rows. Until it
   exists, "who writes NAICS 237110?" cannot be answered end-to-end.
   Start with Commercial Auto (39% of the book) and Workers Comp — 19
   rows, editable in the Carrier Hub, no SQL required.
2. Backfill `carrier_id` — 32 rows can't reach an underwriter contact.
3. Verify the 58 `unverified` rows. **Now unblocked** — the MARK
   VERIFIED toggle shipped 2026-07-25. Start with Commercial Auto (39%
   of the book) and Workers Comp (10 rows).
4. Populate `states_approved` on the 16 rows missing it.
5. Set `appetite_level` on the 16 `null` rows.
6. `carrier_documents` still carries `dify_dataset_id` / `dify_doc_id`
   columns. Dify is retired — those columns are dead weight and should
   be dropped in a cleanup migration.

---

## Save to the document library

When a carrier-fit summary is worth keeping, file it in
**Agent OS → Documents** under the client's folder:

```bash
hermes --doc-add \
  --doc-title "<client> — Carrier Appetite (<LOB>)" \
  --doc-account "<CRM account name>" \
  --doc-type appetite \
  --doc-file <path>
```

Or POST `/api/documents/save` with `{ "title", "content",
"account_name", "doc_type": "appetite", "source": "carrier-appetite" }`.
For a generic note not tied to one client, drop `account_name` and pass
`--doc-folder "Carrier Appetite"`.

---

## References

- `docs/hermes-supabase-domain-map.md`
- `proposal-builder`, `commercial-risk-intake`, `renewal-review`
- `rsg-carrierhub` — `server.ts` (`/api/carriers`),
  `src/lib/carriers-repo.ts`, `src/components/FindMarkets.tsx`
- Live schema verified 2026-07-25 against Supabase project
  `rsg-infrastructure`. Row counts in this file are from that date —
  re-check before trusting them a quarter from now.

## Open items (not done)

- `appetite_placement_outcomes` — DDL written above, **not applied.**
  The feedback loop is inert until someone runs it.
- `class-code-lookup` skill — not built.
- `carrier_appetite.class_codes` — not linked to the classification
  tables. 73 of 74 rows still empty.

## Shipped

- 2026-07-25 — Carrier Hub confidence editing. Added `newAppConfidence`
  state, a confidence dropdown on the add-row form, and a per-row
  MARK VERIFIED toggle in `CarrierDrawer.tsx`. Verification is now
  possible through the UI for the first time.
