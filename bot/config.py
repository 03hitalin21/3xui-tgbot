import os
from typing import Dict

import xui_api
from bot.constants import (
    DEFAULT_ADMIN_TELEGRAM_ID,
    DEFAULT_MAX_BULK_COUNT,
    DEFAULT_MAX_PLAN_DAYS,
    DEFAULT_MAX_PLAN_GB,
    DEFAULT_WEBHOOK_LISTEN,
    DEFAULT_WEBHOOK_PATH,
    DEFAULT_WEBHOOK_PORT,
    DEFAULT_XUI_SUBSCRIPTION_PORT,
    ENV_FILE,
    REQUIRED_ENV_KEYS,
    SETUP_PROMPT_FIELDS,
)


def _load_env_file() -> None:
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip()


def _save_env_file(values: Dict[str, str]) -> None:
    existing: Dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            existing[key.strip()] = value.strip()
    existing.update(values)
    lines = [f"{key}={value}" for key, value in sorted(existing.items())]
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _required_missing() -> str:
    missing = [k for k in REQUIRED_ENV_KEYS if not os.getenv(k)]
    return ", ".join(missing)


def _interactive_setup_if_needed() -> None:
    missing = _required_missing()
    if not missing or not os.isatty(0):
        return

    print("\nFirst-time setup: answer the prompts or press Enter to accept defaults.")
    print("Values will be saved to .env so you don't need to export them manually.\n")
    collected: Dict[str, str] = {}
    for key, label, default, level in SETUP_PROMPT_FIELDS:
        current = os.getenv(key, "")
        shown_default = current or default
        suffix = f" [{level}]"
        if shown_default:
            answer = input(f"{label} ({key}){suffix} [default: {shown_default}]: ").strip()
            value = answer or shown_default
        else:
            answer = input(f"{label} ({key}){suffix} [default: empty]: ").strip()
            value = answer
        os.environ[key] = value
        collected[key] = value

    _save_env_file(collected)
    print("\nSaved setup values to .env\n")


def _runtime_config() -> Dict[str, object]:
    return {
        "BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "ADMIN_TELEGRAM_ID": int(os.getenv("ADMIN_TELEGRAM_ID", str(DEFAULT_ADMIN_TELEGRAM_ID))),
        "WEBHOOK_BASE_URL": os.getenv("WEBHOOK_BASE_URL", "").rstrip("/"),
        "WEBHOOK_PATH": os.getenv("WEBHOOK_PATH", DEFAULT_WEBHOOK_PATH).lstrip("/"),
        "WEBHOOK_LISTEN": os.getenv("WEBHOOK_LISTEN", DEFAULT_WEBHOOK_LISTEN),
        "WEBHOOK_PORT": int(os.getenv("WEBHOOK_PORT", str(DEFAULT_WEBHOOK_PORT))),
        "WEBHOOK_SECRET_TOKEN": os.getenv("WEBHOOK_SECRET_TOKEN", ""),
        "LOW_BALANCE_THRESHOLD_ENV": os.getenv("LOW_BALANCE_THRESHOLD"),
        "MAX_DAYS": int(os.getenv("MAX_PLAN_DAYS", str(DEFAULT_MAX_PLAN_DAYS))),
        "MAX_GB": int(os.getenv("MAX_PLAN_GB", str(DEFAULT_MAX_PLAN_GB))),
        "MAX_BULK_COUNT": int(os.getenv("MAX_BULK_COUNT", str(DEFAULT_MAX_BULK_COUNT))),
        "missing": _required_missing(),
    }


def _apply_xui_runtime() -> None:
    xui_api.BASE_URL = os.getenv("XUI_BASE_URL", "")
    xui_api.USERNAME = os.getenv("XUI_USERNAME", "")
    xui_api.PASSWORD = os.getenv("XUI_PASSWORD", "")
    xui_api.SERVER_HOST = os.getenv("XUI_SERVER_HOST", "")
    xui_api.SUBSCRIPTION_PORT = int(os.getenv("XUI_SUBSCRIPTION_PORT", str(DEFAULT_XUI_SUBSCRIPTION_PORT)))


def load_config() -> Dict[str, object]:
    _load_env_file()
    cfg = _runtime_config()
    _apply_xui_runtime()
    _interactive_setup_if_needed()
    cfg = _runtime_config()
    _apply_xui_runtime()
    return cfg
