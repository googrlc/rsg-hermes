---
name: renewal-desk
description: >
  Hermes-side renewal EXECUTOR for RSG. This is the "one door" that performs the
  actual AMS/CRM writes when Gretchen (working renewals in the Perplexity Space via
  Perplexity Computer) tells Hermes to do something. Gretchen/Perplexity read and
  draft; Hermes executes — sanctioned MCP path only, additive, queued, human-approved.
  Triggers on "renewal desk", "work the renewals", "work renewal requests",
  "execute renewal", "process renewal", "renew {client}", "Gretchen renewal request",
  or a renewal action posted by Gretchen in #renewal-updates / her DM.
  Revenue-critical (retention 54.92% → 75%). Complements retention-risk-scout
  (finds risk) and gretchen-daily-queue (tells Gretchen what to do) — this one DOES it.
---

# Renewal Desk (Hermes = the executor)

## Purpose & role split

Gretchen works renewals inside the **Perplexity Space**. That surface is **read +
draft only** — it can view AMS/CRM data (via the Hermes MCP read tools, or the
read-only web UI allowed by Amendment A‑1) and it can prep packets and draft
outreach. **It never writes to NowCerts or EspoCRM.**

When an actual change needs to happen, **Gretchen tells Hermes directly** (through
Perplexity Computer). **Hermes — this skill — is the only thing that writes.** Every
mutation goes through the sanctioned MCP path, additive-only, queued, human-approved.

```
Gretchen + Perplexity Computer   →   tells Hermes directly   →   Hermes (this skill)
   READ  +  DRAFT  (no writes)          plain-language ask         EXECUTES the writes
```

This skill is the execution arm. It composes with:
- **`retention-risk-scout`** — finds who's at risk and why (scoring model, buckets).
- **`gretchen-daily-queue`** — surfaces the day's renewal actions to Gretchen.
- **`nowcerts-skill`** — renewal buckets + policy facts from the AMS.
- **`crm-manager`** — EspoCRM write mechanics + field-casing rules.

---

## The one-door rules (do not violate — silent data loss lives here)

1. **AMS (NowCerts/Momentum) is the source of truth.** Data is authored in the AMS
   and syncs DOWN to EspoCRM. Never invert this.
2. **CRM → AMS is additive ONLY**, via four narrow channels: (a) new-client stub on
   Opportunity *Closed Won*, (b) Tasks, (c) Cases, (d) fill-blank of an *empty* AMS
   field. Nothing else writes up.
3. **Never overwrite a populated AMS field.** A conflict is flagged to a human, not
   resolved by Hermes.
4. **Never create or edit a policy from the CRM side.** Policies arrive by carrier
   download or are entered manually in the AMS. Renewal ≠ Hermes writing a policy.
5. **Search before insert.** Before creating any insured/account, `search_insureds`
   (NowCerts) and `search_accounts` (Espo) by name / email / FEIN and link to the
   existing record. Never spawn a duplicate.
6. **Writes go through MCP tools, not raw REST/DB.** Prefer `espocrm`, `nowcerts`,
   `supabase` MCP tools. Legacy skills show REST bodies — treat those as *reference
   for the logic only*.
7. **Field casing is per-entity:** `Account` = snake_case, `Contact` = camelCase,
   `Task`/`Opportunity` = camelCase, `Policy` = mixed. Wrong casing is dropped with
   NO error. Confirm casing before every write.

---

## How a request reaches Hermes

Gretchen (via Perplexity Computer) tells Hermes directly. In practice a request
lands as one of:

- A message in **#renewal-updates (`C09R2CG2KS6`)** or **Gretchen DM (`C0AMWAZBBJP`)**.
- A direct instruction in an interactive Hermes session.

Hermes is **not** an always-on listener (Max-plan boundary). Drain requests when:
- invoked on demand ("work the renewal requests"), or
- on a light schedule (e.g. a couple of sweeps per workday of #renewal-updates).

### Request grammar (what Gretchen sends, what Hermes parses)

Gretchen doesn't have to be formal — parse plain language. But the Perplexity
playbook nudges her toward this shape, so honor it when present:

```
@Hermes RENEWAL ACTION
Client: {name or policy #}
Do:
  - {action 1}
  - {action 2}
Notes: {anything Hermes needs to know}
```

If the client is ambiguous, or the ask is vague, **do not guess** — reply in-thread
with a one-line clarifying question and stop. Acting on the wrong record is worse
than a 30-second delay.

---

## Execution workflow

### Step 1 — Resolve the client (search before touch)
- `nowcerts.search_insureds` by name / email / FEIN → get the insured `databaseId`
  and `momentum_client_id`.
- `espocrm.search_accounts` for the matching Account (watch for duplicate
  `momentum_client_id` groups — pick the master record; see the data-integrity tag
  system). Confirm ONE match before mutating. On multiple/no match → clarify, stop.

### Step 2 — Pull the current picture
- `nowcerts.list_policies` for the insured → soonest `expirationDate`, `premium`,
  `lineOfBusiness`, `carrierName`, `active`, prior-term premium if available.
- `espocrm.get_opportunities` + `espocrm.list_open_tasks` for the Account → is there
  already an open renewal Opportunity / Task? (Dedup — never create a second.)
- Bucket the renewal (per `nowcerts-skill`): 0–14 CRITICAL, 15–30 URGENT,
  31–60 WATCH, 61–90 PIPELINE. PL enters at 30 days, CL at 60.

### Step 3 — Classify the requested actions against the approval gate
See **Approval gate** below. Split the ask into *auto-additive* vs *approval-required*.

### Step 4 — State intent, then execute
For live-system writes, **state what you're about to change and why** (CLAUDE.md
default), then perform the additive writes. Report each write.

### Step 5 — Report back in plain English
- To **Gretchen DM (`C0AMWAZBBJP`)**: plain-English confirmation, zero jargon
  ("Done — I set up the renewal for the Smiths and logged that you called them.
  Their auto policy renews May 3.").
- To **#renewal-updates (`C09R2CG2KS6`)**: the action-list entry if it's part of a batch.
- To **#the-boss (`C0ANQUENX4P`)**: only material/financial outcomes, one line.

---

## Approval gate (default: auto-additive, approve-financial)

> Default policy — adjustable. Tell Hermes to change the line if you want a
> different split (e.g. "Gretchen approves ops, Lamar approves money", or
> "Lamar approves everything").

**AUTO (state intent, then do it):**
- Create a renewal **Opportunity** in Espo (stage = Identified/pipeline).
- Create / update / complete a renewal **Task** in Espo.
- Log an activity **Note** on the Account (`espocrm.create_note`) — e.g. "Called
  client, left voicemail" / "Sent renewal quote".
- Open a **Case** for a renewal service issue — *not yet tooled.* The espocrm MCP has
  no Case read/write tool (as of 2026-07-10). Until one is added, capture the issue as
  a **Task** instead, or flag to Lamar. Do NOT fall back to raw REST for a Case.

**APPROVAL-REQUIRED (stage it, ask, wait for an explicit OK):**
- Anything touching **premium** or **policy lifecycle** (status/effective/expiration).
- **Fill-blank of an AMS field** (allowed, but confirm the field is truly empty and
  the value is right — never overwrite populated).
- Any **client-facing send** (email/text going out to the insured).
- Creating a **new insured/account stub** in the AMS (dedup first, then confirm).

**NEVER (out of scope for Hermes, regardless of approval):**
- Writing or editing a **policy** from the CRM side.
- **Overwriting a populated AMS field.**
- Resolving a data **conflict** — flag it to a human instead.

---

## Write playbook by action type

### Log outreach / activity  →  AUTO
`espocrm.create_note` on the Account (`parentType: "Account"`, `parentId: {id}`).
Keep the note factual: what happened, when, next step. This is how renewal outreach
becomes visible to retention-risk-scout and the daily queue.

### Create / advance a renewal Opportunity  →  AUTO
Dedup first with `espocrm.get_opportunities`. If none open, `espocrm.create_opportunity`
linked to the Account. Advance or close with `espocrm.update_opportunity` (stage →
Closed Won = renewed, Closed Lost = lost). **Opportunity fields = camelCase**
(`name`, `stage`, `accountId`, `closeDate`, `amount`, `assignedUserId`). Owner is the
renewal owner (Gretchen for personal lines) — never set yourself as `assignedUser`.
Omit `stage` to accept the install default rather than risk a dropped enum value.

### Create / complete a renewal Task  →  AUTO
Dedup with `espocrm.list_open_tasks` for the Account. If none open, `espocrm.create_task`;
complete/reassign/re-date with `espocrm.update_task`. **Task fields = camelCase**
(`name`, `status`, `priority`, `assignedUserId`, `dateStart`, `dateEnd`,
`description`, `parentType`, `parentId`). Leave `status`/`priority` off to use the
install default (this install customizes them — e.g. `Inbox`, `Cancelled`). An Espo
Task/Case writes back to NowCerts as ONE `Tasks` entity — carry the linking keys
(`insured_database_id` / `policy_number`, `momentum_client_id`).
**`assignedUserId` is REQUIRED** — this Espo install rejects a task create with no
assignee. Default personal-lines renewal tasks to **Gretchen**; use **Lamar**
(`69bdad92458da2204`) otherwise. Never the api/Hermes identity (assignment guardrail).
The server also falls back to `ESPO_DEFAULT_TASK_ASSIGNEE_ID` (env) if set — point
that at Gretchen's user id so renewal tasks auto-route to her.

### Fill-blank an AMS field  →  APPROVAL
Only when the AMS field is **empty**. Confirm emptiness via `nowcerts.list_policies` /
insured read, state the field + value, get OK, then `nowcerts.update_policy` /
insured update. If the field is populated → **stop, flag the conflict.**

### New-client stub on Closed Won  →  APPROVAL
`ams_search_insured` (dedup by name/email/FEIN) → if truly new,
`nowcerts.create_insured` with the minimal stub. Never a duplicate. This is the only
sanctioned CRM-initiated AMS *creation*.

> **Wiring note (capability, as of 2026-07-10):** the espocrm MCP now exposes the
> renewal write tools directly — `create_note`, `create_task`, `update_task`,
> `create_opportunity`, `update_opportunity` (15 tools total; `/health` reports the
> count). The nowcerts MCP covers AMS writes — `create_insured`, `insert_policy`,
> `update_policy` (fill-blank). If a needed write tool is ever *unavailable* (server
> down, tool missing), **stage the request, tell Gretchen it's queued for Hermes/Lamar,
> and do NOT fall back to raw REST, the DB, or the web UI** (fail-closed, per contract
> §6). Espo server source: `rsg-hermes/mcp/espo/src/server.js` (LaunchAgent
> `com.rsg.espo-mcp`, `launchctl kickstart -k` to reload after edits).

---

## Track & follow-up (closes the loop)

- Mark renewals **renewed** vs **lost**: advance/close the Opportunity, complete the
  Task, log the outcome note.
- Chase outstanding quotes: if a "quote requested" Task is aging, create a follow-up
  Task for Gretchen and note it.
- Optionally record the outcome to **Supabase** (KPI/snapshot tables) so
  retention movement is trackable week over week (`supabase.execute_sql`), compared
  against the prior snapshot.

---

## Error handling — fail closed, never fall back

| Situation | Action |
|---|---|
| Ambiguous client / vague ask | Reply one clarifying line in-thread, STOP. Don't guess. |
| MCP tool unavailable / errors / times out | STOP and report to Gretchen + **#systems-check**. **Never** fall back to raw REST, DB, or the web UI. |
| Write "succeeds" but value didn't stick | Suspect field-level ACL (api role) or wrong casing — verify, flag; don't blindly retry. |
| AMS field already populated (would overwrite) | Do NOT write. Flag the conflict to a human. |
| Duplicate account / insured found | Resolve to the master record before any write; if unsure, flag. |
| Asked to change a policy from CRM | Refuse — out of scope. Explain it must be done in the AMS. |

---

## Notes
- LLM: **Anthropic** (revenue-critical — tied to retention).
- Gretchen-facing output: **plain English, zero jargon, zero field names** (mirror
  `gretchen-daily-queue`).
- Hermes never impersonates a user; Tasks/Cases owned by Gretchen or Lamar.
- Every write is logged/auditable through Hermes — don't take actions that dodge the
  audit trail.
- Companion doc for the Perplexity side lives at
  `renewal-desk/perplexity-space-playbook.md` — paste it into the Space.
