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
MAX_PLAN_DAYS=$MAX_PLAN_DAYS
MAX_PLAN_GB=$MAX_PLAN_GB
MAX_BULK_COUNT=$MAX_BULK_COUNT
DOMAIN=$DOMAIN
LETSENCRYPT_EMAIL=$LETSENCRYPT_EMAIL
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
  WEBHOOK_PATH="$(prompt_value "Webhook path" "telegram")"
  PANEL_PORT="$(prompt_value "Public admin panel port (for local/non-SSL fallback)" "8080")"
  BOT_PORT="$(prompt_value "Public bot webhook port (for local/non-SSL fallback)" "8443")"
  ADMIN_TELEGRAM_ID="$(prompt_value "Admin Telegram ID (optional)" "")"
  ADMIN_WEB_TOKEN="$(prompt_value "Admin web token" "change-me")"
  WEBHOOK_SECRET_TOKEN="$(prompt_value "Webhook secret token (recommended)" "")"
  MAX_PLAN_DAYS="$(prompt_value "MAX_PLAN_DAYS" "365")"
  MAX_PLAN_GB="$(prompt_value "MAX_PLAN_GB" "2000")"
  MAX_BULK_COUNT="$(prompt_value "MAX_BULK_COUNT" "100")"
  DOMAIN="$(prompt_value "Domain for nginx SSL (leave empty if no SSL)" "")"
  LETSENCRYPT_EMAIL="$(prompt_value "Let's Encrypt account email" "admin@${DOMAIN:-example.com}")"

  mkdir -p "$target_dir/data" "$target_dir/logs" "$target_dir/acme-webroot/.well-known/acme-challenge" "$target_dir/certs"
  write_env_file "$target_dir"

  echo "✅ Saved configuration to $target_dir/.env"
}

manage_certificates() {
  local target_dir="$1"
  local domain

  if [[ -f "$target_dir/.env" ]]; then
    domain="$(awk -F= '/^DOMAIN=/{print $2}' "$target_dir/.env" | tail -n1)"
  fi

  if [[ -z "${domain:-}" ]]; then
    domain="$(prompt_value "Domain for certificate management" "")"
  fi

  if [[ -z "$domain" ]]; then
    echo "No domain provided. Skipping certificate management."
    return
  fi

  if [[ ! -x "$target_dir/scripts/setup_ssl.sh" ]]; then
    echo "Missing $target_dir/scripts/setup_ssl.sh"
    return 1
  fi

  echo "Running certificate management for $domain ..."
  (cd "$target_dir" && ./scripts/setup_ssl.sh "$domain")
}

start_stack() {
  local target_dir="$1"
  (cd "$target_dir" && docker compose up -d --build)
  echo "✅ Docker services started."
}

primary_menu() {
  local target_dir="$1"

  while true; do
    echo
    echo "========== Primary Menu =========="
    echo "1) Configure app (.env)"
    echo "2) Start / restart containers"
    echo "3) Certificate management (acme.sh)"
    echo "4) Full setup (1 -> 2 -> optional 3)"
    echo "5) Exit"

    local choice
    choice="$(prompt_value "Select an option" "4")"

    case "$choice" in
      1) configure_app "$target_dir" ;;
      2) start_stack "$target_dir" ;;
      3) manage_certificates "$target_dir" ;;
      4)
        configure_app "$target_dir"
        start_stack "$target_dir"
        if [[ "$(prompt_value "Run certificate management now? (y/n)" "n")" =~ ^[Yy]$ ]]; then
          manage_certificates "$target_dir"
        else
          echo "Skipped certificate management by user choice."
        fi
        ;;
      5) break ;;
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
  echo "If DOMAIN is configured, access panel via: https://<domain>/admin?token=<ADMIN_WEB_TOKEN>"
}

main "$@"
