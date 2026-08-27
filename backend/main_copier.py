# -*- coding: utf-8 -*-
"""Раннер копитрейдинга (отдельный процесс на счёте подписчика).

  python -m backend.main_copier run       — цикл (по умолчанию DRY_RUN=1)
  python -m backend.main_copier status    — состояние и статистика

Безопасность: по умолчанию COPIER_DRY_RUN=1 — ордера НЕ отправляются,
копир только логирует что бы он сделал. Для боевого включения:
  .env: COPIER_DRY_RUN=0, COPIER_LOGIN/PASSWORD/SERVER счёта подписчика,
        COPIER_MT5_PATH — путь ко ВТОРОМУ терминалу MT5.
"""
import json
import logging
import sys
import time

import MetaTrader5 as mt5

from . import config, storage
from . import copier_engine as ce

log = logging.getLogger("copier")


def cmd_run():
    storage.init_db()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(config.DATA_DIR / "copier.log",
                                      encoding="utf-8")])
    if not ce.connect():
        sys.exit(1)
    st = ce.state_load()
    log.info("копир запущен: мастера=%s dry_run=%s poll=%ss",
             config.COPIER_MASTERS, config.COPIER_DRY_RUN, config.COPIER_POLL_SEC)

    # первый запуск: не копировать накопленную историю — старт с текущего id
    if not st.get("last_master_id"):
        with storage.get_conn() as c:
            r = c.execute("SELECT COALESCE(MAX(id),0) n FROM scalp_trades").fetchone()
            st["last_master_id"] = r["n"]
            ce.state_save(st)
        log.info("первый запуск: старт с id=%s (историю не копируем)",
                 st["last_master_id"])

    while True:
        try:
            if mt5.terminal_info() is None:
                log.warning("копир: связь потеряна, переподключение...")
                mt5.shutdown()
                time.sleep(5)
                if not ce.connect():
                    time.sleep(30)
                    continue
            r = ce.run_once(st)
            if r["copied"]:
                log.info("копир цикл: новых мастеров %s, скопировано %s, "
                         "зеркалок открыто %s",
                         r["new_master"], r["copied"], r["open_copies"])
            time.sleep(config.COPIER_POLL_SEC)
        except KeyboardInterrupt:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("цикл копира упал: %s", e)
            time.sleep(30)


def cmd_status():
    storage.init_db()
    with storage.get_conn() as c:
        total = c.execute("SELECT COUNT(*) n FROM copy_trades").fetchone()["n"]
        closed = c.execute("""SELECT COUNT(*) n,
                                     COALESCE(SUM(copy_pnl),0) pnl,
                                     SUM(CASE WHEN copy_pnl>0 THEN 1 ELSE 0 END) w
                              FROM copy_trades WHERE status='closed'""").fetchone()
        open_n = c.execute(
            "SELECT COUNT(*) n FROM copy_trades WHERE status='open'").fetchone()["n"]
    st = ce.state_load()
    print(json.dumps({
        "dry_run": config.COPIER_DRY_RUN,
        "masters": config.COPIER_MASTERS,
        "last_master_id": st.get("last_master_id"),
        "copies_total": total,
        "copies_open": open_n,
        "copies_closed": closed["n"],
        "copy_pnl_total": round(closed["pnl"], 2),
        "copy_winrate": round(100 * (closed["w"] or 0) / closed["n"], 1)
                        if closed["n"] else 0.0,
        "max_lot": config.COPIER_MAX_LOT,
        "master_balance_base": config.COPIER_MASTER_BALANCE,
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    {"run": cmd_run, "status": cmd_status}[
        sys.argv[1] if len(sys.argv) > 1 else "status"]()
