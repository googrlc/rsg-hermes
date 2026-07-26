---
name: hermes-crm-writer
description: Write and move pipeline opportunities in RSG's Hermes CRM — the Supabase `opportunities` table worked through the `rsg-hermes-api` endpoints (`POST /api/opportunities`, `PATCH`, `/stage`, `/send-to-nowcerts`). The counterpart to the AMS write path: NowCerts owns who a client IS and what they have BOUND, Hermes owns where the deal STANDS. Use whenever an intake, transcript, submission, cross-sell, or renewal review produces a per-LOB opportunity, or when a deal needs to move stage, get an owner, get a premium, or be marked won/lost. Never writes the pipeline with raw SQL.
---

# Hermes CRM Writer

The sanctioned write path into RSG's sales pipeline. Grounded in the live
`opportunities` table and the live API — **no stage name, column, or tool in
this skill comes from an older design doc.**

Its companion skills:

| Skill | Does |
|---|---|
| `crm-intake-writer` | **Authors** the payload from raw text. Never writes. |
| **this skill** | **Writes** the opportunity and moves it through the pipeline. |
| `carrier-appetite` | Picks the carriers to submit to. |
| `renewal-desk` | Executes renewal-side AMS writes (different queue). |

---

## Ground truth: what is actually there

Verified live 2026-07-26 against Supabase `wibscqhkvpijzqbhjphg` and the
running API. Re-verify counts before quoting them back to anyone.

| Table | Rows | Role |
|---|---|---|
| `opportunities` | 63 | **The pipeline.** One row per (client, LOB, type). |
| `opportunity_quotes` | 0 | Carrier quotes attached to an opportunity. Built, unused. |
| `outbound_sync_queue` | 5 | The approval-gated path to NowCerts. |
| `canonical_clients` | 415 | The client mirror — cross-sell search reads this. |
| `agency_crm_users` | 3 | The only valid `approved_by` identities. |

### `opportunities` — real columns

Identity / link:
`id` (uuid), `client_identifier` (**NOT NULL**, slug), `insured_id` (NowCerts
insured GUID), `insured_name`, `nowcerts_opportunity_id`, `nowcerts_quote_guid`,
`quote_number`, `insured_type`, `prospect_type`.

Pipeline: `stage` (NOT NULL, default `New`), `status` (NOT NULL, default
`open`), `opportunity_type` (NOT NULL, default `New Business`), `likelihood`
(NOT NULL, default `Good`), `probability` (int), `disposition`, `lost_reason`,
`next_action`, `next_action_date`, `stage_due_date`, `description`.

Money / policy: `premium_estimate`, `premium_actual`, `carrier`,
`policy_status`, `effective_date`, `expiration_date`, `needed_by`,
`closed_date`.

Attribution / sync: `assigned_to`, `lead_source`, `referral_source`, `source`,
`created_by`, `sync_source`, `synced_at`, `created_at`, `updated_at`.

### The one constraint that matters

```sql
uq_opportunities_client_lob_type UNIQUE (client_identifier, line_of_business, opportunity_type)
```

This is what makes "one opportunity per LOB" real rather than aspirational.
A second GL row for the same client and type **cannot** be inserted. The API's
create is idempotent against it and returns the existing row with
`created: false` — that is a success, not a failure.

### Live data reality — plan around this

- **63 of 63 rows carry an `insured_id`.** Every opportunity in the system is
  AMS-linked today. Keep it that way; an unlinked opportunity is unreconcilable.
- **Only 14 of 63 carry a `nowcerts_opportunity_id`.** That field is the *only*
  thing that makes terminal writeback possible. The other 49 arrived from the
  quote sync and are CRM-side only.
- `sync_source`: `nowcerts_quote_sync` 50, `nowcerts-opportunity-sync` 12,
  `crm` 1. **The pipeline is overwhelmingly a mirror right now**, not a
  human-worked board. Say so rather than implying an active pipeline discipline.
- **`assigned_to` is null on 49 of 63.** When set, its value is a NowCerts-shaped
  JSON array *string*: `["Lamar Coates"]`, `["Gretchen Coates"]`, `["Tia Coates"]`.
  It is **not** an email and **not** an FK.
- **6 `Lost` rows have no `lost_reason`.** The rule below is the standard going
  forward; it is not what the existing data looks like.
- `next_action`, `created_by`, `disposition`, `prospect_type`: **0 populated.**
  Available, unused. Don't cite them as if they carry signal.
- LOB spread: Personal Auto 40, Commercial Auto 8, Homeowners 6, GL 3, WC 2,
  Professional Liability 1, Commercial Property 1, Motorcycle 1, and one
  **`Commercial Package`** row — which violates the one-LOB-per-row spirit and
  should be split when someone works it.
- RLS is **on** for `opportunities`, `opportunity_quotes`, `canonical_clients`
  and `outbound_sync_queue`. The API writes with the service role. A rejection
  is the system working — do not route around it.

---

## Controlled vocabularies — exact values, do not paraphrase

Source of truth: `hermes/intake/opportunities.py`. These come from NowCerts;
we mirror the AMS vocabulary rather than inventing our own.

### `opportunity_type`

`New Business` · `Renewals` · `Cross-selling` · `Upselling` · `Remarket` ·
`Bundling` · `Competitive Replacements (BOR)` · `Life Events` ·
`Seasonal / Event`

A type outside this list is rejected with a 400. Only `Renewals` routes to the
renewal board; everything else is new business.

### `stage` — TWO stage sets, selected by type

**New business** (ordered, default on manual create = `Preparing Application`):

`Not Assigned` → `Preparing Application` → `Sent For Quoting` →
`Quotes Received` → `Sent Proposal` → `Request to Bind` → `Bound / Won` | `Lost`

**Renewals** (ordered, default on manual create = `Renewal in 90 days`):

`Renewal in 90 days` → `Renewal in 60 days` → `Renewal in 30 days` →
`Requote Renewal` → `Annual Policy Review` → `Complete/Auto-Renewal` |
`Bound / Won` | `Not Renewed`

`Bound / Won` is verbatim, spaces included. There is **no** `Needs Info`,
`Discovery`, `Quoting`, `Proposed`, or `new` stage — if you see one of those in
an older doc or in `router.py`, it is wrong (see Known gaps).

Note the asymmetry: `create` **validates** the stage against the type's set and
400s on a bad one; `/stage` accepts **any** non-empty string, because NowCerts
owns the vocabulary and a drag must never be blocked. That means the `/stage`
endpoint will happily write a typo. Spell it exactly.

### `status` — derived, never set by hand

`open` · `won` · `lost`. Derived from the stage by `status_for_stage()`:
anything containing *won* / *bound* / starting *complete* → `won`; *lost* /
*not renewed* / *dead* → `lost`; else `open`. Setting `stage` through the API
re-derives it for you.

### `likelihood` and `probability`

`Excellent` · `Very Good` · `Good` · `Moderate` · `Not Likely`.
Default `Good` — deliberately, so a NowCerts save never blocks.

`probability` is the stage-driven percentage and normally you should let it
default: Not Assigned 5, Preparing Application 10, Sent For Quoting 25, Quotes
Received 50, Sent Proposal 65, Request to Bind 85, Bound/Won 100, Lost 0;
Renewal 90/60/30 = 40/55/70, Requote 60, Annual Review 50,
Complete/Auto-Renewal 100, Not Renewed 0.

Override the percentage only when a human has a real reason. `likelihood`
re-derives from it.

---

## The write path — one door, and it is not SQL

**Never `INSERT` or `UPDATE` `opportunities` directly**, not via the Supabase
MCP and not via psql. `create_opportunity()` behind the API owns five things
you would silently lose: the `client_identifier` slug, the dedupe read against
the unique constraint, the per-type stage default, the probability/likelihood
derivation, and the background AMS priming that links `insured_id`.

Read-only SQL for verification is fine and encouraged.

**API base (tailnet, no auth required from inside):**
`https://hermes-gretch.tail1cbc83.ts.net:8444`

| Action | Call |
|---|---|
| Create / adopt | `POST /api/opportunities` |
| List | `GET /api/opportunities?status=open&stage=&limit=` |
| Edit fields | `PATCH /api/opportunities/{id}` |
| Move stage | `POST /api/opportunities/{id}/stage` |
| Delete | `DELETE /api/opportunities/{id}` |
| Cross-sell search | `GET /api/cross-sell?q=` |
| Leads (NowCerts prospects) | `GET /api/leads` |
| Valid owners/approvers | `GET /api/agency-users` |
| **Queue a quote to the AMS** | `POST /api/opportunities/{id}/send-to-nowcerts` |

**There is no opportunity tool on the `rsg-hermes` MCP bridge.** The door
exposes `list_renewals`, `list_tasks`, `list_documents`, `retention_scan`,
`list_commissions`, `commission_rules`, `carrier_appetite`,
`ams_search_insured`, `sync_health`, `ping`, `create_client`, `create_case`,
`create_task`, `complete_task`, `draft_intake`, `save_document`,
`file_to_nextcloud`, `ams_create_insured`, `ams_upsert_policy`,
`hermes_dispatch` — and nothing for the pipeline. Use HTTP. If pipeline writes
become routine, the fix is a bridge tool, not a workaround.

**Human write path:** the Pipeline Kanban at `/cockpit#pipeline`
(→ `/command-center/cockpit.html`). Drag-to-stage there hits the same
`/stage` endpoint. When a human can do it in two clicks, hand it to them.

### Create payload

```json
POST /api/opportunities
{
  "insured_name": "Truecraft Drywall & Painting",
  "fein": "12-3456789",
  "line_of_business": "General Liability",
  "opportunity_type": "New Business",
  "insured_id": "c45051bd-...",
  "premium_estimate": 8400,
  "carrier": "Progressive",
  "assigned_to": "[\"Lamar Coates\"]",
  "description": "Source: 2026-07-25 new-business intake call. Needs GL + WC. Payroll not yet supplied.",
  "source": "intake"
}
```

`client_identifier` or `insured_name` is required — supply `insured_name` and
let the API derive the slug (`make_client_identifier`: lowercased, non-alnum →
hyphens, `:FEIN-digits` appended when a FEIN is given). Deriving it yourself
risks a near-miss slug and a duplicate that the constraint won't catch.

Omit `stage` unless you have a reason; the type's default is correct. `referral_source`
is **not** settable on create — it is read-only, pulled from NowCerts by the sync.

---

## Ownership

Every opportunity should get an owner at write time.

| Condition | Owner |
|---|---|
| Personal lines | `["Gretchen Coates"]` |
| Commercial, any LOB | `["Lamar Coates"]` |
| Complex renewal or mid-to-large commercial | `["Lamar Coates"]` |
| Unclear | `["Lamar Coates"]`, and say so in the receipt |

The three active identities are Lamar (`lamar@risksolutionsgroup.net`,
`lc-rsg@risksolutionsgroup.net`, administrator) and Gretchen
(`gretchen@risksolutionsgroup.net`, csr). `Tia Coates` appears once in
`assigned_to` but has **no** `agency_crm_users` row — she is a valid display
name and an invalid approver.

**The two identity formats are not interchangeable:**

- `assigned_to` → NowCerts display-name array string: `["Gretchen Coates"]`
- `approved_by` → an active `.net` **email**, validated against
  `agency_crm_users` by `_require_users`. A `.com` address or a display name
  is a 400. Pull the list from `GET /api/agency-users`; never free-type it.

If you cannot determine an owner, write the opportunity anyway — an unowned row
in the pipeline beats a deal that exists only in a chat log — but state the gap
in the receipt and ask. Don't block the write on it.

---

## Writing to NowCerts: not "never", but never silently

The old rule was "this skill never touches the AMS." That is no longer the
shape of the system. There are exactly **two** sanctioned AMS paths out of an
opportunity, both additive, both queued into `outbound_sync_queue`, both
approval-gated:

1. **Quote push** — `POST /api/opportunities/{id}/send-to-nowcerts`. Creates a
   NowCerts Policy with `IsQuote`. Requires `insured_id`; raises 400 without
   one. Drained by the quote executor, which stamps `nowcerts_quote_guid` and
   `quote_number` back onto the row.
2. **Terminal writeback** — automatic when `/stage` lands on a `won` or `lost`
   status **and** the row has a `nowcerts_opportunity_id`. Sets
   `opportunityStageName` only; the disposition is chosen in the AMS.

Everything else is Supabase-only. A stage move, a premium edit, an owner
change: none of it reaches NowCerts.

### The scheduler is live — "queued" means it fires

`rsg-hermes-scheduler` is running on the box with `SCHEDULER_ENABLED=true` and
cycles **every 5 minutes**, draining approved renewal / intake / quote /
casework / opportunity-writeback jobs. Queuing an approved job is not a dry
run and not a staging step you can walk back. Treat `approved_by` as the
signature it is: get the human's word first, in the same conversation.

`--opportunity-writeback-dry-run` and `--quote-executor` with `--dry-run`
preview without claiming, if you need to show what would go.

### Writeback is built but unproven

One writeback job has ever been attempted (2026-07-20). It **failed**:

```
NowCerts POST /api/Zapier/InsertOpportunity failed 400: {"message":"Can't assign to Insured/Prospect"}
```

A required-field guard was added afterward (`assignedTo` coerced to a list,
`winProbability` and `agencyCommission` defaulted) but **no successful
writeback receipt exists.** Do not tell anyone a Bound/Won move "syncs to
NowCerts." Say it queues, and that the first one still needs to be watched.

---

## Write sequence

Preview → confirm → write → read back. Same discipline as the AMS gate.

### PREVIEW

1. **Resolve the insured.** `mcp__rsg-hermes__ams_search_insured` (or
   `GET /api/leads` for prospects) to get the NowCerts insured GUID. No GUID →
   create the insured through the intake/AMS path first, or write the
   opportunity and let background priming attempt the link — but say which.
2. **Search before create.** `GET /api/opportunities?status=open` and check for
   the same `client_identifier` + LOB + type. Classify
   `EXACT` / `LIKELY` / `AMBIGUOUS` / `NONE`. `AMBIGUOUS` stops the run —
   duplicate clients are a known condition of this book.
3. **Assign the owner** per the table above.
4. **Pick the type first, then the stage** — the type selects the stage set.
5. **Derive the stage from completeness, not optimism.** Missing key facts →
   `Preparing Application`, with the gaps written into `description`. Never
   `Quotes Received` with null premiums.
6. **Show the preview:**

```text
HERMES PIPELINE WRITE PREVIEW — NOT YET WRITTEN
Insured: <name> (NowCerts insured <guid|UNRESOLVED>)
client_identifier: <slug>
Operation: CREATE|ADOPT|UPDATE|STAGE MOVE
Match: EXACT|LIKELY|AMBIGUOUS|NONE — <reason>
Opportunities: <n> — one per LOB
  <LOB> | type <type> | stage <stage> | owner <assigned_to> | carrier <c|none> | est premium <amt|none>
Missing: <fields, or none>
AMS effect: NONE | QUOTE QUEUED (approved_by <email>) | WRITEBACK QUEUED (fires within ~5 min)
Read-back: opportunities.<columns>
```

7. Ask: **"Confirm this pipeline write?"** Never write in the same turn as the
   preview. An AMS-touching step needs the approver's email named out loud.

### COMMIT

1. Re-read the target row immediately before an update. Stop if it changed.
2. Write through the API endpoints above. One request. On timeout, **read back
   before retrying** — a blind retry against a unique constraint is safe, but a
   blind retry of `/send-to-nowcerts` queues a second AMS write.
3. Read back every field you wrote (`GET /api/opportunities`, or read-only SQL).
4. Return a receipt:

```text
HERMES PIPELINE RECEIPT
Result: VERIFIED | PARTIAL | FAILED
Insured: <name> (<guid>)
Written: <n> opportunities — <LOB list>   (created: <n>, adopted existing: <n>)
Owners: <summary>
Stage: <stage> → status <status>, probability <n>%, likelihood <l>
Queued to NowCerts: none | quote job <queue_id> | writeback job <queue_id>
Not written: <rows, or none>
Verified: <read-back time and method>
Next: <none, or the exact remaining step>
```

`created: false` is a normal outcome. Report it as **adopted**, not as a
failure and not as a new row.

---

## Hard rules

1. **No raw SQL writes to `opportunities`.** Reads are fine.
2. **One opportunity per LOB.** Six LOBs, six rows. Never a combined
   "Commercial Package" row — the one that exists is a defect, not a pattern.
3. **Link, don't duplicate.** An opportunity references the insured by
   `insured_id`. Legal name, FEIN, and address live in NowCerts; `insured_name`
   here is a display convenience, not an authority.
4. **`Lost` requires a `lost_reason`.** `PATCH` it in the same call, or pass it
   to `/stage` — that endpoint only persists the reason when the stage resolves
   to `lost`.
5. **No seed or demo data. Ever.** Two orphaned cockpit test rows are already
   parked dead in the queue. Don't add to it.
6. **The first CRM edit claims the row.** Any `PATCH` or `/stage` sets
   `sync_source='crm'`, after which the inbound AMS sync **skips** the row
   permanently — it lives in the CRM until terminal. That is correct behavior
   and the reason a manual correction sticks, but it also means a careless edit
   silently ends the AMS mirror for that opportunity. Edit deliberately.
7. **Provenance goes in `description` and `source`.** There is no per-field
   citation column, and inventing one in JSON stuffed into a text field helps
   nobody. Put the origin — document, call date, sender — in `description`,
   and set `source` to `intake` / `manual` / the producing skill.

---

## Stop conditions

Stop without writing when:

- The opportunity match is `AMBIGUOUS`.
- A stage would be written that isn't in the type's set (`/stage` won't catch it).
- Stage would be `Lost` and no `lost_reason` can be determined.
- `/send-to-nowcerts` is requested and the row has no `insured_id`.
- `approved_by` is not an active `agency_crm_users` email.
- RLS rejects the write.
- The user asked for a "test" or "example" record in the production table.

Preserve the payload verbatim and state the smallest next step.

---

## Known gaps — real, current, do not paper over

- **`router.py` / `intake_executor.py` bypass the canonical helper.**
  `map_opportunity_row()` writes `stage` `"new"` (not a real stage) and sets
  `client_identifier` to the **raw insured name**, not the slug — so an
  intake-lane row will not dedupe against an API-created or synced row for the
  same client. No such row exists in the table today (all 63 identifiers are
  clean slugs), which means this path has not run in anger yet. Prefer
  `POST /api/opportunities`. Fixing the intake lane to call
  `create_opportunity()` is the obvious next change.
- **Opportunity writeback has never succeeded.** See above.
- **`opportunity_quotes` is empty.** The attach-a-carrier-quote flow exists in
  the API and cockpit but has no production data behind it.
- **Renewal linkage.** Pipeline `Renewals` opportunities (4 rows) and the
  `renewal_candidates` / renewal-desk ledger are not joined. A renewal written
  here does not appear in the renewal follow-up ladder, and vice versa.
- **`canonical_clients` is under a two-writer freeze.** The `rsg-import`
  pg_cron path was disabled 2026-07-24 for tombstoning rows. Cross-sell search
  reads that mirror, so treat "client not found" as possibly stale rather than
  proof they aren't a client.
- **The box lags main.** Verify the deployed commit before assuming an endpoint
  or field exists in production.

---

## References

- `hermes/intake/opportunities.py` — the vocabulary and the create/advance logic
- `hermes/api.py` — the endpoints (`/api/opportunities…`, `_require_users`)
- `hermes/sync/opportunity_sync.py` — inbound AMS mirror + the `sync_source='crm'` skip
- `hermes/sync/opportunity_writeback.py` — terminal writeback queue + executor
- `hermes/quotes/executor.py` — `stage_quote_job`, the quote push
- `crm-intake-writer` — authors the payload this skill writes
- `carrier-appetite` — who to submit it to
