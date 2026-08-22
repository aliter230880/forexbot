# -*- coding: utf-8 -*-
"""Локальный дашборд: python -m backend.webapp → http://localhost:8080

Читает только БД/state (не трогает MT5 — не воскресит терминал, если он закрыт).
"""
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from . import config, storage

app = FastAPI(title="Forex Grid Bot")


@app.on_event("startup")
def _init():
    storage.init_db()


def _row(r) -> dict:
    return {k: r[k] for k in r.keys()} if r is not None else None


@app.get("/", response_class=HTMLResponse)
def index():
    html = (Path(__file__).parent / "static" / "dashboard.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


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
        "infra_costs_month": 0.0,  # пока ПК локально; после VPS ~15
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8080, log_level="warning")
