#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR="${1:-$(pwd)}"
ENV_FILE="$TARGET_DIR/.env"

ok() { echo "✅ $*"; }
warn() { echo "⚠️ $*"; }
err() { echo "❌ $*"; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { err "Required command not found: $1"; return 1; }
}

trim_whitespace() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

strip_wrapping_quotes() {
  local value="$1"
  local first_char
  local last_char

  if [[ ${#value} -ge 2 ]]; then
    first_char="${value:0:1}"
    last_char="${value: -1}"
    if [[ "$first_char" == '"' && "$last_char" == '"' ]]; then
      value="${value:1:-1}"
    elif [[ "$first_char" == "'" && "$last_char" == "'" ]]; then
      value="${value:1:-1}"
    fi
  fi

  printf '%s' "$value"
}

load_env() {
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
  done < "$ENV_FILE"
}

main() {
  local failed=0

  [[ -f "$ENV_FILE" ]] || { err "Missing env file: $ENV_FILE"; exit 1; }

  require_cmd curl || failed=$((failed + 1))
  load_env

  local expected_webhook="${WEBHOOK_BASE_URL%/}/${WEBHOOK_PATH#/}"

  if [[ -n "${SSL_DOMAIN:-}" ]] && [[ -f "/etc/letsencrypt/live/${SSL_DOMAIN}/fullchain.pem" ]]; then
    ok "TLS certificate file exists for ${SSL_DOMAIN}."
  else
    err "TLS certificate file missing for SSL_DOMAIN=${SSL_DOMAIN:-unset}."
    failed=$((failed + 1))
  fi

  if [[ -x "$TARGET_DIR/scripts/manage_services.sh" ]]; then
    if service_status="$(cd "$TARGET_DIR" && ./scripts/manage_services.sh status 2>&1)"; then
      ok "Local bot/admin services status check succeeded."
      printf '%s
' "$service_status"
    else
      err "Local bot/admin services status check failed: $service_status"
      failed=$((failed + 1))
    fi
  else
    err "Missing $TARGET_DIR/scripts/manage_services.sh"
    failed=$((failed + 1))
  fi

  if nginx -t >/tmp/tgbot-nginx-test.txt 2>/tmp/tgbot-nginx-test.err; then
    ok "Nginx configuration test passed."
  else
    err "Nginx configuration test failed: $(cat /tmp/tgbot-nginx-test.err)"
    failed=$((failed + 1))
  fi

  if [[ -n "${TELEGRAM_BOT_TOKEN:-}" ]]; then
    local webhook_json
    if webhook_json="$(curl -fsS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo" 2>/tmp/tgbot-webhook.err)"; then
      ok "Fetched Telegram webhook info."
      if printf '%s' "$webhook_json" | grep -q '"ok":true'; then
        ok "Telegram API response is ok=true."
      else
        err "Telegram API returned non-ok response: $webhook_json"
        failed=$((failed + 1))
      fi

      if printf '%s' "$webhook_json" | grep -Fq "\"url\":\"${expected_webhook}\""; then
        ok "Webhook URL matches expected value: ${expected_webhook}"
      else
        warn "Webhook URL does not match expected value (${expected_webhook})."
        warn "Current info: $webhook_json"
      fi
    else
      err "Failed to query Telegram webhook info: $(cat /tmp/tgbot-webhook.err)"
      failed=$((failed + 1))
    fi
  else
    err "TELEGRAM_BOT_TOKEN missing in .env"
    failed=$((failed + 1))
  fi

  echo
  if [[ "$failed" -eq 0 ]]; then
    ok "Health check completed successfully."
    exit 0
  fi

  err "Health check completed with ${failed} failing checks."
  exit 1
}

main "$@"
