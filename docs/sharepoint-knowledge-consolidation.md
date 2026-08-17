# SharePoint knowledge consolidation — several sites → one

Amy Phase 1 needs **one canonical SharePoint site** for agency knowledge. Internal SOPs,
carrier reference, training, and templates belong here — not client files, CRM records,
or bound policy data.

**Target:** a single site (recommended name: **RSG-Knowledge**) with a predictable folder
tree. Retire or archive every other knowledge-oriented SharePoint site after migration.

Related: [`rsg-digital-operating-system.md`](rsg-digital-operating-system.md) ·
[`amy-getting-started.md`](amy-getting-started.md) ·
[`deploy/sharepoint_mcp/README.md`](../deploy/sharepoint_mcp/README.md)

---

## What belongs in SharePoint (and what does not)

| Belongs in **RSG-Knowledge** | Stays elsewhere |
|---|---|
| Internal SOPs, playbooks, checklists | Client documents → **Nextcloud** |
| Carrier appetite *reference* PDFs/guides (not live binding data) | COIs, policies → **NowCerts** |
| Training, licensing notes, onboarding | CRM notes, pipeline → **Zoho** |
| Email/communication *templates* (internal) | Commission ledger, KPIs → **Supabase / Hermes** |
| Obsidian vault exports (procedures, underwriting notes) | Live renewal state → **Hermes / Supabase** |

If a document is **client-specific** or **transactional**, it does not go in the knowledge site.

---

## Target site structure

One site, one default document library (**Documents**), top-level folders by function:

```text
RSG-Knowledge/
├── 00-meta/
│   ├── site-index.md              # what lives where (Amy reads this first)
│   ├── migration-log.md           # old path → new path, date, owner
│   └── deprecated/                # stubs pointing to new locations
├── 01-operations/
│   ├── service-desk/              # service-sops skill
│   ├── intake/                    # intake playbooks, crm-intake-writer context
│   ├── renewals/                  # renewal-playbook, Project 85
│   ├── commissions/               # commission SOPs (not statements)
│   └── hermes/                    # Command Center / Amy operator guides
├── 02-personal-lines/
│   ├── reference/                 # personal-lines-reference
│   ├── intake/                    # personal-lines-intake
│   └── templates/                 # Gretchen comms templates
├── 03-commercial-lines/
│   ├── reference/                 # commercial-auto-reference, class codes
│   ├── intake/                    # commercial-risk-intake
│   └── submissions/               # ACORD / submission checklists (not client packets)
├── 04-carriers/
│   ├── appetite/                  # static guides; live appetite = Hermes/Supabase
│   ├── contacts/
│   └── underwriting-notes/
├── 05-compliance-licensing/
├── 06-training-onboarding/        # client-onboarding (internal steps, not client folders)
├── 07-templates/
│   ├── email/                     # communication-templates
│   └── forms/                     # internal checklists
└── 99-archive/
    └── YYYY-MM-source-site-name/  # read-only copies before delete
```

**Naming rules**

- Folders: `kebab-case`, no spaces in machine paths (display names can be friendly).
- Files: prefer **Markdown** (`.md`) for SOPs Amy should read; PDF only when source is PDF.
- One canonical file per topic — merge duplicates during migration, link from `00-meta/`.

---

## Consolidation workflow

> **Rule:** Run a **site inventory before** creating or populating **RSG-Knowledge**.
> Pull and merge content from other sites only after each source is mapped.

### Phase A — Inventory (required first)

**Automated (Hermes / Cursor with `MS365_*` set):**

```bash
source .venv/bin/activate
python scripts/sharepoint_site_inventory.py --deep
python scripts/sharepoint_site_inventory.py --query RSG --deep
python scripts/sharepoint_site_inventory.py --query training --deep
```

Writes [`sharepoint-site-inventory.md`](sharepoint-site-inventory.md) (review + fill Decision columns).

**Or via MCP:**

```text
list_sites query="*"
list_sites query="RSG"
get_site_info site_url="https://tenant.sharepoint.com/sites/Some-Old-Site"
list_folder path="/"
```

**Or via Power Platform MCP (your Mac):**

```text
search_sharepoint_sites query="RSG"
list_sharepoint_files ...
```

1. List every SharePoint site that might hold agency knowledge (Teams-connected sites, old project sites, Obsidian sync folders, etc.).
2. For each site, capture in the inventory doc or spreadsheet:

| Source site | Library / folder | Topic | Keep / merge / archive / delete | Target path in RSG-Knowledge |
|---|---|---|---|---|
| `sites/Old-Training` | Documents/SOPs | PL quoting | merge | `02-personal-lines/reference/` |
| … | … | … | … | … |

3. **Review inventory** — assign keep / merge / archive / delete / exclude for every site.
4. **Only then** create or designate **RSG-Knowledge** and the folder tree below.

### Phase B — Build the target (after inventory approved)

1. Create site **RSG-Knowledge** (or rename an existing site if it is already the largest).
2. Create the folder tree above (empty folders are fine).
3. Add `00-meta/site-index.md` — a one-page map for humans and Amy.
4. Set `SHAREPOINT_SITE_URL` in `.env` / Cursor MCP / Copilot grounding to this site only.

### Phase C — Migrate content (ongoing, priority order)

Migrate in this order so Amy gets value early:

1. **01-operations/service-desk** + **02-personal-lines/reference** (Gretchen daily use)
2. **03-commercial-lines** + **04-carriers**
3. **07-templates** + **06-training-onboarding**
4. Everything else → **99-archive** or delete

Per file:

1. Check **Keep / merge / archive / delete** from the inventory.
2. If merge: combine into one `.md`, add “Supersedes …” in front matter.
3. Copy to target path (do not leave authoritative copies in two sites).
4. Log old URL → new path in `migration-log.md`.
5. On source site: replace moved pages with a short stub linking to the new path (or move to archive library).

### Phase D — Retire old sites

1. Set old sites to **read-only** for 30 days (communication: “moved to RSG-Knowledge”).
2. Confirm no links in Teams, Outlook signatures, or Hermes docs point to old URLs.
3. Archive a zip or `99-archive/` copy if legally required.
4. Delete or hide old sites from search (admin).

---

## Amy / Copilot grounding

After consolidation:

| Integration | Config |
|---|---|
| **Copilot Studio Phase 1** | Ground Amy on **RSG-Knowledge** site only |
| **SharePoint MCP** | `SHAREPOINT_SITE_URL=https://tenant.sharepoint.com/sites/RSG-Knowledge` |
| **Hermes `search_knowledge`** | Same single URL — searches default library |

Do not point Amy at multiple sites in production. During migration, use explicit `site_url` on
`get_site_info` / manual search only for inventory — not for operator-facing Amy.

---

## Obsidian vault (`rsg-obsidian-vault`)

The vault is the **authoring** source until content is stable:

1. Export or sync vault sections into the matching SharePoint folders above.
2. Prefer Markdown in SharePoint for Amy-readable SOPs.
3. When a note is migrated, add at the top: `SharePoint: /sites/RSG-Knowledge/...`
4. Vault can remain for drafting; **SharePoint is the published knowledge SOR** for Amy.

Suggested mapping:

| Vault area | SharePoint folder |
|---|---|
| Procedures / SOPs | `01-operations/` |
| Carrier notes | `04-carriers/` |
| PL guides | `02-personal-lines/` |
| CL guides | `03-commercial-lines/` |
| Templates | `07-templates/` |

---

## Checklist — “done” definition

- [x] Site inventory / Obsidian migration complete — see [`sharepoint-migration-status.md`](sharepoint-migration-status.md)
- [x] Single site **RSG-Knowledge** exists with folder tree
- [x] `site-index.md` and `migration-log.md` in `00-meta/` (verify live with SharePoint MCP)
- [x] All keepers migrated; duplicates merged
- [ ] Old sites read-only or deleted (optional `list_sites` audit)
- [ ] `SHAREPOINT_SITE_URL` set on hermes-gretch and in Cursor MCP
- [ ] Copilot Studio grounded on this site only — Track A0 in [`amy-copilot-tool-wiring.md`](amy-copilot-tool-wiring.md)
- [ ] SharePoint MCP connector + egress — Tracks A1–A8
- [ ] Amy smoke test: “How do we process a COI?” returns answer from new paths

---

## Need the site list?

With SharePoint MCP configured:

```text
list_sites query="RSG"
list_sites query="training"
get_site_info site_url="https://tenant.sharepoint.com/sites/Old-Site-Name"
list_folder path="/"
search_knowledge query="renewal playbook"
```

Share the inventory table with Operations and we can assign merge targets per source site.
