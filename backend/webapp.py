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

ADMIN_USER = "dim230880"
ADMIN_PASS = "Dim_230880"

app = FastAPI(title="Forex Grid Bot")


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    path = request.url.path
    if path.startswith("/admin") or path.startswith("/api/admin"):
        auth = request.headers.get("Authorization", "")
        ok = False
        if auth.startswith("Basic "):
            try:
                user, _, pwd = base64.b64decode(auth[6:]).decode().partition(":")
                ok = secrets.compare_digest(user, ADMIN_USER) and secrets.compare_digest(pwd, ADMIN_PASS)
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
    s = storage.stats()
    snap = _row(storage.snapshot_latest())
    state = storage.state_load()
    return JSONResponse({
        "stats": s,
        "snapshot": snap,
        "state": {
            "halted": state.get("halted", False),
            "halted_reason": state.get("halted_reason", ""),
            "trend_paused": state.get("trend_paused", False),
            "weekend_flat": state.get("weekend_flat", False),
        },
        "symbol": config.SYMBOL,
        "grid": {
            "levels": config.GRID_LEVELS, "step_usd": config.GRID_STEP_USD,
            "lot": config.GRID_LOT, "tp_usd": config.TP_USD,
        },
    })


@app.get("/api/equity")
def equity(hours: int = 24):
    rows = storage.snapshot_history(hours)
    return JSONResponse([_row(r) for r in rows])


@app.get("/api/pairs")
def pairs(status: str = "closed", limit: int = 100):
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
    if action not in ("start", "stop"):
        return JSONResponse({"ok": False, "error": "action: start|stop"}, status_code=400)
    cmd_file = config.DATA_DIR / "cmd.json"
    cmd_file.write_text(json.dumps({"action": action}), encoding="utf-8")
    return JSONResponse({"ok": True, "queued": action})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")
