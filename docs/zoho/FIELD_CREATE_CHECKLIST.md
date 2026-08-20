# Zoho Field-Create Checklist

Use with the CSVs in this folder. Labels and UUIDs for seeded NowCerts picklists must match `picklists_nowcerts_seed.csv` exactly (source: `supabase/migrations/20260810130000_nowcerts_picklist_options.sql`).

## 0. Prerequisites

- [ ] Zoho CRM org with API access
- [ ] Decide: custom module **Policies** vs Zoho Insurance Policy module
- [ ] Create custom modules: `Renewal_Events`, `Renewals`, `AMS_Write_Queue`
- [ ] Create Users matching `agency_crm_users` emails (for Deal Owner / Approved By)

## 1. Picklists & pipelines (do first)

### 1a. Deal pipelines — copy labels exactly

**Pipeline: New Business** (`list_key=pipeline_new_business`)

| Order | Stage label (exact) | option_id (store on Deal) | Prob % |
|------:|---------------------|---------------------------|-------:|
| 0 | Not Assigned | `d82f13b9-f96d-d154-cdf9-251cc0f03c93` | 5 |
| 1 | Preparing Application | `9c3bae52-2c28-2c49-ffe7-226c5417c41c` | 10 |
| 2 | Sent For Quoting | `09092bd2-3444-429a-7ff6-fdb58f77a68b` | 25 |
| 3 | Quotes Received | `092bb8e4-18f6-fa69-4263-5ca10f78826f` | 50 |
| 4 | Sent Proposal | `e3047f00-88c6-930d-5683-9761bae9f632` | 65 |
| 5 | Request to Bind | `16f23fa8-2515-e6ec-9e11-423849b4d2f1` | 85 |
| 6 | Bound / Won | `82f61678-caea-09f8-e731-5f696449ee0d` | 100 |
| 7 | Lost | `c9ad07f3-689b-36b4-928c-0d7b8597f154` | 0 |

**Pipeline: Renewals** (`list_key=pipeline_renewal`)

| Order | Stage label (exact) | option_id | Prob % |
|------:|---------------------|-----------|-------:|
| 8 | Renewal in 90 days | `bb6eb18f-8b31-cf43-3b57-45cea520183a` | 40 |
| 9 | Renewal in 60 days | `f7ffbbe0-2f08-3e1a-5765-f5fe6e8ce997` | 55 |
| 10 | Renewal in 30 days | `0c76b0dc-acf4-72f1-9a01-1222dede624f` | 70 |
| 11 | Requote Renewal | `9fea61ef-40d1-c7a8-58b5-01b7a74c617b` | 60 |
| 12 | Annual Policy Review | `b917834c-4262-9863-6720-f912daa6f219` | 50 |
| 13 | Complete/Auto-Renewal | `8eb2161c-1925-43d0-602b-5c1486f93def` | 100 |
| 14 | Bound / Won | `76a8a582-6a6f-dbf7-2929-50096e26cb50` | 100 |
| 15 | Not Renewed | `cbad2c95-ef0a-94f2-a534-fc38f2907b02` | 0 |

> Note: **Bound / Won** appears in both pipelines with **different** `option_id` values. Always store the pipeline-specific UUID on `Stage_Option_ID`.

### 1b. Lead Status (`list_key=lead_status`) — lowercase exact

| Order | Label | option_id |
|------:|-------|-----------|
| 16 | new | `b2e18587-57b1-91bc-6b1a-d7690c1a4618` |
| 17 | working | `7d82ec17-15db-7f3d-2744-a0ade49c15a0` |
| 18 | quoted | `51836de6-5193-2fbc-ca70-a9fec7e41946` |
| 19 | converted | `a97b0b79-7b68-43b1-dd2f-1c1f5ce5a9e8` |
| 20 | lost | `ab69f551-f77f-9a65-bb98-5ffd4da476ba` |

### 1c. Renewal status seed (`list_key=renewal_status`)

Use as a subset of Policy Status or a separate Renewal Status field:

| Order | Label | option_id |
|------:|-------|-----------|
| 21 | Up for Renewal | `95a4fc61-bc52-c296-1113-156e343b35da` |
| 22 | Renewing | `3d01d3f4-d334-f3ed-39a5-e04e0381b56d` |
| 23 | Renewed | `3dd5b4be-44be-f13e-842a-e814774a6041` |
| 24 | Non-Renewed | `c35d86b7-fb74-2a17-f734-7c1395c51713` |
| 25 | Cancelled | `a2b134fa-5416-5873-9493-979b501e6f2c` |

### 1d. Endorsement type (`list_key=endorsement_type`) — for Cases later

| Order | Label | option_id |
|------:|-------|-----------|
| 26 | Add Driver | `1966a48e-07a5-f5bb-9e1d-f2f862318381` |
| 27 | Remove Driver | `a9ce714b-c49d-94c6-f95b-d7def7f6de96` |
| 28 | Replace Driver | `2c3c659a-7568-63a8-5ef9-a17d0fb45d54` |
| 29 | Add Vehicle | `3669f7eb-0878-d3ae-204a-10dde33d2b44` |
| 30 | Replace Vehicle | `384baf5f-0058-db81-0d7b-1963b3a7792a` |
| 31 | Address Change | `5c7583c3-2288-eeb5-0b77-3dc08307f0bf` |
| 32 | Coverage Change | `91feb5aa-2fce-7963-06e6-b66204cdaf55` |
| 33 | Policy Change | `391d6b38-2742-a68f-90bf-05c435824a96` |
| 34 | Certificate of Insurance | `39b78879-21ce-6f4f-59d4-b153c330295f` |
| 35 | Other | `d92e98fd-533c-1958-e938-3a8ad75065aa` |

### 1e. Other Hermes vocab

Import values from `picklists_hermes_vocab.csv` for: Opportunity_Type, Prospect_Type, Insured_Type, Win_Likelihood, Deal_Status, Policy_Status (full normalize set), Billing_Type, Risk_Status, Eligibility, Branch, Segment, queue enums, **Desk_Stage**, **Disposition**, **Recommended_Action**, **Window_Bucket**.

## 2. Create fields (CSV order)

| Step | CSV | Module | External IDs to enable |
|-----:|-----|--------|------------------------|
| 1 | `fields_accounts.csv` | Accounts | `NowCerts_Insured_GUID` |
| 2 | `fields_policies.csv` | Policies | `NowCerts_Policy_GUID` |
| 3 | `fields_deals.csv` | Deals | `Hermes_Opportunity_ID` (optional); unique NowCerts IDs |
| 4 | `fields_renewal_events.csv` | Renewal_Events | `Hermes_Candidate_ID` |
| 5 | `fields_renewals.csv` | Renewals | `Hermes_Renewal_ID` |
| 6 | `fields_ams_write_queue.csv` | AMS_Write_Queue | `Queue_ID` |

For each row: create field → set length → set picklist → mark mandatory/unique → set External ID where flagged.

## 3. Uniqueness & relationships to enforce

- [ ] Accounts: unique `NowCerts_Insured_GUID`
- [ ] Policies: unique `NowCerts_Policy_GUID`; unique `Policy_Number`
- [ ] Deals: unique `(Client_Identifier, Line_of_Business, Opportunity_Type)` — Zoho: workflow or custom unique constraint / Deluge guard (native multi-field unique is limited; enforce in sync layer)
- [ ] Deals: unique `NowCerts_Opportunity_ID` / `NowCerts_Quote_GUID` / `NowCerts_Policy_GUID` when non-blank
- [ ] Renewal_Events: unique `Renewal_Key` = `insured|lineage|date`
- [ ] Renewals: unique `Policy_Number`
- [ ] AMS_Write_Queue: at most one **queued** row per `(Object_Type, Object_ID, Destination, Action)`

### Lookups

- [ ] Deals → Accounts
- [ ] Policies → Accounts
- [ ] Renewal_Events → Accounts, Policies
- [ ] Renewals → Policies, Accounts, Renewal_Events (`Related_Renewal_Event`), Deals (`Related_Deal`)
- [ ] AMS_Write_Queue → Accounts / Deals / Policies / Renewals (optional convenience)

## 4. Approval & AMS write rules (Zoho Blueprint / Approval)

- [ ] AMS_Write_Queue: cannot leave “awaiting approval” without `Approved_By` + `Approved_At`
- [ ] Deal stage Bound / Won or Lost with `NowCerts_Opportunity_ID` → enqueue `opportunity_writeback`
- [ ] Deal Bound / Won without AMS opp id + `Bound_Policy_Number` → enqueue `opportunity_won`
- [ ] Account/Policy portal edits → enqueue `client` / `policy` with pushable fields only
- [ ] Renewal desk actions → enqueue `renewal` with payload.action ∈ `request_terms|prepare_options|client_follow_up|update_ams`
- [ ] Prospects: do **not** create AMS insured by default (`HERMES_INTAKE_STAGES_AMS_INSURED` off)

### Pushable field allow-lists (exact Hermes maps)

**Account → AMS:** Insured Name, Email, Phone, Billing Street, City, State, Code  
**Policy → AMS:** Carrier, Line of Business, Premium, Annualized Premium, Effective Date, Expiration Date

## 5. Formulas

- [ ] `Renewals.Increase_Percent` = `((Premium_Renewal - Premium_Current) / Premium_Current) * 100` (null-safe when Premium_Current = 0 → 0)
- [ ] `Renewals.Days_To_Expiration` = `Datecomp(${Expiration_Date}, Today)` (signed days; negative = past due)
- [ ] Optional: Deal Probability default from Stage (see pipeline tables above)

`Window_Bucket` is a **picklist written by Hermes** (`hermes --sync-zoho-renewals`), not a formula — personal LOB always buckets as `personal`.

## 5b. Creator Renewals Desk (after fields exist)

- [ ] Import the Creator app from [`docs/zoho/creator-renewals-desk/INSTALL.md`](creator-renewals-desk/INSTALL.md)
- [ ] Bind reports to CRM modules Policies / Renewal_Events / Renewals / AMS_Write_Queue
- [ ] Publish to Gretchen and Lamar
- [ ] Confirm `hermes --sync-zoho-renewals` then `--sync-zoho-ams-queue` are on cron after `--renewal-refresh`

Desk-owned fields (`Desk_Stage`, `Disposition`, `Recommended_Action`, touch dates, `Related_Deal`) must **not** be overwritten by book sync. Hermes sets `Desk_Stage=Identified` only on create.

## 6. Sync direction smoke tests

| Test | Expect |
|------|--------|
| Import Account with GUID | Dedupes on External ID |
| Move Deal to Bound / Won with AMS opp id | Queue row; no silent AMS call without approval |
| Edit Account phone | Override + queue `client` update |
| Nightly book sync | Policies upsert by GUID; lineage preserved |
| Dismiss renewal | `Dismissed=true` or Eligibility=`excluded`; row not hard-deleted |

## 6b. Document links (do not attach files)

See [`CONNECT_NEXTCLOUD_URLS.md`](CONNECT_NEXTCLOUD_URLS.md). Nextcloud is the file store. Zoho holds https URLs.

- [ ] Run `python scripts/playwright_zoho_document_url_fields.py --apply` (headed login) **or** `python scripts/ensure_zoho_document_url_fields.py --apply` (needs `ZohoCRM.settings.ALL`)
- [ ] Accounts: `Nextcloud_Folder_URL` on the Standard layout
- [ ] Policies / Deals / Renewals: `Primary_Folder_URL` + `Document_URL`
- [ ] Optional: create modules Claims and Certificates, then re-run the script
- [ ] Confirm a click on Account → Nextcloud Folder URL opens the client folder
- [ ] Do **not** use Zoho attachments as the document library

## 7. Do not create as editable user fields

- Policy `raw_payload`
- Any `espocrm_*` column
- Typed `Increase_Percent` (use Formula)
- Pushable `Active` checkbox to AMS
- Free-edit of AMS_Write_Queue.Payload without a structured UI

## API name note

Zoho appends org-specific suffixes (`__s`, `__c`) to custom fields. After create, export **Settings → Developer Space → APIs → API Names** and align integration code to the live API names. The CSVs use logical names without suffixes.
