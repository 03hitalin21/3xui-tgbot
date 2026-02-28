# Production notes

## Install / upgrade using install.sh

Interactive setup (recommended first install):

```bash
bash install.sh --interactive
```

Non-interactive upgrade path (existing install):

```bash
bash install.sh
```

## nginx

- Reference config: `nginx/nginx.conf`
- Helper script: `scripts/configure_nginx.sh`
- TLS helper: `scripts/setup_ssl.sh`

## service supervision

- systemd helper: `scripts/setup_systemd.sh`
- fallback process manager: `scripts/manage_services.sh`

## SQLite backup / restore

Default DB path: `data/bot.db` (or `BOT_DB_PATH`).

Backup:

```bash
cp data/bot.db data/bot.db.$(date +%Y%m%d-%H%M%S).bak
```

Restore:

```bash
cp data/bot.db.<timestamp>.bak data/bot.db
```

## Security checklist

- Set strong values for `ADMIN_WEB_TOKEN`, `ADMIN_WEB_SECRET`, and `WEBHOOK_SECRET_TOKEN`.
- Restrict server/network access and keep system packages updated.
- Prefer TLS termination via nginx with valid certificates.
