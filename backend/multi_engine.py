# -*- coding: utf-8 -*-
"""Мульти-символьный бот (отдельный процесс: python -m backend.main_multi).

Логика входа — та же, что в scalp v2 (тренд H1 + ADX + откат на M5, ATR-стопы,
трейлинг), но параллельно по нескольким инструментам. Цель: 20-40 сделок/день
без ухудшения качества входа за счёт диверсификации, а не учащения.

Риск-фильтр под мини-баланс $100-150: если стоимость стопа на минимальном лоте
превышает MULTI_MAX_RISK_PCT от MULTI_TEST_BALANCE, символ пропускается.
Замер 2026-08-25: NAS100 0.2% OK · XAUUSD 4.9% дорого · XAGUSD 6.4% дорого.

Свой magic (MULTI_MAGIC) и своё состояние (state_multi.json) — не пересекается
с одиночным скальпером, статистика раздельная (version='multi' в БД).
"""
import json
import logging
import time
from datetime import datetime, timezone

import MetaTrader5 as mt5
import numpy as np

from . import config, storage
from .scalp_engine import _adx, _atr, _ema, _rsi

log = logging.getLogger("multi")


class MultiBot:
    def __init__(self):
        self.state_path = config.DATA_DIR / config.MULTI_STATE_FILE
        self.state = self._load()
        self.state.setdefault("halted", False)
        self.state.setdefault("halted_reason", "")
        self.state.setdefault("watermark", None)
        self.state.setdefault("day_anchor", None)
        self.state.setdefault("last_trade", {})      # symbol -> ts
        self.state.setdefault("loss_streak", {})     # symbol -> count
        self.state.setdefault("paused_symbols", {})  # symbol -> day
        self.state.setdefault("skipped_risk", {})    # symbol -> причина (для админки)

    # ---------- состояние ----------

    def _load(self) -> dict:
        try:
            with open(self.state_path, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self):
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)

    # ---------- инструменты ----------

    def prepare_symbols(self) -> list[str]:
        ready = []
        for s in config.MULTI_SYMBOLS:
            s = s.strip()
            if not s:
                continue
            info = mt5.symbol_info(s)
            if info is None:
                log.warning("символ %s недоступен", s)
                continue
            if not info.visible:
                mt5.symbol_select(s, True)
            ready.append(s)
        return ready

    def positions(self, symbol: str | None = None) -> list:
        pos = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        return [p for p in (pos or []) if p.magic == config.MULTI_MAGIC]

    # ---------- рыночный контекст ----------

    def h1_trend(self, symbol: str) -> str:
        bars = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 1, 60)
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

    def m5_context(self, symbol: str) -> dict | None:
        bars = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 1, 120)
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
            "ema_gap": round(float(ema_f[-1] - ema_s[-1]), 4),
            "rsi": _rsi(closes, config.SCALP_RSI_PERIOD),
            "atr": _atr(highs, lows, closes, 14),
            "adx": _adx(highs, lows, closes, config.SCALP_ADX_PERIOD),
        }

    def signal(self, symbol: str) -> tuple[str | None, dict]:
        trend = self.h1_trend(symbol)
        ctx = self.m5_context(symbol)
        if ctx is None:
            return None, {"skip": "нет данных M5"}
        ctx["h1_trend"] = trend
        if trend == "FLAT":
            return None, {**ctx, "skip": "H1 флэт"}
        if ctx["adx"] < config.SCALP_ADX_MIN:
            return None, {**ctx, "skip": f"ADX {ctx['adx']:.0f}"}
        if (trend == "UP" and ctx["ema_f"] > ctx["ema_s"]
                and ctx["low"] <= ctx["ema_f"] <= ctx["close"]
                and config.SCALP_RSI_LONG_MIN <= ctx["rsi"] <= config.SCALP_RSI_LONG_MAX):
            return "LONG", ctx
        if (trend == "DOWN" and ctx["ema_f"] < ctx["ema_s"]
                and ctx["high"] >= ctx["ema_f"] >= ctx["close"]
                and config.SCALP_RSI_SHORT_MIN <= ctx["rsi"] <= config.SCALP_RSI_SHORT_MAX):
            return "SHORT", ctx
        return None, {**ctx, "skip": "нет сетапа"}

    # ---------- риск ----------

    def sl_cost_usd(self, symbol: str, sl_distance: float) -> float:
        """Стоимость стопа в USD на MULTI_LOT."""
        info = mt5.symbol_info(symbol)
        if info is None:
            return 0.0
        return sl_distance * info.trade_contract_size * config.MULTI_LOT

    def risk_ok(self, symbol: str, sl_distance: float) -> tuple[bool, float]:
        cost = self.sl_cost_usd(symbol, sl_distance)
        pct = 100 * cost / config.MULTI_TEST_BALANCE if config.MULTI_TEST_BALANCE else 0
        if config.MULTI_ENFORCE_RISK and pct > config.MULTI_MAX_RISK_PCT:
            return False, pct
        return True, pct

    # ---------- фильтры ----------

    def can_trade_symbol(self, symbol: str) -> tuple[bool, str]:
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        if self.state["paused_symbols"].get(symbol) == today:
            return False, "пауза после серии стопов"
        if len(self.positions(symbol)) >= config.MULTI_MAX_OPEN_PER_SYMBOL:
            return False, "уже есть позиция"
        last = self.state["last_trade"].get(symbol, 0)
        if time.time() - last < config.MULTI_COOLDOWN_SEC:
            return False, "кулдаун"
        tick = mt5.symbol_info_tick(symbol)
        if not tick or tick.ask <= 0:
            return False, "нет тика"
        return True, ""

    def can_trade_global(self) -> tuple[bool, str]:
        now = datetime.now(timezone.utc)
        if self.state["halted"]:
            return False, "halted"
        if now.weekday() >= 5:
            return False, "выходные"
        if not (config.SCALP_HOUR_FROM_UTC <= now.hour < config.SCALP_HOUR_TO_UTC):
            return False, "неактивные часы"
        st = storage.multi_stats()
        if st["trades_today"] >= config.MULTI_MAX_TRADES_DAY:
            return False, "лимит дня"
        if st["open_trades"] >= config.MULTI_MAX_OPEN_TOTAL:
            return False, "лимит позиций"
        return True, ""

    # ---------- сделка ----------

    def open_trade(self, symbol: str, side: str, ctx: dict) -> int | None:
        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        is_long = side == "LONG"
        entry = tick.ask if is_long else tick.bid
        sl_dist = ctx["atr"] * config.SCALP_ATR_MULT_SL
        tp_dist = ctx["atr"] * config.SCALP_ATR_MULT_TP
        # минимальная дистанция стопа брокера (trade_stops_level) + запас на спред.
        # Без этого на закрытом/тонком рынке ATR сжимается и ордер отбивается 10016.
        spread = tick.ask - tick.bid
        min_dist = info.trade_stops_level * info.point + spread * 1.5
        if min_dist > 0 and sl_dist < min_dist:
            sl_dist = min_dist
            tp_dist = sl_dist * (config.SCALP_ATR_MULT_TP / config.SCALP_ATR_MULT_SL)
        ok, pct = self.risk_ok(symbol, sl_dist)
        if not ok:
            self.state["skipped_risk"][symbol] = (
                f"стоп {pct:.1f}% от ${config.MULTI_TEST_BALANCE:.0f} "
                f"> лимита {config.MULTI_MAX_RISK_PCT}%")
            self._save()
            return None
        self.state["skipped_risk"].pop(symbol, None)
        digits = info.digits
        sl = round(entry - sl_dist if is_long else entry + sl_dist, digits)
        tp = round(entry + tp_dist if is_long else entry - tp_dist, digits)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": config.MULTI_LOT,
            "type": mt5.ORDER_TYPE_BUY if is_long else mt5.ORDER_TYPE_SELL,
            "price": entry, "sl": sl, "tp": tp,
            "deviation": 30, "magic": config.MULTI_MAGIC, "comment": "multi",
            "type_time": mt5.ORDER_TIME_GTC,
        }
        for filling in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN):
            request["type_filling"] = filling
            res = mt5.order_send(request)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                storage.scalp_open(res.order, side, res.price, sl, tp, ctx={
                    "adx": round(ctx["adx"], 1), "atr": round(ctx["atr"], 4),
                    "h1_trend": ctx["h1_trend"],
                    "hour_utc": datetime.now(timezone.utc).hour,
                    "spread": round(tick.ask - tick.bid, digits),
                    "ema_gap": ctx["ema_gap"], "version": config.MULTI_DB_VERSION,
                    "symbol": symbol})
                self.state["last_trade"][symbol] = time.time()
                self._save()
                log.info("MULTI %s %s @ %.*f sl %.*f tp %.*f | H1 %s ADX %.0f риск %.1f%%",
                         symbol, side, digits, res.price, digits, sl, digits, tp,
                         ctx["h1_trend"], ctx["adx"], pct)
                storage.log_event("multi", f"{symbol} {side} @ {res.price} (ADX {ctx['adx']:.0f})")
                return res.order
            if res and res.retcode != mt5.TRADE_RETCODE_INVALID_FILL:
                log.error("multi order %s failed: %s %s", symbol, res.retcode, res.comment)
                # кулдаун на символ, чтобы не долбить брокера каждые 20 сек
                self.state["last_trade"][symbol] = time.time()
                self.state["skipped_risk"][symbol] = f"отказ брокера {res.retcode}: {res.comment}"
                self._save()
                return None
        return None

    # ---------- трейлинг ----------

    def trail(self):
        for p in self.positions():
            info = mt5.symbol_info(p.symbol)
            tick = mt5.symbol_info_tick(p.symbol)
            if not info or not tick or tick.bid <= 0:
                continue
            is_long = p.type == mt5.POSITION_TYPE_BUY
            price = tick.bid if is_long else tick.ask
            favor = (price - p.price_open) if is_long else (p.price_open - price)
            # шаги трейлинга масштабируем от ATR символа (в его цене, не в USD)
            sl_dist = abs(p.price_open - p.sl) if p.sl else 0
            if sl_dist <= 0:
                continue
            start = sl_dist * 0.75      # начинаем тянуть при 0.75R в плюсе
            step = sl_dist * 0.5
            if favor < start:
                continue
            steps = int((favor - start) / step)
            lock = sl_dist * 0.15 + steps * step
            new_sl = round(p.price_open + lock if is_long else p.price_open - lock, info.digits)
            better = (new_sl > p.sl + info.point) if is_long else (new_sl < p.sl - info.point)
            if not better:
                continue
            res = mt5.order_send({
                "action": mt5.TRADE_ACTION_SLTP, "symbol": p.symbol,
                "position": p.ticket, "sl": new_sl, "tp": p.tp})
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                storage.scalp_update_sl(p.ticket, new_sl, round(favor, 4))
                log.info("MULTI TRAIL %s #%s sl → %.*f", p.symbol, p.ticket, info.digits, new_sl)

    # ---------- синхронизация ----------

    def sync_closed(self):
        live = {p.ticket for p in self.positions()}
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for t in storage.multi_open_trades():
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
            sym = t["symbol"] or "?"
            streak = self.state["loss_streak"]
            if pnl < 0:
                streak[sym] = streak.get(sym, 0) + 1
                if streak[sym] >= config.SCALP_MAX_LOSS_STREAK:
                    self.state["paused_symbols"][sym] = today
                    streak[sym] = 0
                    log.warning("MULTI %s: серия стопов → пауза до след. дня", sym)
                    storage.log_event("multi", f"{sym}: пауза после серии стопов")
            else:
                streak[sym] = 0
            self._save()
            s = storage.multi_stats()
            log.info("MULTI CLOSED %s %s %s → %.4f pnl %.2f | WR %.0f%% итого %.2f",
                     sym, t["side"], reason, d.price, pnl, s["winrate"], s["realized_pnl"])

    # ---------- защита ----------

    def guard(self) -> bool:
        acc = mt5.account_info()
        if acc is None:
            return not self.state["halted"]
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        if self.state["watermark"] is None:
            self.state["watermark"] = acc.equity
        self.state["watermark"] = max(self.state["watermark"], acc.equity)
        if self.state["day_anchor"] is None or self.state["day_anchor"][0] != today:
            self.state["day_anchor"] = [today, acc.balance]
            self.state["loss_streak"] = {}
            self.state["paused_symbols"] = {}
        # дневной лимит и просадка считаются от PnL мульти-бота, а не всего счёта
        # (на демо счёт общий с другими ботами)
        s = storage.multi_stats()
        risk_base = config.MULTI_TEST_BALANCE
        day_loss_pct = 100 * s["realized_pnl"] / risk_base if risk_base else 0
        if day_loss_pct <= -config.GUARD_DAILY_LOSS_PCT * 100 and not self.state["halted"]:
            self._halt(f"суммарный PnL {s['realized_pnl']:.2f}$ = "
                       f"{day_loss_pct:.1f}% от тестового баланса ${risk_base:.0f}")
        self._save()
        return not self.state["halted"]

    def _halt(self, reason: str):
        for p in self.positions():
            tick = mt5.symbol_info_tick(p.symbol)
            if not tick:
                continue
            is_long = p.type == mt5.POSITION_TYPE_BUY
            mt5.order_send({
                "action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol,
                "volume": p.volume, "position": p.ticket,
                "type": mt5.ORDER_TYPE_SELL if is_long else mt5.ORDER_TYPE_BUY,
                "price": tick.bid if is_long else tick.ask,
                "deviation": 30, "magic": config.MULTI_MAGIC,
                "type_filling": mt5.ORDER_FILLING_IOC})
        self.state.update(halted=True, halted_reason=reason)
        storage.log_event("multi_halt", reason)
        log.error("MULTI HALT: %s", reason)
        from .notifier import send_to, chat_id
        send_to(chat_id(), f"🛑 Мульти-бот остановлен: {reason}")
        self._save()

    def resume(self):
        self.state.update(halted=False, halted_reason="", watermark=None, day_anchor=None,
                          loss_streak={}, paused_symbols={})
        self._save()
        log.info("MULTI resumed")

    # ---------- цикл ----------

    def run_once(self, symbols: list[str]):
        if not self.guard():
            return
        self.sync_closed()
        self.trail()
        ok, why = self.can_trade_global()
        if not ok:
            return
        for sym in symbols:
            ok_sym, _ = self.can_trade_symbol(sym)
            if not ok_sym:
                continue
            side, ctx = self.signal(sym)
            if side:
                self.open_trade(sym, side, ctx)
                if len(self.positions()) >= config.MULTI_MAX_OPEN_TOTAL:
                    break

    def status(self) -> dict:
        s = storage.multi_stats()
        pos = self.positions()
        trends = {}
        for sym in config.MULTI_SYMBOLS:
            sym = sym.strip()
            try:
                trends[sym] = self.h1_trend(sym)
            except Exception:  # noqa: BLE001
                trends[sym] = "?"
        return {
            "symbols": [s.strip() for s in config.MULTI_SYMBOLS],
            "trends": trends,
            "stats": s,
            "positions": len(pos),
            "floating_pnl": round(sum(p.profit for p in pos), 2),
            "halted": self.state["halted"],
            "halted_reason": self.state["halted_reason"],
            "paused_symbols": self.state["paused_symbols"],
            "skipped_risk": self.state["skipped_risk"],
            "test_balance": config.MULTI_TEST_BALANCE,
            "max_risk_pct": config.MULTI_MAX_RISK_PCT,
            "lot": config.MULTI_LOT,
        }
