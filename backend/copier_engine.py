# -*- coding: utf-8 -*-
"""Движок копитрейдинга.

Схема:
  мастера (гибрид, V3, скальпер, мульти, сетка) пишут сделки в общую bot.db;
  копир — отдельный процесс, подключённый ко ВТОРОМУ терминалу MT5 (счёт
  подписчика). Читает новые открытые сделки выбранных мастеров и зеркалит:

    1. лот: баланс_подписчика / баланс_мастера × лот_мастера
       → приводится к минимальному лоту и шагу символа, ограничивается MAX_LOT
    2. вход рыночный; SL/TP берутся от сделки мастера (в цене) и переносятся
       как есть — уровни абсолютные, они одинаково валидны на любом счёте
    3. когда сделка мастера закрылась (tp/sl/manual) — копир закрывает
       зеркалку по рынку,.pnl обеих сторон пишется в copy_trades

Режимы: DRY_RUN=1 (по умолчанию) — только логи и БД, ордера не отправляются.
"""
import json
import logging
import time
from datetime import datetime, timezone

import MetaTrader5 as mt5

from . import config, storage

log = logging.getLogger("copier")


# ---------- состояние ----------

def _state_path():
    return config.DATA_DIR / config.COPIER_STATE_FILE


def state_load() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"last_master_id": 0, "started": datetime.now(timezone.utc).isoformat()}


def state_save(st: dict):
    _state_path().write_text(json.dumps(st, indent=2, ensure_ascii=False),
                             encoding="utf-8")


# ---------- подключение к счёту подписчика ----------

def connect() -> bool:
    kwargs = {"path": config.COPIER_MT5_PATH, "timeout": 60000}
    if config.COPIER_LOGIN:
        kwargs.update(login=config.COPIER_LOGIN,
                      password=config.COPIER_PASSWORD,
                      server=config.COPIER_SERVER)
    ok = mt5.initialize(**kwargs)
    if not ok:
        log.error("копир: MT5 initialize failed: %s", mt5.last_error())
        return False
    acc = mt5.account_info()
    log.info("копир подключён: счёт %s, баланс %.2f %s (dry_run=%s)",
             acc.login, acc.balance, acc.currency, config.COPIER_DRY_RUN)
    return True


# ---------- источник сделок мастеров ----------

def fetch_new_master_trades(last_id: int) -> list:
    """Новые ОТКРЫТЫЕ сделки выбранных мастеров (scalp_trades.id > last_id)."""
    versions = [v.strip() for v in config.COPIER_MASTERS if v.strip()]
    if not versions:
        return []
    q = ", ".join("?" * len(versions))
    with storage.get_conn() as c:
        return c.execute(
            f"""SELECT id, ticket, version, symbol, side, entry, sl, tp, open_time
                FROM scalp_trades
                WHERE id > ? AND status='open' AND version IN ({q})
                ORDER BY id ASC""",
            (last_id, *versions)).fetchall()


def fetch_closed_master_trades() -> list:
    """Закрытые сделки мастеров, у которых зеркалка ещё открыта."""
    with storage.get_conn() as c:
        return c.execute(
            """SELECT ct.id AS copy_id, ct.copy_ticket, ct.symbol, ct.master_ticket,
                      st.pnl AS master_pnl, st.exit
               FROM copy_trades ct
               JOIN scalp_trades st ON st.ticket = ct.master_ticket
               WHERE ct.status='open' AND st.status='closed'"""
        ).fetchall()


# ---------- расчёт лота ----------

def scale_lot(master_lot: float, master_balance: float,
              follower_balance: float, symbol: str) -> float:
    """Лот подписчика: пропорция балансов, нормализация под шаг символа."""
    if config.COPIER_FIXED_LOT > 0:
        lot = config.COPIER_FIXED_LOT
    else:
        if master_balance <= 0:
            return 0.0
        lot = master_lot * follower_balance / master_balance
    info = mt5.symbol_info(symbol)
    if info is None:
        return 0.0
    lot = max(lot, info.volume_min)
    step = info.volume_step or 0.01
    lot = round(int(lot / step) * step, 2)
    lot = min(lot, config.COPIER_MAX_LOT, info.volume_max)
    return lot


# ---------- зеркалирование ----------

def mirror_open(master: dict, follower_balance: float) -> int | None:
    """Открыть зеркальную сделку. Возвращает тикет копии или None."""
    symbol = master["symbol"]
    side = master["side"]
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if info is None or not tick or tick.ask <= 0:
        log.warning("копир: символ %s недоступен на счёте подписчика", symbol)
        return None
    lot = scale_lot(0.01, config.COPIER_MASTER_BALANCE, follower_balance, symbol)
    if lot <= 0:
        return None
    is_long = side == "LONG"
    entry = tick.ask if is_long else tick.bid
    digits = info.digits
    sl = master["sl"] if master["sl"] else 0.0
    tp = master["tp"] if master["tp"] else 0.0
    # стопы не ближе минимальной дистанции брокера
    min_dist = info.trade_stops_level * info.point + (tick.ask - tick.bid) * 1.5
    if sl and abs(entry - sl) < min_dist:
        sl = round(entry - min_dist if is_long else entry + min_dist, digits)
    if tp and abs(tp - entry) < min_dist:
        tp = round(entry + min_dist if is_long else entry - min_dist, digits)
    if config.COPIER_DRY_RUN:
        log.info("копир [DRY] открыл бы %s %s лот %.2f @ %.*f sl %s tp %s "
                 "(мастер #%s %s)",
                 side, symbol, lot, digits, entry, sl, tp,
                 master["ticket"], master["version"])
        return -1   # виртуальный тикет
    req = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol,
        "volume": lot,
        "type": mt5.ORDER_TYPE_BUY if is_long else mt5.ORDER_TYPE_SELL,
        "price": entry, "sl": round(sl, digits) if sl else 0.0,
        "tp": round(tp, digits) if tp else 0.0,
        "deviation": 30, "magic": config.COPIER_MAGIC,
        "comment": f"copy_{master['version'][:6]}",
        "type_time": mt5.ORDER_TIME_GTC,
    }
    for filling in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK,
                    mt5.ORDER_FILLING_RETURN):
        req["type_filling"] = filling
        res = mt5.order_send(req)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            log.info("копир: %s %s лот %.2f @ %.*f (мастер #%s)",
                     side, symbol, lot, digits, res.price, master["ticket"])
            return res.order
        if res and res.retcode != mt5.TRADE_RETCODE_INVALID_FILL:
            log.error("копир order failed: %s %s", res.retcode, res.comment)
            return None
    return None


def mirror_close(copy_id: int, copy_ticket: int, symbol: str,
                 master_pnl: float | None):
    """Закрыть зеркалку, когда мастер закрылся."""
    if config.COPIER_DRY_RUN or copy_ticket <= 0:
        log.info("копир [DRY] закрыл бы #%s %s (мастер pnl %s)",
                 copy_ticket, symbol, master_pnl)
        _close_db(copy_id, master_pnl, copy_pnl=None)
        return
    pos = mt5.positions_get(ticket=copy_ticket)
    if not pos:
        _close_db(copy_id, master_pnl, copy_pnl=0.0)   # уже закрыта сервером
        return
    p = pos[0]
    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        return
    is_long = p.type == mt5.POSITION_TYPE_BUY
    res = mt5.order_send({
        "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol,
        "volume": p.volume, "position": copy_ticket,
        "type": mt5.ORDER_TYPE_SELL if is_long else mt5.ORDER_TYPE_BUY,
        "price": tick.bid if is_long else tick.ask,
        "deviation": 30, "magic": config.COPIER_MAGIC,
        "type_filling": mt5.ORDER_FILLING_IOC})
    copy_pnl = None
    if res and res.retcode == mt5.TRADE_RETCODE_DONE:
        deals = mt5.history_deals_get(position=copy_ticket) or []
        out = [d for d in deals if d.entry == mt5.DEAL_ENTRY_OUT]
        if out:
            d = out[-1]
            copy_pnl = round(d.profit + d.commission + d.swap
                             + getattr(d, "fee", 0.0), 2)
    else:
        log.error("копир close #%s failed: %s", copy_ticket,
                  res.retcode if res else "None")
    _close_db(copy_id, master_pnl, copy_pnl)


def _close_db(copy_id: int, master_pnl, copy_pnl):
    with storage.get_conn() as c:
        c.execute("""UPDATE copy_trades SET close_time=?, master_pnl=?, copy_pnl=?,
                     status='closed' WHERE id=?""",
                  (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                   master_pnl, copy_pnl, copy_id))


def record_copy(master: dict, copy_ticket: int, lot: float, entry: float):
    with storage.get_conn() as c:
        c.execute("""INSERT INTO copy_trades
                     (master_ticket, master_version, symbol, side, master_entry,
                      copy_ticket, copy_entry, copy_lot, master_sl, master_tp, open_time)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                  (master["ticket"], master["version"], master["symbol"],
                   master["side"], master["entry"], copy_ticket, entry, lot,
                   master["sl"], master["tp"],
                   datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")))


# ---------- пропуск невалидных ----------

def already_copied(master_ticket: int) -> bool:
    with storage.get_conn() as c:
        r = c.execute("SELECT COUNT(*) n FROM copy_trades WHERE master_ticket=?",
                      (master_ticket,)).fetchone()
        return r["n"] > 0


def open_copy_count() -> int:
    with storage.get_conn() as c:
        return c.execute(
            "SELECT COUNT(*) n FROM copy_trades WHERE status='open'").fetchone()["n"]


# ---------- один цикл ----------

def run_once(st: dict) -> dict:
    """Цикл копира: новые сделки мастеров → зеркалим; закрытия → закрываем."""
    acc = mt5.account_info()
    balance = acc.balance if acc else config.COPIER_MASTER_BALANCE
    copied = 0

    # 1) новые сделки мастеров
    rows = fetch_new_master_trades(st.get("last_master_id", 0))
    for r in rows:
        st["last_master_id"] = max(st["last_master_id"], r["id"])
        # свежесть: копируем только недавние (иначе на старте скопирует историю)
        try:
            opened = datetime.strptime(r["open_time"], "%Y-%m-%d %H:%M:%S")
            age = (datetime.utcnow() - opened).total_seconds()
        except (ValueError, TypeError):
            age = 10**9
        if age > config.COPIER_MAX_LATENCY_SEC:
            continue
        if already_copied(r["ticket"]):
            continue
        if open_copy_count() >= config.COPIER_MAX_POS:
            log.warning("копир: лимит зеркальных позиций — сделка #%s пропущена",
                        r["ticket"])
            continue
        ticket = mirror_open(r, balance)
        if ticket is not None:
            record_copy(r, ticket, lot=config.COPIER_FIXED_LOT or 0.01,
                        entry=0.0 if ticket < 0 else float(mt5.symbol_info_tick(
                            r["symbol"]).bid))
            copied += 1
    if rows:
        state_save(st)

    # 2) мастера закрылись → закрываем зеркалки
    for cr in fetch_closed_master_trades():
        mirror_close(cr["copy_id"], cr["copy_ticket"], cr["symbol"],
                     cr["master_pnl"])

    # 3) dry-run: позиции с виртуальным тикетом -1, у которых мастер закрылся,
    #    закрываются выше через fetch_closed (copy_ticket=-1 ≤ 0 → только лог)
    return {"new_master": len(rows), "copied": copied,
            "open_copies": open_copy_count(), "balance": balance}
