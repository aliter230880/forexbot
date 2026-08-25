# -*- coding: utf-8 -*-
"""Веб-панели:
  /       — публичная страница статистики (для подписчиков, «страница доверия»)
  /admin  — админка (BasicAuth: dim230880 / Dim_230880)

Управление ботом из админки — через data/cmd.json (исполняет сам цикл бота, без гонок).
"""
import base64
import json
import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from . import config, storage

app = FastAPI(title="Forex Grid Bot")


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    path = request.url.path
    if path.startswith("/admin") or path.startswith("/api/admin"):
        auth = request.headers.get("Authorization", "")
        ok = False
        if auth.startswith("Basic ") and config.ADMIN_PASS:
            try:
                user, _, pwd = base64.b64decode(auth[6:]).decode().partition(":")
                ok = (secrets.compare_digest(user, config.ADMIN_USER)
                      and secrets.compare_digest(pwd, config.ADMIN_PASS))
            except Exception:  # noqa: BLE001
                ok = False
        if not ok:
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="admin"'},
            )
    return await call_next(request)


@app.on_event("startup")
def _init():
    storage.init_db()


def _row(r) -> dict:
    return {k: r[k] for k in r.keys()} if r is not None else None


@app.get("/", response_class=HTMLResponse)
def index():
    html = (Path(__file__).parent / "static" / "user.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    html = (Path(__file__).parent / "static" / "admin.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


# ---------- публичные API ----------

@app.get("/api/summary")
def summary():
    state = storage.state_load()
    snap = _row(storage.snapshot_latest())
    if config.PROFILE == "SCALP":
        s = storage.scalp_stats()
        stats = {"realized_pnl": s["realized_pnl"], "total_costs": 0.0,
                 "closed_pairs": s["closed_trades"], "open_pairs": s["open_trades"],
                 "winrate": s["winrate"], "trades_today": s["trades_today"]}
        grid = {"levels": config.SCALP_MAX_TRADES_DAY, "step_usd": config.SCALP_TP_USD,
                "lot": config.SCALP_LOT, "tp_usd": config.SCALP_TP_USD}
    else:
        stats = storage.stats()
        grid = {"levels": config.GRID_LEVELS, "step_usd": config.GRID_STEP_USD,
                "lot": config.GRID_LOT, "tp_usd": config.TP_USD}
    return JSONResponse({
        "profile": config.PROFILE,
        "stats": stats,
        "snapshot": snap,
        "state": {
            "halted": state.get("halted", False),
            "halted_reason": state.get("halted_reason", ""),
            "trend_paused": state.get("trend_paused", False),
            "weekend_flat": state.get("weekend_flat", False),
        },
        "symbol": config.SYMBOL,
        "grid": grid,
    })


@app.get("/api/equity")
def equity(hours: int = 24):
    rows = storage.snapshot_history(hours)
    return JSONResponse([_row(r) for r in rows])


@app.get("/api/pairs")
def pairs(status: str = "closed", limit: int = 100):
    if config.PROFILE == "SCALP":
        if status == "open":
            return JSONResponse([
                {"buy_price": r["entry"], "lot": config.SCALP_LOT,
                 "open_time": r["open_time"], "buy_ticket": r["ticket"],
                 "side": r["side"], "sl": r["sl"], "tp": r["tp"],
                 "status": "open"} for r in storage.scalp_open_trades()])
        rows = storage.scalp_closed(limit)
        return JSONResponse([
            {"buy_price": r["entry"], "sell_price": r["exit"], "lot": config.SCALP_LOT,
             "pnl": r["pnl"], "costs": 0.0, "close_time": r["close_time"],
             "open_time": r["open_time"], "buy_ticket": r["ticket"], "side": r["side"],
             "reason": r["reason"], "status": r["status"]} for r in rows])
    if status == "open":
        rows = storage.open_pairs()
    else:
        rows = storage.closed_pairs(limit)
    return JSONResponse([_row(r) for r in rows])


@app.get("/api/events")
def events(limit: int = 30):
    return JSONResponse([_row(r) for r in storage.events_recent(limit)])


# ---------- админские API ----------

@app.get("/api/admin/subscribers")
def subscribers():
    try:
        data = json.loads((config.DATA_DIR / "telegram.json").read_text(encoding="utf-8"))
        subs = data.get("subscribers", [])
    except (FileNotFoundError, json.JSONDecodeError):
        subs = []
    return JSONResponse({"count": len(subs), "ids": subs})


@app.post("/api/admin/broadcast")
async def broadcast(request: Request):
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        return JSONResponse({"ok": False, "error": "пустой текст"}, status_code=400)
    cmd_file = config.DATA_DIR / "cmd.json"
    cmd_file.write_text(json.dumps({"action": "broadcast", "text": text}), encoding="utf-8")
    return JSONResponse({"ok": True, "queued": True})


@app.post("/api/admin/bot")
async def bot_control(request: Request):
    body = await request.json()
    action = body.get("action")
    if action not in ("start", "stop", "signals_on", "signals_off"):
        return JSONResponse({"ok": False, "error": "action: start|stop|signals_on|signals_off"},
                            status_code=400)
    cmd_file = config.DATA_DIR / "cmd.json"
    cmd_file.write_text(json.dumps({"action": action}), encoding="utf-8")
    return JSONResponse({"ok": True, "queued": action})


@app.get("/api/admin/multi")
def admin_multi():
    """Прогресс мульти-символьного бота (без трансляций, только для админки)."""
    import json as _json
    state_path = config.DATA_DIR / config.MULTI_STATE_FILE
    try:
        state = _json.loads(state_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, _json.JSONDecodeError):
        state = {}
    return JSONResponse({
        "running": (config.DATA_DIR / "multi.log").exists(),
        "symbols": [s.strip() for s in config.MULTI_SYMBOLS],
        "lot": config.MULTI_LOT,
        "test_balance": config.MULTI_TEST_BALANCE,
        "max_risk_pct": config.MULTI_MAX_RISK_PCT,
        "max_trades_day": config.MULTI_MAX_TRADES_DAY,
        "halted": state.get("halted", False),
        "halted_reason": state.get("halted_reason", ""),
        "paused_symbols": state.get("paused_symbols", {}),
        "skipped_risk": state.get("skipped_risk", {}),
        "stats": storage.multi_stats(),
        "open": [_row(r) for r in storage.multi_open_trades()],
        "closed": [_row(r) for r in storage.multi_closed(50)],
    })


@app.post("/api/admin/multi/control")
async def multi_control(request: Request):
    body = await request.json()
    action = body.get("action")
    if action not in ("start", "stop"):
        return JSONResponse({"ok": False, "error": "action: start|stop"}, status_code=400)
    (config.DATA_DIR / "cmd_multi.json").write_text(
        json.dumps({"action": action}), encoding="utf-8")
    return JSONResponse({"ok": True, "queued": action})


@app.get("/api/admin/scalp")
def admin_scalp():
    from . import storage as st
    state = st.state_load()
    return JSONResponse({
        "signals_on": bool(state.get("scalp_signals", False)),
        "stats": st.scalp_stats(),
        "open": [_row(r) for r in st.scalp_open_trades()],
        "closed": [_row(r) for r in st.scalp_closed(50)],
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=config.WEB_PORT, log_level="warning")
