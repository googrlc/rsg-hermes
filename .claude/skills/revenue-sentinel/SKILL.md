---
name: revenue-sentinel
description: Operator playbook for the Project 85 Sentinel briefing — the daily Slack post that surfaces stale opportunities, Project 85 renewal checkpoints (90/60/30 days), and x-date opportunities. Covers how the sentinel reads from the CRM + `project_85_renewals`, how it routes via `slack_registry`, which env vars and idempotency state file it uses, how to dry-run/force, and how to respond when the sentinel posts an alert (delegating to `renewal-review`, `crm-intake-writer`, or `carrier-appetite` as needed). Use when the user asks to run, configure, or interpret the sentinel — or when a sentinel post lands in Slack and an agent needs to triage it.
---

# Revenue Sentinel

The Project 85 Sentinel is RSG's daily revenue-guardrail briefing. This
skill is the operator's playbook — both for running the sentinel and for
acting on its output.

## What the sentinel does

Once per business day it posts to `HERMES_SENTINEL_SLACK_CHANNEL` (default
`D0AUTEYHBDH`; preferred operational channel is
`#rsg-hermes-project85-renewals` via `slack_registry`):

- **STALE LEADS** — Opportunities in prospecting states with no activity
  for `HERMES_SENTINEL_STALE_DAYS` (default 14).
- **PROJECT 85 RENEWALS** — Active `Policy` records at checkpoint
  intervals (`HERMES_SENTINEL_RENEWAL_CHECKPOINTS`, default `90,60,30`)
  with status from `Account.renewalOutreachStage`.
- **X-DATE OPPORTUNITIES** — `Lead` records and closed-lost
  Opportunities with x-date at `HERMES_SENTINEL_XDATE_DAYS` out
  (default 60).
- **WHALE ACCOUNTS** — surfaced to the top of each section.

Source: `docs/revenue-sentinel.md`, `hermes/commands/revenue.py`.

## Commands

```bash
hermes --revenue-sentinel              # run once and post
hermes --revenue-sentinel-dry-run      # render text without posting
hermes --revenue-sentinel-force        # bypass per-day idempotency
hermes --revenue-sentinel-health       # freshness + config readiness
```

Scheduled (outside Hermes) weekdays 08:00 `America/New_York`:

```cron
0 8 * * 1-5 cd /path/to/rsg-hermes && /path/to/venv/bin/hermes --revenue-sentinel
```

## Env vars

| Variable | Purpose | Default |
|----------|---------|---------|
| `HERMES_SENTINEL_SLACK_CHANNEL` | Target channel ID | `D0AUTEYHBDH` |
| `HERMES_SENTINEL_STALE_DAYS` | Stale-opp threshold | `14` |
| `HERMES_SENTINEL_RENEWAL_CHECKPOINTS` | Comma-separated days | `90,60,30` |
| `HERMES_SENTINEL_XDATE_DAYS` | X-date opportunity window | `60` |
| `HERMES_SENTINEL_TIMEZONE` | Date-window timezone | `America/New_York` |
| `HERMES_SENTINEL_STATE_FILE` | Idempotency state file | (project-relative) |
| `HERMES_SENTINEL_GRETCHEN_USER_ID` | Slack user for "Assign to Gretchen" | (none) |
| `HERMES_SLACK_RETRIES` / `HERMES_SLACK_RETRY_SLEEP` | Slack post retries | (Hermes defaults) |

Missing env in `--revenue-sentinel-health` fails fast.

## When to use this skill

- "Run the sentinel" / "did the sentinel post today?" / "why didn't the
  sentinel fire?"
- "Configure the sentinel for X" / "change the checkpoints."
- A sentinel post lands in Slack and an agent needs to follow up.
- The user says "Dismiss" / "Remind me in 2 days" / "Assign to Gretchen"
  from a sentinel-posted button.

## Triage playbook — when the sentinel posts

For each row the sentinel surfaces, decide:

| Section | Default handler | Skill to delegate to |
|---------|----------------|---------------------|
| STALE LEAD | Producer outreach OR mark dead | `crm-fact-retriever` (pull context), then `crm-intake-writer` (status update Opportunity payload) |
| RENEWAL @ 90 | Acknowledge + schedule outreach | `renewal-review` |
| RENEWAL @ 60 | Outreach + initial market check | `renewal-review` → recommendation often `REMARKET_SAMPLE` |
| RENEWAL @ 30 | Full remarket if not already engaged | `renewal-review` → `REMARKET_FULL` → `carrier-appetite` → `proposal-builder` |
| X-DATE OPP | Re-engage prior prospect | `crm-fact-retriever` (history), then `crm-intake-writer` (refreshed Opportunity) |
| WHALE | Always producer-led | Notify producer, never auto-resolve |

Whatever the section, never write CRM data directly — produce a draft
payload with `approval_required: true`.

## Slack loop-back actions — ⚠️ INERT

Three buttons are attached to each sentinel post, but **nothing handles the
clicks** — the Socket Mode listener that served them was retired July 2026.
Do not tell anyone these work; act on the row in the cockpit instead.

What they were meant to do:

- **Remind me in 2 days** — creates an the CRM `Task` due in 2 days,
  parent = the surfaced entity. Use this when the row is real but
  blocked on a wait (e.g. carrier response).
- **Assign to Gretchen** — creates an the CRM `Task` assigned to
  `HERMES_SENTINEL_GRETCHEN_USER_ID`. Use when the action is service
  work, not producer work.
- **Dismiss** — acknowledges without CRM write. Use only when the row
  is no longer relevant (e.g. policy already bound, lead already dead).

## Reliability controls

- **Retries:** the CRM reads go through `SupabaseClient` in-process; Slack
  posting uses `HERMES_SLACK_RETRIES` / `HERMES_SLACK_RETRY_SLEEP`.
- **Idempotency:** last successful post date is stored in
  `HERMES_SENTINEL_STATE_FILE`. `--revenue-sentinel-force` overrides.
- **Partial output:** if one section query fails, the briefing still
  posts available sections and includes a `WARNINGS` line.
- **Health:** `--revenue-sentinel-health` fails fast when last post is
  stale or required env vars are missing.

## Hard rules

1. **Channel registry required.** Don't post anywhere that isn't in
   `slack_registry` with the sentinel role allowed (`HermesOpsRouter` or
   `HermesRenewalSpecialist`). Drift = `BLOCKED_BY_GUARDRAIL` + log to
   `guardrail_logs`.
2. **No direct CRM writes from the sentinel.** Button actions create
   Tasks via the queue, never bypass it.
3. **No invented stale-opportunity reasons.** The reason is computed
   from `lastActivityDate` and stage — don't editorialize.
4. **Renewal checkpoint stages come from `renewalOutreachStage`** —
   don't invent or paraphrase. Map missing values to `Identified`.
5. **Idempotent by day.** Don't double-post; use `--force` only when a
   prior post failed silently or a config change requires a re-emit.
6. **Whale accounts always escalate to producer.** Never `Dismiss`
   whale rows automatically.
7. **Sensitive numbers go in the row, not the headline.** Premiums and
   renewal-increase numbers are fine in the Slack post body; PHI / DOB
   / EIN never appear in sentinel output.

## Common triage shorthand

```
[STALE] Acme Plumbing - GL  →  /skill crm-fact-retriever  +  /skill crm-intake-writer
[R-90]  JB Noble - WC       →  /skill renewal-review
[R-30]  JB Noble - WC       →  /skill renewal-review  →  /skill carrier-appetite  →  /skill proposal-builder
[XDATE] Joseph Washington   →  /skill crm-fact-retriever  +  /skill crm-intake-writer
[WHALE] Anchor Holdings     →  Slack DM producer; do not auto-resolve
```

## References

- `docs/revenue-sentinel.md`
- `docs/revenue-integrity.md`
- `hermes/commands/revenue.py`
- `renewal-review`, `crm-fact-retriever`, `crm-intake-writer`,
  `carrier-appetite`, `proposal-builder`
