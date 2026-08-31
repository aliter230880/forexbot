# -*- coding: utf-8 -*-
"""Ограничение Telegram-команд владельцем + /stop не выключает терминал на live.

Проблема (критично для live):
  - бот в режиме открытого доступа: любой написавший получает /stop и /start;
  - /stop вызывает shutdown_terminal() → taskkill /F /IM terminal64.exe, то есть
    убивает ОБЩИЙ терминал, которым торгует гибрид;
  - /start поднимает СЕТКУ на том же счёте (противоречит «на live только гибрид»).

Что делает патч (идемпотентно, повторный запуск ничего не портит):
  1) config.py:  + TELEGRAM_OWNER_ID (env, дефолт 789368186)
  2) notifier.py: /status и /help остаются публичными;
                  /stop и /start — только владельцу;
                  /stop при TRADING_MODE=real не гасит терминал (только halt).

Запуск:
    .venv\\Scripts\\python.exe live\\patch_tg_owner.py --dry-run
    .venv\\Scripts\\python.exe live\\patch_tg_owner.py --apply
Бэкапы: рядом с файлами, <имя>.bak_<timestamp>
"""
import argparse
import io
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "backend" / "config.py"
NOTIFIER = ROOT / "backend" / "notifier.py"

# Якорь = строка целиком, с хвостовым комментарием (config.py:198): замена по
# короткому префиксу вставила бы блок СРЕДИ строки и уносила комментарий на строку
# TELEGRAM_OWNER_ID.
CFG_ANCHOR = ('TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL", "@forex_vip_first")'
              '  # пусто → рассылка подписчикам')
CFG_ADD = '''
# Владелец бота: только он может /stop и /start (на live посторонний стоп критичен)
TELEGRAM_OWNER_ID = os.getenv("TELEGRAM_OWNER_ID", "789368186").strip()'''

OLD_START = '''        if text.startswith("/start"):
            if bot.state.get("halted"):
                bot.resume()
            else:
                send_to(cid, "✅ Бот уже активен. /status — сводка")'''

NEW_START = '''        is_owner = (not config.TELEGRAM_OWNER_ID) or cid == config.TELEGRAM_OWNER_ID
        if text.startswith("/start"):
            if not is_owner:
                send_to(cid, "🔒 Управление ботом доступно только владельцу.\\n"
                             "Тебе доступны: /status /help")
                continue
            if bot.state.get("halted"):
                bot.resume()
            else:
                send_to(cid, "✅ Бот уже активен. /status — сводка")'''

OLD_STOP = '''        elif text.startswith("/stop"):
            bot._halt("команда /stop из Telegram")
            bot.shutdown_terminal()
            send_to(cid, "🛑 Полный стоп: ордера отменены, позиции закрыты, терминал выключен.\\n"
                         "▶️ /start — поднимет терминал и сетку заново")'''

NEW_STOP = '''        elif text.startswith("/stop"):
            if not is_owner:
                send_to(cid, "🔒 Управление ботом доступно только владельцу.\\n"
                             "Тебе доступны: /status /help")
                continue
            bot._halt("команда /stop из Telegram")
            # На реальном счёте терминал НЕ гасим: он общий с гибридом,
            # его выключение обрывает live-торговлю и связь с брокером.
            if config.TRADING_MODE != "real":
                bot.shutdown_terminal()
                send_to(cid, "🛑 Полный стоп: ордера отменены, позиции закрыты, "
                             "терминал выключен.\\n▶️ /start — поднимет заново")
            else:
                send_to(cid, "🛑 Стоп: ордера отменены, позиции закрыты, бот в halt.\\n"
                             "Терминал оставлен включённым (LIVE-режим).\\n"
                             "▶️ /start — возобновить")'''


OLD_HELP = '''        elif text.startswith("/help"):
            _m = "ДЕМО" if config.TRADING_MODE != "real" else "РЕАЛ"
            send_to(cid, f"🤖 Gibrid-bot — {_m} (Forex, PU Prime)\\n"
                         "/status — состояние\\n/start — возобновить после стопа\\n/stop — полный стоп\\n"
                         "Все, кто пишет боту, получают уведомления о сделках")'''

NEW_HELP = '''        elif text.startswith("/help"):
            _m = "ДЕМО" if config.TRADING_MODE != "real" else "РЕАЛ"
            _cmds = ("/status — состояние\\n/start — возобновить после стопа\\n"
                     "/stop — полный стоп\\n" if is_owner else "/status — состояние\\n")
            send_to(cid, f"🤖 Gibrid-bot — {_m} (Forex, PU Prime)\\n" + _cmds +
                         "Все, кто пишет боту, получают уведомления о сделках")'''


def patch(path: Path, subs: list[tuple[str, str]], marker: str, apply: bool) -> bool:
    """marker — строка, наличие которой означает «патч уже применён»."""
    src = io.open(path, encoding="utf-8").read()
    if marker in src:
        print(f"  [skip] {path.name}: патч уже применён")
        return True
    out = src
    for old, new in subs:
        if old not in out:
            print(f"  [FAIL] {path.name}: не найден фрагмент:\n    {old.splitlines()[0][:70]}")
            return False
        out = out.replace(old, new, 1)
    if not apply:
        print(f"  [dry-run] {path.name}: патч применится чисто")
        return True
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(path, path.with_suffix(path.suffix + f".bak_{stamp}"))
    io.open(path, "w", encoding="utf-8").write(out)
    print(f"  [ok] {path.name} обновлён (бэкап .bak_{stamp})")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not (args.dry_run or args.apply):
        ap.error("укажите --dry-run или --apply")

    print("=" * 62)
    print("ПАТЧ: команды /stop и /start — только владельцу")
    print("=" * 62)

    ok = True
    print("\nconfig.py:")
    ok &= patch(CONFIG, [(CFG_ANCHOR, CFG_ANCHOR + CFG_ADD)],
                "TELEGRAM_OWNER_ID", args.apply)
    print("\nnotifier.py:")
    ok &= patch(NOTIFIER, [(OLD_START, NEW_START), (OLD_HELP, NEW_HELP),
                           (OLD_STOP, NEW_STOP)],
                "is_owner", args.apply)

    if not ok:
        print("\n[FAIL] патч не применён полностью — файлы не изменены или откатить из .bak")
        sys.exit(1)

    if args.apply:
        import py_compile
        for f in (CONFIG, NOTIFIER):
            py_compile.compile(str(f), doraise=True)
        print("\n[ok] синтаксис обоих файлов валиден")
        print("[ok] готово. В .env при желании: TELEGRAM_OWNER_ID=<chat_id>")
        print("     Рестарт fxbot-telegram, чтобы изменения вступили в силу.")
    else:
        print("\n[dry-run] изменений не внесено. Для применения: --apply")


if __name__ == "__main__":
    main()
