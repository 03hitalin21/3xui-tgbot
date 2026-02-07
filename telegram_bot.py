import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

import db
from xui_api import XUIApi, vless_link

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "8477244366"))
MAX_DAYS = int(os.getenv("MAX_PLAN_DAYS", "365"))
MAX_GB = int(os.getenv("MAX_PLAN_GB", "2000"))
MAX_BULK_COUNT = int(os.getenv("MAX_BULK_COUNT", "100"))
MAX_LINKS_PER_MESSAGE = 10
LIST_PAGE_SIZE = 10


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


def reset_flow(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["flow"] = None
    context.user_data["wizard"] = {}


def required_missing() -> str:
    required = ["TELEGRAM_BOT_TOKEN", "XUI_BASE_URL", "XUI_USERNAME", "XUI_PASSWORD", "XUI_SERVER_HOST"]
    missing = [k for k in required if not os.getenv(k)]
    return ", ".join(missing)


def expiry_value(days: int, start_after_first_use: bool) -> int:
    if start_after_first_use:
        return -int(days * 86400 * 1000)
    return int((time.time() + days * 86400) * 1000)


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📊 Dashboard", callback_data="menu:dashboard")],
            [InlineKeyboardButton("👤 My Clients", callback_data="menu:my_clients")],
            [InlineKeyboardButton("➕ Create Client", callback_data="menu:create_client")],
            [InlineKeyboardButton("🌐 Inbounds List", callback_data="menu:inbounds")],
            [InlineKeyboardButton("💰 Wallet / Balance", callback_data="menu:wallet")],
            [InlineKeyboardButton("📄 Transactions History", callback_data="menu:tx")],
            [InlineKeyboardButton("🆘 Support", callback_data="menu:support")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="menu:settings")],
        ]
    )


def create_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🛒 Single Client", callback_data="create:single")],
            [InlineKeyboardButton("📦 Bulk Clients", callback_data="create:bulk")],
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
    for i in range(0, len(links), MAX_LINKS_PER_MESSAGE):
        await update.message.reply_text("\n".join(links[i:i + MAX_LINKS_PER_MESSAGE]))


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


def wizard_summary(w: Dict, gross: float, discount: float, net: float) -> str:
    return (
        "🧾 Confirm order\n"
        f"Type: {w['kind']}\nInbound: {w['inbound_id']}\n"
        f"Remark/Base: {w.get('remark') or w.get('base_remark')}\n"
        f"Days: {w['days']}\nGB: {w['gb']}\n"
        f"Start after first use: {'Yes' if w['start_after_first_use'] else 'No'}\n"
        f"Auto-renew: {'Yes' if w['auto_renew'] else 'No'}\n"
        f"Count: {w.get('count', 1)}\n"
        f"Gross: {gross}\nDiscount: {discount}%\nFinal: {net}\n\n"
        "Reply with: yes / no"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    role = "admin" if is_admin(u.id) else "reseller"
    db.ensure_agent(u.id, u.username or "", u.full_name or "", role=role)
    reset_flow(context)
    await update.message.reply_text("Welcome to Reseller Panel 👋\nUse the menu below.", reply_markup=main_menu())


async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Main menu", reply_markup=main_menu())


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_flow(context)
    await update.message.reply_text("Operation canceled.")


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    role = "admin" if is_admin(uid) else "reseller"
    db.ensure_agent(uid, q.from_user.username or "", q.from_user.full_name or "", role=role)

    data = q.data
    if data == "menu:home":
        await q.message.reply_text("Main menu", reply_markup=main_menu())
        return

    if data == "menu:dashboard":
        s = db.agent_stats(uid)
        await q.message.reply_text(
            f"📊 Dashboard\nBalance: {s['balance']}\nClients: {s['clients']}\nToday sales: {s['today_sales']}\nAll spent: {s['spent']}"
        )
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
        await q.message.reply_text("\n".join(lines), reply_markup=build_pagination(total, page, LIST_PAGE_SIZE, "page:clients"))
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
        context.user_data["flow"] = "wizard_inbound"
        context.user_data["wizard"] = {"kind": "single"}
        await q.message.reply_text(
            "➕ Single client wizard\nStep 1/7: send inbound ID (or type: default)."
        )
        return

    if data == "create:bulk":
        context.user_data["flow"] = "wizard_inbound"
        context.user_data["wizard"] = {"kind": "bulk"}
        await q.message.reply_text(
            "➕ Bulk client wizard\nStep 1/8: send inbound ID (or type: default)."
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
            await q.message.edit_message_reply_markup(build_pagination(total, page, LIST_PAGE_SIZE, "page:clients"))
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
        await update.message.reply_text(f"Wallet updated. New balance: {bal}")
        return

    # Wizard flows
    if flow == "wizard_inbound":
        if txt.lower() == "default":
            if not agent or not agent["preferred_inbound"]:
                await update.message.reply_text("No default inbound set. Send numeric inbound ID.")
                return
            w["inbound_id"] = int(agent["preferred_inbound"])
        else:
            iid = as_int(txt)
            if not iid or iid <= 0:
                await update.message.reply_text("Invalid inbound ID")
                return
            w["inbound_id"] = iid
        context.user_data["wizard"] = w
        if w["kind"] == "single":
            context.user_data["flow"] = "wizard_remark"
            await update.message.reply_text("Step 2/7: send client remark/email. Hint: user123")
        else:
            context.user_data["flow"] = "wizard_base"
            await update.message.reply_text("Step 2/8: send base remark for bulk. Hint: teamA")
        return

    if flow == "wizard_remark":
        if len(txt) < 2:
            await update.message.reply_text("Remark too short")
            return
        w["remark"] = txt
        context.user_data["flow"] = "wizard_days"
        await update.message.reply_text("Step 3/7: send total days. Hint: 30")
        return

    if flow == "wizard_base":
        if len(txt) < 2:
            await update.message.reply_text("Base remark too short")
            return
        w["base_remark"] = txt
        context.user_data["flow"] = "wizard_count"
        await update.message.reply_text("Step 3/8: send number of clients. Hint: 5")
        return

    if flow == "wizard_count":
        c = as_int(txt)
        if not c or c <= 0 or c > MAX_BULK_COUNT:
            await update.message.reply_text(f"Invalid count. max={MAX_BULK_COUNT}")
            return
        w["count"] = c
        context.user_data["flow"] = "wizard_days"
        await update.message.reply_text("Step 4/8: send total days. Hint: 30")
        return

    if flow == "wizard_days":
        d = as_int(txt)
        if not d or d <= 0 or d > MAX_DAYS:
            await update.message.reply_text(f"Invalid days. max={MAX_DAYS}")
            return
        w["days"] = d
        context.user_data["flow"] = "wizard_gb"
        step = "Step 4/7" if w["kind"] == "single" else "Step 5/8"
        await update.message.reply_text(f"{step}: send total GB. Hint: 50")
        return

    if flow == "wizard_gb":
        g = as_int(txt)
        if not g or g <= 0 or g > MAX_GB:
            await update.message.reply_text(f"Invalid GB. max={MAX_GB}")
            return
        w["gb"] = g
        context.user_data["flow"] = "wizard_start_after_first_use"
        step = "Step 5/7" if w["kind"] == "single" else "Step 6/8"
        await update.message.reply_text(f"{step}: start after first use? (y/n)")
        return

    if flow == "wizard_start_after_first_use":
        v = txt.lower()
        if v not in ["y", "n", "yes", "no"]:
            await update.message.reply_text("Please answer y or n")
            return
        w["start_after_first_use"] = v in ["y", "yes"]
        context.user_data["flow"] = "wizard_auto_renew"
        step = "Step 6/7" if w["kind"] == "single" else "Step 7/8"
        await update.message.reply_text(f"{step}: Enable auto-renew? (y/n)\nHint: auto-renew resets one day before expiry.")
        return

    if flow == "wizard_auto_renew":
        v = txt.lower()
        if v not in ["y", "n", "yes", "no"]:
            await update.message.reply_text("Please answer y or n")
            return
        w["auto_renew"] = v in ["y", "yes"]

        try:
            unit = inbound_price(w["inbound_id"], w["days"], w["gb"])
        except ValueError as exc:
            reset_flow(context)
            await update.message.reply_text(str(exc))
            return

        count = 1 if w["kind"] == "single" else w["count"]
        gross = round(unit * count, 2)
        discount = float(context.user_data.get("promo_discount", 0.0))
        net = round(gross * (1 - discount / 100), 2)
        context.user_data["flow"] = "wizard_confirm"
        await update.message.reply_text(wizard_summary(w, gross, discount, net))
        return

    if flow == "wizard_confirm":
        v = txt.lower()
        if v in ["n", "no"]:
            reset_flow(context)
            context.user_data.pop("promo_discount", None)
            await update.message.reply_text("Order canceled.")
            return
        if v not in ["y", "yes"]:
            await update.message.reply_text("Please answer yes or no")
            return
        await finalize_order(update, context, w)
        return

    await update.message.reply_text("Use /start and choose from menu buttons.")


async def finalize_order(update: Update, context: ContextTypes.DEFAULT_TYPE, w: Dict):
    uid = update.effective_user.id
    count = 1 if w["kind"] == "single" else w["count"]
    unit = inbound_price(w["inbound_id"], w["days"], w["gb"])
    gross = round(unit * count, 2)
    disc = float(context.user_data.pop("promo_discount", 0.0))
    net = round(gross * (1 - disc / 100), 2)
    auto_renew = bool(w.get("auto_renew", False))
    reset_days = max(w["days"] - 1, 0) if auto_renew else 0

    ag = db.get_agent(uid)
    if not ag or int(ag["is_active"]) != 1:
        reset_flow(context)
        await update.message.reply_text("Your reseller account is disabled. Contact admin.")
        return

    try:
        db.deduct_balance(uid, net, "order.charge", json.dumps({"kind": w["kind"], "inbound": w["inbound_id"]}))
    except ValueError:
        reset_flow(context)
        await update.message.reply_text(f"Insufficient balance. Required: {net}")
        return

    api = XUIApi()
    links: List[str] = []
    expiry = expiry_value(w["days"], w["start_after_first_use"])

    try:
        api.login()
        inbound = api.get_inbound(w["inbound_id"])
        clients = []

        if w["kind"] == "single":
            uidc = str(uuid.uuid4())
            email = w["remark"]
            clients.append({
                "id": uidc,
                "email": email,
                "enable": True,
                "expiryTime": expiry,
                "totalGB": int(w["gb"]) * 1024**3,
                "flow": "",
                "limitIp": 0,
                "tgId": str(uid),
                "subId": "",
                "comment": "tg",
                "reset": reset_days,
            })
            link = vless_link(uidc, inbound, email)
            links.append(link)
            db.save_created_client(
                uid,
                w["inbound_id"],
                email,
                uidc,
                link,
                w["days"],
                w["gb"],
                w["start_after_first_use"],
                auto_renew,
            )
        else:
            for i in range(w["count"]):
                uidc = str(uuid.uuid4())
                email = f"{w['base_remark']}_{i+1}"
                clients.append({
                    "id": uidc,
                    "email": email,
                    "enable": True,
                    "expiryTime": expiry,
                    "totalGB": int(w["gb"]) * 1024**3,
                    "flow": "",
                    "limitIp": 0,
                    "tgId": str(uid),
                    "subId": "",
                    "comment": "tg",
                    "reset": reset_days,
                })
                link = vless_link(uidc, inbound, email)
                links.append(link)
                db.save_created_client(
                    uid,
                    w["inbound_id"],
                    email,
                    uidc,
                    link,
                    w["days"],
                    w["gb"],
                    w["start_after_first_use"],
                    auto_renew,
                )

        api.add_clients(w["inbound_id"], clients)
        db.create_order(uid, w["inbound_id"], w["kind"], w["days"], w["gb"], count, gross, disc, net, "success")
    except Exception as exc:
        db.add_balance(uid, net, "order.refund", str(exc))
        db.create_order(uid, w["inbound_id"], w["kind"], w["days"], w["gb"], count, gross, disc, net, "failed")
        await send_notification(
            context,
            uid,
            f"❌ <b>Creation failed</b>\nRefunded: {net}\nReason: {exc}",
        )
        reset_flow(context)
        await update.message.reply_text(f"Panel/API error. Refunded. Details: {exc}")
        return

    bal = db.get_agent(uid)["balance"]
    await update.message.reply_text(
        f"✅ Client(s) created\nType: {w['kind']}\nInbound: {w['inbound_id']}\n"
        f"Days: {w['days']} | GB: {w['gb']} | Count: {count}\n"
        f"Start after first use: {'Yes' if w['start_after_first_use'] else 'No'} | Auto-renew: {'Yes' if auto_renew else 'No'}\n"
        f"Gross: {gross}\nDiscount: {disc}%\nCharged: {net}\nBalance: {bal}"
    )
    await send_links(update, links)

    # QR preview for single client
    if len(links) == 1:
        qr = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={links[0]}"
        await update.message.reply_photo(qr)

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


def main() -> None:
    db.init_db()
    missing = required_missing()
    if missing:
        raise RuntimeError(f"Missing env vars: {missing}")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("topup", topup))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_flow))
    app.run_polling()


if __name__ == "__main__":
    main()
