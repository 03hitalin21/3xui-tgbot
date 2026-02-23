#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR="${1:-$(pwd)}"
ENV_FILE="$TARGET_DIR/.env"

fail() {
  echo "❌ $*" >&2
  exit 1
}

warn() {
  echo "⚠️ $*" >&2
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

trim_whitespace() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

strip_wrapping_quotes() {
  local value="$1"
  if [[ "$value" == \"*\" && "$value" == *\" ]]; then
    value="${value:1:-1}"
  elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
    value="${value:1:-1}"
  fi
  printf '%s' "$value"
}

load_env_file() {
  local file_path="$1"
  local line
  local key
  local value
  local pending_key=""

  while IFS= read -r line || [[ -n "$line" ]]; do
    line="$(trim_whitespace "$line")"
    [[ -z "$line" || "$line" =~ ^# ]] && continue

    if [[ -n "$pending_key" && "$line" != *=* ]]; then
      value="$(strip_wrapping_quotes "$line")"
      printf -v "$pending_key" '%s' "$value"
      pending_key=""
      continue
    fi

    if [[ "$line" == export\ * ]]; then
      line="${line#export }"
      line="$(trim_whitespace "$line")"
    fi

    if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      value="${BASH_REMATCH[2]}"
      value="$(strip_wrapping_quotes "$value")"
      printf -v "$key" '%s' "$value"

      if [[ "$key" == "TELEGRAM_BOT_TOKEN" && -z "$value" ]]; then
        pending_key="$key"
      else
        pending_key=""
      fi
      continue
    fi

    warn "Ignoring malformed .env line: $line"
  done < "$file_path"
}

wait_for_webhook_endpoint() {
  local retries="${WEBHOOK_HEALTH_RETRIES:-10}"
  local delay_seconds="${WEBHOOK_HEALTH_RETRY_DELAY:-3}"
  local attempt
  local code

  for attempt in $(seq 1 "$retries"); do
    code="$(curl -k -sS -o /dev/null -w "%{http_code}" -X POST "$WEBHOOK_URL" || echo "000")"
    case "$code" in
      200|400|401|403|404|405)
        echo "✅ Webhook endpoint is reachable (HTTP $code)."
        return 0
        ;;
      502|503|504|000)
        echo "Attempt ${attempt}/${retries}: webhook endpoint not ready (HTTP $code)."
        ;;
      *)
        echo "Attempt ${attempt}/${retries}: webhook endpoint responded with HTTP $code (continuing)."
        ;;
    esac

    if [[ "$attempt" -lt "$retries" ]]; then
      sleep "$delay_seconds"
    fi
  done

  warn "Webhook endpoint did not become healthy after ${retries} attempts. Telegram may report 502 until backend is ready."
  return 1
}

[[ -f "$ENV_FILE" ]] || fail "Missing env file: $ENV_FILE"
load_env_file "$ENV_FILE"

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

wait_for_webhook_endpoint || true

telegram_set_webhook() {
  if [[ -n "${WEBHOOK_SECRET_TOKEN:-}" ]]; then
    curl -fsS -X POST \
      -d "url=${WEBHOOK_URL}" \
      -d "secret_token=${WEBHOOK_SECRET_TOKEN}" \
      "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook"
  else
    curl -fsS -X POST \
      -d "url=${WEBHOOK_URL}" \
      "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook"
  fi
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

if echo "$status_response" | grep -Eq '"last_error_message"\s*:\s*".*502'; then
  fail "Telegram currently reports webhook 502. Check bot/nginx status and rerun installer option 4 then option 5."
fi

echo "✅ Telegram webhook configured successfully."
