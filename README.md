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

## SSL certificate management (acme.sh + nginx)

This project now separates certificate handling from core app startup.
If you already have SSL on your domain, simply skip certificate management in `install.sh` primary menu.

### Requirements
- Domain A record must point to your server IP (`DOMAIN` in `.env`).
- Ports `80` and `443` must be reachable from the internet.
- Docker/Compose must be running.

### Nginx + ACME challenge flow
- Nginx serves HTTP-01 challenge files from `./acme-webroot`.
- Certificates are stored in `./certs/live/<domain>/`.
- Nginx reads certs from mounted volume `./certs:/etc/nginx/certs:ro`.

### One-time SSL setup
```bash
./scripts/setup_ssl.sh example.com
```

### Automatic renewal
- `acme` service in `docker-compose.yml` runs a renewal loop every 12 hours.
- It executes `./scripts/setup_ssl.sh <domain> --renew-only`.
- On successful renew/install, acme.sh runs reload hook:
  `docker compose exec nginx nginx -s reload`

### Manual validation
```bash
docker compose ps
docker compose logs --tail=200 nginx acme
ls -l certs/live/<your-domain>/
curl -I http://<your-domain>
curl -Ik https://<your-domain>/admin
```

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
