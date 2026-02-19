#!/bin/bash
set -e

########################################
# CONFIG
########################################
DOMAIN="mehrsway.space"
WWW_DOMAIN="www.mehrsway.space"
BOT_TOKEN="8412183336:AAE9U0oyodTEbproEWWcNXSpY4rYW1xB_dM"
EMAIL="admin@mehrsway.space"

WEBHOOK_HEX=$(openssl rand -hex 16)
WEBHOOK_PATH="telegram/$WEBHOOK_HEX"
WEBHOOK_URL="https://$DOMAIN/$WEBHOOK_PATH"

echo "Webhook will be: $WEBHOOK_URL"

########################################
# Install Docker
########################################
apt update
apt install -y docker.io docker-compose
systemctl enable docker
systemctl start docker

########################################
# Install Certbot (snap)
########################################
apt install -y snapd
snap install core
snap refresh core
snap install --classic certbot
ln -sf /snap/bin/certbot /usr/bin/certbot

########################################
# Stop anything on port 80
########################################
docker stop nginx 2>/dev/null || true
systemctl stop nginx 2>/dev/null || true

########################################
# Issue SSL Certificate (Standalone)
########################################
certbot certonly --standalone \
  --non-interactive \
  --agree-tos \
  -m "$EMAIL" \
  -d "$DOMAIN" \
  -d "$WWW_DOMAIN"

certbot certificates

########################################
# Create Project Structure
########################################
mkdir -p /opt/telegram-docker/nginx
mkdir -p /opt/telegram-docker/bot
cd /opt/telegram-docker

########################################
# Save ENV
########################################
cat > .env <<EOF
DOMAIN=$DOMAIN
WWW_DOMAIN=$WWW_DOMAIN
BOT_TOKEN=$BOT_TOKEN
WEBHOOK_PATH=$WEBHOOK_PATH
WEBHOOK_URL=$WEBHOOK_URL
EOF

########################################
# Docker Compose
########################################
cat > docker-compose.yml <<EOF
version: '3.9'

services:
  nginx:
    image: nginx:latest
    container_name: nginx
    restart: always
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /etc/letsencrypt:/etc/letsencrypt:ro
      - ./nginx/default.conf:/etc/nginx/conf.d/default.conf
    depends_on:
      - bot

  bot:
    build: ./bot
    container_name: telegram-bot
    restart: always
    env_file:
      - .env
    expose:
      - "8443"
EOF

########################################
# Nginx Config
########################################
cat > nginx/default.conf <<EOF
server {
    listen 80;
    server_name $DOMAIN $WWW_DOMAIN;
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl;
    server_name $DOMAIN $WWW_DOMAIN;

    ssl_certificate /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;

    location /$WEBHOOK_PATH {
        proxy_pass http://bot:8443;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }

    location / {
        return 403;
    }
}
EOF

########################################
# Python Bot Dockerfile
########################################
cat > bot/Dockerfile <<EOF
FROM python:3.11-slim
WORKDIR /app
COPY bot.py .
RUN pip install flask requests
CMD ["python", "bot.py"]
EOF

########################################
# Minimal Telegram Bot
########################################
cat > bot/bot.py <<EOF
import os
from flask import Flask, request
import requests

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_PATH = "/" + os.getenv("WEBHOOK_PATH")

app = Flask(__name__)

@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    data = request.json
    if data and "message" in data:
        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "")
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": f"Echo: {text}"}
        )
    return "ok", 200

app.run(host="0.0.0.0", port=8443)
EOF

########################################
# Build & Start
########################################
docker-compose build
docker-compose up -d

########################################
# Set Telegram Webhook
########################################
sleep 5
curl -s -X POST \
"https://api.telegram.org/bot$BOT_TOKEN/setWebhook" \
-d "url=$WEBHOOK_URL"

echo ""
echo "Webhook status:"
curl -s "https://api.telegram.org/bot$BOT_TOKEN/getWebhookInfo"

########################################
# Auto Renew Cron
########################################
(crontab -l 2>/dev/null; echo "0 3 * * * certbot renew --quiet --pre-hook 'docker stop nginx' --post-hook 'docker start nginx'") | crontab -

echo ""
echo "======================================="
echo "SETUP COMPLETE"
echo "Webhook: $WEBHOOK_URL"
echo "======================================="
