# -*- coding: utf-8 -*-
"""Скальп-движок XAUUSD v2 (PROFILE=SCALP).

Отличия от провального v1 (59 сделок, WR 30.5%, -$28):
  1. Только по тренду H1 (шорты в аптренде отключены — давали -$22)
  2. ADX-фильтр силы тренда: во флэте не торгуем (там откат = разворот)
  3. Сигнал на M5 вместо M1 (меньше шума, спред меньше влияет)
  4. SL/TP по ATR, не фиксированные; риск-профиль 1:2
  5. Трейлинг-стоп: безубыток при +$1.5, далее тянем за ценой
  6. Стоп-серия: 3 стопа подряд → пауза до следующего дня
  7. Полный контекст входа пишется в БД для аналитики
"""
import logging
import time
from datetime import datetime, timezone

import MetaTrader5 as mt5
import numpy as np

from . import config, mt5_gateway as gw, storage

log = logging.getLogger("scalp")
VERSION = "v2"


def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    k = 2.0 / (period + 1)
    out = np.empty_like(arr)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = arr[i] * k + out[i - 1] * (1 - k)
    return out


def _rsi(closes: np.ndarray, period: int = 14) -> float:
    d = np.diff(closes[-(period + 1):])
    gains = np.where(d > 0, d, 0).mean()
    losses = np.where(d < 0, -d, 0).mean()
    return 100.0 if losses == 0 else 100 - 100 / (1 + gains / losses)


def _atr(highs, lows, closes, period: int = 14) -> float:
    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
           for i in range(1, len(highs))]
    return float(np.mean(trs[-period:])) if len(trs) >= period else 0.0


def _adx(highs, lows, closes, period: int = 14) -> float:
    """Классический ADX: сила тренда независимо от направления."""
    if len(highs) < period * 2 + 2:
        return 0.0
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, len(highs)):
        up, down = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
    n = period
    atr = np.mean(trs[-n:])
    if atr == 0:
        return 0.0
    pdi = 100 * np.mean(plus_dm[-n:]) / atr
    mdi = 100 * np.mean(minus_dm[-n:]) / atr
    return 0.0 if pdi + mdi == 0 else 100 * abs(pdi - mdi) / (pdi + mdi)


class ScalpBot:
    def __init__(self):
        self.state = storage.state_load()
        self.state.setdefault("watermark", None)
        self.state.setdefault("halted", False)
        self.state.setdefault("halted_reason", "")
        self.state.setdefault("day_anchor", None)
        self.state.setdefault("scalp_cleaned", False)
        self.state.setdefault("last_trade_ts", 0.0)
        self.state.setdefault("scalp_signals", False)
        self.state.setdefault("loss_streak", 0)
        self.state.setdefault("streak_pause_day", "")

    def _save(self):
        storage.state_save(self.state)

    # ---------- рыночный контекст ----------

    def h1_trend(self) -> str:
        """UP / DOWN / FLAT по H1: цена относительно EMA50 + наклон."""
        bars = mt5.copy_rates_from_pos(config.SYMBOL, mt5.TIMEFRAME_H1, 1, 60)
        if bars is None or len(bars) < config.SCALP_H1_EMA + 5:
            return "FLAT"
        closes = np.array([b["close"] for b in bars], dtype=float)
        ema = _ema(closes, config.SCALP_H1_EMA)
        price, e_now, e_prev = closes[-1], ema[-1], ema[-5]
        if price > e_now and e_now > e_prev:
            return "UP"
        if price < e_now and e_now < e_prev:
            return "DOWN"
        return "FLAT"

    def m5_context(self) -> dict | None:
        bars = mt5.copy_rates_from_pos(config.SYMBOL, mt5.TIMEFRAME_M5, 1, 120)
        if bars is None or len(bars) < 60:
            return None
        closes = np.array([b["close"] for b in bars], dtype=float)
        highs = np.array([b["high"] for b in bars], dtype=float)
        lows = np.array([b["low"] for b in bars], dtype=float)
        ema_f = _ema(closes, config.SCALP_EMA_FAST)
        ema_s = _ema(closes, config.SCALP_EMA_SLOW)
        return {
            "close": closes[-1], "high": highs[-1], "low": lows[-1],
            "ema_f": ema_f[-1], "ema_s": ema_s[-1],
            "ema_gap": round(float(ema_f[-1] - ema_s[-1]), 2),
            "rsi": _rsi(closes, config.SCALP_RSI_PERIOD),
            "atr": _atr(highs, lows, closes, 14),
            "adx": _adx(highs, lows, closes, config.SCALP_ADX_PERIOD),
        }

    # ---------- сигнал ----------

    def signal(self) -> tuple[str | None, dict]:
        """Вход только по направлению тренда H1 при сильном ADX."""
        trend = self.h1_trend()
        ctx = self.m5_context()
        if ctx is None:
            return None, {"reason": "нет данных M5"}
        ctx["h1_trend"] = trend
        if trend == "FLAT":
            return None, {**ctx, "skip": "H1 флэт"}
        if ctx["adx"] < config.SCALP_ADX_MIN:
            return None, {**ctx, "skip": f"ADX {ctx['adx']:.0f} < {config.SCALP_ADX_MIN:.0f}"}

        # LONG: аптренд H1, EMA9>EMA21 на M5, откат к EMA9, RSI в рабочей зоне
        if (trend == "UP" and ctx["ema_f"] > ctx["ema_s"]
                and ctx["low"] <= ctx["ema_f"] <= ctx["close"]
                and config.SCALP_RSI_LONG_MIN <= ctx["rsi"] <= config.SCALP_RSI_LONG_MAX):
            return "LONG", ctx
        # SHORT: только в даунтренде H1
        if (trend == "DOWN" and ctx["ema_f"] < ctx["ema_s"]
                and ctx["high"] >= ctx["ema_f"] >= ctx["close"]
                and config.SCALP_RSI_SHORT_MIN <= ctx["rsi"] <= config.SCALP_RSI_SHORT_MAX):
            return "SHORT", ctx
        return None, {**ctx, "skip": "нет сетапа"}

    # ---------- фильтры ----------

    def can_trade(self) -> tuple[bool, str]:
        now = datetime.now(timezone.utc)
        if self.state["halted"]:
            return False, "halted"
        if now.weekday() >= 5:
            return False, "выходные"
        if not (config.SCALP_HOUR_FROM_UTC <= now.hour < config.SCALP_HOUR_TO_UTC):
            return False, "неактивные часы"
        today = now.strftime("%Y-%m-%d")
        if self.state["streak_pause_day"] == today:
            return False, "пауза после серии стопов"
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

    def open_trade(self, side: str, ctx: dict) -> int | None:
        tick = mt5.symbol_info_tick(config.SYMBOL)
        is_long = side == "LONG"
        entry = tick.ask if is_long else tick.bid
        sl_dist = min(max(ctx["atr"] * config.SCALP_ATR_MULT_SL, config.SCALP_SL_MIN_USD),
                      config.SCALP_SL_MAX_USD)
        tp_dist = sl_dist * (config.SCALP_ATR_MULT_TP / config.SCALP_ATR_MULT_SL)
        sl = round(entry - sl_dist if is_long else entry + sl_dist, 2)
        tp = round(entry + tp_dist if is_long else entry - tp_dist, 2)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": config.SYMBOL,
            "volume": config.SCALP_LOT,
            "type": mt5.ORDER_TYPE_BUY if is_long else mt5.ORDER_TYPE_SELL,
            "price": entry, "sl": sl, "tp": tp,
            "deviation": 20, "magic": storage.MAGIC, "comment": "scalp2",
            "type_time": mt5.ORDER_TIME_GTC,
        }
        for filling in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN):
            request["type_filling"] = filling
            res = mt5.order_send(request)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                storage.scalp_open(res.order, side, res.price, sl, tp, ctx={
                    "adx": round(ctx["adx"], 1), "atr": round(ctx["atr"], 2),
                    "h1_trend": ctx["h1_trend"], "hour_utc": datetime.now(timezone.utc).hour,
                    "spread": round(tick.ask - tick.bid, 2), "ema_gap": ctx["ema_gap"],
                    "version": VERSION})
                self.state["last_trade_ts"] = time.time()
                self._save()
                log.info("SCALP %s %s @ %.2f sl %.2f tp %.2f | H1 %s ADX %.0f ATR %.2f RSI %.0f",
                         VERSION, side, res.price, sl, tp, ctx["h1_trend"], ctx["adx"],
                         ctx["atr"], ctx["rsi"])
                storage.log_event("scalp", f"{side} @ {res.price:.2f} (ADX {ctx['adx']:.0f})")
                if self.state.get("scalp_signals"):
                    from .notifier import send
                    send(f"⚡ СКАЛЬП {side} | XAUUSD\n"
                         f"Вход @ {res.price:.2f} (лот {config.SCALP_LOT})\n"
                         f"TP {tp:.2f} · SL {sl:.2f}\n"
                         f"Тренд H1: {ctx['h1_trend']} · сила ADX {ctx['adx']:.0f}")
                return res.order
            if res and res.retcode != mt5.TRADE_RETCODE_INVALID_FILL:
                log.error("scalp order failed: %s %s", res.retcode, res.comment)
                return None
        return None

    # ---------- трейлинг ----------

    def trail(self):
        """Безубыток при +TRAIL_START, далее подтягиваем SL за ценой."""
        tick = mt5.symbol_info_tick(config.SYMBOL)
        if not tick or tick.bid <= 0:
            return
        for p in gw.positions(storage.MAGIC):
            is_long = p.type == mt5.POSITION_TYPE_BUY
            price = tick.bid if is_long else tick.ask
            favor = (price - p.price_open) if is_long else (p.price_open - price)
            if favor < config.SCALP_TRAIL_START:
                continue
            # сколько шагов трейлинга пройдено сверх точки старта
            steps = int((favor - config.SCALP_TRAIL_START) / config.SCALP_TRAIL_STEP)
            lock = config.SCALP_TRAIL_LOCK + steps * config.SCALP_TRAIL_STEP
            new_sl = round(p.price_open + lock if is_long else p.price_open - lock, 2)
            better = (new_sl > p.sl + 0.01) if is_long else (new_sl < p.sl - 0.01)
            if not better:
                continue
            if gw.set_position_tp(p.ticket, p.tp, sl=new_sl):
                storage.scalp_update_sl(p.ticket, new_sl, round(favor, 2))
                log.info("TRAIL #%s sl %.2f → %.2f (в плюсе $%.2f)",
                         p.ticket, p.sl, new_sl, favor)

    # ---------- синхронизация ----------

    def sync_closed(self):
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
            # серия стопов → пауза до следующего дня
            if pnl < 0:
                self.state["loss_streak"] = self.state.get("loss_streak", 0) + 1
                if self.state["loss_streak"] >= config.SCALP_MAX_LOSS_STREAK:
                    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    self.state["streak_pause_day"] = today
                    self.state["loss_streak"] = 0
                    storage.log_event("scalp", "серия стопов → пауза до след. дня")
                    log.warning("LOSS STREAK: пауза до следующего дня")
                    from .notifier import send_to, chat_id
                    send_to(chat_id(), f"⏸ {config.SCALP_MAX_LOSS_STREAK} стопа подряд — "
                                       f"скальпер на паузе до следующего дня")
            else:
                self.state["loss_streak"] = 0
            self._save()
            s = storage.scalp_stats()
            emoji = "✅" if pnl >= 0 else "🛑"
            log.info("SCALP CLOSED %s %s → %.2f pnl %.2f | WR %.0f%% итого %.2f",
                     t["side"], reason, d.price, pnl, s["winrate"], s["realized_pnl"])
            if self.state.get("scalp_signals"):
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
            self.state["loss_streak"] = 0
        day_loss = (acc["equity"] - self.state["day_anchor"][1]) / self.state["day_anchor"][1]
        if day_loss <= -config.GUARD_DAILY_LOSS_PCT and not self.state["halted"]:
            self._halt(f"дневной лимит скальпера: {day_loss*100:.1f}%")
        dd = 1 - acc["equity"] / self.state["watermark"]
        if dd >= config.GUARD_EQUITY_DD_STOP and not self.state["halted"]:
            self._halt(f"просадка equity {dd*100:.1f}%")
        self.state.setdefault("last_snap", 0.0)
        if time.time() - self.state["last_snap"] >= 60:
            self.state["last_snap"] = time.time()
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
        from .notifier import send, send_to, chat_id
        send_to(chat_id(), f"🛑 СТОП (скальп): {reason}\nПозиции закрыты, ордера сняты.")
        if self.state.get("scalp_signals"):
            send(f"🛑 СТОП (скальп): {reason}\nПозиции закрыты, ордера сняты.")
        self._save()

    def resume(self):
        self.state.update(halted=False, halted_reason="", watermark=None, day_anchor=None,
                          loss_streak=0, streak_pause_day="")
        self._save()
        from .notifier import send, send_to, chat_id
        send_to(chat_id(), "▶️ Скальпер возобновлён.")
        if self.state.get("scalp_signals"):
            send("▶️ Скальпер возобновлён.")

    def shutdown_terminal(self):
        from .grid_engine import GridBot
        GridBot.shutdown_terminal(self)

    # ---------- цикл ----------

    def run_once(self):
        if not self.state["scalp_cleaned"]:
            for o in gw.open_orders(storage.MAGIC):
                gw.cancel(o.ticket)
            self.state["scalp_cleaned"] = True
            storage.log_event("scalp", "профиль SCALP: сеточные ордера сняты")
            self._save()
        if not self.guard():
            return
        self.sync_closed()
        self.trail()
        ok, why = self.can_trade()
        if not ok:
            return
        side, ctx = self.signal()
        if side:
            self.open_trade(side, ctx)

    def status(self) -> dict:
        acc = gw.account()
        s = storage.scalp_stats()
        pos = gw.positions(storage.MAGIC)
        trend = "?"
        try:
            trend = self.h1_trend()
        except Exception:  # noqa: BLE001
            pass
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
            "trend_paused": self.state["streak_pause_day"] == datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "weekend_flat": False,
            "winrate": s["winrate"],
            "trades_today": s["trades_today"],
            "h1_trend": trend,
            "version": VERSION,
        }
