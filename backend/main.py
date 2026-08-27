# -*- coding: utf-8 -*-
"""Раннер forex-грид-бота. Режимы:
  python -m backend.main status     — показать статус и выйти
  python -m backend.main dry-run    — план сетки без установки ордеров
  python -m backend.main run        — рабочий цикл
  python -m backend.main stop-all   — отменить ордера/закрыть позиции
"""
import json
import logging
import sys
import time

import MetaTrader5 as mt5

from . import config, mt5_gateway as gw, storage
from .grid_engine import GridBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(config.DATA_DIR / "bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("main")


def _connect_and_prepare():
    if not gw.connect():
        log.error("cannot connect to MT5, exit")
        sys.exit(1)
    spec = gw.ensure_symbol()
    log.info("symbol %s spec: %s", config.SYMBOL, spec)


def make_bot():
    if config.PROFILE == "SCALP":
        from .scalp_engine import ScalpBot
        return ScalpBot()
    return GridBot()


def cmd_status():
    _connect_and_prepare()
    bot = make_bot()
    if config.PROFILE != "SCALP":
        bot.sync_fills()
    else:
        bot.sync_closed()
    print(json.dumps(bot.status(), indent=2, ensure_ascii=False, default=str))
    gw.shutdown()


def cmd_dry_run():
    _connect_and_prepare()
    bid, ask = gw.get_tick()
    print(f"Цена: bid={bid} ask={ask} (спред ${(ask-bid):.2f})")
    print(f"План сетки: {config.GRID_LEVELS} BUY LIMIT по {config.GRID_LOT} лот, "
          f"шаг ${config.GRID_STEP_USD}, TP ${config.TP_USD}:")
    for i in range(1, config.GRID_LEVELS + 1):
        price = round(bid - config.GRID_STEP_USD * i, 2)
        print(f"  уровень {i:2d}: buy @ {price:.2f} → tp @ {price + config.TP_USD:.2f}")
    margin_per_level = bid * 100 * config.GRID_LOT / 500  # контракт 100 oz, плечо 1:500
    total_margin = margin_per_level * config.GRID_LEVELS
    print(f"Маржа: ~${margin_per_level:.2f}/уровень, всего ~${total_margin:.0f} из "
          f"${gw.account()['balance']:.0f}")
    bot = GridBot()
    print("trend 24h:", bot.trend_change_pct(), "%")
    gw.shutdown()


def handle_cmd_file(bot):
    """Команды из админ-панели (data/cmd.json) исполняет сам цикл бота — без гонок."""
    import json as _json
    cmd_file = config.DATA_DIR / "cmd.json"
    if not cmd_file.exists():
        return
    try:
        cmd = _json.loads(cmd_file.read_text(encoding="utf-8"))
    except _json.JSONDecodeError:
        cmd_file.unlink(missing_ok=True)
        return
    cmd_file.unlink(missing_ok=True)
    action = cmd.get("action")
    log.info("cmd from admin panel: %s", action)
    if action == "stop":
        bot._halt("стоп из админ-панели")
        bot.shutdown_terminal()
    elif action == "start":
        bot.resume()
    elif action == "broadcast":
        from .notifier import send
        send("📢 " + cmd.get("text", ""))
    elif action == "signals_on":
        bot.state["scalp_signals"] = True
        storage.state_save(bot.state)
        from .notifier import send_to, chat_id
        send_to(chat_id(), "📡 Трансляция скальп-сигналов в канал ВКЛЮЧЕНА")
    elif action == "signals_off":
        bot.state["scalp_signals"] = False
        storage.state_save(bot.state)
        from .notifier import send_to, chat_id
        send_to(chat_id(), "🔇 Трансляция скальп-сигналов в канал ВЫКЛЮЧЕНА")


def cmd_run():
    storage.init_db()
    _connect_and_prepare()
    bot = make_bot()
    from .notifier import send, poll_commands, send_to, chat_id
    send_to(chat_id(), f"🤖 Бот запущен (профиль {config.PROFILE})")
    log.info("старт цикла: poll %ss, профиль %s", config.POLL_SECONDS, config.PROFILE)
    while True:
        try:
            # watchdog: терминал мог закрыться — переподключаемся,
            # но НЕ воскрешаем терминал после /stop (юзер мог закрыть его сам)
            if (mt5.terminal_info() is None or mt5.account_info() is None) and not bot.state.get("halted"):
                log.warning("MT5 связь потеряна, переподключение...")
                gw.shutdown()
                if not gw.connect():
                    from .notifier import send as tg
                    tg("⚠️ MT5 недоступен, повтор через цикл")
                else:
                    gw.ensure_symbol()
            handle_cmd_file(bot)
            bot.run_once()
        except KeyboardInterrupt:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("цикл упал: %s", e)
        # Telegram-команды обрабатываем ВСЕГДА, независимо от состояния торговли
        try:
            poll_commands(bot)
        except Exception as e:  # noqa: BLE001
            log.warning("telegram poll failed: %s", e)
        time.sleep(config.POLL_SECONDS)


def cmd_stop_all():
    _connect_and_prepare()
    n_orders = len(gw.open_orders(storage.MAGIC))
    for o in gw.open_orders(storage.MAGIC):
        gw.cancel(o.ticket)
    pos = gw.positions(storage.MAGIC)
    for p in pos:
        gw.close_position(p.ticket)
    for pair in storage.open_pairs():
        storage.pair_cancel(pair["buy_ticket"])
    st = storage.state_load()
    st.update(halted=True, halted_reason="stop-all вручную")
    storage.state_save(st)
    print(f"отменено ордеров: {n_orders}, закрыто позиций: {len(pos)}, бот halted")
    gw.shutdown()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"status": cmd_status, "dry-run": cmd_dry_run, "run": cmd_run,
     "stop-all": cmd_stop_all}[cmd]()
