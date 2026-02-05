import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional, Tuple

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

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = os.getenv("XUI_BASE_URL", "")
USERNAME = os.getenv("XUI_USERNAME", "")
PASSWORD = os.getenv("XUI_PASSWORD", "")
SERVER_HOST = os.getenv("XUI_SERVER_HOST", "")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
AGENTS_FILE = Path(os.getenv("AGENTS_FILE", "agents.json"))

PRICE_PER_GB = float(os.getenv("PRICE_PER_GB", "0.15"))
PRICE_PER_DAY = float(os.getenv("PRICE_PER_DAY", "0.10"))
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "8477244366"))

DEFAULT_PLANS: List[Tuple[int, int]] = [(7, 20), (30, 50), (90, 100)]


def main_menu(is_admin: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("💰 Balance", callback_data="ui:balance")],
        [InlineKeyboardButton("📍 Set Default Inbound", callback_data="ui:set_inbound")],
        [InlineKeyboardButton("🛒 Buy Single Client", callback_data="ui:buy_single")],
        [InlineKeyboardButton("📦 Buy Bulk Clients", callback_data="ui:buy_bulk")],
        [InlineKeyboardButton("🧮 Price Calculator", callback_data="ui:price")],
        [InlineKeyboardButton("📚 Plans", callback_data="ui:plans")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton("🛠 Create Inbound (Admin)", callback_data="ui:admin_inbound")])
    return InlineKeyboardMarkup(rows)


@dataclass
class ClientPlan:
    days: int
    traffic_gb: int

    @property
    def expiry_ms(self) -> int:
        return int((time.time() + self.days * 86400) * 1000)

    @property
    def total_bytes(self) -> int:
        return self.traffic_gb * 1024 ** 3

    @property
    def price(self) -> float:
        return round(self.traffic_gb * PRICE_PER_GB + self.days * PRICE_PER_DAY, 2)


class WalletStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = Lock()
        if not self.path.exists():
            self.path.write_text("{}", encoding="utf-8")

    def _load(self) -> Dict[str, Dict[str, float]]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, data: Dict[str, Dict[str, float]]) -> None:
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def ensure_agent(self, tg_id: int) -> Dict[str, float]:
        with self.lock:
            data = self._load()
            key = str(tg_id)
            if key not in data:
                data[key] = {"balance": 0.0, "preferred_inbound": None}
                self._save(data)
            elif "preferred_inbound" not in data[key]:
                data[key]["preferred_inbound"] = None
                self._save(data)
            return data[key]

    def get_balance(self, tg_id: int) -> float:
        with self.lock:
            data = self._load()
            return float(data.get(str(tg_id), {}).get("balance", 0.0))

    def add_balance(self, tg_id: int, amount: float) -> float:
        with self.lock:
            data = self._load()
            key = str(tg_id)
            if key not in data:
                data[key] = {"balance": 0.0, "preferred_inbound": None}
            data[key]["balance"] = round(float(data[key].get("balance", 0.0)) + amount, 2)
            self._save(data)
            return float(data[key]["balance"])

    def deduct(self, tg_id: int, amount: float) -> float:
        with self.lock:
            data = self._load()
            key = str(tg_id)
            if key not in data:
                data[key] = {"balance": 0.0, "preferred_inbound": None}
            current = float(data[key].get("balance", 0.0))
            if current < amount:
                raise ValueError("Insufficient balance")
            data[key]["balance"] = round(current - amount, 2)
            self._save(data)
            return float(data[key]["balance"])

    def set_preferred_inbound(self, tg_id: int, inbound_id: int) -> None:
        with self.lock:
            data = self._load()
            key = str(tg_id)
            if key not in data:
                data[key] = {"balance": 0.0, "preferred_inbound": inbound_id}
            else:
                data[key]["preferred_inbound"] = inbound_id
            self._save(data)

    def get_preferred_inbound(self, tg_id: int) -> Optional[int]:
        with self.lock:
            data = self._load()
            inbound = data.get(str(tg_id), {}).get("preferred_inbound")
            return int(inbound) if inbound is not None else None


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
            "protocol": obj["protocol"],
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
        inbound_payload = {
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
            "streamSettings": json.dumps({
                "network": network,
                "security": "none",
                "tcpSettings": {"acceptProxyProtocol": False, "header": {"type": "none"}},
            }),
            "sniffing": json.dumps({
                "enabled": False,
                "destOverride": ["http", "tls", "quic", "fakedns"],
                "metadataOnly": False,
                "routeOnly": False,
            }),
        }

        r = self.s.post(f"{BASE_URL}/panel/api/inbounds/add", data=inbound_payload, timeout=30)
        data = r.json()
        if not data.get("success"):
            raise RuntimeError(f"Failed to create inbound: {r.text}")
        return data.get("obj", {}).get("id")


def safe_int(value: str) -> Optional[int]:
    try:
        return int(value)
    except ValueError:
        return None


def make_vless(uuid_: str, inbound: dict, remark: str) -> str:
    if inbound["security"] == "reality":
        r = inbound["reality"]
        return (
            f"vless://{uuid_}@{SERVER_HOST}:{inbound['port']}"
            f"?type=tcp&security=reality&encryption=none"
            f"&pbk={r['settings']['publicKey']}"
            f"&fp={r['settings'].get('fingerprint', 'chrome')}"
            f"&sni={r['serverNames'][0]}"
            f"&sid={r['shortIds'][0]}"
            f"#{remark}"
        )

    return (
        f"vless://{uuid_}@{SERVER_HOST}:{inbound['port']}"
        f"?type={inbound['network']}&security={inbound['security']}&encryption=none"
        f"#{remark}"
    )


def validate_config() -> str:
    missing = []
    for key, value in {
        "TELEGRAM_BOT_TOKEN": BOT_TOKEN,
        "XUI_BASE_URL": BASE_URL,
        "XUI_USERNAME": USERNAME,
        "XUI_PASSWORD": PASSWORD,
        "XUI_SERVER_HOST": SERVER_HOST,
    }.items():
        if not value:
            missing.append(key)
    return ", ".join(missing)


def plans_text() -> str:
    lines = ["📚 Suggested plans:"]
    for idx, (days, gb) in enumerate(DEFAULT_PLANS, start=1):
        p = ClientPlan(days=days, traffic_gb=gb)
        lines.append(f"{idx}) {days} days / {gb} GB → {p.price}")
    return "\n".join(lines)


def admin_only(tg_id: int) -> bool:
    return tg_id == ADMIN_TELEGRAM_ID


wallets = WalletStore(AGENTS_FILE)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    wallets.ensure_agent(tg_id)
    await update.message.reply_text(
        "✅ Welcome. Use the menu below.",
        reply_markup=main_menu(is_admin=admin_only(tg_id)),
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "Commands:\n"
        "/start\n/help\n/balance\n/topup <amount>\n/setinbound <id>\n/myinbound\n"
        "/price <days> <gb>\n/buy <days> <gb> [remark]\n/bulk <days> <gb> <count> [base_remark]\n"
        "(or include inbound explicitly: /buy <inbound_id> <days> <gb> [remark])"
    )
    if admin_only(update.effective_user.id):
        txt += "\nAdmin: /createinbound <port> <remark> [protocol] [network]"
    await update.message.reply_text(txt)


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    amount = wallets.get_balance(tg_id)
    inbound = wallets.get_preferred_inbound(tg_id)
    inbound_text = inbound if inbound is not None else "not set"
    await update.message.reply_text(f"💰 Balance: {amount}\n📍 Default inbound: {inbound_text}")


async def set_inbound(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /setinbound <inbound_id>")
        return
    inbound_id = safe_int(context.args[0])
    if inbound_id is None or inbound_id <= 0:
        await update.message.reply_text("Inbound ID must be a positive integer")
        return
    wallets.set_preferred_inbound(update.effective_user.id, inbound_id)
    await update.message.reply_text(f"✅ Default inbound set to {inbound_id}")


async def my_inbound(update: Update, context: ContextTypes.DEFAULT_TYPE):
    inbound = wallets.get_preferred_inbound(update.effective_user.id)
    if inbound is None:
        await update.message.reply_text("No default inbound. Use /setinbound <id>")
        return
    await update.message.reply_text(f"📍 Default inbound: {inbound}")


async def topup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /topup <amount>")
        return
    try:
        amount = float(context.args[0])
    except ValueError:
        await update.message.reply_text("Amount must be numeric")
        return
    if amount <= 0:
        await update.message.reply_text("Amount must be positive")
        return
    new_balance = wallets.add_balance(update.effective_user.id, amount)
    await update.message.reply_text(f"✅ Top-up successful. Balance: {new_balance}")


async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /price <days> <gb>")
        return
    days = safe_int(context.args[0])
    gb = safe_int(context.args[1])
    if days is None or gb is None or days <= 0 or gb <= 0:
        await update.message.reply_text("Days and GB must be positive integers")
        return
    plan = ClientPlan(days=days, traffic_gb=gb)
    await update.message.reply_text(f"🧮 Price for {days} days / {gb} GB: {plan.price}")


def parse_buy_args(args: List[str], preferred_inbound: Optional[int]) -> Tuple[int, ClientPlan, str]:
    if len(args) < 2:
        raise ValueError("Usage: /buy <days> <gb> [remark] (with /setinbound) OR /buy <inbound> <days> <gb> [remark]")

    n0 = safe_int(args[0])
    n1 = safe_int(args[1]) if len(args) > 1 else None
    n2 = safe_int(args[2]) if len(args) > 2 else None

    if n0 is not None and n1 is not None and n2 is not None:
        inbound_id, days, gb = n0, n1, n2
        remark = " ".join(args[3:]).strip()
    elif preferred_inbound is not None and n0 is not None and n1 is not None:
        inbound_id, days, gb = preferred_inbound, n0, n1
        remark = " ".join(args[2:]).strip()
    else:
        raise ValueError("Invalid input. Set default inbound using /setinbound or include inbound in /buy")

    if days <= 0 or gb <= 0:
        raise ValueError("Days and GB must be positive")

    if not remark:
        remark = f"client_{int(time.time())}"

    return inbound_id, ClientPlan(days=days, traffic_gb=gb), remark


def parse_bulk_args(args: List[str], preferred_inbound: Optional[int]) -> Tuple[int, ClientPlan, int, str]:
    if len(args) < 3:
        raise ValueError("Usage: /bulk <days> <gb> <count> [base] (with /setinbound) OR /bulk <inbound> <days> <gb> <count> [base]")

    n0 = safe_int(args[0])
    n1 = safe_int(args[1]) if len(args) > 1 else None
    n2 = safe_int(args[2]) if len(args) > 2 else None
    n3 = safe_int(args[3]) if len(args) > 3 else None

    if n0 is not None and n1 is not None and n2 is not None and n3 is not None:
        inbound_id, days, gb, count = n0, n1, n2, n3
        base = " ".join(args[4:]).strip()
    elif preferred_inbound is not None and n0 is not None and n1 is not None and n2 is not None:
        inbound_id, days, gb, count = preferred_inbound, n0, n1, n2
        base = " ".join(args[3:]).strip()
    else:
        raise ValueError("Invalid input. Set default inbound using /setinbound or include inbound in /bulk")

    if days <= 0 or gb <= 0 or count <= 0:
        raise ValueError("Days, GB, and count must be positive")

    if not base:
        base = f"bulk_{int(time.time())}"

    return inbound_id, ClientPlan(days=days, traffic_gb=gb), count, base


async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    preferred = wallets.get_preferred_inbound(update.effective_user.id)
    try:
        inbound_id, plan, remark = parse_buy_args(context.args, preferred)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return

    try:
        wallets.deduct(update.effective_user.id, plan.price)
    except ValueError:
        await update.message.reply_text("❌ Insufficient balance")
        return

    xui = XUI()
    try:
        xui.login()
        inbound = xui.get_inbound(inbound_id)
        uid = str(uuid.uuid4())
        client = {
            "id": uid,
            "email": remark,
            "enable": True,
            "expiryTime": plan.expiry_ms,
            "totalGB": plan.total_bytes,
            "flow": "",
            "limitIp": 0,
            "tgId": str(update.effective_user.id),
            "subId": "",
            "comment": "created-by-telegram-bot",
            "reset": 0,
        }
        xui.add_clients(inbound_id, [client])
        link = make_vless(uid, inbound, remark)
    except Exception as exc:
        wallets.add_balance(update.effective_user.id, plan.price)
        await update.message.reply_text(f"❌ Creation failed. Wallet refunded. Error: {exc}")
        return

    await update.message.reply_text(
        f"✅ Client created\nInbound: {inbound_id}\nPlan: {plan.days}d/{plan.traffic_gb}GB\n"
        f"Charged: {plan.price}\nBalance: {wallets.get_balance(update.effective_user.id)}\n\n{link}"
    )


async def bulk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    preferred = wallets.get_preferred_inbound(update.effective_user.id)
    try:
        inbound_id, plan, count, base = parse_bulk_args(context.args, preferred)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return

    total_price = round(plan.price * count, 2)
    try:
        wallets.deduct(update.effective_user.id, total_price)
    except ValueError:
        await update.message.reply_text("❌ Insufficient balance")
        return

    xui = XUI()
    clients = []
    links = []

    try:
        xui.login()
        inbound = xui.get_inbound(inbound_id)
        for i in range(count):
            uid = str(uuid.uuid4())
            remark = f"{base}_{i+1}"
            clients.append(
                {
                    "id": uid,
                    "email": remark,
                    "enable": True,
                    "expiryTime": plan.expiry_ms,
                    "totalGB": plan.total_bytes,
                    "flow": "",
                    "limitIp": 0,
                    "tgId": str(update.effective_user.id),
                    "subId": "",
                    "comment": "created-by-telegram-bot",
                    "reset": 0,
                }
            )
            links.append(make_vless(uid, inbound, remark))

        xui.add_clients(inbound_id, clients)
    except Exception as exc:
        wallets.add_balance(update.effective_user.id, total_price)
        await update.message.reply_text(f"❌ Bulk creation failed. Wallet refunded. Error: {exc}")
        return

    await update.message.reply_text(
        f"✅ Bulk created: {count}\nInbound: {inbound_id}\nPlan: {plan.days}d/{plan.traffic_gb}GB\n"
        f"Charged: {total_price}\nBalance: {wallets.get_balance(update.effective_user.id)}"
    )
    await update.message.reply_text("\n".join(links))


async def create_inbound(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update.effective_user.id):
        await update.message.reply_text("⛔ Only admin can create inbounds.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Usage: /createinbound <port> <remark> [protocol] [network]")
        return

    port = safe_int(context.args[0])
    if port is None or port <= 0:
        await update.message.reply_text("Port must be a positive integer")
        return

    remark = context.args[1]
    protocol = context.args[2] if len(context.args) > 2 else "vless"
    network = context.args[3] if len(context.args) > 3 else "tcp"

    xui = XUI()
    try:
        xui.login()
        inbound_id = xui.create_inbound(port=port, remark=remark, protocol=protocol, network=network)
    except Exception as exc:
        await update.message.reply_text(f"❌ Inbound creation failed: {exc}")
        return

    await update.message.reply_text(f"✅ Inbound created\nID: {inbound_id}\nPort: {port}\nRemark: {remark}")


async def text_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    flow = context.user_data.get("flow")

    if flow == "set_inbound":
        inbound_id = safe_int(text)
        if inbound_id is None or inbound_id <= 0:
            await update.message.reply_text("Send a valid positive inbound ID.")
            return
        wallets.set_preferred_inbound(update.effective_user.id, inbound_id)
        context.user_data.pop("flow", None)
        await update.message.reply_text(f"✅ Default inbound set to {inbound_id}")
        return

    if flow == "topup":
        try:
            amount = float(text)
        except ValueError:
            await update.message.reply_text("Send a numeric amount, e.g. 100")
            return
        if amount <= 0:
            await update.message.reply_text("Amount must be > 0")
            return
        new_balance = wallets.add_balance(update.effective_user.id, amount)
        context.user_data.pop("flow", None)
        await update.message.reply_text(f"✅ Top-up successful. Balance: {new_balance}")
        return

    await update.message.reply_text("Use /start to open the menu.")


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data

    if data == "ui:balance":
        amount = wallets.get_balance(user_id)
        inbound = wallets.get_preferred_inbound(user_id)
        inbound_text = inbound if inbound is not None else "not set"
        await query.message.reply_text(f"💰 Balance: {amount}\n📍 Default inbound: {inbound_text}")
        return

    if data == "ui:set_inbound":
        context.user_data["flow"] = "set_inbound"
        await query.message.reply_text("Send inbound ID now.")
        return

    if data == "ui:buy_single":
        await query.message.reply_text(
            "Quick buy format:\n"
            "- /buy <days> <gb> [remark] (if default inbound is set)\n"
            "- /buy <inbound_id> <days> <gb> [remark]"
        )
        return

    if data == "ui:buy_bulk":
        await query.message.reply_text(
            "Quick bulk format:\n"
            "- /bulk <days> <gb> <count> [base] (if default inbound is set)\n"
            "- /bulk <inbound_id> <days> <gb> <count> [base]"
        )
        return

    if data == "ui:price":
        await query.message.reply_text("Use /price <days> <gb> (e.g. /price 30 50)")
        return

    if data == "ui:plans":
        await query.message.reply_text(plans_text())
        return

    if data == "ui:admin_inbound":
        if not admin_only(user_id):
            await query.message.reply_text("⛔ Only admin can create inbounds.")
            return
        await query.message.reply_text("Admin use: /createinbound <port> <remark> [protocol] [network]")


def main() -> None:
    missing = validate_config()
    if missing:
        raise RuntimeError(f"Missing env vars: {missing}")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("topup", topup))
    app.add_handler(CommandHandler("setinbound", set_inbound))
    app.add_handler(CommandHandler("myinbound", my_inbound))
    app.add_handler(CommandHandler("price", price))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("bulk", bulk))
    app.add_handler(CommandHandler("createinbound", create_inbound))

    app.add_handler(CallbackQueryHandler(callback_router, pattern=r"^ui:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_flow))

    app.run_polling()


if __name__ == "__main__":
    main()
