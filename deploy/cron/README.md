# Deploy: scheduled automations (cron)

The Hermes host runs its scheduled jobs as cron-fired `docker compose run --rm`
one-shots (the long-running workers — Slack bot, API, queue/intake workers — are
Docker services in `docker-compose.yml`, not here).

## Files

| File | Purpose |
|---|---|
| `hermes.crontab` | The managed cron block (source of truth). Edit here, not the live crontab. |
| `install-cron.sh` | Idempotent installer: replaces only the Hermes-managed block, preserves all other entries. |

## Install / re-install (on the host, `ssh hermes`)

```bash
sudo bash /opt/rsg-hermes/deploy/cron/install-cron.sh   # run after every redeploy
crontab -l | sed -n '/>>> hermes/,/<<< hermes/p'        # verify the block
tail -f /root/hermes-cron.log                           # watch runs
```

The schedule was lost once in the `hermes-elestio → hermes-gretch` migration
(it lived only on the box), which silently stopped Outlook triage and every
revenue briefing. This artifact makes it repeatable.

## Scheduled jobs (times are ET via `CRON_TZ`)

| Job | Cadence | Output |
|---|---|---|
| `--sync-nowcerts` | daily 2:00am | Account Sync v2: NowCerts Insured → EspoCRM Account |
| `--sync-policies` | daily 2:10am | Policy Sync v2: NowCerts Policy → EspoCRM Policy (policies only, no Account writes) |
| `--email-triage` (ms365) | every 30 min | actionable Outlook mail → intake queue |
| `--snapshot-kpis` | daily 6:00am | Supabase `dashboard_kpis` (no Slack) |
| `--renewal-reconcile` | daily 6:15am | retry due Renewal Loop v6 AMS writebacks, alert `#systems-check` on failures |
| `--renewal-classify` | daily 7:30am | reclassify `project_85_renewals` risk |
| `--revenue-sentinel` | weekdays 8:00am | Project-85 briefing → #the-boss |
| `--renewal-sweep` | weekdays 8:15am | renewal prep Tasks for Gretchen |
| `--commission-audit` | Mondays 7:00am | Revenue-integrity audit → Slack |
| `--eom-scorecard` | 1st of month 7:00am | End-of-month scorecard → Slack |
| `--changelog` | nightly 8:00pm | Nightly CRM changelog → #the-boss |

## Deliberately NOT scheduled

`--sync-bidirectional` — full round-trip bidirectional sync. Not scheduled; run on
demand only while the account/insured dedup story is fully settled.

## Prerequisites

- `.env` on the host has the job credentials (`MS365_*`, `SLACK_BOT_TOKEN`,
  `HERMES_SENTINEL_*`, Supabase keys).
- The `hermes-intake-worker` and `hermes-crm-queue-worker` services are up — they
  drain what these jobs enqueue.

## Cloud-scheduled agents (separate — your `/schedule` routines, not cron)

`book-health-monitor` (Mon 10am — writes `agency_snapshots`, which feeds the
dashboard retention trend), `retention-risk-scout` (Wed 9am), and
`gretchen-daily-queue` (weekdays 8:30am) are Claude **cloud** agents with no box
CLI entry point. Schedule them via `/schedule`, not here.
