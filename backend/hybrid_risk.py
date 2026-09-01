"""Pure risk calculations for the hybrid strategy.

The evaluator accepts plain numeric inputs so live checks and offline tests use
exactly the same rules as the order-placement path.
"""
from __future__ import annotations


def classify_close_reason(mt5_reason, *, tp_reason, sl_reason, expert_reason=None,
                           client_reason=None, scenario=None) -> str:
    """Map MT5 exit metadata to a stable strategy reason."""
    if mt5_reason == tp_reason:
        return "tp"
    if mt5_reason == sl_reason:
        return "sl"
    if scenario:
        return scenario
    if expert_reason is not None and mt5_reason == expert_reason:
        return "bot_close"
    if client_reason is not None and mt5_reason == client_reason:
        return "manual"
    return "unknown"


def symbol_risk_cap(symbol: str, default_cap: float, xau_cap: float) -> float:
    return xau_cap if symbol.upper().split(".", 1)[0] == "XAUUSD" else default_cap


def evaluate_metrics(
    *,
    symbol: str,
    atr: float,
    spread: float,
    contract_size: float,
    lot: float,
    point: float,
    stops_level: int,
    base: float,
    tp_atr: float,
    sl_atr: float,
    min_tp_spreads: float,
    max_spread_pct: float,
    max_pos_risk_pct: float,
    xau_max_pos_risk_pct: float,
) -> dict:
    """Return a deterministic risk verdict from broker/market metrics."""
    result = {
        "symbol": symbol,
        "ok": False,
        "reason": "",
        "atr": float(atr),
        "spread": float(spread),
        "tp_distance": 0.0,
        "sl_distance": 0.0,
        "tp_usd": 0.0,
        "sl_usd": 0.0,
        "risk_pct": 0.0,
        "spread_pct": 999.0,
        "risk_cap_pct": symbol_risk_cap(symbol, max_pos_risk_pct, xau_max_pos_risk_pct),
    }
    if atr <= 0:
        result["reason"] = "ATR M5 отсутствует или равен 0"
        return result
    if spread < 0 or contract_size <= 0 or lot <= 0 or base <= 0:
        result["reason"] = "некорректные брокерские/риск-параметры"
        return result

    tp_distance = max(atr * tp_atr, spread * min_tp_spreads)
    sl_distance = tp_distance * (sl_atr / tp_atr) if tp_atr > 0 else 0.0
    min_distance = stops_level * point + spread * 1.5
    tp_distance = max(tp_distance, min_distance)
    sl_distance = max(sl_distance, min_distance)
    pv = contract_size * lot
    result.update(
        tp_distance=tp_distance,
        sl_distance=sl_distance,
        tp_usd=tp_distance * pv,
        sl_usd=sl_distance * pv,
    )
    result["risk_pct"] = 100 * result["sl_usd"] / base
    result["spread_pct"] = 100 * spread / tp_distance if tp_distance > 0 else 999.0

    if result["risk_pct"] > result["risk_cap_pct"]:
        result["reason"] = (
            f"риск позиции {result['risk_pct']:.1f}% > "
            f"лимит {result['risk_cap_pct']:.1f}%"
        )
    elif result["spread_pct"] > max_spread_pct:
        result["reason"] = (
            f"спред {result['spread_pct']:.0f}% от цели > {max_spread_pct:.0f}%"
        )
    else:
        result["ok"] = True
    return result
