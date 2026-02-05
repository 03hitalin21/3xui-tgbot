# 3xui-tgbot

Telegram + 3x-ui sales system with:
- step-by-step agent order flow,
- SQLite database storage,
- simple admin web panel.

## Key upgrades
- **Database-backed storage** (`bot.db`): agents, wallet ledger, orders, promos, settings.
- **Admin web app** (`admin_web.py`):
  - set price per GB/day,
  - create promo codes,
  - view agent performance (balance, lifetime top-up, clients, spent).
- **Better agent UX** in Telegram:
  - guided step-by-step wizard (asks one parameter at a time),
  - no more long single-line command required for creation,
  - promo code support for discounts.
- **Role policy**:
  - admin-only inbound creation (`ADMIN_TELEGRAM_ID`, default `8477244366`),
  - agents only create clients on existing inbounds.

## Environment
```bash
export TELEGRAM_BOT_TOKEN="..."
export XUI_BASE_URL="https://host:port/panelpath"
export XUI_USERNAME="admin"
export XUI_PASSWORD="admin"
export XUI_SERVER_HOST="host"

# optional
export ADMIN_TELEGRAM_ID="8477244366"
export BOT_DB_PATH="bot.db"
export ADMIN_WEB_TOKEN="set-a-secret-token"
export ADMIN_WEB_PORT="8080"
```

## Run bot
```bash
pip install -r requirements.txt
python telegram_bot.py
```

## Run admin web panel
```bash
pip install -r requirements.txt
python admin_web.py
```
Open:
`http://<server-ip>:8080/?token=<ADMIN_WEB_TOKEN>`

## Telegram usage
1. `/start`
2. Tap **Create Single Client** or **Create Bulk Clients**
3. Bot asks parameters one by one:
   - inbound id (or `default`)
   - days
   - GB
   - count/base (bulk) or remark (single)

Other useful commands:
- `/balance`
- `/topup <amount>`
- `/setinbound <id>`
- `/price <days> <gb>`
- `/promo <CODE>`
- `/createinbound ...` (admin only)
