import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Dict, List, Optional, Tuple

DB_PATH = os.getenv("BOT_DB_PATH", "bot.db")


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS agents (
                tg_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                balance REAL NOT NULL DEFAULT 0,
                lifetime_topup REAL NOT NULL DEFAULT 0,
                preferred_inbound INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS promo_codes (
                code TEXT PRIMARY KEY,
                discount_percent REAL NOT NULL,
                max_uses INTEGER,
                used_count INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS promo_redemptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                tg_id INTEGER NOT NULL,
                redeemed_at INTEGER NOT NULL,
                UNIQUE(code, tg_id)
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER NOT NULL,
                inbound_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                days INTEGER NOT NULL,
                gb INTEGER NOT NULL,
                count INTEGER NOT NULL,
                gross_price REAL NOT NULL,
                discount_percent REAL NOT NULL DEFAULT 0,
                net_price REAL NOT NULL,
                status TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS wallet_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                reason TEXT NOT NULL,
                meta TEXT,
                created_at INTEGER NOT NULL
            );
            """
        )

        for key, val in [("price_per_gb", "0.15"), ("price_per_day", "0.10")]:
            conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?,?)", (key, val))


def now_ts() -> int:
    return int(time.time())


def ensure_agent(tg_id: int, username: str = "", full_name: str = "") -> None:
    ts = now_ts()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO agents(tg_id, username, full_name, created_at, updated_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(tg_id) DO UPDATE SET
                username=excluded.username,
                full_name=excluded.full_name,
                updated_at=excluded.updated_at
            """,
            (tg_id, username, full_name, ts, ts),
        )


def get_agent(tg_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM agents WHERE tg_id=?", (tg_id,)).fetchone()


def set_preferred_inbound(tg_id: int, inbound_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE agents SET preferred_inbound=?, updated_at=? WHERE tg_id=?", (inbound_id, now_ts(), tg_id))


def add_balance(tg_id: int, amount: float, reason: str, meta: str = "") -> float:
    with get_conn() as conn:
        conn.execute(
            "UPDATE agents SET balance=balance+?, lifetime_topup=lifetime_topup+?, updated_at=? WHERE tg_id=?",
            (amount, amount if amount > 0 and reason.startswith("topup") else 0, now_ts(), tg_id),
        )
        conn.execute(
            "INSERT INTO wallet_ledger(tg_id, amount, reason, meta, created_at) VALUES(?,?,?,?,?)",
            (tg_id, amount, reason, meta, now_ts()),
        )
        row = conn.execute("SELECT balance FROM agents WHERE tg_id=?", (tg_id,)).fetchone()
        return float(row["balance"])


def deduct_balance(tg_id: int, amount: float, reason: str, meta: str = "") -> float:
    with get_conn() as conn:
        row = conn.execute("SELECT balance FROM agents WHERE tg_id=?", (tg_id,)).fetchone()
        bal = float(row["balance"]) if row else 0.0
        if bal < amount:
            raise ValueError("Insufficient balance")
        conn.execute("UPDATE agents SET balance=balance-?, updated_at=? WHERE tg_id=?", (amount, now_ts(), tg_id))
        conn.execute(
            "INSERT INTO wallet_ledger(tg_id, amount, reason, meta, created_at) VALUES(?,?,?,?,?)",
            (tg_id, -amount, reason, meta, now_ts()),
        )
        nrow = conn.execute("SELECT balance FROM agents WHERE tg_id=?", (tg_id,)).fetchone()
        return float(nrow["balance"])


def get_setting_float(key: str) -> float:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return float(row["value"])


def set_setting(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def create_promo(code: str, discount_percent: float, max_uses: Optional[int]) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO promo_codes(code, discount_percent, max_uses, created_at) VALUES(?,?,?,?)",
            (code.upper(), discount_percent, max_uses, now_ts()),
        )


def list_promos() -> List[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM promo_codes ORDER BY created_at DESC").fetchall()


def apply_promo(code: str, tg_id: int) -> float:
    c = code.upper()
    with get_conn() as conn:
        p = conn.execute("SELECT * FROM promo_codes WHERE code=? AND active=1", (c,)).fetchone()
        if not p:
            raise ValueError("Promo code not found or inactive")
        if p["max_uses"] is not None and p["used_count"] >= p["max_uses"]:
            raise ValueError("Promo code usage limit reached")
        existing = conn.execute("SELECT 1 FROM promo_redemptions WHERE code=? AND tg_id=?", (c, tg_id)).fetchone()
        if existing:
            raise ValueError("Promo code already used")
        conn.execute("INSERT INTO promo_redemptions(code, tg_id, redeemed_at) VALUES(?,?,?)", (c, tg_id, now_ts()))
        conn.execute("UPDATE promo_codes SET used_count=used_count+1 WHERE code=?", (c,))
        return float(p["discount_percent"])


def create_order(tg_id: int, inbound_id: int, kind: str, days: int, gb: int, count: int, gross: float, disc: float, net: float, status: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO orders(tg_id,inbound_id,kind,days,gb,count,gross_price,discount_percent,net_price,status,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (tg_id, inbound_id, kind, days, gb, count, gross, disc, net, status, now_ts()),
        )
        return cur.lastrowid


def agent_stats(tg_id: int) -> Dict[str, float]:
    with get_conn() as conn:
        orders = conn.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(count),0) clients, COALESCE(SUM(net_price),0) spent FROM orders WHERE tg_id=? AND status='success'",
            (tg_id,),
        ).fetchone()
        ag = conn.execute("SELECT balance, lifetime_topup FROM agents WHERE tg_id=?", (tg_id,)).fetchone()
    return {
        "orders": int(orders["c"]),
        "clients": int(orders["clients"]),
        "spent": float(orders["spent"]),
        "balance": float(ag["balance"] if ag else 0),
        "lifetime_topup": float(ag["lifetime_topup"] if ag else 0),
    }


def top_agents(limit: int = 50) -> List[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT a.tg_id, a.username, a.full_name, a.balance, a.lifetime_topup,
                   COALESCE(SUM(o.count),0) clients, COALESCE(SUM(o.net_price),0) spent
            FROM agents a
            LEFT JOIN orders o ON o.tg_id=a.tg_id AND o.status='success'
            GROUP BY a.tg_id
            ORDER BY spent DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
