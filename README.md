# Hermes (rsg-hermes)

Python coordinator for **EspoCRM** over the REST API: REPL / one-shot CLI, optional **Slack** Socket Mode, and pluggable commands (lookup, data entry, revenue views).

The **EspoCRM customization repo** (PHP metadata, hooks, field reference) stays separate: [googrlc/rsg-espocrm](https://github.com/googrlc/rsg-espocrm). This repo only talks to Espo via HTTP; it does not ship Espo source or custom PHP.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env   # then set ESPO_URL and ESPO_API_KEY
```

## Run

```bash
hermes --ping
hermes --doctor       # auth + core CRM read + metadata readiness
hermes --kpi
hermes --audit-schema  # writes schema_map.json
hermes --slack        # needs SLACK_* tokens in .env
hermes --revenue-sentinel
hermes --revenue-sentinel-dry-run
hermes --revenue-sentinel-health
hermes --commission-audit
hermes --commission-audit-dry-run
hermes --eom-scorecard
hermes --eom-scorecard-dry-run
hermes --commission-reconcile-file ./statements/carrier.csv
hermes 'What is Jane phone'
hermes 'total premium for Acme'
hermes 'renewal audit'
```

## Docker

```bash
docker compose up -d --build
docker compose logs -f hermes
```

The container defaults to `hermes --slack`, reads `.env`, and uses host networking so it can reach Tailscale/IP-only services from the VPS host.

Use `docker exec rsg-hermes hermes --doctor` after credential or permission changes. `--ping` only proves the API key can authenticate; `--doctor` proves Hermes can read Account, Contact, Opportunity, and metadata without writing anything.

Slack fallback replies default to `#systems-check` (`C0AFHN83ZE3`). Set `HERMES_SLACK_FALLBACK_CHANNEL` when moving Hermes to a dedicated CRM officer channel.

If you run `hermes --audit-schema` outside the Slack service, run it in the same mounted project directory or copy the generated `schema_map.json` beside the running container. The file is intentionally gitignored because it is runtime cache.

## Project 85 Sentinel (Revenue Guardrail)

`hermes --revenue-sentinel` runs one proactive briefing that:
- flags stale opportunities (14+ days),
- surfaces active renewals at 90/60/30-day checkpoints,
- surfaces x-date opportunities at 60 days,
- bubbles whale accounts to the top,
- posts to `HERMES_SENTINEL_SLACK_CHANNEL` with interactive buttons.

Recommended schedule (outside Hermes): weekdays at 08:00 `America/New_York`.
Example cron:

```bash
0 8 * * 1-5 cd /path/to/rsg-hermes && /path/to/venv/bin/hermes --revenue-sentinel
```

Use `--revenue-sentinel-force` to bypass idempotency and post again on the same day.
Use `--revenue-sentinel-dry-run` to preview output without posting.
Use `--revenue-sentinel-health` to verify freshness and required config.

## TLS Note

Hermes defaults `HERMES_VERIFY_TLS=false` to preserve the current Tailscale/IP HTTPS behavior. Set it to `true` only when EspoCRM is reachable with a matching certificate chain.

See `docs/espocrm.md` for how this relates to the RSG EspoCRM repo.
