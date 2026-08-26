"""
Главный файл запуска forex скальпер-бота
"""
import asyncio
import signal
import sys
from loguru import logger
from config import Config
from src.mt5_connector import MT5Connector
from src.bot_engine import BotEngine

class ForexScalperBot:
    """Главный класс бота"""
    
    def __init__(self):
        self.config = Config()
        self.config.create_directories()
        self.setup_logging()
        self.mt5 = MT5Connector()
        self.engine = None
        self.running = False
        
    def setup_logging(self):
        """Настройка логирования"""
        logger.remove()
        logger.add(
            sys.stdout,
            level=self.config.LOG_LEVEL,
            format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> | <level>{message}</level>"
        )
        logger.add(
            self.config.LOG_FILE,
            rotation="1 day",
            retention="30 days",
            level=self.config.LOG_LEVEL,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function} | {message}"
        )
        
    async def start(self):
        """Запуск бота"""
        logger.info("🚀 Запуск Forex Scalper Bot для XAUUSD")
        
        # Подключение к MT5
        if not self.mt5.connect():
            logger.error("❌ Не удалось подключиться к MetaTrader5")
            return False
            
        logger.info(f"✅ Подключено к MT5: {self.mt5.get_account_info()}")
        
        # Инициализация движка бота
        self.engine = BotEngine(self.mt5)
        
        # Установка обработчиков сигналов
        signal.signal(signal.SIGINT, self.shutdown)
        signal.signal(signal.SIGTERM, self.shutdown)
        
        # Запуск основного цикла
        self.running = True
        await self.engine.run()
        
        return True
        
    def shutdown(self, signum, frame):
        """Корректное завершение работы"""
        logger.warning(f"⚠️  Получен сигнал {signum}, завершение работы...")
        self.running = False
        if self.engine:
            self.engine.stop()
        self.mt5.disconnect()
        logger.info("👋 Бот остановлен")
        sys.exit(0)
        
    async def run(self):
        """Основной цикл работы"""
        try:
            await self.start()
        except Exception as e:
            logger.exception(f"💥 Критическая ошибка: {e}")
            self.shutdown(None, None)

if __name__ == "__main__":
    bot = ForexScalperBot()
    asyncio.run(bot.run())
