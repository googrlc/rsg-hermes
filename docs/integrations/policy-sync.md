# NowCerts → EspoCRM policy sync (policies ONLY)

EspoCRM owns Accounts / Contacts / Opportunities. The **only** data that flows
from NowCerts into EspoCRM is **policies**. This job fetches updated NowCerts
policies and upserts them into the EspoCRM `Policy` entity, matched to an
**existing** account.

> This intentionally replaces the old `--sync-nowcerts` (Insured → Account)
> behavior for automation purposes. That sync creates duplicate-account garbage
> and is **deliberately not scheduled** (see [deploy/cron/README.md](../../deploy/cron/README.md)).

## What it does

- Reads NowCerts policies via `/api/PolicyDetailList` (incremental with `--since`).
- Maps each to an EspoCRM `Policy` payload — exact mixed-casing field names
  (`policy_number`, `line_of_business`, `effective_date`, `expiration_date`,
  `premium_amount`, `business_type`, `carrier`, `status` snake_case; `accountId`
  camelCase). Wrong casing is silently dropped by EspoCRM, so these are pinned and
  covered by tests in `tests/test_sync_field_mapper.py`.
- Matches the insured to an **existing** EspoCRM Account by exact name. **No
  match → skip and report**, never create an account (that's the garbage we avoid).
- Upserts by `policy_number`: updates the existing Policy or creates a new one.

## CLI

```bash
# Dry run first (reads NowCerts + EspoCRM, writes nothing). Cap with --limit.
hermes --sync-policies-dry-run --sync-policies-since 2026-06-01T00:00:00 --sync-policies-limit 50

# Go live once the dry-run looks right.
hermes --sync-policies --sync-policies-since 2026-06-01T00:00:00
```

Output reports `created / updated / skipped_no_account / skipped_no_number /
errors`, and lists the insured names that were skipped for lacking an account so
they can be created/linked in EspoCRM (by a human) before the next run.

## ⚠️ Deployment prerequisite — NowCerts credentials

The current Hermes host (`hermes-gretch`) is **CRM-only and has no `NOWCERTS_*`
env vars**, so this job cannot run there as-is. Before scheduling it, either:

1. Add NowCerts creds to that host's `.env`
   (`NOWCERTS_USERNAME`, `NOWCERTS_PASSWORD`, `NOWCERTS_CLIENT_ID`, …), **or**
2. Run it from a host that already has NowCerts access.

The EspoCRM write path (mapping, account matching, existing-policy detection) is
verified against the live CRM; only the NowCerts read awaits credentials.

## Scheduling (after creds exist + a clean dry-run)

Add one line to the managed block in
[deploy/cron/hermes.crontab](../../deploy/cron/hermes.crontab) and re-run the
installer, e.g. daily at 6:30am ET:

```cron
30 6 * * * cd /opt/rsg-hermes && docker compose run --rm hermes hermes --sync-policies --sync-policies-since "$(date -u -d '2 days ago' +\%Y-\%m-\%dT\%H:\%M:\%S)" >> /root/hermes-cron.log 2>&1
```
