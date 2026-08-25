# -*- coding: utf-8 -*-
"""Тест гибрида сетка+скальпинг на депозите $100-150.

Ключевая идея гибрида:
  - вход ЛИМИТНЫМИ ордерами (как сетка) → не догоняем цену, спред платим один раз
    и он мал относительно цели ($0.35 от $4 = 8.7%, а не 25% как у скальп-цели $1.5)
  - жёсткий SL на позицию (как скальпинг) → нет бесконечной просадки сетки
  - тренд-фильтр H1 → сетка только по направлению тренда

Симуляция честная: маржа, свободные средства, max позиций, просадка, риск разорения.

Запуск: python -m backend.research_hybrid
"""
import sys
from itertools import product

import MetaTrader5 as mt5
import numpy as np

from . import config, mt5_gateway as gw
from .research import ema

SYMBOL = "XAUUSD.s"
BARS_M5 = 12000
LEVERAGE = 500
START_BALANCE = 100.0
LOT = 0.01


def load_data(symbol: str):
    info = mt5.symbol_info(symbol)
    if info is None:
        return None
    if not info.visible:
        mt5.symbol_select(symbol, True)
    m5 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 1, BARS_M5)
    h1 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 1, 1500)
    if m5 is None or h1 is None:
        return None
    import datetime
    t = np.array([b["time"] for b in m5])
    h1c = np.array([b["close"] for b in h1], float)
    h1t = np.array([b["time"] for b in h1])
    h1e = ema(h1c, 50)
    trend = []
    for ts in t:
        i = int(np.searchsorted(h1t, ts) - 1)
        if i < 5:
            trend.append("FLAT"); continue
        p, e, ep = h1c[i], h1e[i], h1e[i - 5]
        trend.append("UP" if (p > e and e > ep) else ("DOWN" if (p < e and e < ep) else "FLAT"))
    tick = mt5.symbol_info_tick(symbol)
    spread = (tick.ask - tick.bid) if tick and tick.ask > 0 else 0.35
    return {
        "c": np.array([b["close"] for b in m5], float),
        "h": np.array([b["high"] for b in m5], float),
        "l": np.array([b["low"] for b in m5], float),
        "hour": np.array([datetime.datetime.fromtimestamp(x, datetime.UTC).hour for x in t]),
        "dow": np.array([datetime.datetime.fromtimestamp(x, datetime.UTC).weekday() for x in t]),
        "h1": trend,
        "spread": spread,
        "contract": info.trade_contract_size,
        "days": len(m5) * 5 / 60 / 24 * (5 / 7),
    }


def simulate_hybrid(d, step, tp, sl, max_pos, trend_filter, balance=START_BALANCE):
    """Сетка лимитных buy-ордеров с жёстким SL на позицию.

    step  — расстояние между уровнями ($)
    tp    — тейк каждой позиции ($)
    sl    — стоп каждой позиции ($), None = без стопа (классическая сетка)
    """
    pv = d["contract"] * LOT            # USD за $1 движения (для XAUUSD = 1.0)
    margin_per = 0.0                    # посчитаем при первом входе
    equity = balance
    peak = balance
    max_dd_pct = 0.0
    positions = []                      # [(entry, tp_price, sl_price)]
    levels = []                         # активные лимит-уровни
    trades = []
    ruined = False
    last_rebuild = -999

    for i in range(60, len(d["c"]) - 1):
        price = d["c"][i]
        lo, hi = d["l"][i], d["h"][i]
        if margin_per == 0.0:
            margin_per = price * d["contract"] * LOT / LEVERAGE

        # --- обслуживание открытых позиций ---
        still = []
        for entry, tp_p, sl_p in positions:
            hit_tp = hi >= tp_p
            hit_sl = sl_p is not None and lo <= sl_p
            if hit_sl and hit_tp:
                hit_tp = False          # консервативно: сначала стоп
            if hit_tp:
                pnl = (tp_p - entry - d["spread"]) * pv
                equity += pnl
                trades.append(pnl)
            elif hit_sl:
                pnl = (sl_p - entry - d["spread"]) * pv
                equity += pnl
                trades.append(pnl)
            else:
                still.append((entry, tp_p, sl_p))
        positions = still

        # --- floating equity и просадка ---
        floating = sum((price - e) * pv for e, _, _ in positions)
        eq_now = equity + floating
        peak = max(peak, eq_now)
        dd = 100 * (peak - eq_now) / peak if peak else 0
        max_dd_pct = max(max_dd_pct, dd)
        if eq_now <= balance * 0.5:      # -50% = считаем разорением
            ruined = True
            break

        # --- маржа: сколько позиций можем себе позволить ---
        used = len(positions) * margin_per
        free = eq_now - used
        cap_by_margin = int(free / margin_per) if margin_per else 0
        room = min(max_pos - len(positions), max(0, cap_by_margin - 1))

        # --- торговое окно ---
        if d["dow"][i] >= 5 or not (6 <= d["hour"][i] < 20):
            continue
        if trend_filter and d["h1"][i] != "UP":
            continue

        # --- пересборка сетки вокруг цены ---
        if i - last_rebuild > 12 or not levels:
            levels = [round(price - step * k, 2) for k in range(1, max_pos + 1)]
            last_rebuild = i

        # --- исполнение лимиток ---
        for lv in list(levels):
            if room <= 0:
                break
            if lo <= lv:
                positions.append((lv, lv + tp, (lv - sl) if sl else None))
                levels.remove(lv)
                room -= 1

    final = equity + sum((d["c"][-1] - e) * pv for e, _, _ in positions)
    wins = [x for x in trades if x > 0]
    gross_w = sum(wins) or 0.0
    gross_l = abs(sum(x for x in trades if x <= 0)) or 1e-9
    return {
        "trades": len(trades),
        "per_day": len(trades) / d["days"],
        "winrate": 100 * len(wins) / len(trades) if trades else 0,
        "pnl": final - balance,
        "pnl_pct": 100 * (final - balance) / balance,
        "daily_pct": 100 * (final - balance) / balance / d["days"],
        "max_dd": max_dd_pct,
        "pf": gross_w / gross_l,
        "ruined": ruined,
    }


def main():
    if not gw.connect():
        sys.exit("нет связи с MT5")
    d = load_data(SYMBOL)
    if not d:
        sys.exit("нет данных")
    print(f"{SYMBOL}: {d['days']:.0f} торговых дней, спред ${d['spread']:.2f}, "
          f"$1 движения = ${d['contract'] * LOT:.2f} на лоте {LOT}")
    print(f"старт ${START_BALANCE:.0f}, плечо 1:{LEVERAGE}, "
          f"маржа/позиция ≈ ${d['c'][-1] * d['contract'] * LOT / LEVERAGE:.2f}\n")

    print(f"{'шаг':>5} {'TP':>5} {'SL':>6} {'поз':>4} {'тренд':>6} "
          f"{'сделок':>7} {'/день':>6} {'WR%':>6} {'PnL%':>8} {'%/день':>7} "
          f"{'просад%':>8} {'PF':>5}  итог")
    print("-" * 100)

    rows = []
    grid = product(
        [2.0, 3.0, 4.0, 6.0],        # шаг
        [None],                       # TP = шаг
        [None, 10.0, 15.0, 25.0],     # SL на позицию
        [3, 5, 8],                    # макс позиций
        [True, False],                # тренд-фильтр
    )
    for step, _, sl, mp, tf in grid:
        tp = step
        r = simulate_hybrid(d, step, tp, sl, mp, tf)
        rows.append((step, tp, sl, mp, tf, r))
        verdict = "РАЗОРЕНИЕ" if r["ruined"] else (
            "ЦЕЛЬ 7-10%!" if r["daily_pct"] >= 7 and r["max_dd"] < 40 else
            ("плюс" if r["pnl"] > 0 else "минус"))
        print(f"{step:>5.1f} {tp:>5.1f} {str(sl or '—'):>6} {mp:>4} "
              f"{'да' if tf else 'нет':>6} {r['trades']:>7} {r['per_day']:>6.1f} "
              f"{r['winrate']:>6.1f} {r['pnl_pct']:>8.1f} {r['daily_pct']:>7.2f} "
              f"{r['max_dd']:>8.1f} {r['pf']:>5.2f}  {verdict}")

    print()
    ok = [x for x in rows if not x[5]["ruined"] and x[5]["pnl"] > 0]
    ok.sort(key=lambda x: -x[5]["daily_pct"])
    print("=== ТОП по доходности (без разорения) ===")
    for step, tp, sl, mp, tf, r in ok[:6]:
        print(f"  шаг ${step:.0f} TP ${tp:.0f} SL {sl or 'нет':>4} поз {mp} "
              f"тренд {'да' if tf else 'нет'}: {r['per_day']:.1f} сд/день, "
              f"{r['daily_pct']:.2f}%/день, просадка {r['max_dd']:.0f}%, PF {r['pf']:.2f}")
    if not ok:
        print("  нет ни одной комбинации без разорения и с плюсом")
    gw.shutdown()


if __name__ == "__main__":
    main()
