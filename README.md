# 3xui Telegram Bot

Minimal Telegram bot + admin panel integration for 3xui.

## Minimum Requirements

- **CPU:** 1 vCPU
- **RAM:** 1 GB minimum (2 GB recommended)
- **Disk:** 5 GB free minimum (10 GB recommended)
- **OS:** Linux (Ubuntu 22.04+ recommended)
- **Network:** Public domain + open ports `80` and `443`

## Quick Start

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/03hitalin21/3xui-tgbot/New10/install.sh)
```

Re-run the same one-liner anytime to perform an in-place upgrade.
The installer now auto-detects existing installs, creates a backup under `backups/<timestamp>/`,
pulls the latest code from the repository default branch, runs DB migrations, and restarts services.

## Environment

```bash
cp config/bot.env.example .env
```

## Why bot can stop after a while

The bot is started as a background process by shell script and is **not** supervised by `systemd` by default. If the Python process exits (error/OOM/restart), it will stay down until manually restarted.


## Panel timeout tuning

If your panel is slow or remote, increase these environment variables in `.env`:

- `XUI_CONNECT_TIMEOUT` (default `30`)
- `XUI_READ_TIMEOUT` (default `30`)
- `XUI_REQUEST_RETRIES` (default `2`)

This helps reduce transient timeout errors when loading the inbounds list.

## Manual wallet top-up flow

The bot supports manual transfer top-ups with receipt verification:

1. User sends `/topup <amount>`.
2. Bot asks user to upload payment receipt (photo).
3. Admin approves in bot using `/approvetopupid <topupid>` **or** from Admin Web Panel → **Topups** → **Confirm**.
4. After approval, wallet balance is credited.

Configure transfer instructions shown to users with `MANUAL_PAYMENT_DETAILS` in `.env` or from Admin Web Panel pricing settings.
