# Bidirectional Sync: the CRM ↔ Supabase ↔ NowCerts

## Architecture: Supabase as Golden Record

```
┌──────────┐         ┌──────────────┐         ┌──────────┐
│ NowCerts │ ──(A)──▶│   Supabase   │◀──(B)── │ the CRM  │
│  (AMS)   │ ◀──(D)──│ Golden Record│──(C)──▶ │  (CRM)   │
└──────────┘         └──────────────┘         └──────────┘
```

### Data Flows

| Flow | Direction | What | Trigger |
|------|-----------|------|---------|
| **A** | NowCerts → Supabase → the CRM | Policy details, insured facts | `sync nowcerts` (already built, PR #6) |
| **B** | the CRM → Supabase | New clients, account details, commissions | `sync crm-to-hub` (new) |
| **C** | Supabase → the CRM | Already handled by Flow A's outbound queue | — |
| **D** | Supabase → NowCerts | New clients + commissions from CRM | `sync hub-to-nowcerts` (new) |

## Flow B: the CRM → Supabase (CRM Mirror)

**Purpose:** Capture new clients and commission data from the CRM into Supabase golden record.

### What gets mirrored:
1. **New Accounts** (clients) — created in the CRM that don't have a `momentumClientId` (no NowCerts link yet)
2. **Account updates** — address, contact info, business details edited in CRM
3. **Commission data** — from Opportunity/Policy records with commission rates/amounts

### How it works:
1. Query the CRM for Accounts modified since last mirror run
2. For each Account:
   - Check if it exists in `sync_mappings` (has a NowCerts link)
   - If no NowCerts link: this is a CRM-originated client → stage for NowCerts push
   - If has NowCerts link: check for CRM-side field changes → update Supabase mirror
3. Query Opportunities/Policies for commission fields
4. Write all to `outbound_sync_queue` with `destination_system = 'nowcerts'`
5. Audit everything under a sync_run

### Supabase mirror tables (new):
```sql
-- Golden record: unified client view
CREATE TABLE IF NOT EXISTS crm_accounts (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    crm_id TEXT UNIQUE NOT NULL,
    nowcerts_id TEXT,                    -- NULL if CRM-originated
    name TEXT NOT NULL,
    account_type TEXT,
    fein TEXT,
    address_street TEXT,
    address_city TEXT,
    address_state TEXT,
    address_zip TEXT,
    email TEXT,
    phone TEXT,
    commission_rate NUMERIC(5,2),
    source_system TEXT NOT NULL DEFAULT 'crm',  -- 'crm' or 'nowcerts'
    last_synced_at TIMESTAMPTZ DEFAULT NOW(),
    raw_crm_payload JSONB,
    raw_nowcerts_payload JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Golden record: commission tracking
CREATE TABLE IF NOT EXISTS crm_commissions (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    account_id UUID REFERENCES crm_accounts(id),
    policy_number TEXT,
    carrier TEXT,
    line_of_business TEXT,
    premium NUMERIC(12,2),
    commission_rate NUMERIC(5,2),
    commission_amount NUMERIC(12,2),
    effective_date DATE,
    expiration_date DATE,
    source_system TEXT NOT NULL,
    crm_id TEXT,
    nowcerts_id TEXT,
    last_synced_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

## Flow D: Supabase → NowCerts (Outbound Push)

**Purpose:** Push new CRM-originated clients and commission data to NowCerts AMS.

### NowCerts Write API Endpoints:
- `POST /api/Insured/Insert` — Create/update insured (upsert on databaseId or name)
- `POST /api/InsuredAndPolicies/Insert` — Create insured with policies in one call
- `POST /api/Policy/Insert` — Create/update policy with commission fields
- `PATCH /api/Policy/PartialUpdate` — Update specific policy fields

### Reverse field mapping (CRM Account → NowCerts Insured):
| the CRM Field | NowCerts Field | Notes |
|---------------|----------------|-------|
| name | CommercialName | |
| primaryFirstName | FirstName | |
| primaryLastName | LastName | |
| fein | FEIN | |
| billingAddressStreet | AddressLine1 | |
| billingAddressCity | City | |
| billingAddressState | State | |
| billingAddressPostalCode | ZipCode | |
| emailAddress | EMail | |
| phoneNumber | Phone | |
| accountType | Type | Reverse enum map |
| businessEntity | TypeOfBusiness | |

### Commission fields (Policy → NowCerts Policy):
| the CRM Field | NowCerts Field | Notes |
|---------------|----------------|-------|
| premium | Premium | |
| commissionRate | AgencyCommissionPercent | |
| commissionAmount | AgencyCommissionValue | |
| carrier | CarrierName | |
| lineOfBusiness | LineOfBusinessName | |

## Conflict Resolution

When the same record is modified in both systems between syncs:

1. **NowCerts wins on:** Policy facts (effective/expiration dates, premium, carrier)
2. **the CRM wins on:** CRM-only fields (pipeline stage, tasks, notes, assignments)
3. **Conflict logged:** When both change the same field → `sync_conflicts` table
4. **Human review:** Conflicts queue visible via `sync conflicts` command

## Commands (CLI + Chat)

| Command | Description |
|---------|-------------|
| `sync crm-to-hub` | Mirror the CRM changes to Supabase golden record |
| `sync hub-to-nowcerts` | Push Supabase outbound queue to NowCerts |
| `sync bidirectional` | Run both directions: NowCerts→Hub + CRM→Hub + Hub→NowCerts |
| `sync bidirectional dry-run` | Preview all directions without writing |

## Implementation Order

1. **Supabase migration** — `crm_accounts` + `crm_commissions` tables
2. **Reverse field mapper** — CRM Account → NowCerts Insured payload
3. **NowCerts write methods** — `create_insured()`, `update_policy_commission()` on NowCertsClient
4. **the CRM → Supabase pipeline** — mirror Accounts + commission data
5. **Supabase → NowCerts pipeline** — dequeue and push via bearer token
6. **Bidirectional orchestrator** — runs all three directions in sequence
7. **Tests** — unit + integration for each direction
