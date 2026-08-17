#!/usr/bin/env bash
# Smoke tests for Hermes API + MCP bridge before Copilot Studio wiring (Phase 2).
# Usage:
#   source .venv/bin/activate
#   hermes-api --host 127.0.0.1 --port 8787   # separate terminal / tmux
#   API_SERVER_KEY=dev-key ./scripts/mcp_smoke_test.sh
#   API_SERVER_KEY=dev-key HERMES_API_URL=http://127.0.0.1:8788 \
#     ./scripts/mcp_smoke_test.sh
#   CHECK_EGRESS=1 API_SERVER_KEY=dev-key ./scripts/mcp_smoke_test.sh

set -euo pipefail

API_URL="${HERMES_API_URL:-http://127.0.0.1:8787}"
# Local bridge always hits the container on the box — not the public hostname.
MCP_LOCAL_URL="${MCP_LOCAL_URL:-${MCP_URL:-http://127.0.0.1:8081}}"
MCP_EGRESS_URL="${MCP_EGRESS_URL:-https://hermes-mcp.risksolutionsgroup.net}"
MCP_EGRESS_HOST="${MCP_EGRESS_HOST:-hermes-mcp.risksolutionsgroup.net}"
MCP_EGRESS_RESOLVE_IP="${MCP_EGRESS_RESOLVE_IP:-}"
API_KEY="${API_SERVER_KEY:-dev-key}"

red() { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
fail() { red "FAIL: $*"; exit 1; }
pass() { green "PASS: $*"; }

_egress_resolve_ip() {
  if [[ -n "$MCP_EGRESS_RESOLVE_IP" ]]; then
    return 0
  fi
  MCP_EGRESS_RESOLVE_IP="$(dig +short "$MCP_EGRESS_HOST" @8.8.8.8 2>/dev/null | head -1 || true)"
}

_egress_curl() {
  local path="$1"
  _egress_resolve_ip
  if [[ -n "$MCP_EGRESS_RESOLVE_IP" ]]; then
    curl -sfk --resolve "${MCP_EGRESS_HOST}:443:${MCP_EGRESS_RESOLVE_IP}" \
      "${MCP_EGRESS_URL}${path}"
  else
    curl -sfk "${MCP_EGRESS_URL}${path}"
  fi
}

_egress_mcp_post() {
  local body="$1"
  _egress_resolve_ip
  if [[ -n "$MCP_EGRESS_RESOLVE_IP" ]]; then
    curl -sk --resolve "${MCP_EGRESS_HOST}:443:${MCP_EGRESS_RESOLVE_IP}" \
      -H "Authorization: Bearer $API_KEY" \
      -H "Accept: application/json" \
      -H "Content-Type: application/json" \
      -d "$body" \
      "${MCP_EGRESS_URL}/mcp"
  else
    curl -sk \
      -H "Authorization: Bearer $API_KEY" \
      -H "Accept: application/json" \
      -H "Content-Type: application/json" \
      -d "$body" \
      "${MCP_EGRESS_URL}/mcp"
  fi
}

echo "Hermes API: $API_URL"
echo "MCP bridge (local): $MCP_LOCAL_URL"
echo

# --- Hermes API ---
health="$(curl -sf "$API_URL/health" || true)"
if [[ -z "$health" ]]; then
  fail "GET $API_URL/health unreachable — start hermes-api first (hermes-gretch: try HERMES_API_URL=http://127.0.0.1:8788)"
fi
pass "hermes-api /health → $health"

skills="$(curl -sf "$API_URL/api/command-center/skills" 2>/dev/null | head -c 200 || true)"
if [[ -z "$skills" ]]; then
  fail "GET $API_URL/api/command-center/skills failed"
fi
pass "hermes-api /api/command-center/skills reachable"

# --- MCP bridge (local) ---
bridge_health="$(curl -sf "$MCP_LOCAL_URL/healthz" || true)"
if [[ -z "$bridge_health" ]]; then
  fail "GET $MCP_LOCAL_URL/healthz unreachable — start MCP bridge (see docs/amy-getting-started.md)"
fi
pass "mcp-bridge /healthz → $bridge_health"

mcp_post() {
  curl -s \
    -H "Authorization: Bearer $API_KEY" \
    -H "Accept: application/json" \
    -H "Content-Type: application/json" \
    -d "$1" \
    "$MCP_LOCAL_URL/mcp"
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
green "All local smoke checks passed."

if [[ "${CHECK_EGRESS:-}" == "1" ]]; then
  echo
  echo "Egress probe: $MCP_EGRESS_URL"
  if [[ -n "$MCP_EGRESS_RESOLVE_IP" ]]; then
    echo "  (resolve ${MCP_EGRESS_HOST} → ${MCP_EGRESS_RESOLVE_IP})"
  fi
  egress_health="$(_egress_curl /healthz 2>/dev/null || true)"
  if [[ -z "$egress_health" ]]; then
    fail "public egress $MCP_EGRESS_URL/healthz unreachable (DNS/nginx/TLS?)"
  fi
  pass "public egress /healthz → $egress_health"
  egress_ping="$(_egress_mcp_post '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"ping","arguments":{}}}')"
  if echo "$egress_ping" | grep -q 'Unauthorized\|-32001'; then
    fail "public egress MCP auth failed"
  fi
  if ! echo "$egress_ping" | grep -q 'bridge reachable'; then
    fail "public egress ping unexpected: $egress_ping"
  fi
  pass "public egress MCP ping"
  green "Egress smoke checks passed."
fi
