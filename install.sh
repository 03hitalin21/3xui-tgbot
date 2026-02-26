#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/03hitalin21/3xui-tgbot.git"
TARGET_DIR_DEFAULT="${HOME}/3xui-tgbot"
REQUIRED_ENV_VARS=(
  TELEGRAM_BOT_TOKEN
  XUI_BASE_URL
  XUI_USERNAME
  XUI_PASSWORD
  XUI_SERVER_HOST
  WEBHOOK_BASE_URL
)

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

require_root_or_sudo() {
  if [[ "${EUID}" -eq 0 ]]; then
    SUDO=""
  elif command_exists sudo; then
    SUDO="sudo"
  else
    echo "This installer needs root privileges to install packages and configure nginx. Please run as root."
    exit 1
  fi
}

install_runtime_if_missing() {
  if command_exists apt-get; then
    ${SUDO:-} apt-get update
    ${SUDO:-} apt-get install -y python3 python3-venv python3-pip nginx gettext-base curl git systemd
  elif command_exists dnf; then
    ${SUDO:-} dnf install -y python3 python3-pip nginx gettext curl git systemd
  elif command_exists yum; then
    ${SUDO:-} yum install -y python3 python3-pip nginx gettext curl git systemd
  else
    echo "Unsupported package manager. Install python3, pip, nginx, gettext, curl, git and systemd manually."
    exit 1
  fi
}

prepare_venv() {
  local target_dir="$1"
  if [[ ! -d "$target_dir/.venv" ]]; then
    python3 -m venv "$target_dir/.venv"
  fi
  "$target_dir/.venv/bin/pip" install --upgrade pip
  "$target_dir/.venv/bin/pip" install -r "$target_dir/requirements.txt"
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


sanitize_env_value() {
  local value="$1"
  value="$(printf '%s' "$value" | tr -d '\r\n')"
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

is_installed() {
  local target_dir="$1"
  [[ -d "$target_dir/.git" ]]
}

detect_default_branch() {
  local target_dir="$1"
  local branch

  branch="$(git -C "$target_dir" symbolic-ref -q --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##')"
  if [[ -n "$branch" ]]; then
    printf '%s' "$branch"
    return 0
  fi

  branch="$(git -C "$target_dir" ls-remote --symref origin HEAD 2>/dev/null | awk '/^ref:/ {sub("refs/heads/", "", $2); print $2; exit}')"
  if [[ -n "$branch" ]]; then
    printf '%s' "$branch"
    return 0
  fi

  return 1
}

stop_services_for_upgrade() {
  local target_dir="$1"
  local systemd_script="$target_dir/scripts/setup_systemd.sh"

  echo "[INFO] Stopping running services..."
  if command_exists systemctl && [[ -x "$systemd_script" ]]; then
    if ! (cd "$target_dir" && ./scripts/setup_systemd.sh stop "$target_dir"); then
      echo "[ERROR] Failed to stop systemd services."
      return 1
    fi
    return 0
  fi

  if [[ -x "$target_dir/scripts/manage_services.sh" ]]; then
    if ! (cd "$target_dir" && ./scripts/manage_services.sh stop "$target_dir"); then
      echo "[ERROR] Failed to stop script-managed services."
      return 1
    fi
    return 0
  fi

  echo "[INFO] No service management scripts found; continuing."
  return 0
}

start_services_for_upgrade() {
  local target_dir="$1"
  local systemd_script="$target_dir/scripts/setup_systemd.sh"

  echo "[INFO] Starting services..."
  if command_exists systemctl && [[ -x "$systemd_script" ]]; then
    (cd "$target_dir" && ./scripts/setup_systemd.sh restart "$target_dir")
    return
  fi

  (cd "$target_dir" && ./scripts/manage_services.sh start "$target_dir")
}

backup_upgrade_state() {
  local target_dir="$1"
  local ts backup_dir
  ts="$(date +%Y%m%d-%H%M%S)"
  backup_dir="$target_dir/backups/$ts"
  mkdir -p "$backup_dir"

  echo "[INFO] Creating backup at $backup_dir"
  [[ -f "$target_dir/.env" ]] && cp -a "$target_dir/.env" "$backup_dir/.env"
  [[ -f "$target_dir/agents.json" ]] && cp -a "$target_dir/agents.json" "$backup_dir/agents.json"
  [[ -f "$target_dir/data/bot.db" ]] && cp -a "$target_dir/data/bot.db" "$backup_dir/bot.db"
  [[ -d "$target_dir/data" ]] && cp -a "$target_dir/data" "$backup_dir/data"

  echo "[INFO] Backup complete"
}

prompt_missing_env_vars() {
  local target_dir="$1"
  local env_file="$target_dir/.env"
  local updated=false

  [[ -f "$env_file" ]] || return 1

  for key in "${REQUIRED_ENV_VARS[@]}"; do
    if ! grep -Eq "^${key}=" "$env_file"; then
      [[ "$updated" == false ]] && cp -a "$env_file" "$env_file.bak"
      local value
      value="$(sanitize_env_value "$(prompt_value "Missing required env var ${key}" "")")"
      echo "${key}=${value}" >> "$env_file"
      updated=true
    fi
  done

  return 0
}

run_db_migrations() {
  local target_dir="$1"
  local db_path="$target_dir/data/bot.db"
  mkdir -p "$target_dir/data"
  echo "[INFO] Running database migrations on $db_path"
  BOT_DB_PATH="$db_path" "$target_dir/.venv/bin/python" "$target_dir/scripts/migrate_db.py" "$db_path"
}

upgrade_existing_installation() {
  local target_dir="$1"
  local branch

  branch="$(detect_default_branch "$target_dir")" || {
    echo "[ERROR] Could not detect origin default branch."
    return 1
  }

  echo "[INFO] Detected default branch: $branch"
  stop_services_for_upgrade "$target_dir" || return 1
  backup_upgrade_state "$target_dir" || return 1

  echo "[INFO] Fetching latest code..."
  git -C "$target_dir" fetch --all --prune
  git -C "$target_dir" checkout "$branch"
  if ! git -C "$target_dir" pull --ff-only origin "$branch"; then
    echo "[ERROR] Fast-forward pull failed. Services remain stopped for safety."
    return 1
  fi

  prepare_venv "$target_dir"
  run_db_migrations "$target_dir"

  if [[ ! -f "$target_dir/.env" ]]; then
    echo "[INFO] .env is missing; entering existing interactive setup menu."
    primary_menu "$target_dir"
    return 0
  fi

  prompt_missing_env_vars "$target_dir"
  start_services_for_upgrade "$target_dir"

  echo "[INFO] Upgrade completed successfully."
  return 0
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
  TELEGRAM_BOT_TOKEN="$(sanitize_env_value "$(prompt_value "Telegram Bot Token" "" true)")"
  XUI_BASE_URL="$(sanitize_env_value "$(prompt_value "XUI Panel Base URL (e.g. https://panel.example.com:2053/panel)" "")")"
  XUI_USERNAME="$(sanitize_env_value "$(prompt_value "XUI Username" "admin")")"
  XUI_PASSWORD="$(sanitize_env_value "$(prompt_value "XUI Password" "admin" true)")"
  XUI_SERVER_HOST="$(sanitize_env_value "$(prompt_value "XUI Server Host/IP" "127.0.0.1")")"
  WEBHOOK_BASE_URL="$(sanitize_env_value "$(prompt_value "Public webhook base URL (e.g. https://bot.example.com)" "")")"
  SSL_DOMAIN="$(sanitize_env_value "$(prompt_value "Primary domain for TLS (e.g. bot.example.com or example.com)" "")")"
  if [[ -z "${WEBHOOK_BASE_URL}" && -n "${SSL_DOMAIN}" ]]; then
    WEBHOOK_BASE_URL="https://${SSL_DOMAIN}"
  fi

  [[ -n "$TELEGRAM_BOT_TOKEN" ]] || { echo "Telegram Bot Token cannot be empty."; return 1; }
  [[ -n "$XUI_BASE_URL" ]] || { echo "XUI_BASE_URL cannot be empty."; return 1; }
  [[ -n "$XUI_USERNAME" ]] || { echo "XUI_USERNAME cannot be empty."; return 1; }
  [[ -n "$XUI_PASSWORD" ]] || { echo "XUI_PASSWORD cannot be empty."; return 1; }
  [[ -n "$XUI_SERVER_HOST" ]] || { echo "XUI_SERVER_HOST cannot be empty."; return 1; }
  [[ -n "$SSL_DOMAIN" ]] || { echo "SSL_DOMAIN cannot be empty."; return 1; }
  SSL_INCLUDE_WWW="$(prompt_value "Include www.<domain> in certificate? Set true only if DNS exists (true/false)" "false")"
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


setup_systemd_services() {
  local target_dir="$1"

  if [[ ! -x "$target_dir/scripts/setup_systemd.sh" ]]; then
    echo "Missing $target_dir/scripts/setup_systemd.sh"
    return 1
  fi

  if ! command_exists systemctl; then
    echo "⚠️ systemd/systemctl not available. Falling back to script-based process management."
    return 1
  fi

  (cd "$target_dir" && ./scripts/setup_systemd.sh install "$target_dir")
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

  prepare_venv "$target_dir"
  if run_menu_action "Systemd integration" setup_systemd_services "$target_dir"; then
    echo "✅ Bot/Admin services are running under systemd supervision."
  else
    (cd "$target_dir" && ./scripts/manage_services.sh restart "$target_dir")
    echo "⚠️ Bot/Admin started without systemd supervision (fallback mode)."
  fi
  (cd "$target_dir" && ./scripts/configure_nginx.sh "$target_dir")
  echo "✅ Host services started (bot/admin/nginx)."

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
    echo "4) Start / restart host services (systemd if available)"
    echo "5) Set Telegram webhook now"
    echo "6) Run health checks (TLS, services, nginx, webhook)"
    echo "7) Full setup (1 -> 2 -> 4 -> 6)"
    echo "8) Exit"

    local choice
    choice="$(prompt_value "Select an option" "8")"

    case "$choice" in
      1) run_menu_action "Configure app" configure_app "$target_dir" || true ;;
      2) run_menu_action "TLS certificate setup" setup_ssl "$target_dir" || true ;;
      3) run_menu_action "TLS certificate status check" check_tls_certificate "$target_dir" || true ;;
      4) run_menu_action "Service startup" start_stack "$target_dir" || true ;;
      5) run_menu_action "Webhook setup" set_webhook "$target_dir" || true ;;
      6) run_menu_action "Health checks" run_health_check "$target_dir" || true ;;
      7)
        run_menu_action "Configure app" configure_app "$target_dir" || true
        run_menu_action "TLS certificate setup" setup_ssl "$target_dir" || true
        run_menu_action "Service startup" start_stack "$target_dir" || true
        run_menu_action "Health checks" run_health_check "$target_dir" || true
        ;;
      8) break ;;
      *) echo "Invalid option." ;;
    esac
  done
}

main() {
  require_root_or_sudo
  install_runtime_if_missing

  local target_dir="${1:-$TARGET_DIR_DEFAULT}"

  if is_installed "$target_dir"; then
    echo "[INFO] Existing installation found at $target_dir. Running in-place upgrade."
    if upgrade_existing_installation "$target_dir"; then
      echo "✅ Upgrade finished."
      exit 0
    fi
    exit 1
  fi

  target_dir="$(prompt_value "Install directory" "$TARGET_DIR_DEFAULT")"

  clone_or_update_repo "$target_dir"
  primary_menu "$target_dir"

  echo
  echo "✅ Installer completed."
  echo "Project directory: $target_dir"
  echo "Access panel via your HTTPS domain reverse proxy at /admin?token=<ADMIN_WEB_TOKEN>"
}

main "$@"
