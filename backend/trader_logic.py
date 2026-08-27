# -*- coding: utf-8 -*-
"""Логика реального трейдера для Скальпера V3.

Четыре блока (все считаются из цены MT5, без внешних источников):
  1. Карта ликвидности: свинг-уровни + круглые числа + сбор стопов
     (ближайший честный аналог Coinglass для XAU — там ликвидаций золота нет)
  2. Азиатский тренд: направление сессии 23:00–03:00 UTC как направление дня
  3. Фигуры M15: голова-плечи + наклонный канал (zigzag-пики)
  4. RSI-дивергенция M5: цена новый лоу, RSI нет (и наоборот)

Каждый метод возвращает сигнал 'LONG'/'SHORT'/None + контекст.
Итоговое решение: совпадение ≥2 блоков (голосование), азиатский тренд — фильтр.
"""
import logging
from datetime import datetime, timezone

import MetaTrader5 as mt5
import numpy as np

log = logging.getLogger("trader")

SYMBOL = "XAUUSD.s"


def _bars(tf, n):
    d = mt5.copy_rates_from_pos(SYMBOL, tf, 1, n)
    return d if d is not None and len(d) > 30 else None


# ---------- 1. Карта ликвидности ----------

def liquidity_map():
    """Свинг-хаи/лоу M15 + круглые числа → ближайшие магниты ликвидности.

    Возврат: {'levels': [(price, kind)], 'nearest_above': p, 'nearest_below': p,
              'bias': 'LONG'/'SHORT'/None}
    Логика: рынок идёт собирать ближайший кластер стопов (магнит).
    """
    d = _bars(mt5.TIMEFRAME_M15, 120)
    if d is None:
        return None
    h = np.array([b["high"] for b in d], float)
    l = np.array([b["low"] for b in d], float)
    c = float(d["close"][-1])
    # свинги: локальные экстремумы окна 5
    swings = []
    for i in range(5, len(d) - 5):
        if h[i] == h[i - 5:i + 6].max():
            swings.append((float(h[i]), "swing_high"))
        if l[i] == l[i - 5:i + 6].min():
            swings.append((float(l[i]), "swing_low"))
    # круглые числа $50
    base = round(c / 50) * 50
    rounds = [(float(base + k * 50), "round") for k in (-2, -1, 0, 1, 2)]
    levels = sorted(set(swings + rounds))
    above = [p for p, _ in levels if p > c]
    below = [p for p, _ in levels if p < c]
    na = min(above) if above else None
    nb = max(below) if below else None
    bias = None
    if na and nb:
        # идём туда, где кластер ближе (магнит ликвидности)
        bias = "LONG" if (na - c) < (c - nb) else "SHORT"
    return {"levels": levels[-12:], "nearest_above": na, "nearest_below": nb,
            "bias": bias, "price": c}


# ---------- 2. Азиатский тренд ----------

def asian_trend():
    """Направление азиатской сессии 23:00–03:00 UTC сегодняшнего дня.

    Возврат: 'UP'/'DOWN'/None + диапазон. Трейдер: 'с 3-4 утра какой тренд —
    в таком и торгуем'. Утро трейдера (МСК+3) ≈ 23:00–03:00 UTC.
    """
    d = _bars(mt5.TIMEFRAME_M15, 400)
    if d is None:
        return None
    now = datetime.now(timezone.utc)
    # берём последние завершённые азиатские часы: ищем бары часа 23-02 UTC
    arr = []
    for b in d:
        t = datetime.fromtimestamp(b["time"], timezone.utc)
        if now.hour >= 3 and t.date() == (now.date() if now.hour >= 3 else now.date()):
            pass
        h = t.hour
        if h in (23, 0, 1, 2) and (now - t).total_seconds() < 36 * 3600:
            arr.append(b)
    if len(arr) < 8:
        return None
    o = float(arr[0]["open"])
    c = float(arr[-1]["close"])
    rng_h = max(float(b["high"]) for b in arr)
    rng_l = min(float(b["low"]) for b in arr)
    if c > o * 1.0003:
        tr = "UP"
    elif c < o * 0.9997:
        tr = "DOWN"
    else:
        tr = None
    return {"trend": tr, "open": o, "close": c,
            "range_high": rng_h, "range_low": rng_l}


# ---------- 3. Фигуры M15 ----------

def _zigzag(d, k=3):
    """Простые пики/впадины: чередование локальных экстремумов."""
    piv = []
    mode = None
    for i in range(2, len(d) - 2):
        h = float(d["high"][i]); l = float(d["low"][i])
        if h == max(float(x) for x in d["high"][max(0, i - k):i + k + 1]):
            if mode != "H":
                piv.append(("H", h, i)); mode = "H"
        elif l == min(float(x) for x in d["low"][max(0, i - k):i + k + 1]):
            if mode != "L":
                piv.append(("L", l, i)); mode = "L"
    return piv


def head_shoulders():
    """Голова-плечи (вершинa и дно) на M15, 150 баров.

    Возврат: {'signal': 'SHORT'/'LONG', 'neckline': p} | None
    """
    d = _bars(mt5.TIMEFRAME_M15, 150)
    if d is None:
        return None
    piv = _zigzag(d)
    if len(piv) < 5:
        return None
    # вершина: L H L H(golova, max) L H → SHORT по пробою шеи (последний L)
    for i in range(len(piv) - 4):
        s = piv[i:i + 5]
        if [p[0] for p in s] == ["L", "H", "L", "H", "L"]:
            ls = s[0][1], s[2][1], s[4][1]
            hs = s[1][1], s[3][1]
            if hs[1] > hs[0] and hs[1] > max(ls):
                neck = (ls[1] + ls[0] + ls[2]) / 3
                if float(d["close"][-1]) < neck:  # шея пробита вниз
                    return {"signal": "SHORT", "neckline": round(neck, 2)}
    # дно (инверсная): H L H L(golova, min) H → LONG
    for i in range(len(piv) - 4):
        s = piv[i:i + 5]
        if [p[0] for p in s] == ["H", "L", "H", "L", "H"]:
            hs = s[0][1], s[2][1], s[4][1]
            ls = s[1][1], s[3][1]
            if ls[1] < ls[0] and ls[1] < min(hs):
                neck = (hs[1] + hs[0] + hs[2]) / 3
                if float(d["close"][-1]) > neck:
                    return {"signal": "LONG", "neckline": round(neck, 2)}
    return None


def trend_channel():
    """Наклонный канал (наклонка) на M15: линейная регрессия хаёв/лоу.

    Пробой нижней границы вниз в нисходящем канале → SHORT,
    пробой верхней вверх в восходящем → LONG.
    """
    d = _bars(mt5.TIMEFRAME_M15, 100)
    if d is None:
        return None
    h = np.array([b["high"] for b in d[-60:]], float)
    l = np.array([b["low"] for b in d[-60:]], float)
    c = np.array([b["close"] for b in d[-60:]], float)
    x = np.arange(len(c), dtype=float)
    km, kb = np.polyfit(x, c, 1)          # наклон средней
    hu = np.polyfit(x, h, 1); lu = np.polyfit(x, l, 1)
    up_line = hu[0] * x[-1] + hu[1]        # верхняя граница сейчас
    dn_line = lu[0] * x[-1] + lu[1]
    slope = km
    px = float(c[-1])
    if slope < -0.02 and px < dn_line:     # нисходящий канал, пробой вниз
        return {"signal": "SHORT", "line": round(dn_line, 2), "slope": round(slope, 3)}
    if slope > 0.02 and px > up_line:      # восходящий, пробой вверх
        return {"signal": "LONG", "line": round(up_line, 2), "slope": round(slope, 3)}
    return None


# ---------- 4. RSI-дивергенция M5 ----------

def _rsi(closes, p=14):
    d = np.diff(closes)
    up = np.where(d > 0, d, 0.0)
    dn = np.where(d < 0, -d, 0.0)
    if len(up) < p + 2:
        return None
    au = up[-p:].mean(); ad = dn[-p:].mean()
    if ad == 0:
        return 100.0
    return 100 - 100 / (1 + au / ad)


def rsi_divergence():
    """Бычья: цена новый лоу, RSI выше прошлого лоу → LONG.
    Медвежья: цена новый хай, RSI ниже прошлого хая → SHORT."""
    d = _bars(mt5.TIMEFRAME_M5, 120)
    if d is None:
        return None
    c = np.array([b["close"] for b in d], float)
    l = np.array([b["low"] for b in d], float)
    h = np.array([b["high"] for b in d], float)
    # два последних локальных лоу/хая (окно 4)
    lows, highs = [], []
    for i in range(4, len(d) - 4):
        if l[i] == l[i - 4:i + 5].min():
            lows.append(i)
        if h[i] == h[i - 4:i + 5].max():
            highs.append(i)
    if len(lows) >= 2:
        i1, i2 = lows[-2], lows[-1]
        r1 = _rsi(c[:i1 + 1]); r2 = _rsi(c[:i2 + 1])
        if r1 and r2 and l[i2] < l[i1] and r2 > r1 + 2:
            return {"signal": "LONG", "rsi_prev": round(r1, 1), "rsi_now": round(r2, 1)}
    if len(highs) >= 2:
        i1, i2 = highs[-2], highs[-1]
        r1 = _rsi(c[:i1 + 1]); r2 = _rsi(c[:i2 + 1])
        if r1 and r2 and h[i2] > h[i1] and r2 < r1 - 2:
            return {"signal": "SHORT", "rsi_prev": round(r1, 1), "rsi_now": round(r2, 1)}
    return None


# ---------- Сводка ----------

def trader_signal():
    """Все блоки → голосование. Возврат dict с сигналами и итогом.

    Итог 'action': LONG/SHORT если ≥2 блока совпали, иначе None.
    """
    liq = liquidity_map()
    asia = asian_trend()
    hs = head_shoulders()
    ch = trend_channel()
    div = rsi_divergence()
    votes = {}
    for name, s in (("liq", liq.get("bias") if liq else None),
                    ("figure", (hs or {}).get("signal") if hs else None),
                    ("channel", (ch or {}).get("signal") if ch else None),
                    ("divergence", (div or {}).get("signal") if div else None)):
        if s in ("LONG", "SHORT"):
            votes[name] = s
    counts = {"LONG": 0, "SHORT": 0}
    for v in votes.values():
        counts[v] += 1
    action = None
    if counts["LONG"] >= 2 and counts["LONG"] > counts["SHORT"]:
        action = "LONG"
    elif counts["SHORT"] >= 2 and counts["SHORT"] > counts["LONG"]:
        action = "SHORT"
    # азиатский тренд — фильтр направления, не источник входа
    asia_tr = asia.get("trend") if asia else None
    if action and asia_tr:
        if action == "LONG" and asia_tr == "DOWN":
            action = None   # против утреннего тренда не торгуем
        if action == "SHORT" and asia_tr == "UP":
            action = None
    return {"action": action, "votes": votes, "asian_trend": asia_tr,
            "liquidity": {k: liq[k] for k in ("nearest_above", "nearest_below", "bias")} if liq else None,
            "figure": hs, "channel": ch, "divergence": div}


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    from backend import mt5_gateway as gw
    gw.connect()
    import json
    print(json.dumps(trader_signal(), indent=2, ensure_ascii=False, default=str))
    gw.shutdown()
