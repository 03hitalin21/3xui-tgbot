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

## Environment

```bash
cp config/bot.env.example .env
```

## Why bot can stop after a while

The bot is started as a background process by shell script and is **not** supervised by `systemd` by default. If the Python process exits (error/OOM/restart), it will stay down until manually restarted.
