# -*- coding: utf-8 -*-
"""Раннер мульти-символьного бота (отдельный процесс от скальпера/сетки).

  python -m backend.main_multi run      — рабочий цикл
  python -m backend.main_multi status   — статус и выход
  python -m backend.main_multi stop-all  — закрыть позиции мульти-бота
  python -m backend.main_multi risk      — таблица риска стопа по символам

Без трансляций в Telegram-канал: прогресс смотреть в админке /admin.
"""
import json
import logging
import sys
import time

import MetaTrader5 as mt5

from . import config, mt5_gateway as gw, storage
from .multi_engine import MultiBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(config.DATA_DIR / "multi.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("main_multi")

POLL_SECONDS = 20


def _connect() -> MultiBot:
    if not gw.connect():
        log.error("нет связи с MT5, выход")
        sys.exit(1)
    return MultiBot()


def _handle_cmd(bot: MultiBot):
    """Команды из админки: data/cmd_multi.json."""
    cmd_file = config.DATA_DIR / "cmd_multi.json"
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
    log.info("мульти-бот: символы %s | лот %s | лимит %s/день | риск-база $%s (max %s%%)",
             symbols, config.MULTI_LOT, config.MULTI_MAX_TRADES_DAY,
             config.MULTI_TEST_BALANCE, config.MULTI_MAX_RISK_PCT)
    from .notifier import send_to, chat_id
    send_to(chat_id(), f"🤖 Мульти-бот запущен: {', '.join(symbols)} "
                       f"(без трансляции, статистика в админке)")
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
    bot.sync_closed()
    print(json.dumps(bot.status(), indent=2, ensure_ascii=False, default=str))
    gw.shutdown()


def cmd_stop_all():
    bot = _connect()
    bot._halt("stop-all вручную")
    print("позиции мульти-бота закрыты, бот halted")
    gw.shutdown()


def cmd_risk():
    """Таблица: сколько стоит стоп по каждому символу для мини-баланса."""
    bot = _connect()
    symbols = bot.prepare_symbols()
    time.sleep(3)
    print(f"{'symbol':12} {'ATR M5':>10} {'SL расст.':>10} {'SL, $':>8} "
          f"{'% от $' + str(int(config.MULTI_TEST_BALANCE)):>10}  вердикт")
    for sym in symbols:
        ctx = bot.m5_context(sym)
        if not ctx:
            print(f"{sym:12} нет данных")
            continue
        sl_dist = ctx["atr"] * config.SCALP_ATR_MULT_SL
        cost = bot.sl_cost_usd(sym, sl_dist)
        pct = 100 * cost / config.MULTI_TEST_BALANCE
        verdict = "OK" if pct <= config.MULTI_MAX_RISK_PCT else "ПРОПУСК (дорого)"
        print(f"{sym:12} {ctx['atr']:>10.4f} {sl_dist:>10.4f} {cost:>8.2f} {pct:>9.1f}%  {verdict}")
    gw.shutdown()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"run": cmd_run, "status": cmd_status, "stop-all": cmd_stop_all,
     "risk": cmd_risk}[cmd]()
