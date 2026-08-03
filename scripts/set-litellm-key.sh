#!/usr/bin/env bash
set -euo pipefail

ENV_FILE=/opt/rsg-hermes/.env
VAR=LITELLM_API_KEY
SERVICE=rsg-hermes-api

[ -f "$ENV_FILE" ] || { echo "ERROR: $ENV_FILE not found"; exit 1; }

printf 'Paste the new LiteLLM virtual key (input hidden), then press Enter:\n> '
read -rs KEY
printf '\n'

KEY="${KEY#"${KEY%%[![:space:]]*}"}"
KEY="${KEY%"${KEY##*[![:space:]]}"}"

[ -n "$KEY" ] || { echo "ERROR: empty key, nothing changed"; exit 1; }
case "$KEY" in
  sk-*) ;;
  *) echo "ERROR: key does not start with 'sk-' -- refusing (nothing changed)"; exit 1 ;;
esac
case "$KEY" in
  *[[:space:]]*) echo "ERROR: key contains whitespace -- bad paste (nothing changed)"; exit 1 ;;
esac

BACKUP="${ENV_FILE}.bak.$(date +%Y%m%d-%H%M%S)"
cp -p "$ENV_FILE" "$BACKUP"
echo ">> backed up to $BACKUP"

OLD=$(grep -c "^${VAR}=" "$ENV_FILE" || true)

TMP=$(mktemp "${ENV_FILE}.XXXXXX")
cp -p "$ENV_FILE" "$TMP"
grep -v "^${VAR}=" "$ENV_FILE" > "$TMP" || true
printf '%s=%s\n' "$VAR" "$KEY" >> "$TMP"

ORIG_LINES=$(wc -l < "$ENV_FILE")
NEW_LINES=$(wc -l < "$TMP")
if [ "$NEW_LINES" -lt "$ORIG_LINES" ]; then
  rm -f "$TMP"
  echo "ERROR: rebuilt file lost lines ($ORIG_LINES -> $NEW_LINES). Nothing changed."
  exit 1
fi
mv "$TMP" "$ENV_FILE"

echo ">> ${VAR} set (replaced ${OLD} existing line(s)); now ...${KEY: -4}"

echo ">> recreating $SERVICE (up -d, not restart -- restart would keep the old env)"
cd /opt/rsg-hermes
docker compose up -d "$SERVICE"

echo ">> waiting for the API to come back"
for _ in $(seq 1 30); do
  curl -fsS -m 3 http://127.0.0.1:8788/health >/dev/null 2>&1 && break
  sleep 1
done

echo ">> asking the Finance desk a real question"
TOKEN=$(grep -m1 '^API_SERVER_KEY=' "$ENV_FILE" | cut -d= -f2- | tr -d '"' || true)
RESP=$(curl -s -m 120 -X POST \
  ${TOKEN:+-H "Authorization: Bearer $TOKEN"} \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Shorts by carrier","hub":"finance","persona":"lamar"}' \
  http://127.0.0.1:8788/api/command-center/ask || true)

case "$RESP" in
  *token_not_found_in_db*|*"Invalid proxy server token"*)
    echo "!! STILL REJECTED -- the proxy does not recognise this key either."
    echo "   Mint it under Virtual Keys (not a Master Key) and re-run."
    echo "   Restore with: cp $BACKUP $ENV_FILE && docker compose up -d $SERVICE"
    exit 1 ;;
  *'"ok":false'*|*error*|*Error*)
    echo "!! Key accepted, but the call errored -- read it:"
    printf '%s\n' "$RESP" | head -c 600; echo
    echo "   (An unknown-model error here means the proxy has no"
    echo "    'hard_judgment_escalation' model group -- proxy config, not the key.)"
    exit 1 ;;
  "")
    echo "!! No response. Check: docker logs --tail 50 $SERVICE"; exit 1 ;;
  *)
    echo ">> WORKING. The Finance desk answered:"
    printf '%s\n' "$RESP" | head -c 600; echo ;;
esac
