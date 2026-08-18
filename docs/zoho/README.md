# Zoho CRM Field-Create Pack (Hermes → Zoho)

Artifacts for recreating the Hermes CRM field model in Zoho CRM.

| File | Purpose |
|------|---------|
| `fields_accounts.csv` | Account custom fields ← `canonical_clients` |
| `fields_deals.csv` | Deal custom fields ← `opportunities` |
| `fields_policies.csv` | Custom module **Policies** ← `canonical_policies` |
| `fields_renewal_events.csv` | Custom module **Renewal_Events** ← `renewal_candidates` |
| `fields_renewals.csv` | Custom module **Renewals** ← `project_85_renewals` |
| `fields_ams_write_queue.csv` | Custom module **AMS_Write_Queue** ← `outbound_sync_queue` |
| `picklists_nowcerts_seed.csv` | Exact Hermes `nowcerts_picklist_options` seeds (labels + option_id UUIDs) |
| `picklists_hermes_vocab.csv` | Additional Hermes vocab not in that seed table (types, likelihoods, statuses, queue enums) |
| `FIELD_CREATE_CHECKLIST.md` | Ordered create steps + uniqueness / pipeline rules |
| [`creator-renewals-desk/`](creator-renewals-desk/) | Zoho Creator **Renewals Desk** — Gretchen's live workstation over these modules |
| [`creator-renewals-desk/ZIA_PASTE_PROMPT.md`](creator-renewals-desk/ZIA_PASTE_PROMPT.md) | Paste into Zia **inside** existing `renewals-desk` (not a new app) |

## How to use

1. Create custom modules **Policies**, **Renewal_Events**, **Renewals**, **AMS_Write_Queue** (if not using Zoho Insurance vertical for Policies).
2. Create two Deal pipelines: **New Business** and **Renewals**, with stages copied from `picklists_nowcerts_seed.csv` (`pipeline_new_business` / `pipeline_renewal`).
3. For each `fields_*.csv` row: create the field with the given **API_Name**, **Data_Type**, **Length**, and picklist values.
4. Store NowCerts option UUIDs in the companion `*_Option_ID` fields (do not invent new UUIDs).
5. Mark External IDs as listed in the checklist before any AMS sync.

## CSV column legend (`fields_*.csv`)

| Column | Meaning |
|--------|---------|
| `Module` | Zoho module / custom module API name |
| `Display_Label` | UI label |
| `API_Name` | Suggested Zoho API name (custom fields end in `__c` for custom modules; Accounts/Deals custom fields also use `__s` or auto-suffix per org — adjust to match your CRM) |
| `Data_Type` | Zoho field type |
| `Length` | Max length / precision |
| `Mandatory` | Y/N |
| `Unique` | Y/N |
| `External_ID` | Y = use as Zoho External ID for sync |
| `Default_Value` | Default if any |
| `Picklist_Source` | File + list_key, or inline |
| `Sync_Direction` | AMS→Z / Z→AMS / Z-only / Derived / System |
| `Hermes_Column` | Source column in Supabase |
| `Notes` | Constraints / push rules |

> Zoho orgs differ on whether custom field API names keep a `__c` / `__s` suffix. Treat `API_Name` as the logical name; rename to match Settings → APIs → API Names after create.

## Source of truth for seeded picklists

`supabase/migrations/20260810130000_nowcerts_picklist_options.sql`
