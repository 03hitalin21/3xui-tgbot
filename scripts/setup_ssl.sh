#!/usr/bin/env bash
set -euo pipefail

DOMAIN="${1:-}"
MODE="${2:-}" # --renew-only optional
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEBROOT_DIR="$PROJECT_DIR/acme-webroot/.well-known/acme-challenge"
CERT_BASE_DIR="$PROJECT_DIR/certs"
LIVE_DIR="$CERT_BASE_DIR/live/$DOMAIN"
ACME_HOME="$CERT_BASE_DIR/.acme.sh"
ACCOUNT_EMAIL="${LETSENCRYPT_EMAIL:-admin@${DOMAIN:-example.com}}"

fail() {
  echo "❌ $*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

usage() {
  cat <<USAGE
Usage:
  ./scripts/setup_ssl.sh <domain> [--renew-only]

Examples:
  ./scripts/setup_ssl.sh example.com
  ./scripts/setup_ssl.sh example.com --renew-only
USAGE
}

validate_domain() {
  [[ -n "$DOMAIN" ]] || {
    usage
    fail "Domain argument is required"
  }
}

load_env() {
  if [[ -f "$PROJECT_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$PROJECT_DIR/.env"
    set +a
  fi
}

install_acme_sh_if_missing() {
  if [[ -x "$ACME_HOME/acme.sh" ]]; then
    return
  fi

  echo "Installing acme.sh into $ACME_HOME ..."
  curl -fsSL https://get.acme.sh | sh -s email="$ACCOUNT_EMAIL" --home "$ACME_HOME"
}

check_dns_points_here() {
  local resolved_ips
  local public_ip

  resolved_ips="$(getent ahostsv4 "$DOMAIN" | awk '{print $1}' | sort -u || true)"
  [[ -n "$resolved_ips" ]] || fail "DNS check failed: no A record found for $DOMAIN"

  public_ip="$(curl -4fsS https://api.ipify.org || true)"
  if [[ -z "$public_ip" ]]; then
    echo "⚠️ Could not determine public IPv4 automatically. Skipping strict DNS/IP comparison."
    return
  fi

  if ! grep -qx "$public_ip" <<<"$resolved_ips"; then
    echo "Resolved A records for $DOMAIN:"
    echo "$resolved_ips"
    fail "DNS mismatch. Expected at least one A record matching this server IP: $public_ip"
  fi
}

check_ports() {
  local listeners
  listeners="$(ss -ltn '( sport = :80 or sport = :443 )' | tail -n +2 || true)"

  if [[ -n "$listeners" ]]; then
    echo "Open listeners on 80/443 detected:"
    echo "$listeners"
  fi
}

ensure_dirs() {
  mkdir -p "$WEBROOT_DIR"
  mkdir -p "$LIVE_DIR"
  chmod 700 "$CERT_BASE_DIR"
}

cert_exists() {
  [[ -s "$LIVE_DIR/fullchain.pem" && -s "$LIVE_DIR/privkey.pem" ]]
}

issue_or_renew_cert() {
  local acme_bin="$ACME_HOME/acme.sh"

  if [[ "$MODE" == "--renew-only" ]]; then
    echo "Running acme.sh renewal for $DOMAIN ..."
    "$acme_bin" --home "$ACME_HOME" --renew -d "$DOMAIN" --server letsencrypt || true
  else
    if cert_exists; then
      echo "Certificate already exists at $LIVE_DIR; skipping new issue (idempotent)."
    else
      echo "Issuing certificate for $DOMAIN using HTTP-01 webroot challenge ..."
      "$acme_bin" --home "$ACME_HOME" --issue -d "$DOMAIN" --webroot "$PROJECT_DIR/acme-webroot" --server letsencrypt
    fi
  fi

  echo "Installing certificate files into $LIVE_DIR ..."
  "$acme_bin" --home "$ACME_HOME" --install-cert -d "$DOMAIN" \
    --key-file "$LIVE_DIR/privkey.pem" \
    --fullchain-file "$LIVE_DIR/fullchain.pem" \
    --reloadcmd "cd '$PROJECT_DIR' && docker compose exec nginx nginx -s reload"

  chmod 600 "$LIVE_DIR/privkey.pem"
  chmod 644 "$LIVE_DIR/fullchain.pem"
}

main() {
  validate_domain
  require_cmd curl
  require_cmd docker
  require_cmd ss
  require_cmd getent

  load_env
  ensure_dirs
  install_acme_sh_if_missing

  check_dns_points_here
  check_ports

  if [[ "$MODE" != "--renew-only" ]]; then
    (cd "$PROJECT_DIR" && docker compose up -d nginx)
  fi

  issue_or_renew_cert

  echo "✅ SSL setup/renew completed for $DOMAIN"
}

main "$@"
