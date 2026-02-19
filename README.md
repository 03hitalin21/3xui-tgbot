# 3xui Telegram Bot Installer

## Quick Installation

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/03hitalin21/3xui-tgbot/New5/install.sh)
```

## TLS / Telegram Webhook notes

The setup wizard now supports:

1. Configuring domain/email values in `.env`.
2. Reusing existing certificates from `/etc/letsencrypt/live/<domain>/`.
3. Issuing a new Let's Encrypt certificate via Certbot (`standalone` by default, `webroot` optional via `SSL_CERTBOT_MODE=webroot`) when no certificate exists. On apt-based hosts, Certbot is installed with snap (same model as `Sample/setup.sh`).
4. Running Nginx on `80/443` with HTTP -> HTTPS redirect and ACME challenge handling.
5. Installing nightly certificate renewal automation with Nginx stop/start hooks.
6. Auto-registering Telegram webhook after stack startup + one-command health checks.

Suggested flow from installer menu:

1. `Configure app (.env)`
2. `Acquire/configure TLS certificate (Let's Encrypt)`
3. `Check TLS certificate status`
4. `Start / restart containers` (auto-runs webhook registration)
5. `Run health checks (TLS, containers, nginx, webhook)`

For your domain (`mehrsway.space` + optional `www.mehrsway.space`), set:

- `SSL_DOMAIN=mehrsway.space`
- `SSL_INCLUDE_WWW=true`
- `WEBHOOK_BASE_URL=https://mehrsway.space`
