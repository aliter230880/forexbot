"""
Центральная конфигурация бота
"""
import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

class Config:
    """Основная конфигурация"""
    
    # MetaTrader5
    MT5_LOGIN = int(os.getenv('MT5_LOGIN', 0))
    MT5_PASSWORD = os.getenv('MT5_PASSWORD', '')
    MT5_SERVER = os.getenv('MT5_SERVER', '')
    MT5_PATH = os.getenv('MT5_PATH', r'C:\Program Files\MetaTrader 5\terminal64.exe')
    
    # Trading
    SYMBOL = os.getenv('SYMBOL', 'XAUUSD')
    INITIAL_BALANCE = float(os.getenv('INITIAL_BALANCE', 100))
    MAX_RISK_PER_TRADE = float(os.getenv('MAX_RISK_PER_TRADE', 0.01))
    MAX_DAILY_DRAWDOWN = float(os.getenv('MAX_DAILY_DRAWDOWN', 0.03))
    MAX_TRADES_PER_DAY = int(os.getenv('MAX_TRADES_PER_DAY', 12))
    MIN_WINRATE_THRESHOLD = float(os.getenv('MIN_WINRATE_THRESHOLD', 0.45))
    
    # Trading Hours
    TRADING_START_HOUR = int(os.getenv('TRADING_START_HOUR', 8))
    TRADING_END_HOUR = int(os.getenv('TRADING_END_HOUR', 17))
    
    # Technical Indicators
    RSI_PERIOD = int(os.getenv('RSI_PERIOD', 14))
    EMA_FAST = int(os.getenv('EMA_FAST', 9))
    EMA_SLOW = int(os.getenv('EMA_SLOW', 21))
    BB_PERIOD = int(os.getenv('BB_PERIOD', 20))
    ATR_PERIOD = int(os.getenv('ATR_PERIOD', 14))
    
    # ML Model
    ML_MODEL_PATH = os.getenv('ML_MODEL_PATH', './models/xgboost_model.json')
    ML_RETRAIN_AFTER_TRADES = int(os.getenv('ML_RETRAIN_AFTER_TRADES', 100))
    ML_MIN_TRAINING_SAMPLES = int(os.getenv('ML_MIN_TRAINING_SAMPLES', 500))
    
    # Risk Management
    TP_PIPS = float(os.getenv('TP_PIPS', 7))
    SL_PIPS = float(os.getenv('SL_PIPS', 5))
    TRAILING_STOP_PIPS = float(os.getenv('TRAILING_STOP_PIPS', 3))
    MAX_SPREAD_PIPS = float(os.getenv('MAX_SPREAD_PIPS', 3))
    
    # External APIs
    TWITTER_BEARER_TOKEN = os.getenv('TWITTER_BEARER_TOKEN', '')
    ALPHA_VANTAGE_API_KEY = os.getenv('ALPHA_VANTAGE_API_KEY', '')
    
    # Telegram
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
    TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')
    
    # Database
    DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///forex_bot.db')
    
    # Logging
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FILE = os.getenv('LOG_FILE', './logs/bot.log')
    
    # Directories
    BASE_DIR = Path(__file__).parent
    LOGS_DIR = BASE_DIR / 'logs'
    MODELS_DIR = BASE_DIR / 'models'
    DATA_DIR = BASE_DIR / 'data'
    
    @classmethod
    def create_directories(cls):
        """Создать необходимые директории"""
        cls.LOGS_DIR.mkdir(exist_ok=True)
        cls.MODELS_DIR.mkdir(exist_ok=True)
        cls.DATA_DIR.mkdir(exist_ok=True)
