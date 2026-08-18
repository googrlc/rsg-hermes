# RSG Policy Reconciliation Agent — Zoho Creator Specification

**Audience:** Zia AI (Zoho Creator) and a human builder following Zia’s output.
**App display name:** RSG Policy Reconciliation
**App link name:** `rsg_policy_reconciliation`
**Workspace:** Risk Solutions Group
**Spec version:** 1.0
**Date:** 2026-08-18

This document is an **actionable build spec**. Create forms, fields, relationships,
Deluge, schedules, views, and notifications in the order given. If a choice is
not specified, stop and ask — do not invent schema.

---

## 0. How Zia must build

### 0.1 Input contract

| Input | Required |
|-------|----------|
| This file | Yes — source of truth |
| `forms_*.csv` (five files) | Yes — field create lists |
| `picklists.csv` | Yes — exact option strings |
| `views.csv` | Yes — reports |
| `workflows.csv` | Yes — automation inventory |
| `deluge/*.dg` | Yes — paste in filename order |
| `tests/acceptance_cases.md` | Yes — Definition of Done |
| `tests/sample_records.json` | Optional seed only |

### 0.2 Build order (do not skip)

1. Create blank Creator app `rsg_policy_reconciliation`.
2. Create five forms (section 4) with **no extra auto-generated sections**.
3. Create fields from CSVs, form by form.
4. Apply picklists.
5. Create lookups and bidirectional display.
6. Install Deluge namespace functions (`thisapp.recon.*`).
7. Attach form workflows.
8. Create views and pages.
9. Create schedules (Phase 3+).
10. Connect Zoho CRM integration (Phase 4). NowCerts/Hermes is **read-only**
    via webhook or manual CSV in v1 — do not call NowCerts write APIs.

### 0.3 Naming rules

- Form link names and field Deluge names are **Pascal_Snake** as in the CSVs.
- Display labels may include spaces; Deluge names may not.
- Do not suffix `__c`. Creator does not use CRM custom-field suffixes.
- After create, export **Settings → Developer Space → Link names** and keep
  them identical to `Deluge_Name` in the CSVs.

---

## 1. Purpose, users, non-goals

### 1.1 Purpose

Compare each in-force or recently cancelled insurance policy across:

1. **NowCerts / Momentum** (AMS — policy truth)
2. **Zoho CRM** Policies module (CRM mirror + pipeline context)
3. **This Creator app** (`Policy_Master` working copy)
4. **Renewal Queue** (Project 85 style worklist)

For every policy, the agent produces:

- a **verdict** (exactly one of 12 types)
- a **confidence score** (0–100 integer)
- a **recommendation**
- zero or more **Audit_Exceptions** with SLA clocks

### 1.2 Users

| User | Role in Creator | What they do |
|------|-----------------|--------------|
| Lamar | Admin, recon-approver | Run recon, approve CRM push drafts, close High/Critical exceptions, see money |
| Gretchen | CSR | Work stale renewals, fill cancel reasons, add notes, resolve Low/Medium exceptions |
| Hermes / system | Integration user | Scheduled pull from CRM; never approves writes |

### 1.3 Non-goals (explicit)

- Not a CRM. Accounts and Deals stay in Zoho CRM.
- Not an AMS. Policy binds, endorsements, and insured inserts stay in NowCerts.
- Not a commission ledger. Money truth stays in Hermes/Supabase
  (`commission_ledger`). This app may **flag** premium/commission drift; it
  does not book revenue.
- Not a client-facing portal.
- No silent writes. No Deluge `zoho.crm.updateRecord` / NowCerts POST unless
  the approval fields in section 9.6 are set.

---

## 2. Systems of record and conflict rules

```
NowCerts (AMS)  ──policy facts──►  Creator Policy_Master (working copy)
Zoho CRM        ──accounts/deals──►  IDs stamped on Policy_Master
Hermes/Supabase ──queues/KPIs──►  optional Last_Hermes_* stamps (Phase 5)
```

| Scenario | Winner | Creator action |
|----------|--------|----------------|
| AMS vs CRM on premium, dates, carrier, status | **NowCerts** | Verdict `stale_crm` or `status_mismatch`; recommend CRM update draft |
| CRM vs Creator on notes, assignment, last contact | **Creator / CRM human fields** | Do not overwrite from AMS |
| Duplicate Policy_Number or GUID | Neither | Verdict `duplicate_policy`; human merge |
| Outbound queue row still `queued`/`processing` | Queue | Verdict `pending_sync`; do not re-push |
| Cancelled AMS + new overlapping policy same insured+LOB | Rewrite | Verdict `rewrite_detected`; link lineage |
| CRM has policy, AMS does not | Investigate | Verdict `missing_in_ams` (possible false tombstone) |
| AMS has policy, CRM does not | CRM lag | Verdict `missing_in_crm` |

Hermes operating constitution still applies: a write is a human decision.
Creator may **draft** a CRM update payload on `Policy_Audit.Recommended_Payload`.
It may **not** execute that payload without approval.

---

## 3. Application settings

| Setting | Value |
|---------|--------|
| Time zone | America/New_York |
| Date format | dd-MMM-yyyy |
| Currency | USD, 2 decimal |
| Duplicate check | Policy_Master: unique `NowCerts_Policy_GUID` only. `Policy_Number` is **not** unique at the database layer so duplicate rows can be stored and flagged. |
| Record owner | Creator system user for integration inserts; Gretchen/Lamar for manual |
| Environment | Build in **sandbox** first. Production only after acceptance cases pass. |

### 3.1 Integration connections (declare, do not hard-code secrets)

| Connection name | Type | Used for |
|-----------------|------|----------|
| `zoho_crm` | Zoho OAuth | Search/get Policies, Accounts, Deals |
| `hermes_read` (optional Phase 5) | HTTP | `GET /api/hermes/book-sync`, `GET /api/hermes/sync-health` |
| Email / Cliq | Notification | Exception SLA alerts to Lamar |

Do **not** store NowCerts passwords, Zoho refresh tokens, or Supabase service
keys in Creator fields. Use Connections.

---

## 4. Core data model — five forms

Create forms in this order. Primary keys are Creator `ID` (system). Business
keys are listed per form.

### 4.1 Form `Policy_Master` (display: Policy Master)

**Purpose:** One working row per AMS policy GUID. 50+ fields covering identity,
insured, status, financials, sync, lineage, and last recon result.

**Business keys**

- Unique: `NowCerts_Policy_GUID` (AMS databaseId)
- **Not** DB-unique: `Policy_Number` (duplicates must be insertable so the agent can flag `duplicate_policy`)
- Lookup safety: `NowCerts_Insured_GUID`

**Name formula** (auto, on add/edit):

```
Policy_Number + " — " + Carrier
```

If Carrier is empty, Name = Policy_Number.

#### Field specification

Use `forms_policy_master.csv` as the create list. Summary by section:

**A. Identity (system + keys)**

| Deluge_Name | Type | Req | Unique | Notes |
|-------------|------|-----|--------|-------|
| Name | Single Line | Y | N | Auto-composed |
| Policy_Number | Single Line 100 | Y | Y | AMS `number` / `policyNumber` |
| NowCerts_Policy_GUID | Single Line 36 | N | Y | AMS `databaseId`. Unique when present. Optional so CRM-only `missing_in_ams` rows can exist. |
| Hermes_Policy_ID | Single Line 36 | N | Y | Supabase `canonical_policies.id` if known |
| CRM_Policy_ID | Single Line 50 | N | Y | Zoho CRM Policies record ID |
| CRM_Account_ID | Single Line 50 | N | N | Zoho CRM Account ID |
| CRM_Deal_ID | Single Line 50 | N | N | Related Deal if any |
| NowCerts_Insured_GUID | Single Line 36 | N | N | AMS insured databaseId |

**B. Insured snapshot (denormalized; AMS/CRM may refresh)**

| Deluge_Name | Type | Notes |
|-------------|------|-------|
| Insured_Name | Single Line 255 | CommercialName or First+Last |
| Insured_Type | Picklist | Personal / Commercial |
| FEIN | Single Line 32 | Commercial dedupe only |
| Email | Email | |
| Phone | Phone | |
| Billing_Street | Single Line 255 | |
| Billing_City | Single Line 100 | |
| Billing_State | Single Line 50 | Mailing |
| Billing_Code | Single Line 20 | ZIP |
| Risk_State | Single Line 10 | Policy state, not mailing |

**C. Coverage / carrier**

| Deluge_Name | Type | Notes |
|-------------|------|-------|
| Carrier | Single Line 150 | AMS CarrierName. Pushable to CRM only. |
| Line_of_Business | Single Line 150 | Normalized LOB label |
| Business_Type | Single Line 100 | AMS typeOfBusiness |
| Billing_Type | Picklist | Direct Bill / Agency Bill / Direct Bill 100 / Agency Bill 100 |
| Policy_Term_Months | Number | 6 or 12 typical |
| Coverage_Amount | Currency | Optional |
| Deductible | Currency | Optional |

**D. Status**

| Deluge_Name | Type | Notes |
|-------------|------|-------|
| Policy_Status | Picklist | Normalized set (section 7.1) |
| AMS_Status_Raw | Single Line 80 | Unnormalized AMS string |
| CRM_Status | Picklist | Same options as Policy_Status; last seen in CRM |
| Active | Checkbox | Default false. Derived: true only if Policy_Status = Active |
| Cancellation_Date | Date | Distinct from Expiration_Date |
| Cancellation_Reason | Multi Line | Free text from AMS |
| Cancellation_Class | Picklist | Non Pay / Rewrite / Insured Request / Underwriter / Other |
| Reinstatement_Date | Date | |

**E. Dates**

| Deluge_Name | Type | Notes |
|-------------|------|-------|
| Effective_Date | Date | |
| Expiration_Date | Date | Original term end; do not overwrite with cancel date |
| Bind_Date | Date | |
| Last_AMS_Modified | Date-Time | Source modified stamp |
| Last_CRM_Modified | Date-Time | |
| Last_Synced | Date-Time | Last successful pull into Creator |
| Last_Audit_Date | Date-Time | Last Policy_Audit insert |

**F. Financials (AMS-authoritative; flag drift, do not invent)**

| Deluge_Name | Type | Notes |
|-------------|------|-------|
| Premium | Currency | AMS totalPremium |
| Current_Term_Premium | Currency | |
| Annualized_Premium | Currency | |
| Agency_Commission | Currency | AMS totalAgencyCommission |
| Commission_Rate | Percent / Decimal 5.4 | If > 1, treat as percent points and divide by 100 in Deluge |
| Agency_Fee | Currency | Shop fee ≠ commission |
| CRM_Premium | Currency | Last CRM value; used for delta |
| Premium_Delta | Currency | Formula: Premium − CRM_Premium |
| Premium_Delta_Pct | Decimal | Formula, null-safe |

**G. Lineage / rewrite**

| Deluge_Name | Type | Notes |
|-------------|------|-------|
| Renewed_Policy_GUID | Single Line 36 | Predecessor AMS GUID |
| Predecessor_Policy_Number | Single Line 100 | |
| Successor_Policy_Number | Single Line 100 | |
| Rewrite_Of | Lookup → Policy_Master | The cancelled/rewritten prior row |
| Is_Rewrite | Checkbox | Default false |
| Sync_Owner | Picklist | book_sync / rsg-import / manual |

**H. Recon result (stamped by agent; read-mostly)**

| Deluge_Name | Type | Notes |
|-------------|------|-------|
| Sync_Status | Picklist | Synced / Pending / Error / Skipped |
| Last_Verdict | Picklist | 12 verdicts |
| Last_Confidence | Number 0–100 | |
| Policy_Tier | Picklist | A / B / C (section 10.4) |
| Assigned_To | Users | Default Gretchen for CSR work |
| Notes | Multi Line | CSR notes; never overwritten by AMS pull |
| Approved_To_Push | Checkbox | Default false |
| Approved_By | Users | Required with Approved_At before any CRM write |
| Approved_At | Date-Time | |

**Formula fields** (Creator Formula, not Deluge):

```
Premium_Delta = if(Premium == null or CRM_Premium == null, null, Premium - CRM_Premium)

Premium_Delta_Pct = if(CRM_Premium == null or CRM_Premium == 0, 0,
                       ((Premium - CRM_Premium) / CRM_Premium) * 100)

Days_To_Expiration = Expiration_Date - zoho.currentdate
```

**Validation**

- On add: Policy_Number required. NowCerts_Policy_GUID unique when present (empty allowed for CRM-only rows).
- On add/edit: if Policy_Status in {Cancelled, Flat Cancel, Pending Cancel}
  and Cancellation_Class is empty → throw `"Cancellation class is required"`.
- If Is_Rewrite = true, Rewrite_Of is required.
- Commission_Rate: if user types 15 meaning 15%, store 0.15 in a hidden
  `Commission_Rate_Decimal` **or** document that the field is percent 0–100
  consistently. **Decision for this app:** store **percent points** (15 = 15%).
  Deluge that compares to AMS fractions must divide AMS by 100 when AMS > 1.

---

### 4.2 Form `Policy_Status_History` (display: Policy Status History)

**Purpose:** Immutable audit trail of every status change across AMS, CRM, and
Creator. Users cannot edit rows after insert except Notes by Admin.

| Deluge_Name | Type | Req | Notes |
|-------------|------|-----|-------|
| Name | Single Line | Y | Auto: Policy_Number + " " + Changed_At |
| Policy | Lookup Policy_Master | Y | |
| Policy_Number | Single Line 100 | Y | Snapshot |
| Source_System | Picklist | Y | AMS / CRM / Creator / Hermes |
| Old_Status | Picklist | N | Same normalized status list; empty on first insert |
| New_Status | Picklist | Y | |
| Old_Active | Checkbox | N | |
| New_Active | Checkbox | N | |
| Changed_At | Date-Time | Y | Default now |
| Changed_By | Users / Single Line | N | Integration user if system |
| Change_Reason | Multi Line | N | |
| Cancellation_Class | Picklist | N | Copied when New_Status is a cancel class |
| Raw_Payload | Multi Line | N | JSON snippet of source row; Admin only view |

**Hook:** Policy_Master on edit — if Policy_Status or Active changed, insert
one history row. See `deluge/05_status_history_hook.dg`.

**Permissions:** CSR create via hook only. No CSR delete. Admin delete
disabled in production.

---

### 4.3 Form `Renewal_Queue` (display: Renewal Queue)

**Purpose:** Working renewal row per policy that is in a renewal window.
Mirrors Hermes `project_85_renewals` conceptually; this is the Creator
worklist, not a second eligibility engine.

**Natural key:** Policy (lookup) unique while `Queue_Status` not in
{Completed, Dismissed}. Enforce in Deluge (Creator unique-on-lookup is
limited).

| Deluge_Name | Type | Req | Notes |
|-------------|------|-----|-------|
| Name | Single Line | Y | Auto: Policy_Number + " x " + Expiration_Date |
| Policy | Lookup Policy_Master | Y | |
| Policy_Number | Single Line 100 | Y | Snapshot |
| Insured_Name | Single Line 255 | Y | Snapshot; CSR may correct |
| Carrier | Single Line 150 | N | Snapshot |
| Line_of_Business | Single Line 150 | N | |
| Expiration_Date | Date | Y | |
| Effective_Date | Date | N | |
| Premium_Current | Currency | N | |
| Premium_Renewal | Currency | N | Quote; human-entered; never invent |
| Increase_Percent | Formula Decimal | N | `((Premium_Renewal - Premium_Current) / Premium_Current) * 100`; 0 if current is 0 |
| Risk_Status | Picklist | Y | SAFE / AT_RISK / CRITICAL / RENEWED / LAPSED |
| Queue_Status | Picklist | Y | Open / In Progress / Waiting Client / Waiting Carrier / Completed / Dismissed / Stale |
| Cadence_Bucket | Picklist | N | 90 / 60 / 30 / Past Due |
| Last_Contact_Date | Date | N | |
| Last_Touched_At | Date-Time | Y | Default now; update on any CSR edit |
| Stale_Flag | Checkbox | N | Set by agent |
| Stale_Reason | Multi Line | N | |
| Eligibility | Picklist | N | eligible / needs_verification / excluded |
| Assigned_To | Users | N | Default Gretchen |
| Strategy_Notes | Multi Line | N | |
| Dismissed | Checkbox | N | Soft-remove; never hard-delete |
| Hermes_Renewal_ID | Single Line 36 | N | `project_85_renewals.id` if known |

**Stale-detection (agent, not a guess):**

A queue row is stale (`stale_renewal_queue`) when **any** of:

1. `Last_Touched_At` older than **7 calendar days** AND Cadence_Bucket is
   `30` or `Past Due` AND Queue_Status in {Open, In Progress}.
2. `Last_Touched_At` older than **14 calendar days** AND Cadence_Bucket is
   `60` AND Queue_Status = Open.
3. Policy_Master.Policy_Status moved to Cancelled / Flat Cancel / Rewritten /
   Renewed / Non-Renewed but Queue_Status still Open.
4. AMS Expiration_Date on Policy_Master ≠ Renewal_Queue.Expiration_Date.

Risk_Status is **not** a 0–100 score. Use the Hermes classifier:

| Condition | Risk_Status |
|-----------|-------------|
| Premium_Renewal present AND increase > 15% | CRITICAL |
| Premium_Renewal present AND increase ≥ 5% | AT_RISK |
| Days to x-date ≤ 30 **or** past x-date | CRITICAL |
| Days to x-date 31–90 | AT_RISK |
| Days to x-date > 90 or no expiration | SAFE |

Evaluate **premium increase first, then timing**. Do not invent a second model.

Cadence_Bucket from Expiration_Date vs today:

| Days to expiration | Bucket |
|--------------------|--------|
| < 0 | Past Due |
| 0–30 | 30 |
| 31–60 | 60 |
| 61–90 | 90 |
| > 90 | (leave empty; row may still exist if eligibility says so) |

---

### 4.4 Form `Policy_Audit` (display: Policy Audit)

**Purpose:** One row per reconciliation **run per policy**. Append-only.

| Deluge_Name | Type | Req | Notes |
|-------------|------|-----|-------|
| Name | Single Line | Y | Auto: Policy_Number + " @ " + Run_At |
| Policy | Lookup Policy_Master | Y | |
| Policy_Number | Single Line 100 | Y | |
| Run_ID | Single Line 40 | Y | UUID or `yyyyMMddHHmmss` + batch |
| Run_At | Date-Time | Y | |
| Run_Type | Picklist | Y | scheduled_daily / scheduled_tier / manual / on_edit |
| Verdict | Picklist | Y | 12 types |
| Confidence | Number | Y | 0–100 |
| AMS_Status | Picklist | N | Snapshot |
| CRM_Status | Picklist | N | Snapshot |
| Creator_Status | Picklist | N | Snapshot |
| AMS_Premium | Currency | N | |
| CRM_Premium | Currency | N | |
| Premium_Delta | Currency | N | |
| Premium_Delta_Pct | Decimal | N | |
| Queue_Stale | Checkbox | N | |
| Duplicate_Count | Number | N | How many Policy_Master rows share number/GUID |
| Rewrite_Linked | Checkbox | N | |
| Pending_Queue_Jobs | Number | N | Count of open sync jobs if known |
| Findings | Multi Line | Y | Bullet list, facts only |
| Recommendation | Multi Line | Y | Next action |
| Recommended_Payload | Multi Line | N | JSON CRM draft; not executed |
| Exception_Created | Checkbox | N | |
| Related_Exception | Lookup Audit_Exceptions | N | |
| Score_Breakdown | Multi Line | N | Points deducted, for debug |

---

### 4.5 Form `Audit_Exceptions` (display: Audit Exceptions)

**Purpose:** Human work items created when a verdict is not `clean_match`
**or** confidence < 80 **or** financial delta exceeds threshold.

| Deluge_Name | Type | Req | Notes |
|-------------|------|-----|-------|
| Name | Single Line | Y | Auto: Severity + " — " + Policy_Number + " — " + Verdict |
| Policy | Lookup Policy_Master | Y | |
| Policy_Number | Single Line 100 | Y | |
| Audit | Lookup Policy_Audit | Y | |
| Verdict | Picklist | Y | |
| Severity | Picklist | Y | Low / Medium / High / Critical |
| Status | Picklist | Y | Open / In Progress / Waiting / Resolved / Won't Fix / Duplicate |
| SLA_Hours | Number | Y | From matrix |
| SLA_Due_At | Date-Time | Y | Created_Time + SLA_Hours (business hours optional v2; v1 calendar) |
| SLA_Breached | Checkbox | N | Schedule sets this |
| Owner | Users | Y | See assignment matrix |
| Opened_At | Date-Time | Y | |
| First_Touched_At | Date-Time | N | |
| Resolved_At | Date-Time | N | |
| Resolution_Notes | Multi Line | N | Required to resolve |
| Resolution_Class | Picklist | N | Corrected CRM / Corrected Creator / Linked rewrite / True cancel / Duplicate merged / Data lag / Won't fix |
| Escalated_To_Lamar | Checkbox | N | |
| Notify_Sent | Checkbox | N | Idempotency for alerts |

**Assignment**

| Severity | Default Owner |
|----------|---------------|
| Low, Medium | Gretchen |
| High, Critical | Lamar |

**Resolve rules**

- Status → Resolved requires Resolution_Notes **and** Resolution_Class.
- Gretchen cannot set Status = Resolved on High or Critical (Deluge hide/disable).
- Closing Duplicate requires the surviving Policy_Master ID in notes.

---

## 5. Relationships

```
Policy_Master 1 ──< Policy_Status_History
Policy_Master 1 ──< Renewal_Queue          (0..1 open row intended)
Policy_Master 1 ──< Policy_Audit
Policy_Master 1 ──< Audit_Exceptions
Policy_Audit  1 ──< Audit_Exceptions
Policy_Master.Rewrite_Of  ──> Policy_Master (self)
```

On each parent form, enable related lists:

| Parent | Related list |
|--------|----------------|
| Policy_Master | Status History, Renewal Queue, Audits, Exceptions |
| Policy_Audit | Exceptions |

---

## 6. AI agent logic — verdicts

The agent is **deterministic Deluge**, not a generative model. Zia may help
author the functions; it must not replace the matrix with an LLM call at
runtime.

### 6.1 Evaluation order (first match wins)

Run checks **top to bottom**. Return the first matching verdict. Always
compute confidence afterward (section 7) even for `clean_match`.

| Order | Verdict | When it fires |
|------:|---------|---------------|
| 1 | `duplicate_policy` | Count of Policy_Master with same Policy_Number **or** same NowCerts_Policy_GUID > 1 |
| 2 | `pending_sync` | `Sync_Status` = Pending **or** `Pending_Queue_Jobs` > 0 **or** CRM_Policy_ID empty **and** Last_Synced within 24h wait window after AMS insert (optional flag) |
| 3 | `rewrite_detected` | Is_Rewrite = true **or** (Cancellation_Class = Rewrite) **or** rewrite heuristic (section 8.2) matches a successor |
| 4 | `status_mismatch` | Normalized AMS_Status_Raw ≠ CRM_Status **or** CRM_Status ≠ Policy_Status (Creator), after normalize |
| 5 | `financial_discrepancy` | Absolute premium delta ≥ **$25** **or** absolute percent delta ≥ **1%** (hybrid; section 8.3) |
| 6 | `missing_in_crm` | NowCerts_Policy_GUID present, CRM_Policy_ID empty, Last_Synced older than 24h |
| 7 | `missing_in_ams` | CRM_Policy_ID present, NowCerts_Policy_GUID empty **or** AMS pull marked not found |
| 8 | `stale_renewal_queue` | Open Renewal_Queue row fails stale-detection (section 4.3) |
| 9 | `stale_crm` | Last_CRM_Modified < Last_AMS_Modified minus 1 hour (v1 clock-drift rule; status/premium mismatches already returned earlier) |
| 10 | `cancel_reason_gap` | Policy_Status in cancel set **and** Cancellation_Class empty |
| 11 | `lineage_orphan` | Renewed_Policy_GUID or Rewrite_Of or Successor_Policy_Number set but target Policy_Master not found |
| 12 | `clean_match` | None of the above |

Cancel set = `Cancelled`, `Flat Cancel`, `Pending Cancel`.

**Do not add more verdict types** without a spec change. Do not rename them.

### 6.2 Recommendation text (use these strings)

| Verdict | Recommendation |
|---------|----------------|
| `duplicate_policy` | Merge duplicate Policy_Master rows; keep the row with NowCerts_Policy_GUID + latest Last_AMS_Modified. Do not delete until Lamar confirms. |
| `pending_sync` | Do not edit AMS-owned fields. Wait for sync or inspect outbound queue. |
| `rewrite_detected` | Link Rewrite_Of / Successor. Move old Renewal_Queue to Completed or Dismissed. Open replacement coverage task if insured still needs a policy. |
| `status_mismatch` | Trust AMS status. Draft CRM Policy_Status update. Do not execute without approval. |
| `financial_discrepancy` | Trust AMS premium. Draft CRM premium update. If delta > $500 or Policy_Tier A, assign Lamar. |
| `missing_in_crm` | Draft CRM Policy create from Policy_Master. Link Account by NowCerts_Insured_GUID. Never create a new Account from this agent. |
| `missing_in_ams` | Do not tombstone. Flag for human: confirm live NowCerts. Historical false tombstones existed in the book mirror. |
| `stale_renewal_queue` | Assign Gretchen; Cadence_Bucket 30/Past Due is same-day. Update Last_Touched_At after real contact, not after opening the record. |
| `stale_crm` | Draft CRM field refresh from AMS snapshot on Policy_Master. |
| `cancel_reason_gap` | Gretchen classifies Cancellation_Class using section 8.1. |
| `lineage_orphan` | Search Policy_Number / GUID; link or clear the broken pointer. Do not invent a GUID. |
| `clean_match` | No action. Stamp Last_Audit_Date. |

### 6.3 Exception severity from verdict

| Verdict | Severity |
|---------|----------|
| `duplicate_policy` | High |
| `pending_sync` | Low (High if pending > 72h) |
| `rewrite_detected` | Medium |
| `status_mismatch` | Medium (High if one side Active and the other Cancelled) |
| `financial_discrepancy` | Medium; High if abs(delta) ≥ $500 or Policy_Tier = A |
| `missing_in_crm` | Medium; High if Policy_Tier = A or premium ≥ $5000 |
| `missing_in_ams` | High |
| `stale_renewal_queue` | Medium; Critical if Cadence_Bucket = Past Due or Risk_Status = CRITICAL |
| `stale_crm` | Low; Medium if status or premium also drifting |
| `cancel_reason_gap` | Medium |
| `lineage_orphan` | Medium |
| `clean_match` | (no exception) |

If confidence < 50, raise severity one step (Low→Medium→High→Critical), cap at Critical.

---

## 7. Confidence scoring (0–100)

Start at **100**. Subtract. Floor at 0. Ceiling at 100. Integer only.

| ID | Condition | Points |
|----|-----------|--------:|
| S1 | Policy_Number blank | −40 |
| S2 | NowCerts_Policy_GUID blank | −25 |
| S3 | CRM_Policy_ID blank | −10 |
| S4 | Insured_Name blank | −10 |
| S5 | Carrier blank | −8 |
| S6 | Line_of_Business blank | −5 |
| S7 | Effective_Date or Expiration_Date blank | −10 each |
| S8 | Policy_Status blank or unnormalized (not in picklist) | −15 |
| S9 | AMS vs CRM status differ after normalize | −12 |
| S10 | AMS vs Creator status differ | −12 |
| S11 | Premium blank on Active policy | −15 |
| S12 | Abs premium $ delta ≥ 25 | −10 |
| S13 | Abs premium % delta ≥ 1 | −8 |
| S14 | Abs premium $ delta ≥ 500 | −15 extra |
| S15 | Duplicate_Count > 1 | −30 |
| S16 | Cancellation without class | −12 |
| S17 | Rewrite detected but Rewrite_Of empty | −15 |
| S18 | Lineage pointer set but target missing | −12 |
| S19 | Last_Synced older than 48h | −8 |
| S20 | Last_Synced older than 7 days | −12 extra |
| S21 | Renewal_Queue stale | −10 |
| S22 | Pending_Queue_Jobs > 0 | −8 |
| S23 | Active=true but status in EXCLUDE_STATUSES | −20 |
| S24 | Active=false but status = Active | −15 |
| S25 | Billing_Type blank on Active Agency Bill candidate (Billing_Type contains "Agency") | −5 |

EXCLUDE_STATUSES = Expired, Cancelled, Flat Cancel, Non-Renewed, Lapsed.

**Bands**

| Score | Band | Exception? |
|------:|------|------------|
| 90–100 | High | Only if verdict ≠ clean_match |
| 80–89 | Medium-high | Exception if verdict ≠ clean_match |
| 50–79 | Medium | Always exception |
| 0–49 | Low | Always exception; bump severity |

Write `Score_Breakdown` as lines `S12 -10 premium $ delta`.

---

## 8. Business rules

### 8.1 Cancellations

Classify every cancel. Gretchen owns classification; Lamar owns commercial
and large-account cancels.

| Cancellation_Class | Meaning | Follow-on |
|--------------------|---------|-----------|
| Non Pay | Carrier cancel for nonpayment | Check reinstatement window; do **not** treat as rewrite; escalate commercial / large to Lamar |
| Rewrite | Replaced by a new policy (same insured, usually same LOB, new number/carrier) | Must set Is_Rewrite and Rewrite_Of / Successor |
| Insured Request | Client asked to cancel | Confirm effective date; replacement coverage task if still needs insurance |
| Underwriter | Carrier UW cancel / material risk | Review contestability; Lamar on commercial |
| Other | Does not fit; Notes required | |

**Rules**

- Mid-term cancel: keep Expiration_Date as original term end; store cutoff in
  Cancellation_Date. Finance uses Cancellation_Date for chargeback estimates.
- Flat Cancel: Cancellation_Class still required (often Insured Request or
  rewrite-before-inception).
- Pending Cancel: do **not** auto-exclude from renewal queue. Eligibility =
  `needs_verification`.
- Non Pay is **not** a rewrite even if a new policy appears later — only
  classify Rewrite when the replacement policy is identified.

### 8.2 Rewrite detection heuristic

A rewrite is detected when **all** of:

1. Policy A status in {Cancelled, Flat Cancel, Pending Cancel, Non-Renewed,
   Rewritten} **or** Cancellation_Class = Rewrite.
2. Policy B exists with same `NowCerts_Insured_GUID` (required) and same
   normalized `Line_of_Business` (required).
3. Policy B `Effective_Date` within **60 days** of Policy A `Cancellation_Date`
   (or Expiration_Date if cancel date empty).
4. Policy B `NowCerts_Policy_GUID` ≠ Policy A.
5. Policy B status in {Active, Up for Renewal, Renewing, Bound mapped to Active}.

On detect:

- Set A.Is_Rewrite = true, A.Successor_Policy_Number = B.Policy_Number.
- Set B.Rewrite_Of = A.
- Set A.Policy_Status = Rewritten if AMS still says Cancelled **only when**
  Cancellation_Class = Rewrite. If AMS says Cancelled and class is Non Pay,
  **do not** relabel to Rewritten.
- Close or dismiss A’s open Renewal_Queue row with reason `rewrite_detected`.

Never invent Policy B. If heuristic finds **multiple** B candidates, verdict
`rewrite_detected` with confidence capped at 60 and exception High — human
picks the link.

### 8.3 Financial discrepancy

Match Hermes commission recon hybrid default:

- Flag if `abs(Premium - CRM_Premium) ≥ 25` **OR**
  `abs((Premium - CRM_Premium) / CRM_Premium) * 100 ≥ 1`.
- If CRM_Premium is 0 or null, use dollar rule only; if AMS premium also 0,
  do not flag.
- Agency_Fee is **not** commission. Do not add it into Premium_Delta.
- Agency Bill + Agency_Fee null/0 on Active policy: mention in Findings as
  info (`DQ-BILL2` analogue); does not by itself create `financial_discrepancy`.

### 8.4 Renewal queue health

- Do not hard-delete queue rows. Dismissed = true + Queue_Status = Dismissed.
- Last_Touched_At updates only from: status change, Last_Contact_Date change,
  Strategy_Notes change, or explicit “Touch” button — **not** from opening
  the form.
- If Policy leaves the book (Renewed / Rewritten / Cancelled / Non-Renewed),
  queue must not stay Open. Agent sets Stale_Flag and exception.

### 8.5 Status normalization

Map AMS/CRM raw strings before compare. Unknown raw → blank Policy_Status and
Sync_Status = Error.

| Raw (case-insensitive) | Policy_Status |
|------------------------|---------------|
| active, in force, inforce, bound | Active |
| up for renewal, renewal pending | Up for Renewal |
| renewing, in renewal, renewal | Renewing |
| renewed | Renewed |
| rewritten | Rewritten |
| expired | Expired |
| cancelled, canceled | Cancelled |
| flat cancel, flat-cancel, flat cancelled | Flat Cancel |
| pending cancel, pending cancellation, cxl pending | Pending Cancel |
| non-renewed, non renewed, nonrenewed | Non-Renewed |
| lapsed | Lapsed |

CURRENT_STATUSES: Active only.
STAGED: Up for Renewal, Renewing.
SUPERSEDED: Renewed, Rewritten.
EXCLUDE: Expired, Cancelled, Flat Cancel, Non-Renewed, Lapsed.

Active checkbox: true **only** if normalized status is Active. Creator may
not set Active=true on Expired.

### 8.6 Billing normalization

| Raw | Billing_Type |
|-----|--------------|
| direct bill, direct, db, direct bill autopay | Direct Bill |
| agency bill, agency, ab, agency bill autopay | Agency Bill |
| direct bill 100, db 100, db100 | Direct Bill 100 |
| agency bill 100, ab 100, ab100 | Agency Bill 100 |

Underscores → spaces before match (`Direct_Bill_100` → Direct Bill 100).

### 8.7 Account matching (CRM create drafts)

When recommending a CRM Policy create:

- Match Account by `NowCerts_Insured_GUID` External ID, else exact
  Insured_Name.
- **No match → skip create.** Log `missing_in_crm` Findings:
  `"No CRM Account for insured {name}; human must link/create Account first."`
- Never auto-create Accounts. That path created duplicate-account garbage
  historically.

---

## 9. Operational workflow

### 9.1 Daily reconciliation (Phase 3 schedule)

Time: **6:45am America/New_York**, after expected AMS→CRM book sync (~6:30am).

Steps (one `Run_ID` for the batch):

1. Optional: pull CRM Policies modified since last cursor (Phase 4).
2. For each Policy_Master where Policy_Tier in {A, B} **or** Active = true
   **or** Expiration_Date within 120 days **or** last verdict not clean_match:
   - Refresh CRM snapshot fields if connected.
   - `thisapp.recon.normalizeStatus`
   - `thisapp.recon.detectRewrite`
   - `thisapp.recon.score`
   - `thisapp.recon.verdict`
   - Insert Policy_Audit.
   - Upsert Audit_Exceptions (section 9.3).
   - Stamp Policy_Master Last_Verdict, Last_Confidence, Last_Audit_Date.
3. For Renewal_Queue Open rows: recompute Cadence_Bucket, Risk_Status,
   Stale_Flag.
4. SLA sweep: set SLA_Breached, notify (section 11).
5. Write a Page / email summary for Lamar: counts by verdict, open Critical
   exceptions, stale 30-day renewals.

### 9.2 Manual run

Button **Run Recon** on Policy_Master (Admin + CSR). CSR may run one policy;
Admin may run “All Tier A” from a page button.

### 9.3 Exception upsert

Same policy + same open Verdict → **update** the existing Open/In Progress
exception (refresh Audit lookup, Findings, SLA if severity increased).
Do not open a second Open exception for the same pair.

If new verdict is `clean_match`, auto-resolve open exceptions with
Resolution_Class = `Data lag` only when the **prior** verdict was
`pending_sync` or `stale_crm`. All other auto-closes are forbidden — human
closes.

### 9.4 Audit frequency by tier

| Policy_Tier | Rule | Cadence |
|-------------|------|---------|
| A | Annualized_Premium ≥ $5000 **or** Commercial with premium ≥ $2500 **or** Lamar-tagged | Daily in the 6:45 job |
| B | Active personal/commercial not A, or in 90-day renewal window | Daily |
| C | Inactive / EXCLUDE_STATUSES, expired > 180 days, no open exception | Weekly Sunday 7:00am |

On add/edit of premium or Insured_Type, Deluge sets Policy_Tier.

### 9.5 Sync health monitoring (Phase 5)

If Hermes HTTP connection exists, once daily after recon:

- `GET /api/hermes/sync-health` — outbound queue freshness.
- `GET /api/hermes/book-sync` — AMS vs mirror drift.

Store last payload timestamp on a single `App_Settings` hidden form **or**
as Creator Page variables. If either endpoint fails, notify Lamar; do not
invent health numbers.

If no Hermes connection, skip. Do not mock values.

### 9.6 Approval gate for CRM writes (Phase 4+)

A CRM write function may run only when:

```
Policy_Master.Approved_To_Push == true
AND Approved_By is not null
AND Approved_At is not null
AND Approved_At > now - 24 hours
AND Recommended_Payload on the latest Policy_Audit is non-empty
```

After write: clear Approved_To_Push, set Sync_Status = Pending, insert
Policy_Status_History Source_System = Creator. Re-read CRM; if mismatch,
verdict `status_mismatch` / Error — do not retry in a loop.

**NowCerts writes are out of scope for this app.** Route humans to Hermes
`outbound_sync_queue` / Command Center.

---

## 10. Views, pages, notifications

Create views from `views.csv`. Minimum:

| View | Form | Filter | Owner |
|------|------|--------|-------|
| All Policies | Policy_Master | all | Admin |
| Active Book | Policy_Master | Active = true | Both |
| Needs Recon | Policy_Master | Last_Verdict != clean_match or Last_Audit_Date empty | Both |
| CSR Queue | Audit_Exceptions | Status in Open, In Progress AND Severity in Low, Medium AND Owner = Gretchen | Gretchen |
| Lamar Exceptions | Audit_Exceptions | Severity in High, Critical AND Status != Resolved | Lamar |
| Stale Renewals | Renewal_Queue | Stale_Flag = true OR Queue_Status = Stale | Gretchen |
| 30-Day Radar | Renewal_Queue | Cadence_Bucket in 30, Past Due AND Dismissed = false | Both |
| Today's Audits | Policy_Audit | Run_At today | Admin |
| SLA Breaches | Audit_Exceptions | SLA_Breached = true AND Status not Resolved | Lamar |

Page **Recon Desk** (HTML/ZML): KPI widgets — open exceptions by severity,
stale renewals count, yesterday’s clean_match %, Tier A mismatches.

### 11. Notification rules

| Event | Channel | Who | Dedupe |
|-------|---------|-----|--------|
| New Critical exception | Email + Creator notification | Lamar | One per policy per calendar day |
| SLA_Breached flipped true | Email | Owner + Lamar if High/Critical | One per exception |
| Stale 30-day renewal count > 0 after daily job | Email digest | Gretchen + Lamar | One digest per day |
| missing_in_ams | Email | Lamar | One per policy per 7 days |

Do not spam. Store Notify_Sent / last notify timestamp.

SLA hours (calendar, v1):

| Severity | SLA_Hours |
|----------|-----------|
| Critical | 4 |
| High | 24 |
| Medium | 72 |
| Low | 120 |

---

## 12. Zoho Creator implementation notes

### 12.1 Deluge namespaces

Install as **custom functions** in app:

| Function | File |
|----------|------|
| `thisapp.recon.normalizeStatus(raw)` | `deluge/01_normalize_status.dg` |
| `thisapp.recon.normalizeBilling(raw)` | (same file) |
| `thisapp.recon.score(policyID)` | `deluge/02_confidence_score.dg` |
| `thisapp.recon.verdict(policyID)` | `deluge/03_verdict_engine.dg` |
| `thisapp.recon.detectRewrite(policyID)` | `deluge/04_rewrite_detection.dg` |
| `thisapp.recon.writeStatusHistory(policyID, oldS, newS, source)` | `deluge/05_status_history_hook.dg` |
| `thisapp.recon.upsertException(auditID)` | `deluge/06_exception_sla.dg` |
| `thisapp.recon.runOne(policyID, runType, runID)` | `deluge/07_scheduled_daily_recon.dg` |
| `thisapp.recon.runDaily()` | `deluge/07_scheduled_daily_recon.dg` |
| `thisapp.recon.pullCRM(policyID)` | `deluge/08_crm_pull.dg` |

### 12.2 Form workflows

See `workflows.csv`. Critical:

- Policy_Master **Created / Edited** → status history if status/active changed;
  recompute Policy_Tier; if Cancellation_Class empty on cancel, show alert.
- Policy_Master **Created / Edited** → optional `runOne` if user clicked
  Save & Recon (stateless checkbox `Run_Recon_On_Save` default false).
- Audit_Exceptions **Edited** → if Status became In Progress and
  First_Touched_At empty, set it; if Resolved, require notes + class.
- Renewal_Queue **Edited** → Last_Touched_At only on the fields in 8.4.

### 12.3 Schedules

| Name | Cron (ET) | Function | Phase |
|------|-----------|----------|-------|
| Daily Recon | 45 6 * * * | runDaily | 3 |
| Weekly Tier C | 0 7 * * 0 | runDaily filtered C | 3 |
| SLA Sweep | 0 8-18 * * 1-5 | exception SLA flags | 3 |

### 12.4 CRM integration (Phase 4)

Zoho CRM custom module **Policies** field model lives in
`docs/zoho/fields_policies.csv`. Map:

| Creator Policy_Master | CRM Policies |
|-----------------------|--------------|
| NowCerts_Policy_GUID | NowCerts_Policy_GUID |
| Policy_Number | Policy_Number |
| Carrier | Carrier |
| Line_of_Business | Line_of_Business |
| Policy_Status | Policy_Status |
| Premium | Premium |
| Effective_Date / Expiration_Date | same |
| Cancellation_Date | Cancellation_Date |
| Billing_Type | Billing_Type |
| Agency_Fee | Agency_Fee |
| Agency_Commission | Agency_Commission |
| CRM_Account_ID | Account_Name (ID) |

Search: `zoho.crm.searchRecords("Policies", "(NowCerts_Policy_GUID:equals:" + guid + ")")`.
Fallback Policy_Number. If two CRM rows, `duplicate_policy` Findings include CRM IDs.

Creator must **not** create CRM Accounts. Policy create draft only.

### 12.5 Zoho Analytics (Phase 6)

Share all five forms to Analytics workspace **RSG Recon**.

Reports:

- Exception aging by severity
- Verdict mix 14-day
- Renewal stale rate
- Premium delta histogram (no PII required)
- CSR vs Lamar close time

---

## 13. Implementation phases

Stop after each phase. Do not start the next until the phase acceptance
rows in `tests/acceptance_cases.md` pass.

### Phase 1 — Schema (Zia starts here)

Forms, fields, picklists, lookups, related lists, permissions (Lamar Admin,
Gretchen CSR), status-history hook, Policy_Tier on save. **No schedules, no
CRM connection.**

### Phase 2 — Agent core

Install score + verdict + rewrite detection. Button Run Recon on one policy
creates Policy_Audit (+ exception if needed). Seed from
`tests/sample_records.json`.

### Phase 3 — Operations

Daily/weekly schedules, SLA sweep, views, Recon Desk page, notifications
(email to app users; no external Slack required).

### Phase 4 — Zoho CRM read

Connection `zoho_crm`, pullCRM, missing_in_crm / stale_crm live. CRM write
still disabled unless approval gate tested in sandbox with one record.

### Phase 5 — Hermes read-only health

Optional HTTP to book-sync / sync-health. Display on Recon Desk.

### Phase 6 — Analytics + harden

Analytics reports, permission review, disable history delete, production
publish checklist.

---

## 14. Permissions

| Form | Gretchen | Lamar |
|------|----------|-------|
| Policy_Master | Read all; edit Notes, Cancellation_Class, Assigned_To, Last_Contact analogue fields | Full |
| Policy_Status_History | Read | Read (no delete) |
| Renewal_Queue | Read/edit work fields; no delete | Full except hard-delete |
| Policy_Audit | Read | Read |
| Audit_Exceptions | Edit own Low/Medium; cannot resolve High/Critical | Full |

Integration user: create/update Policy_Master from CRM pull; cannot set
Approved_To_Push.

---

## 15. Guardrails (Zia and runtime)

1. Never invent policy numbers, premiums, GUIDs, or carrier quotes.
2. Never write NowCerts from Creator.
3. Never create Zoho CRM Accounts from this app.
4. Never auto-commit money or commission.
5. Never hard-delete Policy_Master or Renewal_Queue in production.
6. Unknown AMS status → Error, not a new picklist value.
7. Facts vs recommendations stay in separate fields (Findings vs Recommendation).
8. If a required CSV field cannot be created because of a Creator limitation,
   stop and report the field name — do not silently skip.

---

## 16. Definition of Done

Phase 2 is the first “agent exists” milestone:

- [ ] Five forms with all CSV fields
- [ ] Picklists exact
- [ ] Changing Policy_Status writes Policy_Status_History
- [ ] Run Recon on each `tests/sample_records.json` policy yields the
      **expected verdict** in that file
- [ ] Confidence matches the score table ±0 (integer)
- [ ] No CRM/AMS write calls in Deluge except commented stubs behind the
      approval gate

---

## 17. Rollback

Creator versions: publish a sandbox snapshot before each phase.

To roll back a phase: restore the previous Creator version; scheduled
functions off; CRM connection unused.

Data: Policy_Audit and Audit_Exceptions are append-only; rollback does not
require deleting them.

---

## 18. Supporting files (must ship with this spec)

All paths relative to `docs/zoho-creator/`:

- `README.md`
- `ZIA_PASTE_PROMPT.md`
- `forms_policy_master.csv`
- `forms_policy_status_history.csv`
- `forms_renewal_queue.csv`
- `forms_policy_audit.csv`
- `forms_audit_exceptions.csv`
- `picklists.csv`
- `views.csv`
- `workflows.csv`
- `deluge/01_normalize_status.dg` through `08_crm_pull.dg`
- `tests/acceptance_cases.md`
- `tests/sample_records.json`
