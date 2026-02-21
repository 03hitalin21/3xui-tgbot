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

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

[[ -f "$ENV_FILE" ]] || fail "Missing env file: $ENV_FILE"
[[ -f "$TEMPLATE_FILE" ]] || fail "Missing nginx template: $TEMPLATE_FILE"

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${WEBHOOK_PATH:?WEBHOOK_PATH is required in .env}"
: "${SSL_CERT_PATH:?SSL_CERT_PATH is required in .env}"
: "${SSL_KEY_PATH:?SSL_KEY_PATH is required in .env}"
: "${LETSENCRYPT_WEBROOT:?LETSENCRYPT_WEBROOT is required in .env}"
ADMIN_WEB_PORT="${ADMIN_WEB_PORT:-8080}"

require_cmd envsubst
require_cmd nginx

envsubst '$WEBHOOK_PATH $SSL_CERT_PATH $SSL_KEY_PATH $LETSENCRYPT_WEBROOT $ADMIN_WEB_PORT' < "$TEMPLATE_FILE" > "$OUT_FILE"
nginx -t
systemctl enable nginx >/dev/null 2>&1 || true
systemctl restart nginx

echo "✅ Nginx configured and restarted using $OUT_FILE"
