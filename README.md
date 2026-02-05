# 3xui-tgbot

This project integrates a **3x-ui (Sanaei) panel** with a **Telegram bot** for agency-style VPN sales.

## Project goal
Agents can create VLESS client configs (single or bulk) by selecting package specs such as:
- plan duration
- total traffic (GB)

The system then:
1. creates the client(s) on x-ui,
2. generates VLESS links,
3. calculates price based on selected specs,
4. deducts the amount from the agent wallet balance.

## Roles
- **Admin** (`ADMIN_TELEGRAM_ID`, default: `8477244366`): can create inbounds.
- **Agents**: can only create clients on existing inbounds.

## Current repository contents
- `xui-panel-api/inboundCreatorV1.py`: Interactive helper to create x-ui inbounds.
- `xui-panel-api/clientCreatorV1.py`: Interactive helper to create single/bulk clients and output VLESS links.
- `telegram_bot.py`: Telegram bot frontend with wallets, pricing, default inbound, and client provisioning.

## Telegram bot quick start
1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Set required environment variables:
   ```bash
   export TELEGRAM_BOT_TOKEN="..."
   export XUI_BASE_URL="https://your-host:port/your-panel-path"
   export XUI_USERNAME="admin"
   export XUI_PASSWORD="admin"
   export XUI_SERVER_HOST="your-host"
   ```
3. Optional variables:
   ```bash
   export PRICE_PER_GB="0.15"
   export PRICE_PER_DAY="0.10"
   export AGENTS_FILE="agents.json"
   export ADMIN_TELEGRAM_ID="8477244366"
   ```
4. Run:
   ```bash
   python telegram_bot.py
   ```

## UI/UX improvements
- Inline button menu for practical day-to-day use.
- Default inbound support (`/setinbound`) to reduce repetitive input.
- Guided quick-buy and quick-bulk formats.
- Clear role-based permission: inbound creation is admin-only.

## Commands
- `/start`
- `/help`
- `/balance`
- `/topup <amount>`
- `/setinbound <inbound_id>`
- `/myinbound`
- `/price <days> <gb>`
- `/buy <days> <gb> [remark]`
- `/buy <inbound_id> <days> <gb> [remark]`
- `/bulk <days> <gb> <count> [base_remark]`
- `/bulk <inbound_id> <days> <gb> <count> [base_remark]`
- `/createinbound <port> <remark> [protocol] [network]` (**admin only**)


## How to push your commits
1. Check current branch:
   ```bash
   git branch --show-current
   ```
2. Add and commit your changes:
   ```bash
   git add -A
   git commit -m "your message"
   ```
3. Push your branch to GitHub:
   ```bash
   git push -u origin $(git branch --show-current)
   ```
