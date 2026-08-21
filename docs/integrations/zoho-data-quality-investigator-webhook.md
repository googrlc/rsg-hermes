# Zoho CRM → Cursor Data Quality Investigator (webhook)

Fire the **RSG Data Quality Investigator** Cursor automation from a Zoho CRM
**Renewals** record button.

**Recommended flow (Hermes relay):**

```text
Zoho CRM button
  → POST /api/webhooks/zoho/dqi-investigation  (Hermes, SERVICE_WEBHOOK_SECRET)
  → Hermes loads Renewal from Zoho API, builds JSON
  → Cursor automation webhook (secrets on Hermes only)
  → Agent run at cursor.com/agents
```

Zoho never stores the Cursor API key — only Hermes URL + shared webhook secret.

---

## Part 1 — Hermes server (`hermes-gretch`)

### 1. Environment (`/opt/app/.env`)

```bash
# Shared secret Zoho sends as Bearer token
SERVICE_WEBHOOK_SECRET=<generate-a-long-random-string>

# Cursor automation webhook (from cursor.com/automations → trigger panel)
CURSOR_AUTOMATION_WEBHOOK_URL=https://api2.cursor.sh/automations/webhook/...
CURSOR_AUTOMATION_WEBHOOK_KEY=crsr_...

# Already present for Zoho record lookup
ZOHO_CLIENT_ID=...
ZOHO_CLIENT_SECRET=...
ZOHO_REFRESH_TOKEN=...
```

Restart API after editing:

```bash
docker compose up -d rsg-hermes-api
```

### 2. Expose Hermes to the internet (Zoho cloud must reach it)

Zoho Deluge runs on Zoho servers — Tailscale-only URLs will **not** work.

On **hermes-gretch**:

```bash
tailscale funnel --bg 8444
```

Public base URL (example): `https://hermes-gretch.tail1cbc83.ts.net:8444`

Verify:

```bash
curl -s https://hermes-gretch.tail1cbc83.ts.net:8444/health
```

### 3. Smoke test the relay

From the box (or Mac with secret):

```bash
export SERVICE_WEBHOOK_SECRET='...'
python scripts/zoho_setup_dqi_integration.py --smoke-hermes https://hermes-gretch.tail1cbc83.ts.net:8444
```

Or:

```bash
curl -s -X POST \
  -H "Authorization: Bearer $SERVICE_WEBHOOK_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"policy_number":"990414352","client_name":"Steven Prak"}' \
  https://hermes-gretch.tail1cbc83.ts.net:8444/api/webhooks/zoho/dqi-investigation
```

Expect `{"ok":true,"policy_number":"990414352",...}` and a new run at [cursor.com/agents](https://cursor.com/agents).

Run the setup helper:

```bash
python scripts/zoho_setup_dqi_integration.py
python scripts/zoho_setup_dqi_integration.py --check-zoho   # when ZOHO_* creds valid
```

---

## Part 2 — Cursor automation

1. [cursor.com/automations](https://cursor.com/automations) → **RSG Data Quality Investigator** → **Edit**
2. Enable **Webhook** trigger → **Save** → copy URL + key into Hermes `.env` above
3. **Tools → MCP:** Supabase + Hermes
4. Prompt: copy `## Instructions` from `.cursor/automations/data-quality-investigator.md`

---

## Part 3 — Zoho CRM variables (Text type)

**Setup → Developer Space → CRM Variables**

| API Name | Value |
|---|---|
| `hermes_dqi_webhook_base` | `https://hermes-gretch.tail1cbc83.ts.net:8444` (no `/api/...` suffix) |
| `hermes_dqi_webhook_secret` | Same string as `SERVICE_WEBHOOK_SECRET` on Hermes |

Do **not** use Select/picklist type — use **Text**.

---

## Part 4 — Deluge function

**Setup → Developer Space → Functions**

| Field | Value |
|---|---|
| Display name | `Trigger Cursor Policy Investigation` |
| Function name | `trigger_cursor_policy_investigation` |
| Category | `automation` |
| Argument | `renewalId` (string) |

Paste full function from [`docs/zoho/deluge/trigger_policy_investigation.deluge`](../zoho/deluge/trigger_policy_investigation.deluge).

---

## Part 5 — Custom button (Renewals)

**Setup → Customization → Modules and Fields → Renewals → Links and Buttons**

| Field | Value |
|---|---|
| Button name | `Policy verification` |
| Action | Function → `automation.trigger_cursor_policy_investigation` |
| Page | In Record |
| Argument `renewalId` | **Record Id** from merge-field picker (do not type `${Renewals...}`) |
| Profiles | Ops / admin profiles that may investigate |

Add button to Renewals layout.

---

## API reference (Hermes relay)

`POST /api/webhooks/zoho/dqi-investigation`

| Header | Value |
|---|---|
| `Authorization` | `Bearer <SERVICE_WEBHOOK_SECRET>` |
| `Content-Type` | `application/json` |

| JSON field | Purpose |
|---|---|
| `renewal_id` | **Preferred** — Hermes loads Policy_Number from Zoho Renewals |
| `policy_id` | Load from Policies module instead |
| `policy_number` | Direct trigger when record id not passed |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Policy shows as `${Renewals.Policy_Number}` | Map **Record Id** only; Hermes loads fields |
| `No number after minus sign in JSON` | Old Deluge posted invalid JSON to Cursor — use Hermes relay script |
| `401` from Hermes | `hermes_dqi_webhook_secret` ≠ `SERVICE_WEBHOOK_SECRET` |
| `502` Cursor webhook | Check `CURSOR_AUTOMATION_*` on Hermes + webhook enabled in Cursor |
| `503` SERVICE_WEBHOOK_SECRET | Set env on Hermes and recreate container |
| Zoho can't connect | Run `tailscale funnel 8444`; Zoho can't use Tailscale-only URLs |

---

## Alternate: Zoho → Cursor direct

If you prefer not to expose Hermes publicly, Zoho can POST directly to Cursor with
`cursor_dqi_webhook_url` + `cursor_dqi_webhook_key` CRM variables. See git history
for the one-arg Deluge version. Hermes relay is recommended so Cursor secrets stay
on the box.

---

## Related

- `.cursor/automations/README.md`
- `scripts/trigger_policy_investigation.sh` — shell trigger (same Cursor payload)
- `.claude/skills/data-quality-investigator/SKILL.md`
