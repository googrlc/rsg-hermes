#!/usr/bin/env bash
# Install Hermes' scheduled automations into the current user's crontab.
#
# Idempotent and safe to re-run after every redeploy: it replaces only the
# Hermes-managed block (between the markers in deploy/cron/hermes.crontab) and
# any legacy standalone hermes lines, while preserving every other crontab entry
# (Elestio backups, maintenance, watchtower, …). Run on the Hermes host as the
# user whose crontab runs the jobs (root on the Elestio box):
#
#   sudo bash /opt/rsg-hermes/deploy/cron/install-cron.sh
#
# Why this exists: the schedule was set up directly on the old hermes-elestio box
# and lost in the June 2026 cutover to hermes-gretch — Outlook triage and every
# revenue briefing silently stopped. Keeping the schedule in-repo + an idempotent
# installer makes it a tracked, repeatable artifact.
set -euo pipefail

REPO_DIR="${HERMES_REPO_DIR:-/opt/rsg-hermes}"
LOG="${HERMES_CRON_LOG:-/root/hermes-cron.log}"
BLOCK_FILE="${1:-$REPO_DIR/deploy/cron/hermes.crontab}"

[ -f "$BLOCK_FILE" ] || { echo "crontab source not found: $BLOCK_FILE" >&2; exit 1; }
touch "$LOG"

current="$(crontab -l 2>/dev/null || true)"
# Drop the prior managed block, plus any legacy standalone hermes lines that
# predate the marker block (e.g. the original single email-triage line).
cleaned="$(printf '%s\n' "$current" \
  | sed '/# >>> hermes automations/,/# <<< hermes automations/d' \
  | grep -v -- '--email-triage' \
  | grep -v -F 'cd /opt/rsg-hermes && docker compose run --rm hermes' \
  | sed '/^[[:space:]]*$/d')"

# Append only the marker block from the source file (its doc header stays as
# documentation and must never enter the crontab, or it would accumulate).
block="$(sed -n '/# >>> hermes automations/,/# <<< hermes automations/p' "$BLOCK_FILE")"
[ -n "$block" ] || { echo "no managed block found in $BLOCK_FILE" >&2; exit 1; }

{ [ -n "$cleaned" ] && printf '%s\n' "$cleaned"; printf '%s\n' "$block"; } | crontab -

echo "Installed Hermes cron block:"
crontab -l | sed -n '/# >>> hermes automations/,/# <<< hermes automations/p'
