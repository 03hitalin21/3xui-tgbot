import asyncio
import os
import secrets
import string
import time
from datetime import datetime, timezone
from typing import List

from telegram import Bot

import db

BASE_STYLE = """
<style>
body{font-family:Arial;max-width:1100px;margin:20px auto;padding:0 12px}
.card{border:1px solid #ddd;border-radius:8px;padding:12px;margin-bottom:12px}
table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:8px;font-size:13px}
input,textarea{padding:6px;margin:4px;width:100%}
.flash{border-radius:6px;padding:8px;margin-bottom:10px}
.flash.success{background:#e7f6ea;border:1px solid #b8e0c2}
.flash.error{background:#fde8e8;border:1px solid #f5c2c7}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.pill{background:#f3f3f3;border-radius:6px;padding:8px;text-align:center}
a{color:#0b5ed7}
</style>
"""


def format_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def generate_promo_code(prefix: str, length: int = 10) -> str:
    alphabet = string.ascii_uppercase + string.digits
    random_part = "".join(secrets.choice(alphabet) for _ in range(length))
    return f"{prefix}{random_part}".upper()


def list_recent_referrals(limit: int = 50):
    referrals = []
    for row in db.list_referrals(limit=limit):
        item = dict(row)
        item["referred_at"] = format_ts(int(item["referred_at"]))
        referrals.append(item)
    return referrals


def list_users_formatted(search: str):
    users = db.search_agents(search) if search else db.list_agents()
    formatted = []
    for u in users:
        row = dict(u)
        row["created_at"] = format_ts(int(row["created_at"]))
        formatted.append(row)
    return formatted


def get_user_detail_payload(tg_id: int):
    user = db.get_agent_with_client_count(tg_id)
    if not user:
        return None, []
    user_row = dict(user)
    user_row["created_at"] = format_ts(int(user_row["created_at"]))
    transactions = []
    for t in db.list_transactions(tg_id, limit=20):
        row = dict(t)
        row["created_at"] = format_ts(int(row["created_at"]))
        transactions.append(row)
    return user_row, transactions


async def notify_topup_result(tg_id: int, topup_id: int, balance: float):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return
    bot = Bot(token=token)
    await bot.send_message(chat_id=tg_id, text=f"✅ درخواست شارژ #{topup_id} تایید شد. موجودی جدید شما: {int(balance) if float(balance).is_integer() else round(balance,2)} تومان")


async def broadcast(message: str, user_ids: List[int]) -> dict:
    bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN", ""))
    sent = 0
    failed = 0
    for uid in user_ids:
        try:
            await bot.send_message(chat_id=uid, text=message)
            sent += 1
        except Exception:
            failed += 1
    return {"sent": sent, "failed": failed}


def run_broadcast(message: str, user_ids: List[int]) -> dict:
    return asyncio.run(broadcast(message, user_ids))


def run_notify_topup_result(tg_id: int, topup_id: int, balance: float) -> None:
    asyncio.run(notify_topup_result(tg_id, topup_id, balance))
