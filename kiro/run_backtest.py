"""
Запуск бэктестирования стратегии
"""
import MetaTrader5 as mt5
from loguru import logger
from config import Config
from src.mt5_connector import MT5Connector
from src.market_analyzer import MarketAnalyzer
from src.ml_predictor import MLPredictor
from src.risk_manager import RiskManager
from src.trading_strategy import TradingStrategy
from src.backtester import Backtester
from datetime import datetime, timedelta

def main():
    """Главная функция бэктеста"""
    logger.info("🧪 Starting backtest...")
    
    config = Config()
    config.create_directories()
    
    # Подключение к MT5 для получения исторических данных
    mt5_conn = MT5Connector()
    if not mt5_conn.connect():
        logger.error("Failed to connect to MT5")
        return
        
    # Загрузка данных (2 недели на M5 = примерно 4000 баров)
    logger.info("Loading historical data...")
    df = mt5_conn.get_historical_data(timeframe=mt5.TIMEFRAME_M5, bars=4000)
    
    if df.empty:
        logger.error("No data loaded")
        mt5_conn.disconnect()
        return
        
    logger.info(f"Loaded {len(df)} bars from {df['time'].min()} to {df['time'].max()}")
    
    # Инициализация компонентов
    analyzer = MarketAnalyzer(mt5_conn, config)
    ml_predictor = MLPredictor(config)
    risk_manager = RiskManager(config, mt5_conn)
    strategy = TradingStrategy(config, mt5_conn, analyzer, ml_predictor, risk_manager)
    
    # Создание бэктестера
    backtester = Backtester(config, strategy, initial_balance=100.0)
    
    # Запуск
    results = backtester.run(df)
    
    # Сохранение отчёта
    backtester.save_report('backtest_report.json')
    
    # График equity curve
    try:
        backtester.plot_equity_curve()
    except Exception as e:
        logger.warning(f"Could not plot equity curve: {e}")
    
    mt5_conn.disconnect()
    logger.info("✅ Backtest completed")

if __name__ == "__main__":
    main()
