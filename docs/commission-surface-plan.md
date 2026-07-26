# Plan — AMS policies → commission surface, in the cockpit

**Written 2026-07-26**, measured against the live system.
**Goal:** every commissionable policy reaches a surface where the work can be
done, and that surface lives in the cockpit.

---

## Decisions taken

| Question | Decision |
|---|---|
| Date floor | **Keep 2026+ only** for proactive seeding — but see the collision below. |
| Definition of done | **Add a real `reconciled` status**, written when actual matches expected within tolerance. |
| First-build scope | **Read + reconcile + ingest in the cockpit.** Revised 2026-07-26: no reconciliation has ever been done by hand, so there is no legacy workflow to preserve and no two-writer window to manage. Build it right in one place. |

**Consequence of the date floor, and the rule that follows from it:** 37 active
policies carrying **$287,506** are deliberately off the surface. That is a
choice, so it must be *visible* — never a silent gap. Every total the cockpit
shows carries an "N active policies excluded by the 2026 floor" line. A silent
exclusion is indistinguishable from a broken pipe, and this codebase has already
been burned by exactly that (see the tombstone incident).

### ⚠ The floor collides with statement ingest

**Carrier statements do not respect our reporting window.** Of the 90 unmatched
statement lines already sitting in `commission_transactions`, **53 are dated
before 2026-01-01** and **74 reference a policy that exists in the book** — they
failed to match only because the 2026 floor meant no ledger row was ever seeded
for that policy.

Keeping the floor as-is means real money that actually arrived has nowhere to
land, every month, forever.

**Resolution — the floor governs seeding, not recording:**

- **Proactive seeding** stays 2026+. We do not speculatively create ledger rows
  for 37 old policies. The decision holds.
- **A statement line always lands.** If a parsed transaction matches a policy in
  the book but has no ledger row, ingest **creates** one, flagged
  `origin='statement'`, and it appears on the surface.

Money that arrived is a fact. Our reporting window is a preference. The fact wins.

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

**Hermes has never written a reconciliation outcome** — but something else has,
and it worked.

- `hermes/sync/commission_sync.py` writes the *expected* side only, and stamps
  `reconciliation_status='pending'` on insert. By design it never touches
  `actual_commission`, `delta`, `payment_received`, or the status on an
  existing row.
- `hermes/jobs/commission_reconciliation.py` — despite the name — **is
  read-only.** It parses a statement file, compares against a policy index, and
  posts discrepancies to chat. It contains no insert or update.

### The ingest model already exists and already ran

This is the most important finding, and it changes Phase 2 from "invent a
pipeline" to "re-home one that works."

`commission_transactions` (182 rows, one Progressive statement, 2026-07-08) is a
well-designed statement table:

| Column | Role |
|---|---|
| `statement_id` | → `commission_statements` |
| **`ledger_id`** | → **`commission_ledger`, already matched** |
| `policy_number`, `insured_name`, `carrier_name`, `lob`, `segment` | matching keys |
| `transaction_code` / `transaction_type` | `Renewal` / `New Business` / `Credit Endorsement` → `renewal` / `new` / `adjustment` |
| `transaction_date`, `month_key` | period (`202602`) |
| `gross_premium`, `commission_rate`, `commission_amount`, `is_negative` | the money |
| `fee_type`, `fee_amount` | fee drag |
| **`raw_row`** (jsonb) | **the original statement line, preserved** |

**And the rollup is arithmetically sound.** For all 30 matched ledger rows,
`commission_ledger.actual_commission` equals `SUM(commission_transactions.commission_amount)`
exactly — `mismatched_actual = 0` across every status. The overpaid / underpaid /
`no_expected` rows are **real outcomes from a real Progressive statement**, not
seed garbage. (The `statement_source='seed:canonical_policies:2026-07-07'` label
is stale and misleading — it records where the *row* came from, not where the
*actuals* came from. Phase 2 should correct it.)

So the model is: **a ledger row's `actual_commission` is a rollup of its
transactions.** Reconciliation is not hand-keying a number — it is
`SUM(commission_amount) GROUP BY ledger_id`, then classify. Build that, not a
manual entry form.

### The unmatched 90 — the actual ingest problem

| Measure | Value |
|---|---|
| Unmatched statement lines | **90** ($3,071.41) |
| Distinct policies | 18 |
| **Policy exists in the book** | **74** |
| Ledger row exists for that policy | 15 ← *a genuine matching bug* |
| Dated pre-2026 | 53 ← *the floor collision* |
| Negative lines (credits / chargebacks) | **30** |

Three distinct causes, three different fixes:

1. **~75 lines: no ledger row exists.** The policy is in the book; the 2026 floor
   meant it was never seeded. Fixed by "a statement line always lands" above.
2. **15 lines: the ledger row exists but wasn't linked.** A real matching defect.
   Diagnose the join before patching — likely `policy_number` normalization.
3. **30 negative lines** are credits, endorsement adjustments and chargebacks —
   **not underpayments.** They must roll into the same ledger row as a signed
   amount, never be classified as a discrepancy on their own.

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

## Phase 2 — the work surface, ingest included (the real build)

A `commissions` view in the cockpit alongside Pipeline. Reuses the existing view
registry, `api()` helper and modal patterns — no new app, no new auth.

**Reconciliation is a rollup, not data entry.** Nobody types an actual. You
upload a statement; lines match to ledger rows; the rollup sets the actual; the
classifier sets the status; a human approves the batch.

### 2a. Ingest — `POST /api/commission-statements`

Multipart upload → parse → stage → review → commit. Port the parser behaviour
from the tracker (`progressive_v1`, `next_v1`); keep the table design that
already works.

```text
upload  → commission_statements   (header: carrier, period, filename, hash)
        → commission_transactions_staging  (parsed lines, raw_row preserved)
        → MATCH each line to a ledger row
        → review card: matched / created / unmatched / negatives
        → APPROVE (approved_by, .net email)
        → commit to commission_transactions, roll up, classify
```

**Dedupe on a content hash** of the uploaded file. Re-uploading the same
statement must be a no-op, not a double-count. This is money.

**Matching ladder**, in order, stopping at first hit:

1. `policy_number` exact → ledger row
2. `policy_number` normalized (trim, case, strip punctuation) → ledger row
3. `policy_number` → `canonical_policies` → **create** a ledger row
   (`origin='statement'`), then link
4. no policy match → leave `ledger_id` null, surface as **unmatched** for a human

Step 3 is what fixes ~75 of the 90 unmatched lines. Step 2 is what fixes the 15.

**Never auto-resolve step 4.** An unmatched line is a question for a person, and
the review card is where they answer it.

### 2b. Rollup + classify

On commit, for each touched `ledger_id`:

```text
actual_commission = SUM(commission_transactions.commission_amount)   -- signed
delta             = actual_commission - expected_commission
reconciliation_status = classify_reconciliation(expected, actual, policy_state)
statement_date, statement_source = from the statement header
```

Signed sum: the 30 negative lines are credits and chargebacks and must reduce
the actual, never be read as a separate discrepancy.

Recompute — never accumulate. The actual is always derived fresh from the
transactions so a re-run or a corrected statement converges instead of drifting.

### 2c. The status state machine

Pure and unit-tested — a `classify_reconciliation()` alongside `classify_risk`,
not logic scattered in an endpoint:

| Condition | Status |
|---|---|
| no transactions yet | `pending` |
| `expected` null/0 and `actual` > 0 | `no_expected` |
| `abs(delta) <= $1.00` | **`reconciled`** ← the new terminal state |
| `actual < expected` beyond tolerance | `underpaid` |
| `actual > expected` beyond tolerance | `overpaid` |
| policy cancelled mid-term | `canceled` |
| statement expected but absent past due | `missing_statement` |

Severity bands ($1–50 low, $50–200 medium, $200–500 high, >$500 critical) drive
worklist ordering, not the status. `rolled_up` stays a manual designation for
carrier-level aggregation.

### 2d. Worklist

Exception-first, because that is where the money is:

`underpaid` → `missing_statement` → `unmatched lines` → `overpaid` →
`no_expected` → `pending` → `reconciled` (collapsed).

Columns: policy, client, carrier, LOB, gross premium, expected, actual, delta,
status, statement date. Filter by carrier, LOB, status, period. Drill into a row
to see its transactions, including `raw_row` — the statement line is the
evidence, and a disputed commission is won with it.

### 2e. Manual override

Rare but necessary: a carrier pays outside a statement, or a line is wrong.
`POST /api/commissions/{ledger_id}/adjust` writes an **adjustment transaction**,
not a direct edit to the ledger. The rollup stays the only writer of
`actual_commission`. Same `approved_by` gate, and an audit row.

### Money rules for this phase

- `approved_by` validates against `agency_crm_users` via `_require_users`.
- Server derives `delta` and status. Never trust a client-supplied value.
- Idempotent: same file hash, or same `(ledger_id, statement_source)`, updates
  rather than double-applies.
- Every commit writes an audit row with before/after.
- Nothing commits without an explicit approval — the review card is the gate.

## Persistence — what backs this, and the gaps

**Verified 2026-07-26. The schema is already there and it is well designed.**
This plan adds columns and constraints; it does not add tables.

### The chain that exists

```text
commission_ingest_batches   UNIQUE(content_hash) · ingest_status CHECK
  └─ commission_transactions_staging   batch_id FK ON DELETE CASCADE
       └─ commission_statements        header + carrier-stated totals
            └─ commission_transactions  statement_id FK CASCADE · ledger_id FK · raw_row jsonb
                 └─ commission_ledger   the reconciled position
```

**Dedupe is already enforced in the database.** `commission_ingest_batches`
has `UNIQUE (content_hash)` — re-uploading the same file cannot double-count.

**The review workflow already has a state machine.**
`ingest_status CHECK IN ('pending_review','approved','rejected','committed','needs_mapping','skipped','error')`
— the approval gate this plan needs is already modelled. Use it; don't invent one.

`commission_ingest_batches` also carries the crosscheck fields that catch a bad
parse before it commits: `parsed_total_premium` / `parsed_total_commission` vs
`stated_total_premium` / `stated_total_commission` / `stated_net_due`, plus
`crosscheck_ok` and `flags` jsonb. **Wire the crosscheck — a statement whose
parsed total disagrees with the carrier's own stated total must not commit.**

Indexes already cover the query paths: ledger on `policy_number`, `carrier_name`,
`delta`, `statement_date`, `reconciliation_status`; transactions on `ledger_id`,
`statement_id`, `policy_number`, `carrier_name`, `month_key`, `transaction_type`.

### DDL gaps — one small migration

```sql
-- 1. Flag rows a statement created, so the 2026-floor exclusion stays legible.
alter table commission_ledger add column if not exists origin text
  default 'seed' check (origin in ('seed','statement','manual'));

-- 2. reconciliation_status is currently FREE TEXT — a typo is a silent bug on
--    money data. Pin it, including the new terminal state.
alter table commission_ledger add constraint commission_ledger_status_check
  check (reconciliation_status in (
    'pending','reconciled','underpaid','overpaid','no_expected',
    'rolled_up','canceled','missing_statement'));

-- 3. Stop a line committing twice. The batch hash guards whole files; this
--    guards a line arriving via two different batches.
create unique index if not exists commission_transactions_line_uq
  on commission_transactions (statement_id, policy_number, transaction_code,
                              transaction_date, commission_amount);

-- 4. Slack is retired; these three columns name a dead system.
alter table commission_ingest_batches rename column slack_channel   to source_channel;
alter table commission_ingest_batches rename column slack_file_id   to source_file_id;
alter table commission_ingest_batches rename column slack_message_ts to source_ref;

-- 5. Espo is retired.
alter table commission_ledger drop column if exists espocrm_opportunity_id;
alter table commission_ledger drop column if exists espocrm_policy_id;   -- drops idx_commission_ledger_espocrm_policy too
```

Apply via `supabase/migrations/`, not the dashboard, so it is reviewable.

### Audit

`commission_audits` exists but is a **different grain** — keyed by
`statement_id` + policy + `snapshot_month`, with its own `commission_status`
enum (`PENDING | MATCHED | DISCREPANCY | ESCALATED | RECONCILED`). It is a
per-policy-per-month audit, not a write log.

Decide in Phase 2 and write it down: either reuse it for the reconcile trail, or
add a narrow `commission_write_log`. **Do not leave it ambiguous** — an audit
trail nobody can find is the same as none.

---

## Analytics — already built, just starved

**The dimensional layer exists and is sourced from the right grain** —
`commission_transactions`, not the ledger. That is the correct choice: per-carrier,
per-LOB and per-month breakdowns need line-level data.

| View | Source | Rows today | Answers |
|---|---|---|---|
| `v_comm_by_carrier` | transactions | 1 | **commission per carrier** |
| `v_comm_by_line` | transactions | 3 | **commission per line of business** |
| `v_commission_by_carrier_month` | transactions | 14 | carrier × month trend |
| `v_fee_drag` | transactions | 1 | fees eroding commission |
| `v_reconciliation_summary` | transactions | 1 | position rollup |
| `v_reconciliation_exceptions` | transactions | 44 | what needs chasing |
| `commission_ytd` | ledger | 15 | year-to-date |
| `chargeback_risk_dashboard` | ledger | — | unearned exposure |
| `commission_parity_report` | — | — | parity checks |

**They are thin only because one statement has ever been ingested.** They do not
need building — they need feeding. Every statement that lands through Phase 2
fills them automatically.

That is the direct answer to "reference total commission, break down per carrier,
query highest commissions and highest LOB": the queries already exist. Ingest is
the bottleneck.

### What to add

1. **Expose them.** `GET /api/commissions/analytics?dimension=carrier|lob|month`
   reading the views, plus totals. Nothing in the API surfaces these today.
2. **A cockpit Commissions summary strip** above the worklist: total commission
   YTD, top carrier, top LOB, outstanding delta, fee drag.
3. **Reconcile the two grains.** `commission_ytd` is ledger-sourced while the
   breakdowns are transaction-sourced; they can disagree. Pick one as the
   headline (transactions — it is the grain that survives a re-run) and say so.
4. **Feed the daily snapshot.** Once statements land regularly, add commission
   totals to `agency_snapshots` so commission earns a trend line the way the book
   now does.

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
- [ ] Re-uploading the same statement file is a no-op (batch `content_hash`).
- [ ] A statement whose parsed totals disagree with the carrier's stated totals
      does NOT commit — `crosscheck_ok` gates it.
- [ ] A statement line for a policy in the book with no ledger row CREATES one
      (`origin='statement'`) rather than landing unmatched.
- [ ] Negative lines reduce the rollup; they never classify as a discrepancy.
- [ ] `reconciliation_status` is constrained — an invalid value is rejected by
      the database, not just by convention.
- [ ] `GET /api/commissions/analytics` returns per-carrier and per-LOB totals.
- [ ] The audit grain is decided and documented.

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
