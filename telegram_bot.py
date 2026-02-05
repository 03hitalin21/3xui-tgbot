import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional

import requests
import urllib3
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import db

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = os.getenv("XUI_BASE_URL", "")
USERNAME = os.getenv("XUI_USERNAME", "")
PASSWORD = os.getenv("XUI_PASSWORD", "")
SERVER_HOST = os.getenv("XUI_SERVER_HOST", "")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "8477244366"))
MAX_DAYS = int(os.getenv("MAX_PLAN_DAYS", "365"))
MAX_GB = int(os.getenv("MAX_PLAN_GB", "2000"))
MAX_BULK_COUNT = int(os.getenv("MAX_BULK_COUNT", "100"))
MAX_LINKS_PER_MESSAGE = 10


def menu(is_admin: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("💰 Balance", callback_data="ui:balance")],
        [InlineKeyboardButton("🛒 Create Single Client (Wizard)", callback_data="ui:wizard_single")],
        [InlineKeyboardButton("📦 Create Bulk Clients (Wizard)", callback_data="ui:wizard_bulk")],
        [InlineKeyboardButton("📍 Set Default Inbound", callback_data="ui:set_inbound")],
        [InlineKeyboardButton("🧮 Price", callback_data="ui:price")],
        [InlineKeyboardButton("🎟 Apply Promo", callback_data="ui:promo")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton("🛠 Create Inbound (Admin)", callback_data="ui:admin_inbound")])
    return InlineKeyboardMarkup(rows)


@dataclass
class Plan:
    days: int
    gb: int

    @property
    def expiry_ms(self) -> int:
        return int((time.time() + self.days * 86400) * 1000)

    @property
    def total_bytes(self) -> int:
        return self.gb * 1024 ** 3

    @property
    def price(self) -> float:
        return round(self.gb * db.get_setting_float("price_per_gb") + self.days * db.get_setting_float("price_per_day"), 2)


def expiry_value(days: int, start_after_first_use: bool) -> int:
    if start_after_first_use:
        return -int(days * 86400 * 1000)
    return int((time.time() + days * 86400) * 1000)


class XUI:
    def __init__(self):
        self.s = requests.Session()
        self.s.verify = False

    def login(self):
        r = self.s.post(f"{BASE_URL}/login", data={"username": USERNAME, "password": PASSWORD}, timeout=20)
        if r.status_code != 200:
            raise RuntimeError("x-ui login failed")

    def get_inbound(self, inbound_id: int):
        r = self.s.get(f"{BASE_URL}/panel/api/inbounds/get/{inbound_id}", timeout=20)
        data = r.json()
        if not data.get("success"):
            raise RuntimeError("Failed to fetch inbound")
        obj = data["obj"]
        stream = json.loads(obj.get("streamSettings", "{}"))
        return {
            "port": obj["port"],
            "network": stream.get("network", "tcp"),
            "security": stream.get("security", "none"),
            "reality": stream.get("realitySettings", {}),
        }

    def add_clients(self, inbound_id: int, clients: List[dict]):
        payload = {"id": inbound_id, "settings": json.dumps({"clients": clients})}
        r = self.s.post(f"{BASE_URL}/panel/api/inbounds/addClient", data=payload, timeout=30)
        if not r.json().get("success"):
            raise RuntimeError(f"Client creation failed: {r.text}")

    def create_inbound(self, port: int, remark: str, protocol: str = "vless", network: str = "tcp"):
        payload = {
            "up": 0,
            "down": 0,
            "total": 0,
            "remark": remark,
            "enable": True,
            "expiryTime": 0,
            "trafficReset": "never",
            "lastTrafficResetTime": 0,
            "listen": "",
            "port": port,
            "protocol": protocol,
            "settings": json.dumps({"clients": [], "decryption": "none", "encryption": "none"}),
            "streamSettings": json.dumps({"network": network, "security": "none"}),
            "sniffing": json.dumps({"enabled": False, "destOverride": ["http", "tls"]}),
        }
        r = self.s.post(f"{BASE_URL}/panel/api/inbounds/add", data=payload, timeout=30)
        data = r.json()
        if not data.get("success"):
            raise RuntimeError(f"Failed to create inbound: {r.text}")
        return data.get("obj", {}).get("id")


def vless_link(uid: str, inbound: dict, remark: str) -> str:
    if inbound["security"] == "reality":
        r = inbound["reality"]
        return (
            f"vless://{uid}@{SERVER_HOST}:{inbound['port']}?type=tcp&security=reality&encryption=none"
            f"&pbk={r['settings']['publicKey']}&fp={r['settings'].get('fingerprint', 'chrome')}"
            f"&sni={r['serverNames'][0]}&sid={r['shortIds'][0]}#{remark}"
        )
    return f"vless://{uid}@{SERVER_HOST}:{inbound['port']}?type={inbound['network']}&security={inbound['security']}&encryption=none#{remark}"


def as_int(text: str) -> Optional[int]:
    try:
        return int(text)
    except ValueError:
        return None


def required_missing() -> str:
    missing = []
    for key, val in {
        "TELEGRAM_BOT_TOKEN": BOT_TOKEN,
        "XUI_BASE_URL": BASE_URL,
        "XUI_USERNAME": USERNAME,
        "XUI_PASSWORD": PASSWORD,
        "XUI_SERVER_HOST": SERVER_HOST,
    }.items():
        if not val:
            missing.append(key)
    return ", ".join(missing)


def is_admin(uid: int) -> bool:
    return uid == ADMIN_TELEGRAM_ID


def reset_flow(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["flow"] = None
    context.user_data["wizard"] = {}


async def send_links(update: Update, links: List[str]) -> None:
    for i in range(0, len(links), MAX_LINKS_PER_MESSAGE):
        await update.message.reply_text("\n".join(links[i:i + MAX_LINKS_PER_MESSAGE]))


def wizard_summary(wiz: Dict, gross: float, discount: float, net: float) -> str:
    return (
        "🧾 Please confirm your order:\n"
        f"Type: {wiz['kind']}\n"
        f"Inbound: {wiz['inbound']}\n"
        f"Remark: {wiz.get('remark', '-') if wiz['kind'] == 'single' else wiz.get('base', '-')}\n"
        f"Days: {wiz['days']}\n"
        f"Traffic: {wiz['gb']} GB\n"
        f"Start after first use: {'Yes' if wiz['start_after_first_use'] else 'No'}\n"
        f"Count: {wiz.get('count', 1)}\n"
        f"Gross: {gross}\n"
        f"Discount: {discount}%\n"
        f"Final charge: {net}\n\n"
        "Send: yes / no"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.ensure_agent(user.id, user.username or "", user.full_name or "")
    reset_flow(context)
    await update.message.reply_text("Welcome. Use buttons for a guided flow.", reply_markup=menu(is_admin(user.id)))


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ This bot is wizard-first (step-by-step).\n"
        "Use /start and choose Create Single/Bulk.\n"
        "Commands: /balance, /topup <amount>, /setinbound <id>, /myinbound, /price <days> <gb>, /promo <CODE>, /cancel, /menu"
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_flow(context)
    await update.message.reply_text("Current operation canceled.")


async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Main menu:", reply_markup=menu(is_admin(update.effective_user.id)))


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.ensure_agent(user.id, user.username or "", user.full_name or "")
    agent = db.get_agent(user.id)
    stats = db.agent_stats(user.id)
    await update.message.reply_text(
        f"Balance: {agent['balance']}\n"
        f"Default inbound: {agent['preferred_inbound'] or 'not set'}\n"
        f"All-time topup: {stats['lifetime_topup']}\n"
        f"Clients created: {stats['clients']}\n"
        f"Total spent: {stats['spent']}"
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


async def set_inbound(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /setinbound <id>")
        return
    inbound = as_int(context.args[0])
    if inbound is None or inbound <= 0:
        await update.message.reply_text("Invalid inbound ID")
        return
    db.set_preferred_inbound(update.effective_user.id, inbound)
    await update.message.reply_text(f"Default inbound set to {inbound}")


async def my_inbound(update: Update, context: ContextTypes.DEFAULT_TYPE):
    a = db.get_agent(update.effective_user.id)
    await update.message.reply_text(f"Default inbound: {a['preferred_inbound'] if a else 'not set'}")


async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /price <days> <gb>")
        return
    d, g = as_int(context.args[0]), as_int(context.args[1])
    if not d or not g or d <= 0 or g <= 0:
        await update.message.reply_text("days and gb must be positive integers")
        return
    if d > MAX_DAYS or g > MAX_GB:
        await update.message.reply_text(f"Limits exceeded. Max days: {MAX_DAYS}, Max GB: {MAX_GB}")
        return
    p = Plan(d, g)
    await update.message.reply_text(f"Price: {p.price}")


async def promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /promo <CODE>")
        return
    try:
        disc = db.apply_promo(context.args[0], update.effective_user.id)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return
    context.user_data["promo_discount"] = disc
    await update.message.reply_text(f"Promo applied: {disc}% on next order")


async def create_inbound(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Only admin can create inbounds")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /createinbound <port> <remark> [protocol] [network]")
        return
    port = as_int(context.args[0])
    if not port or port <= 0:
        await update.message.reply_text("Invalid port")
        return
    x = XUI()
    try:
        x.login()
        inbound_id = x.create_inbound(port, context.args[1], context.args[2] if len(context.args) > 2 else "vless", context.args[3] if len(context.args) > 3 else "tcp")
    except Exception as exc:
        await update.message.reply_text(f"Failed: {exc}")
        return
    await update.message.reply_text(f"Inbound created: {inbound_id}")


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = q.from_user
    db.ensure_agent(user.id, user.username or "", user.full_name or "")

    if q.data == "ui:balance":
        stats = db.agent_stats(user.id)
        a = db.get_agent(user.id)
        await q.message.reply_text(
            f"Balance: {a['balance']}\nDefault inbound: {a['preferred_inbound'] or 'not set'}\n"
            f"All-time topup: {stats['lifetime_topup']}\nClients: {stats['clients']}\nSpent: {stats['spent']}"
        )
    elif q.data == "ui:set_inbound":
        context.user_data["flow"] = "set_inbound"
        await q.message.reply_text("📍 Step 1: Send inbound id to save as default.")
    elif q.data == "ui:wizard_single":
        context.user_data["flow"] = "single_inbound"
        context.user_data["wizard"] = {"kind": "single"}
        await q.message.reply_text(
            "🛒 Single Client Wizard\n"
            "Step 1/6: send inbound id (or type 'default').\n"
            "Hint: default uses your saved inbound from /setinbound."
        )
    elif q.data == "ui:wizard_bulk":
        context.user_data["flow"] = "bulk_inbound"
        context.user_data["wizard"] = {"kind": "bulk"}
        await q.message.reply_text(
            "📦 Bulk Client Wizard\n"
            "Step 1/7: send inbound id (or type 'default').\n"
            "Hint: default uses your saved inbound from /setinbound."
        )
    elif q.data == "ui:price":
        await q.message.reply_text("Use /price <days> <gb>")
    elif q.data == "ui:promo":
        await q.message.reply_text("Use /promo <CODE>")
    elif q.data == "ui:admin_inbound":
        if is_admin(user.id):
            await q.message.reply_text("Use /createinbound <port> <remark> [protocol] [network]")
        else:
            await q.message.reply_text("Only admin")
    else:
        await q.message.reply_text("Unknown action. Use /start to refresh menu.")


async def text_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    uid = update.effective_user.id
    agent = db.get_agent(uid)
    flow = context.user_data.get("flow")
    wiz: Dict = context.user_data.get("wizard", {})

    if flow == "set_inbound":
        inbound = as_int(txt)
        if not inbound or inbound <= 0:
            await update.message.reply_text("Invalid inbound id")
            return
        db.set_preferred_inbound(uid, inbound)
        reset_flow(context)
        await update.message.reply_text(f"✅ Default inbound set to {inbound}")
        return

    if flow in ["single_inbound", "bulk_inbound"]:
        if txt.lower() == "default":
            if not agent or not agent["preferred_inbound"]:
                await update.message.reply_text("No default inbound. Send a numeric inbound id.")
                return
            wiz["inbound"] = int(agent["preferred_inbound"])
        else:
            inbound = as_int(txt)
            if not inbound or inbound <= 0:
                await update.message.reply_text("Invalid inbound id")
                return
            wiz["inbound"] = inbound
        context.user_data["wizard"] = wiz
        context.user_data["flow"] = "remark_or_base"
        if wiz.get("kind") == "single":
            await update.message.reply_text("Step 2/6: send client remark/email. Hint: example user123")
        else:
            await update.message.reply_text("Step 2/7: send base remark. Hint: example agentA")
        return

    if flow == "remark_or_base":
        if not txt:
            await update.message.reply_text("Remark cannot be empty")
            return
        if wiz.get("kind") == "single":
            wiz["remark"] = txt
            await update.message.reply_text("Step 3/6: send duration in days. Hint: 30")
        else:
            wiz["base"] = txt
            await update.message.reply_text("Step 3/7: send client count. Hint: 5")
            context.user_data["flow"] = "bulk_count"
            return
        context.user_data["flow"] = "common_days"
        return

    if flow == "bulk_count":
        count = as_int(txt)
        if not count or count <= 0:
            await update.message.reply_text("Invalid count")
            return
        if count > MAX_BULK_COUNT:
            await update.message.reply_text(f"Count is too high. Max allowed: {MAX_BULK_COUNT}")
            return
        wiz["count"] = count
        context.user_data["flow"] = "common_days"
        await update.message.reply_text("Step 4/7: send duration in days. Hint: 30")
        return

    if flow == "common_days":
        days = as_int(txt)
        if not days or days <= 0:
            await update.message.reply_text("Invalid days")
            return
        if days > MAX_DAYS:
            await update.message.reply_text(f"Days is too high. Max allowed: {MAX_DAYS}")
            return
        wiz["days"] = days
        context.user_data["flow"] = "common_gb"
        step_text = "Step 4/6" if wiz.get("kind") == "single" else "Step 5/7"
        await update.message.reply_text(f"{step_text}: send traffic in GB. Hint: 50")
        return

    if flow == "common_gb":
        gb = as_int(txt)
        if not gb or gb <= 0:
            await update.message.reply_text("Invalid GB")
            return
        if gb > MAX_GB:
            await update.message.reply_text(f"GB is too high. Max allowed: {MAX_GB}")
            return
        wiz["gb"] = gb
        context.user_data["flow"] = "start_after_first_use"
        step_text = "Step 5/6" if wiz.get("kind") == "single" else "Step 6/7"
        await update.message.reply_text(
            f"{step_text}: Start after first use? (y/n)\n"
            "Hint: y means validity starts when client first connects."
        )
        return

    if flow == "start_after_first_use":
        v = txt.lower()
        if v not in ["y", "n", "yes", "no"]:
            await update.message.reply_text("Please answer with y or n")
            return
        wiz["start_after_first_use"] = v in ["y", "yes"]

        plan = Plan(wiz["days"], wiz["gb"])
        count = 1 if wiz["kind"] == "single" else wiz["count"]
        gross = round(plan.price * count, 2)
        discount = float(context.user_data.get("promo_discount", 0.0))
        net = round(gross * (1 - discount / 100), 2)

        context.user_data["flow"] = "confirm_order"
        await update.message.reply_text(wizard_summary(wiz, gross, discount, net))
        return

    if flow == "confirm_order":
        v = txt.lower()
        if v in ["no", "n"]:
            reset_flow(context)
            context.user_data.pop("promo_discount", None)
            await update.message.reply_text("Order canceled.")
            return
        if v not in ["yes", "y"]:
            await update.message.reply_text("Please answer with yes or no")
            return
        await finalize_order(update, context, wiz)
        return

    await update.message.reply_text("Use /start and choose a button.")


async def finalize_order(update: Update, context: ContextTypes.DEFAULT_TYPE, wiz: Dict):
    uid = update.effective_user.id
    plan = Plan(wiz["days"], wiz["gb"])
    count = 1 if wiz["kind"] == "single" else wiz["count"]
    gross = round(plan.price * count, 2)
    disc = float(context.user_data.pop("promo_discount", 0.0))
    net = round(gross * (1 - disc / 100), 2)

    try:
        db.deduct_balance(uid, net, "order.charge", json.dumps({"kind": wiz["kind"], "gross": gross, "disc": disc}))
    except ValueError:
        await update.message.reply_text(f"Insufficient balance. Need {net}")
        reset_flow(context)
        return

    x = XUI()
    links = []
    try:
        x.login()
        inbound = x.get_inbound(wiz["inbound"])
        clients = []
        expiry = expiry_value(wiz["days"], wiz["start_after_first_use"])

        if wiz["kind"] == "single":
            uidc = str(uuid.uuid4())
            clients.append({
                "id": uidc,
                "email": wiz["remark"],
                "enable": True,
                "expiryTime": expiry,
                "totalGB": plan.total_bytes,
                "flow": "",
                "limitIp": 0,
                "tgId": str(uid),
                "subId": "",
                "comment": "tg",
                "reset": 0,
            })
            links.append(vless_link(uidc, inbound, wiz["remark"]))
        else:
            for i in range(wiz["count"]):
                uidc = str(uuid.uuid4())
                remark = f"{wiz['base']}_{i + 1}"
                clients.append({
                    "id": uidc,
                    "email": remark,
                    "enable": True,
                    "expiryTime": expiry,
                    "totalGB": plan.total_bytes,
                    "flow": "",
                    "limitIp": 0,
                    "tgId": str(uid),
                    "subId": "",
                    "comment": "tg",
                    "reset": 0,
                })
                links.append(vless_link(uidc, inbound, remark))

        x.add_clients(wiz["inbound"], clients)
        db.create_order(uid, wiz["inbound"], wiz["kind"], wiz["days"], wiz["gb"], count, gross, disc, net, "success")
    except Exception as exc:
        db.add_balance(uid, net, "order.refund", str(exc))
        db.create_order(uid, wiz["inbound"], wiz["kind"], wiz["days"], wiz["gb"], count, gross, disc, net, "failed")
        await update.message.reply_text(f"Creation failed, refunded. Error: {exc}")
        reset_flow(context)
        return

    bal = db.get_agent(uid)["balance"]
    await update.message.reply_text(
        f"Done ✅\nType: {wiz['kind']}\nInbound: {wiz['inbound']}\n"
        f"Days: {wiz['days']} | GB: {wiz['gb']} | Start after first use: {'Yes' if wiz['start_after_first_use'] else 'No'}\n"
        f"Gross: {gross}\nDiscount: {disc}%\nCharged: {net}\nBalance: {bal}"
    )
    await send_links(update, links)
    reset_flow(context)


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
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("topup", topup))
    app.add_handler(CommandHandler("setinbound", set_inbound))
    app.add_handler(CommandHandler("myinbound", my_inbound))
    app.add_handler(CommandHandler("price", price))
    app.add_handler(CommandHandler("promo", promo))
    app.add_handler(CommandHandler("createinbound", create_inbound))
    app.add_handler(CallbackQueryHandler(callback_router, pattern=r"^ui:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_flow))
    app.run_polling()


if __name__ == "__main__":
    main()
