---
name: retention-risk-scout
description: >
  Grades RSG's eligible renewal events for retention urgency and produces a
  prioritized at-risk list, sourced from Supabase `renewal_candidates` /
  `project_85_renewals` and the canonical book. Triggers on "retention scan",
  "who's at risk", "risk report", "retention check", "who might cancel", or the
  Wednesday 9am ET weekly schedule. Revenue-critical — retention baseline 54.92%,
  target 75%+. Finds the risk; `renewal-desk` executes on it and
  `gretchen-daily-queue` puts it on someone's plate.
---

# Retention Risk Scout

Finds who is about to walk, early enough to do something about it.

> **Rewritten 2026-07-26.** The prior version minted a NowCerts token from
> `op://claudeclaw/...`, pulled the renewal pipeline out of EspoCRM, and scored
> clients 0–100 on an invented additive model. EspoCRM is retired, those vault
> paths are stale, and **that scoring model has never existed in the code.** Use
> what is below.

---

## The real classifier — do not invent a score

Risk grading lives in
[hermes/operations/renewal_classifier.py](../../../hermes/operations/renewal_classifier.py)
(`classify_risk`). It is pure, unit-tested, and the only sanctioned model.
There is no 0–100 score and no additive point table.

**Valid statuses** (`renewal_tracker.VALID_RISK_STATUSES`):
`SAFE` · `AT_RISK` · `CRITICAL` · `RENEWED` · `LAPSED`

In practice the classifier only emits the first three — terminal lifecycle
states are excluded upstream by the eligibility engine and never reach it.

**Evaluation order — premium first, then timing:**

| Condition | Result |
|---|---|
| Renewal quote exists and increase **> 15%** | `CRITICAL` |
| Renewal quote exists and increase **≥ 5%** | `AT_RISK` |
| Past x-date, or **≤ 30 days** to it | `CRITICAL` |
| **31–90 days** to x-date | `AT_RISK` |
| **> 90 days**, or no expiration on file | `SAFE` |

Increase % is derived as `(premium_renewal − premium_current) / premium_current × 100`.
`project_85_renewals.increase_percentage` is a **GENERATED column — never write
it.** Only `premium_current` / `premium_renewal` feed it.

**Urgency only.** This model never decides *whether* something is a renewal —
`hermes/renewals/eligibility.py` owns membership. Re-grading writes
`renewal_candidates.risk_status` and mirrors onto `project_85_renewals` by
`policy_number`.

---

## Ground truth (verified live 2026-07-26)

| Source | Rows | Use |
|---|---|---|
| `renewal_candidates` | 475 | The event ledger. Filter `eligibility_state='eligible'`. |
| `project_85_renewals` | 48 | The working queue — what the scan actually reports on. |
| `canonical_clients` | 415 | Client mirror. |
| `canonical_policies` | 618 (163 flagged active) | Premium + LOB. **Contaminated — see below.** |
| `renewal_actions` | 5 | Outreach history. |
| `agency_snapshots` | **1** | Retention history. **See below.** |

**Current standing** (48 renewals in the working queue):

| Status | Count | Premium at stake |
|---|---|---|
| `CRITICAL` | 10 | $30,727 |
| `AT_RISK` | 25 | $64,137 |
| `SAFE` | 13 | $76,926 |

35 of 48 are CRITICAL or AT_RISK, carrying **~$94.9K** of premium. That is the
headline number, and it is the one to lead a report with.

---

## Two data warnings you must carry into every report

1. **`canonical_policies` is contaminated.** 48 rows carry a literal tombstone
   status (`Inactive: not in NowCerts 2026-07-21` ×43, `...2026-07-23` ×5),
   written by the `rsg-import` pg_cron path before it was disabled 2026-07-24
   for tombstoning anything it didn't see. Another 5 are `Expired` with
   `active=true`, and 2 are `Renewed` with `active=true`. The tombstoned rows
   are `active=false`, so they are **excluded** from any active-premium total —
   the contamination **suppresses** the book by **$378,575** rather than
   inflating it. State the caveat; don't silently publish a contaminated number.

2. **There is exactly one retention snapshot, and it is a manual baseline.**
   `agency_snapshots` holds a single row: `2026-03-31`, $385,000 active premium,
   104 policies, 81 clients, **retention 54.92%**, `source='manual'`. The famous
   54.92% is that hand-entered baseline from nearly four months ago — it is not
   a computed, refreshed metric. **Week-over-week retention movement cannot be
   reported**, because no second snapshot exists. Say "no trend available yet"
   rather than implying movement. Writing a fresh snapshot is the fix, and it is
   the single highest-value thing this skill could gain.

---

## How to run a scan

1. **Pull the eligible set.** `mcp__rsg-hermes__retention_scan`, or read
   `renewal_candidates` filtered to `eligibility_state='eligible'` and the
   forward window (**120 days commercial, 30 days personal**).
2. **Re-grade** with `classify_risk` semantics above. Don't hand-score.
3. **Attach premium and client context** from `canonical_policies` /
   `canonical_clients`, applying the contamination caveat.
4. **Check what has already been done** — `renewal_actions` for prior touches.
   With only 5 rows there, assume most clients have *no* recorded outreach
   rather than assuming they've been contacted.
5. **Rank by premium at risk, not by count.** A single CRITICAL commercial
   account outranks six small personal-lines ones.
6. **Hand off**, don't just report: CRITICAL rows go to `renewal-desk` for a
   staged, approved action; day-to-day steps go to `gretchen-daily-queue`.

RSG's policy_number format is `Client | Line of Business | Number` — parse the
LOB out of it (`save_list.parse_lob`) rather than guessing from the client name.

---

## Save-list

[hermes/operations/save_list.py](../../../hermes/operations/save_list.py) builds
the actionable subset: the highest-premium at-risk renewals due in the next N
days, each staged as a reviewable DRAFT in `renewal_outreach_drafts`. Only
`CRITICAL` and `AT_RISK` make the list, sorted by premium descending.

**Nothing is ever auto-sent.** Draft bodies are deterministic templates; sending
is a manual human step. Never describe a draft as "sent".

---

## Reporting

Lead with the number and the decision. Concise, plain English.

```text
RETENTION SCAN — <date>
At risk: <n> renewals, $<premium> of premium
  CRITICAL <n> ($<prem>) — contact now
  AT_RISK  <n> ($<prem>) — outreach this week
Top exposure:
  1. <client> — <LOB> — $<prem> — <days> to x-date — <why>
  ...
Baseline: 54.92% (manual snapshot 2026-03-31; no newer snapshot — no trend available)
Caveat: canonical_policies carries 48 tombstoned rows from the disabled import path
```

Medicare clients are **excluded from all automated client touches**. Never
age-reference a client in writing.

---

## Known gaps

- **No retention trend.** One snapshot, manual, four months old.
- **`renewal_actions` has 5 rows**, so "no outreach logged" is the default state
  for nearly every client and is not by itself a risk signal yet.
- **The `segment` column mislabels personal vs commercial**, as does
  `derive_lineage_id`'s LOB segment. Don't route on `segment` alone.
- **No cross-sell / single-policy signal is wired.** The old model scored it;
  nothing computes it today.
- The classifier's docstring still names the Espo-era `crm_commissions` table.
  The **code** reads `renewal_candidates` — the comment is stale, the behavior
  is correct.

## References

- `hermes/operations/renewal_classifier.py` — `classify_risk`, the only model
- `hermes/operations/renewal_tracker.py` — valid statuses and action types
- `hermes/operations/save_list.py` — the save-list + outreach drafts
- `hermes/renewals/eligibility.py` — what counts as a renewal at all
- `renewal-desk` — executes on what this finds
- `gretchen-daily-queue` — turns it into someone's day
