# 3xui Telegram Bot

Minimal Telegram bot + admin panel integration for 3xui.

## Minimum Requirements

- **CPU:** 1 vCPU
- **RAM:** 1 GB minimum (2 GB recommended)
- **Disk:** 5 GB free minimum (10 GB recommended)
- **OS:** Linux (Ubuntu 22.04+ recommended)
- **Network (prod):** Public domain + open ports `80` and `443`

## Path 1: Quick production install (`install.sh`)

First install (interactive):

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/03hitalin21/3xui-tgbot/New11/install.sh) --interactive
```

Upgrade existing install (non-interactive default):

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/03hitalin21/3xui-tgbot/New11/install.sh)
```

## Path 2: Local development (Docker Compose)

```bash
cp .env.example .env
mkdir -p data logs
docker compose up --build
```

Admin panel: `http://localhost:18080/admin?token=<ADMIN_WEB_TOKEN>`

(Compose maps host `18080` to container `ADMIN_WEB_PORT` (default `8080`)).

## Environment template

```bash
cp .env.example .env
```

## Ops docs

- `ops/README.md`
- `ops/dev.md`
- `ops/production.md`

## Panel timeout tuning

If your panel is slow or remote, increase these environment variables in `.env`:

- `XUI_CONNECT_TIMEOUT` (default `30`)
- `XUI_READ_TIMEOUT` (default `30`)
- `XUI_REQUEST_RETRIES` (default `2`)

## Manual wallet top-up flow

The bot supports manual transfer top-ups with receipt verification:

1. User sends `/topup <amount>`.
2. Bot asks user to upload payment receipt (photo).
3. Admin approves in bot using `/approvetopupid <topupid>` **or** from Admin Web Panel → **Topups** → **Confirm**.
4. After approval, wallet balance is credited.
