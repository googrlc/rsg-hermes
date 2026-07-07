# Commission ingest — nightly schedule (macOS launchd)

Nightly job that pulls NowCerts policies, computes expected commission from
`commission_rules`, and upserts rows into Supabase `commission_ledger`
(Phase 2 of the Commission Command build spec).

## ⛔ HARD GATE — read first

**Do NOT activate this schedule until the ~4,000 glitched duplicate NowCerts
policies are purged and Lamar confirms.** NowCerts is READ-ONLY for this system;
the job never writes to the AMS. Policies tagged `PURGE-POLICY-2026-07` are
excluded automatically as a belt-and-suspenders filter, but the schedule still
stays off until the purge is done.

## Files

- `run-commission-sync.sh` — wrapper that loads `.env` (secrets stay out of the
  plist) and runs `hermes --commission-sync`.
- `com.rsg.hermes.commission-sync.plist` — launchd job, 02:00 local, `RunAtLoad=false`.

## Before activating (post-purge checklist)

1. Confirm the purge is complete (Lamar).
2. Verify credentials are in the repo `.env`: `NOWCERTS_USERNAME/PASSWORD`,
   `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SLACK_BOT_TOKEN`.
3. **Confirm NowCerts field names** against a live sample (the mapper is
   multi-key/case-insensitive but should be validated once):
   `hermes --commission-sync-dry-run` and eyeball the summary counts.
4. **One-time backfill** of the current active book (~104 policies), same code path:
   ```bash
   hermes --commission-backfill            # or --commission-sync-dry-run first
   ```
   Re-run it — idempotency means the second run reports 0 new / all updated.

## Activate

```bash
chmod +x deploy/launchd/run-commission-sync.sh
cp deploy/launchd/com.rsg.hermes.commission-sync.plist ~/Library/LaunchAgents/
launchctl load  ~/Library/LaunchAgents/com.rsg.hermes.commission-sync.plist
launchctl list | grep commission-sync      # confirm it's registered
```

## Deactivate

```bash
launchctl unload ~/Library/LaunchAgents/com.rsg.hermes.commission-sync.plist
```

## Notes

- Incremental cursor (watermark) is stored at `.hermes/commissions_watermark.json`
  (override via `HERMES_COMMISSIONS_WATERMARK_FILE`). `--commission-backfill`
  ignores it and pulls the whole book.
- One-line run summaries post to **#systems-check** (`C0ANSEP6SSD`).
- Logs: `~/Library/Logs/hermes-commission-sync.{log,err}`.
