#!/usr/bin/env bash
# hermes-gretch readiness: ops-doctor + API health + optional MCP smoke.
# See docs/hermes-gretch-health-checklist.md

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

MCP_EGRESS_URL="${MCP_EGRESS_URL:-https://hermes-mcp.risksolutionsgroup.net}"

if [[ -f .venv/bin/activate ]]; then
  # shellcheck source=/dev/null
  source .venv/bin/activate
fi

red() { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }

echo "=== hermes --ops-doctor ==="
if command -v hermes >/dev/null 2>&1; then
  if hermes --ops-doctor; then
    green "ops-doctor OK"
  else
    red "ops-doctor reported issues (see above)"
  fi
else
  red "hermes CLI not in PATH — skip ops-doctor"
fi

echo
echo "=== Hermes API ==="
API_URL="${HERMES_API_URL:-http://127.0.0.1:8787}"
if curl -sf "$API_URL/health" >/dev/null; then
  green "GET $API_URL/health OK"
else
  red "Hermes API not reachable at $API_URL"
fi

echo
echo "=== MCP bridge (optional) ==="
MCP_URL="${MCP_URL:-http://127.0.0.1:8081}"
if curl -sf "$MCP_URL/healthz" >/dev/null; then
  green "MCP bridge up — running mcp_smoke_test.sh"
  API_SERVER_KEY="${API_SERVER_KEY:-dev-key}" MCP_URL="$MCP_URL" HERMES_API_URL="$API_URL" \
    "$REPO_ROOT/scripts/mcp_smoke_test.sh"
  echo
  echo "=== Public egress (optional) ==="
  if curl -sf "$MCP_EGRESS_URL/healthz" >/dev/null 2>&1; then
    CHECK_EGRESS=1 API_SERVER_KEY="${API_SERVER_KEY:-dev-key}" \
      MCP_URL="$MCP_URL" HERMES_API_URL="$API_URL" \
      "$REPO_ROOT/scripts/mcp_smoke_test.sh" | tail -5
  else
    echo "Public egress not reachable yet: $MCP_EGRESS_URL"
    echo "After Wix DNS + install_mcp_egress.sh, re-run or:"
    echo "  CHECK_EGRESS=1 API_SERVER_KEY=... ./scripts/mcp_smoke_test.sh"
  fi
else
  echo "MCP bridge not on $MCP_URL — skip (start app-rsg-hermes-mcp-1 or uvicorn bridge)"
fi
