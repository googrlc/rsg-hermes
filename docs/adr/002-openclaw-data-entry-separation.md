# ADR 002: Separate Data Entry (OpenClaw) from Reconciliation Intelligence (Hermes)

**Date:** 2025-05-08  
**Status:** Proposed  
**Deciders:** RSG Engineering  

---

## Context

Hermes currently serves dual roles:
1. **Data Entry Interface** — Natural language commands for creating/updating CRM records
2. **Operations Intelligence** — Commission auditing, renewal tracking, reconciliation, dashboards, governance

This creates tension:
- Chat-based UI is suboptimal for structured, high-volume data entry
- Hermes' cognitive load is split between routine CRUD and complex reconciliation logic
- Validation happens post-submission rather than pre-flight
- No separation between "forms work" and "intelligence work"

OpenClaw (new repo) will become the dedicated UX layer for structured data entry, while Hermes focuses on being the chief CRM agent for reconciliation, BI, and governed automation.

---

## Decision

**Move ad-hoc/structured data entry from Hermes to OpenClaw.**

**Reposition Hermes as the Chief CRM Intelligence Agent** handling:
- Reconciliation (commission, policy, renewal)
- Business Intelligence (dashboards, KPIs, analytics)
- Governance (guardrails, queue mediation, audit trails)
- Complex natural language operations (research, lookups, casual intake)
- Automated workflows (sync jobs, scheduled reports, escalations)

---

## Architecture

### OpenClaw Responsibilities (New Repo)
```
openclaw/
├── forms/
│   ├── contact_wizard.tsx        # Multi-step contact creation
│   ├── account_onboarding.tsx    # Account setup with FEIN/DOT validation
│   ├── lead_intake_form.tsx      # Structured lead capture
│   ├── policy_bind_flow.tsx      # Policy binding workflow
│   └── task_templates.tsx        # Pre-built task creators
├── validation/
│   ├── field_rules.ts            # Format validation (email, phone, FEIN)
│   ├── duplicate_checker.ts      # Real-time duplicate detection
│   └── required_fields.ts        # Dynamic required field logic
├── workflows/
│   ├── bulk_import.tsx           # CSV upload + mapping
│   ├── mass_update.tsx           # Bulk record modifications
│   └── role_dashboards.tsx       # Producer vs CS rep views
└── api/
    └── hermes_bridge.ts          # Submit to Hermes queue for processing
```

**Key Characteristics:**
- React/TypeScript frontend (or preferred stack)
- Pre-flight validation before any CRM mutation
- Form state management with draft/save/resume
- User-friendly error messages and guidance
- Integration with Hermes API for queue submission

### Hermes Responsibilities (Refocused)
```
hermes/
├── core/
│   ├── nl_agent.py               # Complex NL operations only
│   ├── dispatcher.py             # Route to specialized handlers
│   └── guardrails.py             # Blocked action prevention
├── reconciliation/
│   ├── commission_audit.py       # Statement matching, variance detection
│   ├── policy_reconciler.py      # NowCerts ↔ EspoCRM sync validation
│   └── renewal_tracker.py        # Project 85 lifecycle management
├── intelligence/
│   ├── dashboard_kpis.py         # KPI snapshot generation
│   ├── revenue_sentinel.py       # Proactive briefing engine
│   └── eom_scorecards.py         # Month-end rollups
├── automation/
│   ├── morning_policy_sync.py    # 7am NowCerts → Supabase → EspoCRM
│   ├── nightly_updates.py        # Policy change summaries to Slack
│   └── queue_worker.py           # Process crm_write_queue → EspoCRM
└── commands/
    ├── lookup.py                 # Search, field value retrieval
    ├── research.py               # Business research + CRM save
    └── reports.py                # Pipeline, KPI, renewal audits
    # REMOVED: data_entry.py → moves to OpenClaw
```

**Key Characteristics:**
- No direct form handling — receives structured payloads via queue
- Focus on cross-system reconciliation (Supabase ↔ EspoCRM ↔ NowCerts)
- BI generation from aggregated data
- Governance enforcement (channel registry, receipt logging)
- Complex NL understanding for edge cases humans can't easily form-ify

---

## Consequences

### Positive

1. **Better UX for Routine Work**
   - Forms are faster than chat for structured data entry
   - Pre-flight validation prevents errors before submission
   - Multi-step wizards guide users through complex processes

2. **Hermes Performance Improvement**
   - Reduced cognitive load on NL agent
   - Faster response times for complex queries
   - Clearer separation of concerns in codebase

3. **Improved Data Quality**
   - Validation at point of entry (OpenClaw) + reconciliation (Hermes)
   - Duplicate detection before CRM submission
   - Form state allows draft/review before commit

4. **Scalability**
   - OpenClaw handles high-volume routine entry
   - Hermes focuses on high-value intelligence work
   - Parallel development tracks possible

5. **Clearer Audit Trail**
   - Form submissions logged separately from AI decisions
   - Easier to trace: "Was this a user form entry or AI-generated?"

### Negative

1. **Development Overhead**
   - New repo to maintain (OpenClaw)
   - Need to build form infrastructure from scratch
   - Additional integration testing between systems

2. **Migration Complexity**
   - Existing `data_entry.py` logic must be ported/refactored
   - Users need training on new interface
   - Potential temporary feature parity gaps

3. **Coordination Overhead**
   - Two repos to keep in sync for API contracts
   - Version compatibility management
   - More moving parts in deployment pipeline

---

## Migration Plan

### Phase 1: Foundation (Week 1-2)
- [ ] Create OpenClaw repository with basic scaffolding
- [ ] Define API contract between OpenClaw and Hermes
- [ ] Set up Hermes API endpoints for form submission (`POST /api/v1/queue`)
- [ ] Document field mappings and validation rules

### Phase 2: Core Forms (Week 3-5)
- [ ] Build Contact/Account creation wizard
- [ ] Implement Lead intake form
- [ ] Create Task template system
- [ ] Add real-time duplicate detection
- [ ] Wire form submission → Hermes CRM queue

### Phase 3: Advanced Workflows (Week 6-8)
- [ ] Policy binding flow with carrier selection
- [ ] Bulk import (CSV upload + field mapping)
- [ ] Mass update interface
- [ ] Role-specific dashboards

### Phase 4: Hermes Refactoring (Week 9-10)
- [ ] Extract `data_entry.py` logic into reusable validation library
- [ ] Move shared validation to separate package (usable by both repos)
- [ ] Deprecate direct data entry commands in Hermes CLI
- [ ] Enhance NL agent to redirect: "I can help you create a contact — would you like to use the form? Here's the link..."

### Phase 5: Cutover (Week 11-12)
- [ ] User training and documentation
- [ ] Gradual rollout: forms for new entries, chat remains for lookups/complex
- [ ] Monitor adoption and gather feedback
- [ ] Sunset direct data entry commands after migration period

---

## API Contract: OpenClaw → Hermes

### Submit Form Data
```http
POST https://hermes.rsg.internal/api/v1/queue
Authorization: Bearer <OPENCLAW_SERVICE_TOKEN>
Content-Type: application/json

{
  "source": "openclaw",
  "source_user_id": "user_123",
  "source_form": "contact_wizard",
  "action": "create",
  "entity": "Contact",
  "fields": {
    "firstName": "John",
    "lastName": "Smith",
    "emailAddress": "john@example.com",
    "accountId": "acc_456"
  },
  "metadata": {
    "form_version": "1.2.0",
    "validation_passed": true,
    "duplicate_check_run": true
  }
}
```

### Response
```json
{
  "queue_id": "q_789",
  "status": "PENDING",
  "estimated_processing_time_seconds": 5,
  "receipt_url": "/api/v1/receipts/q_789"
}
```

### Check Status
```http
GET https://hermes.rsg.internal/api/v1/receipts/q_789
```

---

## What Happens to Existing Hermes Commands?

| Command | Fate | Notes |
|---------|------|-------|
| `hermes 'add contact John Smith'` | **Deprecated** → redirects to form | Keep for backward compat during migration |
| `hermes 'create Task name="Call client"'` | **Deprecated** → redirects to form | Template-based tasks stay in Hermes for automation |
| `hermes 'intake: met Juan at Peterbilt...'` | **Stays in Hermes** | Unstructured NL intake remains valuable |
| `hermes 'total premium for Acme'` | **Stays in Hermes** | Lookup/analytics remain core strength |
| `hermes 'renewal audit'` | **Stays in Hermes** | BI/reconciliation is Hermes' future |
| `hermes --revenue-sentinel` | **Stays in Hermes** | Automated intelligence |
| `hermes --commission-audit` | **Stays in Hermes** | Reconciliation core competency |

---

## Success Metrics

| Metric | Baseline | Target (Post-Migration) |
|--------|----------|-------------------------|
| Time to create new contact | 45 seconds (chat) | 20 seconds (form) |
| Data entry error rate | ~8% | <2% |
| Hermes response time (NL queries) | 3.2s avg | <1.5s avg |
| User satisfaction (routine entry) | 6.5/10 | >8.5/10 |
| Duplicate records created/month | 15-20 | <5 |
| Commission audit variance detected | Manual | Automated, <24hr detection |

---

## Alternatives Considered

### Alternative 1: Keep Everything in Hermes
**Rejected because:** Chat is suboptimal for structured forms; Hermes becomes bloated; no separation of concerns.

### Alternative 2: Build Forms Inside EspoCRM
**Rejected because:** EspoCRM customization is PHP-heavy; slower iteration; couples UX to CRM vendor; Hermes loses governance layer.

### Alternative 3: Use Low-Code Form Builder (Retool, etc.)
**Rejected because:** Ongoing licensing costs; less control over UX; vendor lock-in; integration complexity similar to custom build.

---

## References

- [`docs/hermes-operating-constitution.md`](hermes-operating-constitution.md) — Defines Hermes roles and guardrails
- [`hermes/commands/data_entry.py`](../hermes/commands/data_entry.py) — Current implementation to migrate
- [`hermes/core/nl_agent.py`](../hermes/core/nl_agent.py) — NL agent that will be refocused
- [`docs/REPOSITORY_BOUNDARIES.md`](REPOSITORY_BOUNDARIES.md) — Repository boundary guidelines

---

## Appendix: OpenClaw Tech Stack Recommendations

**Frontend:**
- React 18+ with TypeScript
- Vite for build tooling
- Tailwind CSS + shadcn/ui for components
- React Hook Form + Zod for form state/validation
- TanStack Query for server state

**Backend (if needed):**
- Node.js/Express or Next.js API routes
- Or go serverless: Vercel/Netlify functions

**Integration:**
- Hermes REST API (existing `hermes-api`)
- WebSocket for real-time queue status updates
- OAuth2 service-to-service auth with Hermes

**Deployment:**
- Docker container (align with Hermes deployment model)
- Or static hosting + serverless functions
