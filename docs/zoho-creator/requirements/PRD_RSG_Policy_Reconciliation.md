# Product Requirements Document (PRD)

**Product:** RSG Policy Reconciliation  
**Platform:** Zoho Creator  
**Customer:** Risk Solutions Group (RSG)  
**Version:** 1.0  
**Date:** 2026-08-18  
**Owner:** Lamar (Operations / Producer)  
**Builder:** Zia AI (Zoho Creator)  
**Status:** Approved for Phase 1 build

---

## 1. Problem

RSG’s policy truth lives in **NowCerts (AMS)**. Accounts and deals live in **Zoho CRM**. Renewals and ops state live in **Hermes / Supabase**. Those copies drift.

Today there is no single desk that:

- compares AMS vs CRM vs the renewal worklist on every policy
- explains *why* they disagree (verdict)
- scores how trustworthy the comparison is (0–100)
- opens an exception with an SLA instead of a Slack guess
- classifies cancellations (Non Pay vs Rewrite vs Insured Request vs Underwriter)
- links rewrites instead of treating them as lost business

The agency cannot scale to the $1M premium north star if Gretchen and Lamar reconcile the book by hand.

## 2. Product statement

Build a Zoho Creator application named **RSG Policy Reconciliation** (`rsg_policy_reconciliation`) that is the **reconciliation workspace** for the in-force and recently cancelled book.

It is **not** a CRM. It is **not** an AMS. It does **not** book commissions.

For every policy it must emit:

1. Exactly one of **12 verdicts**
2. An integer **confidence score 0–100**
3. A written **recommendation**
4. An **Audit_Exception** when the verdict is not `clean_match` (or confidence < 80, or premium delta exceeds the hybrid threshold)

## 3. Goals

| ID | Goal | Measure |
|----|------|---------|
| G1 | One working row per AMS policy GUID | Unique `NowCerts_Policy_GUID` on `Policy_Master` |
| G2 | Deterministic recon, not an LLM at runtime | Deluge `thisapp.recon.verdict` / `score` only |
| G3 | Cancellation class always present on cancel | Save blocked if class empty (except import seeds) |
| G4 | Rewrites linked, not double-counted | `Rewrite_Of` / `Successor_Policy_Number` |
| G5 | Stale renewals visible same day in 30-day bucket | `stale_renewal_queue` + CSR view |
| G6 | No silent writes | CRM update only with approval gate; **zero** NowCerts writes |

## 4. Non-goals

- Creating Zoho CRM Accounts
- Writing NowCerts / Momentum (binds, endorsements, insured inserts)
- Commission ledger / money booking (Hermes `commission_ledger` stays SoR)
- Client-facing portal
- Inventing premiums, policy numbers, GUIDs, or carrier quotes
- A second 0–100 *renewal risk* score (Risk_Status stays SAFE / AT_RISK / CRITICAL / RENEWED / LAPSED)

## 5. Users

| Persona | Creator role | Jobs to be done |
|---------|--------------|-----------------|
| Lamar | Admin / recon-approver | Run recon, approve CRM drafts, close High/Critical exceptions, see Tier A money drift |
| Gretchen | CSR | Classify cancels, work stale renewals, resolve Low/Medium exceptions, add notes |
| Hermes | Integration user | Scheduled CRM pull; cannot set `Approved_To_Push` |

## 6. User stories

1. **As Lamar**, I run daily recon at 6:45am ET and see counts by verdict, not a dump of every policy.
2. **As Gretchen**, I open CSR Queue and only see Low/Medium exceptions assigned to me.
3. **As Gretchen**, when a policy is Cancelled I must pick Non Pay, Rewrite, Insured Request, Underwriter, or Other before save.
4. **As Lamar**, I never want Creator to push CRM until I set Approved_To_Push, Approved_By, and Approved_At (within 24 hours).
5. **As Gretchen**, opening a renewal record does **not** count as a touch; only real field edits stamp `Last_Touched_At`.
6. **As Lamar**, if AMS has a policy and CRM does not, the app drafts a Policy create and **does not** create an Account.
7. **As either**, if two Policy_Master rows share a Policy_Number, verdict is `duplicate_policy` — uniqueness is **not** enforced on Policy_Number so the duplicate can be stored and flagged.

## 7. In-scope product surface

### 7.1 Five forms (exact API names, this order)

1. `Policy_Master` — working copy (67 fields; see field CSV)
2. `Policy_Status_History` — immutable status audit
3. `Renewal_Queue` — Project 85 style worklist
4. `Policy_Audit` — one row per run per policy
5. `Policy_Audit` lookup child: `Audit_Exceptions` — SLA work items

Do not add a sixth form in Phase 1.

### 7.2 Twelve verdicts (do not rename)

`duplicate_policy` → `pending_sync` → `rewrite_detected` → `status_mismatch` → `financial_discrepancy` → `missing_in_crm` → `missing_in_ams` → `stale_renewal_queue` → `stale_crm` → `cancel_reason_gap` → `lineage_orphan` → `clean_match`

First match wins.

### 7.3 Confidence

Start at 100. Subtract per scoring table S1–S25. Integer. Floor 0, ceiling 100. Write `Score_Breakdown`.

### 7.4 Financial rule

Flag `financial_discrepancy` when `abs(Premium − CRM_Premium) ≥ $25` **OR** `abs(percent) ≥ 1%`. Agency_Fee is not premium.

### 7.5 Renewal risk (separate from confidence)

Premium increase first, then timing: >15% CRITICAL, ≥5% AT_RISK; else ≤30 days CRITICAL, 31–90 AT_RISK, else SAFE.

## 8. Phased delivery

| Phase | Product increment | Zia stop rule |
|-------|-------------------|---------------|
| 1 | Schema, picklists, lookups, permissions, status-history hook, Policy_Tier | **STOP. Wait.** |
| 2 | Verdict + score + rewrite + Run Recon button + sample seeds | Stop for acceptance |
| 3 | Daily/weekly schedules, SLA sweep, views, Recon Desk, email | |
| 4 | Zoho CRM read; gated write stub | |
| 5 | Optional Hermes health HTTP | |
| 6 | Zoho Analytics + harden | |

## 9. Success metrics (product)

- Phase 2: all 14 seed policies in `sample_records.json` match expected verdict **and** confidence.
- Phase 3: no second Open exception for the same policy + verdict.
- Phase 4: Deluge contains no `zoho.crm.createRecord("Accounts"`.
- Production: Gretchen cannot resolve High/Critical; cannot set Approved_To_Push.

## 10. Constraints Zia must obey

1. NowCerts wins policy facts (premium, dates, carrier, status).
2. Unknown AMS status → `Sync_Status=Error`, do not invent a picklist value.
3. Mid-term cancel: keep `Expiration_Date`; store cutoff in `Cancellation_Date`.
4. Pending Cancel is **not** auto-excluded from the renewal queue (`needs_verification`).
5. Non Pay is not a rewrite unless a replacement policy is identified.
6. Time zone America/New_York. Currency USD.

## 11. Dependencies

Field-level create lists: `forms_*.csv`, `picklists.csv`, `views.csv`, `workflows.csv`, `deluge/*.dg`.  
Those files are part of this PRD’s data appendix (also inside `ZIA_UPLOAD.xlsx`).

## 12. Open questions (do not invent answers)

- Live Zoho CRM Policies module API name after org create (may be CustomModuleX). Confirm before Phase 4.
- Gretchen and Lamar Zoho user emails for Owner assignment (placeholders `gretchen` / `lamar` until replaced).
