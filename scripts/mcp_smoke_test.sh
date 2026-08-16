#!/usr/bin/env bash
# Smoke tests for Hermes API + MCP bridge before Copilot Studio wiring (Phase 2).
# Usage:
#   source .venv/bin/activate
#   hermes-api --host 127.0.0.1 --port 8787   # separate terminal / tmux
#   API_SERVER_KEY=dev-key ./scripts/mcp_smoke_test.sh
#   API_SERVER_KEY=dev-key HERMES_API_URL=http://127.0.0.1:8787 \
#     uvicorn deploy.mcp-bridge.app:app --host 127.0.0.1 --port 8081
#   API_SERVER_KEY=dev-key MCP_URL=http://127.0.0.1:8081 ./scripts/mcp_smoke_test.sh

set -euo pipefail

API_URL="${HERMES_API_URL:-http://127.0.0.1:8787}"
MCP_URL="${MCP_URL:-http://127.0.0.1:8081}"
MCP_EGRESS_URL="${MCP_EGRESS_URL:-https://hermes-mcp.risksolutionsgroup.net}"
API_KEY="${API_SERVER_KEY:-dev-key}"

red() { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
fail() { red "FAIL: $*"; exit 1; }
pass() { green "PASS: $*"; }

echo "Hermes API: $API_URL"
echo "MCP bridge: $MCP_URL"
echo

# --- Hermes API ---
health="$(curl -sf "$API_URL/health" || true)"
if [[ -z "$health" ]]; then
  fail "GET $API_URL/health unreachable — start hermes-api first"
fi
pass "hermes-api /health → $health"

skills="$(curl -sf "$API_URL/api/command-center/skills" 2>/dev/null | head -c 200 || true)"
if [[ -z "$skills" ]]; then
  fail "GET $API_URL/api/command-center/skills failed"
fi
pass "hermes-api /api/command-center/skills reachable"

# --- MCP bridge ---
bridge_health="$(curl -sf "$MCP_URL/healthz" || true)"
if [[ -z "$bridge_health" ]]; then
  fail "GET $MCP_URL/healthz unreachable — start MCP bridge (see docs/amy-getting-started.md)"
fi
pass "mcp-bridge /healthz → $bridge_health"

mcp_post() {
  curl -s \
    -H "Authorization: Bearer $API_KEY" \
    -H "Accept: application/json" \
    -H "Content-Type: application/json" \
    -d "$1" \
    "$MCP_URL/mcp"
}

ping_body="$(mcp_post '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"ping","arguments":{}}}')"
if echo "$ping_body" | grep -q 'Unauthorized\|-32001'; then
  fail "MCP ping auth failed — check API_SERVER_KEY (body: $ping_body)"
fi
if ! echo "$ping_body" | grep -q 'bridge reachable'; then
  fail "MCP ping unexpected body: $ping_body"
fi
pass "MCP tools/call ping"

list_body="$(mcp_post '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}')"
if echo "$list_body" | grep -q 'Unauthorized\|-32001'; then
  fail "MCP tools/list auth failed"
fi
if ! echo "$list_body" | grep -q '"name"'; then
  fail "MCP tools/list missing tools: $list_body"
fi
pass "MCP tools/list"

echo
green "All smoke checks passed."

if [[ "${CHECK_EGRESS:-}" == "1" ]] || [[ "$MCP_URL" == http://127.0.0.1:* ]]; then
  echo
  echo "Egress probe: $MCP_EGRESS_URL"
  egress_health="$(curl -sf "$MCP_EGRESS_URL/healthz" 2>/dev/null || true)"
  if [[ -z "$egress_health" ]]; then
    red "FAIL: public egress $MCP_EGRESS_URL/healthz unreachable (DNS/nginx/TLS?)"
    exit 1
  fi
  pass "public egress /healthz → $egress_health"
  egress_ping="$(curl -s \
    -H "Authorization: Bearer $API_KEY" \
    -H "Accept: application/json" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"ping","arguments":{}}}' \
    "$MCP_EGRESS_URL/mcp")"
  if echo "$egress_ping" | grep -q 'Unauthorized\|-32001'; then
    fail "public egress MCP auth failed"
  fi
  if ! echo "$egress_ping" | grep -q 'bridge reachable'; then
    fail "public egress ping unexpected: $egress_ping"
  fi
  pass "public egress MCP ping"
  green "Egress smoke checks passed."
fi
