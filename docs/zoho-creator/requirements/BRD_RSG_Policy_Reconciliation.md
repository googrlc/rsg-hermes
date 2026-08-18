# Business Requirements Document (BRD)

**Initiative:** Insurance Policy Reconciliation Agent  
**Organization:** Risk Solutions Group (RSG)  
**Application:** Zoho Creator — RSG Policy Reconciliation  
**Version:** 1.0  
**Date:** 2026-08-18  
**Sponsor:** Lamar, Operations  
**Prepared for:** Zia AI (Zoho Creator) build

---

## 1. Business purpose

Protect retention (Project 85, target 75%+) and book integrity by making AMS / CRM / renewal-queue disagreement **visible, classified, and owned** every business day.

Drift today causes:

- false tombstones (CRM or mirror says gone; AMS still has the policy)
- unclassed cancellations (Non Pay treated like Rewrite, or the reverse)
- stale 30-day renewals with no recorded touch
- premium in CRM that does not match NowCerts
- duplicate policy rows that inflate or hide the book

## 2. Stakeholders

| Stakeholder | Interest | Authority |
|-------------|----------|-----------|
| Lamar | Book, money, High/Critical exceptions, CRM push approval | Product owner / Admin |
| Gretchen | Daily CSR work: cancels, renewals, Low/Medium exceptions | CSR user |
| Hermes (system) | Scheduled CRM pull, optional health read | Integration; no approvals |
| NowCerts | Policy system of record | Not written by this app |
| Zoho CRM | Account / Deal / Policy mirror | Read always; write only when approved |

## 3. Current state vs future state

| Current | Future |
|---------|--------|
| Operators compare AMS, CRM, and renewal lists by memory and exports | Creator `Policy_Master` holds the working copy; agent compares snapshots |
| Status changes are not a durable trail | Every status/Active change inserts `Policy_Status_History` |
| Cancel reason is free text or missing | Required `Cancellation_Class` enumeration |
| Rewrites look like lost policies | Heuristic + human link `Rewrite_Of` / successor |
| Stale renewals discovered late | Queue stale rules (7/14 day by cadence) + CSR view |
| CRM updates happen ad hoc | Draft payload on `Policy_Audit`; execute only through approval gate |

## 4. Systems of record (business rule)

| Domain | Winner | This app |
|--------|--------|----------|
| Policy facts (status, premium, dates, carrier, GUID) | **NowCerts** | Copy + flag CRM lag |
| Accounts, Contacts, Deals | **Zoho CRM** | Stamp IDs only; never create Accounts |
| Renewal eligibility / Project 85 ledger | Hermes / Supabase | Creator queue is a **worklist**, not a second eligibility engine |
| Commissions | Hermes money tables | Flag premium drift only; do not book |
| Notes, assignment, last contact | Human in Creator/CRM | AMS pull must not overwrite Notes |

Conflict table (must implement):

- AMS vs CRM on premium/dates/carrier/status → NowCerts wins; verdict `stale_crm` or `status_mismatch`; draft CRM update
- Duplicate number or GUID → `duplicate_policy`; human merge
- Outbound sync still pending → `pending_sync`; do not re-push
- Cancelled + overlapping new policy same insured+LOB → `rewrite_detected`
- CRM has policy, AMS GUID empty → `missing_in_ams`; **do not tombstone**
- AMS has policy, CRM ID empty >24h → `missing_in_crm`; no Account create

## 5. Business requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| BR-01 | Maintain one Policy_Master row per NowCerts policy GUID | Must |
| BR-02 | Policy_Number is **not** database-unique so duplicates can be recorded and flagged | Must |
| BR-03 | Normalize AMS/CRM status and billing to the RSG picklists; unknown status is Error | Must |
| BR-04 | Classify every cancel: Non Pay, Rewrite, Insured Request, Underwriter, Other | Must |
| BR-05 | Detect rewrites: same insured GUID + same LOB + successor effective within 60 days of cancel/expiration | Must |
| BR-06 | If multiple successor candidates, cap confidence at 60 and open High exception — human picks the link | Must |
| BR-07 | Hybrid premium discrepancy: ≥ $25 OR ≥ 1% | Must |
| BR-08 | Agency_Fee is shop fee, not commission; missing Agency Bill fee is info, not `financial_discrepancy` alone | Must |
| BR-09 | Renewal queue stale: 7 days in 30/Past Due Open or In Progress; 14 days in 60 Open; or policy left Open after terminal status; or expiration dates disagree | Must |
| BR-10 | Last_Touched_At updates only on Queue_Status, Last_Contact_Date, Strategy_Notes, Risk_Status — not form open | Must |
| BR-11 | Never hard-delete Policy_Master or Renewal_Queue; dismiss = checkbox + status | Must |
| BR-12 | Exception SLA (calendar hours v1): Critical 4, High 24, Medium 72, Low 120 | Must |
| BR-13 | Auto-close Open exceptions on later `clean_match` **only** if prior verdict was `pending_sync` or `stale_crm` | Must |
| BR-14 | Gretchen cannot resolve High/Critical; cannot approve CRM push | Must |
| BR-15 | Policy_Tier: A if annualized ≥ $5000 or Commercial premium ≥ $2500; B if Active or expiring ≤90 days; else C | Must |
| BR-16 | Daily recon 6:45am ET for A/B/Active/120-day window/dirty verdicts; weekly Sunday 7:00am for Tier C | Must |
| BR-17 | No NowCerts write APIs from Creator | Must |
| BR-18 | No Zoho CRM Account create; Policy create is a human-executed draft | Must |

## 6. Process requirements

See attached process diagrams (same package):

1. Systems of record
2. Daily reconciliation
3. Verdict decision tree
4. Cancellation and rewrite
5. Exception and SLA
6. Approval-gated CRM write

## 7. Data requirements (forms)

Five forms only. Field dictionaries are the `forms_*.csv` files (appendix). Summary:

- **Policy_Master:** identity, insured snapshot, coverage, status, dates, financials, lineage, recon stamps, approval gate
- **Policy_Status_History:** source system AMS/CRM/Creator/Hermes; old/new status and Active
- **Renewal_Queue:** premiums current/renewal, Increase_Percent formula, Risk_Status, Cadence_Bucket, stale flags
- **Policy_Audit:** Run_ID, verdict, confidence, snapshots, findings vs recommendation, Recommended_Payload JSON
- **Audit_Exceptions:** severity, SLA clocks, resolution class

## 8. Assumptions

- Zoho CRM Policies custom module exists or will exist per `docs/zoho/fields_policies.csv`
- Gretchen and Lamar have Creator users
- Phase 1 has no live CRM connection
- Seed data is only `tests/sample_records.json` (prefixed `RSG-TEST-`)

## 9. Constraints

- Time zone America/New_York
- USD, 2 decimals
- Picklist strings are exact (including `Won't Fix`, `Non Pay`, `AT_RISK`)
- Deluge dialect may need minor syntax adjust; **logic and names must not change**

## 10. Acceptance (business)

Business accepts Phase 2 when each seed verdict/confidence in `acceptance_cases` is reproduced with no invented policies. Business accepts Phase 4 when a sandbox CRM push is blocked without the approval trio and Accounts are never created.
