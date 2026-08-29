# -*- coding: utf-8 -*-
"""Проверка готовности live-счёта к торговле гибридом. Только чтение, ордера НЕ шлёт.

Запуск на VPS (терминал должен быть залогинен в live-счёт):
    cd C:\\forexbot
    .venv\\Scripts\\python.exe live\\check_live.py

Что печатает:
  1) факт подключения, счёт/сервер/баланс/плечо, trade_allowed
  2) фактические имена символов у брокера (на live суффиксы часто иные, чем на демо)
  3) риск-таблицу гибрида: проходит ли инструмент по SL и спреду при базе из .env
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import MetaTrader5 as mt5  # noqa: E402

from backend import config  # noqa: E402


def line(title: str = ""):
    print("\n" + "=" * 62)
    if title:
        print(title)
        print("=" * 62)


def check_account() -> bool:
    line("1. ПОДКЛЮЧЕНИЕ")
    print(f"terminal path : {config.MT5_TERMINAL_PATH}")
    print(f"login из .env : {config.MT5_LOGIN}")
    print(f"server из .env: {config.MT5_SERVER}")

    ok = mt5.initialize(
        path=config.MT5_TERMINAL_PATH,
        login=config.MT5_LOGIN,
        password=config.MT5_PASSWORD,
        server=config.MT5_SERVER,
        timeout=config.MT5_TIMEOUT_MS,
    )
    if not ok:
        print(f"\n[FAIL] initialize не удался: {mt5.last_error()}")
        print("Проверить: терминал запущен и залогинен в нужный счёт;")
        print("MT5_LOGIN/MT5_PASSWORD/MT5_SERVER в .env; пароль сохранён в терминале.")
        return False

    acc = mt5.account_info()
    if acc is None:
        print(f"\n[FAIL] account_info пустой: {mt5.last_error()}")
        return False

    print("\n[ok] подключение установлено")
    print(f"  счёт        : {acc.login}")
    print(f"  сервер      : {acc.server}")
    print(f"  имя         : {acc.name}")
    print(f"  баланс      : {acc.balance:.2f} {acc.currency}")
    print(f"  equity      : {acc.equity:.2f}")
    print(f"  плечо       : 1:{acc.leverage}")
    print(f"  trade_allowed: {acc.trade_allowed}")

    demo = "demo" in str(acc.server).lower()
    print(f"\n  тип счёта по имени сервера: {'ДЕМО' if demo else 'РЕАЛЬНЫЙ'}")
    if not acc.trade_allowed:
        print("  [WARN] trade_allowed=False → в терминале Ctrl+O → Советники →")
        print("         'Разрешить алгоритмическую торговлю' (применяется на живом сеансе)")

    want_balance = config.HYBRID_TEST_BALANCE
    if abs(acc.balance - want_balance) > 1:
        print(f"  [WARN] HYBRID_TEST_BALANCE={want_balance:.0f}, а баланс {acc.balance:.2f}")
        print("         риск-лимиты считаются от HYBRID_TEST_BALANCE — привести в .env")
    return True


def check_symbols():
    line("2. СИМВОЛЫ: что задано в .env и что реально есть у брокера")
    wanted = [s.strip() for s in config.HYBRID_SYMBOLS if s.strip()]
    print(f"HYBRID_SYMBOLS: {', '.join(wanted)}\n")

    resolved = []
    for name in wanted:
        info = mt5.symbol_info(name)
        if info is None:
            print(f"  [MISS] {name} — нет у брокера")
            continue
        if not info.visible:
            mt5.symbol_select(name, True)
            info = mt5.symbol_info(name)
        tick = mt5.symbol_info_tick(name)
        spread = (tick.ask - tick.bid) if tick else 0.0
        print(f"  [ok] {name:14} lot {info.volume_min}-{info.volume_max} "
              f"step {info.volume_step} · stops_level {info.trade_stops_level} "
              f"· спред {spread:.5f}")
        resolved.append(name)

    missing = [s for s in wanted if s not in resolved]
    if missing:
        print(f"\n  [ВАЖНО] не найдены: {', '.join(missing)}")
        print("  Ищу похожие имена среди символов брокера (суффиксы на live иные):")
        allsym = mt5.symbols_get() or ()
        for miss in missing:
            base = miss.split(".")[0].upper()
            cand = [s.name for s in allsym if s.name.upper().startswith(base)][:8]
            print(f"    {miss:14} → {', '.join(cand) if cand else 'совпадений нет'}")
        print("\n  Правильные имена подставить в .env → HYBRID_SYMBOLS")
    return resolved


def check_risk(symbols):
    line("3. РИСК-ТАБЛИЦА ГИБРИДА (проходят ли инструменты фильтры)")
    if not symbols:
        print("нет доступных символов — пропуск")
        return
    try:
        from backend.hybrid_engine import HybridBot
    except Exception as e:  # noqa: BLE001
        print(f"[skip] импорт HybridBot не удался: {e}")
        return

    bot = HybridBot()
    base = config.HYBRID_TEST_BALANCE
    print(f"база риска: ${base:.0f} · позиция ≤{config.HYBRID_MAX_POS_RISK_PCT}% "
          f"· спред ≤{config.HYBRID_MAX_SPREAD_PCT_OF_TP}% от TP\n")
    print(f"{'symbol':14} {'ATR M5':>10} {'SL,$':>8} {'%базы':>7} {'TP,$':>8} "
          f"{'спред$':>8} {'спред/TP':>9}  вердикт")

    passed = []
    for sym in symbols:
        atr = bot.atr_m5(sym)
        pv = bot.usd_per_price_unit(sym)
        tick = mt5.symbol_info_tick(sym)
        if atr <= 0 or pv <= 0 or not tick:
            print(f"{sym:14} нет данных (рынок закрыт? выходные — это нормально)")
            continue
        spread_now = tick.ask - tick.bid
        tp_d = max(atr * config.HYBRID_TP_ATR, spread_now * config.HYBRID_MIN_TP_SPREADS)
        sl_d = tp_d * (config.HYBRID_SL_ATR / config.HYBRID_TP_ATR)
        sl_usd, tp_usd = sl_d * pv, tp_d * pv
        pct = 100 * sl_usd / base
        sp_pct = 100 * spread_now / tp_d if tp_d else 999
        if pct > config.HYBRID_MAX_POS_RISK_PCT:
            verdict = "ПРОПУСК (риск)"
        elif sp_pct > config.HYBRID_MAX_SPREAD_PCT_OF_TP:
            verdict = "ПРОПУСК (спред)"
        else:
            verdict = "OK"
            passed.append(sym)
        print(f"{sym:14} {atr:>10.4f} {sl_usd:>8.2f} {pct:>6.1f}% {tp_usd:>8.2f} "
              f"{spread_now * pv:>8.3f} {sp_pct:>8.0f}%  {verdict}")

    print(f"\nпроходят фильтры: {', '.join(passed) if passed else 'НИ ОДИН'}")
    if not passed:
        print("[WARN] если рынок открыт и не проходит ни один инструмент —")
        print("       поднять HYBRID_TEST_BALANCE или пересмотреть список символов")


def main():
    print("ПРОВЕРКА LIVE-СЧЁТА (только чтение, ордера не отправляются)")
    if not check_account():
        mt5.shutdown()
        sys.exit(1)
    symbols = check_symbols()
    check_risk(symbols)
    line("ИТОГ")
    print("Если счёт/сервер/баланс верны, trade_allowed=True и символы найдены —")
    print("можно переходить к split_db.py (архив демо + чистая БД под live).")
    mt5.shutdown()


if __name__ == "__main__":
    main()
