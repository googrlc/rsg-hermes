# Acceptance cases — RSG Policy Reconciliation (Zoho Creator)

Run after Phase 1 (schema) and Phase 2 (agent). Use
`tests/sample_records.json` as the only seed. Do not invent extra policies.

Pass = actual `Policy_Audit.Verdict` equals `expected_verdict` and
`Policy_Audit.Confidence` equals `expected_confidence`.

## Phase 1 — schema

| ID | Case | Pass criteria |
|----|------|----------------|
| P1-01 | Five forms exist | Link names: Policy_Master, Policy_Status_History, Renewal_Queue, Policy_Audit, Audit_Exceptions |
| P1-02 | Field counts | Policy_Master ≥ 50 data fields from CSV (including formulas); other forms match CSV row counts |
| P1-03 | Picklists | Every `picklists.csv` value exists; no extra verdict values |
| P1-04 | Unique keys | Saving a second Policy_Master with the same NowCerts_Policy_GUID fails. Saving a second row with the same Policy_Number **succeeds** (duplicate_policy is the detector). |
| P1-05 | Status history hook | Edit Policy_Status Active → Cancelled inserts exactly one Policy_Status_History row with Old_Status=Active, New_Status=Cancelled, Source_System=Creator |
| P1-06 | Cancel class required | Save Cancelled with empty Cancellation_Class is blocked |
| P1-07 | Rewrite_Of required | Is_Rewrite=true with empty Rewrite_Of is blocked |
| P1-08 | Permissions | Gretchen cannot delete Policy_Status_History; Gretchen cannot set Approved_To_Push |

## Phase 2 — agent (sample_records.json)

| Seed id | expected_verdict | expected_confidence | Notes |
|---------|------------------|--------------------:|-------|
| S01 | clean_match | 100 | Happy path Active book |
| S02 | duplicate_policy | 70 | Twin of S02b same Policy_Number; S15 −30 |
| S02b | duplicate_policy | 70 | Twin |
| S03 | pending_sync | 92 | Sync_Status=Pending and Pending_Queue_Jobs=1 (−8 S22 only) |
| S04 | rewrite_detected | 100 | Cancelled + Rewrite class + Rewrite_Of → S04b. S16/S17 do not fire |
| S04b | clean_match | 100 | Successor Active rewrite target |
| S05 | status_mismatch | 88 | AMS+Creator Active vs CRM Cancelled (−12 S9). S10 does not fire |
| S06 | financial_discrepancy | 82 | $100 vs $50 → abs $50 ≥25 (−10 S12) and 100% ≥1 (−8 S13) |
| S07 | missing_in_crm | 90 | GUID set, CRM_Policy_ID empty (−10 S3), Last_Synced ~30h (no S19) |
| S08 | missing_in_ams | 75 | CRM id set, GUID empty (−25 S2) |
| S09 | stale_renewal_queue | 90 | Open 30-day queue untouched 10 days (−10 S21) |
| S10 | stale_crm | 100 | Statuses and premiums match. Last_CRM_Modified 48h before Last_AMS_Modified. v1 uses the clock drift rule |
| S11 | cancel_reason_gap | 88 | Cancelled, class empty (−12 S16). Save guard is **on** in production; for this test, temporarily allow save via Admin bypass or import API |
| S12 | lineage_orphan | 88 | Renewed_Policy_GUID set to a GUID that does not exist (−12 S18) |

### S10 implementation note for Zia

In `03_verdict_engine.dg` after cancel/financial/status checks, if
`Last_AMS_Modified` and `Last_CRM_Modified` exist and CRM is more than 1 hour
behind AMS, return `stale_crm`. Do not require a second field diff in v1 if
the only CRM snapshots are status and premium and those already matched
(otherwise a higher verdict would have fired).

## Phase 3 — operations

| ID | Case | Pass |
|----|------|------|
| P3-01 | Exception on S05 | One Audit_Exceptions row, Severity High (Active vs Cancelled) |
| P3-02 | No exception on S01 | Zero Open exceptions |
| P3-03 | Auto-close | S03 pending_sync exception auto-resolves Resolution_Class=Data lag after a later clean_match only if you then clear Pending and re-run |
| P3-04 | Gretchen cannot resolve High | UI/Deluge blocks |
| P3-05 | SLA hours | Critical=4, High=24, Medium=72, Low=120 |
| P3-06 | Touch stamp | Opening Renewal_Queue without editing listed fields does **not** change Last_Touched_At |

## Phase 4 — CRM (sandbox)

| ID | Case | Pass |
|----|------|------|
| P4-01 | pullCRM | Stamps CRM_Policy_ID from search by GUID |
| P4-02 | No Account create | Deluge contains no `zoho.crm.createRecord("Accounts"` |
| P4-03 | Push blocked | pushCRMIfApproved without Approved_To_Push returns approval gate failed |
| P4-04 | Duplicate CRM | Two CRM hits set Sync_Status=Error |

## Forbidden (any phase)

- `insertInsured` / NowCerts POST / Policy Insert from Creator
- Invented picklist values
- Hard-delete of Policy_Master in a workflow
- LLM call inside verdict or score functions
