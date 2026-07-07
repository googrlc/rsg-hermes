#!/usr/bin/env bash
# Wrapper for the nightly commission ingest (hermes --commission-sync).
# Keeps secrets in the repo's gitignored .env rather than in the launchd plist.
set -euo pipefail

REPO="${HERMES_REPO:-/Users/lamarcoates/Documents/GitHub/rsg-hermes}"
cd "$REPO"

# Load .env (NOWCERTS_*, SUPABASE_*, SLACK_BOT_TOKEN, HERMES_COMMISSIONS_*).
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

exec /opt/homebrew/bin/uv run hermes --commission-sync
