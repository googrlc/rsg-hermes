# Deploy: hermes-gretch (Gretchen's Hermes instance)

Gretchen's Hermes is the **same image** as Lamar's, run as a **second container**
from this one repo. Nothing in the code is forked — the instance is defined
entirely by environment. This runbook is the deploy + verification procedure.

| Knob | Lamar's instance | hermes-gretch |
|---|---|---|
| `HERMES_AGENT_ID` | `hermes-lamar` | `hermes-gretch` (set in compose) |
| `HERMES_PERSONA_FILE` | _(unset → default Hermes)_ | `/app/personas/SOUL-GRETCHEN.md` |
| `HERMES_MEMORY_SCOPE` | `hermes-lamar` | `hermes-gretch` (isolated Supermemory) |
| `HERMES_DISABLED_TOOLS` | _(none)_ | `web_research` — CRM-only, no public-web research |
| Slack app | Hermes app | dedicated **hermes-gretch** app + tokens |
| NowCerts | (gated queue) | **none** — no `NOWCERTS_*`, no mcp/nowcerts mount |

---

## 0. Prerequisites (do these once, in the dashboards — cannot be scripted from here)

1. **Slack app `hermes-gretch`** — create at api.slack.com/apps. **Outbound
   posting only** — the inbound Socket Mode listener is retired, so no
   App-Level (`xapp-`) token and no `connections:write`.
   - Bot scope: `chat:write`.
   - Install to workspace → copy **Bot User OAuth token** (`xoxb-…`).
   - Invite the bot to **#gretchen-tasks** (`C0AMWAZBBJP`).
2. **Supermemory** — reuse the existing `SUPERMEMORY_API_KEY`. Isolation is by
   **scope tag**, not by account: the compose sets `HERMES_MEMORY_SCOPE=hermes-gretch`,
   so every memory this instance writes is tagged `scope:hermes-gretch` and reads are
   constrained to it. No bleed into Lamar's `scope:hermes-lamar`, and vice versa.
   **Medicare PHI rule (3c):** memory for Medicare-lane interactions stores client
   name + CRM link + task context ONLY. Enforced in `hermes/core/phi.py` —
   `build_medicare_memory()` is an allowlist, and `add_document` redacts MBI / SSN /
   eligibility detail from any Medicare-tagged write as a backstop.
3. **Supabase migration** — apply once (adds the `agent_id` column the writes stamp):
   `supabase/migrations/20260611120000_agent_id_stamping.sql`.

## 1. Apply the agent_id migration

```bash
# via Supabase CLI (or paste the SQL in the dashboard SQL editor)
supabase db push
# verify
#   select table_name from information_schema.columns
#   where column_name='agent_id'
#     and table_name in ('agency_intake_drafts','cc_submissions');
```

## 2. Create the env file

```bash
cp deploy/hermes-gretch/.env.hermes-gretch.example .env.hermes-gretch
# fill every <...> with hermes-gretch-DEDICATED values (Slack tokens, Supabase, Supermemory)
```

`.env.hermes-gretch` is gitignored — it never gets committed.

### 2a. Set SERVICE_WEBHOOK_SECRET (required for /renewals/complete)

The `SERVICE_WEBHOOK_SECRET` must be set and must match the value configured in
whatever calls `/renewals/complete`. If it is missing or blank, every incoming
service webhook (renewal task completions, etc.) will be
silently rejected with 401 — no worksheet filed, no Slack win/loss post.

```bash
# 1. Generate a strong secret (do this once; store the result in 1Password).
openssl rand -hex 32

# 2. Paste the output into .env (the main shared env, loaded by hermes-api):
#    SERVICE_WEBHOOK_SECRET=<the hex string>

# 3. Set the same value in the caller that posts to /renewals/complete.

# 4. Recreate hermes-api so the new env is loaded
#    (docker compose restart does NOT reload env_file — use up -d):
docker compose up -d hermes-api
```

> **Verify it is live:**
> ```bash
> docker exec rsg-hermes-api printenv SERVICE_WEBHOOK_SECRET
> # must print a non-empty hex string
> ```

## 3. Build & start the container

```bash
docker compose -f deploy/hermes-gretch/docker-compose.yml up -d --build
docker logs -f rsg-hermes-gretch
```

> ⚠️ This instance existed to run the Slack Socket Mode listener, which is
> retired. Its compose command is now a harmless `--ops-doctor` read, so
> starting it does nothing useful — decide what this instance is *for* before
> enabling it. The persona and memory-scope isolation below still work; it just
> has no inbound surface to serve.

It builds the same `Dockerfile` as the main stack;
`personas/SOUL-GRETCHEN.md` is baked into the image at `/app/personas/`.

---

## 4. Verification (the deploy isn't done until all three pass)

### 4a. Persona check — in **#gretchen-tasks**
DM the bot or @mention it: **"who are you and who do you help?"**
- ✅ PASS: it answers as Gretchen's assistant, by name, in plain English, no jargon.
- ❌ FAIL: it says "Lamar" / talks like the owner's chief of staff →
  `HERMES_PERSONA_FILE` isn't being read. Check the path resolves inside the
  container: `docker exec rsg-hermes-gretch cat /app/personas/SOUL-GRETCHEN.md`.

### 4b. Write-confirmation test
Ask it to change something (e.g. **"update a client's phone number"**) *without*
confirming.
- ✅ PASS: it describes what it WOULD do and asks for your go-ahead — does not write.
- ❌ FAIL: it writes immediately → confirmation guard regressed.

Then confirm a small, safe write and let it run.

### 4c. CRM receipt shows hermes-gretch

This used to read `agent_id` off `crm_write_queue`, which was dropped in the
EspoCRM decommission. Attribution still works — check whichever table the write
landed in:

```sql
select agent_id, status, created_at from agency_intake_drafts
order by created_at desc limit 5;
-- or, for a Command Center submission:
select agent_id, status, created_at from cc_submissions
order by created_at desc limit 5;
```

- ✅ PASS: the new row's `agent_id` = `hermes-gretch`.

Note the gap: `outbound_sync_queue` — the queue that replaced `crm_write_queue`
for AMS-bound writes — has **no** `agent_id` column, so writes that go straight
to the queue are not attributable. Only the two tables above are stamped
(`hermes/command_center/store.py`, `hermes/commands/agency_intake.py`).

Memory isolation spot-check (optional):
```
# a doc written by this instance carries scope:hermes-gretch and is absent from
# Lamar's scope:hermes-lamar listing.
```

---

## 5. Rollback

```bash
docker compose -f deploy/hermes-gretch/docker-compose.yml down   # stop + remove the container
```
- Lamar's instance is a separate container and is **unaffected** by anything here.
- The `agent_id` columns are additive with a default — leaving them in place is
  harmless. To fully revert the schema (rarely needed):
  `alter table agency_intake_drafts drop column agent_id;` (repeat for
  `cc_submissions`; `crm_write_queue` no longer exists).
- Revoke the hermes-gretch Slack tokens in their dashboards if
  decommissioning for good.
