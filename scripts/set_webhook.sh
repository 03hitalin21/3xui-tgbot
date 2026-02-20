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

MAX_RETRIES="${WEBHOOK_SETUP_RETRIES:-10}"
RETRY_DELAY_SECONDS="${WEBHOOK_SETUP_RETRY_DELAY:-3}"

telegram_set_webhook() {
  curl -fsS -X POST \
    -d "url=${WEBHOOK_URL}" \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook"
}

for attempt in $(seq 1 "$MAX_RETRIES"); do
  response="$(telegram_set_webhook || true)"
  if echo "$response" | grep -q '"ok":true'; then
    break
  fi

  echo "Attempt ${attempt}/${MAX_RETRIES} failed while calling setWebhook."
  if [[ "$attempt" -lt "$MAX_RETRIES" ]]; then
    sleep "$RETRY_DELAY_SECONDS"
  fi
done

echo "$response" | grep -q '"ok":true' || fail "Telegram setWebhook failed after ${MAX_RETRIES} attempts: $response"

echo "Webhook status:"
status_response="$(curl -fsS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo")"
echo "$status_response"
echo "$status_response" | grep -q '"ok":true' || fail "Telegram getWebhookInfo failed: $status_response"
echo "$status_response" | grep -Fq "\"url\":\"${WEBHOOK_URL}\"" || fail "Webhook URL mismatch. Expected ${WEBHOOK_URL}"

echo "✅ Telegram webhook configured successfully."
