#!/usr/bin/env bash
# Install locked MCP egress vhost on hermes-gretch (Elestio nginx + bridge env).
# Run ON THE BOX as a user with sudo for nginx reload.
#
#   cd /opt/rsg-hermes && sudo ./scripts/install_mcp_egress.sh
#
# Prerequisite: DNS A record hermes-mcp.risksolutionsgroup.net → this host's public IP.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NGINX_SRC="$REPO_ROOT/deploy/nginx/hermes-mcp.risksolutionsgroup.net.conf"
NGINX_DEST="/opt/elestio/nginx/conf.d/hermes-mcp.risksolutionsgroup.net.conf"
ENV_FILE="${ENV_FILE:-/opt/app/.env}"
MCP_PUBLIC_BASE_URL="${MCP_PUBLIC_BASE_URL:-https://hermes-mcp.risksolutionsgroup.net}"
MCP_CONTAINER="${MCP_CONTAINER:-app-rsg-hermes-mcp-1}"

echo "Hermes MCP egress install"
echo "  repo:     $REPO_ROOT"
echo "  nginx:    $NGINX_DEST"
echo "  env:      $ENV_FILE"
echo "  public:   $MCP_PUBLIC_BASE_URL"
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
echo "Ensure TLS cert paths in that file match your box (edit if needed)."

if command -v nginx >/dev/null 2>&1; then
  nginx -t
  nginx -s reload || true
elif docker ps --format '{{.Names}}' 2>/dev/null | grep -qx elestio-nginx; then
  docker exec elestio-nginx nginx -t
  docker exec elestio-nginx nginx -s reload
else
  echo "WARN: no nginx/openresty on PATH and elestio-nginx container not found — reload nginx manually" >&2
fi

if [[ -f "$ENV_FILE" ]]; then
  if grep -q '^MCP_PUBLIC_BASE_URL=' "$ENV_FILE"; then
    sed -i "s|^MCP_PUBLIC_BASE_URL=.*|MCP_PUBLIC_BASE_URL=$MCP_PUBLIC_BASE_URL|" "$ENV_FILE"
  else
    echo "MCP_PUBLIC_BASE_URL=$MCP_PUBLIC_BASE_URL" >> "$ENV_FILE"
  fi
  echo "Set MCP_PUBLIC_BASE_URL in $ENV_FILE"
else
  echo "WARN: $ENV_FILE not found — set MCP_PUBLIC_BASE_URL manually" >&2
fi

if docker inspect "$MCP_CONTAINER" >/dev/null 2>&1; then
  docker restart "$MCP_CONTAINER"
  echo "Restarted $MCP_CONTAINER"
  sleep 2
  curl -sf "http://127.0.0.1:8081/healthz" && echo " local bridge OK"
else
  echo "WARN: container $MCP_CONTAINER not found — start MCP bridge manually" >&2
fi

echo
echo "Next: smoke from OUTSIDE tailnet:"
echo "  curl -s https://hermes-mcp.risksolutionsgroup.net/healthz"
echo "  API_SERVER_KEY=\$API_SERVER_KEY MCP_URL=https://hermes-mcp.risksolutionsgroup.net $REPO_ROOT/scripts/mcp_smoke_test.sh"
