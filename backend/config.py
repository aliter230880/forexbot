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

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")  # в .env
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")  # авто-привязка по первому /start
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL", "@forex_vip_first")  # пусто → рассылка подписчикам

DB_PATH = DATA_DIR / "bot.db"
STATE_PATH = DATA_DIR / "state.json"
