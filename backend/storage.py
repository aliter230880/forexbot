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
        c.execute("""
            CREATE TABLE IF NOT EXISTS scalp_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket INTEGER UNIQUE,
                side TEXT,                -- LONG | SHORT
                entry REAL, sl REAL, tp REAL,
                open_time TEXT,
                exit REAL, close_time TEXT,
                pnl REAL, reason TEXT,    -- tp | sl | manual
                status TEXT DEFAULT 'open'
            )
        """)
        # аналитика входов: контекст рынка на момент сделки
        tcols = {r["name"] for r in c.execute("PRAGMA table_info(scalp_trades)")}
        for col, ddl in (("adx", "REAL"), ("atr", "REAL"), ("h1_trend", "TEXT"),
                         ("hour_utc", "INTEGER"), ("spread", "REAL"),
                         ("ema_gap", "REAL"), ("max_favor", "REAL"), ("version", "TEXT"),
                         ("symbol", "TEXT")):
            if col not in tcols:
                c.execute(f"ALTER TABLE scalp_trades ADD COLUMN {col} {ddl}")


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


# --- скальперские сделки ---

def scalp_open(ticket: int, side: str, entry: float, sl: float, tp: float,
               ctx: dict | None = None):
    """ctx — контекст входа для аналитики: adx, atr, h1_trend, spread, ema_gap, version, symbol."""
    ctx = ctx or {}
    with get_conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO scalp_trades "
            "(ticket, side, entry, sl, tp, open_time, adx, atr, h1_trend, hour_utc, "
            " spread, ema_gap, version, symbol) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ticket, side, entry, sl, tp, _now(), ctx.get("adx"), ctx.get("atr"),
             ctx.get("h1_trend"), ctx.get("hour_utc"), ctx.get("spread"),
             ctx.get("ema_gap"), ctx.get("version", "v2"), ctx.get("symbol")))


def scalp_update_sl(ticket: int, sl: float, max_favor: float | None = None):
    with get_conn() as c:
        if max_favor is None:
            c.execute("UPDATE scalp_trades SET sl=? WHERE ticket=?", (sl, ticket))
        else:
            c.execute("UPDATE scalp_trades SET sl=?, max_favor=? WHERE ticket=?",
                      (sl, max_favor, ticket))


def scalp_close(ticket: int, exit_price: float, pnl: float, reason: str):
    with get_conn() as c:
        c.execute("""UPDATE scalp_trades SET exit=?, pnl=?, reason=?, close_time=?, status='closed'
                     WHERE ticket=?""", (exit_price, pnl, reason, _now(), ticket))


def scalp_open_trades() -> list:
    """Только одиночный скальпер (мульти-бот живёт под version='multi')."""
    with get_conn() as c:
        return c.execute("SELECT * FROM scalp_trades WHERE status='open' "
                         "AND COALESCE(version,'') NOT IN ('multi','hybrid','kiro')").fetchall()


def scalp_closed(limit: int = 100) -> list:
    with get_conn() as c:
        return c.execute("SELECT * FROM scalp_trades WHERE status='closed' "
                         "AND COALESCE(version,'') NOT IN ('multi','hybrid','kiro') "
                         "ORDER BY close_time DESC LIMIT ?", (limit,)).fetchall()


def multi_stats() -> dict:
    """Статистика мульти-бота (version='multi'), с разбивкой по символам."""
    with get_conn() as c:
        r = c.execute("""SELECT COUNT(*) n, COALESCE(SUM(pnl),0) pnl,
                           SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) wins
                         FROM scalp_trades WHERE status='closed' AND version='multi'""").fetchone()
        o = c.execute("SELECT COUNT(*) n FROM scalp_trades "
                      "WHERE status='open' AND version='multi'").fetchone()
        today = _now()[:10]
        t = c.execute("SELECT COUNT(*) n FROM scalp_trades "
                      "WHERE version='multi' AND open_time LIKE ?", (today + "%",)).fetchone()
        per = c.execute("""SELECT symbol, COUNT(*) n, COALESCE(SUM(pnl),0) pnl,
                             SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) wins
                           FROM scalp_trades WHERE status='closed' AND version='multi'
                           GROUP BY symbol ORDER BY pnl DESC""").fetchall()
        n, wins = r["n"], r["wins"] or 0
        return {
            "closed_trades": n, "realized_pnl": round(r["pnl"], 2),
            "open_trades": o["n"], "trades_today": t["n"],
            "winrate": round(100 * wins / n, 1) if n else 0.0,
            "per_symbol": [
                {"symbol": p["symbol"], "trades": p["n"], "pnl": round(p["pnl"], 2),
                 "winrate": round(100 * (p["wins"] or 0) / p["n"], 1) if p["n"] else 0.0}
                for p in per],
        }


def kiro_stats() -> dict:
    """Статистика kiro-бота (version='kiro', ML-скальпер)."""
    with get_conn() as c:
        r = c.execute("""SELECT COUNT(*) n, COALESCE(SUM(pnl),0) pnl,
                           SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) wins
                         FROM scalp_trades WHERE status='closed' AND version='kiro'""").fetchone()
        o = c.execute("SELECT COUNT(*) n FROM scalp_trades "
                      "WHERE status='open' AND version='kiro'").fetchone()
        today = _now()[:10]
        t = c.execute("SELECT COUNT(*) n FROM scalp_trades "
                      "WHERE version='kiro' AND open_time LIKE ?", (today + "%",)).fetchone()
        n, wins = r["n"], r["wins"] or 0
        return {
            "closed_trades": n, "realized_pnl": round(r["pnl"], 2),
            "open_trades": o["n"], "trades_today": t["n"],
            "winrate": round(100 * wins / n, 1) if n else 0.0,
        }


def kiro_open_trades() -> list:
    with get_conn() as c:
        return c.execute("SELECT * FROM scalp_trades "
                         "WHERE status='open' AND version='kiro'").fetchall()


def kiro_closed(limit: int = 50) -> list:
    with get_conn() as c:
        return c.execute("SELECT * FROM scalp_trades WHERE status='closed' AND version='kiro' "
                         "ORDER BY close_time DESC LIMIT ?", (limit,)).fetchall()


def hybrid_stats() -> dict:
    """Статистика гибрида (version='hybrid') с разбивкой по символам."""
    with get_conn() as c:
        r = c.execute("""SELECT COUNT(*) n, COALESCE(SUM(pnl),0) pnl,
                           SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) wins
                         FROM scalp_trades WHERE status='closed' AND version='hybrid'""").fetchone()
        o = c.execute("SELECT COUNT(*) n FROM scalp_trades "
                      "WHERE status='open' AND version='hybrid'").fetchone()
        today = _now()[:10]
        t = c.execute("SELECT COUNT(*) n FROM scalp_trades "
                      "WHERE version='hybrid' AND open_time LIKE ?", (today + "%",)).fetchone()
        tp = c.execute("""SELECT COALESCE(SUM(pnl),0) p FROM scalp_trades
                          WHERE version='hybrid' AND status='closed'
                          AND close_time LIKE ?""", (today + "%",)).fetchone()
        per = c.execute("""SELECT symbol, COUNT(*) n, COALESCE(SUM(pnl),0) pnl,
                             SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) wins
                           FROM scalp_trades WHERE status='closed' AND version='hybrid'
                           GROUP BY symbol ORDER BY pnl DESC""").fetchall()
        n, wins = r["n"], r["wins"] or 0
        return {
            "closed_trades": n, "realized_pnl": round(r["pnl"], 2),
            "open_trades": o["n"], "trades_today": t["n"],
            "pnl_today": round(tp["p"], 2),
            "winrate": round(100 * wins / n, 1) if n else 0.0,
            "per_symbol": [
                {"symbol": p["symbol"], "trades": p["n"], "pnl": round(p["pnl"], 2),
                 "winrate": round(100 * (p["wins"] or 0) / p["n"], 1) if p["n"] else 0.0}
                for p in per],
        }


def hybrid_open_trades() -> list:
    with get_conn() as c:
        return c.execute("SELECT * FROM scalp_trades "
                         "WHERE status='open' AND version='hybrid'").fetchall()


def hybrid_closed(limit: int = 50) -> list:
    with get_conn() as c:
        return c.execute("SELECT * FROM scalp_trades WHERE status='closed' AND version='hybrid' "
                         "ORDER BY close_time DESC LIMIT ?", (limit,)).fetchall()


def multi_open_trades() -> list:
    with get_conn() as c:
        return c.execute("SELECT * FROM scalp_trades "
                         "WHERE status='open' AND version='multi'").fetchall()


def multi_closed(limit: int = 50) -> list:
    with get_conn() as c:
        return c.execute("SELECT * FROM scalp_trades WHERE status='closed' AND version='multi' "
                         "ORDER BY close_time DESC LIMIT ?", (limit,)).fetchall()


def multi_trades_today_symbol(symbol: str) -> int:
    with get_conn() as c:
        today = _now()[:10]
        r = c.execute("SELECT COUNT(*) n FROM scalp_trades WHERE version='multi' "
                      "AND symbol=? AND open_time LIKE ?", (symbol, today + "%")).fetchone()
        return r["n"]


def scalp_stats() -> dict:
    """Статистика одиночного скальпера (без мульти-бота)."""
    with get_conn() as c:
        r = c.execute("""SELECT COUNT(*) n, COALESCE(SUM(pnl),0) pnl,
                           SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) wins
                           FROM scalp_trades WHERE status='closed'
                           AND COALESCE(version,'') NOT IN ('multi','hybrid','kiro')""").fetchone()
        o = c.execute("SELECT COUNT(*) n FROM scalp_trades WHERE status='open' "
                      "AND COALESCE(version,'') NOT IN ('multi','hybrid','kiro')").fetchone()
        today = _now()[:10]
        t = c.execute("SELECT COUNT(*) n FROM scalp_trades WHERE open_time LIKE ? "
                      "AND COALESCE(version,'') NOT IN ('multi','hybrid','kiro')", (today + "%",)).fetchone()
        n, wins = r["n"], r["wins"] or 0
        return {"closed_trades": n, "realized_pnl": round(r["pnl"], 2),
                "open_trades": o["n"], "trades_today": t["n"],
                "winrate": round(100 * wins / n, 1) if n else 0.0}


if __name__ == "__main__":
    init_db()
    print("DB initialized:", config.DB_PATH)
