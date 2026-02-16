# 3xui-tgbot

Commercial-grade Telegram reseller panel for 3x-ui / Sanaei.

## What is implemented
- Button-first Telegram UX (not command-heavy).
- FSM-style guided client creation flow.
- SQLite database for wallets, orders, transactions, clients, promo codes, settings.
- Admin web panel for pricing/promos/agent performance.
- Admin/reseller role separation.

## Professional Telegram menu
- 📊 Dashboard
- 👤 My Clients
- ➕ Create Client
- 🌐 Inbounds List
- 💰 Wallet / Balance
- 📄 Transactions History
- 🆘 Support
- ⚙️ Settings

## Client creation wizard (step-by-step)
1. Select inbound (or default)
2. Enter remark/base remark
3. Enter count (bulk only)
4. Enter total days
5. Enter total GB
6. Start after first use? (y/n)
7. Auto-renew? (y/n)
8. Confirm (yes/no)

After creation:
- configuration links are shown,
- single creation includes QR preview,
- order and client are stored in DB.

## Pricing & billing
- Global price per GB/day.
- Optional per-inbound pricing rule (and enable/disable inbound).
- Automatic wallet deduction.
- Prevent creation on insufficient balance.
- Clear error messages and refund on panel/API failure.

## Environment
```bash
export TELEGRAM_BOT_TOKEN="..."
export XUI_BASE_URL="https://host:port/panel-path"
export XUI_USERNAME="admin"
export XUI_PASSWORD="admin"
export XUI_SERVER_HOST="host"

# optional
export ADMIN_TELEGRAM_ID="8477244366"
export BOT_DB_PATH="bot.db"
export ADMIN_WEB_TOKEN="set-a-secret-token"
export ADMIN_WEB_PORT="8080"
export MAX_PLAN_DAYS="365"
export MAX_PLAN_GB="2000"
export MAX_BULK_COUNT="100"

# webhook (required)
export WEBHOOK_BASE_URL="https://your-domain.example"
# optional
export WEBHOOK_PATH="telegram"
export WEBHOOK_LISTEN="0.0.0.0"
export WEBHOOK_PORT="8443"
export WEBHOOK_SECRET_TOKEN="set-a-secret-token"
```

## Run
```bash
pip install -r requirements.txt
python telegram_bot.py
```

## One-liner install
```bash
bash <(curl -fsSL https://raw.githubusercontent.com/03hitalin21/3xui-tgbot/New3/install.sh)
```

## Docker (recommended)
1. Copy sample env and fill values:
```bash
cp config/bot.env.example config/bot.env
```
2. Start services:
```bash
docker compose up -d --build
```
- `bot` runs `telegram_bot.py`
- `admin-web` runs `admin_web.py`
- SQLite data persists in `./data` on the server root
- Logs are written to `./logs/bot.log` on the server root

Stop:
```bash
docker compose down
```

After changing Python code, rebuild:
```bash
docker compose up -d --build
```

After changing only env values, restart is enough:
```bash
docker compose up -d
```


## Recommended server-root layout
```
.
├── config/
│   └── bot.env            # copy from bot.env.example
├── data/                  # sqlite data (persistent)
├── logs/                  # bot logs
├── docker-compose.yml
├── Dockerfile
├── telegram_bot.py
├── admin_web.py
├── db.py
└── xui_api.py
```

## Webhook notes (production)
- Your `WEBHOOK_BASE_URL` must be reachable over HTTPS with a valid certificate.
- Ensure the `WEBHOOK_PORT` is open or place the bot behind a reverse proxy that forwards
  `https://<domain>/<WEBHOOK_PATH>` to `http://127.0.0.1:<WEBHOOK_PORT>/<WEBHOOK_PATH>`.
- If you set `WEBHOOK_SECRET_TOKEN`, configure your reverse proxy to pass it through.
- If you set `BOT_DB_PATH`, make sure the directory exists and is writable by the bot process.

## Certbot + Nginx recovery playbook

Use this when Let's Encrypt issuance fails or when you need a fresh production HTTPS setup.

### 1) Diagnose certbot failure

Set your domain first:

```bash
export DOMAIN="your-domain.example"
```

Check listeners and conflicts:

```bash
sudo ss -tulpn | rg ':80|:443'
sudo lsof -iTCP:80 -sTCP:LISTEN -n -P
sudo lsof -iTCP:443 -sTCP:LISTEN -n -P
```

Check DNS resolution and expected public IP:

```bash
dig +short A "$DOMAIN"
dig +short AAAA "$DOMAIN"
curl -4 ifconfig.me
```

Check firewall status:

```bash
sudo ufw status verbose || true
sudo iptables -S
sudo iptables -L -n -v
```

Read certbot logs and identify challenge error:

```bash
sudo tail -n 200 /var/log/letsencrypt/letsencrypt.log
sudo rg -n "(error|failed|unauthorized|timeout|connection|challenge)" /var/log/letsencrypt/letsencrypt.log
```

Quick external reachability checks:

```bash
curl -I "http://$DOMAIN"
curl -Ik "https://$DOMAIN"
```

### 2) Fix common issues

Install required packages:

```bash
sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx
```

Open required firewall ports:

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw reload
```

Stop conflicting services if they occupy 80/443 (example: apache2):

```bash
sudo systemctl stop apache2 || true
sudo systemctl disable apache2 || true
```

Fix nginx permissions and config validity:

```bash
sudo nginx -t
sudo chown -R root:root /etc/nginx
```

### 3) Get SSL certificate with certbot nginx plugin

```bash
sudo certbot --nginx -d "$DOMAIN" --redirect -m admin@"$DOMAIN" --agree-tos --no-eff-email
```

### 4) Configure nginx reverse proxy

Create `/etc/nginx/sites-available/telegram-bot`:

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name your-domain.example;

    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name your-domain.example;

    ssl_certificate /etc/letsencrypt/live/your-domain.example/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.example/privkey.pem;

    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header X-XSS-Protection "1; mode=block" always;

    location /telegram {
        proxy_pass http://127.0.0.1:8443/telegram;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Telegram-Bot-Api-Secret-Token "set-a-secret-token";
    }

    location /admin {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable and validate config:

```bash
sudo ln -sf /etc/nginx/sites-available/telegram-bot /etc/nginx/sites-enabled/telegram-bot
sudo nginx -t
sudo systemctl reload nginx
```

### 5) Ensure persistence

Enable auto-start and renewal:

```bash
sudo systemctl enable nginx
sudo systemctl enable certbot.timer
sudo systemctl start certbot.timer
sudo certbot renew --dry-run
```

Zero-downtime style nginx restart test:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

Create a systemd service for the bot (if missing):

```ini
# /etc/systemd/system/telegram-bot.service
[Unit]
Description=3xui Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/3xui-tgbot
EnvironmentFile=/opt/3xui-tgbot/config/bot.env
ExecStart=/usr/bin/python3 /opt/3xui-tgbot/telegram_bot.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable telegram-bot
sudo systemctl restart telegram-bot
sudo systemctl status telegram-bot --no-pager
```

### 6) Validation checklist

```bash
curl -i -X POST "https://$DOMAIN/telegram"
echo | openssl s_client -connect "$DOMAIN:443" -servername "$DOMAIN" 2>/dev/null | openssl x509 -noout -subject -issuer -dates
sudo tail -n 100 /var/log/nginx/error.log
curl -I "https://$DOMAIN/admin"
nc -zv "$DOMAIN" 80
nc -zv "$DOMAIN" 443
```

If the certificate still fails, inspect the exact ACME error in `/var/log/letsencrypt/letsencrypt.log` and compare it with firewall, DNS, and active listeners above.

## Admin web
```bash
python admin_web.py
```
Open:
`http://<server-ip>:8080/?token=<ADMIN_WEB_TOKEN>`

## Project structure
- `telegram_bot.py` — Telegram UI & FSM flows.
- `xui_api.py` — x-ui API integration layer.
- `db.py` — persistence & data/business helpers.
- `admin_web.py` — admin panel.
- `xui-panel-api` scripts have been removed as they are no longer needed.
