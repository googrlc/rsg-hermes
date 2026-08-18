# Request for Proposal (RFP) — Build Response for Zia AI

**RFP number:** RSG-CREATOR-RECON-2026-01  
**Title:** Zoho Creator application — RSG Policy Reconciliation  
**Issuer:** Risk Solutions Group  
**Respondent:** Zia AI (Zoho Creator)  
**Issue date:** 2026-08-18  
**Response type:** Build the application in Zoho Creator. Do not write a sales proposal. Do not invent scope.

This RFP is the statement of work. Attach it to Zia together with the PRD, BRD, and process-diagram PDF. Field CSVs and Deluge in `ZIA_UPLOAD.xlsx` are the data appendix and win over prose if they conflict.

---

## 1. Invitation

RSG invites Zia to **create** a Zoho Creator application that reconciles insurance policies across NowCerts (AMS), Zoho CRM, and the renewal worklist.

Success is a working Creator app that matches this RFP. Failure is extra forms, renamed verdicts, invented fields, LLM-based scoring, or any NowCerts write.

## 2. Scope of work

### In scope

- Application display name: **RSG Policy Reconciliation**
- Application link name: `rsg_policy_reconciliation`
- Exactly **five** forms, API names in this order:
  1. `Policy_Master`
  2. `Policy_Status_History`
  3. `Renewal_Queue`
  4. `Policy_Audit`
  5. `Audit_Exceptions`
- Every field in the form dictionary (appendix / Excel sheets)
- Every picklist value exactly as listed
- Lookups and related lists as specified
- Role permissions: Lamar Admin, Gretchen CSR
- Status-history hook on Policy_Master add/edit
- **Phase 1 only** until the issuer confirms Phase 2

### Out of scope (reject if proposed)

- Additional forms in Phase 1
- NowCerts / Momentum POST, Insert, PartialUpdate
- `zoho.crm.createRecord` for Accounts
- Commission posting
- Client portal
- Renaming the 12 verdicts
- A generative model inside `thisapp.recon.verdict` or `score`

## 3. Mandatory technical requirements

| ID | Requirement | Proof |
|----|-------------|-------|
| M1 | Deluge_Name in CSVs = Creator field link names | Export of link names |
| M2 | Unique `NowCerts_Policy_GUID` when present; Policy_Number **not** unique | Duplicate Policy_Number save succeeds |
| M3 | 12 verdicts, first-match order in the BRD/PRD | Run Recon on seeds (Phase 2) |
| M4 | Confidence S1–S25 point table, integer 0–100 | Seed expected_confidence |
| M5 | Cancel class required on Cancelled / Flat Cancel / Pending Cancel | Save blocked |
| M6 | Is_Rewrite requires Rewrite_Of | Save blocked |
| M7 | Active checkbox true only if Policy_Status = Active | On save |
| M8 | No `__c` suffixes | Link names |
| M9 | CRM write only if Approved_To_Push AND Approved_By AND Approved_At within 24h AND Recommended_Payload non-empty | Phase 4 stub returns gate failed otherwise |
| M10 | Integration user cannot set Approved_To_Push | Permissions |

## 4. Deliverables by phase

| Phase | Deliverable | Issuer action |
|-------|-------------|----------------|
| 1 | App + 5 forms + fields + picklists + lookups + history hook + views skeleton + permissions | **Issuer reviews. Zia waits.** |
| 2 | `thisapp.recon.*` functions, Run Recon button, Policy_Audit / exceptions | Issuer runs sample seeds |
| 3 | Schedules 6:45am ET daily, Sunday Tier C, weekday SLA sweep, notifications | Issuer watches one dry run |
| 4 | `pullCRM` read; gated `pushCRMIfApproved` | Sandbox only |
| 5–6 | Optional Hermes health; Analytics | Later RFP addendum |

## 5. Data appendix (must follow)

| Artifact | Contents |
|----------|----------|
| `forms_policy_master.csv` (and four sibling CSVs) | Field create list |
| `picklists.csv` | Exact option strings |
| `views.csv` / `workflows.csv` | Reports and automation |
| `deluge/01` through `08` | Copy-paste functions |
| `tests/sample_records.json` | Only allowed seed policies |
| Process diagrams PDF | Flows Zia must implement |

If prose in this RFP and a CSV disagree, **the CSV wins**. If Deluge comments and the scoring table disagree, **the scoring table in the PRD/BRD wins**.

## 6. Evaluation (how RSG will judge the build)

| Weight | Criterion |
|--------|-----------|
| 30% | Schema completeness (five forms, all fields, exact picklists) |
| 25% | Verdict order and seed accuracy (Phase 2) |
| 20% | Guardrails (no AMS write, no Account create, approval gate) |
| 15% | Permissions (Gretchen vs Lamar) |
| 10% | Operational fit (views, SLA hours, stale-touch rule) |

Any invented policy number, premium, or GUID in sample data is an automatic fail.

## 7. Response instructions for Zia

1. Read PRD, BRD, this RFP, and the process diagrams.
2. Load field dictionaries from the attached Excel/CSV/JSON pack.
3. Create the Creator application.
4. Execute **Phase 1 only**.
5. Reply with: form names created, field counts per form, picklist keys loaded, and a list of anything you could not create (field name + reason). Do not silently skip.
6. Do not start Phase 2 until the issuer says to proceed.

## 8. Legal / operating constraints

- NowCerts is the system of record for policy facts.
- Zoho CRM is the system of record for Accounts, Contacts, Deals.
- Human approval is required for CRM writes. There is no Creator path to AMS writes.
- Test seeds use `RSG-TEST-*` numbers only.

## 9. Issuer contacts

- Product / approvals: Lamar  
- CSR operator: Gretchen  
- Build agent: Zia AI on Zoho Creator
