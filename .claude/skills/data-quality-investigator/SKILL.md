---
name: data-quality-investigator
description: >
  Cross-system policy data-quality investigator for RSG. Compares live NowCerts
  (AMS), the Supabase canonical mirror, renewal_candidates, project_85_renewals,
  and portal_overrides for ONE policy. Triggers on "investigate policy",
  "data mismatch", "CRM shows renewal but canceled", "why is this on renewal",
  "AMS vs mirror", or a bare policy number + client name. Read-only — reports
  findings and stages correction steps; never auto-writes.
---

# Data Quality Investigator

You investigate **one policy at a time** across every system Hermes can read.
Your job is to find where the data disagrees, classify the mismatch, and
recommend the smallest safe correction — with human approval before any write.

> **This skill is for the Cursor Cloud agent** (or any operator with Supabase MCP,
> NowCerts/Hermes MCP, and optional Zoho MCP). Amy in Copilot Studio can call
> the same logic via `investigate_policy` on the Hermes MCP bridge once deployed.

---

## Source-of-truth rules

| System | Role |
|---|---|
| **NowCerts / Momentum** | Policy status (Active / Cancelled / Expired) — **wins** |
| **Supabase `canonical_policies`** | Nightly mirror of the book — can lag or have stale `rsg-import` rows |
| **`renewal_candidates`** | Event ledger rebuilt nightly from mirror + live insured flags |
| **`project_85_renewals`** | Working renewal queue (subset of eligible candidates) |
| **`portal_overrides`** | Human dismissals and corrections that survive rebuilds |
| **Zoho CRM** | CRM SOR — may lag; not fully wired to Hermes corrections yet |

---

## How to investigate

### Preferred: Hermes tool (one call)

```
investigate_policy(
  policy_number="990414352",
  client_name="Steven Prak",      # optional
  line_of_business="Personal Auto" # optional
)
```

Via MCP bridge: `mcp__rsg-hermes__investigate_policy`
Via Hermes API: `GET /api/hermes/investigate-policy?policy_number=...`
Via CLI dispatch: `investigate policy 990414352 for Steven Prak`

Returns JSON with `verdict`, `summary`, `issues`, `recommended_actions`, and
per-system snapshots.

### Manual fallback (when Hermes is down)

Run these in parallel:

1. **Live AMS** — `ams_search_insured` + policy detail for the number
2. **Mirror** — Supabase `canonical_policies` WHERE `policy_number = '...'`
3. **Renewal ledger** — `renewal_candidates` + `project_85_renewals`
4. **Overrides** — `portal_overrides` WHERE `entity_key = '...'`
5. **Insured** — `canonical_clients` by `nowcerts_insured_guid`

---

## Verdicts

| Verdict | Meaning |
|---|---|
| `outcome_a_stale_mirror` | AMS is Cancelled/Expired but mirror or worklist still shows Active/renewal |
| `outcome_b_ams_wrong` | Mirror says terminal but AMS still shows Active — verify before AMS writeback |
| `insured_inactive` | Live AMS says insured inactive but mirror has active policies |
| `no_mismatch` | Systems agree |
| `ambiguous` | Multiple AMS rows for same policy number — escalate |
| `not_found` | Policy missing everywhere |

---

## Report format

Always produce this structure for the human:

```
Policy Investigation: {policy_number}
Client: {name} · LOB: {lob}
Verdict: {verdict}

AMS (live):     {status} · exp {date} · ${premium}
Mirror:         {N} rows — {active_count} active (note sync_owner)
Renewal queue:  {on_project_85?} · candidates: {eligible|excluded}
Overrides:      {none | dismissed}

Issues:
  - ...

Recommended actions:
  1. ...
```

Sort mirror rows by `expiration_date` descending. Flag any `rsg-import` Active
row when a newer Cancelled term exists — that is the classic stale-mirror pattern.

---

## Correction playbook (approval-gated)

### Outcome A — stale mirror (most common)

AMS canceled; CRM/mirror still shows renewal.

1. `hermes --sync-canonical-book` — reconcile mirror from NowCerts
2. `hermes --renewal-refresh` — rebuild renewal candidates
3. If ghost persists: `POST /api/renewals/{id}/override` to dismiss
4. Update Zoho policy/deal status manually until Zoho sync is wired

**Never auto-run writes.** Present the plan; wait for `APPROVE` from L or ops.

### Outcome B — AMS wrong

Mirror/candidates say canceled but AMS still Active.

1. Verify in Momentum UI (human step)
2. If confirmed wrong: gated `POST /api/ams/policy` with `confirm=true` after approval
3. Then book sync + renewal refresh

### Book-wide drift

When the question is "is the book healthy?" not one policy:

```
book_sync_health()
```

via `GET /api/hermes/book-sync` — tombstones, count drift, carrier premium deltas.

---

## Example: Steven Prak 990414352

Input: `990414352, Steven Prak, Auto`

Expected finding (as of 2026-08-17):

- AMS: **Cancelled** (term exp 2026-12-09)
- Mirror: stale **Active** `rsg-import` row (exp 2026-12-10) + correct Cancelled row
- `project_85_renewals`: empty (correct)
- `renewal_candidates`: all **excluded** (`insured is not active`) but one ghost Active candidate
- Verdict: **outcome_a_stale_mirror**
- Fix: book sync → renewal refresh → Zoho update

---

## What you do NOT do

- Auto-dismiss renewals or push AMS writes without explicit approval
- Pick one of multiple AMS matches when ambiguous — escalate
- Edit `.env`, credentials, or production connection strings
- Force-push git or run destructive DB commands

---

## Related skills

- `renewal-desk` — executes approved renewal corrections
- `retention-risk-scout` — book-wide at-risk scan (not single-policy)
- `crm-fact-retriever` — general client/policy lookup without cross-system diff
- `nowcerts-skill` — AMS field reference
