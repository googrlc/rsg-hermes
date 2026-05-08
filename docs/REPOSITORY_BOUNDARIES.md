# Repository Boundaries: Hermes Integration Architecture

## Overview

This document clarifies which code lives where in the NowCerts ↔ Supabase ↔ EspoCRM workflow.

## Repository Responsibilities

### 1. Hermes (`/workspace/hermes/`) - Integration Hub ✅

**Purpose:** Orchestrates all cross-system workflows, API clients, and business logic.

**Contains:**
```
hermes/
├── core/
│   └── client.py              # EspoCRM REST API client
├── integrations/
│   ├── supabase_client.py     # Supabase PostgREST client
│   └── slack_notifier.py      # Slack Bot API client
├── sync/
│   ├── pipeline.py            # NowCerts → Supabase → EspoCRM pipeline
│   ├── bidirectional.py       # Full bidirectional sync orchestrator
│   ├── field_mapper.py        # Field transformation mappings
│   └── nowcerts_client.py     # NowCerts API client
├── jobs/
│   ├── morning_policy_sync.py # 7am daily sync cron job
│   └── nightly_changelog.py   # 11pm nightly summary job
├── commands/
│   └── sync.py                # CLI commands (hermes --sync-*)
└── operations/
    └── crm_queue_worker.py    # Background queue processor
```

**Key Principle:** All integration logic lives here. This is the **only** place where:
- EspoCRM API calls are made
- Supabase writes occur for sync purposes
- NowCerts data is fetched
- Cross-system field mapping happens
- Sync orchestration logic exists

---

### 2. Supabase (`/workspace/supabase/`) - Data Layer 📦

**Purpose:** Defines database schema, security policies, and seed data.

**Contains:**
```
supabase/
├── migrations/
│   ├── 20260501131246_hermes_ai_master_schema.sql  # Core tables
│   ├── 20260501144500_hermes_service_role_rls.sql  # RLS policies
│   ├── 20260507010000_sync_control_tables.sql      # sync_* tables
│   ├── 20260507020000_golden_record_tables.sql     # crm_* tables
│   └── ...                                         # Schema changes
└── seeds/
    └── hermes_operations_seed.sql                   # Test data
```

**Tables Managed:**
- `inbound_sync_staging` - Raw incoming data
- `outbound_sync_queue` - Pending outbound writes
- `sync_mappings` - Cross-system ID mappings
- `sync_runs` - Sync execution history
- `sync_audit_log` - Detailed audit trail
- `crm_accounts` - Golden record for accounts
- `crm_commissions` - Golden record for commissions

**What DOES NOT go here:**
- ❌ Business logic (belongs in Hermes)
- ❌ API call orchestration (belongs in Hermes)
- ❌ Field mapping rules (belongs in Hermes)
- ❌ Cron job scheduling (belongs in Hermes + system cron)

**Exception:** Simple stored procedures for performance-critical operations ONLY:
- ✅ Hash computation for change detection
- ✅ Basic validation constraints
- ✅ Audit log triggers

---

### 3. EspoCRM (External System) - Operational CRM 🔒

**Purpose:** Client management, task tracking, operational workflows.

**Configuration (managed outside this repo):**
- Policy entity definition
- Account entity custom fields
- Role/permission configurations
- Workflow automations
- UI layouts

**Integration Points:**
- REST API at `{ESPO_URL}/api/v1/`
- Entities: `Account`, `Policy`, `Contact`, `Task`, `Opportunity`
- Authentication: X-Api-Key header

**What Hermes Does:**
- ✅ Reads from EspoCRM (modified records, lookups)
- ✅ Writes to EspoCRM (policy updates, new accounts, tasks)
- ✅ Creates audit Tasks for sync runs

**What Hermes Does NOT Do:**
- ❌ Modify EspoCRM schema
- ❌ Change permission roles
- ❌ Configure UI layouts
- ❌ Manage EspoCRM users

---

## Data Flow Examples

### Morning Policy Sync (7am)

```
1. Hermes job triggers (cron)
   ↓
2. NowCerts API → fetch updated policies
   ↓
3. Supabase → stage in inbound_sync_staging
   ↓
4. Hermes → resolve mappings, transform fields
   ↓
5. EspoCRM API → update/create Policy records
   ↓
6. Supabase → update sync_mappings, log audit
   ↓
7. Slack API → post summary
   ↓
8. EspoCRM → create Task with sync summary
```

**Code Locations:**
- Step 1: `hermes/jobs/morning_policy_sync.py` + system cron
- Step 2: `hermes/sync/nowcerts_client.py`
- Step 3: `hermes/integrations/supabase_client.py`
- Step 4: `hermes/sync/field_mapper.py` + `hermes/sync/pipeline.py`
- Step 5: `hermes/core/client.py` (EspoClient)
- Step 6: `hermes/integrations/supabase_client.py`
- Step 7: `hermes/integrations/slack_notifier.py`
- Step 8: `hermes/core/client.py` (EspoClient)

---

### Bidirectional Sync (Every 6 Hours)

```
Direction A: NowCerts → Supabase → EspoCRM
  hermes/sync/pipeline.py::run_insured_to_account_sync()

Direction B: EspoCRM → Supabase
  hermes/sync/bidirectional.py::run_crm_to_hub()

Direction D: Supabase → NowCerts
  hermes/sync/bidirectional.py::run_hub_to_nowcerts()
```

All orchestrated by: `hermes/sync/bidirectional.py::run_bidirectional()`

---

## Common Questions

### Q: "Should I put this function in Supabase or Hermes?"

**A:** Ask these questions:
1. Does it call an external API? → **Hermes**
2. Does it transform data between systems? → **Hermes**
3. Is it pure SQL for performance? → **Maybe Supabase**
4. Does it orchestrate a workflow? → **Hermes**
5. Is it a database constraint? → **Supabase**

### Q: "Where do field mappings live?"

**A:** **Hermes** (`hermes/sync/field_mapper.py`) because:
- They're business logic, not database schema
- They need to be versioned with sync code
- They require testing across systems

### Q: "Should Supabase have stored procedures for sync?"

**A:** Only if:
- Performance requires it (measured, not assumed)
- It's simple data transformation (hash, format)
- It doesn't involve business rules

Example OK:
```sql
CREATE FUNCTION sync.compute_payload_hash(jsonb) RETURNS text
```

Example NOT OK:
```sql
CREATE FUNCTION sync.resolve_customer_mapping(...) 
  -- This is business logic, belongs in Hermes
```

### Q: "How do I add a new field to the sync?"

**A:**
1. **EspoCRM:** Add field via EspoCRM admin UI (external)
2. **Supabase:** Add column if needed in golden record table (migration)
3. **Hermes:** Update field mapper (`field_mapper.py`) and pipeline

---

## Testing Boundaries

### Unit Tests (`/workspace/tests/`)
- Test Hermes clients with mocks
- Test field mappers with sample data
- Test pipeline logic with fake APIs

### Integration Tests
- Test against Supabase dev instance
- Test against EspoCRM sandbox
- Test end-to-end sync flows

### What NOT to Test Here
- EspoCRM internal logic (test in EspoCRM)
- Supabase RLS policies (test in Supabase repo)
- NowCerts API behavior (mock it)

---

## Deployment

### Hermes Deployment
```bash
# Deploy as container or service
docker-compose up -d
# or
systemctl start hermes-sync
```

### Supabase Migrations
```bash
# Apply via Supabase CLI
supabase db push
# or
psql $DATABASE_URL -f migrations/xxx.sql
```

### Cron Jobs
```bash
# Install cron schedule
crontab /workspace/docs/cron-jobs.txt
```

---

## Summary

| Concern | Location | Why |
|---------|----------|-----|
| API Clients | Hermes | Integration logic |
| Field Mappings | Hermes | Business rules |
| Sync Orchestration | Hermes | Workflow control |
| Cron Jobs | Hermes + System | Scheduling |
| Database Schema | Supabase | Data layer |
| RLS Policies | Supabase | Security |
| CRM Configuration | EspoCRM | External system |
| Audit Logging | Supabase (tables) + Hermes (writes) | Both |

**Golden Rule:** If it touches more than one system, it lives in **Hermes**.

## Related Documents

- [Duplicate Analysis & Merge Plan](DUPLICATE_ANALYSIS_AND_MERGE_PLAN.md) - Comprehensive audit showing no problematic duplicates
- [Morning Policy Sync](morning-policy-sync.md) - 7am cron job implementation details
- [Bidirectional Sync Plan](bidirectional-sync-plan.md) - Full sync architecture
