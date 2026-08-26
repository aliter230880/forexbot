# -*- coding: utf-8 -*-
"""Раннер kiro-бота (стратегия №4, ML-скальпер) — экспериментальный.

Использует kiro-компоненты для сигнала (TradingStrategy.analyze_and_decide:
теханализ + ML confidence + режим рынка), но сделки открывает собственным
кодом с отдельным magic (KIRO_MAGIC) и пишет в общую bot.db (version='kiro').

Ограничения под мини-баланс $100: лот 0.01, max 2 позиции, 12 сделок/день,
дневной стоп 6%, торговое окно как у скальпера. Без трансляций в канал.
"""
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import MetaTrader5 as mt5

KIRO_DIR = Path(__file__).resolve().parent.parent / "kiro"
sys.path.insert(0, str(KIRO_DIR))

from config import Config as KiroConfig  # noqa: E402
from src.mt5_connector import MT5Connector  # noqa: E402
from src.market_analyzer import MarketAnalyzer  # noqa: E402
from src.ml_predictor import MLPredictor  # noqa: E402
from src.risk_manager import RiskManager  # noqa: E402
from src.trading_strategy import TradingStrategy  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import config, storage  # noqa: E402

log = logging.getLogger("kiro")
KIRO_MAGIC = 20260827
SYMBOL = "XAUUSD.s"
LOT = 0.01
MAX_POS = 2
MAX_TRADES_DAY = 12
DAILY_LOSS_PCT = 6.0
TEST_BALANCE = 100.0
COOLDOWN_SEC = 300
POLL_SECONDS = 30
VERSION = "kiro"


def kiro_state_path():
    return config.DATA_DIR / "state_kiro.json"


def load_state():
    try:
        return json.loads(kiro_state_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"halted": False, "halted_reason": "", "last_trade": 0.0, "day": None, "day_pnl": 0.0}


def save_state(st):
    kiro_state_path().write_text(json.dumps(st, indent=2, ensure_ascii=False), encoding="utf-8")


def positions():
    pos = mt5.positions_get(symbol=SYMBOL) or []
    return [p for p in pos if p.magic == KIRO_MAGIC]


def sync_closed():
    """Закрытые позиции (TP/SL сервером) → БД + PnL."""
    live = {p.ticket for p in positions()}
    for t in storage.kiro_open_trades():
        if t["ticket"] in live:
            continue
        deals = mt5.history_deals_get(position=t["ticket"]) or []
        out = [d for d in deals if d.entry == mt5.DEAL_ENTRY_OUT]
        if not out:
            continue
        d = out[-1]
        reason = "tp" if d.reason == mt5.DEAL_REASON_TP else (
            "sl" if d.reason == mt5.DEAL_REASON_SL else "manual")
        pnl = d.profit + d.commission + d.swap + getattr(d, "fee", 0.0)
        storage.scalp_close(t["ticket"], d.price, round(pnl, 2), reason)
        s = storage.kiro_stats()
        log.info("KIRO CLOSED %s %s → %.2f pnl %.2f | WR %.0f%% итого %.2f",
                 t["side"], reason, d.price, pnl, s["winrate"], s["realized_pnl"])


def open_trade(side: str, confidence: float) -> int | None:
    info = mt5.symbol_info(SYMBOL)
    tick = mt5.symbol_info_tick(SYMBOL)
    if not info or not tick or tick.ask <= 0:
        return None
    is_long = side == "buy"
    entry = tick.ask if is_long else tick.bid
    # TP/SL от kiro-конфига (в пипсах), пересчёт в цену
    tp_dist = KiroConfig.TP_PIPS * info.point * 10   # pip = 10 points на 5-значных
    sl_dist = KiroConfig.SL_PIPS * info.point * 10
    # мини-баланс: стоп не дороже 4% от $100
    pv = info.trade_contract_size * LOT
    if sl_dist * pv > TEST_BALANCE * 0.04:
        sl_dist = TEST_BALANCE * 0.04 / pv
        tp_dist = sl_dist * 1.4
    digits = info.digits
    sl = round(entry - sl_dist if is_long else entry + sl_dist, digits)
    tp = round(entry + tp_dist if is_long else entry - tp_dist, digits)
    req = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": SYMBOL,
        "volume": LOT, "type": mt5.ORDER_TYPE_BUY if is_long else mt5.ORDER_TYPE_SELL,
        "price": entry, "sl": sl, "tp": tp, "deviation": 30,
        "magic": KIRO_MAGIC, "comment": "kiro_ml",
        "type_time": mt5.ORDER_TIME_GTC,
    }
    for filling in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN):
        req["type_filling"] = filling
        res = mt5.order_send(req)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            storage.scalp_open(res.order, "LONG" if is_long else "SHORT",
                               res.price, sl, tp, ctx={
                                   "hour_utc": datetime.now(timezone.utc).hour,
                                   "version": VERSION, "symbol": SYMBOL,
                                   "adx": round(confidence * 100, 1),
                                   "h1_trend": f"conf={confidence:.2f}",
                               })
            log.info("KIRO %s @ %.*f conf %.2f sl %.2f tp %.2f",
                     side, digits, res.price, confidence, sl, tp)
            return res.order
        if res and res.retcode != mt5.TRADE_RETCODE_INVALID_FILL:
            log.error("kiro order failed: %s %s", res.retcode, res.comment)
            return None
    return None


def main():
    storage.init_db()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(config.DATA_DIR / "kiro.log", encoding="utf-8")])
    global log
    log = logging.getLogger("kiro")

    st = load_state()
    kc = KiroConfig()
    conn = MT5Connector()
    if not conn.connect():
        log.error("нет связи с MT5")
        sys.exit(1)
    kc.ML_MODEL_PATH = str(KIRO_DIR / "models" / "xgboost_model.json")
    ml = MLPredictor(kc)
    analyzer = MarketAnalyzer(conn, kc)
    risk = RiskManager(kc, conn)
    strategy = TradingStrategy(kc, conn, analyzer, ml, risk)
    log.info("kiro-бот запущен: %s лот %s max %s/день (ML %s)",
             SYMBOL, LOT, MAX_TRADES_DAY,
             "обучена" if ml.is_trained else "НЕ обучена (сигналы будут ждать)")

    from backend.notifier import send_to, chat_id
    send_to(chat_id(), "🤖 kiro-бот (ML-скальпер) запущен как экспериментальный 4-й бот")

    while True:
        try:
            if mt5.terminal_info() is None or mt5.account_info() is None:
                log.warning("связь с MT5 потеряна")
                gw_reconnect(conn)

            # команда из админки
            cmd_file = config.DATA_DIR / "cmd_kiro.json"
            if cmd_file.exists():
                try:
                    cmd = json.loads(cmd_file.read_text(encoding="utf-8"))
                    cmd_file.unlink(missing_ok=True)
                    if cmd.get("action") == "stop":
                        close_all()
                        st.update(halted=True, halted_reason="стоп из админки")
                        save_state(st)
                    elif cmd.get("action") == "start":
                        st.update(halted=False, halted_reason="")
                        save_state(st)
                except json.JSONDecodeError:
                    cmd_file.unlink(missing_ok=True)

            now = datetime.now(timezone.utc)
            today = now.strftime("%Y-%m-%d")
            if st.get("day") != today:
                s = storage.kiro_stats()
                st.update(day=today, day_pnl=s["realized_pnl"])
                save_state(st)

            sync_closed()

            s = storage.kiro_stats()
            day_pnl = s["realized_pnl"] - (st.get("day_pnl") or 0.0)
            day_pct = 100 * day_pnl / TEST_BALANCE

            halted = st.get("halted", False) or s["trades_today"] >= MAX_TRADES_DAY \
                or day_pct <= -DAILY_LOSS_PCT \
                or now.weekday() >= 5 or not (6 <= now.hour < 20)

            if not halted:
                if (time.time() - st.get("last_trade", 0) >= COOLDOWN_SEC
                        and len(positions()) < MAX_POS):
                    decision = strategy.analyze_and_decide()
                    action = decision.get("action")
                    conf = float(decision.get("confidence") or 0)
                    if action in ("buy", "sell") and conf >= 0.6:
                        t = open_trade(action, conf)
                        if t:
                            st["last_trade"] = time.time()
                            save_state(st)
            elif s["trades_today"] >= MAX_TRADES_DAY:
                log.debug("лимит дня")
            time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("цикл упал: %s", e)
            time.sleep(POLL_SECONDS)


def gw_reconnect(conn):
    try:
        import MetaTrader5 as mt5
        mt5.shutdown()
    except Exception:  # noqa: BLE001
        pass
    time.sleep(5)
    conn.connect()


def close_all():
    for p in positions():
        tick = mt5.symbol_info_tick(SYMBOL)
        if not tick:
            continue
        is_long = p.type == mt5.POSITION_TYPE_BUY
        mt5.order_send({
            "action": mt5.TRADE_ACTION_DEAL, "symbol": SYMBOL,
            "volume": p.volume, "position": p.ticket,
            "type": mt5.ORDER_TYPE_SELL if is_long else mt5.ORDER_TYPE_BUY,
            "price": tick.bid if is_long else tick.ask,
            "deviation": 30, "magic": KIRO_MAGIC,
            "type_filling": mt5.ORDER_FILLING_IOC})
    log.info("KIRO: все позиции закрыты")


if __name__ == "__main__":
    main()
