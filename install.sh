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
ENVEOF
}

write_nginx_config() {
  local target_dir="$1"
  local domain="$2"
  local panel_port="$3"
  local bot_path="$4"

  mkdir -p "$target_dir/nginx/conf.d" "$target_dir/certbot/www" "$target_dir/certbot/conf"

  cat > "$target_dir/nginx/conf.d/default.conf" <<NGINXEOF
server {
    listen 80;
    server_name $domain;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl;
    server_name $domain;

    ssl_certificate /etc/letsencrypt/live/$domain/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$domain/privkey.pem;

    location /$bot_path {
        proxy_pass http://bot:8443/$bot_path;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location / {
        proxy_pass http://admin-web:8080;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
NGINXEOF
}

request_cert() {
  local target_dir="$1"
  local domain="$2"
  local email="$3"

  (cd "$target_dir" && docker compose --profile ssl up -d nginx)
  (cd "$target_dir" && docker compose --profile ssl run --rm certbot certonly --webroot -w /var/www/certbot -d "$domain" --email "$email" --agree-tos --no-eff-email)
  (cd "$target_dir" && docker compose --profile ssl exec nginx nginx -s reload)
  echo "SSL certificate issued and nginx reloaded."
}

renew_cert() {
  local target_dir="$1"

  (cd "$target_dir" && docker compose --profile ssl run --rm certbot renew)
  (cd "$target_dir" && docker compose --profile ssl exec nginx nginx -s reload || true)
  echo "Renewal command completed."
}

revoke_cert() {
  local target_dir="$1"
  local domain="$2"

  (cd "$target_dir" && docker compose --profile ssl run --rm certbot revoke --cert-path "/etc/letsencrypt/live/$domain/cert.pem" --non-interactive || true)
  (cd "$target_dir" && docker compose --profile ssl run --rm certbot delete --cert-name "$domain" --non-interactive || true)
  (cd "$target_dir" && docker compose --profile ssl exec nginx nginx -s reload || true)
  echo "Revocation/delete command completed."
}

main() {
  require_root_or_sudo
  install_docker_if_missing
  install_compose_if_missing

  local target_dir
  target_dir="$(prompt_value "Install directory" "$TARGET_DIR_DEFAULT")"

  clone_or_update_repo "$target_dir"
  mkdir -p "$target_dir/data" "$target_dir/logs"

  echo
  echo "--- 3xui Telegram Bot Interactive Setup ---"
  TELEGRAM_BOT_TOKEN="$(prompt_value "Telegram Bot Token" "" true)"
  XUI_BASE_URL="$(prompt_value "XUI Panel Base URL (e.g. https://panel.example.com:2053/panel)" "")"
  XUI_USERNAME="$(prompt_value "XUI Username" "admin")"
  XUI_PASSWORD="$(prompt_value "XUI Password" "admin" true)"
  XUI_SERVER_HOST="$(prompt_value "XUI Server Host/IP" "127.0.0.1")"
  WEBHOOK_BASE_URL="$(prompt_value "Public webhook base URL (e.g. https://bot.example.com)" "")"
  WEBHOOK_PATH="$(prompt_value "Webhook path" "telegram")"
  PANEL_PORT="$(prompt_value "Panel host port" "8080")"
  BOT_PORT="$(prompt_value "Bot webhook host port" "8443")"
  ADMIN_TELEGRAM_ID="$(prompt_value "Admin Telegram ID (optional)" "")"
  ADMIN_WEB_TOKEN="$(prompt_value "Admin web token" "change-me")"
  WEBHOOK_SECRET_TOKEN="$(prompt_value "Webhook secret token (recommended)" "")"
  MAX_PLAN_DAYS="$(prompt_value "MAX_PLAN_DAYS" "365")"
  MAX_PLAN_GB="$(prompt_value "MAX_PLAN_GB" "2000")"
  MAX_BULK_COUNT="$(prompt_value "MAX_BULK_COUNT" "100")"

  write_env_file "$target_dir"

  echo
  ssl_choice="$(prompt_value "Enable SSL reverse proxy with Nginx + Certbot? (y/n)" "y")"

  if [[ "$ssl_choice" =~ ^[Yy]$ ]]; then
    SSL_DOMAIN="$(prompt_value "Domain for SSL (must point to this server)" "")"
    CERTBOT_EMAIL="$(prompt_value "Email for Let's Encrypt" "")"
    write_nginx_config "$target_dir" "$SSL_DOMAIN" "$PANEL_PORT" "$WEBHOOK_PATH"

    (cd "$target_dir" && docker compose up -d --build)

    cert_menu="$(prompt_value "SSL action: 1) Request 2) Renew 3) Revoke 4) Skip" "1")"
    case "$cert_menu" in
      1) request_cert "$target_dir" "$SSL_DOMAIN" "$CERTBOT_EMAIL" ;;
      2) renew_cert "$target_dir" ;;
      3) revoke_cert "$target_dir" "$SSL_DOMAIN" ;;
      *) echo "Skipping SSL certificate action." ;;
    esac
  else
    (cd "$target_dir" && docker compose up -d --build)
  fi

  echo
  echo "✅ Installation completed successfully!"
  echo "Project directory: $target_dir"
  echo "Panel URL: http://<server-ip>:$PANEL_PORT/?token=$ADMIN_WEB_TOKEN"
  if [[ "${ssl_choice}" =~ ^[Yy]$ ]]; then
    echo "If SSL certificate was issued, use: https://$SSL_DOMAIN/?token=$ADMIN_WEB_TOKEN"
  fi
}

main "$@"
