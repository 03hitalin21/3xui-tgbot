import re
import string
from pathlib import Path
from typing import Dict, List

DEFAULT_ADMIN_TELEGRAM_ID = 8477244366
DEFAULT_WEBHOOK_PATH = "telegram"
DEFAULT_WEBHOOK_LISTEN = "0.0.0.0"
DEFAULT_WEBHOOK_PORT = 8443
DEFAULT_MAX_PLAN_DAYS = 365
DEFAULT_MAX_PLAN_GB = 2000
DEFAULT_MAX_BULK_COUNT = 100
DEFAULT_XUI_SUBSCRIPTION_PORT = 2096

MAX_LIMIT_IP = 5
DEFAULT_FLOW = "xtls-rprx-vision"
DEFAULT_LIMIT_IP = 2
UNLIMITED_DEFAULT_LIMIT_IP = 1
MAX_LINKS_PER_MESSAGE = 10
LIST_PAGE_SIZE = 10
CANCEL_OPTIONS = {"cancel", "لغو"}
REMARK_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
SUB_ID_ALPHABET = string.ascii_lowercase + string.digits
WIZARD_RATE_LIMIT = 5
WIZARD_RATE_WINDOW = 600
WIZARD_STARTS: Dict[int, List[float]] = {}
BROADCAST_CHOOSE_TARGET = 1
BROADCAST_SEND_MESSAGE = 2
BROADCAST_PREVIEW_CONFIRM = 3
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

SETUP_PROMPT_FIELDS = [
    ("TELEGRAM_BOT_TOKEN", "Telegram bot token", "", "required"),
    ("ADMIN_TELEGRAM_ID", "Admin Telegram ID", "8477244366", "recommended"),
    ("XUI_BASE_URL", "x-ui panel URL", "", "required"),
    ("XUI_USERNAME", "x-ui username", "", "required"),
    ("XUI_PASSWORD", "x-ui password", "", "required"),
    ("XUI_SERVER_HOST", "x-ui server host/IP", "", "required"),
    ("XUI_SUBSCRIPTION_PORT", "x-ui subscription port", "2096", "recommended"),
    ("WEBHOOK_BASE_URL", "Webhook base URL", "", "required"),
    ("WEBHOOK_PATH", "Webhook path", "telegram", "recommended"),
    ("WEBHOOK_LISTEN", "Webhook listen address", "0.0.0.0", "recommended"),
    ("WEBHOOK_PORT", "Webhook port", "8443", "recommended"),
    ("WEBHOOK_SECRET_TOKEN", "Webhook secret token", "", "optional"),
    ("MAX_PLAN_DAYS", "Maximum plan days", "365", "recommended"),
    ("MAX_PLAN_GB", "Maximum plan GB", "2000", "recommended"),
    ("MAX_BULK_COUNT", "Maximum bulk client count", "100", "recommended"),
    ("BOT_DB_PATH", "SQLite DB path", "bot.db", "recommended"),
]

REQUIRED_ENV_KEYS = [
    "TELEGRAM_BOT_TOKEN",
    "XUI_BASE_URL",
    "XUI_USERNAME",
    "XUI_PASSWORD",
    "XUI_SERVER_HOST",
    "WEBHOOK_BASE_URL",
]
