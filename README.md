# 3xui Telegram Bot Installer

## Quick Installation

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/03hitalin21/3xui-tgbot/New3/install.sh)
```

## TLS / Telegram Webhook notes

The setup wizard now supports:

1. Configuring domain/email values in `.env`.
2. Reusing existing certificates from `/etc/letsencrypt/live/<domain>/`.
3. Issuing a new Let's Encrypt certificate via Certbot (`webroot`) when no certificate exists.
4. Running Nginx on `80/443` with HTTP -> HTTPS redirect and ACME challenge handling.

Suggested flow from installer menu:

1. `Configure app (.env)`
2. `Acquire/configure TLS certificate (Let's Encrypt)`
3. `Start / restart containers`
4. `Set Telegram webhook now`

For your domain (`mehrsway.space` + optional `www.mehrsway.space`), set:

- `SSL_DOMAIN=mehrsway.space`
- `SSL_INCLUDE_WWW=true`
- `WEBHOOK_BASE_URL=https://mehrsway.space`
