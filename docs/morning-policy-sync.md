# Morning Policy Sync (7am Cron Job)

## Overview

The **Morning Policy Sync** is a daily automated workflow that runs at 7:00 AM to synchronize policy data from NowCerts (source of truth) through Supabase (golden record hub) into EspoCRM, with a summary posted to Slack.

## Workflow Architecture

```
┌─────────────┐      ┌──────────────┐      ┌─────────────┐      ┌─────────────┐
│  NowCerts   │ ───► │   Supabase   │ ───► │  EspoCRM    │ ───► │    Slack    │
│  (Source)   │      │  (Golden)    │      │     (CRM)   │      │  (Summary)  │
└─────────────┘      └──────────────┘      └─────────────┘      └─────────────┘
     │                      │                     │                    │
     │ 1. Fetch policies    │ 2. Stage records    │ 3. Push updates    │ 4. Post digest
     │    & insureds        │    & mappings       │    to Policy       │    with stats
     │                      │                     │    entities        │
```

## What It Does

### Step 1: Sync Insureds → Accounts
- Fetches updated insured records from NowCerts (changed in last 24 hours by default)
- Runs the existing `run_insured_to_account_sync` pipeline
- Matches insureds to EspoCRM Accounts using:
  1. Existing sync mappings
  2. Dedup key (momentumClientId)
  3. FEIN lookup
  4. Email lookup
  5. Name fuzzy match
- Creates or updates Accounts in EspoCRM via outbound queue

### Step 2: Fetch Policies from NowCerts
- Pulls all policies modified since the lookback window (default: 24 hours)
- Uses OData pagination with `$filter=changeDate ge datetime'{since}'`
- Stages each policy in `inbound_sync_staging` table

### Step 3: Push Policies to EspoCRM
- Resolves or creates sync mappings for each policy
- Maps NowCerts policy fields to EspoCRM Policy entity:
  - `policyNumber` ← `number`
  - `carrier` ← `carrierName`
  - `effectiveDate` ← `effectiveDate`
  - `expirationDate` ← `expirationDate`
  - `premium` ← `premium`
  - `status` ← `status`
  - `type` ← `type`
- Creates new Policy records or updates existing ones
- Updates sync_mappings with EspoCRM IDs

### Step 4: Slack Notification
Posts a formatted summary to Slack including:
- Total policies synced
- New vs updated counts
- Accounts synced count
- Any errors (first 5 shown)
- Warning count

### Step 5: CRM Audit Trail
Creates a Task in EspoCRM with:
- Sync summary statistics
- Error details (if any)
- Timestamp and run metadata

## Usage

### Manual Execution

```bash
# Dry run (no writes, no Slack post)
hermes --morning-sync-dry-run

# Normal run (respects idempotency guard)
hermes --morning-sync

# Force re-run (bypass "already sent today" check)
hermes --morning-sync-force

# Custom lookback window (e.g., last 48 hours)
hermes --morning-sync --morning-sync-hours 48
```

### Cron Installation

See `/workspace/docs/cron-jobs.txt` for complete cron configuration.

```bash
# Install cron jobs
crontab /workspace/docs/cron-jobs.txt

# Verify
crontab -l
```

## Environment Variables

Required environment variables:

```bash
# NowCerts API
export NOWCERTS_API_URL="https://api.nowcerts.com"
export NOWCERTS_USERNAME="your_username"
export NOWCERTS_PASSWORD="your_password"

# Supabase
export SUPABASE_URL="https://xxx.supabase.co"
export SUPABASE_SERVICE_ROLE_KEY="your_service_role_key"

# EspoCRM
export ESPO_URL="https://your-espocrm.com"
export ESPO_API_KEY="your_api_key"

# Slack
export SLACK_BOT_TOKEN="xoxb-..."
export HERMES_SENTINEL_SLACK_CHANNEL="D0B2PJYLGQG"

# Optional configuration
export HERMES_SENTINEL_TIMEZONE="America/New_York"
export HERMES_MORNING_SYNC_HOURS="24"
export HERMES_STATE_DIR="/tmp/hermes"
```

## Database Tables Used

### sync_mappings
Stores cross-system ID mappings:
- `nowcerts_entity_type`: "Insured" or "Policy"
- `nowcerts_id`: NowCerts database ID
- `espocrm_entity_type`: "Account" or "Policy"
- `espocrm_id`: EspoCRM record ID
- `match_method`: How the match was resolved
- `match_confidence`: Confidence score (0.0-1.0)

### inbound_sync_staging
Temporary staging for raw payloads:
- `source_system`: "nowcerts"
- `source_object_type`: "Insured" or "Policy"
- `source_object_id`: Source system ID
- `raw_payload`: JSON blob of source data
- `payload_hash`: For change detection
- `processing_status`: pending/queued/processed/failed

## Error Handling

- **Idempotency**: Won't post duplicate Slack messages for same day (unless `--force`)
- **Retry logic**: Built-in retries for API failures (configurable via env vars)
- **Error collection**: Continues processing on individual record failures
- **Audit trail**: All errors logged to both Slack and CRM Task

## Monitoring

### Logs
```bash
tail -f /var/log/hermes/morning_sync.log
```

### Slack Channel
Check the configured Slack channel for daily summaries and error alerts.

### CRM Tasks
Search EspoCRM Tasks for "Morning Policy Sync" to see audit history.

## Troubleshooting

### "Already sent today" message
The job has already run successfully today. Use `--morning-sync-force` to re-run.

### Connection failures
Check environment variables and network connectivity:
```bash
hermes --ping  # Test EspoCRM connection
# Test other connections manually or add diagnostic commands
```

### Missing policies in CRM
1. Check `sync_mappings` table for unresolved mappings
2. Verify field mapping matches your EspoCRM schema
3. Review error logs for specific failure reasons

### Slack notifications not posting
Verify `SLACK_BOT_TOKEN` is valid and bot has permission to post to the channel.

## Customization

### Adjust Lookback Window
```bash
export HERMES_MORNING_SYNC_HOURS=48  # Sync last 48 hours
```

### Change Timezone
```bash
export HERMES_SENTINEL_TIMEZONE="America/Los_Angeles"
```

### Modify Field Mapping
Edit `_map_policy_to_crm()` in `/workspace/hermes/jobs/morning_policy_sync.py` to match your EspoCRM schema.

## Related Workflows

- **Nightly CRM Changelog** (`--changelog`): Summarizes all CRM changes at 11pm
- **Bidirectional Sync** (`--sync-bidirectional`): Full two-way sync every 6 hours
- **Revenue Sentinel** (`--revenue-sentinel`): Daily revenue KPI briefing at 9am

## Architecture Notes

- **NowCerts is source of truth** for policy data
- **Supabase is golden record hub** for cross-system reconciliation
- **EspoCRM is operational system** for client management
- **Slack is notification layer** for human visibility
- **All writes are audited** to sync_runs and sync_audit_log tables
