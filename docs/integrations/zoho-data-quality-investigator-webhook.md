# Zoho CRM → Cursor Data Quality Investigator (webhook)

Fire the **RSG Data Quality Investigator** Cursor automation when someone
requests a cross-system policy check from Zoho CRM.

**Flow:**

```text
Zoho CRM (Policies or Renewals record)
  → Workflow rule (button or field change)
  → Deluge function (invokeurl)
  → Cursor automation webhook
  → Agent run at cursor.com/agents (read-only report)
```

Hermes is **not** in the path. Zoho talks directly to Cursor.

---

## Part 1 — Cursor automation (webhook trigger)

1. Open [cursor.com/automations](https://cursor.com/automations) → **RSG Data Quality Investigator** → **Edit**.
2. **Trigger:** add or switch to **Webhook** (you can keep manual runs too).
3. **Save** the automation.
4. Copy from the trigger panel:
   - **Webhook URL** (e.g. `https://api2.cursor.sh/automations/webhook/...`)
   - **API key** (Generate if needed) — used as `Authorization: Bearer <key>`
5. Confirm **Tools → MCP:** Supabase + Hermes are enabled.

The webhook body is passed to the agent prompt as-is. Send JSON:

```json
{
  "policy_number": "990414352",
  "client_name": "Steven Prak",
  "line_of_business": "Personal Auto",
  "source": "zoho_renewals",
  "zoho_record_id": "1234567890123456789"
}
```

Only `policy_number` is required.

**Smoke test** (Mac terminal, after exporting secrets):

```bash
export CURSOR_AUTOMATION_WEBHOOK_URL="https://..."
export CURSOR_AUTOMATION_WEBHOOK_KEY="crsr_..."
./scripts/trigger_policy_investigation.sh 990414352 "Steven Prak" "Personal Auto"
```

Open the new run at [cursor.com/agents](https://cursor.com/agents).

---

## Part 2 — Store secrets in Zoho (CRM Variables)

Do **not** hard-code the Cursor API key in Deluge source.

1. Zoho CRM → **Setup** (gear) → **Developer Space** → **CRM Variables**.
2. Create two variables (org-level, **visible** to admins only):

| Variable API name | Example value | Notes |
|---|---|---|
| `cursor_dqi_webhook_url` | `https://api2.cursor.sh/automations/webhook/...` | Full webhook URL from Cursor |
| `cursor_dqi_webhook_key` | `crsr_...` | Bearer token only — no `Bearer ` prefix |

If your org uses a different naming convention, update the Deluge file accordingly.

---

## Part 3 — Deluge function

1. **Setup** → **Developer Space** → **Functions** → **New Function**.
2. **Display name:** `trigger_cursor_policy_investigation`
3. **Category:** `automation` (or any category — note the full name for the workflow step)
4. **Arguments** (add each as **string**):

| Argument | Required | Workflow merge field (Renewals) |
|---|---|---|
| `policyNumber` | Yes | `${Renewals.Policy_Number}` |
| `clientName` | No | `${Renewals.Client_Name}` |
| `lineOfBusiness` | No | `${Renewals.Line_of_Business}` |
| `recordId` | No | `${Renewals.id}` |
| `sourceModule` | No | literal `Renewals` |

5. Paste the function body from [`docs/zoho/deluge/trigger_policy_investigation.deluge`](../zoho/deluge/trigger_policy_investigation.deluge) (or paste the whole file if your org expects the `string automation....` wrapper).
6. Save.

The function POSTs JSON to Cursor with `Authorization: Bearer <key>`.

---

## Part 4 — Workflow rule (recommended: button on Renewals)

Start with a **manual button** so investigations are not fired on every save.

### Option A — Renewals module (Project 85 desk)

Best when ops works from the renewal worklist.

1. **Setup** → **Automation** → **Workflow Rules** → **Create Rule**.
2. **Module:** `Renewals` (custom module API name may differ — check **Setup → APIs → Modules**).
3. **Rule name:** `Cursor DQ Investigation`
4. **When:** **Record action** → **Button** (or **On a record action** if your org uses Blueprint/custom button).
   - Button label: `Investigate data quality`
5. **Condition (optional):** `Policy_Number` is not empty.
6. **Instant action:** **Function** → `automation.trigger_cursor_policy_investigation` (name matches your category).
   - Map arguments from the table in Part 3.
7. Save and activate.

Add the button to the Renewals layout: **Setup** → **Customization** → **Modules and Fields** → **Renewals** → **Layouts** → drag the workflow button onto the layout.

### Option B — Policies module

Same pattern on **Policies** when the mismatch is spotted on the policy record.
The Deluge script falls back to the related Account name when `Client_Name` is empty.

### Option C — Field-change trigger (use carefully)

Trigger when a flag field is checked, e.g. custom checkbox `Request_DQ_Check__c`:

- **When:** Create or Edit
- **Condition:** `Request_DQ_Check__c` equals `true`
- **Function:** `Trigger Cursor Policy Investigation`
- **Follow-up:** optional second workflow or Deluge line to uncheck the box after send (avoids repeat fires on every edit).

Avoid wiring to every `Policy_Status` change — that will spam Cursor runs.

---

## Part 5 — Field mapping (Zoho → Cursor)

| Cursor JSON key | Zoho Renewals | Zoho Policies |
|---|---|---|
| `policy_number` | `Policy_Number` | `Policy_Number` |
| `client_name` | `Client_Name` | `Account_Name` (lookup → name) |
| `line_of_business` | `Line_of_Business` | `Line_of_Business` |

API names must match your org. See `docs/zoho/fields_renewals.csv` and `fields_policies.csv`.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Nothing in Cursor | Missing `Authorization: Bearer` | Use Deluge `invokeurl` with headers — native Zoho webhook UI often cannot set Bearer |
| `401` from Cursor | Wrong or expired webhook key | Regenerate key in Cursor; update CRM variable |
| Zoho function error | CRM variables unset | Fill `cursor_dqi_webhook_url` and `cursor_dqi_webhook_key` |
| Agent run but no data | MCP not connected on automation | Cursor Settings → MCP → Supabase + Hermes |
| Duplicate runs | Save-triggered workflow | Switch to button trigger or one-shot checkbox |

**Deluge debug:** add `info response;` at the end of the function and check **Setup → Developer Space → Logs** after a test click.

---

## Security

- Cursor webhook key = org secret. Store in CRM Variables, not in git.
- Rotate the key if it was ever pasted in chat or email.
- The investigator agent is **read-only**; Zoho cannot auto-run book sync or AMS writes through this webhook.

---

## Related

- `.cursor/automations/README.md` — automation setup
- `scripts/trigger_policy_investigation.sh` — same payload from shell
- `.claude/skills/data-quality-investigator/SKILL.md` — agent SOP
