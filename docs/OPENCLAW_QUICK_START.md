# OpenClaw Quick Start Guide

## Decision Summary

**✅ YES — Move data entry to OpenClaw, refocus Hermes as Chief CRM Intelligence Agent**

This is the right architectural move for RSG. See full details in:
- [`OPENCLAW_ARCHITECTURE_SUMMARY.md`](OPENCLAW_ARCHITECTURE_SUMMARY.md) — Executive overview
- [`adr/002-openclaw-data-entry-separation.md`](adr/002-openclaw-data-entry-separation.md) — Full ADR with migration plan

---

## What Changes

### Before (Hermes Does Everything)
```
User: "Add contact John Smith email john@example.com"
  → Hermes parses NL → validates → creates in CRM
  ⚠️ Chat is slow for forms, validation happens after submission
```

### After (Split Responsibilities)
```
User: Opens OpenClaw Contact Wizard
  → Form validates in real-time → checks duplicates → submits to Hermes queue
  → Hermes processes queue → writes to CRM → logs receipt
  ✅ Fast UX, pre-flight validation, clear audit trail

User: "Total premium for Acme" or "Renewal audit"
  → Hermes handles directly (intelligence/reconciliation work)
  ✅ Hermes focuses on high-value operations
```

---

## Immediate Next Steps

### Week 1: Foundation

1. **Create OpenClaw Repository**
   ```bash
   # Option A: Separate repo (recommended)
   gh repo create openclaw --private --template=rsg-hermes
   
   # Option B: Monorepo structure
   mkdir -p /workspace/openclaw
   cd /workspace/openclaw
   npm create vite@latest . -- --template react-ts
   ```

2. **Set Up Basic Scaffolding**
   ```bash
   cd openclaw
   npm install
   npm install @tanstack/react-query react-hook-form zod @hookform/resolvers
   npm install tailwindcss postcss autoprefixer
   npx tailwindcss init -p
   ```

3. **Define API Contract** (Hermes side)
   
   Add to `hermes/api.py`:
   ```python
   @app.post("/api/v1/queue")
   async def submit_to_queue(payload: QueueSubmission):
       """Accept form submissions from OpenClaw"""
       # Validate source token
       # Insert into crm_write_queue table
       # Return queue_id and status URL
   ```

4. **Document Field Mappings**
   - Create `docs/FORM_FIELD_MAPPINGS.md`
   - List all EspoCRM entities and their required/optional fields
   - Include validation rules (regex patterns, enums, FK constraints)

### Week 2-3: First Form

Build the **Contact Creation Wizard**:
- Step 1: Name + Email (validate format, check duplicates)
- Step 2: Phone + Address (optional)
- Step 3: Link to Account (search + select)
- Step 4: Review + Submit

Submit to Hermes:
```typescript
// openclaw/src/api/hermes.ts
export async function submitContact(data: ContactFormData) {
  const response = await fetch('https://hermes.rsg.internal/api/v1/queue', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${OPENCLAW_SERVICE_TOKEN}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      source: 'openclaw',
      source_form: 'contact_wizard_v1',
      action: 'create',
      entity: 'Contact',
      fields: data,
      metadata: {
        validation_passed: true,
        duplicate_check_run: true
      }
    })
  });
  return response.json();
}
```

---

## Tech Stack Recommendation

### Frontend (OpenClaw)
| Tool | Purpose | Why |
|------|---------|-----|
| React 18+ | UI framework | Component model, ecosystem |
| TypeScript | Type safety | Catch errors early |
| Vite | Build tool | Fast HMR, modern |
| Tailwind CSS | Styling | Rapid UI development |
| shadcn/ui | Components | Beautiful, accessible |
| React Hook Form | Form state | Performance, DX |
| Zod | Validation | Schema-first, TS integration |
| TanStack Query | Server state | Caching, sync, mutations |

### Backend (Hermes Enhancements)
| Enhancement | Purpose |
|-------------|---------|
| `POST /api/v1/queue` | Accept form submissions |
| Service-to-service auth | OpenClaw ↔ Hermes security |
| Enhanced receipt API | Real-time status updates |
| WebSocket endpoint (optional) | Push notifications on queue completion |

---

## Migration Checklist

### Phase 1: Foundation (Week 1-2)
- [ ] Create OpenClaw repository
- [ ] Set up React + TypeScript + Vite
- [ ] Install dependencies (Tailwind, RHF, Zod, TanStack Query)
- [ ] Define API contract document
- [ ] Implement `POST /api/v1/queue` in Hermes
- [ ] Document field mappings and validation rules
- [ ] Set up service-to-service authentication

### Phase 2: Core Forms (Week 3-5)
- [ ] Build Contact Creation Wizard
- [ ] Build Account Onboarding Form (FEIN/DOT validation)
- [ ] Build Lead Intake Form
- [ ] Build Task Template Creator
- [ ] Implement real-time duplicate detection
- [ ] Wire all forms to Hermes queue
- [ ] Test end-to-end flow

### Phase 3: Advanced Workflows (Week 6-8)
- [ ] Policy Binding Flow (carrier selection, effective dates)
- [ ] Bulk Import (CSV upload, field mapping UI)
- [ ] Mass Update Interface (select multiple, apply changes)
- [ ] Role-specific Dashboards (Producer vs CS Rep views)
- [ ] Draft/Save/Resume functionality

### Phase 4: Hermes Refactoring (Week 9-10)
- [ ] Extract `data_entry.py` validation logic into shared library
- [ ] Create `rsg-validation` package (usable by both repos)
- [ ] Deprecate direct data entry CLI commands
- [ ] Update NL agent to redirect: "Use our form instead: [link]"
- [ ] Add analytics tracking (form usage vs chat commands)

### Phase 5: Cutover (Week 11-12)
- [ ] User training sessions
- [ ] Create documentation and video tutorials
- [ ] Gradual rollout: forms for new entries, chat for lookups
- [ ] Monitor adoption metrics
- [ ] Gather user feedback
- [ ] Sunset deprecated commands after 30-day grace period

---

## Success Metrics

Track these weekly:

| Metric | How to Measure | Target |
|--------|---------------|--------|
| Form completion time | Analytics on form submit timestamps | <20 seconds |
| Data entry error rate | % of submissions rejected by Hermes | <2% |
| Duplicate records created | Count from CRM dedupe reports | <5/month |
| Hermes response time | P95 latency on NL queries | <1.5s |
| User satisfaction | Weekly survey (1-10 scale) | >8.5/10 |
| Form adoption rate | % of new records via forms vs chat | >80% |

---

## Common Questions

### Q: Should OpenClaw be a separate repo or monorepo?
**A:** Separate repo recommended because:
- Different tech stacks (Python vs TypeScript)
- Independent deployment cycles
- Clearer ownership boundaries
- Easier to scale teams

### Q: Can we use Retool/Low-code instead?
**A:** You could, but custom build gives you:
- Full control over UX
- No per-user licensing costs
- Deeper integration with Hermes
- Better long-term maintainability

### Q: What happens to existing Slack data entry commands?
**A:** During migration:
- Keep them working but add deprecation warnings
- Include link to OpenClaw form in response
- After 30 days: remove from help docs
- After 60 days: return error with migration guide

### Q: Do we need a backend for OpenClaw?
**A:** Not necessarily. You can:
- Host as static site (Vercel, Netlify, S3)
- Call Hermes API directly from browser
- Use serverless functions for sensitive ops only

---

## Resources

- **Full Architecture:** [`OPENCLAW_ARCHITECTURE_SUMMARY.md`](OPENCLAW_ARCHITECTURE_SUMMARY.md)
- **Detailed ADR:** [`adr/002-openclaw-data-entry-separation.md`](adr/002-openclaw-data-entry-separation.md)
- **Current Implementation:** [`hermes/commands/data_entry.py`](../hermes/commands/data_entry.py)
- **NL Agent:** [`hermes/core/nl_agent.py`](../hermes/core/nl_agent.py)
- **Operating Constitution:** [`hermes-operating-constitution.md`](hermes-operating-constitution.md)

---

## Get Started Today

1. Read the full ADR (30 min)
2. Decide on repo structure (separate vs monorepo)
3. Create repository and scaffold project (1 hour)
4. Build "Hello World" form that submits to Hermes (2 hours)
5. Iterate from there!

**You've got this!** This separation will make both systems better. 🚀
