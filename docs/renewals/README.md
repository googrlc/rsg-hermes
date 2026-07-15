# Renewals — Hermes runner + Renewal Walker workstation (v3)

Canonical spec: [`BRIEF-renewal-walker-runner-2026-07-13.md`](./BRIEF-renewal-walker-runner-2026-07-13.md).
Everything here sits under the AMS/CRM Access Contract; where they disagree, the
contract wins.

## The v3 split — one system, four roles

- **RSG Renewal Walker GPT (ChatGPT Business) = the workstation.** Gretchen and
  Lamar work renewals here. It pulls the queue from Hermes, walks one step at a
  time, renders and patches the worksheet, and posts every outcome back. It knows
  nothing except what the Hermes API returns — it never invents policy data.
- **Hermes = the runner and the API.** Nightly classify → compute due touches →
  serve the Walker API → write every touch, flag, handoff, and outcome to
  EspoCRM/NowCerts → fire doorbells and escalations → compute the scoreboard.
  **Hermes never talks to a client.**
- **Slack = the notification wire only.** Morning doorbell DM to Gretchen,
  🚨 escalations and 📋 handoffs to `#lamar-alerts`, Monday digest. No
  data-bearing cards on the primary path.
- **Gretchen = the only hands that touch clients.** The Walker drafts; she sends.

The GPT talks to **Hermes only** — never NowCerts direct, never EspoCRM direct.
One Action, one scoped API key (`/walker/*`), one owner of writes (Hermes).

```
Renewal Walker GPT (ChatGPT)      HERMES  (this repo — the runner + API)
──────────────────────────        ─────────────────────────────────────
pull queue / snapshot   ────────► GET  /walker/queue · /walker/renewal/{id}
draft outreach (Gretchen sends)   classify · compute due touches nightly
post outcome / flag / handoff ──► POST /walker/.../touch|flag|handoff|outcome
render worksheet inline           PATCH /walker/.../worksheet  (CRM state)
                                  writes to EspoCRM + NowCerts · doorbells · scoreboard
```

## Build status — read before you touch anything

| Piece | State |
|---|---|
| v3 architecture (this doc + the brief) | **Designed. Approved 2026-07-13.** |
| `/walker/*` API + scoped key + GPT Action | **Phase 1 — designed, NOT built.** Does not start until Phase 0 is green AND Lamar says go. |
| EspoCRM Opportunity field additions | **Phase 1 — designed, NOT built.** No live Entity Manager changes yet. |
| Classifier / cadence / auto-flags module | **Phase 1 — designed, NOT built.** |
| `renewal-desk` executor skill (MCP write path) | **Live now.** This is what actually runs today. |
| v2 Slack paste-block flow | **Fallback Mode only** (behind a feature flag, for OpenAI/Action outages). |

Phase 0 gates everything (pending patch deploy, `rsg-sync-daily` clean, stale
`canonical_policies` resolved, ingest dedup, manual renewal fires). See the brief's
**Phases & Gates**.

## Renewal Executor — Job Contract v2 (2026-07-15)

`hermes/renewals/executor.py` is the controlled, **queue-driven** execution worker.
The upstream Renewal Desk Site stages **human-approved** instructions in Supabase
`outbound_sync_queue` (`object_type='renewal'`, `destination_system='nowcerts'`,
`status='queued'`, `approved_by` + `approved_at` set, `payload.action` +
`payload.expected_result` present, `payload.renewal_id` resolving in
`project_85_renewals`). Hermes claims one, validates, reads NowCerts, compares,
executes exactly the approved action, **re-reads to verify** (a 200 is not proof),
writes a receipt to `renewal_execution_receipts`, marks the queue row
completed/failed, records the outcome in `renewal_actions`, and escalates
high-impact failures to `#systems-check`. Actions: `request_terms`,
`prepare_options` (no AMS write), `client_follow_up`, `update_ams` (approved fields
only). Run: `hermes --renewal-executor` (add `--renewal-executor-dry-run` to preview
with zero side effects). Scheduled/triggered, **not** always-on.

**Renewal Loop v6 is retired** (`loop.py`, the `/webhooks/espo/disposition` +
`/webhooks/espo/worksheet` endpoints, and `--renewal-reconcile`). Its ledger tables
(`renewals_master`, `renewal_events`, `crm_dispositions`, `ams_writeback_log`) remain
in the DB as history but are no longer written. The Momentum MCP notes client
(`momentum_mcp_client.py`) survives — the executor's `note` channel uses it. This
supersedes the brief's "no new tables / Walker-is-the-write-path" model for the
execution path; the `/walker/*` read+draft workstation is unaffected.

## Files here

| File | Runs where | Purpose |
|---|---|---|
| [`BRIEF-renewal-walker-runner-2026-07-13.md`](./BRIEF-renewal-walker-runner-2026-07-13.md) | spec | **Canonical v3 spec.** Mission, the loop, the Walker API contract, worksheet-as-CRM-state, phases/gates, decisions, risks. |
| [`renewal-desk-skill.md`](./renewal-desk-skill.md) | **Hermes** (Claude Code) | The executor skill. The one door that performs the sanctioned, additive, queued, approved AMS/CRM writes. Version-controlled source for the live skill. |

## Where the live copy lives

- **Skill** → `~/.claude/skills/renewal-desk/SKILL.md` (auto-loads for Hermes; must
  stay in the skills dir to trigger). Keep it in sync with `renewal-desk-skill.md`
  here — they are meant to be byte-identical.

## The worksheet is not a file

The canonical renewal worksheet is **structured state on the renewal Opportunity**,
not a document anywhere. The Walker renders it on demand and PATCHes field updates.
Nobody files anything, nothing drifts, and the CRM record is complete the moment the
renewal closes. (PDF export for an E&O file is parking-lot, one endpoint away if
ever needed.) The proposed Opportunity field additions live in the brief.

## Capability notes (2026-07-13)

- espocrm MCP write tools: `create_note`, `create_task`, `update_task`,
  `create_opportunity`, `update_opportunity` (server `mcp/espo/src/server.js`).
- Espo **Task create requires an assignee** — pass `assignedUserId` (Gretchen for PL,
  Lamar `69bdad92458da2204`) or set `ESPO_DEFAULT_TASK_ASSIGNEE_ID` in the env.
- **Cases are not yet tooled** — no Case read/write tool exists; capture as a Task or
  flag Lamar until one is added.
- **Business rules live in exactly one place — Hermes config** ($5K commercial
  cutoff, 10% remarket threshold, cadence timing). The GPT carries zero numbers by
  design, so there is no "make the GPT and Hermes agree" drift to manage.
