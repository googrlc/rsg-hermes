# Slack `#crm-entry` → n8n → Hermes webhook

> ## ⚠️ HISTORICAL — this transport was never deployed (as of 2026-07-26)
>
> **n8n does not exist in this stack.** It was removed from
> `docker-compose.yml` during the LiteLLM consolidation (see
> `litellm-consolidation-guide.md`, "Remove n8n from the running stack"), and
> there is no `rsg-n8n` container on the box. Every webhook path below is dead.
>
> **What runs today:** the Socket Mode listener is the only Slack intake path,
> and inbound work is staged through `intake_submissions` →
> `approve_draft` → the intake worker. Outbound writes go through
> `outbound_sync_queue`, drained by the scheduler every 5 minutes.
>
> Kept for the dedupe-by-message-`ts` design, which the Socket Mode path still
> follows. Do not build against the n8n half.

Second transport for Slack-driven CRM intake. Lives alongside the existing
Socket Mode listener; both can deliver the same `#crm-entry` message and
Hermes dedupes by message `ts` so the CRM only gets written once.

```
Slack #crm-entry post
   ↓ (Slack Events API)
n8n "Hermes Trigger" workflow
   ↓ (filter: contains "Hermes:" block)
HTTP POST → /api/hermes/slack/crm-entry
   ↓
Hermes verifies signature, parses block, writes to the CRM, posts ack
```

## Why a second Slack app?

A single Slack app cannot use both Socket Mode and Events API Request URL —
enabling one disables the other. To run both transports in parallel:

- **App #1 (existing, `rsg_hermes`)** — Socket Mode. Powers DMs, @mentions,
  sentinel buttons, and the original `on_crm_entry_message` handler.
- **App #2 (new)** — Events API. Subscribed to `message.channels` in
  `#crm-entry`. Slack POSTs events to n8n, which forwards to Hermes.

Both apps are installed in the same workspace. Both see the same
`#crm-entry` message events. Hermes' sqlite dedupe at
`${HERMES_STATE_DIR:-/app/state}/slack_events.sqlite` keyed by
`crm_entry_ts:<ts>` makes sure only the first arrival dispatches.

## Slack app #2 setup

1. Create a new app at <https://api.slack.com/apps> — "From scratch" — install
   to the RSG workspace.
2. **OAuth & Permissions** → bot token scopes: `channels:history`,
   `groups:history` (if `#crm-entry` is private), `chat:write` (only needed
   if you ever want this app to post; in our design only app #1 posts).
3. **Event Subscriptions** → enable, set Request URL to your n8n webhook URL
   (see below). Subscribe to bot events: `message.channels` (and
   `message.groups` if `#crm-entry` is private).
4. **Install to Workspace**, then add the new bot user to `#crm-entry`.
5. Copy the app's **Signing Secret** (Basic Information page) into
   `.env` on the Hermes host:

   ```
   SLACK_EVENTS_SIGNING_SECRET=<signing secret of app #2>
   ```

   Restart `rsg-hermes-api` so it picks up the env var.

## n8n "Hermes Trigger" workflow

One workflow with two nodes after the webhook trigger.

### 1. Webhook trigger
- HTTP method: `POST`
- Path: `hermes-slack-events` (the public URL becomes the Slack Request URL)
- Respond: **Immediately** (we forward to Hermes; don't make Slack wait)
- Authentication: None (Slack auth is via signature, verified by Hermes)

### 2. IF node (optional pre-filter)
- Condition: `{{ $json.body.event.text }}` contains `Hermes:` AND contains
  `MODULE:`
- True branch → HTTP Request node below. False branch → ignored.

Skipping this node is fine — Hermes also filters server-side. But filtering
in n8n cuts API noise.

### 3. HTTP Request node — forward to Hermes
- **Method:** POST
- **URL:** `http://rsg-hermes-api:8787/api/hermes/slack/crm-entry`
  (uses the `hermes-shared` docker network; n8n is on the same network).
- **Send Body:** **Raw** — body type `JSON`, content
  `{{ $binary.data || $json.body }}`. **Critical:** the body must be the
  exact bytes Slack signed. Any JSON re-serialization by n8n will break the
  signature.
- **Send Headers:** explicit passthrough of three Slack headers:

  | Name | Value |
  |---|---|
  | `Content-Type` | `application/json` |
  | `X-Slack-Signature` | `{{$json.headers["x-slack-signature"]}}` |
  | `X-Slack-Request-Timestamp` | `{{$json.headers["x-slack-request-timestamp"]}}` |

- **Response:** ignore. Hermes returns 200 immediately and dispatches in a
  background task.

### Initial Slack URL verification

Slack will POST `{"type":"url_verification","challenge":"..."}` once when
you configure the Request URL. The n8n workflow must forward this through;
Hermes returns the `challenge` value as plain text. After that you can
remove the IF pre-filter for the verification leg if you want.

## How dedupe works in practice

| Scenario | Behavior |
|---|---|
| Same message delivered to app #1 (Socket) and app #2 (Events API) | First arrival claims `crm_entry_ts:<ts>` in sqlite; second arrival sees `ignored="cross-transport-duplicate"` (webhook) or skips silently (socket). |
| Slack retries the Events API POST after a timeout | Webhook claims `slack_event_id:<event_id>`; retry returns `ignored="slack-retry"` without invoking the dispatcher again. |
| n8n misfires and sends the same payload twice | Same as above — second one is `slack-retry`. |
| Hermes posts its own ack into `#crm-entry` | Both apps see the event with `bot_id` set; both transports filter on `bot_id` and ignore. |

## Verifying the deploy

1. `curl -s -X POST http://localhost:8788/health` (or external URL) — confirm
   API is up.
2. From the Slack Events page, click **Retry** on the URL verification — the
   request URL should turn green.
3. Post a test block in `#crm-entry`:
   ```
   Hermes:
   MODULE: intake
   account: Acme Test
   ```
   Expect: one CRM Account create and one ack reply (not two of either).
4. Tail logs: `docker compose logs -f hermes-api` should show
   `claim_event` decisions and dispatcher output.

## Rolling back

To temporarily disable the webhook path without disabling the second Slack
app: unset `SLACK_EVENTS_SIGNING_SECRET` and restart `rsg-hermes-api`. The
endpoint returns 503 and Socket Mode keeps serving `#crm-entry` alone.
