"""
Bot Engine - главный движок скальпер-бота
"""
import asyncio
from loguru import logger
from datetime import datetime
import MetaTrader5 as mt5
from config import Config
from src.market_analyzer import MarketAnalyzer
from src.ml_predictor import MLPredictor
from src.risk_manager import RiskManager
from src.trading_strategy import TradingStrategy
from src.self_learning import SelfLearningSystem
from src.telegram_notifier import TelegramNotifier

class BotEngine:
    """Главный движок бота - оркестрация всех компонентов"""
    
    def __init__(self, mt5_connector):
        self.config = Config()
        self.mt5 = mt5_connector
        self.running = False
        
        # Инициализация компонентов
        logger.info("🔧 Initializing bot components...")
        
        self.analyzer = MarketAnalyzer(self.mt5, self.config)
        self.ml = MLPredictor(self.config)
        self.risk = RiskManager(self.config, self.mt5)
        self.strategy = TradingStrategy(
            self.config, self.mt5, self.analyzer, self.ml, self.risk
        )
        self.learning = SelfLearningSystem(self.config, self.ml, self.risk)
        self.notifier = TelegramNotifier(self.config)
        
        # Счётчик итераций для ретрейнинга
        self.iteration_count = 0
        
        logger.info("✅ All components initialized")
        
    async def run(self):
        """Основной цикл работы бота"""
        self.running = True
        logger.info("🤖 Bot engine started")
        
        await self.notifier.send_message("🚀 Forex Scalper Bot started\nSymbol: XAUUSD\nMode: Adaptive Scalping")
        
        # Начальное обучение (если есть исторические данные)
        await self._initial_training()
        
        while self.running:
            try:
                self.iteration_count += 1
                
                # 1. Управление открытыми позициями
                self.strategy.manage_open_positions()
                
                # 2. Анализ и принятие решения
                decision = self.strategy.analyze_and_decide()
                
                logger.debug(f"Decision: {decision['action']} (confidence: {decision.get('confidence', 0):.2%})")
                
                # 3. Исполнение сделки
                if decision['action'] in ['buy', 'sell']:
                    result = self.strategy.execute_trade(decision)
                    
                    if result['success']:
                        # Уведомление
                        await self.notifier.notify_trade_opened(result['details'])
                        
                        # Мониторинг позиции
                        asyncio.create_task(
                            self._monitor_position(result['ticket'], result['details'])
                        )
                        
                # 4. Самообучение (каждые N сделок)
                if self.iteration_count % 50 == 0:
                    await self._check_and_retrain()
                    
                # 5. Периодическая статистика
                if self.iteration_count % 100 == 0:
                    await self._send_statistics()
                    
                # Пауза между итерациями (скальпинг = частые проверки)
                await asyncio.sleep(10)  # 10 секунд
                
            except Exception as e:
                logger.exception(f"Error in main loop: {e}")
                await asyncio.sleep(30)
                
        logger.info("🛑 Bot engine stopped")
        
    async def _initial_training(self):
        """Начальное обучение модели на исторических данных"""
        if self.ml.is_trained:
            logger.info("ML model already trained, skipping initial training")
            return
            
        logger.info("📚 Starting initial ML training...")
        
        try:
            # Загрузка 2 недель исторических данных на M5
            df = self.mt5.get_historical_data(timeframe=mt5.TIMEFRAME_M5, bars=4000)
            
            if df.empty or len(df) < self.config.ML_MIN_TRAINING_SAMPLES:
                logger.warning("Not enough data for training")
                return
                
            # Feature extraction
            features_list = []
            for i in range(100, len(df) - 10):
                window = df.iloc[:i+1]
                features = self.ml.extract_features(window)
                if not features.empty:
                    features_list.append(features.iloc[0])
                    
            if len(features_list) == 0:
                logger.error("Failed to extract features")
                return
                
            features_df = pd.DataFrame(features_list)
            labels = self.ml.create_labels(df.iloc[100:])
            
            # Обучение
            success = self.ml.train(features_df, labels[:len(features_df)])
            
            if success:
                logger.info("✅ Initial training completed")
                await self.notifier.send_message("✅ ML model trained and ready")
            else:
                logger.error("❌ Initial training failed")
                
        except Exception as e:
            logger.exception(f"Initial training error: {e}")
            
    async def _monitor_position(self, ticket: int, trade_details: dict):
        """Мониторинг открытой позиции до закрытия"""
        logger.info(f"👁️  Monitoring position #{ticket}")
        
        while self.running:
            try:
                positions = self.mt5.get_open_positions()
                position = next((p for p in positions if p['ticket'] == ticket), None)
                
                if position is None:
                    # Позиция закрылась
                    logger.info(f"Position #{ticket} closed")
                    
                    # Получение финального результата
                    # В реальности нужно получить из history
                    account_info = self.mt5.get_account_info()
                    
                    # Запись результата
                    trade_data = {
                        'ticket': ticket,
                        'direction': trade_details['direction'],
                        'entry_price': trade_details['entry_price'],
                        'exit_price': trade_details.get('exit_price', 0),
                        'profit': trade_details.get('profit', 0),
                        'reason': 'sl_or_tp'
                    }
                    
                    self.risk.record_trade(trade_data)
                    
                    # Уведомление
                    await self.notifier.notify_trade_closed(trade_data)
                    
                    # Добавление данных для самообучения
                    self.learning.add_trade_result(trade_details, trade_data)
                    
                    break
                    
                await asyncio.sleep(5)
                
            except Exception as e:
                logger.error(f"Error monitoring position #{ticket}: {e}")
                await asyncio.sleep(10)
                
    async def _check_and_retrain(self):
        """Проверка необходимости ретрейнинга"""
        stats = self.risk.get_statistics()
        
        if stats['total_trades'] >= self.config.ML_RETRAIN_AFTER_TRADES:
            if stats['total_trades'] % self.config.ML_RETRAIN_AFTER_TRADES == 0:
                logger.info("🔄 Starting model retraining...")
                await self.notifier.send_message("🔄 Retraining ML model...")
                
                success = await self.learning.retrain_model()
                
                if success:
                    logger.info("✅ Model retrained successfully")
                    await self.notifier.send_message("✅ Model retrained")
                else:
                    logger.error("❌ Retraining failed")
                    
    async def _send_statistics(self):
        """Отправка статистики"""
        stats = self.risk.get_statistics()
        account_info = self.mt5.get_account_info()
        
        message = f"""
📊 Trading Statistics

Balance: ${account_info['balance']:.2f}
Equity: ${account_info['equity']:.2f}
Profit: ${account_info['profit']:.2f}

Total Trades: {stats['total_trades']}
Winrate: {stats['winrate']:.2%}
Avg Win: ${stats['avg_win']:.2f}
Avg Loss: ${stats['avg_loss']:.2f}

Today: {stats['daily_trades']} trades, ${stats['daily_profit']:.2f}
        """
        
        await self.notifier.send_message(message)
        
    def stop(self):
        """Остановка бота"""
        logger.warning("Stopping bot engine...")
        self.running = False
        
        # Закрытие всех открытых позиций
        positions = self.mt5.get_open_positions()
        for position in positions:
            logger.warning(f"Closing position #{position['ticket']}")
            self.strategy.close_position_and_record(position['ticket'], reason='bot_shutdown')


import pandas as pd
