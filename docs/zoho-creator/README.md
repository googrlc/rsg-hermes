# Zoho Creator — Policy Reconciliation Agent (Zia AI pack)

Builder pack for **Zia AI in Zoho Creator**. Zia’s file upload accepts only:

`.xls .xlsx .xlsm .csv .tsv .ods .mdb .accdb .ds .json .numbers`

Maximum 2 GB. Files over 100 MB must be CSV. **Markdown is not an upload
format.** Use the files in `zia-upload/` when talking to Zia.

This app is an **operations reconciliation workspace**. It does **not** replace:

| System | Role |
|--------|------|
| **NowCerts (AMS)** | System of record for policy facts |
| **Zoho CRM** | System of record for Accounts, Contacts, Deals |
| **Supabase / Hermes** | Ops/analytics mirror, queues, KPIs, renewal state |
| **Zoho Creator (this app)** | Compare those systems, score confidence, queue exceptions |

## What to upload to Zia (one file)

Use **one** of these — they are the same spec in Zia-accepted formats:

| File | Format | Use when |
|------|--------|----------|
| `ZIA_UPLOAD.xlsx` | Excel | Best default. Sheets for forms, picklists, Deluge, seeds |
| `ZIA_UPLOAD.json` | JSON | Full spec + Deluge + seeds in one object |
| `ZIA_UPLOAD.csv` | CSV | Same content as rows (`Section,Item,Field,Value`) if Excel/JSON is blocked |

Copies also live in `zia-upload/` with the same names.

Do **not** upload Markdown. Zia only accepts `.xls .xlsx .xlsm .csv .tsv .ods .mdb .accdb .ds .json .numbers` (max 2 GB; over 100 MB must be CSV).

Rebuild those generated files after editing sources:

```bash
python3 docs/zoho-creator/scripts/build_zia_upload.py
python3 docs/zoho-creator/scripts/validate_pack.py
```

## Human-readable sources (git; not for Zia upload)

| File | Purpose |
|------|---------|
| `ZIA_PASTE_PROMPT.md` | Short prompt (also inside the JSON pack) |
| `ZIA_AI_BUILD_INSTRUCTIONS.md` | Full actionable specification |
| `forms_*.csv` | Form field create lists |
| `picklists.csv` | Exact picklist strings |
| `views.csv` / `workflows.csv` | Reports and automation inventory |
| `deluge/*.dg` | Verdict, score, hooks, CRM pull |
| `tests/acceptance_cases.md` | Definition of Done |
| `tests/sample_records.json` | Seed rows for a dry-run reconciliation |

## How to use with Zia

1. In Zoho Creator, start **Zia → Create application from description**.
2. Paste the text from `ZIA_PASTE_PROMPT.md` (or row 2 of `00_READ_ME_FIRST.csv`).
3. Upload the JSON pack and the XLSX. Do not try to upload `.md` files.
4. After forms exist, verify fields against the five form sheets.
5. Install Deluge from the Deluge sheet / `deluge_scripts.csv` in install order.
6. Create views and workflows from those sheets.
7. Run acceptance cases before connecting live CRM/AMS.

## Related Hermes docs (do not contradict)

- `docs/zoho/` — Zoho CRM field model for Policies / Renewals
- `docs/zoho-supabase-sync-design.md` — systems of record
- `docs/hermes-operating-constitution.md` — no unapproved AMS/CRM writes
- `packages/rsg-hermes-core/hermes_core/canonical.py` — status + billing normalize
