#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR="${1:-$(pwd)}"
ENV_FILE="$TARGET_DIR/.env"

fail() {
  echo "❌ $*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

[[ -f "$ENV_FILE" ]] || fail "Missing env file: $ENV_FILE"

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

require_cmd curl

[[ -n "${TELEGRAM_BOT_TOKEN:-}" ]] || fail "TELEGRAM_BOT_TOKEN is empty in .env"
[[ -n "${WEBHOOK_BASE_URL:-}" ]] || fail "WEBHOOK_BASE_URL is empty in .env"

WEBHOOK_PATH="${WEBHOOK_PATH:-telegram}"
WEBHOOK_BASE_URL="${WEBHOOK_BASE_URL%/}"
WEBHOOK_PATH="${WEBHOOK_PATH#/}"
WEBHOOK_URL="$WEBHOOK_BASE_URL/$WEBHOOK_PATH"

echo "Setting Telegram webhook to: $WEBHOOK_URL"

if [[ -n "${WEBHOOK_SECRET_TOKEN:-}" ]]; then
  response="$(curl -fsS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
    --data-urlencode "url=${WEBHOOK_URL}" \
    --data-urlencode "secret_token=${WEBHOOK_SECRET_TOKEN}" \
    --data-urlencode "drop_pending_updates=false")"
else
  response="$(curl -fsS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
    --data-urlencode "url=${WEBHOOK_URL}" \
    --data-urlencode "drop_pending_updates=false")"
fi

echo "$response" | grep -q '"ok":true' || fail "Telegram setWebhook failed: $response"

echo "Webhook status:"
status_response="$(curl -fsS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo")"
echo "$status_response"
echo "$status_response" | grep -q '"ok":true' || fail "Telegram getWebhookInfo failed: $status_response"
echo "$status_response" | grep -Fq "\"url\":\"${WEBHOOK_URL}\"" || fail "Webhook URL mismatch. Expected ${WEBHOOK_URL}"

echo "✅ Telegram webhook configured successfully."
