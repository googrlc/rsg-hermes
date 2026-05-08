# ADR 001: Hermes-Centric Integration Architecture

**Date:** 2026-05-07  
**Status:** Accepted  
**Deciders:** Hermes Integration Team

---

## Context

We need to integrate three systems without duplicating business logic:

1. **NowCerts** - Policy administration system (source of truth for policy data)
2. **Supabase** - PostgreSQL database with PostgREST API
3. **EspoCRM** - Customer relationship management system

The integration must support:
- Morning policy sync (7am cron job): NowCerts → Supabase → EspoCRM
- Bidirectional sync (every 6 hours): All three systems stay in sync
- Nightly changelog: CRM changes summary to Slack
- Client updates from EspoCRM back to NowCerts
- Audit trails and compliance reporting

Without clear boundaries, we risk:
- Business logic duplication across repositories
- Inconsistent field mappings
- Difficult testing and debugging
- Deployment complexity
- Data integrity issues

---

## Decision

**All cross-system integration logic lives in Hermes.**

### Repository Responsibilities

#### Hermes (`hermes/`) - Integration Hub ✅
**Owns:**
- API clients for all external systems (NowCerts, EspoCRM, Supabase, Slack)
- Field mapping and transformation rules
- Sync orchestration and pipeline logic
- Cron job implementations
- CLI commands for manual operations
- Background workers and queue processors

**Does NOT own:**
- Database schema definitions
- CRM entity configurations
- External system user management

#### Supabase (`supabase/`) - Data Layer 📦
**Owns:**
- Database schema (tables, columns, indexes)
- Row-level security (RLS) policies
- Basic constraints and validations
- Seed data for testing

**Does NOT own:**
- Business logic
- API call orchestration
- Field mapping rules
- Workflow control

**Exception:** Simple stored procedures for performance-critical operations ONLY:
- Hash computation for change detection
- Timestamp maintenance (`hermes_touch_updated_at()`)
- Basic audit triggers

#### EspoCRM (External System) 🔒
**Managed via:** EspoCRM Admin UI (outside version control)
**Integration:** REST API at `{ESPO_URL}/api/v1/`

---

## Consequences

### Positive ✅

1. **Single Source of Truth**
   - All business rules in one place
   - No conflicting field mappings
   - Clear ownership of integration logic

2. **Testability**
   - Easy to mock external APIs
   - Unit tests for field mappers
   - Integration tests for pipelines
   - End-to-end test scenarios

3. **Deployment Simplicity**
   - Deploy Hermes independently
   - Apply Supabase migrations separately
   - No coordination required for most changes

4. **Maintainability**
   - New developers know where to look
   - Clear decision tree for new features
   - Reduced cognitive load

5. **Audit Trail**
   - All sync operations logged consistently
   - Centralized error handling
   - Unified monitoring and alerting

### Negative ⚠️

1. **Hermes Becomes Critical**
   - Single point of failure for integrations
   - Must ensure high availability
   - Requires robust error handling and retry logic

2. **Performance Considerations**
   - All transformations happen in application layer
   - May need caching for frequently accessed data
   - Database cannot optimize cross-system queries

3. **Team Coordination**
   - Hermes team must understand all three systems
   - Requires broader knowledge base
   - Cross-training essential

---

## Compliance

### Decision Tree for New Features

When adding new functionality, ask:

```
Does it call an external API?
├─ Yes → Hermes/integrations/
└─ No → Continue

Does it transform data between systems?
├─ Yes → Hermes/sync/field_mapper.py
└─ No → Continue

Is it pure SQL for performance?
├─ Yes → Maybe Supabase (requires review)
└─ No → Continue

Does it orchestrate a workflow?
├─ Yes → Hermes/jobs/ or Hermes/sync/
└─ No → Continue

Is it a database constraint?
├─ Yes → Supabase/migrations/
└─ No → Hermes/core/
```

### Boundary Violation Examples

**❌ WRONG - Business Logic in Supabase:**
```sql
CREATE FUNCTION sync.resolve_customer_mapping(...)
  -- Contains business rules for matching customers
  IF nc_record.insured_type = 'Commercial' THEN
    -- Business logic should not be here
  END IF;
```

**✅ CORRECT - Technical Utility in Supabase:**
```sql
CREATE FUNCTION hermes_touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
```

**✅ CORRECT - Business Logic in Hermes:**
```python
# hermes/sync/field_mapper.py
INSURED_TYPE_MAP = {
    "Commercial": "Commercial Lines",
    "Personal": "Personal Lines",
}

def map_insured_to_account(nc_record: dict) -> dict:
    # Business logic lives here
    return transformed_payload
```

---

## Validation

### Automated Checks

Pre-commit hook to prevent boundary violations:
```yaml
- id: check-supabase-business-logic
  name: Check Supabase for business logic
  entry: bash -c 'grep -r "CREATE.*FUNCTION" supabase/migrations/*.sql | grep -v "touch_updated_at" && exit 1 || exit 0'
```

CI/CD validation:
```yaml
- name: Verify Hermes has all integration clients
  run: |
    test -f hermes/core/client.py || exit 1
    test -f hermes/sync/nowcerts_client.py || exit 1
    test -f hermes/integrations/supabase_client.py || exit 1
```

### Monthly Audits

Checklist for architecture compliance:
- [ ] No new stored procedures in Supabase (except approved exceptions)
- [ ] All field mappings remain in Hermes
- [ ] Sync orchestration unchanged in Hermes
- [ ] Test coverage maintained for boundary compliance

---

## References

- [Repository Boundaries](../REPOSITORY_BOUNDARIES.md) - Detailed boundary documentation
- [Duplicate Analysis](../DUPLICATE_ANALYSIS_AND_MERGE_PLAN.md) - Comprehensive code audit
- [Morning Policy Sync](../morning-policy-sync.md) - Implementation example
- [Bidirectional Sync Plan](../bidirectional-sync-plan.md) - Full sync architecture

---

## Notes

This architecture was validated on 2026-05-07 with a comprehensive duplicate analysis that confirmed:
- 45 Python files in Hermes (~8,500 lines) - all integration logic ✅
- 10 SQL files in Supabase (~1,200 lines) - schema only ✅
- 1 acceptable stored procedure (`hermes_touch_updated_at`) ✅
- 17 test files covering all integration flows ✅

**Conclusion:** Architecture is sound and requires no reconciliation.
