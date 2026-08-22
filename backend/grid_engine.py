# -*- coding: utf-8 -*-
"""Грид-движок XAUUSD: сетка buy-limit + counter-sell (пары), тренд-фильтр."""
import logging
import time
from datetime import datetime, timezone

import MetaTrader5 as mt5

from . import config, mt5_gateway as gw, storage

log = logging.getLogger("engine")


class GridBot:
    def __init__(self):
        self.state = storage.state_load()
        self.state.setdefault("watermark", None)       # пик equity
        self.state.setdefault("halted", False)
        self.state.setdefault("halted_reason", "")
        self.state.setdefault("trend_paused", False)
        self.state.setdefault("day_anchor", None)      # [дата, баланс]
        self.state.setdefault("weekend_flat", False)

    # ---------- утилиты ----------

    def _save(self):
        storage.state_save(self.state)

    def _utcnow(self) -> datetime:
        return datetime.now(timezone.utc)

    # ---------- тренд-фильтр (аналог v10.1) ----------

    def trend_change_pct(self) -> float | None:
        bars = gw.rates(config.TREND_LOOKBACK_HOURS + 1)
        if bars is None or len(bars) < config.TREND_LOOKBACK_HOURS + 1:
            return None
        then, now = bars[0]["close"], bars[-1]["close"]
        return (now - then) / then * 100.0

    def atr_usd_per_hour(self) -> float | None:
        """Средний истинный диапазон за час (ATR-14 на H1) — для прогноза времени."""
        bars = gw.rates(16)
        if len(bars) < 15:
            return None
        trs = []
        for i in range(1, len(bars)):
            h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        return sum(trs[-14:]) / 14

    def eta_hours(self, distance_usd: float) -> str:
        """Ориентировочное время достижения уровня по ATR."""
        atr = self.atr_usd_per_hour()
        if not atr or atr <= 0:
            return "~неизвестно"
        hours = distance_usd / atr
        if hours < 1:
            return f"~{max(5, int(hours * 60))} мин"
        return f"~{hours:.0f} ч"

    def check_trend(self):
        change = self.trend_change_pct()
        if change is None:
            log.warning("trend data unavailable, keep state")
            return
        from .notifier import send
        if not self.state["trend_paused"] and change <= config.TREND_DROP_PCT:
            self.state["trend_paused"] = True
            self._cancel_buy_limits()
            storage.log_event("trend", f"пауза покупок: {change:.2f}% за 24ч")
            send(f"⚠️ Тренд-фильтр: {change:.2f}% за 24ч — покупки на паузе, buy-ордера отменены "
                 f"(TP открытых позиций работают)")
            log.warning("TREND PAUSE: %.2f%%/24h", change)
        elif self.state["trend_paused"] and change > config.TREND_RESUME_PCT:
            self.state["trend_paused"] = False
            storage.log_event("trend", f"покупки возобновлены: {change:.2f}%")
            send(f"✅ Тренд-фильтр: {change:.2f}% за 24ч — покупки возобновлены, сетка восстанавливается")
            log.info("TREND RESUME: %.2f%%", change)
        self._save()

    # ---------- сетка ----------

    def _buy_limits(self) -> list:
        return [o for o in gw.open_orders(storage.MAGIC)
                if o.type == mt5.ORDER_TYPE_BUY_LIMIT]

    def _sell_limits(self) -> list:
        return [o for o in gw.open_orders(storage.MAGIC)
                if o.type == mt5.ORDER_TYPE_SELL_LIMIT]

    def _cancel_buy_limits(self):
        for o in self._buy_limits():
            gw.cancel(o.ticket)

    def ensure_grid(self):
        """Начальная сетка, поддержание снизу и авто-сдвиг вверх (как auto-range-shift)."""
        if self.state["halted"] or self.state["trend_paused"] or self.state["weekend_flat"]:
            return
        bid, _ = gw.get_tick()
        buys = self._buy_limits()

        # авто-сдвиг: цена ушла выше верхнего уровня + N шагов → пересборка вокруг цены.
        # Открытые позиции и их TP не трогаем (закрываются сервером сами).
        if buys:
            top = max(o.price_open for o in buys)
            self.state.setdefault("last_shift", 0.0)
            now_ts = time.time()
            if (bid > top + config.GRID_STEP_USD * config.SHIFT_TRIGGER_STEPS
                    and now_ts - self.state["last_shift"] >= config.SHIFT_COOLDOWN_SEC):
                self._cancel_buy_limits()
                for i in range(1, config.GRID_LEVELS + 1):
                    self._place_buy(round(bid - config.GRID_STEP_USD * i, 2))
                self.state["last_shift"] = now_ts
                storage.log_event("grid", f"авто-сдвиг вверх: пересборка вокруг {bid:.2f}")
                log.info("GRID SHIFT UP around %.2f", bid)
                self._signal_grid(bid, "СДВИНУТА ВВЕРХ (рост цены)")
                self._save()
                return

        existing_prices = {round(o.price_open, 2) for o in buys}
        # первый запуск: построить лесенку от текущей цены
        if not buys and not storage.open_pairs():
            for i in range(1, config.GRID_LEVELS + 1):
                price = round(bid - config.GRID_STEP_USD * i, 2)
                self._place_buy(price)
            storage.log_event("grid", f"начальная сетка: {config.GRID_LEVELS} уровней от {bid:.2f}")
            self._signal_grid(bid, "СОБРАНА")
            return
        # поддержание: если уровней меньше положенного — добавить ниже самого нижнего
        lowest = min(existing_prices) if existing_prices else bid
        need = config.GRID_LEVELS - len(buys)
        for i in range(need):
            price = round(lowest - config.GRID_STEP_USD * (i + 1), 2)
            if price > 0:
                self._place_buy(price)

    def _place_buy(self, price: float) -> int | None:
        ticket = gw.place_limit("BUY", price, config.GRID_LOT, "grid_buy", storage.MAGIC)
        if ticket:
            log.info("BUY LIMIT #%s @ %.2f", ticket, price)
        return ticket

    def _signal_grid(self, bid: float, reason: str):
        """Сигнал в канал: лесенка + ориентир времени до ближнего уровня."""
        from .notifier import send
        buys = sorted(self._buy_limits(), key=lambda o: o.price_open, reverse=True)
        if not buys:
            return
        top = buys[0].price_open
        ladder = ", ".join(f"{o.price_open:.2f}" for o in buys[:5])
        atr = self.atr_usd_per_hour()
        eta = self.eta_hours(bid - top)
        atr_note = f"волатильность ~${atr:.1f}/ч" if atr else ""
        send(
            f"📊 СЕТКА {reason} | XAUUSD\n"
            f"Уровней: {len(buys)} · шаг ${config.GRID_STEP_USD:.0f} · лот {config.GRID_LOT}\n"
            f"Диапазон: {buys[-1].price_open:.2f} – {top:.2f}\n"
            f"Ближние уровни: {ladder}\n"
            f"⏱ {atr_note}, первый уровень {top:.2f} — {eta}\n"
            f"Повторяйте: buy-limit по ценам уровней, TP +${config.TP_USD:.0f}"
        )

    # ---------- синхронизация сделок ----------

    def sync_fills(self):
        """Новая позиция без пары = buy-fill; исчезнувшая позиция пары = sell-fill."""
        positions = {p.ticket: p for p in gw.positions(storage.MAGIC)}
        open_pairs = storage.open_pairs()

        # 1) buy-fill: LONG-позиция без пары → пара + серверный TP.
        #    Аномалии: SELL-позиция с нашим magic (остаток старых багов) → закрыть немедленно.
        for ticket, p in positions.items():
            if p.type != mt5.POSITION_TYPE_BUY:
                log.warning("анормальная SELL-позиция #%s — закрываю", ticket)
                gw.close_position(ticket)
                continue
            if not storage.pair_by_buy(ticket):
                buy_price = p.price_open
                storage.pair_open(ticket, buy_price, p.volume)
                tp_price = round(buy_price + config.TP_USD, 2)
                gw.set_position_tp(ticket, tp_price)
                log.info("BUY FILLED #%s @ %.2f → TP %.2f (server-side)", ticket, buy_price, tp_price)
                storage.log_event("fill", f"buy #{ticket} @ {buy_price:.2f}, tp {tp_price:.2f}")
                from .notifier import send
                atr = self.atr_usd_per_hour()
                vol_note = f"волатильность ~${atr:.1f}/ч" if atr else ""
                send(f"🟢 УРОВЕНЬ СРАБОТАЛ | XAUUSD\n"
                     f"BUY @ {buy_price:.2f} (лот {p.volume})\n"
                     f"TP {tp_price:.2f} (+${config.TP_USD:.0f})\n"
                     f"⏱ {vol_note} · тейк ориентировочно {self.eta_hours(config.TP_USD)}\n"
                     f"Повторение: вход по рынку сейчас или лимит {buy_price:.2f}")

        # 2) sell-fill: пары, чьей позиции больше нет
        for pair in open_pairs:
            if pair["buy_ticket"] not in positions:
                deals = mt5.history_deals_get(position=pair["buy_ticket"]) or []
                out = [d for d in deals if d.entry == mt5.DEAL_ENTRY_OUT]
                if out:
                    d = out[-1]
                    costs = d.commission + d.swap + getattr(d, "fee", 0.0)
                    net = d.profit + costs
                    storage.pair_close(pair["buy_ticket"], d.order, d.price, round(net, 2),
                                       round(costs, 2))
                    s = storage.stats()
                    storage.log_event("close", f"pair #{pair['buy_ticket']} pnl {net:.2f}$")
                    log.info("PAIR CLOSED: buy#%s → %.2f, pnl %.2f$ (итого %.2f$)",
                             pair["buy_ticket"], d.price, net, s["realized_pnl"])
                    from .notifier import send
                    emoji = "✅" if net >= 0 else "🔻"
                    send(f"{emoji} ПАРА ЗАКРЫТА | XAUUSD\n"
                         f"Sell @ {d.price:.2f} · PnL {net:+.2f}$\n"
                         f"Итог: {s['realized_pnl']:+.2f}$ на {s['closed_pairs']} парах")

        # 3) ремонт: серверный TP пары отсутствует/сбит → восстановить.
        #    Если цена уже у цели (bid >= TP) — закрыть по рынку, это и есть тейк.
        for pair in open_pairs:
            p = positions.get(pair["buy_ticket"])
            if p is None or p.type != mt5.POSITION_TYPE_BUY:
                continue
            expected_tp = round(pair["buy_price"] + config.TP_USD, 2)
            if abs(p.tp - expected_tp) <= 0.01:
                continue
            bid, _ = gw.get_tick()
            if bid >= expected_tp:
                if gw.close_position(pair["buy_ticket"]):
                    log.info("TP AT MARKET: #%s bid %.2f >= tp %.2f",
                             pair["buy_ticket"], bid, expected_tp)
            else:
                if gw.set_position_tp(pair["buy_ticket"], expected_tp):
                    log.info("TP REPAIR: #%s tp %.2f → %.2f",
                             pair["buy_ticket"], p.tp, expected_tp)

    # ---------- weekend flat ----------

    def check_weekend(self):
        now = self._utcnow()
        flat = self.state["weekend_flat"]
        in_close_window = (now.weekday() == config.WEEKEND_CLOSE_DOW
                           and now.hour >= config.WEEKEND_CLOSE_HOUR_UTC)
        in_open_window = (now.weekday() == config.WEEKEND_OPEN_DOW
                          and now.hour >= config.WEEKEND_OPEN_HOUR_UTC)
        if in_close_window and not flat:
            for o in gw.open_orders(storage.MAGIC):
                gw.cancel(o.ticket)
            for p in gw.positions(storage.MAGIC):
                gw.close_position(p.ticket)
            for pair in storage.open_pairs():
                storage.pair_cancel(pair["buy_ticket"])
            self.state["weekend_flat"] = True
            storage.log_event("weekend", "флэт перед выходными: всё закрыто")
            from .notifier import send
            send("🗓 Weekend flat: пятничное закрытие — ордера отменены, позиции закрыты. "
                 "Воскресенье ~22:05 UTC — возобновление.")
            log.warning("WEEKEND FLAT applied")
            self._save()
        elif in_open_window and flat:
            self.state["weekend_flat"] = False
            self.state["day_anchor"] = None  # новый день
            storage.log_event("weekend", "выходные закончились, ресет")
            from .notifier import send
            send("🗓 Выходные закончились — торговля возобновляется, сетка будет восстановлена")
            log.info("Weekend over")
            self._save()

    # ---------- margin guard ----------

    def guard(self) -> bool:
        """Возвращает False если бот должен остановиться. None-счёт (терминал выключен) — просто ждём."""
        acc = gw.account()
        if acc is None:
            log.warning("guard: счёт недоступен (терминал выключен?) — пропуск цикла")
            return not self.state["halted"]
        now = self._utcnow()

        if self.state["watermark"] is None:
            self.state["watermark"] = acc["equity"]
        self.state["watermark"] = max(self.state["watermark"], acc["equity"])

        # дневной лимит убытка
        today = now.strftime("%Y-%m-%d")
        if self.state["day_anchor"] is None or self.state["day_anchor"][0] != today:
            self.state["day_anchor"] = [today, acc["balance"]]
        day_loss = (acc["equity"] - self.state["day_anchor"][1]) / self.state["day_anchor"][1]
        if day_loss <= -config.GUARD_DAILY_LOSS_PCT and not self.state["halted"]:
            self._halt(f"дневной лимит: {day_loss*100:.1f}%")

        # стоп по просадке equity от watermark
        dd = 1 - acc["equity"] / self.state["watermark"]
        if dd >= config.GUARD_EQUITY_DD_STOP and not self.state["halted"]:
            self._halt(f"просадка equity {dd*100:.1f}% ≥ {config.GUARD_EQUITY_DD_STOP*100:.0f}%")

        # margin level: усечь позиции
        if acc["margin_level"] and acc["margin_level"] < config.GUARD_MARGIN_LEVEL_MIN:
            for p in sorted(gw.positions(storage.MAGIC), key=lambda x: x.profit)[:1]:
                if gw.close_position(p.ticket):
                    storage.log_event("guard", f"margin level {acc['margin_level']:.0f}% → закрыта #{p.ticket}")
                    from .notifier import send
                    send(f"⚠️ Margin guard: уровень {acc['margin_level']:.0f}% < "
                         f"{config.GUARD_MARGIN_LEVEL_MIN:.0f}% — закрыта самая убыточная позиция #{p.ticket}")
                    log.warning("GUARD: closed #%s (margin level)", p.ticket)

        # снимок для дашборда: не чаще раза в минуту
        self.state.setdefault("last_snap", 0.0)
        now_ts = time.time()
        if now_ts - self.state["last_snap"] >= 60:
            self.state["last_snap"] = now_ts
            pos = gw.positions(storage.MAGIC)
            buys = self._buy_limits()
            try:
                trend = self.trend_change_pct()
            except Exception:  # noqa: BLE001
                trend = None
            storage.snapshot_insert(
                equity=acc["equity"], balance=acc["balance"],
                floating=round(sum(p.profit for p in pos), 2),
                margin_level=acc["margin_level"] or 0.0,
                positions=len(pos), buy_levels=len(buys),
                trend=trend,
                margin=acc["margin"], margin_free=acc["margin_free"],
                grid_low=min((o.price_open for o in buys), default=None),
                grid_high=max((o.price_open for o in buys), default=None),
            )

        self._save()
        return not self.state["halted"]

    def resume(self):
        """Ручной перезапуск после halt. Терминал может быть выключен —
        watchdog поднимет его, watermark переустановится на свежий equity."""
        self.state.update(halted=False, halted_reason="", watermark=None, day_anchor=None)
        self._save()
        storage.log_event("resume", "ручной перезапуск")
        from .notifier import send
        send("▶️ Бот возобновлён. Поднимаю терминал и восстанавливаю сетку...")

    def shutdown_terminal(self):
        """Полное выключение из Telegram: закрыть терминал MT5 (watchdog не воскресит — halted)."""
        import subprocess
        try:
            gw.shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            r = subprocess.run(["taskkill", "/F", "/IM", "terminal64.exe"],
                               capture_output=True, text=True, timeout=15)
            out = ((r.stdout or "") + (r.stderr or "")).strip()
            log.info("terminal kill: %s", out[:80])
        except Exception as e:  # noqa: BLE001
            log.warning("terminal kill failed: %s", e)

    def _halt(self, reason: str):
        for o in gw.open_orders(storage.MAGIC):
            gw.cancel(o.ticket)
        for p in gw.positions(storage.MAGIC):
            gw.close_position(p.ticket)
        self.state.update(halted=True, halted_reason=reason)
        storage.log_event("halt", reason)
        log.error("HALT: %s", reason)
        from .notifier import send
        send(f"🛑 СТОП: {reason}\nВсе ордера отменены, позиции закрыты.")
        self._save()

    # ---------- ежедневный дайджест ----------

    def daily_digest(self):
        now = self._utcnow()
        today = now.strftime("%Y-%m-%d")
        self.state.setdefault("last_digest", "")
        if now.hour < config.DIGEST_HOUR_UTC or self.state["last_digest"] == today:
            return
        acc = gw.account()
        s = storage.stats()
        trend = self.trend_change_pct()
        day_pnl = 0.0
        if self.state.get("day_anchor"):
            anchor = self.state["day_anchor"][1] or 1.0
            day_pnl = (acc["balance"] - anchor) / anchor * 100.0
        self.state["last_digest"] = today
        self._save()
        from .notifier import send
        lines = [
            "📅 Дайджест дня",
            f"Баланс: {acc['balance']:.2f} | Equity: {acc['equity']:.2f}",
            f"За день: {day_pnl:+.2f}% | Реализовано всего: {s['realized_pnl']:+.2f}$ ({s['closed_pairs']} пар)",
            f"Открытых пар: {s['open_pairs']} | Уровней в сетке: {len(self._buy_limits())}",
        ]
        if trend is not None:
            lines.append(f"Тренд 24ч: {trend:+.2f}%")
        send("\n".join(lines))

    # ---------- главный цикл ----------

    def run_once(self):
        self.check_weekend()
        if not self.guard():
            return
        self.check_trend()
        self.sync_fills()
        self.ensure_grid()
        self.daily_digest()

    def status(self) -> dict:
        acc = gw.account()
        s = storage.stats()
        buys = self._buy_limits()
        pos = gw.positions(storage.MAGIC)
        return {
            "account": acc,
            "realized_pnl": s["realized_pnl"],
            "closed_pairs": s["closed_pairs"],
            "open_pairs": s["open_pairs"],
            "buy_limits": len(buys),
            "positions": len(pos),
            "floating_pnl": round(sum(p.profit for p in pos), 2),
            "halted": self.state["halted"],
            "halted_reason": self.state["halted_reason"],
            "trend_paused": self.state["trend_paused"],
            "weekend_flat": self.state["weekend_flat"],
        }
