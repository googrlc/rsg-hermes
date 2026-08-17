#!/usr/bin/env bash
# Install SharePoint MCP egress vhost on hermes-gretch (Elestio nginx).
# Run ON THE BOX as a user with sudo for nginx reload.
#
#   cd /opt/rsg-hermes && sudo ./scripts/install_sharepoint_mcp_egress.sh
#
# Prerequisite: DNS A record sharepoint-mcp.risksolutionsgroup.net → this host's public IP.
# Prerequisite: SharePoint MCP listening on 127.0.0.1:8082 (see deploy/sharepoint_mcp/README.md).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NGINX_SRC="$REPO_ROOT/deploy/nginx/sharepoint-mcp.risksolutionsgroup.net.conf"
NGINX_DEST="/opt/elestio/nginx/conf.d/sharepoint-mcp.risksolutionsgroup.net.conf"
ENV_FILE="${ENV_FILE:-/opt/app/.env}"
SP_MCP_PUBLIC_BASE_URL="${SP_MCP_PUBLIC_BASE_URL:-https://sharepoint-mcp.risksolutionsgroup.net}"
SP_MCP_PORT="${SP_MCP_PORT:-8082}"

echo "SharePoint MCP egress install"
echo "  repo:     $REPO_ROOT"
echo "  nginx:    $NGINX_DEST"
echo "  env:      $ENV_FILE"
echo "  public:   $SP_MCP_PUBLIC_BASE_URL"
echo "  local:    127.0.0.1:$SP_MCP_PORT"
echo

if [[ ! -f "$NGINX_SRC" ]]; then
  echo "Missing $NGINX_SRC — run from repo root" >&2
  exit 1
fi

if [[ $EUID -ne 0 ]]; then
  echo "Re-run with sudo for nginx install/reload." >&2
  exit 1
fi

cp "$NGINX_SRC" "$NGINX_DEST"
echo "Installed nginx vhost → $NGINX_DEST"

if command -v nginx >/dev/null 2>&1; then
  nginx -t
  nginx -s reload || true
elif docker ps --format '{{.Names}}' 2>/dev/null | grep -qx elestio-nginx; then
  docker exec elestio-nginx nginx -t
  docker exec elestio-nginx nginx -s reload
else
  echo "WARN: no nginx on PATH and elestio-nginx not found — reload nginx manually" >&2
fi

if [[ -f "$ENV_FILE" ]]; then
  if grep -q '^SP_MCP_PUBLIC_BASE_URL=' "$ENV_FILE"; then
    sed -i "s|^SP_MCP_PUBLIC_BASE_URL=.*|SP_MCP_PUBLIC_BASE_URL=$SP_MCP_PUBLIC_BASE_URL|" "$ENV_FILE"
  else
    echo "SP_MCP_PUBLIC_BASE_URL=$SP_MCP_PUBLIC_BASE_URL" >> "$ENV_FILE"
  fi
  echo "Set SP_MCP_PUBLIC_BASE_URL in $ENV_FILE"
else
  echo "WARN: $ENV_FILE not found — set SP_MCP_PUBLIC_BASE_URL manually" >&2
fi

if curl -sf "http://127.0.0.1:${SP_MCP_PORT}/healthz" >/dev/null 2>&1; then
  echo "SharePoint MCP local healthz OK on :$SP_MCP_PORT"
else
  echo "WARN: nothing on 127.0.0.1:$SP_MCP_PORT — start SharePoint MCP before Copilot wiring:" >&2
  echo "  source .venv/bin/activate" >&2
  echo "  set -a && source $ENV_FILE && set +a" >&2
  echo "  SHAREPOINT_MCP_TRANSPORT=http uvicorn deploy.sharepoint_mcp.http_app:app --host 127.0.0.1 --port $SP_MCP_PORT" >&2
fi

echo
echo "Next: Wix DNS A record sharepoint-mcp → box public IP (same as hermes-mcp)"
echo "  curl -s https://sharepoint-mcp.risksolutionsgroup.net/healthz"
echo "  CHECK_EGRESS=1 API_SERVER_KEY=\$API_SERVER_KEY $REPO_ROOT/scripts/sharepoint_mcp_smoke_test.sh"
