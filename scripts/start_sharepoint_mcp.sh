#!/usr/bin/env bash
# Start SharePoint MCP on hermes-gretch via Docker (production has no host .venv).
#
#   cd /opt/rsg-hermes && ./scripts/start_sharepoint_mcp.sh
#
# Env file defaults to /opt/app/.env (Elestio); override with ENV_FILE=...

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ENV_FILE:-/opt/app/.env}"
COMPOSE_DIR="${COMPOSE_DIR:-$REPO_ROOT}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE — set ENV_FILE to your Hermes .env path" >&2
  exit 1
fi

if [[ ! -f "$COMPOSE_DIR/docker-compose.yml" ]]; then
  echo "Missing $COMPOSE_DIR/docker-compose.yml" >&2
  exit 1
fi

cd "$COMPOSE_DIR"

echo "Building and starting sharepoint-mcp (env: $ENV_FILE)"
docker compose --env-file "$ENV_FILE" up -d --build sharepoint-mcp

sleep 2
curl -sf http://127.0.0.1:8082/healthz && echo
echo "SharePoint MCP listening on http://127.0.0.1:8082"
echo "Smoke: API_SERVER_KEY=\$(grep '^API_SERVER_KEY=' $ENV_FILE | cut -d= -f2-) $REPO_ROOT/scripts/sharepoint_mcp_smoke_test.sh"
