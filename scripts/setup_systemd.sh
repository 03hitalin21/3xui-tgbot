#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-install}"
TARGET_DIR="${2:-$(pwd)}"
SERVICE_PREFIX="3xui-tgbot"
BOT_SERVICE="${SERVICE_PREFIX}-bot.service"
ADMIN_SERVICE="${SERVICE_PREFIX}-admin.service"

if [[ "${EUID}" -eq 0 ]]; then
  SUDO=""
else
  SUDO="sudo"
fi

fail() {
  echo "❌ $*" >&2
  exit 1
}

info() {
  echo "✅ $*"
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

service_user() {
  local owner
  owner="$(stat -c %U "$TARGET_DIR")"
  if [[ -z "$owner" || "$owner" == "UNKNOWN" ]]; then
    printf 'root'
  else
    printf '%s' "$owner"
  fi
}

service_group() {
  local owner_group
  owner_group="$(stat -c %G "$TARGET_DIR")"
  if [[ -z "$owner_group" || "$owner_group" == "UNKNOWN" ]]; then
    printf 'root'
  else
    printf '%s' "$owner_group"
  fi
}

write_units() {
  [[ -d "$TARGET_DIR" ]] || fail "Target dir not found: $TARGET_DIR"
  [[ -f "$TARGET_DIR/.env" ]] || fail "Missing env file: $TARGET_DIR/.env"
  [[ -x "$TARGET_DIR/.venv/bin/python" ]] || fail "Missing venv python: $TARGET_DIR/.venv/bin/python"

  local user group systemd_dir bot_unit admin_unit
  user="$(service_user)"
  group="$(service_group)"
  systemd_dir="/etc/systemd/system"
  bot_unit="$systemd_dir/$BOT_SERVICE"
  admin_unit="$systemd_dir/$ADMIN_SERVICE"

  ${SUDO:-} mkdir -p "$TARGET_DIR/logs" "$TARGET_DIR/data"

  ${SUDO:-} tee "$bot_unit" >/dev/null <<UNIT
[Unit]
Description=3xui Telegram Bot Webhook Service
After=network.target

[Service]
Type=simple
User=$user
Group=$group
WorkingDirectory=$TARGET_DIR
EnvironmentFile=$TARGET_DIR/.env
Environment=BOT_DB_PATH=$TARGET_DIR/data/bot.db
Environment=BOT_LOG_PATH=$TARGET_DIR/logs/bot.log
ExecStart=$TARGET_DIR/.venv/bin/python $TARGET_DIR/telegram_bot.py
Restart=always
RestartSec=3
StandardOutput=append:$TARGET_DIR/logs/bot.log
StandardError=append:$TARGET_DIR/logs/bot.log

[Install]
WantedBy=multi-user.target
UNIT

  ${SUDO:-} tee "$admin_unit" >/dev/null <<UNIT
[Unit]
Description=3xui Telegram Admin Web Service
After=network.target

[Service]
Type=simple
User=$user
Group=$group
WorkingDirectory=$TARGET_DIR
EnvironmentFile=$TARGET_DIR/.env
Environment=BOT_DB_PATH=$TARGET_DIR/data/bot.db
ExecStart=$TARGET_DIR/.venv/bin/python $TARGET_DIR/admin_web.py
Restart=always
RestartSec=3
StandardOutput=append:$TARGET_DIR/logs/admin_web.log
StandardError=append:$TARGET_DIR/logs/admin_web.log

[Install]
WantedBy=multi-user.target
UNIT

  ${SUDO:-} systemctl daemon-reload
  ${SUDO:-} systemctl enable "$BOT_SERVICE" "$ADMIN_SERVICE"
  info "Installed and enabled systemd units: $BOT_SERVICE, $ADMIN_SERVICE"
}

restart_units() {
  ${SUDO:-} systemctl restart "$BOT_SERVICE" "$ADMIN_SERVICE"
  info "Restarted systemd services."
}

status_units() {
  ${SUDO:-} systemctl --no-pager --full status "$BOT_SERVICE" "$ADMIN_SERVICE" || true
}

stop_units() {
  ${SUDO:-} systemctl stop "$BOT_SERVICE" "$ADMIN_SERVICE"
  info "Stopped systemd services."
}

uninstall_units() {
  ${SUDO:-} systemctl disable "$BOT_SERVICE" "$ADMIN_SERVICE" || true
  ${SUDO:-} rm -f "/etc/systemd/system/$BOT_SERVICE" "/etc/systemd/system/$ADMIN_SERVICE"
  ${SUDO:-} systemctl daemon-reload
  info "Removed systemd units."
}

main() {
  require_cmd systemctl

  case "$ACTION" in
    install)
      write_units
      restart_units
      ;;
    restart)
      restart_units
      ;;
    status)
      status_units
      ;;
    stop)
      stop_units
      ;;
    uninstall)
      uninstall_units
      ;;
    *)
      fail "Usage: $0 {install|restart|status|stop|uninstall} [target_dir]"
      ;;
  esac
}

main "$@"
