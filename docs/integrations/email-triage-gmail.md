# Gmail email triage

The Gmail lane mirrors the [Microsoft 365 lane](email-triage-365.md): it reads a
Workspace mailbox, turns actionable insurance mail into CRM intakes, and sweeps
noise into a quarantine label. It runs **unattended** with a service account
using domain-wide delegation — no interactive OAuth, no per-user refresh tokens.

Gmail has no folders, so the quarantine "folder" is a **label** (`Hermes/Triage`)
and moving a message means *adding that label and removing `INBOX`*. Processed
messages are tagged `Hermes/Triaged` and excluded from the next poll.

```
Inbox ──► email_triage ──► classify each message
                              ├─ actionable ─► intake_submissions (status=received)
                              │                    └─► intake worker → Contact/Opportunity/Task (after approval)
                              └─ noise ───────► +Hermes/Triage  −INBOX  (never deleted)
```

## One-time admin setup (Google Cloud + Workspace)

1. **Google Cloud Console → create/select a project → enable the Gmail API.**
2. **Create a service account** → **Keys → Add key → JSON.** Download the key
   file. Note the service account's **client ID** (a long number).
3. **Workspace Admin console → Security → Access and data control →
   API controls → Domain-wide delegation → Add new.**
   - Client ID: the service account's client ID
   - OAuth scopes: `https://www.googleapis.com/auth/gmail.modify`
4. Place the JSON key where the Hermes container can read it (e.g. a Docker
   secret) and point `GMAIL_SA_KEY_PATH` at it.

> `gmail.modify` grants read + label/move, **not** send. Domain-wide delegation
> lets the service account impersonate any mailbox in the domain; scope it to
> just the addresses you list in `GMAIL_MAILBOXES`.

## Install + configure

```bash
pip install -e '.[gmail]'     # adds google-auth (token minting only)
```

In `.env`:

```bash
GMAIL_SA_KEY_PATH=/run/secrets/gmail-sa.json
GMAIL_MAILBOXES=lamar@risk-solutionsgroup.com   # comma-separated for several
HERMES_OPENAI_API_KEY=...                        # enables the LLM classifier stage
```

## Run

```bash
# Dry run first — classifies and logs intended actions, writes/moves nothing.
hermes --email-triage-dry-run --email-provider gmail --email-since-hours 48

# Go live once the classifications look right.
hermes --email-triage --email-provider gmail
```

Idempotency, quarantine-not-trash, and the shared classifier all behave exactly
as documented for the [365 lane](email-triage-365.md#classifier) — both
providers feed the same `intake_submissions` pipeline.

## Scheduling

```cron
*/30 * * * * cd /path/to/rsg-hermes && hermes --email-triage --email-provider gmail >> /var/log/hermes-email-triage.log 2>&1
```
