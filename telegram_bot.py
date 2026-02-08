import json
import logging
import os
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
from xui_api import XUIApi, subscription_link, vless_link

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "8477244366"))
LOW_BALANCE_THRESHOLD = float(os.getenv("LOW_BALANCE_THRESHOLD", db.get_setting_float("low_balance_threshold")))
MAX_DAYS = int(os.getenv("MAX_PLAN_DAYS", "365"))
MAX_GB = int(os.getenv("MAX_PLAN_GB", "2000"))
MAX_BULK_COUNT = int(os.getenv("MAX_BULK_COUNT", "100"))
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


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()],
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
    return agent["role"] if agent else "reseller"


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
    return ReplyKeyboardMarkup([["لغو", "Cancel"]], resize_keyboard=True)


def is_cancel(text: str) -> bool:
    return text.strip().lower() in CANCEL_OPTIONS


def parse_positive_int(text: str) -> Optional[int]:
    if not text.isdigit():
        return None
    value = int(text)
    if value <= 0:
        return None
    return value


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
            [InlineKeyboardButton("پشتیبانی", callback_data="menu:support")],
            [InlineKeyboardButton("ادامه", callback_data="menu:home")],
        ]
    )


def broadcast_target_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("All users", callback_data="broadcast:target:all"),
                InlineKeyboardButton("Only agents", callback_data="broadcast:target:agents"),
            ]
        ]
    )


def broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Confirm", callback_data="broadcast:confirm"),
                InlineKeyboardButton("✏️ Edit", callback_data="broadcast:edit"),
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data="broadcast:cancel")],
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
    required = ["TELEGRAM_BOT_TOKEN", "XUI_BASE_URL", "XUI_USERNAME", "XUI_PASSWORD", "XUI_SERVER_HOST"]
    missing = [k for k in required if not os.getenv(k)]
    return ", ".join(missing)


def expiry_value(days: int, start_after_first_use: bool) -> int:
    if start_after_first_use:
        return -int(days * 86400 * 1000)
    return int((time.time() + days * 86400) * 1000)


def main_menu(role: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📊 Dashboard", callback_data="menu:dashboard")],
        [InlineKeyboardButton("👤 My Clients", callback_data="menu:my_clients")],
        [InlineKeyboardButton("➕ Create Client", callback_data="menu:create_client")],
        [InlineKeyboardButton("🌐 Inbounds List", callback_data="menu:inbounds")],
        [InlineKeyboardButton("💰 Wallet / Balance", callback_data="menu:wallet")],
        [InlineKeyboardButton("📄 Transactions History", callback_data="menu:tx")],
        [InlineKeyboardButton("🆘 Support", callback_data="menu:support")],
    ]
    if role in {"reseller", "agent"}:
        rows.append([InlineKeyboardButton("🎁 Referral", callback_data="menu:referral")])
    rows.append([InlineKeyboardButton("⚙️ Settings", callback_data="menu:settings")])
    return InlineKeyboardMarkup(rows)


def create_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🛒 Single Client", callback_data="create:single")],
            [InlineKeyboardButton("📦 Bulk Clients", callback_data="create:bulk")],
            [InlineKeyboardButton("🧩 Multi-Inbound Client", callback_data="create:multi")],
            [InlineKeyboardButton("⬅️ Back", callback_data="menu:home")],
        ]
    )


def settings_menu(admin: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📍 Set Default Inbound", callback_data="settings:set_default_inbound")],
        [InlineKeyboardButton("🎟 Apply Promo Code", callback_data="settings:promo")],
    ]
    if admin:
        rows.extend(
            [
                [InlineKeyboardButton("🛠 Admin: Create Inbound", callback_data="admin:create_inbound")],
                [InlineKeyboardButton("💵 Admin: Set Global Pricing", callback_data="admin:set_global_price")],
                [InlineKeyboardButton("🌐 Admin: Set Inbound Rule", callback_data="admin:set_inbound_rule")],
                [InlineKeyboardButton("👥 Admin: Resellers", callback_data="admin:resellers")],
                [InlineKeyboardButton("💳 Admin: Charge Wallet", callback_data="admin:charge_wallet")],
            ]
        )
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="menu:home")])
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


def inbound_price(inbound_id: int, days: int, gb: int) -> float:
    rule = db.inbound_rule(inbound_id)
    if rule and int(rule["enabled"]) == 0:
        raise ValueError("Selected inbound is disabled by admin")
    ppgb = float(rule["price_per_gb"]) if rule and rule["price_per_gb"] is not None else db.get_setting_float("price_per_gb")
    ppday = float(rule["price_per_day"]) if rule and rule["price_per_day"] is not None else db.get_setting_float("price_per_day")
    return round(gb * ppgb + days * ppday, 2)


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
    count = order_count(w)
    if w["kind"] == "multi":
        total = sum(inbound_price(i, w["days"], w["gb"]) for i in (w.get("inbound_ids") or []))
        return round(total, 2)
    unit = inbound_price(w["inbound_id"], w["days"], w["gb"])
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
        f"هزینه کل: <b>{net}</b> واحد (قیمت: {pricing_text})\n"
        f"inbound: <b>{inbound_label}</b>\n"
        f"remark/base: <b>{w.get('remark') or w.get('base_remark')}</b>\n"
        f"شروع بعد از اولین استفاده: <b>{'بله' if w['start_after_first_use'] else 'خیر'}</b>\n"
        f"تمدید خودکار: <b>{'بله' if w['auto_renew'] else 'خیر'}</b>\n"
        f"تخفیف: <b>{discount}%</b> | مبلغ ناخالص: <b>{gross}</b>"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    role = "admin" if is_admin(u.id) else "reseller"
    db.ensure_agent(u.id, u.username or "", u.full_name or "", role=role)
    if context.args:
        code = context.args[0].strip()
        referrer = db.get_agent_by_referral_code(code)
        if referrer and int(referrer["tg_id"]) != u.id:
            db.set_referred_by(u.id, int(referrer["tg_id"]))
    reset_flow(context)
    logger.info("user_start | user=%s | role=%s", u.id, role)
    await update.message.reply_text(
        "Welcome to Reseller Panel 👋\nUse the menu below.",
        reply_markup=main_menu(role),
    )


async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = get_user_role(update.effective_user.id)
    await update.message.reply_text("Main menu", reply_markup=main_menu(role))


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_flow(context)
    await update.message.reply_text(
        "عملیات لغو شد. به منوی اصلی بازگشتید.",
        reply_markup=ReplyKeyboardRemove(),
    )
    role = get_user_role(update.effective_user.id)
    await update.message.reply_text("Main menu", reply_markup=main_menu(role))


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    role = "admin" if is_admin(uid) else "reseller"
    db.ensure_agent(uid, q.from_user.username or "", q.from_user.full_name or "", role=role)

    data = q.data
    if data == "menu:home":
        await q.message.reply_text("Main menu", reply_markup=main_menu(role))
        return

    if data == "menu:dashboard":
        s = db.agent_stats(uid)
        await q.message.reply_text(
            f"📊 Dashboard\nBalance: {s['balance']}\nClients: {s['clients']}\nToday sales: {s['today_sales']}\nAll spent: {s['spent']}"
        )
        return

    if data == "menu:referral":
        await referral_info(q.message, context, uid, role)
        return

    if data == "menu:my_clients":
        total = db.count_clients(uid)
        if total == 0:
            await q.message.reply_text("No clients yet.")
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
        await q.message.reply_text("Choose creation mode:", reply_markup=create_menu())
        return

    if data == "menu:inbounds":
        api = XUIApi()
        try:
            api.login()
            ins = api.list_inbounds()
        except Exception as exc:
            await q.message.reply_text(f"Panel error: {exc}")
            return
        if not ins:
            await q.message.reply_text("No inbounds found.")
            return
        total = len(ins)
        if total == 0:
            await q.message.reply_text("No inbounds found.")
            return
        page, offset, total_pages = page_bounds(total, 1, LIST_PAGE_SIZE)
        lines = [f"🌐 Inbounds (page {page}/{total_pages}):"]
        for i in ins[offset:offset + LIST_PAGE_SIZE]:
            rid = i.get("id")
            remark = i.get("remark", "-")
            port = i.get("port", "-")
            lines.append(f"• ID {rid} | {remark} | port {port}")
        await q.message.reply_text("\n".join(lines), reply_markup=build_pagination(total, page, LIST_PAGE_SIZE, "page:inbounds"))
        return

    if data == "menu:wallet":
        a = db.get_agent(uid)
        await q.message.reply_text(f"💰 Balance: {a['balance'] if a else 0}")
        return

    if data == "menu:tx":
        total = db.count_transactions(uid)
        if total == 0:
            await q.message.reply_text("No transactions yet.")
            return
        page, offset, total_pages = page_bounds(total, 1, LIST_PAGE_SIZE)
        tx = db.list_transactions_paged(uid, LIST_PAGE_SIZE, offset)
        lines = [f"📄 Transactions (page {page}/{total_pages}):"]
        for t in tx:
            lines.append(f"• {t['amount']} | {t['reason']} | {time.strftime('%Y-%m-%d %H:%M', time.localtime(t['created_at']))}")
        await q.message.reply_text("\n".join(lines), reply_markup=build_pagination(total, page, LIST_PAGE_SIZE, "page:tx"))
        return

    if data == "menu:support":
        await q.message.reply_text("🆘 Support\n" + db.get_setting_text("support_text"))
        return

    if data == "menu:settings":
        await q.message.reply_text("Settings", reply_markup=settings_menu(is_admin(uid)))
        return

    if data == "settings:set_default_inbound":
        context.user_data["flow"] = "set_default_inbound"
        await q.message.reply_text("Send inbound ID to save as default.")
        return

    if data == "settings:promo":
        context.user_data["flow"] = "promo_apply"
        await q.message.reply_text("Send promo code now.")
        return

    if data == "create:single":
        if not can_start_wizard(uid):
            await q.message.reply_text("⏳ لطفا کمی بعد دوباره تلاش کنید.")
            return
        context.user_data["flow"] = "wizard_inbound"
        context.user_data["wizard"] = {"kind": "single"}
        logger.info("wizard_start | user=%s | kind=single", uid)
        await q.message.reply_text(
            "➕ Single client wizard\nStep 1/7: send inbound ID (or type: default).",
            reply_markup=cancel_keyboard(),
        )
        return

    if data == "create:bulk":
        if not can_start_wizard(uid):
            await q.message.reply_text("⏳ لطفا کمی بعد دوباره تلاش کنید.")
            return
        context.user_data["flow"] = "wizard_inbound"
        context.user_data["wizard"] = {"kind": "bulk"}
        logger.info("wizard_start | user=%s | kind=bulk", uid)
        await q.message.reply_text(
            "➕ Bulk client wizard\nStep 1/8: send inbound ID (or type: default).",
            reply_markup=cancel_keyboard(),
        )
        return

    if data == "create:multi":
        if not can_start_wizard(uid):
            await q.message.reply_text("⏳ لطفا کمی بعد دوباره تلاش کنید.")
            return
        context.user_data["flow"] = "wizard_inbounds"
        context.user_data["wizard"] = {"kind": "multi"}
        logger.info("wizard_start | user=%s | kind=multi", uid)
        await q.message.reply_text(
            "➕ Multi-inbound client wizard\nStep 1/7: send inbound IDs separated by comma. Example: 1,2,3",
            reply_markup=cancel_keyboard(),
        )
        return

    if data.startswith("admin:"):
        if not is_admin(uid):
            await q.message.reply_text("Only admin can use this option.")
            return
        if data == "admin:create_inbound":
            context.user_data["flow"] = "admin_create_inbound"
            await q.message.reply_text("Send: <port> <remark> [protocol] [network]")
        elif data == "admin:set_global_price":
            context.user_data["flow"] = "admin_set_global_price"
            await q.message.reply_text("Send: <price_per_gb> <price_per_day>\nExample: 0.2 0.1")
        elif data == "admin:set_inbound_rule":
            context.user_data["flow"] = "admin_set_inbound_rule"
            await q.message.reply_text("Send: <inbound_id> <enabled 1/0> <price_per_gb or -> <price_per_day or ->")
        elif data == "admin:resellers":
            rows = db.list_resellers(limit=50)
            if not rows:
                await q.message.reply_text("No resellers.")
            else:
                txt = ["👥 Resellers:"]
                for r in rows:
                    txt.append(f"• {r['tg_id']} | {r['username'] or '-'} | bal={r['balance']} | active={r['is_active']}")
                await q.message.reply_text("\n".join(txt[:60]))
        elif data == "admin:charge_wallet":
            context.user_data["flow"] = "admin_charge_wallet"
            await q.message.reply_text("Send: <tg_id> <amount>\nExample: 123456 50")
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
            await q.message.reply_text("Main menu", reply_markup=main_menu(role))
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
            await q.message.reply_text("Invalid client action.")
            return
        client_id = as_int(parts[1])
        action = parts[2]
        if not client_id:
            await q.message.reply_text("Invalid client.")
            return
        client = db.get_client(uid, client_id)
        if not client:
            await q.message.reply_text("Client not found.")
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
                f"Inbound: {client['inbound_id']}\n"
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
            await q.message.reply_text("Invalid page request.")
            return
        page_type = parts[1]
        page_num = as_int(parts[2]) or 1

        if page_type == "clients":
            total = db.count_clients(uid)
            if total == 0:
                await q.message.edit_message_text("No clients yet.")
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
                await q.message.edit_message_text("No transactions yet.")
                return
            page, offset, total_pages = page_bounds(total, page_num, LIST_PAGE_SIZE)
            rows = db.list_transactions_paged(uid, LIST_PAGE_SIZE, offset)
            lines = [f"📄 Transactions (page {page}/{total_pages}):"]
            for t in rows:
                lines.append(f"• {t['amount']} | {t['reason']} | {time.strftime('%Y-%m-%d %H:%M', time.localtime(t['created_at']))}")
            await q.message.edit_message_text("\n".join(lines))
            await q.message.edit_message_reply_markup(build_pagination(total, page, LIST_PAGE_SIZE, "page:tx"))
            return

        if page_type == "inbounds":
            api = XUIApi()
            try:
                api.login()
                ins = api.list_inbounds()
            except Exception as exc:
                await q.message.edit_message_text(f"Panel error: {exc}")
                return
            total = len(ins)
            if total == 0:
                await q.message.edit_message_text("No inbounds found.")
                return
            page, offset, total_pages = page_bounds(total, page_num, LIST_PAGE_SIZE)
            lines = [f"🌐 Inbounds (page {page}/{total_pages}):"]
            for i in ins[offset:offset + LIST_PAGE_SIZE]:
                rid = i.get("id")
                remark = i.get("remark", "-")
                port = i.get("port", "-")
                lines.append(f"• ID {rid} | {remark} | port {port}")
            await q.message.edit_message_text("\n".join(lines))
            await q.message.edit_message_reply_markup(build_pagination(total, page, LIST_PAGE_SIZE, "page:inbounds"))
            return

    await q.message.reply_text("Unknown action.")


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
        role = agent["role"] if agent else "reseller"
        await update.message.reply_text("Main menu", reply_markup=main_menu(role))
        return

    if flow == "set_default_inbound":
        iid = as_int(txt)
        if not iid or iid <= 0:
            await update.message.reply_text("Invalid inbound ID")
            return
        db.set_preferred_inbound(uid, iid)
        reset_flow(context)
        await update.message.reply_text(f"Default inbound set to {iid}")
        return

    if flow == "promo_apply":
        try:
            disc = db.apply_promo(txt, uid)
        except ValueError as exc:
            await update.message.reply_text(str(exc))
            return
        context.user_data["promo_discount"] = disc
        reset_flow(context)
        await update.message.reply_text(f"Promo applied: {disc}% on your next order")
        return

    # Admin flows
    if flow == "admin_create_inbound":
        if not is_admin(uid):
            await update.message.reply_text("Not allowed")
            return
        parts = txt.split()
        if len(parts) < 2:
            await update.message.reply_text("Usage: <port> <remark> [protocol] [network]")
            return
        port = as_int(parts[0])
        if not port:
            await update.message.reply_text("Invalid port")
            return
        api = XUIApi()
        try:
            api.login()
            inbound_id = api.create_inbound(port, parts[1], parts[2] if len(parts) > 2 else "vless", parts[3] if len(parts) > 3 else "tcp")
        except Exception as exc:
            await update.message.reply_text(f"Failed: {exc}")
            return
        reset_flow(context)
        logger.info("admin_create_inbound | admin=%s | inbound=%s", uid, inbound_id)
        await update.message.reply_text(f"Inbound created with ID: {inbound_id}")
        return

    if flow == "admin_set_global_price":
        if not is_admin(uid):
            await update.message.reply_text("Not allowed")
            return
        parts = txt.split()
        if len(parts) != 2:
            await update.message.reply_text("Usage: <price_per_gb> <price_per_day>")
            return
        try:
            pgb = float(parts[0]); pday = float(parts[1])
        except ValueError:
            await update.message.reply_text("Prices must be numeric")
            return
        db.set_setting("price_per_gb", str(pgb))
        db.set_setting("price_per_day", str(pday))
        reset_flow(context)
        logger.info("admin_set_global_price | admin=%s | ppgb=%s | ppday=%s", uid, pgb, pday)
        await update.message.reply_text("Global pricing updated.")
        return

    if flow == "admin_set_inbound_rule":
        if not is_admin(uid):
            await update.message.reply_text("Not allowed")
            return
        parts = txt.split()
        if len(parts) != 4:
            await update.message.reply_text("Usage: <inbound_id> <enabled 1/0> <price_per_gb or -> <price_per_day or ->")
            return
        iid = as_int(parts[0]); en = as_int(parts[1])
        if not iid or en not in [0, 1]:
            await update.message.reply_text("Invalid inbound_id/enabled")
            return
        pgb = None if parts[2] == "-" else float(parts[2])
        pday = None if parts[3] == "-" else float(parts[3])
        db.set_inbound_rule(iid, bool(en), pgb, pday)
        reset_flow(context)
        logger.info("admin_set_inbound_rule | admin=%s | inbound=%s | enabled=%s", uid, iid, en)
        await update.message.reply_text("Inbound pricing/enable rule saved.")
        return

    if flow == "admin_charge_wallet":
        if not is_admin(uid):
            await update.message.reply_text("Not allowed")
            return
        parts = txt.split()
        if len(parts) != 2:
            await update.message.reply_text("Usage: <tg_id> <amount>")
            return
        tid = as_int(parts[0])
        try:
            amount = float(parts[1])
        except ValueError:
            await update.message.reply_text("Amount must be numeric")
            return
        if not tid:
            await update.message.reply_text("Invalid tg_id")
            return
        db.ensure_agent(tid, "", "", role="reseller")
        bal = db.add_balance(tid, amount, "topup.admin", meta=f"by:{uid}")
        reset_flow(context)
        logger.info("admin_charge_wallet | admin=%s | target=%s | amount=%s", uid, tid, amount)
        await update.message.reply_text(f"Wallet updated. New balance: {bal}")
        return

    # Wizard flows
    if flow == "wizard_inbounds":
        inbound_ids = parse_inbound_ids(txt)
        if not inbound_ids:
            await update.message.reply_text(
                "Invalid inbound list. Send comma-separated inbound IDs like: 1,2,3",
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
                await update.message.reply_text("Invalid inbound ID. Send digits only.", reply_markup=cancel_keyboard())
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
        await update.message.reply_text("Step 3/7: send total days. Hint: 30", reply_markup=cancel_keyboard())
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
        await update.message.reply_text("Step 3/8: send number of clients. Hint: 5", reply_markup=cancel_keyboard())
        return

    if flow == "wizard_count":
        c = parse_positive_int(txt)
        if not c or c > MAX_BULK_COUNT:
            await update.message.reply_text(
                f"Invalid count. Enter a number between 1 and {MAX_BULK_COUNT}.",
                reply_markup=cancel_keyboard(),
            )
            return
        w["count"] = c
        context.user_data["flow"] = "wizard_days"
        await update.message.reply_text("Step 4/8: send total days. Hint: 30", reply_markup=cancel_keyboard())
        return

    if flow == "wizard_days":
        d = parse_positive_int(txt)
        if not d or d > MAX_DAYS:
            await update.message.reply_text(
                f"Invalid days. Enter a number between 1 and {MAX_DAYS}.",
                reply_markup=cancel_keyboard(),
            )
            return
        w["days"] = d
        context.user_data["flow"] = "wizard_gb"
        step = "Step 4/7" if w["kind"] in {"single", "multi"} else "Step 5/8"
        await update.message.reply_text(f"{step}: send total GB. Hint: 50", reply_markup=cancel_keyboard())
        return

    if flow == "wizard_gb":
        g = parse_positive_int(txt)
        if not g or g > MAX_GB:
            await update.message.reply_text(
                f"Invalid GB. Enter a number between 1 and {MAX_GB}.",
                reply_markup=cancel_keyboard(),
            )
            return
        w["gb"] = g
        context.user_data["flow"] = "wizard_start_after_first_use"
        step = "Step 5/7" if w["kind"] in {"single", "multi"} else "Step 6/8"
        await update.message.reply_text(f"{step}: start after first use? (y/n)", reply_markup=cancel_keyboard())
        return

    if flow == "wizard_start_after_first_use":
        v = txt.lower()
        if v not in ["y", "n", "yes", "no"]:
            await update.message.reply_text("Please answer y or n", reply_markup=cancel_keyboard())
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
            await update.message.reply_text("Please answer y or n", reply_markup=cancel_keyboard())
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
            role = agent["role"] if agent else "reseller"
            await update.message.reply_text("Main menu", reply_markup=main_menu(role))
            return
        if v not in ["y", "yes"]:
            await update.message.reply_text("Please answer yes or no", reply_markup=cancel_keyboard())
            return
        await finalize_order(update, context, w)
        return

    await update.message.reply_text("Use /start and choose from menu buttons.")


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
            f"Insufficient balance. Required: {net}",
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
            clients.append({
                "id": uidc,
                "email": email,
                "enable": True,
                "expiryTime": expiry,
                "totalGB": int(w["gb"]) * 1024**3,
                "flow": "",
                "limitIp": 0,
                "tgId": str(uid),
                "subId": sub_id,
                "comment": "tg",
                "reset": reset_days,
            })
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
            for i in range(w["count"]):
                uidc = str(uuid.uuid4())
                email = f"{w['base_remark']}_{i+1}"
                sub_id = generate_sub_id()
                sub_link = subscription_link(sub_id)
                clients.append({
                    "id": uidc,
                    "email": email,
                    "enable": True,
                    "expiryTime": expiry,
                    "totalGB": int(w["gb"]) * 1024**3,
                    "flow": "",
                    "limitIp": 0,
                    "tgId": str(uid),
                    "subId": sub_id,
                    "comment": "tg",
                    "reset": reset_days,
                })
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
            for inbound_id in inbound_ids:
                inbound = api.get_inbound(inbound_id)
                uidc = str(uuid.uuid4())
                email = w["remark"]
                client = {
                    "id": uidc,
                    "email": email,
                    "enable": True,
                    "expiryTime": expiry,
                    "totalGB": int(w["gb"]) * 1024**3,
                    "flow": "",
                    "limitIp": 0,
                    "tgId": str(uid),
                    "subId": sub_id,
                    "comment": "tg",
                    "reset": reset_days,
                }
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
            "⚠️ We couldn't create the client(s) right now. Your balance was refunded. Please try again later.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    bal = db.get_agent(uid)["balance"]
    inbound_label = ", ".join(str(i) for i in inbound_ids)
    summary = (
        f"✅ Client(s) created\nType: {w['kind']}\nInbound: {inbound_label}\n"
        f"Days: {w['days']} | GB: {w['gb']} | Count: {count}\n"
        f"Start after first use: {'Yes' if w['start_after_first_use'] else 'No'} | Auto-renew: {'Yes' if auto_renew else 'No'}\n"
        f"Gross: {gross}\nDiscount: {disc}%\nCharged: {net}\nBalance: {bal}"
    )
    configs = "\n".join(links)
    subs = "\n".join(subscription_links)
    sections = [summary]
    if configs:
        sections.append(f"Configs:\n{configs}")
    if subs:
        sections.append(f"Subscription links:\n{subs}")
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
            "⚠️ موجودی شما کم است. برای جلوگیری از خطا، کیف پول را شارژ کنید.",
            reply_markup=low_balance_keyboard(),
        )

    reset_flow(context)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "This is a button-first reseller panel.\n"
        "Use /start to open menu.\n"
        "Useful commands: /start, /menu, /cancel, /topup <amount>"
    )


async def topup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /topup <amount>")
        return
    try:
        amt = float(context.args[0])
    except ValueError:
        await update.message.reply_text("Amount must be numeric")
        return
    if amt <= 0:
        await update.message.reply_text("Amount must be > 0")
        return
    bal = db.add_balance(update.effective_user.id, amt, "topup.manual")
    await update.message.reply_text(f"Top-up ok. Balance: {bal}")


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
        await message.reply_text("Referral program is available to agents only.")
        return
    code = ensure_referral_code(tg_id)
    stats = db.get_referral_stats(tg_id)
    username = context.bot.username or "your_bot"
    link = f"https://t.me/{username}?start={code}"
    await message.reply_text(
        "🎁 Referral Program\n"
        f"Your referral link:\n{link}\n\n"
        f"Referred users: {stats['referred_count']}\n"
        f"Total commission earned: {stats['commission_total']}"
    )


async def referral_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    role = get_user_role(uid)
    await referral_info(update.effective_message, context, uid, role)


async def start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("Only admin can use this command.")
        return ConversationHandler.END
    context.user_data.pop("broadcast", None)
    await update.effective_message.reply_text(
        "Send to: All users / Only agents?",
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
        await query.edit_message_text("Invalid target. Use /broadcast again.")
        return ConversationHandler.END
    context.user_data["broadcast"] = {
        "target": data,
    }
    await query.edit_message_text(
        "Now send the message you want to broadcast (text, photo, document allowed). Use /cancel to stop."
    )
    return BROADCAST_SEND_MESSAGE


async def receive_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    message = update.effective_message
    if message.text and is_cancel(message.text):
        await message.reply_text("Broadcast canceled.")
        return ConversationHandler.END

    broadcast = context.user_data.get("broadcast") or {}
    broadcast["source_chat_id"] = message.chat_id
    broadcast["source_message_id"] = message.message_id
    if message.text:
        broadcast["preview_text"] = message.text
    else:
        broadcast["preview_text"] = message.caption or "[Media message]"
    context.user_data["broadcast"] = broadcast

    target = broadcast.get("target", "all")
    target_title = "all users" if target == "all" else "agents"
    count = db.count_broadcast_targets(target)

    if message.text is None:
        await context.bot.copy_message(
            chat_id=message.chat_id,
            from_chat_id=message.chat_id,
            message_id=message.message_id,
        )
        preview_message = (
            "Broadcast preview:\n\n"
            f"[Media preview above]\n\n"
            f"To: {count} {target_title}\nConfirm?"
        )
    else:
        preview_message = (
            "Broadcast preview:\n\n"
            f"{broadcast['preview_text']}\n\n"
            f"To: {count} {target_title}\nConfirm?"
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
            "Send the updated message you want to broadcast (text, photo, document allowed). Use /cancel to stop."
        )
        return BROADCAST_SEND_MESSAGE
    if action == "cancel":
        await query.edit_message_text("Broadcast canceled.")
        return ConversationHandler.END
    if action != "confirm":
        await query.edit_message_text("Invalid action.")
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
    await query.edit_message_text(f"Broadcast sent to {sent}/{sent + failed} users successfully.")
    return ConversationHandler.END


async def broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if is_admin(update.effective_user.id):
        await update.effective_message.reply_text("Broadcast canceled.")
    return ConversationHandler.END


def main() -> None:
    db.init_db()
    missing = required_missing()
    if missing:
        raise RuntimeError(f"Missing env vars: {missing}")

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
    app.add_handler(CommandHandler("referral", referral_cmd))
    app.add_handler(broadcast_conv_handler)
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_flow))
    app.run_polling()


if __name__ == "__main__":
    main()
