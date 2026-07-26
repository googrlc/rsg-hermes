---
name: class-code-lookup
description: Resolve a business description, operation, or NAICS/SIC code into WC (NCCI) and GL (ISO) class codes using the Supabase classification tables — `naics_codes`, `gl_class_codes`, `wc_class_codes`, `sic_codes`, `naics_gl_mappings`, `naics_wc_mappings`, and `operations_to_codes`. Returns candidate codes with descriptions, the evidence for each match, and an explicit confidence level. Never invents a class code or its description. Use whenever anyone asks "what class code is X?", "what's the WC code for a plumber?", "what NAICS is this business?", "what GL code do I put on the ACORD?", or when `commercial-risk-intake`, `carrier-appetite`, or `proposal-builder` need a code resolved before they can run.
---

# Class Code Lookup

The "what code is this?" skill. Resolves operations and NAICS/SIC codes into
WC and GL class codes from RSG's own classification tables.

**This skill is a lookup, not a classification authority.** It returns
candidates plus the evidence. The final code on a submission is the
underwriter's call — say so every time.

Companion to `carrier-appetite`, which owns "who writes this?" and must never
answer code questions from `carrier_appetite` (that table has almost no codes).

---

## Ground truth: what's actually in the tables

Verified live 2026-07-25 against Supabase project `rsg-infrastructure`
(`wibscqhkvpijzqbhjphg`). **Re-check before trusting these a quarter from now.**

The code tables are well populated. The *mapping* and *search* layers on top of
them are not. That gap defines how this skill has to work.

| Table | Rows | Actually usable? |
|---|---|---|
| `naics_codes` | 2,126 | Yes — `naics_title` on all 2,126 |
| `gl_class_codes` | 1,154 | Yes — `description` on all 1,154 |
| `wc_class_codes` | 499 | Yes — `description` + `category` on all 499 |
| `sic_codes` | 445 | Yes — `sic_description` populated |
| `operations_to_codes` | 57 | Keywords yes; NAICS links only 6 of 57 |
| `naics_gl_mappings` | 151 | Covers **125 of 2,126** NAICS (5.9%) |
| `naics_wc_mappings` | 174 | Covers **154 of 2,126** NAICS (7.2%) |
| `appetite_code_definitions` | 6 | Too thin to rely on |
| `code_bundles` + all `bundle_*` | 0 | Empty |
| `gl_prohibited_pairings`, `wc_common_pairings`, `wc_red_flag_pairings` | 0 | Empty — no pairing validation exists |
| `operation_gl_codes`, `operation_wc_codes` | 0 | Empty |

### Four traps that will produce wrong answers

1. **No embeddings.** `naics_codes`, `gl_class_codes`, `wc_class_codes`,
   `sic_codes` and `operations_to_codes` all carry an `embedding` column and
   **every one of them is NULL** — 0 rows populated across all five tables.
   There is no semantic search. Use text matching. Do not write a
   vector query; it returns nothing.
2. **The `vw_classification_*` views only work on leads.**
   `vw_classification_resolved`, `vw_naics_candidate_expansion` and
   `vw_ops_keyword_candidates` all join `leads_staging` and key on `lead_id`.
   They classify a **row that already exists in `leads_staging`** — they are not
   general-purpose "classify this sentence" helpers. For ad-hoc text, run the
   keyword logic directly (recipe C below).
3. **`search_keywords` is nearly empty.** 29 of 499 on `wc_class_codes`,
   **0 of 1,154** on `gl_class_codes`, 0 of 2,126 for `naics_codes.common_ops_keywords`.
   `typical_businesses` and `typical_duties` are 0. Search `description` /
   `naics_title` — those are complete.
4. **NAICS→code mapping covers ~6% of NAICS.** A NAICS with no mapping row is
   overwhelmingly the normal case, not an error. Say "no mapping on file for
   this NAICS," then fall back to description search.

---

## Confidence levels — always state one

| Level | When | How to phrase it |
|---|---|---|
| `mapped` | A row exists in `naics_gl_mappings` / `naics_wc_mappings` | "Mapped in our tables." |
| `keyword` | Matched `operations_to_codes.keywords` or a `description` substring | "Keyword match — confirm with the underwriter." |
| `none` | Nothing matched | "No code on file for this. Do not guess one." |

Never present a `keyword` hit as settled. Never emit a code that is not in the
tables — if the right answer is a code we don't carry, say exactly that.

---

## Query recipes

### A. Code → description (the reverse lookup, most common)

```sql
select 'wc' as system, wc_code as code, description, category
  from wc_class_codes where wc_code = :code
union all
select 'gl', gl_code, description, subcategory
  from gl_class_codes where gl_code = :code
union all
select 'naics', naics_code, naics_title, industry_group
  from naics_codes where naics_code = :code;
```

A code can be absent. `5552`/`5553` (roofing) are not in `wc_class_codes` even
though `5551` is. Report the miss; do not substitute a neighbour.

### B. NAICS → GL + WC (the mapped path)

```sql
select n.naics_code, n.naics_title,
       g.gl_code, g.description as gl_description,
       w.wc_code, w.description as wc_description
  from naics_codes n
  left join naics_gl_mappings ngl on ngl.naics_id = n.id
  left join gl_class_codes    g   on g.id  = ngl.gl_code_id
  left join naics_wc_mappings nwc on nwc.naics_id = n.id
  left join wc_class_codes    w   on w.id  = nwc.wc_code_id
 where n.naics_code = :naics;
```

All-null GL/WC columns = unmapped NAICS (the ~94% case). Fall through to C.

### C. Description → candidate codes (the everyday path)

Two passes. Run both, merge, label the evidence.

```sql
-- pass 1: curated operations (57 rows, keywords are good here)
select operation_name, keywords, requires_pollution,
       specialty_market_required, prohibited_flags
  from operations_to_codes
 where operation_name ilike '%'||:term||'%'
    or keywords       ilike '%'||:term||'%';

-- pass 2: direct description search
select 'wc' as system, wc_code as code, description, category
  from wc_class_codes
 where description ilike '%'||:term||'%' or search_keywords ilike '%'||:term||'%'
union all
select 'gl', gl_code, description, subcategory
  from gl_class_codes where description ilike '%'||:term||'%'
union all
select 'naics', naics_code, naics_title, industry_group
  from naics_codes where naics_title ilike '%'||:term||'%'
 limit 25;
```

Search the **trade noun**, not the full sentence — `roof`, not
"they do roofing and gutters." Try singular and stem forms (`plumb` catches
plumber/plumbing). Multi-word phrases almost never hit these columns.

`operations_to_codes` also carries genuine underwriting signal —
`requires_pollution`, `specialty_market_required`, `prohibited_flags`. Surface
those; they change which markets `carrier-appetite` should even try.

### D. Hand off to carrier-appetite

Once a code is resolved, `vw_who_writes_naics` and
`vw_carrier_appetite_class_resolved` (added 2026-07-25) carry it through to
carriers. An empty result means **no code-level appetite link on file**, not
"nobody writes it" — `carrier-appetite` then falls back to LOB-level matching.

---

## Output shape

Keep it short. This is a lookup; nobody wants JSON for one code.

```
Plumbing contractor, GA:

WC   5183  Plumbing NOC & Drivers          — keyword match
GL   —     no GL code on file for "plumb"  — search the ISO description directly
NAICS 238220 Plumbing/HVAC Contractors      — mapped

⚠ Keyword matches are candidates, not determinations. The underwriter sets the
  final code. No pairing-validation tables exist, so nothing here checks whether
  these codes belong on the same policy.
```

For a full submission packet, add the NAICS + the GL/WC pair and flag anything
`operations_to_codes` marked as pollution / specialty / prohibited.

---

## Hard rules

1. **Never invent a class code or a description.** Every code and every
   description comes from a row. No source, no answer.
2. **Never guess an adjacent code.** If `5552` isn't in the table, the answer is
   "not on file" — not `5551`.
3. **Always state the confidence level** (`mapped` / `keyword` / `none`).
4. **Never claim a determination.** This skill produces candidates; the
   underwriter and the carrier's own class guide decide.
5. **Do not use the `vw_classification_*` views for ad-hoc text.** They require a
   `leads_staging` row.
6. **Do not attempt vector/semantic search.** All embedding columns are empty.
7. **No pairing validation exists.** `gl_prohibited_pairings`,
   `wc_common_pairings` and `wc_red_flag_pairings` are all empty — never imply a
   code combination has been checked.
8. **Client class codes are restricted by default.** Do not publish a named
   client tied to a class interpretation into a broad NextCloud Talk room
   without checking sensitivity.
9. **Read-only.** This skill writes nothing.

---

## Handoff

Feeds:
- `carrier-appetite` — resolves the code before the "who writes this?" run.
- `commercial-risk-intake` — populates NAICS/GL/WC on the intake payload.
- `proposal-builder` — the submission packet carries the right code.

---

## Data-quality backlog

Mention once, briefly, when a run trips one:

1. **Populate `search_keywords`** on `gl_class_codes` (0 of 1,154) and
   `wc_class_codes` (29 of 499). Highest-value fix — description-substring
   search is the only working path and it misses trade synonyms.
2. **Extend `naics_gl_mappings` / `naics_wc_mappings`** beyond ~6% NAICS
   coverage. Start with the classes RSG actually writes: contracting, trucking,
   habitational, restaurants.
3. **Link `operations_to_codes` to NAICS** — only 6 of 57 rows have
   `primary_naics_id`, which strands the curated keyword layer.
4. **Backfill embeddings** on all five tables, or drop the columns. Right now
   they advertise a capability that does not exist.
5. **`gl_class_codes.category` is 0 of 1,154** populated (`subcategory` is the
   usable one). Either fill it or stop selecting it.
6. Pairing tables (`gl_prohibited_pairings`, `wc_common_pairings`,
   `wc_red_flag_pairings`) and all `code_bundles` / `bundle_*` tables are empty.

---

## References

- `carrier-appetite` — the "who writes this?" companion skill
- `supabase/migrations/20260725120000_carrier_appetite_class_bridge.sql` —
  adds `carrier_appetite_class_codes`, `vw_carrier_appetite_class_resolved`,
  `vw_who_writes_naics`
- Live schema verified 2026-07-25 against project `rsg-infrastructure`
