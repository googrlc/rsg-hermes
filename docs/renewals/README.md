# Renewals — the Hermes runner

Everything here sits under the AMS/CRM Access Contract; where they disagree, the
contract wins.

## The shape today — one system, three roles

- **Hermes = the runner and the only writer.** Nightly classify → refresh
  `renewal_candidates` → execute human-approved instructions → fire doorbells and
  escalations → compute the scoreboard. **Hermes never talks to a client.**
- **The cockpit = the workstation.** Renewals are worked in the Command Center
  (`/command-center/`), which reads the canonical book from Supabase and stages
  approved instructions for the executor. The **Zoho Creator Renewals Desk**
  (`docs/zoho/creator-renewals-desk/`) is the Zoho-native desk over CRM Renewals;
  Hermes upserts those modules via `--sync-zoho-renewals` and mirrors approved
  AMS_Write_Queue rows via `--sync-zoho-ams-queue`.
- **Gretchen = the only hands that touch clients.** Hermes drafts; she sends.
- **Slack = the notification wire only.** Morning doorbell DM to Gretchen,
  🚨 escalations and 📋 handoffs to `#lamar-alerts`, Monday digest. No
  data-bearing cards on the primary path.

## Renewal Executor — Job Contract v2 (2026-07-15)

`hermes/renewals/executor.py` is the controlled, **queue-driven** execution worker.
The upstream cockpit stages **human-approved** instructions in Supabase
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

## Retired paths

- **The `/walker/*` API and the Renewal Walker GPT workstation (retired 2026-07-24).**
  `hermes/walker/` served an on-demand renewal API to a ChatGPT Action, and kept its
  entire retain layer (touch log, complexity flags, handoff notes, renewal decision)
  in EspoCRM `Opportunity` custom fields. EspoCRM has since been removed entirely,
  and the retain layer had no home outside it, so the service, its router mount, its
  OpenAPI contract, and the Opportunity field reference were deleted rather than
  rebuilt. Renewal work runs through the cockpit + the queue-driven executor.
- **Renewal Loop v6** (`loop.py`, the `/webhooks/espo/disposition` +
  `/webhooks/espo/worksheet` endpoints, and `--renewal-reconcile`). Its ledger tables
  (`renewals_master`, `renewal_events`, `crm_dispositions`, `ams_writeback_log`) remain
  in the DB as history but are no longer written. The Momentum MCP notes client
  (`momentum_mcp_client.py`) survives — the executor's `note` channel uses it.

## Files here

| File | Runs where | Purpose |
|---|---|---|
| [`BRIEF-renewal-walker-runner-2026-07-13.md`](./BRIEF-renewal-walker-runner-2026-07-13.md) | spec | **Historical.** The v3 Walker × runner design. Superseded — kept for the reasoning behind the cadence and segment rules, which still hold. |

## Where the skill lives

- **`.claude/skills/renewal-desk/SKILL.md`** — one copy, in the repo,
  version-controlled and auto-loading.

> Changed 2026-07-26: there used to be a second copy here
> (`docs/renewals/renewal-desk-skill.md`) that was meant to stay byte-identical
> with a live copy under `~/.claude/skills/`. It drifted — the doc fork still
> described the EspoCRM desk months after the executor moved to
> `outbound_sync_queue`. Both duplicates are gone; the skill in `.claude/skills/`
> is the only copy. Don't reintroduce a fork.

## Business rules live in exactly one place

Hermes config owns the numbers — $5K commercial cutoff, 10% remarket threshold,
cadence timing. No consumer of the renewal data carries its own copy, so there is
no drift to reconcile.
