#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR="${1:-$(pwd)}"
ENV_FILE="$TARGET_DIR/.env"
TEMPLATE_FILE="$TARGET_DIR/nginx/nginx.conf"
OUT_FILE="/etc/nginx/conf.d/tgbot.conf"

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
      export "$pending_key"
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
      export "$key"

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

[[ -f "$ENV_FILE" ]] || fail "Missing env file: $ENV_FILE"
[[ -f "$TEMPLATE_FILE" ]] || fail "Missing nginx template: $TEMPLATE_FILE"

load_env_file

: "${WEBHOOK_PATH:?WEBHOOK_PATH is required in .env}"
: "${SSL_CERT_PATH:?SSL_CERT_PATH is required in .env}"
: "${SSL_KEY_PATH:?SSL_KEY_PATH is required in .env}"
: "${LETSENCRYPT_WEBROOT:?LETSENCRYPT_WEBROOT is required in .env}"
WEBHOOK_PORT="${WEBHOOK_PORT:-8443}"
ADMIN_WEB_PORT="${ADMIN_WEB_PORT:-8080}"

require_cmd envsubst
require_cmd nginx

envsubst '$WEBHOOK_PATH $SSL_CERT_PATH $SSL_KEY_PATH $LETSENCRYPT_WEBROOT $WEBHOOK_PORT $ADMIN_WEB_PORT' < "$TEMPLATE_FILE" > "$OUT_FILE"
nginx -t
systemctl enable nginx >/dev/null 2>&1 || true
systemctl restart nginx

echo "✅ Nginx configured and restarted using $OUT_FILE"
