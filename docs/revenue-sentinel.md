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

## Slack Loop-Back Actions

The briefing includes buttons handled by Hermes Socket Mode (`hermes --slack`):

- `Remind me in 2 days`: creates an the CRM `Task` due in 2 days.
- `Assign to Gretchen`: creates an the CRM `Task` assigned to `HERMES_SENTINEL_GRETCHEN_USER_ID`.
- `Dismiss`: acknowledges without CRM write.

To use interactive actions, ensure Slack Interactivity is enabled for the app and the Socket Mode bot is running.

