# -*- coding: utf-8 -*-
"""Проверка подключения к демо-счёту PU Prime через MetaTrader5."""
import sys

import MetaTrader5 as mt5

from . import config

TERMINAL_PATH = config.MT5_TERMINAL_PATH
LOGIN = config.MT5_LOGIN
PASSWORD = config.MT5_PASSWORD
SERVER = config.MT5_SERVER


def main():
    print(f"MetaTrader5 package version: {mt5.__version__}")

    ok = mt5.initialize(
        path=TERMINAL_PATH,
        login=LOGIN,
        password=PASSWORD,
        server=SERVER,
        timeout=180000,
    )
    if not ok:
        print(f"initialize() failed, error = {mt5.last_error()}")
        sys.exit(1)
    print("initialize() OK")

    acc = mt5.account_info()
    if acc is None:
        print(f"account_info() failed: {mt5.last_error()}")
        sys.exit(1)
    print("\n=== ACCOUNT ===")
    print(f"login={acc.login}  server={acc.server}  currency={acc.currency}")
    print(f"leverage=1:{acc.leverage}  balance={acc.balance}  equity={acc.equity}")
    print(f"margin_free={acc.margin_free}  margin_level={acc.margin_level}")
    print(f"trade_mode={acc.trade_mode}  company={acc.company}")

    # XAUUSD
    sym = mt5.symbol_info("XAUUSD")
    if sym is None:
        print("\nXAUUSD not found, searching gold symbols...")
        for s in mt5.symbols_get():
            if "XAU" in s.name or "GOLD" in s.name.upper():
                print(f"  candidate: {s.name}")
    else:
        print("\n=== XAUUSD ===")
        print(f"visible={sym.visible}  point={sym.point}  digits={sym.digits}")
        print(f"volume_min={sym.volume_min}  volume_step={sym.volume_step}  volume_max={sym.volume_max}")
        print(f"trade_contract_size={sym.trade_contract_size}")
        if not sym.visible:
            mt5.symbol_select("XAUUSD", True)
        tick = mt5.symbol_info_tick("XAUUSD")
        if tick:
            print(f"bid={tick.bid}  ask={tick.ask}  spread={(tick.ask - tick.bid) / sym.point:.1f} points")
            print(f"time={tick.time}")

    mt5.shutdown()
    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
