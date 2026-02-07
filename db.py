import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Dict, List, Optional

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


def _ensure_column(conn: sqlite3.Connection, table: str, col: str, ddl: str) -> None:
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def now_ts() -> int:
    return int(time.time())


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS agents (
                tg_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                role TEXT NOT NULL DEFAULT 'reseller',
                is_active INTEGER NOT NULL DEFAULT 1,
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

            CREATE TABLE IF NOT EXISTS wallet_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                reason TEXT NOT NULL,
                meta TEXT,
                created_at INTEGER NOT NULL
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

            CREATE TABLE IF NOT EXISTS created_clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER NOT NULL,
                inbound_id INTEGER NOT NULL,
                email TEXT NOT NULL,
                uuid TEXT NOT NULL,
                vless_link TEXT NOT NULL,
                days INTEGER NOT NULL,
                gb INTEGER NOT NULL,
                start_after_first_use INTEGER NOT NULL DEFAULT 0,
                auto_renew INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS inbound_pricing (
                inbound_id INTEGER PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1,
                price_per_gb REAL,
                price_per_day REAL,
                updated_at INTEGER NOT NULL
            );
            """
        )

        # Migrations for older DBs
        _ensure_column(conn, "agents", "role", "role TEXT NOT NULL DEFAULT 'reseller'")
        _ensure_column(conn, "agents", "is_active", "is_active INTEGER NOT NULL DEFAULT 1")
        _ensure_column(conn, "created_clients", "auto_renew", "auto_renew INTEGER NOT NULL DEFAULT 0")

        defaults = {
            "price_per_gb": "0.15",
            "price_per_day": "0.10",
            "support_text": "Contact admin for support.",
        }
        for k, v in defaults.items():
            conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?,?)", (k, v))


def get_setting_float(key: str) -> float:
    with get_conn() as conn:
        r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return float(r["value"])


def get_setting_text(key: str) -> str:
    with get_conn() as conn:
        r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else ""


def set_setting(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def ensure_agent(tg_id: int, username: str = "", full_name: str = "", role: str = "reseller") -> None:
    ts = now_ts()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO agents(tg_id, username, full_name, role, created_at, updated_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(tg_id) DO UPDATE SET
                username=excluded.username,
                full_name=excluded.full_name,
                updated_at=excluded.updated_at
            """,
            (tg_id, username, full_name, role, ts, ts),
        )


def get_agent(tg_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM agents WHERE tg_id=?", (tg_id,)).fetchone()


def list_resellers(limit: int = 200) -> List[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM agents WHERE role='reseller' ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()


def set_agent_active(tg_id: int, active: bool) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE agents SET is_active=?, updated_at=? WHERE tg_id=?", (1 if active else 0, now_ts(), tg_id))


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
        return float(row["balance"] if row else 0)


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
        n = conn.execute("SELECT balance FROM agents WHERE tg_id=?", (tg_id,)).fetchone()
        return float(n["balance"] if n else 0)


def list_transactions(tg_id: int, limit: int = 20) -> List[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM wallet_ledger WHERE tg_id=? ORDER BY id DESC LIMIT ?",
            (tg_id, limit),
        ).fetchall()


def create_order(
    tg_id: int,
    inbound_id: int,
    kind: str,
    days: int,
    gb: int,
    count: int,
    gross: float,
    disc: float,
    net: float,
    status: str,
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO orders(tg_id,inbound_id,kind,days,gb,count,gross_price,discount_percent,net_price,status,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (tg_id, inbound_id, kind, days, gb, count, gross, disc, net, status, now_ts()),
        )
        return int(cur.lastrowid)


def save_created_client(
    tg_id: int,
    inbound_id: int,
    email: str,
    uuid_: str,
    link: str,
    days: int,
    gb: int,
    start_after_first_use: bool,
    auto_renew: bool,
):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO created_clients(tg_id,inbound_id,email,uuid,vless_link,days,gb,start_after_first_use,auto_renew,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (tg_id, inbound_id, email, uuid_, link, days, gb, 1 if start_after_first_use else 0, 1 if auto_renew else 0, now_ts()),
        )


def list_clients(tg_id: int, limit: int = 30) -> List[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM created_clients WHERE tg_id=? ORDER BY id DESC LIMIT ?", (tg_id, limit)).fetchall()


def agent_stats(tg_id: int) -> Dict[str, float]:
    with get_conn() as conn:
        orders = conn.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(count),0) clients, COALESCE(SUM(net_price),0) spent FROM orders WHERE tg_id=? AND status='success'",
            (tg_id,),
        ).fetchone()
        today = conn.execute(
            "SELECT COALESCE(SUM(net_price),0) s FROM orders WHERE tg_id=? AND status='success' AND created_at >= ?",
            (tg_id, now_ts() - 86400),
        ).fetchone()
        ag = conn.execute("SELECT balance, lifetime_topup FROM agents WHERE tg_id=?", (tg_id,)).fetchone()
    return {
        "orders": int(orders["c"] if orders else 0),
        "clients": int(orders["clients"] if orders else 0),
        "spent": float(orders["spent"] if orders else 0),
        "today_sales": float(today["s"] if today else 0),
        "balance": float(ag["balance"] if ag else 0),
        "lifetime_topup": float(ag["lifetime_topup"] if ag else 0),
    }


def set_inbound_rule(inbound_id: int, enabled: bool, price_per_gb: Optional[float], price_per_day: Optional[float]) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO inbound_pricing(inbound_id,enabled,price_per_gb,price_per_day,updated_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(inbound_id) DO UPDATE SET
              enabled=excluded.enabled,
              price_per_gb=excluded.price_per_gb,
              price_per_day=excluded.price_per_day,
              updated_at=excluded.updated_at
            """,
            (inbound_id, 1 if enabled else 0, price_per_gb, price_per_day, now_ts()),
        )


def inbound_rule(inbound_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM inbound_pricing WHERE inbound_id=?", (inbound_id,)).fetchone()


def create_promo(code: str, discount_percent: float, max_uses: Optional[int]) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO promo_codes(code, discount_percent, max_uses, created_at) VALUES(?,?,?,?)",
            (code.upper(), discount_percent, max_uses, now_ts()),
        )


def apply_promo(code: str, tg_id: int) -> float:
    c = code.upper()
    with get_conn() as conn:
        p = conn.execute("SELECT * FROM promo_codes WHERE code=? AND active=1", (c,)).fetchone()
        if not p:
            raise ValueError("Promo code not found or inactive")
        if p["max_uses"] is not None and p["used_count"] >= p["max_uses"]:
            raise ValueError("Promo code usage limit reached")
        ex = conn.execute("SELECT 1 FROM promo_redemptions WHERE code=? AND tg_id=?", (c, tg_id)).fetchone()
        if ex:
            raise ValueError("Promo code already used")
        conn.execute("INSERT INTO promo_redemptions(code, tg_id, redeemed_at) VALUES(?,?,?)", (c, tg_id, now_ts()))
        conn.execute("UPDATE promo_codes SET used_count=used_count+1 WHERE code=?", (c,))
        return float(p["discount_percent"])


def list_promos() -> List[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM promo_codes ORDER BY created_at DESC").fetchall()


def top_agents(limit: int = 100) -> List[sqlite3.Row]:
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


def list_promos() -> List[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM promo_codes ORDER BY created_at DESC").fetchall()


def top_agents(limit: int = 100) -> List[sqlite3.Row]:
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
