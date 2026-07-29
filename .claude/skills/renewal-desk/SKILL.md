---
name: renewal-desk
description: >
  Hermes-side renewal EXECUTOR for RSG — the one door that performs the actual
  AMS writes for renewals. Work is staged as human-approved `outbound_sync_queue`
  rows (`object_type='renewal'`) and drained by the renewal executor against
  NowCerts, with a verified receipt per job. Also covers the desk's own data:
  correcting a renewal field or removing a renewal from the worklist — durable,
  named, reversible, and never an AMS write. Triggers on "renewal desk", "work the
  renewals", "execute renewal", "process renewal", "renew {client}", "fix/correct
  this renewal", "take this renewal off the list", or a renewal action raised in
  the portal or Nextcloud Talk. Revenue-critical — retention
  60.78% as of 2026-07-26, target 75%+. Complements retention-risk-scout and
  gretchen-daily-queue (tells Gretchen what to do); this one DOES it.
---

# Renewal Desk (Hermes = the executor)

The sanctioned write door for renewals. Grounded in
[hermes/renewals/executor.py](../../../hermes/renewals/executor.py) — the
**Hermes Job Contract v2** — and verified against live data 2026-07-26.

> **This skill was rewritten 2026-07-26.** The prior version described an
> EspoCRM-based desk (`espocrm.create_opportunity`, field-casing rules, a
> `/walker/*` Phase-1 API). EspoCRM is retired and none of that applies. If you
> are following instructions about Espo field casing or `crm-manager`, you are
> reading a stale copy.

---

## Role split

- **The Renewals screen in the RSG Agency Portal** (`:8447`, `GET
  /api/command-center/renewals`) is the workstation. Gretchen and Lamar work
  renewals there. It reads, drafts, and — since 2026-07-29 — **corrects and
  removes** renewal records. None of that reaches NowCerts. (The Hermes cockpit
  at `/cockpit` still serves the same worklist off `GET /api/renewals`, the
  candidate ledger; the portal is the surface people actually use.)
- **Hermes — this skill — is the only thing that writes to the AMS.** Every
  mutation is staged as an approved queue row and executed by the renewal
  executor. **Hermes never talks to a client.**
- **Gretchen is the only hands that touch clients.** Hermes drafts; she sends.

---

## Ground truth (verified live 2026-07-26)

| Table | Rows | Role |
|---|---|---|
| `renewal_candidates` | 475 | **The event ledger.** Every renewal event, with eligibility state and lineage. Never delete from it. |
| `project_85_renewals` | 48 | The working queue — a *projection* of eligible events. `payload.renewal_id` must resolve here or the job is rejected. |
| `renewal_actions` | 5 | The audit trail. One row per executed action. |
| `renewal_execution_receipts` | 2 | Before/after proof per job. **Two receipts exist total.** |
| `outbound_sync_queue` | 5 | The approval gate. |
| `canonical_policies` | 618 (163 flagged active) | Policy mirror — **see the data warning below.** |

### The executor contract

A job is only eligible when **every** field holds:

```
object_type   = 'renewal'
destination_system = 'nowcerts'
status        = 'queued'
approved_by   IS NOT NULL
approved_at   IS NOT NULL
payload.renewal_id      resolves in project_85_renewals
payload.action          ∈ ACTIONS
payload.expected_result IS SET
```

**The four authorized actions** — there are no others:

| Action | Mutates AMS? |
|---|---|
| `request_terms` | yes |
| `prepare_options` | **no** — the only non-mutating action |
| `client_follow_up` | yes |
| `update_ams` | yes — **high-impact**, failures escalate |

Per-job procedure, in order: **claim → validate → read NowCerts → compare →
stop on ambiguity → execute → re-read to verify → write receipt → mark the queue
row completed/failed → record in `renewal_actions`.** Do not skip the re-read;
the receipt's `after_state` and `verified` flag come from it.

### The scheduler is live — approved means it fires

`rsg-hermes-scheduler` runs on the box with `SCHEDULER_ENABLED=true` and cycles
**every 5 minutes**, draining approved renewal / intake / quote / casework /
opportunity-writeback jobs. Setting `approved_by` is the act of authorizing a
live AMS write, not a staging step. Get the human's word first, in the same
conversation, and use their real `agency_crm_users` identity.

Preview without committing: `--renewal-executor --dry-run`.

---

## Correcting the desk's data (2026-07-29)

Renewal data is not typed by anyone — candidates are rebuilt from the live book
nightly and `project_85_renewals` is re-projected from them. So a value written
straight onto a row was replaced by morning, which is why a premium that came
over wrong stayed wrong. The desk can now fix it, and the fix holds.

| Surface | Endpoint | What it does |
|---|---|---|
| Correct a field | `POST /api/renewals/{id}/override` | Records a named override **and** writes the value onto the row |
| Remove from the worklist | `DELETE /api/renewals/{id}` | Marks the renewal removed and excludes the events under it |
| Undo either | `DELETE /api/renewals/overrides/{override_id}?approved_by=` | Restores the source value; on a removal, puts the renewal back |
| See what's corrected | `GET /api/renewals/overrides?status=` | Active corrections, removals included |

- **Correctable:** `client_name`, `premium_current`, `premium_renewal`,
  `risk_status`, `expiration_date`, `last_contact_date`, `ai_strategy_notes`.
  Nothing else. `policy_number` is the correction's own key and the AMS match;
  `increase_percentage` is generated by Postgres from the two premiums.
- **Keyed by policy number**, not row id, so a correction survives a re-projection
  or a re-seed. The rebuild (`_project_eligible`) applies overrides before it
  upserts — that is what stops the nightly run from reverting a fix.
- **Removal is an exclusion, never a DELETE.** `renewal_actions` cascades off
  `project_85_renewals`, so deleting a row would erase the record of the work
  done on that renewal — and the refresh would rebuild it from the same policy
  anyway. The event underneath is excluded too; that is what makes it stick.
- **None of this touches NowCerts.** A correction fixes what the agency sees
  today. The AMS is still fixed by hand, and the override retires itself once
  the two agree. Renewals have no push-to-AMS path — clients and policies do.
- Corrected rows carry `_overridden` (`{field: what the source said}`). **If you
  quote a renewal premium, check it** — a corrected number is a person's
  decision, not the book's word, and saying which is which is the point.

---

## Data warnings — read before quoting any number

- **`canonical_policies` is under a two-writer freeze.** 48 rows carry a
  tombstone status of the literal form `Inactive: not in NowCerts 2026-07-21`
  (43 rows) and `...2026-07-23` (5 rows) — written by the `rsg-import` pg_cron
  path, which pulled `is_quote=false` only and tombstoned everything it didn't
  see. It was **disabled 2026-07-24** and needs one writer before re-enabling.
  A further 5 rows are `status='Expired'` but `active=true`, and 2 are
  `'Renewed'` but `active=true`. Of the 48 tombstoned rows, **28 still exist in
  the live AMS (24 active) and 20 are genuinely gone** (checked 2026-07-26).
  Reading live (`HERMES_AMS_LIVE_READS`) resolves it; the mirror does not.
  **Say which source a premium figure came from.**
- **`renewal_candidates` is an event ledger, not a to-do list.** 475 rows is the
  history of renewal events, not 475 open renewals. Filter to
  `in_working_queue` / the forward window before showing a count to anyone. Rows
  a human removed from the worklist read `eligibility_state='excluded'` with the
  reason naming who removed it — that is a decision, not a data error.
- **Two known labelling bugs:** the `segment` column and `derive_lineage_id`'s
  LOB segment both mislabel personal vs commercial. Don't route on `segment`
  alone.
- Renewal windows are forward-only: **120 days commercial, 30 days personal.**

---

## Cadence

Segments (`hermes/renewals/cadence_config.py`): `commercial_small`,
`commercial_mid`, `benefits`, plus the auto/6-month light path.

Touch fields on a renewal: `touch_early_sent` (T-90 / T-45 / T-40),
`touch_mid_sent` (T-60), `touch_decision_sent` (T-30 / T-15 / light confirm),
`welcome_sent` (on bound).

Cadence sending is gated by `RENEWAL_CADENCE_ENABLED` — **check it before
promising a client touch will go out.**

---

## Execution workflow

### 1. Resolve the client — search before you touch
`mcp__rsg-hermes__ams_search_insured` by name / email / FEIN → the insured
GUID. Then confirm the renewal event in `renewal_candidates` /
`project_85_renewals`. **One confirmed match before any mutation.** Multiple or
zero matches → ask one clarifying question and stop. The book has known
duplicate clients; acting on the wrong record is worse than a delay.

### 2. Pull the current picture
`mcp__rsg-hermes__list_renewals` for the forward window, and the canonical book
for the current term (premium, carrier, effective/expiration, `renewed_policy`
lineage). Check `renewal_actions` for what has already been done — never
duplicate a touch.

### 3. Classify against the approval gate
Split the ask into non-mutating vs approval-required. See below.

### 4. State intent, then stage
Say exactly what will change and why, name the approver, then insert the queue
row with `approved_by` / `approved_at`. Report the queue id.

### 5. Report in plain English
Gretchen-facing output is plain English, zero jargon, zero field names — mirror
`gretchen-daily-queue`. Material or financial outcomes go to Lamar, one line.

---

## Approval gate

**NON-MUTATING (safe to run and show):**
- `prepare_options` — assembling renewal options. Touches nothing in the AMS.
- Any read: candidates, policies, prior actions, receipts.

**APPROVAL-REQUIRED (stage it, name the approver, wait for an explicit OK):**
- `request_terms`, `client_follow_up`, `update_ams` — all three reach NowCerts.
- Anything touching premium or policy lifecycle (status / effective / expiration).
- Any client-facing send.

**CORRECTIONS — a different gate.** Correcting or removing a renewal in the
portal reaches no AMS record, so it does not go through the queue. It still
needs a named person (`approved_by` / `deleted_by`, a real `agency_crm_users`
identity) because it is a decision that outranks the book, and it is logged and
reversible. Never make one on Hermes's own initiative: bring the discrepancy to
whoever owns the renewal and let them make the call.

**NEVER, regardless of approval:**
- Creating or editing a **policy** from the CRM side. Policies arrive by carrier
  download or are entered in the AMS by a human.
- **Correcting a renewal to make a number look right.** A correction says the
  source is wrong; if the source is right and the renewal is unwanted, remove it,
  and if the AMS is wrong, it also needs fixing by hand.
- **Overwriting a populated AMS field.** Fill-blank only, and only after
  confirming the field is genuinely empty.
- Resolving a data conflict. Flag it to a human.
- Bypassing the queue with a direct NowCerts call.

---

## Error handling — fail closed, never fall back

| Situation | Action |
|---|---|
| Ambiguous client or vague ask | One clarifying line, then STOP. Don't guess. |
| `payload.renewal_id` doesn't resolve in `project_85_renewals` | Job is invalid. Fix the projection; don't hand-edit the payload to make it pass. |
| MCP tool or NowCerts unavailable | STOP, report to `#systems-check`. **Never** fall back to raw REST, the DB, or the web UI. |
| Write "succeeds" but the re-read disagrees | Receipt is `verified=false`. Flag it. Do not retry blindly. |
| AMS field already populated | Do not write. Flag the conflict. |
| The desk's number disagrees with the AMS | Check `_overridden` before calling it a sync bug — someone may have corrected it deliberately. |
| A renewal you expect is missing from the list | Check `GET /api/renewals/overrides` for a removal before re-deriving the pipeline. |
| Duplicate insured found | Resolve to the master record first; if unsure, flag. |
| Asked to change a policy from the CRM | Refuse; explain it must be done in the AMS. |
| Job failed | It is dead-lettered after backoff by the scheduler's retry pass and alerted to `#systems-check`. Read `last_error` before requeuing. |

---

## Known gaps

- **Only 2 execution receipts exist.** This executor has barely run in
  production. Treat every run as a first run: dry-run, then watch the receipt.
- **`renewal_actions` has 5 rows** — the audit trail is nearly empty, so
  "has this client been touched?" cannot yet be answered from data alone.
- **`project_85_renewals` (48) vs `renewal_candidates` (475)** — the projection
  is the queue. If a renewal you expect isn't in the 48, the eligibility rule
  excluded it; check `eligibility_state` / `eligibility_reason` before assuming
  the pipeline dropped it.
- **`momentum_mcp_client.py` is still imported** by the executor. "Momentum" is
  the legacy name for the NowCerts API; it is not a second system.
- Renewal opportunities in the CRM pipeline (`opportunity_type='Renewals'`,
  4 rows) are **not joined** to this ledger. See `hermes-crm-writer`.

---

## Notes

- Gretchen-facing pings are direct, never broadcast.
- **Medicare is excluded from all automated client touches.** Never
  age-reference a client in writing.
- Hermes never impersonates a user. Tasks and cases are owned by Gretchen or Lamar.
- Every write is auditable through the receipt chain — never take an action that
  dodges it.

## References

- `hermes/renewals/executor.py` — the job contract, actions, receipts
- `hermes/renewals/eligibility.py` — what makes an event a candidate
- `hermes/renewals/corrections.py` — what may be corrected, and how a
  correction survives the nightly rebuild
- `hermes/renewals/candidate_refresh.py` — the rebuild that applies them
- `hermes/renewals/cadence_config.py` — segments, touch timing, templates
- `retention-risk-scout` — finds who is at risk
- `gretchen-daily-queue` — surfaces the day's actions
- `hermes-crm-writer` — the CRM pipeline side
