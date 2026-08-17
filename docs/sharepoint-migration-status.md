# SharePoint migration status — Obsidian vault → RSG-Knowledge

> **Operations sign-off (August 2026):** Obsidian vault migration into SharePoint is
> **complete**. This doc records the target layout and checklist; run live verification
> with SharePoint MCP tools (Track A in [`amy-copilot-tool-wiring.md`](amy-copilot-tool-wiring.md)).

Source playbook: [`sharepoint-knowledge-consolidation.md`](sharepoint-knowledge-consolidation.md)

---

## Canonical site

| Item | Value |
|---|---|
| **Site** | RSG-Knowledge |
| **URL** | `https://<tenant>.sharepoint.com/sites/RSG-Knowledge` (set `SHAREPOINT_SITE_URL`) |
| **Library** | Documents (default) |
| **Amy grounding** | This site **only** |
| **Obsidian vault** | `rsg-obsidian-vault` — drafting; SharePoint is published SOR for Amy |

---

## Folder tree (expected after migration)

```text
RSG-Knowledge/Documents/
├── 00-meta/
│   ├── site-index.md
│   ├── migration-log.md
│   └── deprecated/
├── 01-operations/
│   ├── service-desk/
│   ├── intake/
│   ├── renewals/
│   ├── commissions/
│   └── hermes/
├── 02-personal-lines/
│   ├── reference/
│   ├── intake/
│   └── templates/
├── 03-commercial-lines/
│   ├── reference/
│   ├── intake/
│   └── submissions/
├── 04-carriers/
│   ├── appetite/
│   ├── contacts/
│   └── underwriting-notes/
├── 05-compliance-licensing/
├── 06-training-onboarding/
├── 07-templates/
│   ├── email/
│   └── forms/
└── 99-archive/
    └── YYYY-MM-source-site-name/
```

---

## Obsidian → SharePoint mapping (completed)

| Vault area | SharePoint folder | Status |
|---|---|---|
| Procedures / SOPs | `01-operations/` | ✅ migrated |
| Carrier notes | `04-carriers/` | ✅ migrated |
| Personal lines guides | `02-personal-lines/` | ✅ migrated |
| Commercial lines guides | `03-commercial-lines/` | ✅ migrated |
| Templates | `07-templates/` | ✅ migrated |
| Training / onboarding | `06-training-onboarding/` | ✅ migrated |
| Compliance / licensing | `05-compliance-licensing/` | ✅ migrated |

Each migrated note should include front matter or header: `SharePoint: /sites/RSG-Knowledge/...`

---

## Consolidation checklist

| Item | Status |
|---|---|
| Site inventory reviewed | ✅ (Obsidian migration covered primary sources) |
| Single site **RSG-Knowledge** with folder tree | ✅ |
| `00-meta/site-index.md` and `migration-log.md` | ✅ expected — verify with `list_folder` |
| Keepers migrated; duplicates merged | ✅ per Operations |
| Old knowledge sites read-only or retired | ⏳ confirm with `list_sites` if needed |
| `SHAREPOINT_SITE_URL` on hermes-gretch | ⏳ verify in `/opt/app/.env` |
| Copilot grounded on RSG-Knowledge only | ⏳ wire Track A0 |
| SharePoint MCP egress + connector | ⏳ wire Track A1–A8 |
| Amy smoke: COI procedure | ⏳ prompt in wiring doc |

---

## Verify on the box (post-migration)

```bash
source .venv/bin/activate
export $(grep -E '^(MS365_|SHAREPOINT_SITE_URL|API_SERVER_KEY)' /opt/app/.env | xargs)

# Folder tree spot-check
curl -s -H "Authorization: Bearer $API_SERVER_KEY" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_folder","arguments":{"path":"/"}}}' \
  http://127.0.0.1:8082/mcp | python3 -m json.tool

# COI / service content search
curl -s -H "Authorization: Bearer $API_SERVER_KEY" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"search_knowledge","arguments":{"query":"COI certificate insurance"}}}' \
  http://127.0.0.1:8082/mcp | python3 -m json.tool
```

Or regenerate tenant inventory (optional):

```bash
python scripts/sharepoint_site_inventory.py --query RSG --deep
```

---

## Not in SharePoint (by design)

| Data type | System of record |
|---|---|
| Client COI PDFs, policies | NowCerts + Nextcloud |
| CRM pipeline, notes | Zoho |
| Live renewal state, KPIs | Hermes / Supabase |
| Commission ledger | Supabase |

Amy should answer *how* to process a COI from SharePoint SOPs; the actual COI file lives in
NextCerts/Nextcloud via Hermes tools (`list_documents`, `client_documents`).
