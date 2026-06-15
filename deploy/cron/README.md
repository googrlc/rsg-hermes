# Deploy: scheduled jobs (cron)

The Hermes host runs one scheduled job that isn't a Docker service: the
**Microsoft 365 (Outlook) email triage** sweep. It's a `docker compose run --rm`
one-shot fired by cron, matching how the revenue-sentinel job runs.

## Files

| File | Purpose |
|---|---|
| `hermes-email-triage.cron` | The crontab line (source of truth — edit here, not the live crontab). |
| `install-cron.sh` | Idempotent installer: preserves existing crontab entries, replaces only the triage line. |

## Install / re-install (on the host, e.g. `ssh hermes`)

```bash
sudo bash /opt/rsg-hermes/deploy/cron/install-cron.sh
crontab -l | grep -- --email-triage     # verify
tail -f /root/hermes-email-triage.log    # watch a run (fires at :00 and :30)
```

Run this **after every redeploy**. The schedule was lost once in the
`hermes-elestio → hermes-gretch` migration (it lived only on the box, not in the
repo), which silently stopped Outlook mail from flowing into the intake pipeline.

## Prerequisites

- `.env` on the host has `MS365_TENANT_ID`, `MS365_CLIENT_ID`, `MS365_CLIENT_SECRET`,
  `MS365_MAILBOXES` (the Entra app-only registration — see
  [docs/integrations/email-triage-365.md](../../docs/integrations/email-triage-365.md)).
- The `hermes-intake-worker` service is up — it drains the `intake_submissions`
  rows that triage inserts. Without it, actionable mail queues but never synthesizes.

## Verify the whole lane end-to-end

```bash
# 1. dry run (reads mailbox, writes/moves nothing)
cd /opt/rsg-hermes && docker compose run --rm hermes \
  hermes --email-triage-dry-run --email-provider ms365 --email-since-hours 48

# 2. after a live run, actionable mail should reach awaiting_approval, not failed:
#    query intake_submissions where source='email-ms365' group by status
```
