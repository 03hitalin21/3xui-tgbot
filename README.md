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
```

## Run
```bash
pip install -r requirements.txt
python telegram_bot.py
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
