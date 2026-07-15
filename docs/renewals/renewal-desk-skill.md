---
name: renewal-desk
description: >
  Hermes-side renewal EXECUTOR for RSG. This is the "one door" that performs the
  actual AMS/CRM writes for renewals. The RSG Renewal Walker GPT (ChatGPT Business)
  is the workstation where Gretchen and Lamar work renewals — it reads and drafts;
  Hermes executes. Every mutation goes through the sanctioned MCP path — additive,
  queued, human-approved.
  Triggers on "renewal desk", "work the renewals", "work renewal requests",
  "execute renewal", "process renewal", "renew {client}", "Gretchen renewal request",
  or a renewal action posted in #renewal-updates / her DM.
  Revenue-critical (retention 54.92% → 80%). Complements retention-risk-scout
  (finds risk) and gretchen-daily-queue (tells Gretchen what to do) — this one DOES it.
---

# Renewal Desk (Hermes = the executor)

> Canonical spec: `docs/renewals/BRIEF-renewal-walker-runner-2026-07-13.md` (v3).
> This skill is the **execution arm** — the sanctioned write door. It is live today.
>
> **Automated path (Job Contract v2, 2026-07-15):** the same execution role also
> runs headless as `hermes/renewals/executor.py` (`hermes --renewal-executor`). It
> processes **only** rows in `outbound_sync_queue` where `object_type='renewal'`,
> `destination_system='nowcerts'`, `status='queued'`, **both** `approved_by` and
> `approved_at` are set, `payload.renewal_id` resolves in `project_85_renewals`, and
> the payload carries an explicit `action` + `expected_result`. It reads NowCerts,
> compares, executes exactly the approved action (`request_terms` / `prepare_options`
> / `client_follow_up` / `update_ams`), re-reads to verify, and writes a receipt to
> `renewal_execution_receipts`. It never infers approval from notes/Slack/chat, never
> retries an ambiguous write, and escalates high-impact failures to `#systems-check`.
> See `docs/renewals/README.md`. When you (the interactive skill) work a renewal
> conversationally, the approval + queue rules below still govern; the automated
> executor is the same contract without a human in the request loop.

## Purpose & role split

One system, four roles (v3):

- **RSG Renewal Walker GPT (ChatGPT Business) = the workstation.** Gretchen and
  Lamar work renewals there. It pulls the queue, walks one step at a time, renders
  the worksheet, and posts outcomes back. It is **read + draft only** — it never
  writes to NowCerts or EspoCRM, and it knows nothing except what Hermes returns.
- **Hermes — this skill — is the only thing that writes.** Every mutation goes
  through the sanctioned MCP path: additive-only, queued, human-approved. **Hermes
  never talks to a client.**
- **Gretchen = the only hands that touch clients.** The Walker drafts; she sends.

```
Renewal Walker GPT (ChatGPT)   →   asks Hermes   →   Hermes (this skill)
   READ  +  DRAFT  (no writes)       plain ask        EXECUTES the writes (MCP path)
```

### Two front doors into this executor

**1. The `/walker/*` API — Phase 1, DESIGNED, NOT BUILT.**
The v3 target is the Walker GPT hitting Hermes over an Action wired to a
`/walker/*` HTTPS API (scoped key, `GET /walker/queue`, `POST /walker/.../touch`,
etc. — see the brief). **This does not exist yet.** Do not assume the endpoints,
the scoped key, or the EspoCRM Opportunity field additions are live. Phase 1 does
not start until Phase 0 is green AND Lamar explicitly says go.

**2. The live path — what runs TODAY (and the permanent FALLBACK MODE).**
Until the API ships, and forever after as the outage fallback, a renewal action
reaches Hermes as a plain-language request in **#renewal-updates (`C09R2CG2KS6`)**,
**Gretchen DM (`C0AMWAZBBJP`)**, or an interactive Hermes session. Hermes parses it
and performs the additive MCP writes below. If OpenAI or the Action is ever down,
this is the door the day runs through — data-bearing Slack cards + the reply
grammar in the fallback section.

This skill composes with:
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
8. **The workstation talks to Hermes only.** Never NowCerts direct, never EspoCRM
   direct. One door, one owner of writes.

---

## How a request reaches Hermes

A renewal action lands as one of:

- A message in **#renewal-updates (`C09R2CG2KS6`)** or **Gretchen DM (`C0AMWAZBBJP`)**.
- A direct instruction in an interactive Hermes session.
- (Phase 1, once built) a `/walker/*` API call from the Renewal Walker GPT.

Hermes is **not** an always-on listener (Max-plan boundary). Drain requests when:
- invoked on demand ("work the renewal requests"), or
- on a light schedule (e.g. a couple of sweeps per workday of #renewal-updates).

### Request grammar (what a request looks like, what Hermes parses)

Parse plain language — no formality required. When the request arrives in this
shape, honor it:

```
RENEWAL ACTION
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
- To **#lamar-alerts**: 🚨 escalations and 📋 handoffs (ID resolved by name at
  startup; fail loud if it can't resolve).
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
  no Case read/write tool (as of 2026-07-13). Until one is added, capture the issue as
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

> The v3 worksheet is **structured state on the renewal Opportunity**, not a file.
> The proposed Opportunity field additions (`cRenewalSegment`, `cRenewalOwner`,
> `cComplexityFlags`, `cTouchLog`, `cDay1SentAt`, `cNextTouchCode`, etc.) are in the
> brief and are **Phase 1 — NOT yet added to live Espo.** Do not write fields that
> don't exist yet; Espo silently drops unknown fields.

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
`nowcerts.search_insureds` (dedup by name/email/FEIN) → if truly new,
`nowcerts.create_insured` with the minimal stub. Never a duplicate. This is the only
sanctioned CRM-initiated AMS *creation*.

> **Wiring note (capability, as of 2026-07-13):** the espocrm MCP exposes the
> renewal write tools directly — `create_note`, `create_task`, `update_task`,
> `create_opportunity`, `update_opportunity`. The nowcerts MCP covers AMS writes —
> `create_insured`, `insert_policy`, `update_policy` (fill-blank). If a needed write
> tool is ever *unavailable* (server down, tool missing), **stage the request, tell
> Gretchen it's queued for Hermes/Lamar, and do NOT fall back to raw REST, the DB, or
> the web UI** (fail-closed, per contract §6). Espo server source:
> `rsg-hermes/mcp/espo/src/server.js` (LaunchAgent `com.rsg.espo-mcp`,
> `launchctl kickstart -k` to reload after edits).

---

## Fallback Mode (Slack paste grammar — keep it implemented)

When the `/walker/*` API is unavailable (not built yet, or OpenAI/Action down),
Hermes works the day the v2 way: data-bearing Slack cards + a thread-reply grammar
Gretchen can use in #renewal-updates / her DM. Behind a feature flag; the backup
door is always present. The reply grammar:

```
done · log: <text> · flag: <reason> · handoff · renewed $<amt> · lost <reason> · pending
```

Each maps to an executor action here (`done` → advance/complete, `log:` → activity
note, `flag:` → escalate to `#lamar-alerts`, `handoff` → 📋 to Lamar, `renewed $` /
`lost` → close the Opportunity with premium/reason, `pending` → no-op ack).

---

## Track & follow-up (closes the loop)

- Mark renewals **renewed** vs **lost**: advance/close the Opportunity, complete the
  Task, log the outcome note.
- Chase outstanding quotes: if a "quote requested" Task is aging, create a follow-up
  Task for Gretchen and note it.
- Optionally record the outcome to **Supabase** (KPI/snapshot tables) so
  retention movement is trackable week over week (`supabase.execute_sql`), compared
  against the prior snapshot. Scoreboard = rolling 12-mo premium retention,
  "Retention: XX.X% (baseline 54.9%)".

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
| Asked to write a Phase-1 field/endpoint not yet built | STOP. Say it's Phase 1, not live. Don't write unknown Espo fields (silent drop). |

---

## Notes
- LLM: **Anthropic** (revenue-critical — tied to retention).
- Gretchen-facing output: **plain English, zero jargon, zero field names** (mirror
  `gretchen-daily-queue`). Her pings are **DMs, never channel posts**.
- **Medicare excluded from all automated client touches** (T-65 watcher is internal
  only, separate brief). Never age-reference a client in writing.
- Hermes never impersonates a user; Tasks/Cases owned by Gretchen or Lamar.
- Every write is logged/auditable through Hermes — don't take actions that dodge the
  audit trail.
