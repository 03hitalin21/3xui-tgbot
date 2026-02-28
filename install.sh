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

INTERACTIVE=false
TARGET_DIR=""
MODE="full" # full|deps-only|deploy-only

command_exists() { command -v "$1" >/dev/null 2>&1; }
log_step() { echo "[STEP] $*"; }

usage() {
  cat <<USAGE
Usage: bash install.sh [options] [target_dir]

Options:
  --interactive   Enable prompt-driven setup/menu.
  --deps-only     Install OS/runtime dependencies only.
  --deploy-only   Deploy/start services without dependency install.
  --full          Full flow (default).
  --target-dir D  Set install directory.
  -h, --help      Show this help.
USAGE
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --interactive) INTERACTIVE=true; shift ;;
      --deps-only) MODE="deps-only"; shift ;;
      --deploy-only) MODE="deploy-only"; shift ;;
      --full) MODE="full"; shift ;;
      --target-dir) TARGET_DIR="$2"; shift 2 ;;
      -h|--help) usage; exit 0 ;;
      *)
        if [[ -z "$TARGET_DIR" ]]; then
          TARGET_DIR="$1"
          shift
        else
          echo "Unknown argument: $1"
          usage
          exit 1
        fi
        ;;
    esac
  done
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

install_deps() {
  log_step "Installing runtime dependencies"
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

sanitize_env_value() {
  local value="$1"
  value="$(printf '%s' "$value" | tr -d '\r\n')"
  printf '%s' "$value"
}

generate_token() {
  local length="${1:-32}"
  if command_exists openssl; then openssl rand -hex "$length"; else od -An -N"$length" -tx1 /dev/urandom | tr -d ' \n'; fi
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

is_installed() { [[ -d "$1/.git" ]]; }

detect_default_branch() {
  local target_dir="$1" branch
  branch="$(git -C "$target_dir" symbolic-ref -q --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##')"
  [[ -n "$branch" ]] && { printf '%s' "$branch"; return 0; }
  branch="$(git -C "$target_dir" ls-remote --symref origin HEAD 2>/dev/null | awk '/^ref:/ {sub("refs/heads/", "", $2); print $2; exit}')"
  [[ -n "$branch" ]] && { printf '%s' "$branch"; return 0; }
  return 1
}

stop_services_for_upgrade() {
  local target_dir="$1" systemd_script="$target_dir/scripts/setup_systemd.sh"
  echo "[INFO] Stopping running services..."
  if command_exists systemctl && [[ -x "$systemd_script" ]]; then
    (cd "$target_dir" && ./scripts/setup_systemd.sh stop "$target_dir") && return 0
  fi
  if [[ -x "$target_dir/scripts/manage_services.sh" ]]; then
    (cd "$target_dir" && ./scripts/manage_services.sh stop "$target_dir") && return 0
  fi
  echo "[INFO] No service management scripts found; continuing."
}

restart_services() {
  local target_dir="$1" systemd_script="$target_dir/scripts/setup_systemd.sh"
  log_step "Restarting services"
  if command_exists systemctl && [[ -x "$systemd_script" ]]; then
    (cd "$target_dir" && ./scripts/setup_systemd.sh restart "$target_dir")
  else
    (cd "$target_dir" && ./scripts/manage_services.sh restart "$target_dir")
  fi
}

backup() {
  local target_dir="$1" ts backup_dir
  ts="$(date +%Y%m%d-%H%M%S)"
  backup_dir="$target_dir/backups/$ts"
  mkdir -p "$backup_dir"
  echo "[INFO] Creating backup at $backup_dir"
  [[ -f "$target_dir/.env" ]] && cp -a "$target_dir/.env" "$backup_dir/.env"
  [[ -f "$target_dir/agents.json" ]] && cp -a "$target_dir/agents.json" "$backup_dir/agents.json"
  [[ -f "$target_dir/data/bot.db" ]] && cp -a "$target_dir/data/bot.db" "$backup_dir/bot.db"
  [[ -d "$target_dir/data" ]] && cp -a "$target_dir/data" "$backup_dir/data"
}

migrate_db() {
  local target_dir="$1" db_path="$target_dir/data/bot.db"
  mkdir -p "$target_dir/data"
  log_step "Running migrations on $db_path"
  BOT_DB_PATH="$db_path" "$target_dir/.venv/bin/python" "$target_dir/scripts/migrate_db.py" "$db_path"
}

prompt_missing_env_vars() {
  local target_dir="$1" env_file="$target_dir/.env" updated=false
  [[ -f "$env_file" ]] || return 1
  for key in "${REQUIRED_ENV_VARS[@]}"; do
    if ! grep -Eq "^${key}=" "$env_file"; then
      if [[ "$INTERACTIVE" != "true" ]]; then
        echo "[WARN] Missing required env var in .env: $key"
        continue
      fi
      [[ "$updated" == false ]] && cp -a "$env_file" "$env_file.bak"
      read -r -p "Missing required env var ${key}: " value
      echo "${key}=$(sanitize_env_value "$value")" >> "$env_file"
      updated=true
    fi
  done
}

setup_env() {
  local target_dir="$1"
  if [[ -f "$target_dir/.env" ]]; then
    prompt_missing_env_vars "$target_dir" || true
    return 0
  fi

  if [[ "$INTERACTIVE" == "true" ]]; then
    echo "Launching interactive setup menu..."
    primary_menu "$target_dir"
    return 0
  fi

  if [[ -f "$target_dir/.env.example" ]]; then
    cp "$target_dir/.env.example" "$target_dir/.env"
  elif [[ -f "$target_dir/config/bot.env.example" ]]; then
    cp "$target_dir/config/bot.env.example" "$target_dir/.env"
  else
    : > "$target_dir/.env"
  fi

  sed -i "s|^ADMIN_WEB_TOKEN=$|ADMIN_WEB_TOKEN=$(generate_token 24)|" "$target_dir/.env" || true
  sed -i "s|^WEBHOOK_SECRET_TOKEN=$|WEBHOOK_SECRET_TOKEN=$(generate_token 24)|" "$target_dir/.env" || true
  echo "[INFO] Created $target_dir/.env from example (non-interactive)."
  echo "[INFO] Fill required values (${REQUIRED_ENV_VARS[*]}) then re-run install.sh or start services manually."
}

set_webhook() {
  local target_dir="$1"
  [[ -x "$target_dir/scripts/set_webhook.sh" ]] || return 0
  (cd "$target_dir" && ./scripts/set_webhook.sh "$target_dir") || true
}

setup_ssl() {
  local target_dir="$1"
  [[ -x "$target_dir/scripts/setup_ssl.sh" ]] || return 0
  (cd "$target_dir" && ./scripts/setup_ssl.sh acquire "$target_dir")
}

setup_systemd_services() {
  local target_dir="$1"
  [[ -x "$target_dir/scripts/setup_systemd.sh" ]] || return 1
  command_exists systemctl || return 1
  (cd "$target_dir" && ./scripts/setup_systemd.sh install "$target_dir")
}

setup_services() {
  local target_dir="$1"
  log_step "Configuring services"
  prepare_venv "$target_dir"
  if ! setup_systemd_services "$target_dir"; then
    (cd "$target_dir" && ./scripts/manage_services.sh restart "$target_dir")
  fi
  (cd "$target_dir" && ./scripts/configure_nginx.sh "$target_dir")
}

upgrade_existing_installation() {
  local target_dir="$1" branch
  branch="$(detect_default_branch "$target_dir")" || { echo "[ERROR] Could not detect origin default branch."; return 1; }
  echo "[INFO] Detected default branch: $branch"
  stop_services_for_upgrade "$target_dir" || true
  backup "$target_dir"
  git -C "$target_dir" fetch --all --prune
  git -C "$target_dir" checkout "$branch"
  git -C "$target_dir" pull --ff-only origin "$branch"
  prepare_venv "$target_dir"
  migrate_db "$target_dir"
  setup_env "$target_dir"
  restart_services "$target_dir"
  echo "[INFO] Upgrade completed successfully."
}

primary_menu() {
  local target_dir="$1"
  while true; do
    echo
    echo "========== Primary Menu =========="
    echo "1) Configure app (.env)"
    echo "2) Acquire/configure TLS certificate (Let's Encrypt)"
    echo "3) Start / restart host services"
    echo "4) Set Telegram webhook now"
    echo "5) Exit"
    read -r -p "Select an option [5]: " choice
    choice="${choice:-5}"
    case "$choice" in
      1) setup_env "$target_dir" ;;
      2) setup_ssl "$target_dir" ;;
      3) setup_services "$target_dir"; restart_services "$target_dir" ;;
      4) set_webhook "$target_dir" ;;
      5) break ;;
      *) echo "Invalid option." ;;
    esac
  done
}

main() {
  parse_args "$@"
  require_root_or_sudo

  local target_dir="${TARGET_DIR:-$TARGET_DIR_DEFAULT}"

  [[ "$MODE" != "deploy-only" ]] && install_deps
  [[ "$MODE" == "deps-only" ]] && exit 0

  if is_installed "$target_dir"; then
    upgrade_existing_installation "$target_dir"
    exit 0
  fi

  clone_or_update_repo "$target_dir"
  setup_env "$target_dir"
  prepare_venv "$target_dir"
  migrate_db "$target_dir"
  setup_services "$target_dir"
  restart_services "$target_dir" || true
  set_webhook "$target_dir" || true

  if [[ "$INTERACTIVE" == "true" ]]; then
    primary_menu "$target_dir"
  fi

  echo "✅ Installer completed."
  echo "Project directory: $target_dir"
}

main "$@"
