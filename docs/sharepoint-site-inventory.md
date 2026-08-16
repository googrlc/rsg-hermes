# SharePoint site inventory

> **Status:** Not generated yet — run inventory **before** creating **RSG-Knowledge**.
>
> This file is overwritten by `scripts/sharepoint_site_inventory.py`. Until `MS365_*`
> credentials are set, fill the manual table below or run the script from a machine
> with Entra app access.

See [`sharepoint-knowledge-consolidation.md`](sharepoint-knowledge-consolidation.md) for the
full workflow (inventory → approve map → build site → migrate → retire old sites).

---

## How to generate (automated)

```bash
source .venv/bin/activate
python scripts/sharepoint_site_inventory.py --deep
python scripts/sharepoint_site_inventory.py --query RSG --deep
python scripts/sharepoint_site_inventory.py --query training --deep
```

Requires `MS365_TENANT_ID`, `MS365_CLIENT_ID`, `MS365_CLIENT_SECRET` (same as SharePoint MCP).

Optional JSON dump:

```bash
python scripts/sharepoint_site_inventory.py --deep --json docs/sharepoint-site-inventory.json
```

---

## Manual capture (until script runs)

Use this if credentials are not ready. One row per **site** that might hold agency knowledge.

| Display name | URL | Libraries / root folders | Topic / owner | Decision | Target folder in RSG-Knowledge |
|---|---|---|---|---|---|
| *(example) Old Training* | `https://tenant.sharepoint.com/sites/Old-Training` | Documents/SOPs | PL quoting | merge | `02-personal-lines/reference/` |
| | | | | TBD | TBD |

### Decision key

| Decision | Meaning |
|---|---|
| **keep** | Already canonical; may become RSG-Knowledge itself |
| **merge** | Copy content into RSG-Knowledge, then stub/archive source |
| **archive** | Copy to `99-archive/YYYY-MM-site-name/`, then read-only source |
| **delete** | Empty or superseded; archive zip only if required |
| **exclude** | Not agency knowledge (project site, client site, Teams junk) |

---

## Approval gate

Do **not** create the RSG-Knowledge site or folder tree until:

1. Every candidate site is listed above (automated or manual).
2. Operations assigns **Decision** and **Target folder** for each row.
3. Duplicate topics are noted for merge during Phase C migration.

After approval → Phase B in [`sharepoint-knowledge-consolidation.md`](sharepoint-knowledge-consolidation.md).
