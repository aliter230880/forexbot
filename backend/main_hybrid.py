# -*- coding: utf-8 -*-
"""Раннер гибрида (стратегия №3), отдельный процесс.

  python -m backend.main_hybrid run       — рабочий цикл
  python -m backend.main_hybrid status    — статус
  python -m backend.main_hybrid stop-all  — закрыть всё и halt
  python -m backend.main_hybrid risk      — риск-таблица по символам

Без трансляций в канал: прогресс в админке /admin.
"""
import json
import logging
import sys
import time

import MetaTrader5 as mt5

from . import config, mt5_gateway as gw, storage
from .hybrid_engine import HybridBot
from .hybrid_risk import evaluate_metrics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(config.DATA_DIR / "hybrid.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("main_hybrid")

POLL_SECONDS = 20


def _connect() -> HybridBot:
    if not gw.connect():
        log.error("нет связи с MT5, выход")
        sys.exit(1)
    return HybridBot()


def _handle_cmd(bot: HybridBot):
    cmd_file = config.DATA_DIR / "cmd_hybrid.json"
    if not cmd_file.exists():
        return
    try:
        cmd = json.loads(cmd_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        cmd_file.unlink(missing_ok=True)
        return
    cmd_file.unlink(missing_ok=True)
    action = cmd.get("action")
    log.info("cmd from admin: %s", action)
    if action == "stop":
        bot._halt("стоп из админ-панели")
    elif action == "start":
        bot.resume()


def cmd_run():
    storage.init_db()
    bot = _connect()
    symbols = bot.prepare_symbols()
    log.info("гибрид: %s | лот %s | шаг ATRx%s | SL ATRx%s | max поз %s/символ, %s всего | "
             "риск-база $%s (позиция ≤%s%%, корзина ≤%s%%)",
             symbols, config.HYBRID_LOT, config.HYBRID_STEP_ATR, config.HYBRID_SL_ATR,
             config.HYBRID_MAX_POS_PER_SYMBOL, config.HYBRID_MAX_POS_TOTAL,
             config.HYBRID_TEST_BALANCE, config.HYBRID_MAX_POS_RISK_PCT,
             config.HYBRID_MAX_BASKET_RISK_PCT)
    from .notifier import send_to, chat_id
    send_to(chat_id(), f"🤖 Гибрид запущен: {', '.join(symbols)} "
                       f"(лимитки + жёсткий SL, без трансляции)")
    while True:
        try:
            if mt5.terminal_info() is None or mt5.account_info() is None:
                log.warning("связь с MT5 потеряна, переподключение...")
                gw.shutdown()
                if gw.connect():
                    symbols = bot.prepare_symbols()
            _handle_cmd(bot)
            bot.run_once(symbols)
        except KeyboardInterrupt:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("цикл упал: %s", e)
        time.sleep(POLL_SECONDS)


def cmd_status():
    bot = _connect()
    bot.prepare_symbols()
    bot.sync()
    print(json.dumps(bot.status(), indent=2, ensure_ascii=False, default=str))
    gw.shutdown()


def cmd_stop_all():
    bot = _connect()
    bot._halt("stop-all вручную")
    print("гибрид: всё закрыто, halted")
    gw.shutdown()


def cmd_risk():
    bot = _connect()
    symbols = bot.prepare_symbols()
    time.sleep(3)
    print(f"{'symbol':11} {'ATR M5':>10} {'SL,$':>8} {'%базы':>7} {'TP,$':>7} "
          f"{'спред$':>8} {'спред/TP':>9}  вердикт")
    for sym in symbols:
        atr = bot.atr_m5(sym)
        pv = bot.usd_per_price_unit(sym)
        tick = mt5.symbol_info_tick(sym)
        if atr <= 0 or pv <= 0 or not tick:
            print(f"{sym:11} нет данных")
            continue
        info = mt5.symbol_info(sym)
        risk = evaluate_metrics(
            symbol=sym, atr=atr, spread=tick.ask - tick.bid,
            contract_size=info.trade_contract_size, lot=config.HYBRID_LOT,
            point=info.point, stops_level=info.trade_stops_level,
            base=config.HYBRID_TEST_BALANCE, tp_atr=config.HYBRID_TP_ATR,
            sl_atr=config.HYBRID_SL_ATR, min_tp_spreads=config.HYBRID_MIN_TP_SPREADS,
            max_spread_pct=config.HYBRID_MAX_SPREAD_PCT_OF_TP,
            max_pos_risk_pct=config.HYBRID_MAX_POS_RISK_PCT,
            xau_max_pos_risk_pct=config.HYBRID_XAU_MAX_POS_RISK_PCT,
        )
        v = "OK" if risk["ok"] else "ПРОПУСК (" + risk["reason"] + ")"
        spread_usd = risk["spread"] * info.trade_contract_size * config.HYBRID_LOT
        print(f"{sym:11} {atr:>10.4f} {risk['sl_usd']:>8.2f} {risk['risk_pct']:>6.1f}% "
              f"{risk['tp_usd']:>7.2f} {spread_usd:>8.3f} {risk['spread_pct']:>8.0f}%  {v}")
    print(f"\nмакс. позиций: {config.HYBRID_MAX_POS_PER_SYMBOL}/символ, "
          f"{config.HYBRID_MAX_POS_TOTAL} всего · риск корзины ≤ "
          f"{config.HYBRID_MAX_BASKET_RISK_PCT}% от ${config.HYBRID_TEST_BALANCE:.0f}")
    gw.shutdown()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"run": cmd_run, "status": cmd_status, "stop-all": cmd_stop_all,
     "risk": cmd_risk}[cmd]()
