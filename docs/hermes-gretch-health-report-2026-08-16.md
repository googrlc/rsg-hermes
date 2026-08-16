# hermes-gretch health report — 2026-08-16

Run context: Cloud agent with **Supabase MCP** (live `rsg-infrastructure` project).
**Box SSH** from this environment was not available — API/MCP bridge checks on
hermes-gretch must be run on the box or after egress is live.

Checklist: [`hermes-gretch-health-checklist.md`](hermes-gretch-health-checklist.md)

---

## Supabase (`wibscqhkvpijzqbhjphg`) — OK

| Check | Result |
|---|---|
| Project status | ACTIVE_HEALTHY |
| API URL | Supabase project `rsg-infrastructure` (dashboard) |

### Book mirror

| Table | Rows |
|---|---|
| `canonical_clients` | 364 |
| `canonical_policies` | 456 |
| `commission_ledger` | 123 |

### Agency memory (thin)

| Table | Rows |
|---|---|
| `client_facts` | 11 |
| `client_notes` | 2 |
| `quote_facts` | 0 |
| `intake_submissions` | 1 |

→ Run `python scripts/backfill_agency_memory.py --dry-run` on the box when convenient.

### Ops tables (`--ops-doctor` probe set)

| Table | Rows |
|---|---|
| `hermes_ai_roles` | 4 |
| `commission_audits` | 3 |
| `eom_scorecards` | 1 |
| `project_85_renewals` | 46 |
| `renewal_actions` | 5 |
| `guardrail_logs` | 564 |
| `reporting_schedules` | 3 |
| `dashboard_kpis` | 347 |

### Operators (`agency_crm_users`)

| Email | Role |
|---|---|
| Lamar (administrator) | `lamar` @ agency `.net` domain |
| Gretchen (csr) | `gretchen` @ agency `.net` domain |
| RSG Service (machine) | `lc-rsg` @ agency `.net` domain |

### Outbound AMS queue — needs attention

| Status | Count | Notes |
|---|---|---|
| `dead` | 4 | Latest update 2026-07-26 |
| `completed` | 1 | Last success 2026-07-16 |

No recent `completed` queue rows — scheduler/executor or approvals may be idle.
On the box: `curl -H "Authorization: Bearer $HERMES_API_TOKEN" \
http://127.0.0.1:8787/api/hermes/sync-health` and review dead-letter rows.

---

## hermes-gretch API / MCP — not verified from cloud

| Check | Status |
|---|---|
| `GET :8787/health` on box | **Not run** (no SSH/tailnet from cloud VM) |
| `GET :8081/healthz` on box | **Not run** |
| MCP `ping` / `tools/list` | **Not run** |
| Public egress smoke | **Not live** — DNS/nginx pending |

---

## Egress lock (Copilot)

| Item | Value |
|---|---|
| Hostname | `hermes-mcp.risksolutionsgroup.net` |
| DNS target | A → `152.53.201.154` (Wix) |
| Copilot connector URL | `https://hermes-mcp.risksolutionsgroup.net/mcp` |
| Box install | `sudo ./scripts/install_mcp_egress.sh` |

---

## Recommended next steps on the box

```bash
cd /opt/rsg-hermes && git pull
source .venv/bin/activate
./scripts/hermes_gretch_health.sh
hermes --ops-doctor
```

After Wix DNS + TLS:

```bash
sudo ./scripts/install_mcp_egress.sh
API_SERVER_KEY=... MCP_URL=https://hermes-mcp.risksolutionsgroup.net \
  ./scripts/mcp_smoke_test.sh
```
