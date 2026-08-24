# -*- coding: utf-8 -*-
"""Скальп-движок XAUUSD (PROFILE=SCALP): тренд EMA M1 + вход на откате.

TP $2 / SL $1 (1:2), до 30 сделок/день, активные часы 6-20 UTC,
фильтры спреда/кулдаун/лимит позиций. Каждая сделка = market-ордер
с серверными SL/TP — переживает отключение бота.
"""
import logging
import time
from datetime import datetime, timezone

import MetaTrader5 as mt5
import numpy as np

from . import config, mt5_gateway as gw, storage

log = logging.getLogger("scalp")


def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    k = 2.0 / (period + 1)
    out = np.empty_like(arr)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = arr[i] * k + out[i - 1] * (1 - k)
    return out


def _rsi(closes: np.ndarray, period: int = 14) -> float:
    deltas = np.diff(closes[-(period + 1):])
    gains = np.where(deltas > 0, deltas, 0).mean()
    losses = np.where(deltas < 0, -deltas, 0).mean()
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100 - 100 / (1 + rs)


class ScalpBot:
    def __init__(self):
        self.state = storage.state_load()
        self.state.setdefault("watermark", None)
        self.state.setdefault("halted", False)
        self.state.setdefault("halted_reason", "")
        self.state.setdefault("day_anchor", None)
        self.state.setdefault("scalp_cleaned", False)
        self.state.setdefault("last_trade_ts", 0.0)

    def _save(self):
        storage.state_save(self.state)

    # ---------- сигналы ----------

    def signal(self) -> str | None:
        """LONG / SHORT / None по последней закрытой M1-свече."""
        bars = mt5.copy_rates_from_pos(config.SYMBOL, mt5.TIMEFRAME_M1, 1, 80)
        if bars is None or len(bars) < config.SCALP_EMA_SLOW + 5:
            return None
        closes = np.array([b["close"] for b in bars], dtype=float)
        lows = np.array([b["low"] for b in bars], dtype=float)
        highs = np.array([b["high"] for b in bars], dtype=float)
        ema_f = _ema(closes, config.SCALP_EMA_FAST)
        ema_s = _ema(closes, config.SCALP_EMA_SLOW)
        rsi = _rsi(closes, config.SCALP_RSI_PERIOD)
        i = -1  # последняя закрытая свеча
        # лонг: аптренд, свеча коснулась быстрой EMA и закрылась выше неё
        if (ema_f[i] > ema_s[i] and lows[i] <= ema_f[i] <= closes[i]
                and rsi >= config.SCALP_RSI_LONG_MIN):
            return "LONG"
        # шорт: даунтренд, свеча коснулась EMA сверху и закрылась ниже
        if (ema_f[i] < ema_s[i] and highs[i] >= ema_f[i] >= closes[i]
                and rsi <= config.SCALP_RSI_SHORT_MAX):
            return "SHORT"
        return None

    # ---------- фильтры ----------

    def can_trade(self) -> tuple[bool, str]:
        now = datetime.now(timezone.utc)
        if self.state["halted"]:
            return False, "halted"
        if now.weekday() >= 5:
            return False, "выходные"
        if not (config.SCALP_HOUR_FROM_UTC <= now.hour < config.SCALP_HOUR_TO_UTC):
            return False, "неактивные часы"
        st = storage.scalp_stats()
        if st["trades_today"] >= config.SCALP_MAX_TRADES_DAY:
            return False, "лимит дня"
        if st["open_trades"] >= config.SCALP_MAX_OPEN:
            return False, "лимит позиций"
        if time.time() - self.state["last_trade_ts"] < config.SCALP_COOLDOWN_SEC:
            return False, "кулдаун"
        tick = mt5.symbol_info_tick(config.SYMBOL)
        if not tick or tick.ask <= 0:
            return False, "нет тика"
        if tick.ask - tick.bid > config.SCALP_MAX_SPREAD_USD:
            return False, "спред"
        return True, ""

    # ---------- сделка ----------

    def open_trade(self, side: str) -> int | None:
        tick = mt5.symbol_info_tick(config.SYMBOL)
        is_long = side == "LONG"
        entry = tick.ask if is_long else tick.bid
        sl = round(entry - config.SCALP_SL_USD if is_long else entry + config.SCALP_SL_USD, 2)
        tp = round(entry + config.SCALP_TP_USD if is_long else entry - config.SCALP_TP_USD, 2)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": config.SYMBOL,
            "volume": config.SCALP_LOT,
            "type": mt5.ORDER_TYPE_BUY if is_long else mt5.ORDER_TYPE_SELL,
            "price": entry,
            "sl": sl, "tp": tp,
            "deviation": 20,
            "magic": storage.MAGIC,
            "comment": "scalp",
            "type_time": mt5.ORDER_TIME_GTC,
        }
        for filling in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN):
            request["type_filling"] = filling
            res = mt5.order_send(request)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                storage.scalp_open(res.order, side, res.price, sl, tp)
                self.state["last_trade_ts"] = time.time()
                self._save()
                log.info("SCALP %s @ %.2f sl %.2f tp %.2f", side, res.price, sl, tp)
                storage.log_event("scalp", f"{side} @ {res.price:.2f}")
                from .notifier import send
                send(f"⚡ СКАЛЬП {side} | XAUUSD\n"
                     f"Вход @ {res.price:.2f} (лот {config.SCALP_LOT})\n"
                     f"TP {tp:.2f} (+${config.SCALP_TP_USD:.0f}) · SL {sl:.2f} (-${config.SCALP_SL_USD:.0f})")
                return res.order
            if res and res.retcode != mt5.TRADE_RETCODE_INVALID_FILL:
                log.error("scalp order failed: %s %s", res.retcode, res.comment)
                return None
        return None

    def sync_closed(self):
        """Закрытые сделки (TP/SL сервером) → БД + сигнал в канал."""
        live = {p.ticket for p in gw.positions(storage.MAGIC)}
        for t in storage.scalp_open_trades():
            if t["ticket"] in live:
                continue
            deals = mt5.history_deals_get(position=t["ticket"]) or []
            out = [d for d in deals if d.entry == mt5.DEAL_ENTRY_OUT]
            if not out:
                continue
            d = out[-1]
            reason = "tp" if d.reason == mt5.DEAL_REASON_TP else (
                "sl" if d.reason == mt5.DEAL_REASON_SL else "manual")
            pnl = d.profit + d.commission + d.swap + getattr(d, "fee", 0.0)
            storage.scalp_close(t["ticket"], d.price, round(pnl, 2), reason)
            s = storage.scalp_stats()
            emoji = "✅" if pnl >= 0 else "🛑"
            log.info("SCALP CLOSED %s %s → %.2f pnl %.2f", t["side"], reason, d.price, pnl)
            from .notifier import send
            send(f"{emoji} СКАЛЬП ЗАКРЫТ ({reason.upper()}) | {t['side']}\n"
                 f"Выход @ {d.price:.2f} · PnL {pnl:+.2f}$\n"
                 f"Сессия: {s['closed_trades']} сделок, winrate {s['winrate']:.0f}%, "
                 f"итого {s['realized_pnl']:+.2f}$")

    # ---------- защита ----------

    def guard(self) -> bool:
        acc = gw.account()
        if acc is None:
            return not self.state["halted"]
        now = datetime.now(timezone.utc)
        if self.state["watermark"] is None:
            self.state["watermark"] = acc["equity"]
        self.state["watermark"] = max(self.state["watermark"], acc["equity"])
        today = now.strftime("%Y-%m-%d")
        if self.state["day_anchor"] is None or self.state["day_anchor"][0] != today:
            self.state["day_anchor"] = [today, acc["balance"]]
        day_loss = (acc["equity"] - self.state["day_anchor"][1]) / self.state["day_anchor"][1]
        if day_loss <= -config.GUARD_DAILY_LOSS_PCT and not self.state["halted"]:
            self._halt(f"дневной лимит скальпера: {day_loss*100:.1f}%")
        dd = 1 - acc["equity"] / self.state["watermark"]
        if dd >= config.GUARD_EQUITY_DD_STOP and not self.state["halted"]:
            self._halt(f"просадка equity {dd*100:.1f}%")
        # снимок для дашборда
        self.state.setdefault("last_snap", 0.0)
        if time.time() - self.state["last_snap"] >= 60:
            self.state["last_snap"] = time.time()
            s = storage.scalp_stats()
            pos = gw.positions(storage.MAGIC)
            storage.snapshot_insert(
                equity=acc["equity"], balance=acc["balance"],
                floating=round(sum(p.profit for p in pos), 2),
                margin_level=acc["margin_level"] or 0.0,
                positions=len(pos), buy_levels=0, trend=None,
                margin=acc["margin"], margin_free=acc["margin_free"])
        self._save()
        return not self.state["halted"]

    def _halt(self, reason: str):
        for o in gw.open_orders(storage.MAGIC):
            gw.cancel(o.ticket)
        for p in gw.positions(storage.MAGIC):
            gw.close_position(p.ticket)
        self.state.update(halted=True, halted_reason=reason)
        storage.log_event("halt", reason)
        log.error("HALT: %s", reason)
        from .notifier import send
        send(f"🛑 СТОП (скальп): {reason}\nПозиции закрыты, ордера сняты.")
        self._save()

    def resume(self):
        self.state.update(halted=False, halted_reason="", watermark=None, day_anchor=None)
        self._save()
        from .notifier import send
        send("▶️ Скальпер возобновлён.")

    def shutdown_terminal(self):
        from .grid_engine import GridBot  # общий механизм
        GridBot.shutdown_terminal(self)

    # ---------- цикл ----------

    def run_once(self):
        # одноразовая зачистка сеточных ордеров при переходе на скальпинг
        if not self.state["scalp_cleaned"]:
            for o in gw.open_orders(storage.MAGIC):
                gw.cancel(o.ticket)
            self.state["scalp_cleaned"] = True
            storage.log_event("scalp", "профиль SCALP: сеточные ордера сняты")
            self._save()
        if not self.guard():
            return
        self.sync_closed()
        ok, why = self.can_trade()
        if ok:
            sig = self.signal()
            if sig:
                self.open_trade(sig)
        elif why in ("лимит дня", "выходные", "неактивные часы"):
            log.debug("skip: %s", why)

    def status(self) -> dict:
        acc = gw.account()
        s = storage.scalp_stats()
        pos = gw.positions(storage.MAGIC)
        return {
            "account": acc or {},
            "realized_pnl": s["realized_pnl"],
            "closed_pairs": s["closed_trades"],
            "open_pairs": s["open_trades"],
            "buy_limits": 0,
            "positions": len(pos),
            "floating_pnl": round(sum(p.profit for p in pos), 2),
            "halted": self.state["halted"],
            "halted_reason": self.state["halted_reason"],
            "trend_paused": False,
            "weekend_flat": False,
            "winrate": s["winrate"],
            "trades_today": s["trades_today"],
        }
