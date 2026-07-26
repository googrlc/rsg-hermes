# Project 85 Sentinel

Project 85 Sentinel is a Hermes-native revenue guardrail that posts a daily Slack briefing built from the CRM data.

## What It Checks

- `STALE LEADS`: Opportunity records in prospecting states with no activity for `HERMES_SENTINEL_STALE_DAYS` (default: 14).
- `PROJECT 85 RENEWALS`: Active `Policy` records at `HERMES_SENTINEL_RENEWAL_CHECKPOINTS` (default: `90,60,30`) with pipeline status from `Account.renewalOutreachStage`.
- `X-DATE OPPORTUNITIES`: `Lead` and closed-lost `Opportunity` records with x-date at `HERMES_SENTINEL_XDATE_DAYS` out (default: 60).

## Commands

- `hermes --revenue-sentinel`: run once and post to Slack.
- `hermes --revenue-sentinel-dry-run`: render message text without posting.
- `hermes --revenue-sentinel-force`: bypass the daily duplicate-post guard.
- `hermes --revenue-sentinel-health`: check freshness and config readiness.

## Reliability Controls

- **Retries:** the CRM reads go through `SupabaseClient` in-process; Slack posting uses `HERMES_SLACK_RETRIES` and `HERMES_SLACK_RETRY_SLEEP`.
- **Idempotency:** last successful post date is stored in `HERMES_SENTINEL_STATE_FILE`.
- **Partial output:** if one query fails, Sentinel still posts available sections and includes warnings.
- **Timezone consistency:** all date windows use `HERMES_SENTINEL_TIMEZONE` (default `America/New_York`).
- **Health signal:** `--revenue-sentinel-health` fails fast when last post is stale or required env vars are missing.
- **Checkpoint control:** configure renewal checkpoints with `HERMES_SENTINEL_RENEWAL_CHECKPOINTS` (comma-separated days).

If `HERMES_SENTINEL_SLACK_CHANNEL` is not set, Sentinel defaults to Slack channel `D0AUTEYHBDH`.

## Slack Loop-Back Actions — ⚠️ INERT

The briefing still renders three buttons, but **nothing handles the clicks.**
They were served by the Socket Mode listener (`hermes --slack`), which was
retired July 2026 — the flag no longer exists and no `block_actions` handler
remains in the tree. A click is silently dropped.

- `Remind me in 2 days` — was: create a task due in 2 days.
- `Assign to Gretchen` — was: create a task assigned to `HERMES_SENTINEL_GRETCHEN_USER_ID`.
- `Dismiss` — was: acknowledge without a CRM write.

Until an inbound path exists (Events API endpoint or re-enabled Socket Mode),
treat the briefing as read-only and act on it in the cockpit. Either wire a
handler or stop rendering the buttons.

