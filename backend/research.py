# -*- coding: utf-8 -*-
"""Исследование стратегий на исторических данных.

Задача: найти логику входа, которая даёт МНОГО сделок и положительное
матожидание на депозите $100-150 (мин. лот 0.01).

Запуск:  python -m backend.research

Тестируются на одних и тех же данных:
  1. v2_ema      — текущая: тренд H1 + ADX + откат к EMA9 на M5
  2. fib         — откаты Фибоначчи 38.2/50/61.8 от импульса по тренду
  3. bb_revert   — mean reversion: касание полос Боллинджера + возврат
  4. donchian    — пробой канала Дончиана (breakout)
  5. vwap_dev    — отклонение от VWAP и возврат к нему
  6. rsi_extreme — RSI перепродан/перекуплен в сторону тренда H1
  7. ema_cross   — пересечение EMA9/EMA21 (частые входы)
  8. combo_hf    — высокочастотная: пробой микро-канала + импульс

Все выходы одинаковые: SL/TP по ATR (1:1.5) + трейлинг, учёт спреда.
Метрики: сделок/день, winrate, PnL на лоте 0.01, просадка, profit factor.
"""
import sys
from dataclasses import dataclass, field

import MetaTrader5 as mt5
import numpy as np

from . import config, mt5_gateway as gw

BARS_M5 = 12000        # ~41 день торгов (M5, 24/5)
ATR_SL_MULT = 1.2
ATR_TP_MULT = 1.8      # 1:1.5 — чаще берём профит
import os
TRAIL_ENABLE = os.getenv("TRAIL", "1") == "1"


# ---------- индикаторы ----------

def ema(a: np.ndarray, p: int) -> np.ndarray:
    k = 2.0 / (p + 1)
    out = np.empty_like(a)
    out[0] = a[0]
    for i in range(1, len(a)):
        out[i] = a[i] * k + out[i - 1] * (1 - k)
    return out


def rsi_series(c: np.ndarray, p: int = 14) -> np.ndarray:
    out = np.full(len(c), 50.0)
    d = np.diff(c)
    for i in range(p, len(c)):
        w = d[i - p:i]
        g = w[w > 0].sum() / p
        l = -w[w < 0].sum() / p
        out[i] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return out


def atr_series(h, l, c, p: int = 14) -> np.ndarray:
    tr = np.zeros(len(c))
    for i in range(1, len(c)):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    out = np.zeros(len(c))
    for i in range(p, len(c)):
        out[i] = tr[i - p + 1:i + 1].mean()
    return out


def adx_series(h, l, c, p: int = 14) -> np.ndarray:
    out = np.zeros(len(c))
    pdm = np.zeros(len(c)); mdm = np.zeros(len(c)); tr = np.zeros(len(c))
    for i in range(1, len(c)):
        up, dn = h[i] - h[i - 1], l[i - 1] - l[i]
        pdm[i] = up if up > dn and up > 0 else 0.0
        mdm[i] = dn if dn > up and dn > 0 else 0.0
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    for i in range(p * 2, len(c)):
        a = tr[i - p + 1:i + 1].mean()
        if a == 0:
            continue
        pdi = 100 * pdm[i - p + 1:i + 1].mean() / a
        mdi = 100 * mdm[i - p + 1:i + 1].mean() / a
        if pdi + mdi:
            out[i] = 100 * abs(pdi - mdi) / (pdi + mdi)
    return out


def bollinger(c: np.ndarray, p: int = 20, k: float = 2.0):
    """Полосы Боллинджера БЕЗ загляда в будущее.

    np.convolve(mode="same") центрирует окно и подмешивает БУДУЩИЕ бары —
    это look-ahead bias, из-за которого mean-reversion показывала фальшивый плюс.
    """
    mid = np.copy(c)
    std = np.zeros(len(c))
    for i in range(p, len(c)):
        w = c[i - p + 1:i + 1]
        mid[i] = w.mean()
        std[i] = w.std()
    return mid, mid + k * std, mid - k * std


def vwap_series(h, l, c, vol, p: int = 40) -> np.ndarray:
    tp = (h + l + c) / 3
    out = np.copy(c)
    for i in range(p, len(c)):
        v = vol[i - p:i]
        out[i] = (tp[i - p:i] * v).sum() / v.sum() if v.sum() else c[i]
    return out


# ---------- результат ----------

@dataclass
class Result:
    name: str
    trades: list = field(default_factory=list)

    def add(self, pnl: float):
        self.trades.append(pnl)

    def report(self, days: float) -> dict:
        t = self.trades
        if not t:
            return {"name": self.name, "trades": 0}
        wins = [x for x in t if x > 0]
        losses = [x for x in t if x <= 0]
        gross_w = sum(wins) or 0.0
        gross_l = abs(sum(losses)) or 1e-9
        eq = np.cumsum(t)
        dd = float(np.max(np.maximum.accumulate(eq) - eq)) if len(eq) else 0.0
        return {
            "name": self.name,
            "trades": len(t),
            "per_day": len(t) / days,
            "winrate": 100 * len(wins) / len(t),
            "pnl": sum(t),
            "avg": float(np.mean(t)),
            "pf": gross_w / gross_l,
            "dd": dd,
        }


# ---------- ядро бэктеста ----------

def simulate(sig_fn, data: dict, spread: float, point_value: float, name: str) -> Result:
    """sig_fn(i, d) -> 'LONG' | 'SHORT' | None. Выход: ATR-стопы + трейлинг."""
    res = Result(name)
    d = data
    n = len(d["c"])
    pos = None
    cooldown_until = 0
    for i in range(210, n - 1):
        if pos:
            side, entry, sl, tp, peak, risk0 = pos
            hi, lo = d["h"][i], d["l"][i]
            favor = (hi - entry) if side == "LONG" else (entry - lo)
            peak = max(peak, favor)
            # трейлинг от ИСХОДНОЙ дистанции риска (risk0), а не от текущего SL
            if TRAIL_ENABLE and risk0 > 0:
                start = risk0 * 0.8
                if peak >= start:
                    lock = risk0 * 0.2 + (peak - start) * 0.5
                    nsl = entry + lock if side == "LONG" else entry - lock
                    sl = max(sl, nsl) if side == "LONG" else min(sl, nsl)
            hit_sl = (lo <= sl) if side == "LONG" else (hi >= sl)
            hit_tp = (hi >= tp) if side == "LONG" else (lo <= tp)
            if hit_sl or hit_tp:
                px = sl if hit_sl else tp
                raw = (px - entry) if side == "LONG" else (entry - px)
                res.add((raw - spread) * point_value)
                pos = None
                cooldown_until = i + 2
            else:
                pos = (side, entry, sl, tp, peak, risk0)
            continue
        if i < cooldown_until:
            continue
        hour = d["hour"][i]
        if not (6 <= hour < 20):
            continue
        side = sig_fn(i, d)
        if not side:
            continue
        a = d["atr"][i]
        if a <= 0:
            continue
        sl_d = a * ATR_SL_MULT
        tp_d = a * ATR_TP_MULT
        px = d["c"][i]
        pos = (side, px,
               px - sl_d if side == "LONG" else px + sl_d,
               px + tp_d if side == "LONG" else px - tp_d, 0.0, sl_d)
    return res


# ---------- стратегии ----------

def s_v2_ema(i, d):
    if d["h1"][i] == "FLAT" or d["adx"][i] < 22:
        return None
    if (d["h1"][i] == "UP" and d["ef"][i] > d["es"][i]
            and d["l"][i] <= d["ef"][i] <= d["c"][i] and 45 <= d["rsi"][i] <= 72):
        return "LONG"
    if (d["h1"][i] == "DOWN" and d["ef"][i] < d["es"][i]
            and d["h"][i] >= d["ef"][i] >= d["c"][i] and 28 <= d["rsi"][i] <= 55):
        return "SHORT"
    return None


def s_fib(i, d):
    """Откат Фибоначчи 38.2-61.8% от последнего импульса, по тренду H1."""
    look = 30
    if d["h1"][i] == "FLAT":
        return None
    seg_h = d["h"][i - look:i].max()
    seg_l = d["l"][i - look:i].min()
    rng = seg_h - seg_l
    if rng <= 0 or d["atr"][i] <= 0 or rng < d["atr"][i] * 2:
        return None
    p = d["c"][i]
    if d["h1"][i] == "UP":
        f382, f618 = seg_h - rng * 0.382, seg_h - rng * 0.618
        # цена в зоне отката и разворачивается вверх
        if f618 <= p <= f382 and d["c"][i] > d["c"][i - 1] and d["rsi"][i] > 40:
            return "LONG"
    else:
        f382, f618 = seg_l + rng * 0.382, seg_l + rng * 0.618
        if f382 <= p <= f618 and d["c"][i] < d["c"][i - 1] and d["rsi"][i] < 60:
            return "SHORT"
    return None


def s_bb_revert(i, d):
    """Mean reversion: закрытие за полосой, вход на возврат внутрь."""
    if d["c"][i - 1] < d["bl"][i - 1] and d["c"][i] > d["bl"][i] and d["rsi"][i] < 45:
        return "LONG"
    if d["c"][i - 1] > d["bu"][i - 1] and d["c"][i] < d["bu"][i] and d["rsi"][i] > 55:
        return "SHORT"
    return None


def s_donchian(i, d):
    """Пробой канала Дончиана (20 бар) в сторону тренда H1."""
    p = 20
    hh = d["h"][i - p:i].max()
    ll = d["l"][i - p:i].min()
    if d["c"][i] > hh and d["h1"][i] != "DOWN" and d["adx"][i] > 20:
        return "LONG"
    if d["c"][i] < ll and d["h1"][i] != "UP" and d["adx"][i] > 20:
        return "SHORT"
    return None


def s_vwap_dev(i, d):
    """Отклонение от VWAP > 1 ATR и возврат."""
    dev = d["c"][i] - d["vwap"][i]
    if d["atr"][i] <= 0:
        return None
    z = dev / d["atr"][i]
    if z < -1.0 and d["c"][i] > d["c"][i - 1]:
        return "LONG"
    if z > 1.0 and d["c"][i] < d["c"][i - 1]:
        return "SHORT"
    return None


def s_rsi_extreme(i, d):
    """RSI-экстремум в сторону старшего тренда."""
    if d["h1"][i] == "UP" and d["rsi"][i - 1] < 32 <= d["rsi"][i]:
        return "LONG"
    if d["h1"][i] == "DOWN" and d["rsi"][i - 1] > 68 >= d["rsi"][i]:
        return "SHORT"
    return None


def s_ema_cross(i, d):
    """Пересечение EMA9/EMA21 — много сделок."""
    if d["ef"][i - 1] <= d["es"][i - 1] and d["ef"][i] > d["es"][i]:
        return "LONG"
    if d["ef"][i - 1] >= d["es"][i - 1] and d["ef"][i] < d["es"][i]:
        return "SHORT"
    return None


def s_combo_hf(i, d):
    """Высокочастотная: микро-пробой 6 бар + импульс + ADX любой, без H1-фильтра."""
    p = 6
    hh = d["h"][i - p:i].max()
    ll = d["l"][i - p:i].min()
    body = abs(d["c"][i] - d["o"][i])
    if body < d["atr"][i] * 0.3:
        return None
    if d["c"][i] > hh and d["c"][i] > d["o"][i]:
        return "LONG"
    if d["c"][i] < ll and d["c"][i] < d["o"][i]:
        return "SHORT"
    return None


STRATEGIES = [
    ("v2_ema", s_v2_ema), ("fib", s_fib), ("bb_revert", s_bb_revert),
    ("donchian", s_donchian), ("vwap_dev", s_vwap_dev),
    ("rsi_extreme", s_rsi_extreme), ("ema_cross", s_ema_cross),
    ("combo_hf", s_combo_hf),
]


def load(symbol: str) -> tuple[dict, float, float, float] | None:
    info = mt5.symbol_info(symbol)
    if info is None:
        return None
    if not info.visible:
        mt5.symbol_select(symbol, True)
    m5 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 1, BARS_M5)
    h1 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 1, 1500)
    if m5 is None or h1 is None or len(m5) < 500:
        return None
    import datetime
    c = np.array([b["close"] for b in m5], float)
    o = np.array([b["open"] for b in m5], float)
    h = np.array([b["high"] for b in m5], float)
    l = np.array([b["low"] for b in m5], float)
    v = np.array([b["tick_volume"] for b in m5], float)
    t = np.array([b["time"] for b in m5])
    h1c = np.array([b["close"] for b in h1], float)
    h1t = np.array([b["time"] for b in h1])
    h1e = ema(h1c, 50)
    trend = []
    for ts in t:
        idx = int(np.searchsorted(h1t, ts) - 1)
        if idx < 5:
            trend.append("FLAT"); continue
        p_, e_, ep_ = h1c[idx], h1e[idx], h1e[idx - 5]
        trend.append("UP" if (p_ > e_ and e_ > ep_) else ("DOWN" if (p_ < e_ and e_ < ep_) else "FLAT"))
    mid, bu, bl = bollinger(c)
    d = {
        "c": c, "o": o, "h": h, "l": l, "v": v,
        "hour": np.array([datetime.datetime.fromtimestamp(x, datetime.UTC).hour for x in t]),
        "dow": np.array([datetime.datetime.fromtimestamp(x, datetime.UTC).weekday() for x in t]),
        "ef": ema(c, 9), "es": ema(c, 21),
        "rsi": rsi_series(c, 14), "atr": atr_series(h, l, c, 14),
        "adx": adx_series(h, l, c, 14), "h1": trend,
        "bu": bu, "bl": bl, "bm": mid,
        "vwap": vwap_series(h, l, c, v, 40),
    }
    tick = mt5.symbol_info_tick(symbol)
    spread = (tick.ask - tick.bid) if tick and tick.ask > 0 else info.spread * info.point
    point_value = info.trade_contract_size * 0.01   # USD за 1.0 движения цены на лоте 0.01
    days = len(m5) * 5 / 60 / 24 * (5 / 7)          # только торговые дни
    return d, spread, point_value, days


def main():
    if not gw.connect():
        sys.exit("нет связи с MT5")
    symbols = [s.strip() for s in config.MULTI_SYMBOLS] + ["XAUUSD.s"]
    grand = {name: Result(name) for name, _ in STRATEGIES}
    grand_days = 0.0
    print(f"{'symbol':11} {'strategy':12} {'trades':>7} {'/день':>6} {'WR%':>6} "
          f"{'PnL$':>9} {'ср.':>7} {'PF':>5} {'DD$':>7}")
    print("-" * 82)
    for sym in symbols:
        loaded = load(sym)
        if not loaded:
            print(f"{sym:11} нет данных")
            continue
        d, spread, pv, days = loaded
        grand_days = max(grand_days, days)
        for name, fn in STRATEGIES:
            r = simulate(fn, d, spread, pv, name)
            rep = r.report(days)
            if rep["trades"] == 0:
                print(f"{sym:11} {name:12} {'0':>7}")
                continue
            print(f"{sym:11} {name:12} {rep['trades']:>7} {rep['per_day']:>6.1f} "
                  f"{rep['winrate']:>6.1f} {rep['pnl']:>9.2f} {rep['avg']:>7.3f} "
                  f"{rep['pf']:>5.2f} {rep['dd']:>7.2f}")
            grand[name].trades.extend(r.trades)
        print("-" * 82)
    print()
    print("=== ИТОГО ПО ВСЕМ ИНСТРУМЕНТАМ (портфель, лот 0.01) ===")
    print(f"{'strategy':12} {'trades':>7} {'/день':>6} {'WR%':>6} {'PnL$':>9} "
          f"{'ср.':>7} {'PF':>5} {'DD$':>7}  вердикт")
    rows = [grand[n].report(grand_days) for n, _ in STRATEGIES if grand[n].trades]
    rows.sort(key=lambda r: -r["pnl"])
    for r in rows:
        v = "ГОДНА" if r["pnl"] > 0 and r["pf"] > 1.15 else ("слабо" if r["pnl"] > 0 else "УБЫТОК")
        print(f"{r['name']:12} {r['trades']:>7} {r['per_day']:>6.1f} {r['winrate']:>6.1f} "
              f"{r['pnl']:>9.2f} {r['avg']:>7.3f} {r['pf']:>5.2f} {r['dd']:>7.2f}  {v}")
    print(f"\nпериод: ~{grand_days:.0f} торговых дней, спред учтён, трейлинг включён")
    gw.shutdown()


if __name__ == "__main__":
    main()
