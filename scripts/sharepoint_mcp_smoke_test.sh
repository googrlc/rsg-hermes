#!/usr/bin/env bash
# Smoke tests for SharePoint MCP (local + optional public egress).
#
#   source .venv/bin/activate
#   API_SERVER_KEY=... MS365_*=... SHAREPOINT_SITE_URL=... ./scripts/sharepoint_mcp_smoke_test.sh
#   CHECK_EGRESS=1 API_SERVER_KEY=... ./scripts/sharepoint_mcp_smoke_test.sh

set -euo pipefail

SP_LOCAL_URL="${SP_MCP_LOCAL_URL:-http://127.0.0.1:8082}"
SP_EGRESS_URL="${SP_MCP_EGRESS_URL:-https://sharepoint-mcp.risksolutionsgroup.net}"
SP_EGRESS_HOST="${SP_MCP_EGRESS_HOST:-sharepoint-mcp.risksolutionsgroup.net}"
SP_EGRESS_RESOLVE_IP="${SP_MCP_EGRESS_RESOLVE_IP:-}"
API_KEY="${API_SERVER_KEY:-}"

red() { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
fail() { red "FAIL: $*"; exit 1; }
pass() { green "PASS: $*"; }

_egress_resolve_ip() {
  if [[ -n "$SP_EGRESS_RESOLVE_IP" ]]; then
    return 0
  fi
  SP_EGRESS_RESOLVE_IP="$(dig +short "$SP_EGRESS_HOST" @8.8.8.8 2>/dev/null | head -1 || true)"
}

_egress_curl() {
  local path="$1"
  _egress_resolve_ip
  if [[ -n "$SP_EGRESS_RESOLVE_IP" ]]; then
    curl -sfk --resolve "${SP_EGRESS_HOST}:443:${SP_EGRESS_RESOLVE_IP}" \
      "${SP_EGRESS_URL}${path}"
  else
    curl -sfk "${SP_EGRESS_URL}${path}"
  fi
}

sp_post() {
  local url="$1"
  local body="$2"
  curl -s \
    -H "Authorization: Bearer $API_KEY" \
    -H "Accept: application/json" \
    -H "Content-Type: application/json" \
    -d "$body" \
    "$url"
}

echo "SharePoint MCP (local): $SP_LOCAL_URL"
echo

health="$(curl -sf "$SP_LOCAL_URL/healthz" || true)"
if [[ -z "$health" ]]; then
  fail "GET $SP_LOCAL_URL/healthz unreachable — start SharePoint MCP on 8082"
fi
pass "sharepoint-mcp /healthz → $health"

if [[ -z "$API_KEY" ]]; then
  fail "API_SERVER_KEY not set"
fi

ping_body="$(sp_post "$SP_LOCAL_URL/mcp" '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"ping","arguments":{}}}')"
if echo "$ping_body" | grep -q 'Unauthorized\|-32001'; then
  fail "SharePoint MCP ping auth failed"
fi
if echo "$ping_body" | grep -qi 'sharepoint error'; then
  fail "SharePoint MCP ping Graph error: $ping_body"
fi
if ! echo "$ping_body" | grep -qi 'ok\|reachable\|RSG-Knowledge\|sharepoint'; then
  fail "SharePoint MCP ping unexpected: $ping_body"
fi
pass "SharePoint MCP tools/call ping"

list_body="$(sp_post "$SP_LOCAL_URL/mcp" '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}')"
if ! echo "$list_body" | grep -q 'search_knowledge'; then
  fail "SharePoint MCP tools/list missing search_knowledge: $list_body"
fi
pass "SharePoint MCP tools/list"

echo
green "All local SharePoint MCP smoke checks passed."

if [[ "${CHECK_EGRESS:-}" == "1" ]]; then
  echo
  echo "Egress probe: $SP_EGRESS_URL"
  egress_health="$(_egress_curl /healthz 2>/dev/null || true)"
  if [[ -z "$egress_health" ]]; then
    fail "public egress $SP_EGRESS_URL/healthz unreachable (DNS/nginx/TLS?)"
  fi
  pass "public egress /healthz → $egress_health"
  green "SharePoint egress smoke checks passed."
fi
