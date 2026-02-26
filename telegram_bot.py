import json
import logging
import os
from pathlib import Path
import re
import secrets
import string
import time
import uuid
from datetime import datetime
from dataclasses import dataclass
from typing import Dict, List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

import db
import xui_api
from xui_api import XUIApi, build_client_payload, subscription_link, vless_link

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "8477244366"))
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "").rstrip("/")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "telegram").lstrip("/")
WEBHOOK_LISTEN = os.getenv("WEBHOOK_LISTEN", "0.0.0.0")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8443"))
WEBHOOK_SECRET_TOKEN = os.getenv("WEBHOOK_SECRET_TOKEN", "")
LOW_BALANCE_THRESHOLD_ENV = os.getenv("LOW_BALANCE_THRESHOLD")
LOW_BALANCE_THRESHOLD = 0.0
MAX_DAYS = int(os.getenv("MAX_PLAN_DAYS", "365"))
MAX_GB = int(os.getenv("MAX_PLAN_GB", "2000"))
MAX_BULK_COUNT = int(os.getenv("MAX_BULK_COUNT", "100"))
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
ENV_FILE = Path(__file__).with_name(".env")
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


@dataclass
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
    return ReplyKeyboardMarkup([["لغو"]], resize_keyboard=True)


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
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ تایید", callback_data="wizard:confirm"),
                InlineKeyboardButton("✏️ ویرایش", callback_data="wizard:edit"),
            ],
            [InlineKeyboardButton("لغو", callback_data="wizard:cancel")],
        ]
    )


def low_balance_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("شارژ کیف پول", callback_data="menu:wallet")],
            [InlineKeyboardButton("ثبت درخواست شارژ", callback_data="menu:topup")],
            [InlineKeyboardButton("پشتیبانی", callback_data="menu:support")],
            [InlineKeyboardButton("ادامه", callback_data="menu:home")],
        ]
    )


def broadcast_target_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("همه کاربران", callback_data="broadcast:target:all"),
                InlineKeyboardButton("فقط نمایندگان", callback_data="broadcast:target:agents"),
            ]
        ]
    )


def broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ تایید", callback_data="broadcast:confirm"),
                InlineKeyboardButton("✏️ ویرایش", callback_data="broadcast:edit"),
            ],
            [InlineKeyboardButton("❌ لغو", callback_data="broadcast:cancel")],
        ]
    )


def client_actions_keyboard(rows: List[Dict], total_items: int, page: int) -> InlineKeyboardMarkup:
    buttons: List[List[InlineKeyboardButton]] = []
    for c in rows:
        cid = c["id"]
        buttons.append(
            [
                InlineKeyboardButton("نمایش کانفیگ", callback_data=f"client_action:{cid}:config"),
                InlineKeyboardButton("QR کد", callback_data=f"client_action:{cid}:qr"),
            ]
        )
        buttons.append(
            [
                InlineKeyboardButton("جزئیات", callback_data=f"client_action:{cid}:details"),
                InlineKeyboardButton("تمدید خودکار", callback_data=f"client_action:{cid}:renew"),
            ]
        )
    if total_items > LIST_PAGE_SIZE:
        buttons.extend(build_pagination(total_items, page, LIST_PAGE_SIZE, "page:clients").inline_keyboard)
    return InlineKeyboardMarkup(buttons)


def required_missing() -> str:
    required = [
        "TELEGRAM_BOT_TOKEN",
        "XUI_BASE_URL",
        "XUI_USERNAME",
        "XUI_PASSWORD",
        "XUI_SERVER_HOST",
        "WEBHOOK_BASE_URL",
    ]
    missing = [k for k in required if not os.getenv(k)]
    return ", ".join(missing)


def load_env_file() -> None:
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


def save_env_file(values: Dict[str, str]) -> None:
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


def interactive_setup_if_needed() -> None:
    missing = required_missing()
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

    save_env_file(collected)
    print("\nSaved setup values to .env\n")


def apply_runtime_config() -> None:
    global BOT_TOKEN, ADMIN_TELEGRAM_ID, WEBHOOK_BASE_URL, WEBHOOK_PATH, WEBHOOK_LISTEN
    global WEBHOOK_PORT, WEBHOOK_SECRET_TOKEN, LOW_BALANCE_THRESHOLD_ENV, MAX_DAYS, MAX_GB, MAX_BULK_COUNT

    BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "8477244366"))
    WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "").rstrip("/")
    WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "telegram").lstrip("/")
    WEBHOOK_LISTEN = os.getenv("WEBHOOK_LISTEN", "0.0.0.0")
    WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8443"))
    WEBHOOK_SECRET_TOKEN = os.getenv("WEBHOOK_SECRET_TOKEN", "")
    LOW_BALANCE_THRESHOLD_ENV = os.getenv("LOW_BALANCE_THRESHOLD")
    MAX_DAYS = int(os.getenv("MAX_PLAN_DAYS", "365"))
    MAX_GB = int(os.getenv("MAX_PLAN_GB", "2000"))
    MAX_BULK_COUNT = int(os.getenv("MAX_BULK_COUNT", "100"))

    xui_api.BASE_URL = os.getenv("XUI_BASE_URL", "")
    xui_api.USERNAME = os.getenv("XUI_USERNAME", "")
    xui_api.PASSWORD = os.getenv("XUI_PASSWORD", "")
    xui_api.SERVER_HOST = os.getenv("XUI_SERVER_HOST", "")
    xui_api.SUBSCRIPTION_PORT = int(os.getenv("XUI_SUBSCRIPTION_PORT", "2096"))


def load_low_balance_threshold() -> float:
    if LOW_BALANCE_THRESHOLD_ENV is not None:
        return float(LOW_BALANCE_THRESHOLD_ENV)
    return float(db.get_setting_float("low_balance_threshold"))


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
    rows = [
        [InlineKeyboardButton("📊 داشبورد", callback_data="menu:dashboard")],
        [InlineKeyboardButton("👤 کلاینت‌های من", callback_data="menu:my_clients")],
        [InlineKeyboardButton("➕ ساخت کلاینت", callback_data="menu:create_client")],
        [InlineKeyboardButton("🌐 لیست اینباندها", callback_data="menu:inbounds")],
        [InlineKeyboardButton("📦 پلن‌های پیشنهادی", callback_data="menu:suggested_plans")],
        [InlineKeyboardButton("💰 کیف پول / موجودی", callback_data="menu:wallet")],
        [InlineKeyboardButton("📄 تاریخچه تراکنش", callback_data="menu:tx")],
        [InlineKeyboardButton("🆘 پشتیبانی", callback_data="menu:support")],
    ]
    if role in {"reseller", "agent"}:
        rows.append([InlineKeyboardButton("🎁 معرفی دوستان", callback_data="menu:referral")])
    rows.append([InlineKeyboardButton("⚙️ تنظیمات", callback_data="menu:settings")])
    return InlineKeyboardMarkup(rows)


def create_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🛒 کلاینت تکی", callback_data="create:single")],
            [InlineKeyboardButton("📦 ساخت گروهی", callback_data="create:bulk")],
            [InlineKeyboardButton("🧩 کلاینت چند اینباند", callback_data="create:multi")],
            [InlineKeyboardButton("⬅️ بازگشت", callback_data="menu:home")],
        ]
    )


def settings_menu(admin: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📍 تنظیم اینباند پیش‌فرض", callback_data="settings:set_default_inbound")],
        [InlineKeyboardButton("🎟 اعمال کد تخفیف", callback_data="settings:promo")],
    ]
    if admin:
        rows.extend(
            [
                [InlineKeyboardButton("🛠 ادمین: ساخت اینباند", callback_data="admin:create_inbound")],
                [InlineKeyboardButton("💵 ادمین: قیمت‌گذاری سراسری", callback_data="admin:set_global_price")],
                [InlineKeyboardButton("🌐 ادمین: قانون اینباند", callback_data="admin:set_inbound_rule")],
                [InlineKeyboardButton("👥 ادمین: نمایندگان", callback_data="admin:resellers")],
                [InlineKeyboardButton("💳 ادمین: شارژ کیف پول", callback_data="admin:charge_wallet")],
            ]
        )
    rows.append([InlineKeyboardButton("⬅️ بازگشت", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


async def send_links(update: Update, links: List[str]) -> None:
    message = update.effective_message
    for i in range(0, len(links), MAX_LINKS_PER_MESSAGE):
        await message.reply_text("\n".join(links[i:i + MAX_LINKS_PER_MESSAGE]))


def build_pagination(total_items: int, current_page: int, items_per_page: int, callback_prefix: str) -> InlineKeyboardMarkup:
    total_pages = max((total_items - 1) // items_per_page + 1, 1)
    page = max(1, min(current_page, total_pages))
    buttons = []

    if page > 1:
        buttons.append(InlineKeyboardButton("«", callback_data=f"{callback_prefix}:1"))
        buttons.append(InlineKeyboardButton("‹", callback_data=f"{callback_prefix}:{page - 1}"))
    else:
        buttons.append(InlineKeyboardButton("«", callback_data=f"{callback_prefix}:1"))
        buttons.append(InlineKeyboardButton("‹", callback_data=f"{callback_prefix}:1"))

    start = max(1, page - 1)
    end = min(total_pages, page + 1)
    for p in range(start, end + 1):
        label = f"- {p} -" if p == page else str(p)
        buttons.append(InlineKeyboardButton(label, callback_data=f"{callback_prefix}:{p}"))

    if page < total_pages:
        buttons.append(InlineKeyboardButton("›", callback_data=f"{callback_prefix}:{page + 1}"))
        buttons.append(InlineKeyboardButton("»", callback_data=f"{callback_prefix}:{total_pages}"))
    else:
        buttons.append(InlineKeyboardButton("›", callback_data=f"{callback_prefix}:{total_pages}"))
        buttons.append(InlineKeyboardButton("»", callback_data=f"{callback_prefix}:{total_pages}"))

    return InlineKeyboardMarkup([buttons])


def page_bounds(total_items: int, page: int, per_page: int) -> tuple[int, int, int]:
    total_pages = max((total_items - 1) // per_page + 1, 1)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page
    return page, offset, total_pages


def inbound_price(tg_id: int, inbound_id: int, days: int, gb: int, limit_ip: Optional[int] = None) -> float:
    rule = db.inbound_rule(inbound_id)
    if rule and int(rule["enabled"]) == 0:
        raise ValueError("Selected inbound is disabled by admin")
    if gb == 0:
        limit = limit_ip if limit_ip in {1, 2, 3} else UNLIMITED_DEFAULT_LIMIT_IP
        return float(db.get_setting_float(f"price_unlimited_ip{limit}"))
    ppgb_default = db.get_setting_float("price_per_gb")
    ppday_default = db.get_setting_float("price_per_day")
    ppgb = float(rule["price_per_gb"]) if rule and rule["price_per_gb"] is not None else ppgb_default
    ppday = float(rule["price_per_day"]) if rule and rule["price_per_day"] is not None else ppday_default
    ppgb_eff = db.get_effective_price_per_gb(tg_id, ppgb)
    ppday_eff = db.get_effective_price_per_day(tg_id, ppday)
    return round(gb * ppgb_eff + days * ppday_eff, 2)


def inbound_pricing_text(inbound_id: int) -> str:
    rule = db.inbound_rule(inbound_id)
    ppgb = float(rule["price_per_gb"]) if rule and rule["price_per_gb"] is not None else db.get_setting_float("price_per_gb")
    ppday = float(rule["price_per_day"]) if rule and rule["price_per_day"] is not None else db.get_setting_float("price_per_day")
    return f"{ppgb} برای هر GB + {ppday} برای هر روز"


def inbound_pricing_text_list(inbound_ids: List[int]) -> str:
    return " | ".join(f"{inbound_id}: {inbound_pricing_text(inbound_id)}" for inbound_id in inbound_ids)


def order_count(w: Dict) -> int:
    if w["kind"] == "bulk":
        return int(w["count"])
    if w["kind"] == "multi":
        return len(w.get("inbound_ids") or [])
    return 1


def order_total_price(w: Dict) -> float:
    tg_id = int(w.get("tg_id", 0))
    count = order_count(w)
    if w["kind"] == "multi":
        total = sum(inbound_price(tg_id, i, w["days"], w["gb"], w.get("limit_ip")) for i in (w.get("inbound_ids") or []))
        return round(total, 2)
    unit = inbound_price(tg_id, w["inbound_id"], w["days"], w["gb"], w.get("limit_ip"))
    return round(unit * count, 2)


def wizard_summary(w: Dict, gross: float, discount: float, net: float) -> str:
    inbound_ids = w.get("inbound_ids")
    count = len(inbound_ids) if inbound_ids else w.get("count", 1)
    total_gb = w["gb"] * count
    inbound_label = ", ".join(str(i) for i in inbound_ids) if inbound_ids else str(w["inbound_id"])
    pricing_text = inbound_pricing_text_list(inbound_ids) if inbound_ids else inbound_pricing_text(w["inbound_id"])
    return (
        "🧾 <b>پیش‌نمایش سفارش</b>\n"
        f"تعداد کلاینت: <b>{count}</b>\n"
        f"مدت: <b>{w['days']} روز</b>\n"
        f"حجم کل: <b>{total_gb} گیگ</b>\n"
        f"هزینه کل: <b>{toman(net)}</b> (قیمت: {pricing_text})\n"
        f"اینباند: <b>{inbound_label}</b>\n"
        f"نام/پیشوند: <b>{w.get('remark') or w.get('base_remark')}</b>\n"
        f"شروع بعد از اولین استفاده: <b>{'بله' if w['start_after_first_use'] else 'خیر'}</b>\n"
        f"تمدید خودکار: <b>{'بله' if w['auto_renew'] else 'خیر'}</b>\n"
        f"تخفیف: <b>{discount}%</b> | مبلغ ناخالص: <b>{toman(gross)}</b>"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    role = "admin" if is_admin(u.id) else get_user_role(u.id)
    db.ensure_agent(u.id, u.username or "", u.full_name or "", role=role)
    if context.args:
        code = context.args[0].strip()
        referrer = db.get_agent_by_referral_code(code)
        if referrer and int(referrer["tg_id"]) != u.id:
            db.set_referred_by(u.id, int(referrer["tg_id"]))
    reset_flow(context)
    logger.info("user_start | user=%s | role=%s", u.id, role)
    await update.message.reply_text("به پنل فروش خوش آمدید 👋", reply_markup=main_menu(role))


async def photo_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("flow") != "topup_receipt":
        await update.message.reply_text("برای این عکس عملیاتی تعریف نشده است.")
        return
    req_id = context.user_data.get("topup_request_id")
    if not req_id:
        await update.message.reply_text("درخواست شارژ یافت نشد. دوباره /topup بزنید.")
        return
    photo = update.message.photo[-1]
    db.attach_topup_receipt(int(req_id), photo.file_id)
    context.user_data["flow"] = None
    context.user_data.pop("topup_request_id", None)
    await update.message.reply_text(f"✅ رسید برای درخواست #{req_id} ثبت شد. منتظر تایید ادمین باشید.")
    await context.bot.send_message(chat_id=ADMIN_TELEGRAM_ID, text=f"درخواست شارژ جدید #{req_id} برای تایید: /approvetopupid {req_id}")
    try:
        await context.bot.send_photo(chat_id=ADMIN_TELEGRAM_ID, photo=photo.file_id, caption=f"Receipt #{req_id}")
    except Exception:
        pass


async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = get_user_role(update.effective_user.id)
    await update.message.reply_text("منوی اصلی", reply_markup=main_menu(role))


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_flow(context)
    await update.message.reply_text(
        "عملیات لغو شد. به منوی اصلی بازگشتید.",
        reply_markup=ReplyKeyboardRemove(),
    )
    role = get_user_role(update.effective_user.id)
    await update.message.reply_text("منوی اصلی", reply_markup=main_menu(role))


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    role = "admin" if is_admin(uid) else get_user_role(uid)
    db.ensure_agent(uid, q.from_user.username or "", q.from_user.full_name or "", role=role)

    data = q.data
    if data == "menu:home":
        await q.message.reply_text("منوی اصلی", reply_markup=main_menu(role))
        return

    if data == "menu:dashboard":
        s = db.agent_stats(uid)
        await q.message.reply_text(
            f"📊 داشبورد\nموجودی: {toman(s['balance'])}\nتعداد کلاینت: {s['clients']}\nفروش امروز: {toman(s['today_sales'])}\nمجموع هزینه: {toman(s['spent'])}"
        )
        return

    if data == "menu:referral":
        await referral_info(q.message, context, uid, role)
        return

    if data == "menu:my_clients":
        total = db.count_clients(uid)
        if total == 0:
            await q.message.reply_text("هنوز کلاینتی ثبت نشده است.")
            return
        page, offset, total_pages = page_bounds(total, 1, LIST_PAGE_SIZE)
        rows = db.list_clients_paged(uid, LIST_PAGE_SIZE, offset)
        lines = [f"👤 Your clients (page {page}/{total_pages}):"]
        for c in rows:
            lines.append(f"• {c['email']} | inbound {c['inbound_id']} | {c['days']}d/{c['gb']}GB")
        await q.message.reply_text(
            "\n".join(lines),
            reply_markup=client_actions_keyboard(rows, total, page),
        )
        return

    if data == "menu:create_client":
        await q.message.reply_text("نوع ساخت را انتخاب کنید:", reply_markup=create_menu())
        return

    if data == "menu:suggested_plans":
        plans = db.list_plan_templates(get_user_role(uid))
        if not plans:
            await q.message.reply_text("هنوز پلن پیشنهادی ثبت نشده است.")
            return
        lines = ["📦 پلن‌های پیشنهادی:"]
        for p in plans[:20]:
            gb_txt = "نامحدود" if int(p["gb"]) == 0 else f"{p['gb']} گیگ"
            lines.append(f"• /useplan {p['id']} - {p['title']} ({p['days']} روز | {gb_txt} | {p['limit_ip']} کاربر)")
        await q.message.reply_text("\n".join(lines))
        return

    if data == "menu:inbounds":
        api = XUIApi()
        try:
            api.login()
            ins = api.list_inbounds()
        except Exception as exc:
            await q.message.reply_text(f"خطا از پنل: {exc}")
            return
        if not ins:
            await q.message.reply_text("اینباندی پیدا نشد.")
            return
        total = len(ins)
        if total == 0:
            await q.message.reply_text("اینباندی پیدا نشد.")
            return
        page, offset, total_pages = page_bounds(total, 1, LIST_PAGE_SIZE)
        lines = [f"🌐 اینباندها (صفحه {page}/{total_pages}):"]
        for i in ins[offset:offset + LIST_PAGE_SIZE]:
            rid = i.get("id")
            remark = i.get("remark", "-")
            port = i.get("port", "-")
            lines.append(f"• ID {rid} | {remark} | port {port}")
        await q.message.reply_text("\n".join(lines), reply_markup=build_pagination(total, page, LIST_PAGE_SIZE, "page:inbounds"))
        return

    if data == "menu:wallet":
        a = db.get_agent(uid)
        msg = [f"💰 موجودی: {toman(a['balance'] if a else 0)}"]
        payment_details = manual_payment_text()
        if payment_details:
            msg.append("")
            msg.append(payment_details)
            msg.append("برای شارژ روی «ثبت درخواست شارژ» بزنید.")
        await q.message.reply_text(
            "\n".join(msg),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ثبت درخواست شارژ", callback_data="menu:topup")]]),
        )
        return

    if data == "menu:topup":
        context.user_data["flow"] = "topup_amount"
        await q.message.reply_text(
            "مبلغ شارژ را وارد کنید (فقط عدد). مثال: 50000",
            reply_markup=cancel_keyboard(),
        )
        return

    if data == "menu:tx":
        total = db.count_transactions(uid)
        if total == 0:
            await q.message.reply_text("هنوز تراکنشی ثبت نشده است.")
            return
        page, offset, total_pages = page_bounds(total, 1, LIST_PAGE_SIZE)
        tx = db.list_transactions_paged(uid, LIST_PAGE_SIZE, offset)
        lines = [f"📄 تراکنش‌ها (صفحه {page}/{total_pages}):"]
        for t in tx:
            lines.append(f"• {toman(t['amount'])} | {t['reason']} | {time.strftime('%Y-%m-%d %H:%M', time.localtime(t['created_at']))}")
        await q.message.reply_text("\n".join(lines), reply_markup=build_pagination(total, page, LIST_PAGE_SIZE, "page:tx"))
        return

    if data == "menu:support":
        await q.message.reply_text("🆘 پشتیبانی\n" + db.get_setting_text("support_text"))
        return

    if data == "menu:settings":
        await q.message.reply_text("تنظیمات", reply_markup=settings_menu(is_admin(uid)))
        return

    if data == "settings:set_default_inbound":
        context.user_data["flow"] = "set_default_inbound"
        await q.message.reply_text("شناسه اینباند را برای ذخیره به‌عنوان پیش‌فرض ارسال کنید.")
        return

    if data == "settings:promo":
        context.user_data["flow"] = "promo_apply"
        await q.message.reply_text("الان کد تخفیف را ارسال کنید.")
        return

    if data == "create:single":
        if not can_start_wizard(uid):
            await q.message.reply_text("⏳ لطفاً کمی بعد دوباره تلاش کنید.")
            return
        context.user_data["flow"] = "wizard_inbound"
        context.user_data["wizard"] = {"kind": "single", "tg_id": uid}
        logger.info("wizard_start | user=%s | kind=single", uid)
        await q.message.reply_text(
            "➕ ساخت کلاینت تکی\nمرحله ۱/۷: شناسه اینباند را ارسال کنید (یا default).",
            reply_markup=cancel_keyboard(),
        )
        return

    if data == "create:bulk":
        if not can_start_wizard(uid):
            await q.message.reply_text("⏳ لطفاً کمی بعد دوباره تلاش کنید.")
            return
        context.user_data["flow"] = "wizard_inbound"
        context.user_data["wizard"] = {"kind": "bulk", "tg_id": uid}
        logger.info("wizard_start | user=%s | kind=bulk", uid)
        await q.message.reply_text(
            "➕ Bulk client wizard\nStep 1/8: send inbound ID (or type: default).",
            reply_markup=cancel_keyboard(),
        )
        return

    if data == "create:multi":
        if not can_start_wizard(uid):
            await q.message.reply_text("⏳ لطفاً کمی بعد دوباره تلاش کنید.")
            return
        context.user_data["flow"] = "wizard_inbounds"
        context.user_data["wizard"] = {"kind": "multi", "tg_id": uid}
        logger.info("wizard_start | user=%s | kind=multi", uid)
        await q.message.reply_text(
            "➕ Multi-inbound client wizard\nStep 1/7: send inbound IDs separated by comma. Example: 1,2,3",
            reply_markup=cancel_keyboard(),
        )
        return

    if data.startswith("admin:"):
        if not is_admin(uid):
            await q.message.reply_text("این گزینه فقط برای ادمین است.")
            return
        if data == "admin:create_inbound":
            context.user_data["flow"] = "admin_create_inbound"
            await q.message.reply_text("ارسال کنید: <port> <remark> [protocol] [network]")
        elif data == "admin:set_global_price":
            context.user_data["flow"] = "admin_set_global_price"
            await q.message.reply_text("ارسال کنید: <price_per_gb> <price_per_day>\nنمونه: 2000 100")
        elif data == "admin:set_inbound_rule":
            context.user_data["flow"] = "admin_set_inbound_rule"
            await q.message.reply_text("ارسال کنید: <inbound_id> <enabled 1/0> <price_per_gb or -> <price_per_day or ->")
        elif data == "admin:resellers":
            rows = db.list_resellers(limit=50)
            if not rows:
                await q.message.reply_text("نماینده‌ای یافت نشد.")
            else:
                txt = ["👥 Resellers:"]
                for r in rows:
                    txt.append(f"• {r['tg_id']} | {r['username'] or '-'} | bal={r['balance']} | active={r['is_active']}")
                await q.message.reply_text("\n".join(txt[:60]))
        elif data == "admin:charge_wallet":
            context.user_data["flow"] = "admin_charge_wallet"
            await q.message.reply_text("ارسال کنید: <tg_id> <amount>\nنمونه: 123456 50000")
        return

    if data.startswith("wizard:"):
        action = data.split(":", 1)[1]
        if action == "cancel":
            reset_flow(context)
            context.user_data.pop("promo_discount", None)
            await q.message.reply_text(
                "عملیات لغو شد. به منوی اصلی بازگشتید.",
                reply_markup=ReplyKeyboardRemove(),
            )
            await q.message.reply_text("منوی اصلی", reply_markup=main_menu(role))
            return
        if action == "edit":
            context.user_data["flow"] = "wizard_days"
            await q.message.reply_text("مرحله ویرایش: تعداد روزها را ارسال کنید.", reply_markup=cancel_keyboard())
            return
        if action == "confirm":
            await finalize_order(update, context, context.user_data.get("wizard", {}))
            return

    if data.startswith("client_action:"):
        parts = data.split(":")
        if len(parts) != 3:
            await q.message.reply_text("عملیات کلاینت نامعتبر است.")
            return
        client_id = as_int(parts[1])
        action = parts[2]
        if not client_id:
            await q.message.reply_text("کلاینت نامعتبر است.")
            return
        client = db.get_client(uid, client_id)
        if not client:
            await q.message.reply_text("کلاینت پیدا نشد.")
            return
        if action == "config":
            await q.message.reply_text(f"🔐 کانفیگ:\n{client['vless_link']}")
            return
        if action == "qr":
            qr = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={client['vless_link']}"
            await q.message.reply_photo(qr)
            return
        if action == "details":
            created_at = datetime.fromtimestamp(client["created_at"]).strftime("%Y-%m-%d %H:%M")
            if client["start_after_first_use"]:
                expiry_text = "شروع بعد از اولین استفاده"
            else:
                expiry_ts = client["created_at"] + client["days"] * 86400
                expiry_text = datetime.fromtimestamp(expiry_ts).strftime("%Y-%m-%d")
            await q.message.reply_text(
                "ℹ️ جزئیات کلاینت\n"
                f"Remark: {client['email']}\n"
                f"اینباند: {client['inbound_id']}\n"
                f"Subscription: {client['subscription_link']}\n"
                f"مدت: {client['days']} روز | حجم: {client['gb']} گیگ\n"
                f"تاریخ ایجاد: {created_at}\n"
                f"انقضا: {expiry_text}\n"
                f"تمدید خودکار: {'فعال' if client['auto_renew'] else 'غیرفعال'}"
            )
            return
        if action == "renew":
            new_value = not bool(client["auto_renew"])
            db.update_client_auto_renew(uid, client_id, new_value)
            logger.info("client_auto_renew_toggle | user=%s | client=%s | enabled=%s", uid, client_id, new_value)
            await q.message.reply_text(f"✅ تمدید خودکار {'فعال شد' if new_value else 'غیرفعال شد'}.")
            return

    if data.startswith("page:"):
        parts = data.split(":")
        if len(parts) < 3:
            await q.message.reply_text("درخواست صفحه نامعتبر است.")
            return
        page_type = parts[1]
        page_num = as_int(parts[2]) or 1

        if page_type == "clients":
            total = db.count_clients(uid)
            if total == 0:
                await q.message.edit_message_text("هنوز کلاینتی ثبت نشده است.")
                return
            page, offset, total_pages = page_bounds(total, page_num, LIST_PAGE_SIZE)
            rows = db.list_clients_paged(uid, LIST_PAGE_SIZE, offset)
            lines = [f"👤 Your clients (page {page}/{total_pages}):"]
            for c in rows:
                lines.append(f"• {c['email']} | inbound {c['inbound_id']} | {c['days']}d/{c['gb']}GB")
            await q.message.edit_message_text("\n".join(lines))
            await q.message.edit_message_reply_markup(client_actions_keyboard(rows, total, page))
            return

        if page_type == "tx":
            total = db.count_transactions(uid)
            if total == 0:
                await q.message.edit_message_text("هنوز تراکنشی ثبت نشده است.")
                return
            page, offset, total_pages = page_bounds(total, page_num, LIST_PAGE_SIZE)
            rows = db.list_transactions_paged(uid, LIST_PAGE_SIZE, offset)
            lines = [f"📄 تراکنش‌ها (صفحه {page}/{total_pages}):"]
            for t in rows:
                lines.append(f"• {toman(t['amount'])} | {t['reason']} | {time.strftime('%Y-%m-%d %H:%M', time.localtime(t['created_at']))}")
            await q.message.edit_message_text("\n".join(lines))
            await q.message.edit_message_reply_markup(build_pagination(total, page, LIST_PAGE_SIZE, "page:tx"))
            return

        if page_type == "inbounds":
            api = XUIApi()
            try:
                api.login()
                ins = api.list_inbounds()
            except Exception as exc:
                await q.message.edit_message_text(f"خطا از پنل: {exc}")
                return
            total = len(ins)
            if total == 0:
                await q.message.edit_message_text("اینباندی پیدا نشد.")
                return
            page, offset, total_pages = page_bounds(total, page_num, LIST_PAGE_SIZE)
            lines = [f"🌐 اینباندها (صفحه {page}/{total_pages}):"]
            for i in ins[offset:offset + LIST_PAGE_SIZE]:
                rid = i.get("id")
                remark = i.get("remark", "-")
                port = i.get("port", "-")
                lines.append(f"• ID {rid} | {remark} | port {port}")
            await q.message.edit_message_text("\n".join(lines))
            await q.message.edit_message_reply_markup(build_pagination(total, page, LIST_PAGE_SIZE, "page:inbounds"))
            return

    await q.message.reply_text("عملیات ناشناخته است.")


async def text_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    uid = update.effective_user.id
    agent = db.get_agent(uid)
    flow = context.user_data.get("flow")
    w = context.user_data.get("wizard", {})

    if is_cancel(txt):
        reset_flow(context)
        context.user_data.pop("promo_discount", None)
        await update.message.reply_text(
            "عملیات لغو شد. به منوی اصلی بازگشتید.",
            reply_markup=ReplyKeyboardRemove(),
        )
        role = agent["role"] if agent else "buyer"
        await update.message.reply_text("منوی اصلی", reply_markup=main_menu(role))
        return

    if flow == "set_default_inbound":
        iid = as_int(txt)
        if not iid or iid <= 0:
            await update.message.reply_text("شناسه اینباند نامعتبر است")
            return
        db.set_preferred_inbound(uid, iid)
        reset_flow(context)
        await update.message.reply_text(f"اینباند پیش‌فرض روی {iid} تنظیم شد")
        return

    if flow == "promo_apply":
        try:
            disc = db.apply_promo(txt, uid)
        except ValueError as exc:
            await update.message.reply_text(str(exc))
            return
        context.user_data["promo_discount"] = disc
        reset_flow(context)
        await update.message.reply_text(f"کد تخفیف اعمال شد: {disc}% برای سفارش بعدی")
        return

    if flow == "register_agent_experience":
        exp = parse_positive_int(txt)
        if exp is None or exp > 50:
            await update.message.reply_text("سابقه نامعتبر است. یک عدد بین 0 تا 50 ارسال کنید.", reply_markup=cancel_keyboard())
            return
        context.user_data["register_agent_experience"] = exp
        context.user_data["flow"] = "register_agent_history"
        await update.message.reply_text(
            "لطفاً خلاصه سوابق کاری خود را ارسال کنید (حداقل 10 کاراکتر).",
            reply_markup=cancel_keyboard(),
        )
        return

    if flow == "register_agent_history":
        history = txt.strip()
        if len(history) < 10:
            await update.message.reply_text("لطفاً توضیحات کامل‌تری از سابقه کاری خود ارسال کنید.", reply_markup=cancel_keyboard())
            return
        exp = context.user_data.pop("register_agent_experience", 0)
        user = update.effective_user
        db.ensure_agent(user.id, user.username or "", user.full_name or "", role="agent")
        db.set_agent_registration(user.id, True)
        db.set_agent_profile(user.id, exp, history)
        reset_flow(context)
        await update.message.reply_text(
            "✅ ثبت‌نام نماینده انجام شد. اطلاعات شما برای تعیین قیمت اختصاصی ثبت شد.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await update.message.reply_text("منوی اصلی", reply_markup=main_menu(get_user_role(user.id)))
        try:
            await context.bot.send_message(
                chat_id=ADMIN_TELEGRAM_ID,
                text=(
                    "📥 ثبت‌نام نماینده جدید\n"
                    f"ID: {user.id}\n"
                    f"نام کاربری: @{user.username if user.username else '-'}\n"
                    f"نام: {user.full_name or '-'}\n"
                    f"سابقه: {exp} سال\n"
                    f"سوابق: {history}\n"
                    "برای قیمت اختصاصی: /admin/users"
                ),
            )
        except Exception:
            pass
        return

    if flow == "topup_amount":
        try:
            amt = float(txt)
        except ValueError:
            await update.message.reply_text("مبلغ باید عدد باشد", reply_markup=cancel_keyboard())
            return
        if amt <= 0:
            await update.message.reply_text("مبلغ باید بیشتر از صفر باشد", reply_markup=cancel_keyboard())
            return
        req_id = db.create_topup_request(uid, amt)
        context.user_data["flow"] = "topup_receipt"
        context.user_data["topup_request_id"] = req_id
        details = manual_payment_text()
        msg = [f"درخواست #{req_id} ثبت شد."]
        if details:
            msg.append(details)
        msg.append("پس از انتقال، لطفاً رسید پرداخت را به صورت عکس همینجا ارسال کنید.")
        await update.message.reply_text("\n\n".join(msg), reply_markup=ReplyKeyboardRemove())
        return

    # Admin flows
    if flow == "admin_create_inbound":
        if not is_admin(uid):
            await update.message.reply_text("دسترسی ندارید")
            return
        parts = txt.split()
        if len(parts) < 2:
            await update.message.reply_text("فرمت: <port> <remark> [protocol] [network]")
            return
        port = as_int(parts[0])
        if not port:
            await update.message.reply_text("پورت نامعتبر است")
            return
        api = XUIApi()
        try:
            api.login()
            inbound_id = api.create_inbound(port, parts[1], parts[2] if len(parts) > 2 else "vless", parts[3] if len(parts) > 3 else "tcp")
        except Exception as exc:
            await update.message.reply_text(f"ناموفق: {exc}")
            return
        reset_flow(context)
        logger.info("admin_create_inbound | admin=%s | inbound=%s", uid, inbound_id)
        await update.message.reply_text(f"اینباند با شناسه {inbound_id} ساخته شد")
        return

    if flow == "admin_set_global_price":
        if not is_admin(uid):
            await update.message.reply_text("دسترسی ندارید")
            return
        parts = txt.split()
        if len(parts) != 2:
            await update.message.reply_text("فرمت: <price_per_gb> <price_per_day>")
            return
        try:
            pgb = float(parts[0]); pday = float(parts[1])
        except ValueError:
            await update.message.reply_text("مقادیر قیمت باید عددی باشند")
            return
        db.set_setting("price_per_gb", str(pgb))
        db.set_setting("price_per_day", str(pday))
        reset_flow(context)
        logger.info("admin_set_global_price | admin=%s | ppgb=%s | ppday=%s", uid, pgb, pday)
        await update.message.reply_text("قیمت‌گذاری سراسری به‌روزرسانی شد.")
        return

    if flow == "admin_set_inbound_rule":
        if not is_admin(uid):
            await update.message.reply_text("دسترسی ندارید")
            return
        parts = txt.split()
        if len(parts) != 4:
            await update.message.reply_text("فرمت: <inbound_id> <enabled 1/0> <price_per_gb or -> <price_per_day or ->")
            return
        iid = as_int(parts[0]); en = as_int(parts[1])
        if not iid or en not in [0, 1]:
            await update.message.reply_text("inbound_id یا enabled نامعتبر است")
            return
        pgb = None if parts[2] == "-" else float(parts[2])
        pday = None if parts[3] == "-" else float(parts[3])
        db.set_inbound_rule(iid, bool(en), pgb, pday)
        reset_flow(context)
        logger.info("admin_set_inbound_rule | admin=%s | inbound=%s | enabled=%s", uid, iid, en)
        await update.message.reply_text("قانون قیمت/فعال‌سازی اینباند ذخیره شد.")
        return

    if flow == "admin_charge_wallet":
        if not is_admin(uid):
            await update.message.reply_text("دسترسی ندارید")
            return
        parts = txt.split()
        if len(parts) != 2:
            await update.message.reply_text("فرمت: <tg_id> <amount>")
            return
        tid = as_int(parts[0])
        try:
            amount = float(parts[1])
        except ValueError:
            await update.message.reply_text("مبلغ باید عددی باشد")
            return
        if not tid:
            await update.message.reply_text("شناسه کاربر نامعتبر است")
            return
        db.ensure_agent(tid, "", "", role="buyer")
        bal = db.add_balance(tid, amount, "topup.admin", meta=f"by:{uid}")
        reset_flow(context)
        logger.info("admin_charge_wallet | admin=%s | target=%s | amount=%s", uid, tid, amount)
        await update.message.reply_text(f"کیف پول به‌روزرسانی شد. موجودی جدید: {toman(bal)}")
        return

    # Wizard flows
    if flow == "wizard_inbounds":
        inbound_ids = parse_inbound_ids(txt)
        if not inbound_ids:
            await update.message.reply_text(
                "لیست اینباند نامعتبر است. شناسه‌ها را با کاما بفرستید، مثل: 1,2,3",
                reply_markup=cancel_keyboard(),
            )
            return
        w["inbound_ids"] = inbound_ids
        context.user_data["wizard"] = w
        context.user_data["flow"] = "wizard_remark"
        await update.message.reply_text(
            "Step 2/7: send client remark/email. Hint: user123",
            reply_markup=cancel_keyboard(),
        )
        return

    if flow == "wizard_inbound":
        if txt.lower() == "default":
            if not agent or not agent["preferred_inbound"]:
                await update.message.reply_text(
                    "No default inbound set. Send numeric inbound ID.",
                    reply_markup=cancel_keyboard(),
                )
                return
            w["inbound_id"] = int(agent["preferred_inbound"])
        else:
            iid = parse_positive_int(txt)
            if not iid:
                await update.message.reply_text("شناسه اینباند نامعتبر است. فقط عدد ارسال کنید.", reply_markup=cancel_keyboard())
                return
            w["inbound_id"] = iid
        context.user_data["wizard"] = w
        if w["kind"] == "single":
            context.user_data["flow"] = "wizard_remark"
            await update.message.reply_text(
                "Step 2/7: send client remark/email. Hint: user123",
                reply_markup=cancel_keyboard(),
            )
        else:
            context.user_data["flow"] = "wizard_base"
            await update.message.reply_text(
                "Step 2/8: send base remark for bulk. Hint: teamA",
                reply_markup=cancel_keyboard(),
            )
        return

    if flow == "wizard_remark":
        remark = normalize_remark(txt)
        if not remark:
            await update.message.reply_text(
                "Remark must be 2-64 chars using letters, numbers, underscore, or dash only.",
                reply_markup=cancel_keyboard(),
            )
            return
        w["remark"] = remark
        context.user_data["flow"] = "wizard_days"
        await update.message.reply_text("مرحله ۳/۷: تعداد روز را ارسال کنید. مثال: 30", reply_markup=cancel_keyboard())
        return

    if flow == "wizard_base":
        base_remark = normalize_remark(txt)
        if not base_remark:
            await update.message.reply_text(
                "Base remark must be 2-64 chars using letters, numbers, underscore, or dash only.",
                reply_markup=cancel_keyboard(),
            )
            return
        w["base_remark"] = base_remark
        context.user_data["flow"] = "wizard_count"
        await update.message.reply_text("مرحله ۳/۸: تعداد کلاینت را ارسال کنید. مثال: 5", reply_markup=cancel_keyboard())
        return

    if flow == "wizard_count":
        c = parse_positive_int(txt)
        if not c or c > MAX_BULK_COUNT:
            await update.message.reply_text(
                f"تعداد نامعتبر است. عددی بین 1 تا {MAX_BULK_COUNT} وارد کنید.",
                reply_markup=cancel_keyboard(),
            )
            return
        w["count"] = c
        context.user_data["flow"] = "wizard_days"
        await update.message.reply_text("مرحله ۴/۸: تعداد روز را ارسال کنید. مثال: 30", reply_markup=cancel_keyboard())
        return

    if flow == "wizard_days":
        d = parse_positive_int(txt)
        if not d or d > MAX_DAYS:
            await update.message.reply_text(
                f"روز نامعتبر است. عددی بین 1 تا {MAX_DAYS} وارد کنید.",
                reply_markup=cancel_keyboard(),
            )
            return
        w["days"] = d
        context.user_data["flow"] = "wizard_gb"
        step = "Step 4/7" if w["kind"] in {"single", "multi"} else "Step 5/8"
        await update.message.reply_text(f"{step}: حجم کل (گیگ) را ارسال کنید. مثال: 50", reply_markup=cancel_keyboard())
        return

    if flow == "wizard_gb":
        if txt.strip() in {"0", "نامحدود", "unlimited"}:
            g = 0
        else:
            g = parse_positive_int(txt)
        if g is None or g < 0 or g > MAX_GB:
            await update.message.reply_text(
                f"حجم نامعتبر است. عددی بین 0 تا {MAX_GB} وارد کنید. (0 = نامحدود)",
                reply_markup=cancel_keyboard(),
            )
            return
        w["gb"] = g
        if g == 0:
            context.user_data["flow"] = "wizard_limit_ip"
            await update.message.reply_text("تعداد کاربر همزمان را انتخاب کنید (1/2/3). پیش‌فرض 1.", reply_markup=cancel_keyboard())
            return
        context.user_data["flow"] = "wizard_start_after_first_use"
        step = "Step 5/7" if w["kind"] in {"single", "multi"} else "Step 6/8"
        await update.message.reply_text(f"{step}: شروع پس از اولین استفاده؟ (y/n)", reply_markup=cancel_keyboard())
        return

    if flow == "wizard_limit_ip":
        v = txt.strip() or "1"
        if v not in {"1", "2", "3"}:
            await update.message.reply_text("فقط یکی از مقادیر 1 یا 2 یا 3 را ارسال کنید.", reply_markup=cancel_keyboard())
            return
        w["limit_ip"] = int(v)
        context.user_data["flow"] = "wizard_start_after_first_use"
        await update.message.reply_text("بعد از اولین استفاده شروع شود؟ (y/n)", reply_markup=cancel_keyboard())
        return

    if flow == "wizard_start_after_first_use":
        v = txt.lower()
        if v not in ["y", "n", "yes", "no"]:
            await update.message.reply_text("لطفاً فقط y یا n ارسال کنید", reply_markup=cancel_keyboard())
            return
        w["start_after_first_use"] = v in ["y", "yes"]
        context.user_data["flow"] = "wizard_auto_renew"
        step = "Step 6/7" if w["kind"] in {"single", "multi"} else "Step 7/8"
        await update.message.reply_text(
            f"{step}: Enable auto-renew? (y/n)\nHint: auto-renew resets one day before expiry.",
            reply_markup=cancel_keyboard(),
        )
        return

    if flow == "wizard_auto_renew":
        v = txt.lower()
        if v not in ["y", "n", "yes", "no"]:
            await update.message.reply_text("لطفاً فقط y یا n ارسال کنید", reply_markup=cancel_keyboard())
            return
        w["auto_renew"] = v in ["y", "yes"]

        try:
            gross = order_total_price(w)
        except ValueError as exc:
            reset_flow(context)
            await update.message.reply_text(str(exc))
            return

        count = order_count(w)
        discount = float(context.user_data.get("promo_discount", 0.0))
        net = round(gross * (1 - discount / 100), 2)
        context.user_data["flow"] = "wizard_preview"
        await update.message.reply_text(
            wizard_summary(w, gross, discount, net),
            parse_mode="HTML",
            reply_markup=preview_keyboard(),
        )
        return

    if flow == "wizard_preview":
        v = txt.lower()
        if v in ["n", "no"]:
            reset_flow(context)
            context.user_data.pop("promo_discount", None)
            await update.message.reply_text(
                "عملیات لغو شد. به منوی اصلی بازگشتید.",
                reply_markup=ReplyKeyboardRemove(),
            )
            role = agent["role"] if agent else "buyer"
            await update.message.reply_text("منوی اصلی", reply_markup=main_menu(role))
            return
        if v not in ["y", "yes"]:
            await update.message.reply_text("لطفاً فقط بله یا خیر (yes/no) ارسال کنید", reply_markup=cancel_keyboard())
            return
        await finalize_order(update, context, w)
        return

    await update.message.reply_text("دستور /start را بزنید و از دکمه‌های منو انتخاب کنید.")


async def finalize_order(update: Update, context: ContextTypes.DEFAULT_TYPE, w: Dict):
    effective_message = update.effective_message
    uid = update.effective_user.id
    count = order_count(w)
    gross = order_total_price(w)
    disc = float(context.user_data.pop("promo_discount", 0.0))
    net = round(gross * (1 - disc / 100), 2)
    auto_renew = bool(w.get("auto_renew", False))
    reset_days = max(w["days"] - 1, 0) if auto_renew else 0
    inbound_ids = w.get("inbound_ids") or [w["inbound_id"]]

    ag = db.get_agent(uid)
    if not ag or int(ag["is_active"]) != 1:
        reset_flow(context)
        await effective_message.reply_text(
            "Your reseller account is disabled. Contact admin.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    try:
        db.deduct_balance(uid, net, "order.charge", json.dumps({"kind": w["kind"], "inbound": w["inbound_id"]}))
        logger.info("order_deduct | user=%s | amount=%s | kind=%s", uid, net, w["kind"])
    except ValueError:
        reset_flow(context)
        await effective_message.reply_text(
            f"موجودی کافی نیست. مبلغ موردنیاز: {toman(net)}",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    api = XUIApi()
    links: List[str] = []
    subscription_links: List[str] = []
    expiry = expiry_value(w["days"], w["start_after_first_use"])

    try:
        api.login()
        logger.info("api_login | user=%s", uid)
        if w["kind"] == "single":
            inbound = api.get_inbound(w["inbound_id"])
            clients = []
            uidc = str(uuid.uuid4())
            email = w["remark"]
            sub_id = generate_sub_id()
            sub_link = subscription_link(sub_id)
            limit_ip = clamp_limit_ip(int(w.get("limit_ip") or (UNLIMITED_DEFAULT_LIMIT_IP if int(w.get("gb", 0)) == 0 else DEFAULT_LIMIT_IP)))
            clients.append(build_client_payload(
                uidc,
                email,
                expiry,
                int(w["gb"]),
                sub_id,
                str(uid),
                flow=DEFAULT_FLOW,
                reset=reset_days,
                limit_ip=limit_ip,
            ))
            link = vless_link(uidc, inbound, email)
            links.append(link)
            subscription_links.append(sub_link)
            db.save_created_client(
                uid,
                w["inbound_id"],
                email,
                uidc,
                link,
                sub_id,
                sub_link,
                w["days"],
                w["gb"],
                w["start_after_first_use"],
                auto_renew,
            )
            api.add_clients(w["inbound_id"], clients)
        elif w["kind"] == "bulk":
            inbound = api.get_inbound(w["inbound_id"])
            clients = []
            limit_ip = clamp_limit_ip(int(w.get("limit_ip") or (UNLIMITED_DEFAULT_LIMIT_IP if int(w.get("gb", 0)) == 0 else DEFAULT_LIMIT_IP)))
            for i in range(w["count"]):
                uidc = str(uuid.uuid4())
                email = f"{w['base_remark']}_{i+1}"
                sub_id = generate_sub_id()
                sub_link = subscription_link(sub_id)
                clients.append(build_client_payload(
                    uidc,
                    email,
                    expiry,
                    int(w["gb"]),
                    sub_id,
                    str(uid),
                    flow=DEFAULT_FLOW,
                    reset=reset_days,
                    limit_ip=limit_ip,
                ))
                link = vless_link(uidc, inbound, email)
                links.append(link)
                subscription_links.append(sub_link)
                db.save_created_client(
                    uid,
                    w["inbound_id"],
                    email,
                    uidc,
                    link,
                    sub_id,
                    sub_link,
                    w["days"],
                    w["gb"],
                    w["start_after_first_use"],
                    auto_renew,
                )
            api.add_clients(w["inbound_id"], clients)
        else:
            sub_id = generate_sub_id()
            sub_link = subscription_link(sub_id)
            subscription_links.append(sub_link)
            limit_ip = clamp_limit_ip(int(w.get("limit_ip") or (UNLIMITED_DEFAULT_LIMIT_IP if int(w.get("gb", 0)) == 0 else DEFAULT_LIMIT_IP)))
            for inbound_id in inbound_ids:
                inbound = api.get_inbound(inbound_id)
                uidc = str(uuid.uuid4())
                email = w["remark"]
                client = build_client_payload(
                    uidc,
                    email,
                    expiry,
                    int(w["gb"]),
                    sub_id,
                    str(uid),
                    flow=DEFAULT_FLOW,
                    reset=reset_days,
                    limit_ip=limit_ip,
                )
                api.add_clients(inbound_id, [client])
                link = vless_link(uidc, inbound, email)
                links.append(link)
                db.save_created_client(
                    uid,
                    inbound_id,
                    email,
                    uidc,
                    link,
                    sub_id,
                    sub_link,
                    w["days"],
                    w["gb"],
                    w["start_after_first_use"],
                    auto_renew,
                )

        db.create_order(uid, inbound_ids[0], w["kind"], w["days"], w["gb"], count, gross, disc, net, "success")
        logger.info("order_success | user=%s | inbound=%s | count=%s", uid, inbound_ids[0], count)
    except Exception as exc:
        db.add_balance(uid, net, "order.refund", str(exc))
        db.create_order(uid, inbound_ids[0], w["kind"], w["days"], w["gb"], count, gross, disc, net, "failed")
        logger.error("order_failed | user=%s | error=%s", uid, exc)
        reset_flow(context)
        await effective_message.reply_text(
            "⚠️ در حال حاضر امکان ساخت کلاینت وجود ندارد. مبلغ به کیف پول شما برگشت داده شد. لطفاً بعداً دوباره تلاش کنید.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    bal = db.get_agent(uid)["balance"]
    inbound_label = ", ".join(str(i) for i in inbound_ids)
    summary = (
        f"✅ کلاینت(ها) با موفقیت ساخته شد\nنوع: {w['kind']}\nاینباند: {inbound_label}\n"
        f"مدت: {w['days']} روز | حجم: {w['gb']} گیگ | تعداد: {count}\n"
        f"شروع پس از اولین استفاده: {'بله' if w['start_after_first_use'] else 'خیر'} | تمدید خودکار: {'بله' if auto_renew else 'خیر'}\n"
        f"مبلغ ناخالص: {toman(gross)}\nتخفیف: {disc}%\nکسرشده: {toman(net)}\nموجودی: {toman(bal)}"
    )
    configs = "\n".join(links)
    subs = "\n".join(subscription_links)
    sections = [summary]
    if configs:
        sections.append(f"کانفیگ‌ها:\n{configs}")
    if subs:
        sections.append(f"لینک‌های اشتراک:\n{subs}")
    message_text = "\n\n".join(sections)
    if len(message_text) <= 4000:
        await update.effective_message.reply_text(message_text, reply_markup=ReplyKeyboardRemove())
    else:
        await update.effective_message.reply_text(summary, reply_markup=ReplyKeyboardRemove())
        await send_links(update, links)

    # QR preview for single client
    if len(links) == 1:
        qr = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={links[0]}"
        await update.effective_message.reply_photo(qr)

    if bal < LOW_BALANCE_THRESHOLD:
        await update.effective_message.reply_text(
            "⚠️ موجودی شما کم است. برای جلوگیری از اختلال، کیف پول را شارژ کنید.",
            reply_markup=low_balance_keyboard(),
        )

    reset_flow(context)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "راهنما:\n"
        "/start - شروع\n/menu - منو\n/cancel - لغو\n"
        "ثبت درخواست شارژ از داخل منو (بدون نیاز به دستور)\n/registeragent - ثبت به عنوان نماینده"
    )


async def topup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("فرمت: /topup <amount>")
        return
    try:
        amt = float(context.args[0])
    except ValueError:
        await update.message.reply_text("مبلغ باید عدد باشد")
        return
    if amt <= 0:
        await update.message.reply_text("مبلغ باید بیشتر از صفر باشد")
        return
    req_id = db.create_topup_request(update.effective_user.id, amt)
    context.user_data["flow"] = "topup_receipt"
    context.user_data["topup_request_id"] = req_id
    details = manual_payment_text()
    msg = [f"درخواست #{req_id} ثبت شد."]
    if details:
        msg.append(details)
    msg.append("پس از انتقال، لطفاً رسید پرداخت را به صورت عکس همینجا ارسال کنید.")
    msg.append("پس از ارسال رسید، ادمین می‌تواند با /approvetopupid درخواست را تأیید کند.")
    await update.message.reply_text("\n\n".join(msg))


async def approve_topup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("دسترسی ندارید")
        return
    if len(context.args) != 1:
        await update.message.reply_text("فرمت: /approvetopupid <topupid>")
        return
    req_id = as_int(context.args[0])
    if not req_id:
        await update.message.reply_text("شناسه نامعتبر است")
        return
    try:
        bal = db.approve_topup_request(req_id, update.effective_user.id)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return
    req = db.get_topup_request(req_id)
    await update.message.reply_text(f"✅ درخواست #{req_id} تایید شد. موجودی جدید کاربر: {toman(bal)}")
    if req:
        await context.bot.send_message(chat_id=int(req["tg_id"]), text=f"✅ درخواست شارژ #{req_id} تایید شد.")


async def register_agent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    db.ensure_agent(u.id, u.username or "", u.full_name or "", role="buyer")
    context.user_data["flow"] = "register_agent_experience"
    await update.message.reply_text(
        "برای ثبت‌نام نماینده، لطفاً تعداد سال سابقه فروش VPN را ارسال کنید (مثال: 2).",
        reply_markup=cancel_keyboard(),
    )


async def use_plan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("فرمت: /useplan <id>")
        return
    pid = as_int(context.args[0])
    if not pid:
        await update.message.reply_text("شناسه پلن نامعتبر است")
        return
    plans = db.list_plan_templates(get_user_role(update.effective_user.id))
    plan = next((p for p in plans if int(p["id"]) == pid), None)
    if not plan:
        await update.message.reply_text("پلن پیدا نشد")
        return
    context.user_data["flow"] = "wizard_inbound"
    context.user_data["wizard"] = {
        "kind": "single",
        "tg_id": update.effective_user.id,
        "days": int(plan["days"]),
        "gb": int(plan["gb"]),
        "limit_ip": int(plan["limit_ip"]),
    }
    await update.message.reply_text("پلن انتخاب شد. حالا شناسه اینباند را ارسال کنید (یا default).")


def ensure_referral_code(tg_id: int) -> str:
    code = db.get_referral_code(tg_id)
    if code:
        return code
    while True:
        code = generate_referral_code()
        if not db.get_agent_by_referral_code(code):
            db.set_referral_code(tg_id, code)
            return code


async def referral_info(message, context: ContextTypes.DEFAULT_TYPE, tg_id: int, role: str) -> None:
    if not is_referral_agent(role):
        await message.reply_text("برنامه معرفی فقط برای نمایندگان فعال است.")
        return
    code = ensure_referral_code(tg_id)
    stats = db.get_referral_stats(tg_id)
    username = context.bot.username or "your_bot"
    link = f"https://t.me/{username}?start={code}"
    await message.reply_text(
        "🎁 برنامه معرفی\n"
        f"لینک معرفی شما:\n{link}\n\n"
        f"تعداد کاربران معرفی‌شده: {stats['referred_count']}\n"
        f"مجموع کمیسیون دریافتی: {toman(stats['commission_total'])}"
    )


async def referral_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    role = get_user_role(uid)
    await referral_info(update.effective_message, context, uid, role)


async def start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("فقط ادمین می‌تواند از این دستور استفاده کند.")
        return ConversationHandler.END
    context.user_data.pop("broadcast", None)
    await update.effective_message.reply_text(
        "ارسال به: همه کاربران / فقط نمایندگان؟",
        reply_markup=broadcast_target_keyboard(),
    )
    return BROADCAST_CHOOSE_TARGET


async def choose_broadcast_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    data = query.data.split(":")[-1]
    if data not in {"all", "agents"}:
        await query.edit_message_text("گیرنده نامعتبر است. دوباره /broadcast را اجرا کنید.")
        return ConversationHandler.END
    context.user_data["broadcast"] = {
        "target": data,
    }
    await query.edit_message_text(
        "حالا پیام موردنظر برای ارسال همگانی را بفرستید (متن/عکس/فایل). برای لغو /cancel را بزنید."
    )
    return BROADCAST_SEND_MESSAGE


async def receive_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    message = update.effective_message
    if message.text and is_cancel(message.text):
        await message.reply_text("ارسال همگانی لغو شد.")
        return ConversationHandler.END

    broadcast = context.user_data.get("broadcast") or {}
    broadcast["source_chat_id"] = message.chat_id
    broadcast["source_message_id"] = message.message_id
    if message.text:
        broadcast["preview_text"] = message.text
    else:
        broadcast["preview_text"] = message.caption or "[پیام رسانه‌ای]"
    context.user_data["broadcast"] = broadcast

    target = broadcast.get("target", "all")
    target_title = "همه کاربران" if target == "all" else "نمایندگان"
    count = db.count_broadcast_targets(target)

    if message.text is None:
        await context.bot.copy_message(
            chat_id=message.chat_id,
            from_chat_id=message.chat_id,
            message_id=message.message_id,
        )
        preview_message = (
            "پیش‌نمایش پیام همگانی:\n\n"
            f"[پیش‌نمایش رسانه در بالا]\n\n"
            f"برای: {count} {target_title}\nتأیید می‌کنید؟"
        )
    else:
        preview_message = (
            "پیش‌نمایش پیام همگانی:\n\n"
            f"{broadcast['preview_text']}\n\n"
            f"برای: {count} {target_title}\nتأیید می‌کنید؟"
        )

    await message.reply_text(preview_message, reply_markup=broadcast_confirm_keyboard())
    return BROADCAST_PREVIEW_CONFIRM


async def broadcast_preview_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    action = query.data.split(":")[-1]
    if action == "edit":
        await query.edit_message_text(
            "پیام جدید برای ارسال همگانی را بفرستید (متن/عکس/فایل). برای لغو /cancel را بزنید."
        )
        return BROADCAST_SEND_MESSAGE
    if action == "cancel":
        await query.edit_message_text("ارسال همگانی لغو شد.")
        return ConversationHandler.END
    if action != "confirm":
        await query.edit_message_text("عملیات نامعتبر است.")
        return ConversationHandler.END

    broadcast = context.user_data.get("broadcast") or {}
    target = broadcast.get("target", "all")
    ids = db.list_broadcast_target_ids(target)
    ids = [uid for uid in ids if uid != ADMIN_TELEGRAM_ID]

    sent = 0
    failed = 0
    for uid in ids:
        try:
            await context.bot.copy_message(
                chat_id=uid,
                from_chat_id=broadcast.get("source_chat_id"),
                message_id=broadcast.get("source_message_id"),
            )
            sent += 1
        except Exception as exc:
            failed += 1
            logger.warning("broadcast_failed | user=%s | error=%s", uid, exc)
    logger.info("broadcast_complete | target=%s | sent=%s | failed=%s", target, sent, failed)
    await query.edit_message_text(f"پیام همگانی با موفقیت برای {sent} از {sent + failed} کاربر ارسال شد.")
    return ConversationHandler.END


async def broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if is_admin(update.effective_user.id):
        await update.effective_message.reply_text("ارسال همگانی لغو شد.")
    return ConversationHandler.END


def main() -> None:
    load_env_file()
    apply_runtime_config()
    interactive_setup_if_needed()
    apply_runtime_config()
    db.init_db()
    missing = required_missing()
    if missing:
        raise RuntimeError(f"Missing env vars: {missing}")
    global LOW_BALANCE_THRESHOLD
    LOW_BALANCE_THRESHOLD = load_low_balance_threshold()

    app = Application.builder().token(BOT_TOKEN).build()
    broadcast_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("broadcast", start_broadcast)],
        states={
            BROADCAST_CHOOSE_TARGET: [
                CallbackQueryHandler(choose_broadcast_target, pattern="^broadcast:target:(all|agents)$")
            ],
            BROADCAST_SEND_MESSAGE: [
                MessageHandler(filters.TEXT | filters.PHOTO | filters.Document.ALL, receive_broadcast_message)
            ],
            BROADCAST_PREVIEW_CONFIRM: [
                CallbackQueryHandler(broadcast_preview_action, pattern="^broadcast:(confirm|edit|cancel)$")
            ],
        },
        fallbacks=[CommandHandler("cancel", broadcast_cancel)],
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("topup", topup))
    app.add_handler(CommandHandler("approvetopupid", approve_topup_cmd))
    app.add_handler(CommandHandler("registeragent", register_agent_cmd))
    app.add_handler(CommandHandler("useplan", use_plan_cmd))
    app.add_handler(CommandHandler("referral", referral_cmd))
    app.add_handler(broadcast_conv_handler)
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_flow))
    app.add_handler(MessageHandler(filters.PHOTO, photo_flow))
    webhook_url = f"{WEBHOOK_BASE_URL}/{WEBHOOK_PATH}"
    app.run_webhook(
        listen=WEBHOOK_LISTEN,
        port=WEBHOOK_PORT,
        url_path=WEBHOOK_PATH,
        webhook_url=webhook_url,
        secret_token=WEBHOOK_SECRET_TOKEN or None,
    )


if __name__ == "__main__":
    main()
