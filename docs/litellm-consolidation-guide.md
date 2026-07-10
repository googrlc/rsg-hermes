# LiteLLM Consolidation — Setup & Deploy Guide

## What changed

All Hermes LLM calls now route through a single gateway: the LiteLLM proxy at
`litellm-qidsf-u69864.vm.elestio.app`. No code path calls a provider directly.

### Files changed (code)

| File | Change |
|------|--------|
| `hermes/core/llm_client.py` | **NEW** — shared `get_client()` / `resolve_model()` helper |
| `hermes/core/nl_agent.py` | Replaced direct `OpenAI()` with `get_client()` |
| `hermes/core/intent_openai.py` | Replaced direct `OpenAI()` with `get_client()` |
| `hermes/commands/intake.py` | Replaced direct `OpenAI()` with `get_client()` |
| `hermes/commands/agency_intake.py` | Replaced direct `OpenAI()` with `get_client()` |
| `hermes/commands/business_research.py` | Replaced direct `OpenAI()` with `get_client()` |
| `hermes/operations/command_center_qa.py` | Replaced direct `OpenAI()` with `get_client()` |
| `hermes/sync/email_classifier.py` | Replaced direct `OpenAI()` with `get_client()` |
| `hermes/api.py` | **NEW** `/api/hermes/tts` endpoint (Slack voice clips) |
| `docker-compose.yml` | Removed n8n service + volume |
| `pyproject.toml` | Added `edge-tts>=6.1` (free TTS) |
| `.env.example` | Added LITELLM_BASE_URL / LITELLM_API_KEY docs |

### Tests

- `tests/test_llm_client.py` — 13 tests covering env resolution, key priority,
  base_url priority, model fallback, client construction
- `tests/test_tts_endpoint.py` — 4 tests covering TTS endpoint (empty text,
  no token, success path, generation failure)

---

## VPS deploy steps (hermes-gretch)

### 1. Pull the branch and set env vars

```bash
cd /opt/rsg-hermes
git fetch origin
git checkout hermes/litellm-consolidation
git pull origin hermes/litellm-consolidation
```

Add to `/opt/rsg-hermes/.env` (already partially set):

```
LITELLM_BASE_URL=https://litellm-qidsf-u69864.vm.elestio.app/v1
LITELLM_API_KEY=sk-Wsn-bgj32U3PKLVN65Y1Qw
HERMES_OPENAI_MODEL=gpt-4.1-mini
```

### 2. Rebuild and restart containers

```bash
docker compose up -d --build hermes hermes-api hermes-crm-queue-worker hermes-intake-worker
docker compose ps
```

A rebuild is required — `pyproject.toml` added `edge-tts>=6.1`.

### 3. Remove n8n from the running stack

The n8n service was removed from `docker-compose.yml`. If `rsg-n8n` is still
running, stop it:

```bash
docker stop rsg-n8n && docker rm rsg-n8n
docker volume rm rsg-hermes_n8n_data 2>/dev/null || true
```

### 4. Update the Nous Hermes Agent config

Edit `/opt/app/hermes-home/config.yaml`:

```yaml
model:
  default: hermes_intake_default       # LiteLLM model group (was deepseek-v4-pro)
  provider: openai                      # OpenAI-compatible
  base_url: https://litellm-qidsf-u69864.vm.elestio.app/v1
```

Add to `/opt/app/hermes-home/.env`:
```
LITELLM_API_KEY=sk-Wsn-bgj32U3PKLVN65Y1Qw
```

Remove the n8n MCP server from `config.yaml`:
```yaml
# DELETE this entire block:
#   n8n:
#     command: /home/hermes/.hermes/mcp-installs/n8n/.venv/bin/python
#     ...
```

Enable TTS:
```yaml
tts:
  provider: edge
  edge:
    voice: en-US-AriaNeural
  auto_tts: true
```

Restart:
```bash
cd /opt/app && docker compose restart hermes-agent hermes-webui
```

### 5. Test voice

```bash
# From the VPS, test the TTS endpoint:
curl -X POST http://localhost:8788/api/hermes/tts \
  -H "Content-Type: application/json" \
  -d '{"text":"Hermes voice check. If you can hear this, voice output is working."}'
```

In the web chat, type `/voice tts` to toggle voice output on.

---

## Mac local Hermes config changes

Edit `~/.hermes/config.yaml`:

```yaml
model:
  default: gpt-4.1-mini              # cheap model for personal use (was claude-opus-4.8)
  provider: openai
  base_url: https://litellm-qidsf-u69864.vm.elestio.app/v1
```

Add to `~/.hermes/.env`:
```
LITELLM_API_KEY=sk-Wsn-bgj32U3PKLVN65Y1Qw
```

This routes personal queries through LiteLLM with a cheap model. Bump to
`claude-sonnet` or `deepseek-v4-pro` via the web chat model selector when you
need heavier reasoning.

---

## Decommission: Onyx VPS

Onyx is a self-hosted RAG/search platform running 12 containers on
`onyx-1t6jv-u69864.vm.elestio.app`. Perplexity (already wired as an MCP server
in the Hermes Agent) replaces its search/research function.

### Stop all containers (immediate)

```bash
ssh root@onyx-1t6jv-u69864.vm.elestio.app \
  'docker compose -f /opt/app/docker-compose.yml down'
```

### Full decommission

On the Elestio dashboard (https://dash.elest.io/76821/rsg-llm/services/891092/onyx-1t6jv/overview):
1. Stop the service
2. Delete the VPS

This saves the full cost of an 8 GB RAM VPS running 12 containers.

---

## Decommission: n8n VPS

n8n is on Tailscale `n8n-9uiaa-u69864` (all workflows killed ~2026-07-02).
The Hermes Python codebase now owns all automation (sync, intake, queue worker).

### Stop all containers

```bash
ssh root@n8n-9uiaa-u69864 'docker compose down' 2>/dev/null || true
```

### Full decommission

On the Elestio dashboard, find the n8n service and delete the VPS.

---

## Cost impact

| Item | Before | After |
|------|--------|-------|
| Onyx VPS (8 GB, 12 containers) | Full VPS cost | $0 (decommissioned) |
| n8n VPS | Full VPS cost | $0 (decommissioned) |
| LLM calls (6 code paths bypassing LiteLLM) | Direct provider billing, no budget caps | LiteLLM budget caps + caching |
| Mac local Hermes model | claude-opus-4.8 ($15/$75 per Mtok) | gpt-4.1-mini ($2/$8 per Mtok) |
| Voice TTS | Not configured | Edge TTS (free) |

---

## Rollback

```bash
# Revert code
cd /opt/rsg-hermes
git checkout main
docker compose up -d --build

# Restore n8n (if needed)
git show main:docker-compose.yml > docker-compose.yml
docker compose up -d n8n
```

For VPS Hermes Agent config, restore from backup:
```bash
cp /opt/app/hermes-home/config.yaml.bak config.yaml
```
