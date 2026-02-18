#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-acquire}"
TARGET_DIR="${2:-$(pwd)}"

if [[ "$MODE" != "acquire" && "$MODE" != "check" ]]; then
  TARGET_DIR="${1:-$(pwd)}"
  MODE="acquire"
fi

ENV_FILE="$TARGET_DIR/.env"
CERTBOT_WEBROOT="$TARGET_DIR/certbot/www"

fail() {
  echo "❌ $*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

is_true() {
  [[ "${1:-}" =~ ^(1|true|TRUE|yes|YES|y|Y)$ ]]
}

ensure_env_file() {
  [[ -f "$ENV_FILE" ]] || fail "Missing env file: $ENV_FILE. Run app configuration first."
}

load_env() {
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
}

write_env_key() {
  local key="$1"
  local value="$2"

  if grep -qE "^${key}=" "$ENV_FILE"; then
    sed -i "s#^${key}=.*#${key}=${value}#" "$ENV_FILE"
  else
    printf '\n%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

install_certbot_if_needed() {
  if command -v certbot >/dev/null 2>&1; then
    return
  fi

  echo "Installing Certbot..."
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y certbot
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y certbot
  elif command -v yum >/dev/null 2>&1; then
    yum install -y certbot
  else
    fail "Could not detect package manager to install certbot. Install certbot manually and re-run."
  fi
}

show_certificate_status() {
  local domain="$1"
  local cert_path="/etc/letsencrypt/live/${domain}/fullchain.pem"
  local key_path="/etc/letsencrypt/live/${domain}/privkey.pem"

  if [[ ! -f "$cert_path" || ! -f "$key_path" ]]; then
    echo "❌ TLS certificate not found for ${domain}."
    echo "   Expected: ${cert_path}"
    return 1
  fi

  echo "✅ TLS certificate found for ${domain}."
  echo "   Cert: ${cert_path}"
  echo "   Key : ${key_path}"

  if command -v openssl >/dev/null 2>&1; then
    local end_date
    end_date="$(openssl x509 -enddate -noout -in "$cert_path" | sed 's/notAfter=//')"
    if [[ -n "$end_date" ]]; then
      echo "   Expires: ${end_date}"
    fi
  fi

  return 0
}

ensure_certificates() {
  local domain="$1"
  local include_www="$2"
  local email="$3"

  if show_certificate_status "$domain" >/dev/null; then
    echo "✅ Existing certificate found for ${domain}."
    return
  fi

  echo "No existing certificate detected for ${domain}. Requesting Let's Encrypt certificate..."
  install_certbot_if_needed
  mkdir -p "$CERTBOT_WEBROOT"

  local domains=(-d "$domain")
  if is_true "$include_www"; then
    domains+=( -d "www.${domain}" )
  fi

  certbot certonly \
    --webroot \
    -w "$CERTBOT_WEBROOT" \
    "${domains[@]}" \
    --agree-tos \
    --email "$email" \
    --non-interactive \
    --keep-until-expiring

  show_certificate_status "$domain" || fail "Certificate generation finished but files were not found in /etc/letsencrypt/live/${domain}/"
  echo "✅ SSL certificate issued for ${domain}."
}

configure_ssl_env() {
  local domain="$1"
  local include_www="$2"

  write_env_key "SSL_ENABLED" "true"
  write_env_key "SSL_DOMAIN" "$domain"
  write_env_key "SSL_INCLUDE_WWW" "$include_www"
  write_env_key "SSL_CERT_PATH" "/etc/letsencrypt/live/${domain}/fullchain.pem"
  write_env_key "SSL_KEY_PATH" "/etc/letsencrypt/live/${domain}/privkey.pem"
  write_env_key "LETSENCRYPT_WEBROOT" "/var/www/certbot"

  local webhook_base="https://${domain}"
  write_env_key "WEBHOOK_BASE_URL" "$webhook_base"

  echo "✅ Updated SSL settings in $ENV_FILE"
}

main() {
  require_cmd sed
  require_cmd grep

  ensure_env_file
  load_env

  local domain="${SSL_DOMAIN:-${3:-}}"
  if [[ -z "$domain" ]]; then
    fail "Domain is required. Set SSL_DOMAIN in .env or pass as argument."
  fi

  if [[ "$MODE" == "check" ]]; then
    show_certificate_status "$domain"
    return
  fi

  local include_www="${SSL_INCLUDE_WWW:-true}"
  local email="${LETSENCRYPT_EMAIL:-}"
  if [[ -z "$email" ]]; then
    fail "LETSENCRYPT_EMAIL is required in .env for certificate issuance/renewal notices."
  fi

  mkdir -p "$CERTBOT_WEBROOT"
  configure_ssl_env "$domain" "$include_www"
  ensure_certificates "$domain" "$include_www" "$email"

  echo "You can now (re)start the stack: docker compose up -d --build"
}

main "$@"
