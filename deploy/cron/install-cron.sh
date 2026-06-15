#!/usr/bin/env bash
# Idempotently install the Hermes scheduled jobs into the current user's crontab.
#
# Safe to re-run after every redeploy: existing crontab entries (Elestio backups,
# maintenance, watchtower, etc.) are preserved; only the Hermes triage line is
# replaced. Run on the Hermes host (hermes-gretch), as the user whose crontab runs
# the jobs (root on the Elestio box):
#
#   sudo bash /opt/rsg-hermes/deploy/cron/install-cron.sh
#
# Why this exists: the email-triage schedule was set up directly on the old
# hermes-elestio box and was silently lost in the June 2026 VPS cutover — triage
# never ran on hermes-gretch, so Outlook mail stopped flowing in. Keeping the cron
# in-repo + idempotent installer makes the schedule a tracked, repeatable artifact.
set -euo pipefail

REPO_DIR="${HERMES_REPO_DIR:-/opt/rsg-hermes}"
LOG="${HERMES_TRIAGE_LOG:-$HOME/hermes-email-triage.log}"
CRON_LINE="*/30 * * * * cd ${REPO_DIR} && docker compose run --rm hermes hermes --email-triage --email-provider ms365 --email-since-hours 2 >> ${LOG} 2>&1"

touch "${LOG}"

# Preserve existing entries; drop any prior triage line (match on the unique
# --email-triage flag); append the fresh one.
( crontab -l 2>/dev/null | grep -v -- "--email-triage" ; echo "${CRON_LINE}" ) | crontab -

echo "Installed Hermes email-triage cron:"
crontab -l | grep -- "--email-triage"
