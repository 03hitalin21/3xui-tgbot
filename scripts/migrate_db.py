#!/usr/bin/env python3
import os
import sqlite3
import sys
from pathlib import Path


def main() -> int:
    target_dir = Path(__file__).resolve().parents[1]
    db_path = Path(sys.argv[1] if len(sys.argv) > 1 else os.getenv("BOT_DB_PATH", str(target_dir / "data" / "bot.db")))
    migrations_dir = target_dir / "migrations"
    migrations_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        files = sorted(p for p in migrations_dir.glob("*.sql") if p.is_file())
        pending = []
        for p in files:
            try:
                idx = int(p.stem.split("_", 1)[0])
            except Exception:
                continue
            if idx > current:
                pending.append((idx, p))

        for idx, path in pending:
            sql = path.read_text(encoding="utf-8")
            if sql.strip():
                conn.executescript(sql)
            conn.execute(f"PRAGMA user_version={idx}")
            conn.commit()
            print(f"[INFO] applied migration {path.name}")

        if not pending:
            print("[INFO] no pending migrations")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
