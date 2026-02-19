#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/03hitalin21/3xui-tgbot.git"
TARGET_DIR_DEFAULT="${HOME}/3xui-tgbot"

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

require_root_or_sudo() {
  if [[ "${EUID}" -eq 0 ]]; then
    SUDO=""
  elif command_exists sudo; then
    SUDO="sudo"
  else
    echo "This installer needs root privileges to install Docker. Please run as root."
    exit 1
  fi
}

install_docker_if_missing() {
  if command_exists docker; then
    echo "Docker is already installed."
    return
  fi

  echo "Docker not found. Installing Docker..."
  curl -fsSL https://get.docker.com | sh

  if [[ -n "${SUDO:-}" && "${USER}" != "root" ]]; then
    ${SUDO} usermod -aG docker "${USER}" || true
  fi
}

install_compose_if_missing() {
  if docker compose version >/dev/null 2>&1; then
    echo "Docker Compose plugin is already available."
    return
  fi

  echo "Docker Compose plugin not found. Installing plugin..."
  ${SUDO:-} apt-get update
  ${SUDO:-} apt-get install -y docker-compose-plugin
}

prompt_value() {
  local prompt="$1"
  local default_value="$2"
  local secret="${3:-false}"
  local value

  if [[ "$secret" == "true" ]]; then
    read -r -s -p "$prompt [$default_value]: " value
    echo
  else
    read -r -p "$prompt [$default_value]: " value
  fi

  if [[ -z "$value" ]]; then
    value="$default_value"
  fi

  printf '%s' "$value"
}

generate_token() {
  local length="${1:-32}"

  if command_exists openssl; then
    openssl rand -hex "$length"
  else
    od -An -N"$length" -tx1 /dev/urandom | tr -d ' \n'
  fi
}

clone_or_update_repo() {
  local target_dir="$1"

  if [[ -d "$target_dir/.git" ]]; then
    echo "Repository already exists at $target_dir. Pulling latest changes..."
    git -C "$target_dir" pull --ff-only
  else
    echo "Cloning repository into $target_dir..."
    git clone "$REPO_URL" "$target_dir"
  fi
}

write_env_file() {
  local target_dir="$1"

  cat > "$target_dir/.env" <<ENVEOF
TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN
XUI_BASE_URL=$XUI_BASE_URL
XUI_USERNAME=$XUI_USERNAME
XUI_PASSWORD=$XUI_PASSWORD
XUI_SERVER_HOST=$XUI_SERVER_HOST
ADMIN_TELEGRAM_ID=$ADMIN_TELEGRAM_ID
ADMIN_WEB_TOKEN=$ADMIN_WEB_TOKEN
ADMIN_WEB_PORT=8080
PANEL_PORT=$PANEL_PORT
BOT_PORT=$BOT_PORT
WEBHOOK_BASE_URL=$WEBHOOK_BASE_URL
WEBHOOK_PATH=$WEBHOOK_PATH
WEBHOOK_LISTEN=0.0.0.0
WEBHOOK_PORT=8443
WEBHOOK_SECRET_TOKEN=$WEBHOOK_SECRET_TOKEN
SSL_ENABLED=$SSL_ENABLED
SSL_DOMAIN=$SSL_DOMAIN
SSL_INCLUDE_WWW=$SSL_INCLUDE_WWW
SSL_CERTBOT_MODE=$SSL_CERTBOT_MODE
SSL_CERT_PATH=$SSL_CERT_PATH
SSL_KEY_PATH=$SSL_KEY_PATH
LETSENCRYPT_EMAIL=$LETSENCRYPT_EMAIL
LETSENCRYPT_WEBROOT=/var/www/certbot
MAX_PLAN_DAYS=$MAX_PLAN_DAYS
MAX_PLAN_GB=$MAX_PLAN_GB
MAX_BULK_COUNT=$MAX_BULK_COUNT
ENVEOF
}

configure_app() {
  local target_dir="$1"

  echo
  echo "--- 3xui Telegram Bot Configuration ---"
  TELEGRAM_BOT_TOKEN="$(prompt_value "Telegram Bot Token" "" true)"
  XUI_BASE_URL="$(prompt_value "XUI Panel Base URL (e.g. https://panel.example.com:2053/panel)" "")"
  XUI_USERNAME="$(prompt_value "XUI Username" "admin")"
  XUI_PASSWORD="$(prompt_value "XUI Password" "admin" true)"
  XUI_SERVER_HOST="$(prompt_value "XUI Server Host/IP" "127.0.0.1")"
  WEBHOOK_BASE_URL="$(prompt_value "Public webhook base URL (e.g. https://bot.example.com)" "")"
  SSL_DOMAIN="$(prompt_value "Primary domain for TLS (e.g. mehrsway.space)" "")"
  if [[ -z "${WEBHOOK_BASE_URL}" && -n "${SSL_DOMAIN}" ]]; then
    WEBHOOK_BASE_URL="https://${SSL_DOMAIN}"
  fi
  SSL_INCLUDE_WWW="$(prompt_value "Include www subdomain in certificate? (true/false)" "true")"
  SSL_CERTBOT_MODE="standalone"
  LETSENCRYPT_EMAIL="$(prompt_value "Email for Let's Encrypt notices" "")"
  SSL_ENABLED="true"
  SSL_CERT_PATH="/etc/letsencrypt/live/${SSL_DOMAIN}/fullchain.pem"
  SSL_KEY_PATH="/etc/letsencrypt/live/${SSL_DOMAIN}/privkey.pem"
  WEBHOOK_PATH="telegram/$(generate_token 8)"
  PANEL_PORT="8080"
  BOT_PORT="8443"
  ADMIN_TELEGRAM_ID="$(prompt_value "Admin Telegram ID (optional)" "")"
  ADMIN_WEB_TOKEN="$(generate_token 24)"
  WEBHOOK_SECRET_TOKEN="$(generate_token 24)"
  echo "Generated backend tokens, webhook path, and service ports automatically."
  MAX_PLAN_DAYS="$(prompt_value "MAX_PLAN_DAYS" "365")"
  MAX_PLAN_GB="$(prompt_value "MAX_PLAN_GB" "2000")"
  MAX_BULK_COUNT="$(prompt_value "MAX_BULK_COUNT" "100")"
  mkdir -p "$target_dir/data" "$target_dir/logs"
  write_env_file "$target_dir"

  local admin_panel_base
  admin_panel_base="${WEBHOOK_BASE_URL%/}"
  local admin_panel_url="${admin_panel_base}/admin?token=${ADMIN_WEB_TOKEN}"
  local webhook_url="${admin_panel_base}/${WEBHOOK_PATH#/}"

  echo "export ADMIN_WEB_TOKEN=${ADMIN_WEB_TOKEN}"
  echo "export WEBHOOK_SECRET_TOKEN=${WEBHOOK_SECRET_TOKEN}"
  echo "export WEBHOOK_PATH=${WEBHOOK_PATH}"
  echo "export PANEL_PORT=${PANEL_PORT}"
  echo "export BOT_PORT=${BOT_PORT}"

  echo "✅ Saved configuration to $target_dir/.env"
  echo "🔐 Admin token: $ADMIN_WEB_TOKEN"
  echo "🔐 Webhook token: $WEBHOOK_SECRET_TOKEN"
  echo "🌐 Admin panel URL: $admin_panel_url"
  echo "🪝 Webhook URL: $webhook_url"
}

set_webhook() {
  local target_dir="$1"

  if [[ ! -x "$target_dir/scripts/set_webhook.sh" ]]; then
    echo "Missing $target_dir/scripts/set_webhook.sh"
    return 1
  fi

  (cd "$target_dir" && ./scripts/set_webhook.sh "$target_dir")
}


setup_ssl() {
  local target_dir="$1"

  if [[ ! -x "$target_dir/scripts/setup_ssl.sh" ]]; then
    echo "Missing $target_dir/scripts/setup_ssl.sh"
    return 1
  fi

  (cd "$target_dir" && ./scripts/setup_ssl.sh acquire "$target_dir")
}

check_tls_certificate() {
  local target_dir="$1"

  if [[ ! -x "$target_dir/scripts/setup_ssl.sh" ]]; then
    echo "Missing $target_dir/scripts/setup_ssl.sh"
    return 1
  fi

  (cd "$target_dir" && ./scripts/setup_ssl.sh check "$target_dir")
}

run_menu_action() {
  local label="$1"
  shift

  if "$@"; then
    return 0
  fi

  echo "⚠️ ${label} failed. Review the error above and try again."
  return 1
}

run_health_check() {
  local target_dir="$1"

  if [[ ! -x "$target_dir/scripts/health_check.sh" ]]; then
    echo "Missing $target_dir/scripts/health_check.sh"
    return 1
  fi

  (cd "$target_dir" && ./scripts/health_check.sh "$target_dir")
}

start_stack() {
  local target_dir="$1"
  (cd "$target_dir" && docker compose up -d --build)
  echo "✅ Docker services started."

  if [[ -x "$target_dir/scripts/set_webhook.sh" ]]; then
    echo "🔄 Auto-registering Telegram webhook..."
    if (cd "$target_dir" && ./scripts/set_webhook.sh "$target_dir"); then
      echo "✅ Webhook registration completed."
    else
      echo "⚠️ Webhook registration failed. You can retry from the menu (Set Telegram webhook now)."
    fi
  fi
}

primary_menu() {
  local target_dir="$1"

  while true; do
    echo
    echo "========== Primary Menu =========="
    echo "1) Configure app (.env)"
    echo "2) Acquire/configure TLS certificate (Let's Encrypt)"
    echo "3) Check TLS certificate status"
    echo "4) Start / restart containers"
    echo "5) Set Telegram webhook now"
    echo "6) Run health checks (TLS, containers, nginx, webhook)"
    echo "7) Full setup (1 -> 2 -> 4 -> 6)"
    echo "8) Exit"

    local choice
    choice="$(prompt_value "Select an option" "8")"

    case "$choice" in
      1) run_menu_action "Configure app" configure_app "$target_dir" || true ;;
      2) run_menu_action "TLS certificate setup" setup_ssl "$target_dir" || true ;;
      3) run_menu_action "TLS certificate status check" check_tls_certificate "$target_dir" || true ;;
      4) run_menu_action "Container startup" start_stack "$target_dir" || true ;;
      5) run_menu_action "Webhook setup" set_webhook "$target_dir" || true ;;
      6) run_menu_action "Health checks" run_health_check "$target_dir" || true ;;
      7)
        run_menu_action "Configure app" configure_app "$target_dir" || true
        run_menu_action "TLS certificate setup" setup_ssl "$target_dir" || true
        run_menu_action "Container startup" start_stack "$target_dir" || true
        run_menu_action "Health checks" run_health_check "$target_dir" || true
        ;;
      8) break ;;
      *) echo "Invalid option." ;;
    esac
  done
}

main() {
  require_root_or_sudo
  install_docker_if_missing
  install_compose_if_missing

  local target_dir
  target_dir="$(prompt_value "Install directory" "$TARGET_DIR_DEFAULT")"

  clone_or_update_repo "$target_dir"
  primary_menu "$target_dir"

  echo
  echo "✅ Installer completed."
  echo "Project directory: $target_dir"
  echo "Access panel via your HTTPS domain reverse proxy at /admin?token=<ADMIN_WEB_TOKEN>"
}

main "$@"
