# Plan — AMS policies → commission surface, in the cockpit

**Written 2026-07-26**, measured against the live system.
**Goal:** every commissionable policy reaches a surface where the work can be
done, and that surface lives in the cockpit.

---

## Decisions taken

| Question | Decision |
|---|---|
| Date floor | **Keep 2026+ only.** The `HERMES_COMMISSION_SINCE=2026-01-01` floor stays. |
| Definition of done | **Add a real `reconciled` status**, written when actual matches expected within tolerance. |
| First-build scope | **Read + reconcile in the cockpit.** Statement ingest stays in the standalone tracker. |

**Consequence of the date floor, and the rule that follows from it:** 37 active
policies carrying **$287,506** are deliberately off the surface. That is a
choice, so it must be *visible* — never a silent gap. Every total the cockpit
shows carries an "N active policies excluded by the 2026 floor" line. A silent
exclusion is indistinguishable from a broken pipe, and this codebase has already
been burned by exactly that (see the tombstone incident).

---

## Current state, measured

### The pipe mostly works

| Measure | Value |
|---|---|
| Active policies (live AMS) | 163 |
| **With a `commission_ledger` row** | **116** |
| Missing | **47** ($327,178) |
| — excluded by the 2026 floor | 37 ($287,506) — *by policy* |
| — in-window but absent | **6** — *a real gap, fix it* |
| — non-Active/Renewed status | 5 — correctly excluded |

### The ledger

108 rows. **No row has `reconciliation_status='reconciled'`.**

| Status | Rows | Expected | Actual |
|---|---|---|---|
| `pending` | 76 | $30,853 | $1,427 |
| `no_expected` | 12 | $3,371 | $5,194 |
| `overpaid` | 8 | $1,738 | $4,068 |
| `underpaid` | 5 | $1,436 | $1,327 |
| `rolled_up` | 3 | $671 | — |
| `canceled` | 3 | $766 | $945 |
| `missing_statement` | 1 | $15 | — |

The $30,853-expected / $1,427-actual gap on `pending` is **not a shortfall** —
it is 76 policies nobody has reconciled. Do not report it as money owed.

### Three defects

1. **The cockpit Commissions view renders empty.** `/api/commissions` defaults
   to `status=reconciled`; zero rows carry that status. Verified live:
   `{"commissions":[],"count":0}`.
2. **The "Commission Tracker" link is dead** — `cockpit.html` points at
   `rsg-commission-tracker-339396843209.us-east1.run.app`. Cloud Run was
   deleted; the tracker is tailnet-only on `:8446`.
3. **6 in-window active policies have no ledger row** despite qualifying.

### The architectural fact that shapes this plan

**Hermes has never written a reconciliation outcome.**

- `hermes/sync/commission_sync.py` writes the *expected* side only, and stamps
  `reconciliation_status='pending'` on insert. By design it never touches
  `actual_commission`, `delta`, `payment_received`, or the status on an
  existing row.
- `hermes/jobs/commission_reconciliation.py` — despite the name — **is
  read-only.** It parses a statement file, compares against a policy index, and
  posts discrepancies to chat. It contains no insert or update.

Every `overpaid` / `underpaid` / `no_expected` row in the ledger was written by
the **standalone tracker**, which reads and writes the same Supabase.

**So "reconcile in the cockpit" means Hermes gains its first actuals-write
path into money data.** That is the real weight of this build, and it is why
Phase 2 carries an approval gate rather than a save button.

### Slack is retired — what that does and does not change

**The transport already migrated.** `SlackNotifier` is a façade:
`hermes/integrations/slack_notifier.py` subclasses `TeamNotifier`, and
`team_notify.py` resolves the legacy Slack channel id to a **Nextcloud Talk**
room token and renders Block Kit to markdown. So the reconciler's discrepancy
post already lands in Talk, not Slack. No transport work is needed for this plan.

**But two things must change for commissions specifically:**

1. **The Slack-drop ingest premise is dead.** `commission-inbox` is built on
   "Lamar drops statement files into `#commission-inbox` from his phone." There
   is no Slack to drop into. Statement ingest therefore means **upload in the
   tracker UI** (where the `progressive_v1` / `next_v1` parsers already live),
   not a channel poll. Phase 3 inherits this: whatever replaces the tracker must
   carry a file-upload path, because there is no longer a chat side door.
2. **Chat is a notification, not a surface.** With no Slack, a discrepancy post
   to Talk is a *ping*, not a place to work. Every notification this plan emits
   must deep-link into the cockpit (`HERMES_PUBLIC_BASE_URL` +
   `/cockpit#commissions`), the way `task_notify._crm_link()` already does. The
   cockpit is where the work happens; Talk only says that work exists.

---

## Phase 0 — stop the surface lying (hours)

Small, unblocks everything, no schema change.

1. **`hermes/api.py::list_commissions_endpoint`** — keep `reconciled` as the
   default filter (per the decision), but return the worklist context with it:

   ```jsonc
   { "commissions": [...], "count": 0,
     "counts_by_status": { "pending": 76, "underpaid": 5, ... },
     "excluded_by_date_floor": { "policies": 37, "premium": 287506, "since": "2026-01-01" } }
   ```

2. **`hermes/webui/cockpit.html`** — replace the bare empty table with a real
   empty state: *"Nothing reconciled yet — 76 pending, 14 exceptions"* plus a
   jump to the worklist. A blank grid reads as "no data exists", which is false.

3. **Fix the tracker link** → the tailnet `:8446` origin (Cloud Run is deleted).

**Done when:** the Commissions view tells the truth on first load, including the
37 excluded policies.

---

## Phase 1 — close the real gap (small)

1. **Find the 6.** In-window, Active/Renewed, no ledger row. Diagnose before
   patching — it is either the `policy_number` join key or the status filter.
2. **Make the sync account for every policy it saw.** `run_commission_sync`
   returns counts today; extend to `{seeded, updated, skipped_pre_floor,
   skipped_status, skipped_no_premium}` and log it. Anything not seeded must be
   attributable to a named rule.
3. **Surface the reconciliation of the book itself** — active policies vs ledger
   rows — as a cockpit number, so a future regression is visible the next day
   rather than in three months.

**Done when:** `active_policies == in_ledger + skipped(with named reasons)`,
and that identity is asserted in a test.

---

## Phase 1b — finish the Slack retirement (parallel, independent)

Not commission-specific, but it lands on the same code and two of the residues
are live defects. Do it alongside Phase 1.

**Already done:** outbound reports route to Nextcloud Talk via the
`SlackNotifier` → `TeamNotifier` façade. Don't redo it.

**Live defects — a retired system is still load-bearing:**

| Where | Problem |
|---|---|
| `hermes/renewals/executor.py:793` | High-impact renewal-failure escalation **returns early unless `SLACK_BOT_TOKEN` is set.** The escalation posts to Talk, so gating it on a Slack credential means removing that stale token silently disables renewal failure alerts. |
| `hermes/jobs/revenue_sentinel.py:557` | `_missing_required_env()` lists `SLACK_BOT_TOKEN` as **required**. The sentinel refuses to run without a credential for a system it no longer posts to. |
| `hermes/api.py:2150–2243` | A real `slack_sdk.WebClient`, 503 when the token is missing. The only genuine Slack API path left. Decide: delete, or keep deliberately. |

`SLACK_BOT_TOKEN` is currently **SET** on the box, which is why none of this has
surfaced. It is a tripwire: the day that token is rotated out, renewal
escalations go quiet and the sentinel stops.

**Naming debt (cosmetic but actively confusing):**

- Talk rooms are addressed by **Slack channel ids** (`C0ANQUENX4P` → boss).
  Works, reads like a bug.
- `slack_registry` (3 rows) and the `SLACK_ALERT` action-type enum in
  `renewal_tracker.VALID_ACTION_TYPES`.
- **14 skills still instruct humans in Slack terms** — worst offenders
  `revenue-sentinel` (14 mentions), `commission-inbox` (10), `renewal-review`
  (5), `crm-intake-writer` (4).

**Order:** fix the two gating bugs first (they are silent failures), then rename,
then the skills.

---

---

## Phase 2 — the work surface (the real build)

A `commissions` view in the cockpit, alongside Pipeline. Reuses the existing
view registry, `api()` helper, and modal patterns — no new app, no new auth.

### 2a. Worklist

Default ordering is exception-first, because that is where the money is:

`underpaid` → `missing_statement` → `overpaid` → `no_expected` → `pending` →
`reconciled` (collapsed).

Columns: policy, client, carrier, LOB, gross premium, expected, actual, delta,
status, statement date. Filter by carrier, LOB, status, date range.

### 2b. The reconcile action — new, money-writing, gated

New endpoint, mirroring the sanctioned pattern already used by renewals:

```text
POST /api/commissions/{ledger_id}/reconcile
{ "actual_commission": 812.44, "statement_date": "2026-07-15",
  "statement_source": "progressive-2026-07", "approved_by": "<.net email>", "note": "..." }
```

Rules, non-negotiable because this is money:

- `approved_by` **must** validate against `agency_crm_users` via the existing
  `_require_users` — the same guard the quote push uses.
- Recompute `delta` server-side from `actual - expected`. Never trust a
  client-supplied delta.
- Derive the status server-side (state machine below). Never accept a
  caller-supplied status.
- Write an audit row (`commission_audits`) with before/after. The renewal
  executor's receipt discipline is the model.
- **Idempotent:** re-posting the same `(ledger_id, statement_source)` updates
  rather than double-applying.

### 2c. The status state machine

Single source of truth, pure and unit-tested — a `classify_reconciliation()`
alongside `classify_risk`, not logic scattered in the endpoint:

| Condition | Status |
|---|---|
| `actual` is null | `pending` |
| `expected` is null/0 and `actual` > 0 | `no_expected` |
| `abs(delta) <= $1.00` | **`reconciled`** ← the new terminal state |
| `actual < expected` beyond tolerance | `underpaid` |
| `actual > expected` beyond tolerance | `overpaid` |
| policy cancelled mid-term | `canceled` |
| statement expected but absent past due | `missing_statement` |

Tolerance from `commission-reconciliation`: ±$1.00 = matched; the severity bands
($1–50 low, $50–200 medium, $200–500 high, >$500 critical) drive worklist
ordering, not the status.

`rolled_up` is carrier-level aggregation and stays a manual designation.

### 2d. Batch reconcile

Select N rows → one approval → apply. Same gate, one confirmation. Reconciling
76 pending rows one modal at a time is how a surface goes unused.

---

## Phase 3 — subordinate the tracker

Once Phase 2 is live and has reconciled real statements:

- Narrow the tracker to **statement ingest + parsing** (`progressive_v1`,
  `next_v1`), which is where its parsers already live.
- Cockpit becomes the single work surface.
- Revisit full retirement only after a full month runs through the cockpit.

**This reverses a recorded decision** — "NowCerts→Supabase→standalone tracker
(the workspace, keep it); CRM cockpit Commissions = READ-ONLY reconciled-only."
The reversal is deliberate: the read-only split is what produced a blank view
nobody noticed. The cost is that two systems write the ledger during the
transition, which is exactly the failure mode that corrupted
`canonical_policies`. **Mitigation: the tracker must stop writing
`reconciliation_status` the day Phase 2 ships.** One writer per column.

---

## Money-safety rules (apply to every phase)

1. **Nothing auto-commits.** Every actuals write is approval-gated.
2. **One writer per column.** Hermes owns expected; after Phase 2 Hermes owns
   the reconciliation outcome; the tracker owns parsed statement lines.
3. **Never invent a rate.** No matching `commission_rules` row → `no_expected`,
   not an estimate. `v_rule_coverage` says which carriers are covered.
4. **Never overwrite an actual with a computed value.**
5. **Audit every write.** Before/after, actor, timestamp.

---

## Acceptance criteria

- [ ] Commissions view is non-empty on first load and states the 37 exclusions.
- [ ] `active_policies == in_ledger + skipped(named reasons)`, asserted in a test.
- [ ] The 6 in-window missing policies are seeded or have a documented reason.
- [ ] Reconciling a row with a valid `approved_by` sets `actual`, `delta`, and a
      server-derived status, and writes an audit row.
- [ ] `approved_by` with a `.com` address → 400.
- [ ] Re-posting the same reconcile is idempotent.
- [ ] `classify_reconciliation()` unit-tested per branch, including the ±$1 edge.
- [ ] Tracker link resolves.
- [ ] The tracker no longer writes `reconciliation_status`.
- [ ] Renewal escalation and the sentinel no longer gate on `SLACK_BOT_TOKEN`.
- [ ] Every chat notification deep-links to `/cockpit#commissions`.

## Open risks

- **Two writers during transition.** The known-worst failure mode here. Phase 3
  must not lag Phase 2.
- **`commission_reconciliation` is empty (0 rows)** — the discrepancy-tracking
  table has never been used. Phase 2 should decide whether to populate it or
  let `commission_ledger` + `commission_audits` carry the record.
- **`carrier_commission_profile` has 2 rows against 216 rules** — per-carrier
  terms are essentially unpopulated; expected-side accuracy depends on the AMS's
  own `agency_commission_amount`, present on 114 of 163 active policies.

## References

- `hermes/sync/commission_sync.py` — expected-side seeding, the date floor
- `hermes/jobs/commission_reconciliation.py` — the read-only analyzer
- `hermes/api.py` — `/api/commissions`, `/api/commission-rules`, `_require_users`
- `.claude/skills/commission-reconciliation/SKILL.md` — tolerance bands, workflow
- `.claude/skills/commission-inbox/SKILL.md` — statement intake + approval gate
