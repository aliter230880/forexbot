# -*- coding: utf-8 -*-
"""SQLite-хранилище: циклы buy→sell с pair-PnL, состояние бота."""
import json
import sqlite3
from datetime import datetime, timezone

from . import config

MAGIC = 20260819  # magic-число всех ордеров бота


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS pairs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                buy_ticket INTEGER UNIQUE,
                buy_price REAL,
                lot REAL,
                open_time TEXT,
                sell_ticket INTEGER,
                sell_price REAL,
                close_time TEXT,
                pnl REAL,
                status TEXT DEFAULT 'open'   -- open | closed | cancelled
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT, kind TEXT, message TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT,
                equity REAL, balance REAL,
                floating REAL, margin_level REAL,
                positions INTEGER, buy_levels INTEGER,
                trend REAL
            )
        """)
        # миграции
        cols = {r["name"] for r in c.execute("PRAGMA table_info(pairs)")}
        if "costs" not in cols:
            c.execute("ALTER TABLE pairs ADD COLUMN costs REAL DEFAULT 0")
        scols = {r["name"] for r in c.execute("PRAGMA table_info(snapshots)")}
        for col, ddl in (("margin", "REAL DEFAULT 0"), ("margin_free", "REAL DEFAULT 0"),
                         ("grid_low", "REAL"), ("grid_high", "REAL")):
            if col not in scols:
                c.execute(f"ALTER TABLE snapshots ADD COLUMN {col} {ddl}")


def state_load() -> dict:
    try:
        with open(config.STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def state_save(state: dict):
    with open(config.STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def log_event(kind: str, message: str):
    with get_conn() as c:
        c.execute("INSERT INTO events (ts, kind, message) VALUES (?,?,?)",
                  (_now(), kind, message))


# --- пары buy→sell ---

def pair_open(buy_ticket: int, buy_price: float, lot: float):
    with get_conn() as c:
        c.execute("""INSERT OR IGNORE INTO pairs (buy_ticket, buy_price, lot, open_time)
                     VALUES (?,?,?,?)""", (buy_ticket, buy_price, lot, _now()))


def pair_by_buy(buy_ticket: int) -> sqlite3.Row | None:
    with get_conn() as c:
        return c.execute("SELECT * FROM pairs WHERE buy_ticket=?", (buy_ticket,)).fetchone()


def open_pairs() -> list[sqlite3.Row]:
    with get_conn() as c:
        return c.execute("SELECT * FROM pairs WHERE status='open'").fetchall()


def pair_close(buy_ticket: int, sell_ticket: int, sell_price: float, pnl: float,
               costs: float = 0.0):
    with get_conn() as c:
        c.execute("""UPDATE pairs SET sell_ticket=?, sell_price=?, pnl=?, costs=?, close_time=?, status='closed'
                     WHERE buy_ticket=?""", (sell_ticket, sell_price, pnl, costs, _now(), buy_ticket))


def pair_cancel(buy_ticket: int):
    with get_conn() as c:
        c.execute("UPDATE pairs SET status='cancelled' WHERE buy_ticket=? AND status='open'",
                  (buy_ticket,))


def stats() -> dict:
    with get_conn() as c:
        row = c.execute("""SELECT COUNT(*) AS closed, COALESCE(SUM(pnl),0) AS total_pnl,
                                  COALESCE(SUM(costs),0) AS total_costs
                           FROM pairs WHERE status='closed'""").fetchone()
        open_row = c.execute("SELECT COUNT(*) AS n FROM pairs WHERE status='open'").fetchone()
        return {"closed_pairs": row["closed"], "realized_pnl": round(row["total_pnl"], 2),
                "total_costs": round(row["total_costs"], 2), "open_pairs": open_row["n"]}


def snapshot_insert(equity: float, balance: float, floating: float, margin_level: float,
                    positions: int, buy_levels: int, trend: float | None,
                    margin: float = 0.0, margin_free: float = 0.0,
                    grid_low: float | None = None, grid_high: float | None = None):
    with get_conn() as c:
        c.execute("""INSERT INTO snapshots (ts, equity, balance, floating, margin_level,
                     positions, buy_levels, trend, margin, margin_free, grid_low, grid_high)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (_now(), equity, balance, floating, margin_level, positions, buy_levels,
                   trend, margin, margin_free, grid_low, grid_high))


def snapshot_latest() -> sqlite3.Row | None:
    with get_conn() as c:
        return c.execute("SELECT * FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()


def snapshot_history(hours: int = 24) -> list:
    with get_conn() as c:
        rows = c.execute("""SELECT * FROM snapshots ORDER BY id DESC LIMIT ?""",
                         (hours * 120,)).fetchall()  # ~снимок в минуту
        return list(reversed(rows))


def closed_pairs(limit: int = 100) -> list:
    with get_conn() as c:
        return c.execute("""SELECT * FROM pairs WHERE status='closed'
                            ORDER BY close_time DESC LIMIT ?""", (limit,)).fetchall()


def events_recent(limit: int = 30) -> list:
    with get_conn() as c:
        return c.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


if __name__ == "__main__":
    init_db()
    print("DB initialized:", config.DB_PATH)
