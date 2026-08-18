#!/usr/bin/env bash
# POST a policy investigation case to the RSG Data Quality Investigator webhook.
#
# Usage:
#   export CURSOR_AUTOMATION_WEBHOOK_URL="https://..."
#   export CURSOR_AUTOMATION_WEBHOOK_KEY="..."
#   ./scripts/trigger_policy_investigation.sh 990414352 "Steven Prak" "Personal Auto"
#
# Or pipe JSON:
#   echo '{"policy_number":"990414352","client_name":"Steven Prak"}' | ./scripts/trigger_policy_investigation.sh
#
# Secrets: set CURSOR_AUTOMATION_WEBHOOK_URL and CURSOR_AUTOMATION_WEBHOOK_KEY from
# the automation's Webhook trigger panel in cursor.com/automations (never commit them).
set -euo pipefail

URL="${CURSOR_AUTOMATION_WEBHOOK_URL:-}"
KEY="${CURSOR_AUTOMATION_WEBHOOK_KEY:-}"

if [[ -z "$URL" || -z "$KEY" ]]; then
  echo "Set CURSOR_AUTOMATION_WEBHOOK_URL and CURSOR_AUTOMATION_WEBHOOK_KEY" >&2
  exit 1
fi

if [[ $# -ge 1 ]]; then
  PN="$1"
  CLIENT="${2:-}"
  LOB="${3:-}"
  BODY=$(python3 -c "
import json, sys
print(json.dumps({
    'policy_number': sys.argv[1],
    'client_name': sys.argv[2] or None,
    'line_of_business': sys.argv[3] or None,
}))
" "$PN" "$CLIENT" "$LOB")
elif [[ ! -t 0 ]]; then
  BODY=$(cat)
else
  echo "Usage: $0 <policy_number> [client_name] [line_of_business]" >&2
  echo "   or: echo '{\"policy_number\":\"...\"}' | $0" >&2
  exit 1
fi

curl -fsS -X POST "$URL" \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d "$BODY"
echo
