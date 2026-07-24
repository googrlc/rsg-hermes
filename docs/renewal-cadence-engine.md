# Renewal Cadence Engine

Segment-aware renewal communication: right touch, right timing, per policy
segment — drafted by Hermes, approved and sent by a human, tracked on the
CRM Renewal record. This document covers the **deterministic core** that is
built and tested; the human-in-the-loop delivery layers that sit on top are
listed under [Remaining phases](#remaining-phases).

Source of the design: `BRIEF — Renewal Cadence Engine v2` (2026-07-09).

## What ships in this slice (Build Order step 2)

Pure, unit-tested Python. No LLM, no network.

| File | Role |
|---|---|
| `hermes/renewals/cadence_config.py` | Versioned config: segment cutoffs, term thresholds, per-segment touch schedule, LOB vocabulary, template names, kill switch. **Tune numbers here.** |
| `hermes/renewals/cadence.py` | `classify_segment()` + `due_touches()` + the 6-mo-auto `auto_6mo_cycle()` rotation. Logic only. |
| `tests/test_renewal_cadence.py` | Acceptance Criteria #1 (classifier) and #2 (idempotency / grace window). |

### Segmentation

A policy lands in exactly one segment, or is **excluded** (`None`). Rules are
evaluated in order (`classify_segment`):

1. **Medicare LOB → excluded.** No touches, no cards, ever. Excluded at the
   classifier, not at send time.
2. **Group benefits LOB → `benefits`**, regardless of premium.
3. **Personal auto on a 6-month term → `auto_6mo`.** Term = `expiration_date −
   effective_date < 270 days`.
4. **Personal lines, 12-month → `personal_12mo`.**
5. **Commercial → `commercial_small` / `commercial_mid`**, split on **account-level
   total active premium** (`≤ 5000` → small, else mid). A multi-policy account is
   scored as a relationship: four $3K policies is a $12K account and earns the mid
   cadence for *all* of its policies. The caller supplies
   `account_active_premium` (sum of active policies for the client).

### Cadence & touch fields

Touch day-thresholds are tuned in `CADENCE`; `TOUCH_SPEC` maps each to its
the CRM date-field slot and template (a drift guard fails the import if the two
disagree).

| Segment | Touches (days before x-date) |
|---|---|
| `auto_6mo` | 30 (full review *or* light confirm — see rotation) |
| `personal_12mo` | 45, 15 |
| `commercial_small` | 40, 15 |
| `commercial_mid` | 90, 60, 30 |
| `benefits` | 90, 60, 30 |

Four Renewal date fields track state, one per phase:
`touch_early_sent` (T-90/45/40), `touch_mid_sent` (T-60),
`touch_decision_sent` (T-30/15/light), `welcome_sent` (on bound).

### Idempotency & grace window

`due_touches()` fires a touch only when **all** hold:

- its field slot is empty (stamped on card post → never fires twice),
- today has reached the threshold (`days_until ≤ T`),
- it is not stale (`days_until ≥ T − grace`, grace = 10 days).

So a 90-day heads-up that is 40 days late is **skipped, not sent** — a proactive
email on a stale renewal date is worse than silence. The engine jumps to the
next in-window touch instead.

### 6-month auto rotation

One full market review per policy per ~12 months. `auto_6mo_cycle()` returns
`full_review` when no full review was logged within 300 days, else
`light_confirm`; the T-30 touch resolves its template accordingly.

## the CRM changes required before enabling

Add four **date** fields to the Renewal entity (Admin → Field Manager → Quick
Repair and Rebuild), then verify the exact API names against the entity:
`touch_early_sent`, `touch_mid_sent`, `touch_decision_sent`, `welcome_sent`.

## Enabling

`RENEWAL_CADENCE_ENABLED` is a kill switch (default OFF). Until it is truthy and
the 8 templates + first live cards are approved, the scheduler plans but posts
nothing.

## Remaining phases

These depend on external systems (the CRM Admin, Slack, live sync data, deploys)
and are intentionally **not** in this slice:

- **Phase 0 blockers** — deploy pending patch, resume `rsg-sync-daily`, spot-check
  expiration dates. Nothing proactive ships until sync data is trustworthy.
- Touch scheduler daily job (6:00 AM ET, after Policy Sync v2), reading the
  segment + due touches over live expirations, with a 48-hour stale-data guard.
- 8 Jinja2 templates in `hermes/renewals/templates/` — Hermes drafts, Lamar
  approves the copy.
- Slack approval cards + Gretchen check sheets (DM routing), welcome-on-bound
  trigger, weekly renewal digest.
- `crm-field-reference` `modules/renewals.md` documenting the full Renewal
  entity incl. these touch fields.
- Dry-run report over live data → Lamar reviews before the flag flips on.
