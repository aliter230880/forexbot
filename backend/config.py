# -*- coding: utf-8 -*-
"""Конфигурация forex-грид-бота (XAUUSD, PU Prime demo)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env():
    """Читает .env из корня проекта (секреты не в git)."""
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# --- MetaTrader 5 ---
MT5_TERMINAL_PATH = os.getenv("MT5_TERMINAL_PATH", r"E:\AI\MetaTrader5\terminal64.exe")
MT5_LOGIN = int(os.getenv("MT5_LOGIN", "700158875"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")        # в .env
MT5_SERVER = os.getenv("MT5_SERVER", "PUPrime-Demo")
MT5_TIMEOUT_MS = 45_000

SYMBOL = os.getenv("SYMBOL", "XAUUSD.s")

# --- Сетка (активнее: шаг $4 при спреде $0.32 = запас ×12; лот 0.02) ---
GRID_LEVELS = int(os.getenv("GRID_LEVELS", "15"))       # уровней buy ниже цены
GRID_STEP_USD = float(os.getenv("GRID_STEP_USD", "4.0"))  # шаг сетки в $
GRID_LOT = float(os.getenv("GRID_LOT", "0.02"))          # лот на уровень
TP_USD = float(os.getenv("TP_USD", "4.0"))               # тейк пары = шаг

# --- Авто-сдвиг сетки (аналог auto-range-shift) ---
SHIFT_TRIGGER_STEPS = 1     # цена выше верхнего уровня на N шагов → пересборка
SHIFT_COOLDOWN_SEC = 600    # не чаще раза в 10 минут

# --- Тренд-фильтр (v10.1-подобный) ---
TREND_LOOKBACK_HOURS = 24
TREND_DROP_PCT = -3.0      # падение >= 3% за 24ч → пауза покупок
TREND_RESUME_PCT = -2.0    # гистерезис: возврат выше -2% → купить снова

# --- Margin guard ---
GUARD_EQUITY_DD_STOP = 0.12    # -12% equity от watermark → закрыть всё, стоп
GUARD_MARGIN_LEVEL_MIN = 300.0 # % margin level ниже → усечь сетку
GUARD_DAILY_LOSS_PCT = 0.05    # -5% за сутки → стоп до след. дня

# --- Weekend flat ---
WEEKEND_CLOSE_DOW = 4         # Friday
WEEKEND_CLOSE_HOUR_UTC = 23   # закрыть всё в пятницу 23:00 UTC (рынок у PU Prime до 23:55)
WEEKEND_OPEN_DOW = 6          # Sunday
WEEKEND_OPEN_HOUR_UTC = 22    # воскресенье 22:05 UTC

# --- Циклы ---
POLL_SECONDS = 20             # основной цикл движка
GUARD_CHECK_SECONDS = 60
DIGEST_HOUR_UTC = 8           # ежедневный дайджест в 8:00 UTC (11:00 МСК)

# --- Профили ---
# PROFILE=STANDARD — сетка (мастер)
# PROFILE=MICRO   — сетка под микродепозит $100-150
# PROFILE=SCALP   — скальпинг M1: TP $2 / SL $1, до 30 сделок/день
PROFILE = os.getenv("PROFILE", "STANDARD")
if PROFILE == "MICRO":
    GRID_LEVELS = 5
    GRID_STEP_USD = 8.0
    TP_USD = 8.0
    GRID_LOT = 0.01
    TREND_DROP_PCT = -2.0
    TREND_RESUME_PCT = -1.0
    GUARD_EQUITY_DD_STOP = 0.15   # стоп сетки -15% депозита
    GUARD_DAILY_LOSS_PCT = 0.05

# --- Скальпинг (PROFILE=SCALP) ---
# v2 (2026-08-25): вход только по тренду H1, ADX-фильтр силы, сигнал на M5, трейлинг.
# v1 (M1 EMA-откат в обе стороны) провалился: 59 сделок, WR 30.5%, -$28.
SCALP_ATR_MULT_SL = float(os.getenv("SCALP_ATR_MULT_SL", "1.2"))   # SL = ATR(M5) × 1.2
SCALP_ATR_MULT_TP = float(os.getenv("SCALP_ATR_MULT_TP", "2.4"))   # TP = ATR(M5) × 2.4 (1:2)
SCALP_SL_MIN_USD = float(os.getenv("SCALP_SL_MIN_USD", "1.5"))     # но не тесней $1.5 (спред!)
SCALP_SL_MAX_USD = float(os.getenv("SCALP_SL_MAX_USD", "5.0"))     # и не шире $5
SCALP_LOT = float(os.getenv("SCALP_LOT", "0.01"))
SCALP_EMA_FAST = 9
SCALP_EMA_SLOW = 21
SCALP_H1_EMA = 50            # старший тренд: цена и EMA9 относительно EMA50 на H1
SCALP_ADX_PERIOD = 14
SCALP_ADX_MIN = float(os.getenv("SCALP_ADX_MIN", "22"))  # < 22 → флэт, не торгуем
SCALP_RSI_PERIOD = 14
SCALP_RSI_LONG_MIN = 45.0
SCALP_RSI_LONG_MAX = 72.0    # не покупать на перегреве
SCALP_RSI_SHORT_MAX = 55.0
SCALP_RSI_SHORT_MIN = 28.0
SCALP_MAX_TRADES_DAY = int(os.getenv("SCALP_MAX_TRADES_DAY", "25"))
SCALP_MAX_OPEN = 1           # одна позиция за раз (качество > количество)
SCALP_COOLDOWN_SEC = 300     # 5 мин между входами
SCALP_HOUR_FROM_UTC = 7      # Лондон-открытие
SCALP_HOUR_TO_UTC = 19       # до вечера NY
SCALP_MAX_SPREAD_USD = 0.45
SCALP_MAX_LOSS_STREAK = 3    # 3 стопа подряд → пауза до след. дня
# трейлинг: при движении в плюс на TRAIL_START переносим SL на TRAIL_LOCK от входа
SCALP_TRAIL_START = float(os.getenv("SCALP_TRAIL_START", "1.5"))   # $1.5 в плюсе
SCALP_TRAIL_LOCK = float(os.getenv("SCALP_TRAIL_LOCK", "0.3"))     # SL → вход +$0.3 (безубыток)
SCALP_TRAIL_STEP = float(os.getenv("SCALP_TRAIL_STEP", "1.0"))     # далее тянем каждые $1

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")  # в .env
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")  # авто-привязка по первому /start
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL", "@forex_vip_first")  # пусто → рассылка подписчикам

# --- Админ-панель ---
ADMIN_USER = os.getenv("ADMIN_USER", "dim230880")
ADMIN_PASS = os.getenv("ADMIN_PASS", "")  # в .env
WEB_PORT = int(os.getenv("WEB_PORT", "8181"))

DB_PATH = DATA_DIR / "bot.db"
STATE_PATH = DATA_DIR / "state.json"
