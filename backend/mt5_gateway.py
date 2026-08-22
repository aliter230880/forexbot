# -*- coding: utf-8 -*-
"""Обёртка над MetaTrader5: подключение, символ, ордера, позиции, счёт."""
import logging
import time

import MetaTrader5 as mt5

from . import config

log = logging.getLogger("gateway")

_initialized = False


def connect(retries: int = 3) -> bool:
    global _initialized
    for attempt in range(1, retries + 1):
        ok = mt5.initialize(
            path=config.MT5_TERMINAL_PATH,
            login=config.MT5_LOGIN,
            password=config.MT5_PASSWORD,
            server=config.MT5_SERVER,
            timeout=config.MT5_TIMEOUT_MS,
        )
        if ok:
            _initialized = True
            log.info("MT5 connected: %s", mt5.account_info().login)
            return True
        log.warning("initialize failed (%s/%s): %s", attempt, retries, mt5.last_error())
        mt5.shutdown()
        time.sleep(5)
    return False


def ensure_symbol() -> dict:
    info = mt5.symbol_info(config.SYMBOL)
    if info is None:
        raise RuntimeError(f"symbol {config.SYMBOL} not found")
    if not info.visible:
        mt5.symbol_select(config.SYMBOL, True)
    return {
        "point": info.point,
        "digits": info.digits,
        "volume_min": info.volume_min,
        "volume_step": info.volume_step,
        "volume_max": info.volume_max,
        "contract_size": info.trade_contract_size,
    }


def get_tick(max_wait_sec: int = 30) -> tuple[float, float]:
    """Живой bid/ask; ждёт валидный тик (после выбора символа бывает 0.0)."""
    deadline = time.time() + max_wait_sec
    while time.time() < deadline:
        t = mt5.symbol_info_tick(config.SYMBOL)
        if t and t.bid > 0 and t.ask > 0:
            return t.bid, t.ask
        time.sleep(1)
    raise RuntimeError(f"no live tick for {config.SYMBOL} in {max_wait_sec}s (рынок закрыт?)")


def account() -> dict | None:
    a = mt5.account_info()
    if a is None:
        return None
    return {
        "login": a.login,
        "balance": a.balance,
        "equity": a.equity,
        "margin": a.margin,
        "margin_free": a.margin_free,
        "margin_level": a.margin_level,  # 0 если нет позиций
        "leverage": a.leverage,
        "currency": a.currency,
    }


def rates(hours: int, timeframe=mt5.TIMEFRAME_H1) -> list:
    bars = max(hours + 2, 3)
    data = mt5.copy_rates_from_pos(config.SYMBOL, timeframe, 1, bars)
    return data if data is not None else []


def place_limit(side: str, price: float, lot: float, comment: str, magic: int,
                position_ticket: int | None = None) -> int | None:
    """BUY_LIMIT ниже рынка / SELL_LIMIT выше.

    position_ticket: для counter-sell — привязка к конкретной позиции (hedging),
    чтобы ордер закрывал её, а не открывал новую короткую.
    """
    order_type = mt5.ORDER_TYPE_BUY_LIMIT if side == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT
    request = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": config.SYMBOL,
        "volume": lot,
        "type": order_type,
        "price": round(price, 2),
        "deviation": 20,
        "magic": magic,
        "comment": comment[:31],
        "type_time": mt5.ORDER_TIME_GTC,
    }
    if position_ticket is not None:
        request["position"] = position_ticket
    for filling in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN):
        request["type_filling"] = filling
        res = mt5.order_send(request)
        if res is None:
            log.error("order_send returned None: %s", mt5.last_error())
            return None
        if res.retcode == mt5.TRADE_RETCODE_DONE:
            return res.order
        if res.retcode != mt5.TRADE_RETCODE_INVALID_FILL:
            log.error("place_limit %s @%.2f failed: retcode=%s (%s)",
                      side, price, res.retcode, res.comment)
            return None
        log.info("filling %s rejected, trying next", filling)
    return None


def cancel(ticket: int) -> bool:
    res = mt5.order_send({
        "action": mt5.TRADE_ACTION_REMOVE,
        "order": ticket,
    })
    return res is not None and res.retcode == mt5.TRADE_RETCODE_DONE


def set_position_tp(ticket: int, tp: float, sl: float = 0.0) -> bool:
    """Серверный TP позиции: закрывается самим торговым сервером, переживает оффлайн."""
    res = mt5.order_send({
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": config.SYMBOL,
        "position": ticket,
        "tp": round(tp, 2),
        "sl": round(sl, 2) if sl else 0.0,
    })
    ok = res is not None and res.retcode == mt5.TRADE_RETCODE_DONE
    if not ok:
        log.error("set_position_tp #%s tp=%.2f failed: %s", ticket, tp,
                  f"{res.retcode} {res.comment}" if res else "None")
    return ok


def close_position(ticket: int, volume: float | None = None) -> bool:
    """Market-закрытие позиции по тикету (частично, если volume задан)."""
    pos = mt5.positions_get(ticket=ticket)
    if not pos:
        return True
    p = pos[0]
    vol = volume or p.volume
    tick = mt5.symbol_info_tick(config.SYMBOL)
    if not tick or tick.bid <= 0:
        log.error("close_position #%s: нет живого тика", ticket)
        return False
    is_buy = p.type == mt5.POSITION_TYPE_BUY
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": config.SYMBOL,
        "volume": vol,
        "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
        "position": ticket,
        "price": tick.bid if is_buy else tick.ask,
        "deviation": 30,
        "magic": p.magic,
        "comment": "guard_close",
        "type_time": mt5.ORDER_TIME_GTC,
    }
    for filling in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN):
        request["type_filling"] = filling
        res = mt5.order_send(request)
        if res is None:
            return False
        if res.retcode == mt5.TRADE_RETCODE_DONE:
            return True
        if res.retcode != mt5.TRADE_RETCODE_INVALID_FILL:
            log.error("close_position #%s failed: %s %s", ticket, res.retcode, res.comment)
            return False
    return False


def open_orders(magic: int | None = None) -> list:
    orders = mt5.orders_get(symbol=config.SYMBOL) or []
    if magic is not None:
        orders = [o for o in orders if o.magic == magic]
    return list(orders)


def positions(magic: int | None = None) -> list:
    pos = mt5.positions_get(symbol=config.SYMBOL) or []
    if magic is not None:
        pos = [p for p in pos if p.magic == magic]
    return list(pos)


def total_volume(magic: int) -> float:
    return round(sum(p.volume for p in positions(magic)), 2)


def shutdown():
    global _initialized
    if _initialized:
        mt5.shutdown()
        _initialized = False
