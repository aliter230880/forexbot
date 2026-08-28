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

# --- Мульти-символьный бот (отдельный процесс: python -m backend.main_multi) ---
# Инструменты отобраны по отношению ATR(M5)/спред на лоте 0.01 (замер 2026-08-25):
#   XAUUSD 8% спреда от ATR · NAS100 12% · XAGUSD 21% — годны.
#   Мажоры (EURUSD 76%, GBPUSD 65%, AUDUSD >100%) НЕ годны для M5 на мин. лоте.
MULTI_MAGIC = 20260825
# Отбор 2026-08-25 сканом всех инструментов: критерий — стоп ≤2.5% от $100
# на мин. лоте И спред ≤30% от ATR(M5). Прошли: индексы NAS100/GER40/UK100,
# нефть USOUSD/UKOUSD, BTCUSD. Золото/серебро отсеяны (стоп 4.8%/6.1% от $100),
# мажоры — спред 65-100% от ATR, крипта кроме BTC — спред 180-750%.
MULTI_SYMBOLS = os.getenv(
    "MULTI_SYMBOLS", "NAS100.s,GER40.s,UK100.s,USOUSD.s,UKOUSD.s,BTCUSD").split(",")
MULTI_LOT = float(os.getenv("MULTI_LOT", "0.01"))
MULTI_MAX_OPEN_TOTAL = int(os.getenv("MULTI_MAX_OPEN_TOTAL", "3"))   # всего позиций
MULTI_MAX_OPEN_PER_SYMBOL = 1
MULTI_MAX_TRADES_DAY = int(os.getenv("MULTI_MAX_TRADES_DAY", "45"))  # суммарно по всем
MULTI_COOLDOWN_SEC = int(os.getenv("MULTI_COOLDOWN_SEC", "180"))     # на символ
MULTI_STATE_FILE = "state_multi.json"
MULTI_DB_VERSION = "multi"      # метка в scalp_trades для раздельной статистики

# Риск-фильтр под мини-баланс: тестируем на демо, но риск считаем как для $100.
# Если стоимость стопа > MULTI_MAX_RISK_PCT от MULTI_TEST_BALANCE — символ пропускается.
# Замер 2026-08-25 (лот 0.01): NAS100 SL≈$0.22 (0.2%) OK · XAUUSD $4.88 (4.9%) · XAGUSD $6.36 (6.4%).
MULTI_TEST_BALANCE = float(os.getenv("MULTI_TEST_BALANCE", "100"))
MULTI_MAX_RISK_PCT = float(os.getenv("MULTI_MAX_RISK_PCT", "2.5"))
MULTI_ENFORCE_RISK = os.getenv("MULTI_ENFORCE_RISK", "1") == "1"

# --- ГИБРИД (hybrid_engine.py): лимитные входы сетки + жёсткий SL скальпинга ---
# Обоснование (research_hybrid.py, 30 торговых дней золота):
#   лимитка не платит спред на догоне → при TP $4 спред $0.35 = 8.7% от цели
#   (у скальпинга с целью $1.5 было 25% — отсюда его убыток).
#   Найденная конфигурация: 14-16 сделок/день, WR ~78%, PF 1.36.
# ⚠️ Стресс-тест на развёрнутом (падающем) рынке: без ограничений — маржин-колл.
#   Поэтому max 3 позиции + аварийный выход корзины + лимит риска корзины.
HYBRID_MAGIC = 20260826
HYBRID_SYMBOLS = os.getenv("HYBRID_SYMBOLS", "XAUUSD.s,NAS100.s,GER40.s").split(",")
HYBRID_LOT = float(os.getenv("HYBRID_LOT", "0.01"))
HYBRID_STEP_ATR = float(os.getenv("HYBRID_STEP_ATR", "1.0"))   # шаг сетки = ATR(M5) x N
HYBRID_TP_ATR = float(os.getenv("HYBRID_TP_ATR", "1.0"))       # тейк = шаг
HYBRID_SL_ATR = float(os.getenv("HYBRID_SL_ATR", "2.5"))       # стоп позиции = ATR x N
HYBRID_MAX_POS_PER_SYMBOL = int(os.getenv("HYBRID_MAX_POS_PER_SYMBOL", "3"))
HYBRID_MAX_POS_TOTAL = int(os.getenv("HYBRID_MAX_POS_TOTAL", "6"))
HYBRID_LEVELS = int(os.getenv("HYBRID_LEVELS", "3"))           # активных лимиток на символ
HYBRID_REBUILD_SEC = int(os.getenv("HYBRID_REBUILD_SEC", "900"))  # пересборка сетки
HYBRID_TEST_BALANCE = float(os.getenv("HYBRID_TEST_BALANCE", "100"))
HYBRID_MAX_BASKET_RISK_PCT = float(os.getenv("HYBRID_MAX_BASKET_RISK_PCT", "25"))
HYBRID_MAX_POS_RISK_PCT = float(os.getenv("HYBRID_MAX_POS_RISK_PCT", "10"))
# главный урок скальпинга: спред 25% от цели = гарантированный убыток.
# Лимитка платит спред один раз, но цель всё равно должна быть кратно больше спреда.
HYBRID_MAX_SPREAD_PCT_OF_TP = float(os.getenv("HYBRID_MAX_SPREAD_PCT_OF_TP", "12"))
# цель не меньше N спредов (адаптивная): у индексов ATR-цель выходила $0.05 при
# спреде $0.025 = 46%. При 10 спредах доля спреда в цели гарантированно ≤10%.
HYBRID_MIN_TP_SPREADS = float(os.getenv("HYBRID_MIN_TP_SPREADS", "10"))
HYBRID_DAILY_LOSS_PCT = float(os.getenv("HYBRID_DAILY_LOSS_PCT", "8"))
HYBRID_HOUR_FROM_UTC = 6
HYBRID_HOUR_TO_UTC = 20
HYBRID_WEEKEND_CLOSE_HOUR = 22   # пятница: флэт
HYBRID_STATE_FILE = "state_hybrid.json"
HYBRID_DB_VERSION = "hybrid"

# --- КОПИТРЕЙДИНГ (copier_engine.py + main_copier.py) ---
# Копир — отдельный процесс на втором терминале MT5 (счёт подписчика).
# Читает сделки выбранных мастеров из общей bot.db и зеркалит:
#   лот = баланс_подписчика / баланс_мастера × лот_мастера (приводится к шагу лота)
#   вход — рыночный по появлению сделки мастера; SL/TP пересчитываются от цены входа мастера
#   закрытие — когда мастер закрыл (tp/sl/manual), копир закрывает зеркалку по рынку
# По умолчанию DRY_RUN=1: только логи, ордера не отправляются.
COPIER_DRY_RUN = os.getenv("COPIER_DRY_RUN", "1") == "1"
COPIER_MASTERS = os.getenv("COPIER_MASTERS", "hybrid,kiro").split(",")  # версии мастеров
# второй терминал MT5 (счёт подписчика); пока = мастер-терминал для теста логики
COPIER_MT5_PATH = os.getenv("COPIER_MT5_PATH", r"E:\AI\MetaTrader5\terminal64.exe")
COPIER_LOGIN = int(os.getenv("COPIER_LOGIN", "0"))        # 0 = текущий счёт терминала
COPIER_PASSWORD = os.getenv("COPIER_PASSWORD", "")
COPIER_SERVER = os.getenv("COPIER_SERVER", "")
COPIER_MAGIC = 20260828
# баланс мастера для масштабирования лота (если 0 — берётся из БД/конфига)
COPIER_MASTER_BALANCE = float(os.getenv("COPIER_MASTER_BALANCE", "100000"))
COPIER_FIXED_LOT = float(os.getenv("COPIER_FIXED_LOT", "0"))  # >0 = фикс. лот без масштабирования
COPIER_MAX_LOT = float(os.getenv("COPIER_MAX_LOT", "0.1"))
COPIER_MAX_POS = int(os.getenv("COPIER_MAX_POS", "6"))     # макс. зеркальных позиций
COPIER_MAX_LATENCY_SEC = int(os.getenv("COPIER_MAX_LATENCY_SEC", "300"))  # копируем сделки не старше
COPIER_POLL_SEC = int(os.getenv("COPIER_POLL_SEC", "5"))
COPIER_STATE_FILE = "state_copier.json"

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")  # в .env
TELEGRAM_API_IPS = os.getenv("TELEGRAM_API_IPS", "")  # пиннинг IP api.telegram.org (обход блокировки РКН)
TELEGRAM_API_BASE = os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org")  # базовый URL (или релей через другой хост)
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")  # авто-привязка по первому /start
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL", "@forex_vip_first")  # пусто → рассылка подписчикам

# --- Админ-панель ---
ADMIN_USER = os.getenv("ADMIN_USER", "dim230880")
ADMIN_PASS = os.getenv("ADMIN_PASS", "")  # в .env
WEB_PORT = int(os.getenv("WEB_PORT", "8181"))

DB_PATH = DATA_DIR / "bot.db"
STATE_PATH = DATA_DIR / "state.json"
