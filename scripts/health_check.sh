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

load_env() {
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
}

main() {
  local failed=0

  [[ -f "$ENV_FILE" ]] || { err "Missing env file: $ENV_FILE"; exit 1; }

  require_cmd docker || failed=$((failed + 1))
  require_cmd curl || failed=$((failed + 1))
  load_env

  local expected_webhook="${WEBHOOK_BASE_URL%/}/${WEBHOOK_PATH#/}"

  if [[ -n "${SSL_DOMAIN:-}" ]] && [[ -f "/etc/letsencrypt/live/${SSL_DOMAIN}/fullchain.pem" ]]; then
    ok "TLS certificate file exists for ${SSL_DOMAIN}."
  else
    err "TLS certificate file missing for SSL_DOMAIN=${SSL_DOMAIN:-unset}."
    failed=$((failed + 1))
  fi

  if (cd "$TARGET_DIR" && docker compose ps >/tmp/tgbot-compose-ps.txt 2>/tmp/tgbot-compose-ps.err); then
    ok "docker compose ps succeeded."
    if grep -Eq "tgbot-(bot|admin|nginx)|\b(bot|admin-web|nginx)\b" /tmp/tgbot-compose-ps.txt; then
      ok "Compose services are visible."
    else
      warn "Compose output did not include expected service names."
    fi
  else
    err "docker compose ps failed: $(cat /tmp/tgbot-compose-ps.err)"
    failed=$((failed + 1))
  fi

  if (cd "$TARGET_DIR" && docker compose exec -T nginx nginx -t >/tmp/tgbot-nginx-test.txt 2>/tmp/tgbot-nginx-test.err); then
    ok "Nginx configuration test passed in container."
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
