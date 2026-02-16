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

download_tls_params() {
  local target_dir="$1"
  local certbot_conf_dir="$target_dir/certbot/conf"

  mkdir -p "$certbot_conf_dir"

  if [[ ! -f "$certbot_conf_dir/options-ssl-nginx.conf" ]]; then
    echo "Downloading recommended TLS options file..."
    curl -fsSL \
      https://raw.githubusercontent.com/certbot/certbot/master/certbot-nginx/certbot_nginx/_internal/tls_configs/options-ssl-nginx.conf \
      -o "$certbot_conf_dir/options-ssl-nginx.conf"
  else
    echo "Using existing TLS options: $certbot_conf_dir/options-ssl-nginx.conf"
  fi

  if [[ ! -f "$certbot_conf_dir/ssl-dhparams.pem" ]]; then
    echo "Downloading recommended DH params file..."
    curl -fsSL \
      https://raw.githubusercontent.com/certbot/certbot/master/certbot/certbot/ssl-dhparams.pem \
      -o "$certbot_conf_dir/ssl-dhparams.pem"
  else
    echo "Using existing DH params: $certbot_conf_dir/ssl-dhparams.pem"
  fi
}

write_nginx_config() {
  local target_dir="$1"
  local domain="$2"
  local bot_path="$3"
  local webhook_secret_token="$4"

  mkdir -p "$target_dir/nginx/conf.d" "$target_dir/certbot/www" "$target_dir/certbot/conf"

  cat > "$target_dir/nginx/conf.d/default.conf" <<NGINXEOF
server {
    listen 80;
    listen [::]:80;
    server_name $domain;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name $domain;

    ssl_certificate /etc/letsencrypt/live/$domain/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$domain/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    location /$bot_path {
        proxy_pass http://bot:8443/$bot_path;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Telegram-Bot-Api-Secret-Token "$webhook_secret_token";
    }

    location /admin {
        proxy_pass http://admin-web:8080;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    # Backward compatibility with existing deployments that use '/'.
    location / {
        proxy_pass http://admin-web:8080;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
NGINXEOF
}

create_dummy_certificate() {
  local target_dir="$1"
  local domain="$2"
  local cert_path="/etc/letsencrypt/live/$domain"

  echo "Creating temporary self-signed certificate for $domain ..."
  mkdir -p "$target_dir/certbot/conf/live/$domain"

  (
    cd "$target_dir"
    docker compose --profile ssl run --rm --entrypoint "\
      sh -c 'mkdir -p $cert_path && \
      openssl req -x509 -nodes -newkey rsa:4096 -days 1 \
      -keyout $cert_path/privkey.pem \
      -out $cert_path/fullchain.pem \
      -subj /CN=localhost'" certbot
  )
}

delete_certificate_for_domain() {
  local target_dir="$1"
  local domain="$2"

  rm -rf "$target_dir/certbot/conf/live/$domain"
  rm -rf "$target_dir/certbot/conf/archive/$domain"
  rm -f "$target_dir/certbot/conf/renewal/$domain.conf"
}

resolve_public_ipv4() {
  curl -4fsS https://api.ipify.org 2>/dev/null || true
}

check_domain_points_to_server() {
  local domain="$1"
  local public_ip
  local dns_ips

  public_ip="$(resolve_public_ipv4)"
  dns_ips="$(getent ahostsv4 "$domain" 2>/dev/null | awk '{print $1}' | sort -u || true)"

  if [[ -z "$dns_ips" ]]; then
    echo "❌ Could not resolve A records for $domain."
    return 1
  fi

  echo "Domain A records for $domain:"
  echo "$dns_ips"

  if [[ -z "$public_ip" ]]; then
    echo "⚠️ Could not determine this server's public IPv4 (network restriction)."
    echo "Please verify DNS manually before requesting a certificate."
    return 0
  fi

  echo "Detected server public IPv4: $public_ip"

  if ! grep -qx "$public_ip" <<<"$dns_ips"; then
    echo "❌ DNS mismatch: $domain does not point to this server IP ($public_ip)."
    return 1
  fi

  return 0
}

check_ports_80_443_available() {
  local in_use
  in_use="$(ss -ltn '( sport = :80 or sport = :443 )' | tail -n +2 || true)"

  if [[ -n "$in_use" ]]; then
    echo "⚠️ Ports 80/443 already have listeners:"
    echo "$in_use"
    echo "This can be fine if it's the nginx container, but may block cert issuance if another service owns the ports."
  else
    echo "Ports 80/443 are free before nginx start."
  fi

  if command_exists ufw; then
    echo "Firewall snapshot (ufw):"
    ${SUDO:-} ufw status verbose || true
  fi
}

request_cert() {
  local target_dir="$1"
  local domain="$2"
  local email="$3"
  local staging_mode="$4"

  check_domain_points_to_server "$domain"
  check_ports_80_443_available

  local staging_arg=""
  if [[ "$staging_mode" =~ ^[Yy]$ ]]; then
    staging_arg="--staging"
    echo "Using Let's Encrypt staging endpoint."
  fi

  create_dummy_certificate "$target_dir" "$domain"

  # Start nginx with dummy certificate first (avoids chicken-and-egg startup failure).
  (
    cd "$target_dir"
    docker compose --profile ssl up -d nginx
  )

  # Remove dummy cert so certbot can create real cert files.
  delete_certificate_for_domain "$target_dir" "$domain"

  echo "Requesting a real Let's Encrypt certificate for $domain ..."
  if ! (
    cd "$target_dir"
    docker compose --profile ssl run --rm certbot certonly --webroot \
      -w /var/www/certbot \
      -d "$domain" \
      --email "$email" \
      --agree-tos \
      --no-eff-email \
      $staging_arg
  ); then
    echo "❌ Certificate request failed. Attempting rollback to dummy certificate so nginx can keep serving HTTPS..."
    create_dummy_certificate "$target_dir" "$domain"
    (
      cd "$target_dir"
      docker compose --profile ssl exec nginx nginx -s reload || true
    )
    echo "Rollback complete with dummy certificate. Check DNS, firewall, and certbot logs, then retry."
    return 1
  fi

  (
    cd "$target_dir"
    docker compose --profile ssl exec nginx nginx -s reload
  )

  echo "✅ SSL certificate issued and nginx reloaded."
}

renew_cert() {
  local target_dir="$1"

  (
    cd "$target_dir"
    docker compose --profile ssl run --rm certbot renew
  )
  (
    cd "$target_dir"
    docker compose --profile ssl exec nginx nginx -s reload || true
  )
  echo "Renewal command completed."
}

revoke_cert() {
  local target_dir="$1"
  local domain="$2"

  (
    cd "$target_dir"
    docker compose --profile ssl run --rm certbot revoke --cert-path "/etc/letsencrypt/live/$domain/cert.pem" --non-interactive || true
  )
  (
    cd "$target_dir"
    docker compose --profile ssl run --rm certbot delete --cert-name "$domain" --non-interactive || true
  )
  (
    cd "$target_dir"
    docker compose --profile ssl exec nginx nginx -s reload || true
  )
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
    LE_STAGING="$(prompt_value "Use Let's Encrypt staging server for testing? (y/n)" "n")"

    download_tls_params "$target_dir"
    write_nginx_config "$target_dir" "$SSL_DOMAIN" "$WEBHOOK_PATH" "$WEBHOOK_SECRET_TOKEN"

    (cd "$target_dir" && docker compose up -d --build)

    cert_menu="$(prompt_value "SSL action: 1) Request 2) Renew 3) Revoke 4) Skip" "1")"
    case "$cert_menu" in
      1) request_cert "$target_dir" "$SSL_DOMAIN" "$CERTBOT_EMAIL" "$LE_STAGING" ;;
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
    echo "If SSL certificate was issued, use: https://$SSL_DOMAIN/admin?token=$ADMIN_WEB_TOKEN"
  fi
}

main "$@"
