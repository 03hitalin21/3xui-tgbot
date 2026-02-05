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

## Current repository contents
- `xui-panel-api/inboundCreatorV1.py`: Interactive helper to create x-ui inbounds.
- `xui-panel-api/clientCreatorV1.py`: Interactive helper to create single/bulk clients and output VLESS links.
- `telegram_bot.py`: Telegram bot with wallet, pricing, preferred inbound, and single/bulk client provisioning commands.

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
3. Optional pricing and storage settings:
   ```bash
   export PRICE_PER_GB="0.15"
   export PRICE_PER_DAY="0.10"
   export AGENTS_FILE="agents.json"
   ```
4. Run the bot:
   ```bash
   python telegram_bot.py
   ```

## Practical UX improvements in the bot
- Reply keyboard buttons for common actions (`Balance`, `Plans`, `Price`, `Help`).
- `/setinbound` + `/myinbound` so agents can set a default inbound and avoid typing inbound ID every time.
- Flexible command input:
  - `/buy <inbound> <days> <gb> [remark]`
  - `/buy <days> <gb> [remark]` (after `/setinbound`)
  - `/bulk <inbound> <days> <gb> <count> [base_remark]`
  - `/bulk <days> <gb> <count> [base_remark]` (after `/setinbound`)
- Better validation and clearer user-facing error messages.

### Bot commands
- `/start`
- `/help`
- `/plans`
- `/balance`
- `/topup <amount>`
- `/setinbound <inbound_id>`
- `/myinbound`
- `/price <days> <gb>`
- `/buy <inbound_id> <days> <gb> [remark]`
- `/bulk <inbound_id> <days> <gb> <count> [base_remark]`
