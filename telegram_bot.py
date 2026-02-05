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
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application,
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

# Wallet units (e.g. USD, IRR, credits)
PRICE_PER_GB = float(os.getenv("PRICE_PER_GB", "0.15"))
PRICE_PER_DAY = float(os.getenv("PRICE_PER_DAY", "0.10"))
DEFAULT_PLANS: List[Tuple[int, int]] = [(7, 20), (30, 50), (90, 100)]

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["💰 Balance", "📦 Plans"],
        ["🧮 Price", "📚 Help"],
    ],
    resize_keyboard=True,
)


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
            else:
                if "preferred_inbound" not in data[key]:
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
            f"?type=tcp"
            f"&security=reality"
            f"&encryption=none"
            f"&pbk={r['settings']['publicKey']}"
            f"&fp={r['settings'].get('fingerprint', 'chrome')}"
            f"&sni={r['serverNames'][0]}"
            f"&sid={r['shortIds'][0]}"
            f"#{remark}"
        )

    return (
        f"vless://{uuid_}@{SERVER_HOST}:{inbound['port']}"
        f"?type={inbound['network']}"
        f"&security={inbound['security']}"
        f"&encryption=none"
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


def parse_buy_args(args: List[str], preferred_inbound: Optional[int]) -> Tuple[int, ClientPlan, str]:
    if len(args) < 2:
        raise ValueError("Usage: /buy <inbound_id> <days> <gb> [remark] OR /buy <days> <gb> [remark] after /setinbound")

    first = safe_int(args[0])
    second = safe_int(args[1]) if len(args) > 1 else None
    third = safe_int(args[2]) if len(args) > 2 else None

    if first is not None and second is not None and third is not None:
        inbound_id = first
        days = second
        gb = third
        remark = " ".join(args[3:]).strip()
    elif first is not None and second is not None and preferred_inbound is not None:
        inbound_id = preferred_inbound
        days = first
        gb = second
        remark = " ".join(args[2:]).strip()
    else:
        raise ValueError("Invalid input. Use /buy <inbound_id> <days> <gb> [remark] or set inbound first with /setinbound")

    if days <= 0 or gb <= 0:
        raise ValueError("Days and GB must be positive numbers")

    if not remark:
        remark = f"tg_{int(time.time())}"

    return inbound_id, ClientPlan(days=days, traffic_gb=gb), remark


def parse_bulk_args(args: List[str], preferred_inbound: Optional[int]) -> Tuple[int, ClientPlan, int, str]:
    if len(args) < 3:
        raise ValueError("Usage: /bulk <inbound_id> <days> <gb> <count> [base_remark] OR /bulk <days> <gb> <count> [base_remark]")

    n0 = safe_int(args[0])
    n1 = safe_int(args[1]) if len(args) > 1 else None
    n2 = safe_int(args[2]) if len(args) > 2 else None
    n3 = safe_int(args[3]) if len(args) > 3 else None

    if n0 is not None and n1 is not None and n2 is not None and n3 is not None:
        inbound_id = n0
        days = n1
        gb = n2
        count = n3
        base_remark = " ".join(args[4:]).strip()
    elif n0 is not None and n1 is not None and n2 is not None and preferred_inbound is not None:
        inbound_id = preferred_inbound
        days = n0
        gb = n1
        count = n2
        base_remark = " ".join(args[3:]).strip()
    else:
        raise ValueError("Invalid bulk input. Use /setinbound then /bulk <days> <gb> <count>")

    if days <= 0 or gb <= 0 or count <= 0:
        raise ValueError("Days, GB, and count must be positive numbers")

    if not base_remark:
        base_remark = f"bulk_{int(time.time())}"

    return inbound_id, ClientPlan(days=days, traffic_gb=gb), count, base_remark


def plans_text() -> str:
    lines = ["📦 Suggested plans:"]
    for idx, (days, gb) in enumerate(DEFAULT_PLANS, start=1):
        p = ClientPlan(days=days, traffic_gb=gb)
        lines.append(f"{idx}) {days} days / {gb} GB → {p.price}")
    lines.append("\nQuick usage:")
    lines.append("- /price 30 50")
    lines.append("- /buy 12 30 50 my-client")
    lines.append("- /setinbound 12 then /buy 30 50 my-client")
    lines.append("- /bulk 12 30 50 5 teamA")
    return "\n".join(lines)


wallets = WalletStore(AGENTS_FILE)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    wallets.ensure_agent(tg_id)
    await update.message.reply_text(
        "✅ Bot is ready. Use /help for full guide.",
        reply_markup=MAIN_KEYBOARD,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Commands:\n"
        "/balance\n"
        "/topup <amount>\n"
        "/setinbound <inbound_id>\n"
        "/myinbound\n"
        "/plans\n"
        "/price <days> <gb>\n"
        "/buy <inbound_id> <days> <gb> [remark]\n"
        "/buy <days> <gb> [remark] (if /setinbound used)\n"
        "/bulk <inbound_id> <days> <gb> <count> [base_remark]\n"
        "/bulk <days> <gb> <count> [base_remark] (if /setinbound used)"
    )


async def plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(plans_text())


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    amount = wallets.get_balance(update.effective_user.id)
    inbound = wallets.get_preferred_inbound(update.effective_user.id)
    inbound_text = inbound if inbound is not None else "not set"
    await update.message.reply_text(f"💰 Balance: {amount}\n📍 Preferred inbound: {inbound_text}")


async def set_inbound(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /setinbound <inbound_id>")
        return

    inbound_id = safe_int(context.args[0])
    if inbound_id is None or inbound_id <= 0:
        await update.message.reply_text("Inbound ID must be a positive integer")
        return

    wallets.set_preferred_inbound(update.effective_user.id, inbound_id)
    await update.message.reply_text(f"✅ Preferred inbound set to {inbound_id}")


async def my_inbound(update: Update, context: ContextTypes.DEFAULT_TYPE):
    inbound_id = wallets.get_preferred_inbound(update.effective_user.id)
    if inbound_id is None:
        await update.message.reply_text("No preferred inbound is set. Use /setinbound <id>")
        return
    await update.message.reply_text(f"Preferred inbound: {inbound_id}")


async def topup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /topup <amount>")
        return

    try:
        amount = float(context.args[0])
    except ValueError:
        await update.message.reply_text("Amount must be a number, e.g. /topup 100")
        return

    if amount <= 0:
        await update.message.reply_text("Amount must be positive")
        return

    new_balance = wallets.add_balance(update.effective_user.id, amount)
    await update.message.reply_text(f"✅ Top-up successful. New balance: {new_balance}")


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
        vless = make_vless(uid, inbound, remark)
    except Exception as exc:
        wallets.add_balance(update.effective_user.id, plan.price)
        await update.message.reply_text(f"❌ Creation failed. Wallet refunded. Error: {exc}")
        return

    new_balance = wallets.get_balance(update.effective_user.id)
    await update.message.reply_text(
        f"✅ Client created\n"
        f"Inbound: {inbound_id}\n"
        f"Plan: {plan.days} days / {plan.traffic_gb} GB\n"
        f"Charged: {plan.price}\n"
        f"Balance: {new_balance}\n\n"
        f"{vless}"
    )


async def bulk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    preferred = wallets.get_preferred_inbound(update.effective_user.id)
    try:
        inbound_id, plan, count, base_remark = parse_bulk_args(context.args, preferred)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return

    total_price = round(plan.price * count, 2)
    try:
        wallets.deduct(update.effective_user.id, total_price)
    except ValueError:
        await update.message.reply_text(f"❌ Insufficient balance for bulk order ({total_price})")
        return

    xui = XUI()
    links = []
    clients = []

    try:
        xui.login()
        inbound = xui.get_inbound(inbound_id)
        for i in range(count):
            uid = str(uuid.uuid4())
            remark = f"{base_remark}_{i + 1}"
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

    new_balance = wallets.get_balance(update.effective_user.id)
    await update.message.reply_text(
        f"✅ Bulk created: {count} clients\n"
        f"Inbound: {inbound_id}\n"
        f"Plan: {plan.days} days / {plan.traffic_gb} GB\n"
        f"Charged: {total_price}\n"
        f"Balance: {new_balance}"
    )
    await update.message.reply_text("\n".join(links))


async def keyboard_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if text == "💰 Balance":
        await balance(update, context)
    elif text == "📦 Plans":
        await plans(update, context)
    elif text == "🧮 Price":
        await update.message.reply_text("Use: /price <days> <gb>  (example: /price 30 50)")
    elif text == "📚 Help":
        await help_cmd(update, context)


def main() -> None:
    missing = validate_config()
    if missing:
        raise RuntimeError(f"Missing env vars: {missing}")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("plans", plans))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("topup", topup))
    app.add_handler(CommandHandler("setinbound", set_inbound))
    app.add_handler(CommandHandler("myinbound", my_inbound))
    app.add_handler(CommandHandler("price", price))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("bulk", bulk))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, keyboard_actions))

    app.run_polling()


if __name__ == "__main__":
    main()
