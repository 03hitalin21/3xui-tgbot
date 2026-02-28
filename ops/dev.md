# Local development (Docker Compose)

## Prerequisites

- Docker + Docker Compose plugin

## Quickstart

```bash
cp .env.example .env
mkdir -p data logs
docker compose up --build
```

## Services

- Admin panel: `http://localhost:18080/admin?token=<ADMIN_WEB_TOKEN>` (host `18080` -> container `ADMIN_WEB_PORT`, default `8080`)
- Bot webhook listener: `localhost:${WEBHOOK_PORT}` (default: `8443`)

## Notes about webhook vs local development

The bot entrypoint runs with the same runtime mode as production (`python telegram_bot.py`), which uses webhook settings from `.env`.

For local development, you can keep placeholder webhook values and still run the stack for code-level checks. Telegram delivery needs a publicly reachable `WEBHOOK_BASE_URL`.

## Common commands

```bash
make dev-up
make logs
make migrate
make dev-down
```
