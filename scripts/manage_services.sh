#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR="${2:-$(pwd)}"
ENV_FILE="$TARGET_DIR/.env"
VENV_DIR="$TARGET_DIR/.venv"
BOT_PID_FILE="$TARGET_DIR/.bot.pid"
ADMIN_PID_FILE="$TARGET_DIR/.admin.pid"
BOT_LOG_FILE="$TARGET_DIR/logs/bot.log"
ADMIN_LOG_FILE="$TARGET_DIR/logs/admin_web.log"

fail() {
  echo "❌ $*" >&2
  exit 1
}

warn() {
  echo "⚠️ $*" >&2
}

trim_whitespace() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

strip_wrapping_quotes() {
  local value="$1"
  local first_char
  local last_char

  if [[ ${#value} -ge 2 ]]; then
    first_char="${value:0:1}"
    last_char="${value: -1}"
    if [[ "$first_char" == '"' && "$last_char" == '"' ]]; then
      value="${value:1:-1}"
    elif [[ "$first_char" == "'" && "$last_char" == "'" ]]; then
      value="${value:1:-1}"
    fi
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

is_running() {
  local pid_file="$1"
  [[ -f "$pid_file" ]] || return 1
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

stop_one() {
  local name="$1"
  local pid_file="$2"
  if is_running "$pid_file"; then
    kill "$(cat "$pid_file")" 2>/dev/null || true
    sleep 1
    if is_running "$pid_file"; then
      kill -9 "$(cat "$pid_file")" 2>/dev/null || true
    fi
  fi
  rm -f "$pid_file"
  pkill -f "$name" 2>/dev/null || true
}

start_services() {
  [[ -f "$ENV_FILE" ]] || fail "Missing env file: $ENV_FILE"
  [[ -x "$VENV_DIR/bin/python" ]] || fail "Virtualenv not found: $VENV_DIR (run installer option 4 after setup)"

  mkdir -p "$TARGET_DIR/logs" "$TARGET_DIR/data"
  load_env_file

  stop_one "telegram_bot.py" "$BOT_PID_FILE"
  stop_one "admin_web.py" "$ADMIN_PID_FILE"

  (
    cd "$TARGET_DIR"
    env BOT_DB_PATH="$TARGET_DIR/data/bot.db" BOT_LOG_PATH="$BOT_LOG_FILE" "$VENV_DIR/bin/python" telegram_bot.py >>"$BOT_LOG_FILE" 2>&1 &
    echo $! > "$BOT_PID_FILE"
  )

  (
    cd "$TARGET_DIR"
    env BOT_DB_PATH="$TARGET_DIR/data/bot.db" ADMIN_WEB_PORT="${ADMIN_WEB_PORT:-8080}" "$VENV_DIR/bin/python" admin_web.py >>"$ADMIN_LOG_FILE" 2>&1 &
    echo $! > "$ADMIN_PID_FILE"
  )

  echo "✅ Bot/Admin services started."
}

status_services() {
  local failed=0
  if is_running "$BOT_PID_FILE"; then
    echo "✅ bot running (pid $(cat "$BOT_PID_FILE"))"
  else
    echo "❌ bot not running"
    failed=1
  fi

  if is_running "$ADMIN_PID_FILE"; then
    echo "✅ admin-web running (pid $(cat "$ADMIN_PID_FILE"))"
  else
    echo "❌ admin-web not running"
    failed=1
  fi

  return "$failed"
}

case "${1:-}" in
  start) start_services ;;
  stop)
    stop_one "telegram_bot.py" "$BOT_PID_FILE"
    stop_one "admin_web.py" "$ADMIN_PID_FILE"
    echo "✅ Bot/Admin services stopped."
    ;;
  restart)
    "$0" stop "$TARGET_DIR"
    "$0" start "$TARGET_DIR"
    ;;
  status) status_services ;;
  *)
    echo "Usage: $0 {start|stop|restart|status} [target_dir]"
    exit 1
    ;;
esac
