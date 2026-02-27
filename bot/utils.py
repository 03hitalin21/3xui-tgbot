import logging
import os
import secrets
import string
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from telegram import InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.constants import (
    CANCEL_OPTIONS,
    DEFAULT_ADMIN_TELEGRAM_ID,
    DEFAULT_FLOW,
    DEFAULT_LIMIT_IP,
    DEFAULT_MAX_BULK_COUNT,
    DEFAULT_MAX_PLAN_DAYS,
    DEFAULT_MAX_PLAN_GB,
    DEFAULT_WEBHOOK_LISTEN,
    DEFAULT_WEBHOOK_PATH,
    DEFAULT_WEBHOOK_PORT,
    MAX_LIMIT_IP,
    MAX_LINKS_PER_MESSAGE,
    REMARK_PATTERN,
    SUB_ID_ALPHABET,
    UNLIMITED_DEFAULT_LIMIT_IP,
    WIZARD_RATE_LIMIT,
    WIZARD_RATE_WINDOW,
    WIZARD_STARTS,
)
from bot import ui
from core import orders as core_orders
from core import pricing as core_pricing
from core import wallet as core_wallet
import db

BOT_TOKEN = ""
ADMIN_TELEGRAM_ID = DEFAULT_ADMIN_TELEGRAM_ID
WEBHOOK_BASE_URL = ""
WEBHOOK_PATH = DEFAULT_WEBHOOK_PATH
WEBHOOK_LISTEN = DEFAULT_WEBHOOK_LISTEN
WEBHOOK_PORT = DEFAULT_WEBHOOK_PORT
WEBHOOK_SECRET_TOKEN = ""
LOW_BALANCE_THRESHOLD_ENV = None
LOW_BALANCE_THRESHOLD = 0.0
MAX_DAYS = DEFAULT_MAX_PLAN_DAYS
MAX_GB = DEFAULT_MAX_PLAN_GB
MAX_BULK_COUNT = DEFAULT_MAX_BULK_COUNT

BOT_LOG_PATH = os.getenv("BOT_LOG_PATH", "logs/bot.log")
log_dir = os.path.dirname(BOT_LOG_PATH)
if log_dir:
    os.makedirs(log_dir, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.FileHandler(BOT_LOG_PATH), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


class Plan:
    days: int
    gb: int

def as_int(v: str) -> Optional[int]:
    try:
        return int(v)
    except ValueError:
        return None

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_TELEGRAM_ID

def is_referral_agent(role: str) -> bool:
    return role in {"reseller", "agent"}

def get_user_role(tg_id: int) -> str:
    agent = db.get_agent(tg_id)
    return agent["role"] if agent else "buyer"

def generate_referral_code(length: int = 8) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))

def generate_sub_id(length: int = 16) -> str:
    return "".join(secrets.choice(SUB_ID_ALPHABET) for _ in range(length))

def parse_inbound_ids(text: str) -> Optional[List[int]]:
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        return None
    ids: List[int] = []
    for part in parts:
        value = parse_positive_int(part)
        if not value:
            return None
        if value not in ids:
            ids.append(value)
    return ids if ids else None

def reset_flow(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["flow"] = None
    context.user_data["wizard"] = {}

def cancel_keyboard() -> ReplyKeyboardMarkup:
    return ui.kb_cancel()

def is_cancel(text: str) -> bool:
    return text.strip().lower() in CANCEL_OPTIONS

def parse_positive_int(text: str) -> Optional[int]:
    if not text.isdigit():
        return None
    value = int(text)
    if value <= 0:
        return None
    return value

def clamp_limit_ip(value: int) -> int:
    return min(value, MAX_LIMIT_IP)

def normalize_remark(text: str) -> Optional[str]:
    remark = text.strip()
    if len(remark) < 2 or len(remark) > 64:
        return None
    if not REMARK_PATTERN.match(remark):
        return None
    return remark

def can_start_wizard(user_id: int) -> bool:
    now = time.time()
    timestamps = [ts for ts in WIZARD_STARTS.get(user_id, []) if now - ts < WIZARD_RATE_WINDOW]
    if len(timestamps) >= WIZARD_RATE_LIMIT:
        WIZARD_STARTS[user_id] = timestamps
        return False
    timestamps.append(now)
    WIZARD_STARTS[user_id] = timestamps
    return True

def preview_keyboard() -> InlineKeyboardMarkup:
    return ui.kb_preview()

def low_balance_keyboard() -> InlineKeyboardMarkup:
    return ui.kb_low_balance()

def broadcast_target_keyboard() -> InlineKeyboardMarkup:
    return ui.kb_broadcast_target()

def broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    return ui.kb_broadcast_confirm()

def client_actions_keyboard(rows: List[Dict], total_items: int, page: int) -> InlineKeyboardMarkup:
    return ui.kb_client_actions(rows, total_items, page)

def apply_runtime_config(cfg: Dict[str, object]) -> None:
    global BOT_TOKEN, ADMIN_TELEGRAM_ID, WEBHOOK_BASE_URL, WEBHOOK_PATH, WEBHOOK_LISTEN
    global WEBHOOK_PORT, WEBHOOK_SECRET_TOKEN, LOW_BALANCE_THRESHOLD_ENV, MAX_DAYS, MAX_GB, MAX_BULK_COUNT

    BOT_TOKEN = str(cfg["BOT_TOKEN"])
    ADMIN_TELEGRAM_ID = int(cfg["ADMIN_TELEGRAM_ID"])
    WEBHOOK_BASE_URL = str(cfg["WEBHOOK_BASE_URL"])
    WEBHOOK_PATH = str(cfg["WEBHOOK_PATH"])
    WEBHOOK_LISTEN = str(cfg["WEBHOOK_LISTEN"])
    WEBHOOK_PORT = int(cfg["WEBHOOK_PORT"])
    WEBHOOK_SECRET_TOKEN = str(cfg["WEBHOOK_SECRET_TOKEN"])
    LOW_BALANCE_THRESHOLD_ENV = cfg["LOW_BALANCE_THRESHOLD_ENV"]
    MAX_DAYS = int(cfg["MAX_DAYS"])
    MAX_GB = int(cfg["MAX_GB"])
    MAX_BULK_COUNT = int(cfg["MAX_BULK_COUNT"])

def load_low_balance_threshold() -> float:
    return core_wallet.load_low_balance_threshold(LOW_BALANCE_THRESHOLD_ENV, db)

def manual_payment_text() -> str:
    details = db.get_setting_text("manual_payment_details").strip()
    if not details:
        return ""
    return f"💳 روش پرداخت (انتقال دستی):\n{details}"

def toman(amount: float) -> str:
    try:
        val = float(amount)
    except (TypeError, ValueError):
        val = 0.0
    if val.is_integer():
        return f"{int(val):,} تومان"
    return f"{val:,.2f} تومان"

def expiry_value(days: int, start_after_first_use: bool) -> int:
    if start_after_first_use:
        return -int(days * 86400 * 1000)
    return int((time.time() + days * 86400) * 1000)

def main_menu(role: str) -> InlineKeyboardMarkup:
    return ui.kb_main_menu(role)

def create_menu() -> InlineKeyboardMarkup:
    return ui.kb_create_menu()

def settings_menu(admin: bool) -> InlineKeyboardMarkup:
    return ui.kb_settings_menu(admin)

async def send_links(update: Update, links: List[str]) -> None:
    message = update.effective_message
    for i in range(0, len(links), MAX_LINKS_PER_MESSAGE):
        await message.reply_text("\n".join(links[i:i + MAX_LINKS_PER_MESSAGE]))

def build_pagination(total_items: int, current_page: int, items_per_page: int, callback_prefix: str) -> InlineKeyboardMarkup:
    return ui.kb_pagination(total_items, current_page, items_per_page, callback_prefix)

def page_bounds(total_items: int, page: int, per_page: int) -> tuple[int, int, int]:
    total_pages = max((total_items - 1) // per_page + 1, 1)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page
    return page, offset, total_pages

def inbound_price(tg_id: int, inbound_id: int, days: int, gb: int, limit_ip: Optional[int] = None) -> float:
    return core_pricing.compute_agent_price(tg_id, inbound_id, days, gb, db, limit_ip, UNLIMITED_DEFAULT_LIMIT_IP)

def inbound_pricing_text(inbound_id: int) -> str:
    return core_pricing.inbound_pricing_text(inbound_id, db)

def inbound_pricing_text_list(inbound_ids: List[int]) -> str:
    return core_pricing.inbound_pricing_text_list(inbound_ids, db)

def order_count(w: Dict) -> int:
    return core_pricing.order_count(w)

def order_total_price(w: Dict) -> float:
    return core_pricing.calculate_price(w, db, UNLIMITED_DEFAULT_LIMIT_IP)

def wizard_summary(w: Dict, gross: float, discount: float, net: float) -> str:
    return core_orders.build_order_summary(w, gross, discount, net, inbound_pricing_text, inbound_pricing_text_list, toman)
