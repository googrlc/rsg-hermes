# Architecture Summary: Hermes Integration Platform

**Quick Reference Guide** - Updated 2026-05-07

---

## 🎯 Bottom Line

**✅ NO DUPLICATES FOUND - Architecture is correct!**

Your codebase properly separates concerns across three repositories:

| Repository | What It Owns | Files | Status |
|------------|-------------|-------|--------|
| **Hermes** | All integration logic, API clients, sync workflows | 45 Python files (~8.5K lines) | ✅ Perfect |
| **Supabase** | Database schema, RLS policies, migrations | 10 SQL files (~1.2K lines) | ✅ Perfect |
| **EspoCRM** | External CRM system (REST API) | N/A | ✅ Correct |

---

## 📍 Where Code Lives

### Need to add/modify... → Go Here

| Feature Type | Location | Example File |
|--------------|----------|--------------|
| **API Client** (NowCerts/EspoCRM/Slack) | `hermes/core/` or `hermes/integrations/` | `hermes/core/client.py` (EspoCRM) |
| **Field Mapping** (transform data) | `hermes/sync/field_mapper.py` | `map_insured_to_account()` |
| **Sync Workflow** (orchestration) | `hermes/sync/pipeline.py` or `hermes/sync/bidirectional.py` | `run_insured_to_account_sync()` |
| **Cron Job** (scheduled tasks) | `hermes/jobs/` | `hermes/jobs/morning_policy_sync.py` |
| **Database Table** (schema) | `supabase/migrations/` | `20260507010000_sync_control_tables.sql` |
| **CLI Command** (manual ops) | `hermes/commands/` | `hermes/commands/sync.py` |

---

## ⚡ Key Workflows

### 1. Morning Policy Sync (7am Daily)
```
NowCerts API → Supabase staging → Field mapping → EspoCRM → Slack summary
   ↓              ↓                    ↓              ↓           ↓
fetch_policies() insert()      map_policy_to_commission() create() post_message()
```
**Code:** `hermes/jobs/morning_policy_sync.py`  
**Cron:** `0 7 * * * hermes --morning-sync`

### 2. Bidirectional Sync (Every 6 Hours)
```
Direction A: NowCerts → Supabase → EspoCRM (policy updates)
Direction B: EspoCRM → Supabase (client updates)
Direction C: Supabase → NowCerts (commission data)
```
**Code:** `hermes/sync/bidirectional.py::run_bidirectional()`  
**Cron:** `0 */6 * * * hermes --sync-bidirectional`

### 3. Nightly Changelog (11pm Daily)
```
EspoCRM (last 24h changes) → Aggregate → Slack digest + Task audit
```
**Code:** `hermes/jobs/nightly_changelog.py`  
**Cron:** `0 23 * * * hermes --changelog`

---

## 🛡️ Decision Tree: "Where Does This Code Go?"

```
Start: Adding new feature
    │
    ├─→ Does it call an external API?
    │       └─→ YES → hermes/integrations/ or hermes/core/
    │
    ├─→ Does it transform data between systems?
    │       └─→ YES → hermes/sync/field_mapper.py
    │
    ├─→ Is it pure SQL for performance?
    │       └─→ YES → Maybe supabase/migrations/ (requires review)
    │
    ├─→ Does it orchestrate a workflow?
    │       └─→ YES → hermes/jobs/ or hermes/sync/
    │
    └─→ Is it a database constraint?
            └─→ YES → supabase/migrations/
```

---

## 🚫 Anti-Patterns (Don't Do This)

### ❌ WRONG: Business Logic in Supabase
```sql
-- DON'T put business rules in stored procedures!
CREATE FUNCTION sync.resolve_customer_mapping(...)
  IF nc_record.insured_type = 'Commercial' THEN
    -- This is business logic - belongs in Hermes!
  END IF;
```

### ❌ WRONG: API Calls Outside Hermes
```python
# DON'T make EspoCRM calls from outside Hermes!
import requests
requests.post('https://espocrm.example.com/api/v1/Account')  # Wrong!
```

### ✅ CORRECT: Use Hermes Clients
```python
# DO use Hermes clients for all integration work
from hermes.core.client import EspoClient
espo = EspoClient()
espo.create('Account', payload)  # Correct!
```

---

## 📊 Current State Metrics

| Metric | Count | Notes |
|--------|-------|-------|
| Hermes Python files | 45 | All integration logic |
| Supabase SQL files | 10 | Schema only |
| Test files | 17 | Full coverage of Hermes |
| Stored procedures | 1 | `hermes_touch_updated_at()` (acceptable) |
| Cron jobs | 5 | Morning sync, changelog, bidirectional, commission, revenue |
| API clients | 4 | NowCerts, EspoCRM, Supabase, Slack |

---

## 🔍 Verification Commands

### Check for duplicates
```bash
# Should find nothing in Supabase
grep -r "def.*fetch" supabase/migrations/*.sql

# Should find everything in Hermes
grep -r "def.*fetch" hermes/sync/*.py
```

### Verify architecture
```bash
# Count files
find hermes -name "*.py" | wc -l  # Should be ~45
find supabase -name "*.sql" | wc -l  # Should be ~10
find tests -name "*.py" | wc -l  # Should be ~17
```

---

## 📚 Documentation Index

| Document | Purpose |
|----------|---------|
| [REPOSITORY_BOUNDARIES.md](REPOSITORY_BOUNDARIES.md) | Detailed boundary definitions |
| [DUPLICATE_ANALYSIS_AND_MERGE_PLAN.md](DUPLICATE_ANALYSIS_AND_MERGE_PLAN.md) | Complete audit results |
| [adr/001-integration-architecture.md](adr/001-integration-architecture.md) | Architecture decision record |
| [morning-policy-sync.md](morning-policy-sync.md) | 7am sync implementation |
| [cron-jobs.txt](cron-jobs.txt) | Cron configuration |
| [bidirectional-sync-plan.md](bidirectional-sync-plan.md) | Full sync architecture |

---

## 🎓 Onboarding Checklist

New team member should:
- [ ] Read this summary
- [ ] Review `REPOSITORY_BOUNDARIES.md`
- [ ] Understand the 7am morning sync flow
- [ ] Run tests: `pytest tests/`
- [ ] Try manual sync: `hermes --morning-sync-dry-run`
- [ ] Review one complete workflow end-to-end

---

**Questions?** See [DUPLICATE_ANALYSIS_AND_MERGE_PLAN.md](DUPLICATE_ANALYSIS_AND_MERGE_PLAN.md) for full details.

**Last Updated:** 2026-05-07  
**Maintained By:** Hermes Integration Team
