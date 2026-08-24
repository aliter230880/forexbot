# -*- coding: utf-8 -*-
"""Отрисовка зоны торговли: свечи M15 + уровни сетки → PNG для канала."""
import logging
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import MetaTrader5 as mt5  # noqa: E402

from . import config  # noqa: E402

log = logging.getLogger("chart")

BG = "#0d1117"
GRID = "#21262d"
GREEN = "#2ea043"
RED = "#f85149"
BLUE = "#388bfd"
GOLD = "#d29922"


def render_grid(levels: list[float], bid: float, out_path: Path,
                filled: list[float] | None = None) -> Path | None:
    """levels — цены buy-limit (по убыванию не обязательно), bid — текущая."""
    rates = mt5.copy_rates_from_pos(config.SYMBOL, mt5.TIMEFRAME_M15, 1, 60)
    if rates is None or len(rates) < 10:
        log.warning("нет свечей для графика")
        return None
    filled = filled or []

    fig, ax = plt.subplots(figsize=(12, 6.5), dpi=110)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    # свечи
    for i, r in enumerate(rates):
        up = r["close"] >= r["open"]
        color = GREEN if up else RED
        ax.plot([i, i], [r["low"], r["high"]], color=color, lw=0.8, zorder=2)
        ax.add_patch(plt.Rectangle((i - 0.35, min(r["open"], r["close"])), 0.7,
                                   max(abs(r["close"] - r["open"]), 0.01),
                                   facecolor=color, edgecolor=color, zorder=3))

    # уровни сетки
    for lv in levels:
        is_filled = any(abs(lv - f) < 0.3 for f in filled)
        ax.axhline(lv, color=GOLD if is_filled else "#8b949e",
                   lw=1.4 if is_filled else 0.9,
                   linestyle="--", alpha=0.95 if is_filled else 0.6, zorder=1)
        ax.annotate(f"{lv:.2f}", xy=(len(rates) + 1.5, lv), color=GOLD if is_filled else "#8b949e",
                    fontsize=8, va="center")

    # текущая цена
    ax.axhline(bid, color=BLUE, lw=1.2, zorder=4)
    ax.annotate(f"  {bid:.2f}", xy=(len(rates) + 1.5, bid), color=BLUE, fontsize=10,
                fontweight="bold", va="center")

    span = max(rates["high"]) - min(rates["low"])
    ax.set_ylim(min(min(rates["low"]), min(levels)) - 0.25 * span,
                max(bid, max(levels)) + 0.35 * span)
    ax.set_xlim(-1, len(rates) + 7)

    times = [datetime.fromtimestamp(r["time"], tz=timezone.utc).strftime("%H:%M") for r in rates]
    step = max(1, len(times) // 8)
    ax.set_xticks(range(0, len(times), step))
    ax.set_xticklabels(times[::step], color="#8b949e", fontsize=8)
    ax.tick_params(colors="#8b949e")
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.grid(color=GRID, lw=0.4, alpha=0.5)
    ax.set_title(f"XAUUSD · M15 · сетка {len(levels)} ур. · шаг ${config.GRID_STEP_USD:.0f} · "
                 f"@forex_vip_first", color="#c9d1d9", fontsize=11, loc="left", pad=10)
    fig.tight_layout()
    out_path.parent.mkdir(exist_ok=True)
    fig.savefig(out_path, facecolor=BG)
    plt.close(fig)
    return out_path
