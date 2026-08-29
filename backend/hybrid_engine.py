# -*- coding: utf-8 -*-
"""Гибрид: лимитные входы сетки + жёсткий SL скальпинга (стратегия №3).

Почему гибрид, а не скальпинг:
  лимитный ордер не платит спред на догоне цены. При тейке $4 спред $0.35 — это
  8.7% от цели, у скальпинга с целью $1.5 было 25%. Это различие переворачивает
  матожидание: research_hybrid.py на 30 днях золота дал 14-16 сделок/день,
  winrate ~78%, PF 1.36 против PF 0.91 у скальпинга.

Почему жёсткие ограничения:
  стресс-тест на развёрнутом (падающем) рынке показал маржин-колл у конфигураций
  без лимита позиций. Поэтому: max 3 позиции на символ, аварийное закрытие всей
  корзины при пробое тренда H1 против нас, лимит риска корзины 25% от базы,
  дневной стоп 8%, флэт перед выходными.

Ожидания: 25-40 сделок/день на 3 инструментах, усреднённо 1.5-3%/день,
просадка до 25%. В аптренде перевыполняет, в развороте даёт ограниченный минус.
"""
import json
import logging
import time
from datetime import datetime, timezone

import MetaTrader5 as mt5
import numpy as np

from . import config, storage
from .research import ema

log = logging.getLogger("hybrid")


def _atr(h, l, c, p: int = 14) -> float:
    trs = [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
           for i in range(1, len(h))]
    return float(np.mean(trs[-p:])) if len(trs) >= p else 0.0


class HybridBot:
    def __init__(self):
        self.state_path = config.DATA_DIR / config.HYBRID_STATE_FILE
        self.state = self._load()
        self.state.setdefault("halted", False)
        self.state.setdefault("halted_reason", "")
        self.state.setdefault("day_anchor", None)      # [дата, realized на начало дня]
        self.state.setdefault("last_rebuild", {})      # symbol -> ts
        self.state.setdefault("grid_trend", {})        # symbol -> тренд на момент сборки
        self.state.setdefault("skipped", {})           # symbol -> причина пропуска
        self.state.setdefault("weekend_flat", False)

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
        for s in config.HYBRID_SYMBOLS:
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
        return [p for p in (pos or []) if p.magic == config.HYBRID_MAGIC]

    def orders(self, symbol: str | None = None) -> list:
        o = mt5.orders_get(symbol=symbol) if symbol else mt5.orders_get()
        return [x for x in (o or []) if x.magic == config.HYBRID_MAGIC]

    # ---------- контекст ----------

    def h1_trend(self, symbol: str) -> str:
        bars = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 1, 60)
        if bars is None or len(bars) < 55:
            return "FLAT"
        c = np.array([b["close"] for b in bars], float)
        e = ema(c, 50)
        if c[-1] > e[-1] and e[-1] > e[-5]:
            return "UP"
        if c[-1] < e[-1] and e[-1] < e[-5]:
            return "DOWN"
        return "FLAT"

    def atr_m5(self, symbol: str) -> float:
        bars = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 1, 40)
        if bars is None or len(bars) < 20:
            return 0.0
        return _atr(np.array([b["high"] for b in bars], float),
                    np.array([b["low"] for b in bars], float),
                    np.array([b["close"] for b in bars], float))

    def usd_per_price_unit(self, symbol: str) -> float:
        info = mt5.symbol_info(symbol)
        return info.trade_contract_size * config.HYBRID_LOT if info else 0.0

    # ---------- риск ----------

    def basket_risk_usd(self) -> float:
        """Суммарный риск открытых позиций (расстояние до SL в USD)."""
        total = 0.0
        for p in self.positions():
            if not p.sl:
                continue
            pv = self.usd_per_price_unit(p.symbol)
            total += abs(p.price_open - p.sl) * pv * (p.volume / config.HYBRID_LOT)
        return total

    # ---------- сетка ----------

    def rebuild_grid(self, symbol: str, trend: str):
        """Снять старые лимитки и выставить новые уровни под текущую цену."""
        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        atr = self.atr_m5(symbol)
        if not info or not tick or tick.ask <= 0 or atr <= 0:
            return
        # цель адаптивная: не меньше N спредов, иначе спред съедает прибыль
        # (у индексов ATR-цель выходила $0.05 при спреде $0.025 = 46%)
        spread_now = tick.ask - tick.bid
        min_tp = spread_now * config.HYBRID_MIN_TP_SPREADS
        tp_d = max(atr * config.HYBRID_TP_ATR, min_tp)
        step = tp_d                                   # шаг = цель
        sl_d = tp_d * (config.HYBRID_SL_ATR / config.HYBRID_TP_ATR)
        pv = self.usd_per_price_unit(symbol)

        # риск одной позиции не должен превышать лимит
        pos_risk_pct = 100 * sl_d * pv / config.HYBRID_TEST_BALANCE
        # спред не должен съедать цель (главный урок скальпинга: было 25% → убыток)
        spread_pct_of_tp = 100 * (tick.ask - tick.bid) / tp_d if tp_d > 0 else 999
        reason = None
        if pos_risk_pct > config.HYBRID_MAX_POS_RISK_PCT:
            reason = f"риск позиции {pos_risk_pct:.1f}% > {config.HYBRID_MAX_POS_RISK_PCT}%"
        elif spread_pct_of_tp > config.HYBRID_MAX_SPREAD_PCT_OF_TP:
            reason = (f"спред {spread_pct_of_tp:.0f}% от цели "
                      f"> {config.HYBRID_MAX_SPREAD_PCT_OF_TP:.0f}%")
        if reason:
            self.state["skipped"][symbol] = reason
            self._save()
            for o in self.orders(symbol):
                mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})
            return
        self.state["skipped"].pop(symbol, None)

        for o in self.orders(symbol):
            mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})

        # минимальная дистанция брокера
        spread = tick.ask - tick.bid
        min_dist = info.trade_stops_level * info.point + spread * 1.5
        sl_d = max(sl_d, min_dist)
        tp_d = max(tp_d, min_dist)

        is_up = trend == "UP"
        base = tick.bid if is_up else tick.ask
        placed = 0
        for k in range(1, config.HYBRID_LEVELS + 1):
            if is_up:
                price = round(base - step * k, info.digits)
                sl = round(price - sl_d, info.digits)
                tp = round(price + tp_d, info.digits)
                otype = mt5.ORDER_TYPE_BUY_LIMIT
            else:
                price = round(base + step * k, info.digits)
                sl = round(price + sl_d, info.digits)
                tp = round(price - tp_d, info.digits)
                otype = mt5.ORDER_TYPE_SELL_LIMIT
            req = {
                "action": mt5.TRADE_ACTION_PENDING, "symbol": symbol,
                "volume": config.HYBRID_LOT, "type": otype, "price": price,
                "sl": sl, "tp": tp, "magic": config.HYBRID_MAGIC,
                "comment": "hybrid", "type_time": mt5.ORDER_TIME_GTC,
            }
            for filling in (mt5.ORDER_FILLING_RETURN, mt5.ORDER_FILLING_IOC,
                            mt5.ORDER_FILLING_FOK):
                req["type_filling"] = filling
                res = mt5.order_send(req)
                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                    placed += 1
                    break
                if res and res.retcode != mt5.TRADE_RETCODE_INVALID_FILL:
                    log.warning("hybrid limit %s @%.*f: %s %s", symbol, info.digits,
                                price, res.retcode, res.comment)
                    break
        self.state["last_rebuild"][symbol] = time.time()
        self.state["grid_trend"][symbol] = trend
        self._save()
        if placed:
            log.info("HYBRID GRID %s %s: %d уровней, шаг %.*f, SL %.*f, TP %.*f "
                     "(риск позиции %.1f%%)", symbol, trend, placed, info.digits, step,
                     info.digits, sl_d, info.digits, tp_d, pos_risk_pct)
            storage.log_event("hybrid", f"{symbol}: сетка {trend}, {placed} уровней")

    # ---------- синхронизация ----------

    def sync(self):
        """Новые позиции → в БД; закрытые → PnL и причина."""
        live = {p.ticket: p for p in self.positions()}
        known = {t["ticket"] for t in storage.hybrid_open_trades()}
        for ticket, p in live.items():
            if ticket in known:
                continue
            side = "LONG" if p.type == mt5.POSITION_TYPE_BUY else "SHORT"
            storage.scalp_open(ticket, side, p.price_open, p.sl, p.tp, ctx={
                "hour_utc": datetime.now(timezone.utc).hour,
                "version": config.HYBRID_DB_VERSION, "symbol": p.symbol,
                "atr": round(self.atr_m5(p.symbol), 4),
                "h1_trend": self.state["grid_trend"].get(p.symbol, "?"),
            })
            log.info("HYBRID FILL %s %s @ %.5f", p.symbol, side, p.price_open)
            if config.HYBRID_SIGNAL_FILLS:
                from .notifier import send_signal
                arrow = "🟢" if side == "LONG" else "🔴"
                trend = self.state["grid_trend"].get(p.symbol, "?")
                send_signal(
                    f"{arrow} ГИБРИД ВХОД {p.symbol} {side}\n"
                    f"Цена: {p.price_open:.5f}\n"
                    f"TP: {p.tp:.5f} · SL: {p.sl:.5f}\n"
                    f"Тренд H1: {trend}")
        for t in storage.hybrid_open_trades():
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
            s = storage.hybrid_stats()
            log.info("HYBRID CLOSED %s %s %s → %.5f pnl %.2f | WR %.0f%% итого %.2f",
                     t["symbol"], t["side"], reason, d.price, pnl,
                     s["winrate"], s["realized_pnl"])
            if config.HYBRID_SIGNAL_CLOSES:
                from .notifier import send_signal
                mark = "✅" if pnl >= 0 else "🔻"
                reason_txt = {"tp": "тейк", "sl": "стоп", "manual": "закрытие"}.get(reason, reason)
                send_signal(
                    f"{mark} ГИБРИД ЗАКРЫТ {t['symbol']} {t['side']} ({reason_txt})\n"
                    f"PnL: {pnl:+.2f}$\n"
                    f"Winrate: {s['winrate']:.0f}% · итого: {s['realized_pnl']:+.2f}$")

    # ---------- защита ----------

    def emergency_check(self, symbols: list[str]):
        """Аварийный выход: тренд H1 развернулся против корзины символа."""
        for sym in symbols:
            pos = self.positions(sym)
            if not pos:
                continue
            grid_trend = self.state["grid_trend"].get(sym)
            now_trend = self.h1_trend(sym)
            if not grid_trend or now_trend == grid_trend or now_trend == "FLAT":
                continue
            # тренд перевернулся — закрываем корзину символа и снимаем лимитки
            floating = sum(p.profit for p in pos)
            self.close_symbol(sym, f"тренд H1 развернулся {grid_trend}→{now_trend}")
            log.warning("HYBRID EMERGENCY %s: %s→%s, floating %.2f",
                        sym, grid_trend, now_trend, floating)
            storage.log_event("hybrid", f"{sym}: аварийный выход ({grid_trend}→{now_trend}), "
                                        f"floating {floating:+.2f}$")
            from .notifier import send_to, chat_id
            send_to(chat_id(), f"⚠️ Гибрид: аварийный выход {sym}\n"
                               f"Тренд H1 {grid_trend}→{now_trend}, "
                               f"плавающий {floating:+.2f}$")

    def close_symbol(self, symbol: str, reason: str = ""):
        for o in self.orders(symbol):
            mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})
        for p in self.positions(symbol):
            tick = mt5.symbol_info_tick(p.symbol)
            if not tick:
                continue
            is_long = p.type == mt5.POSITION_TYPE_BUY
            mt5.order_send({
                "action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol,
                "volume": p.volume, "position": p.ticket,
                "type": mt5.ORDER_TYPE_SELL if is_long else mt5.ORDER_TYPE_BUY,
                "price": tick.bid if is_long else tick.ask,
                "deviation": 30, "magic": config.HYBRID_MAGIC,
                "type_filling": mt5.ORDER_FILLING_IOC})
        self.state["grid_trend"].pop(symbol, None)
        self.state["last_rebuild"].pop(symbol, None)
        self._save()

    def guard(self) -> bool:
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        s = storage.hybrid_stats()

        if self.state["day_anchor"] is None or self.state["day_anchor"][0] != today:
            self.state["day_anchor"] = [today, s["realized_pnl"]]
            self._save()

        # дневной лимит убытка (от тестовой базы)
        day_pnl = s["realized_pnl"] - self.state["day_anchor"][1]
        day_pct = 100 * day_pnl / config.HYBRID_TEST_BALANCE
        if day_pct <= -config.HYBRID_DAILY_LOSS_PCT and not self.state["halted"]:
            self._halt(f"дневной лимит: {day_pct:.1f}% (${day_pnl:.2f})")
            return False

        # риск корзины
        risk_pct = 100 * self.basket_risk_usd() / config.HYBRID_TEST_BALANCE
        self.state["basket_risk_pct"] = round(risk_pct, 1)
        if risk_pct > config.HYBRID_MAX_BASKET_RISK_PCT:
            for o in self.orders():
                mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})
            self.state["skipped"]["_basket"] = (
                f"риск корзины {risk_pct:.0f}% > {config.HYBRID_MAX_BASKET_RISK_PCT}% "
                f"— новые уровни не ставим")
            self._save()
        else:
            self.state["skipped"].pop("_basket", None)

        # weekend flat
        if now.weekday() == 4 and now.hour >= config.HYBRID_WEEKEND_CLOSE_HOUR:
            if not self.state["weekend_flat"]:
                for sym in [x.strip() for x in config.HYBRID_SYMBOLS]:
                    self.close_symbol(sym, "weekend flat")
                self.state["weekend_flat"] = True
                self._save()
                log.warning("HYBRID weekend flat")
            return False
        if self.state["weekend_flat"] and now.weekday() < 4:
            self.state["weekend_flat"] = False
            self._save()

        return not self.state["halted"]

    def _halt(self, reason: str):
        for sym in [x.strip() for x in config.HYBRID_SYMBOLS]:
            self.close_symbol(sym, reason)
        self.state.update(halted=True, halted_reason=reason)
        self._save()
        storage.log_event("hybrid_halt", reason)
        log.error("HYBRID HALT: %s", reason)
        from .notifier import send_to, chat_id
        send_to(chat_id(), f"🛑 Гибрид остановлен: {reason}")

    def resume(self):
        self.state.update(halted=False, halted_reason="", day_anchor=None,
                          weekend_flat=False, grid_trend={}, last_rebuild={})
        self._save()
        log.info("HYBRID resumed")

    # ---------- цикл ----------

    def run_once(self, symbols: list[str]):
        self.sync()
        if not self.guard():
            return
        self.emergency_check(symbols)

        now = datetime.now(timezone.utc)
        if now.weekday() >= 5:
            return
        if not (config.HYBRID_HOUR_FROM_UTC <= now.hour < config.HYBRID_HOUR_TO_UTC):
            return
        if "_basket" in self.state["skipped"]:
            return
        if len(self.positions()) >= config.HYBRID_MAX_POS_TOTAL:
            return

        for sym in symbols:
            trend = self.h1_trend(sym)
            if trend == "FLAT":
                # во флэте лимитки снимаем, позиции доживают по своим TP/SL
                if self.orders(sym):
                    for o in self.orders(sym):
                        mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})
                continue
            if len(self.positions(sym)) >= config.HYBRID_MAX_POS_PER_SYMBOL:
                continue
            last = self.state["last_rebuild"].get(sym, 0)
            need_rebuild = (
                time.time() - last > config.HYBRID_REBUILD_SEC
                or not self.orders(sym)
                or self.state["grid_trend"].get(sym) != trend
            )
            if need_rebuild:
                self.rebuild_grid(sym, trend)

    def status(self) -> dict:
        s = storage.hybrid_stats()
        pos = self.positions()
        trends = {}
        for sym in [x.strip() for x in config.HYBRID_SYMBOLS]:
            try:
                trends[sym] = self.h1_trend(sym)
            except Exception:  # noqa: BLE001
                trends[sym] = "?"
        return {
            "symbols": [x.strip() for x in config.HYBRID_SYMBOLS],
            "trends": trends,
            "stats": s,
            "positions": len(pos),
            "pending_orders": len(self.orders()),
            "floating_pnl": round(sum(p.profit for p in pos), 2),
            "basket_risk_pct": round(
                100 * self.basket_risk_usd() / config.HYBRID_TEST_BALANCE, 1),
            "max_basket_risk_pct": config.HYBRID_MAX_BASKET_RISK_PCT,
            "halted": self.state["halted"],
            "halted_reason": self.state["halted_reason"],
            "weekend_flat": self.state["weekend_flat"],
            "skipped": self.state["skipped"],
            "grid_trend": self.state["grid_trend"],
            "test_balance": config.HYBRID_TEST_BALANCE,
            "lot": config.HYBRID_LOT,
        }
