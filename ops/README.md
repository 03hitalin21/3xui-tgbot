# Ops documentation

This project supports two operation modes:

1. **Production install path** via `install.sh` (system packages + nginx + service scripts/systemd).
2. **Developer path** via Docker Compose (local iteration, optional for contributors).

## Architecture (high-level)

```text
[Telegram] -> [Bot webhook endpoint :8443] -> telegram_bot.py
                           |
                           +-> SQLite (BOT_DB_PATH)

[Browser] -> [Admin panel :8080] -> admin_web.py
                           |
                           +-> same SQLite (BOT_DB_PATH)

[Optional production reverse proxy]
[nginx :80/:443] -> bot/admin services
```

## Guides

- Local development: [`ops/dev.md`](dev.md)
- Production deployment: [`ops/production.md`](production.md)
