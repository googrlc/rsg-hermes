# Microsoft 365 email triage

Hermes reads a 365 mailbox via Microsoft Graph, turns actionable insurance mail
into CRM intakes, and sweeps newsletters/noise into a quarantine folder you
review and delete. It runs **unattended** with app-only auth — no interactive
login, no per-user refresh tokens.

```
Inbox ──► email_triage ──► classify each message
                              ├─ actionable ─► intake_submissions (status=received)
                              │                    └─► intake worker: synthesize → draft
                              │                        → awaiting_approval → Contact/Opportunity/Task
                              └─ noise ───────► move to "Hermes Triage" folder (never deleted)
```

Triage performs **no CRM writes itself** — it only inserts `intake_submissions`
rows and lets the existing intake worker / approval gate do the writing.

## One-time admin setup (Microsoft Entra)

1. **Entra ID → App registrations → New registration.** Name it e.g.
   `hermes-mail-triage`. Single tenant.
2. **API permissions → Add → Microsoft Graph → Application permissions** →
   add `Mail.ReadWrite` (and `Mail.Read`). Then **Grant admin consent**.
3. **Certificates & secrets → New client secret.** Copy the secret *value*.
4. Collect: **Directory (tenant) ID**, **Application (client) ID**, the
   **secret value**.

> `Mail.ReadWrite` as an *application* permission grants tenant-wide mailbox
> access. To limit Hermes to specific mailboxes, add an Exchange
> **Application Access Policy** scoped to a mail-enabled security group that
> contains only the triaged mailboxes (`New-ApplicationAccessPolicy` in
> Exchange Online PowerShell).

## Configure

In `.env`:

```bash
MS365_TENANT_ID=...
MS365_CLIENT_ID=...
MS365_CLIENT_SECRET=...
MS365_MAILBOXES=lamar@risk-solutionsgroup.com   # comma-separated for several
HERMES_OPENAI_API_KEY=...                        # enables the LLM classifier stage
```

## Run

```bash
# Dry run first — classifies and logs intended actions, writes/moves nothing.
hermes --email-triage-dry-run --email-since-hours 48

# Go live once the dry-run classifications look right.
hermes --email-triage
```

- **Dry run** is the safe default for validating classifier accuracy.
- **Idempotency:** every processed message is tagged with the `Hermes Triaged`
  category and skipped on the next poll; actionable mail also dedupes downstream
  on `internetMessageId`.
- **Quarantine, not trash:** noise moves to the `Hermes Triage` folder
  (auto-created). Nothing is ever deleted — you empty it periodically.

## Classifier

`hermes/sync/email_classifier.py` decides actionable vs noise:

1. Heuristic gate catches obvious bulk/no-reply/marketing mail for free.
2. Anything else goes to the OpenAI model (`HERMES_OPENAI_MODEL`), which also
   guesses `intake_kind` (commercial/personal/life/benefits/medicare/unknown).
3. With no API key (or on a model outage) it defaults to **actionable/unknown**
   — it fails toward human review, never auto-quarantines.

Tune the heuristics and prompt there as you observe real inbox traffic.

## Scheduling

On the server (`/opt/rsg-hermes`, Docker Compose deploy) triage runs from cron
via `docker compose run --rm hermes`, matching the revenue-sentinel job. A
2-hour lookback with a 30-minute cadence guarantees no gaps even if a run is
missed; already-processed messages are skipped via the `Hermes Triaged`
category, so the overlap is idempotent.

```cron
*/30 * * * * cd /opt/rsg-hermes && docker compose run --rm hermes hermes --email-triage --email-provider ms365 --email-since-hours 2 >> /var/log/hermes-email-triage.log 2>&1
```

For a local/venv install the command is simply `hermes --email-triage
--email-provider ms365 …`.

