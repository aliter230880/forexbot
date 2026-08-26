"""
Self Learning System - самообучение бота на собственных сделках
"""
import pandas as pd
import numpy as np
from loguru import logger
from datetime import datetime, timedelta
import MetaTrader5 as mt5

class SelfLearningSystem:
    """Система самообучения для адаптации к изменениям рынка"""
    
    def __init__(self, config, ml_predictor, risk_manager):
        self.config = config
        self.ml = ml_predictor
        self.risk = risk_manager
        
        # Накопление данных для обучения
        self.training_buffer = []
        
    def add_trade_result(self, trade_details: dict, trade_result: dict):
        """
        Добавление результата сделки в буфер обучения
        trade_details: детали сделки при открытии (фичи, сигналы)
        trade_result: результат закрытия (profit, exit_price)
        """
        try:
            # Создание обучающего примера
            training_sample = {
                'timestamp': datetime.now(),
                'features': trade_details.get('features'),
                'market_signal': trade_details.get('market_signal'),
                'ml_signal': trade_details.get('ml_signal'),
                'direction': trade_details.get('direction'),
                'entry_price': trade_details.get('entry_price'),
                'exit_price': trade_result.get('exit_price'),
                'profit': trade_result.get('profit'),
                'success': trade_result.get('profit', 0) > 0
            }
            
            self.training_buffer.append(training_sample)
            
            logger.debug(f"Added training sample (buffer size: {len(self.training_buffer)})")
            
        except Exception as e:
            logger.error(f"Error adding training sample: {e}")
            
    async def retrain_model(self) -> bool:
        """
        Переобучение модели на новых данных
        Использует как исторические данные, так и результаты собственных сделок
        """
        try:
            logger.info("🔄 Starting model retraining...")
            
            # 1. Загрузка свежих исторических данных
            from src.mt5_connector import MT5Connector
            # Предполагаем доступ к MT5 через self.risk.mt5
            df = self.risk.mt5.get_historical_data(timeframe=mt5.TIMEFRAME_M5, bars=2000)
            
            if df.empty:
                logger.error("No historical data for retraining")
                return False
                
            # 2. Feature extraction
            features_list = []
            for i in range(100, len(df) - 10):
                window = df.iloc[:i+1]
                features = self.ml.extract_features(window)
                if not features.empty:
                    features_list.append(features.iloc[0])
                    
            if len(features_list) < self.config.ML_MIN_TRAINING_SAMPLES:
                logger.warning(f"Not enough samples: {len(features_list)}")
                return False
                
            features_df = pd.DataFrame(features_list)
            labels = self.ml.create_labels(df.iloc[100:])
            
            # 3. Добавление данных из реальных сделок с повышенным весом
            real_trade_features = self._extract_real_trade_features()
            if len(real_trade_features) > 0:
                logger.info(f"Adding {len(real_trade_features)} real trade samples")
                # TODO: реализовать sample weighting в XGBoost
                
            # 4. Обучение
            success = self.ml.train(features_df, labels[:len(features_df)])
            
            if success:
                # Очистка буфера после успешного обучения
                self.training_buffer = []
                logger.info("✅ Retraining completed successfully")
                
                # Анализ улучшений
                self._analyze_improvements()
                
            return success
            
        except Exception as e:
            logger.exception(f"Retraining error: {e}")
            return False
            
    def _extract_real_trade_features(self) -> pd.DataFrame:
        """Фичи из реальных сделок: собираем сохранённые при входе feature-строки.

        В add_trade_result каждая сделка кладёт в буфер поле 'features'
        (строка признаков на момент входа). Здесь собираем их в DataFrame —
        это материал для дообучения на собственных результатах.
        """
        if len(self.training_buffer) == 0:
            return pd.DataFrame()
        rows = []
        for trade in self.training_buffer:
            feat = trade.get('features')
            if feat is None:
                continue
            if isinstance(feat, pd.DataFrame) and len(feat):
                rows.append(feat.iloc[-1])
            elif isinstance(feat, dict):
                rows.append(pd.Series(feat))
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows).reset_index(drop=True)
        
    def _analyze_improvements(self):
        """Анализ улучшений после ретрейнинга"""
        stats = self.risk.get_statistics()
        
        # Простая эвристика: сравнение winrate до и после последних N сделок
        if len(stats['trade_history']) < 20:
            return
            
        recent_20 = stats['trade_history'][-20:]
        wins_recent = sum(1 for t in recent_20 if t.get('profit', 0) > 0)
        winrate_recent = wins_recent / 20
        
        overall_winrate = stats['winrate']
        
        logger.info(f"""
📈 Performance Analysis:
   Overall winrate: {overall_winrate:.2%}
   Recent 20 trades: {winrate_recent:.2%}
   Trend: {'📈 Improving' if winrate_recent > overall_winrate else '📉 Declining' if winrate_recent < overall_winrate else '➡️  Stable'}
        """)
        
    def optimize_parameters(self):
        """
        Оптимизация параметров стратегии на основе результатов
        Использует генетические алгоритмы или grid search
        """
        # TODO: реализация GA для оптимизации TP/SL/trailing_stop
        pass
        
    def detect_pattern_shifts(self) -> dict:
        """
        Определение смены паттернов рынка
        Возвращает: {'shift_detected': bool, 'confidence': float}
        """
        stats = self.risk.get_statistics()
        
        if len(stats['trade_history']) < 50:
            return {'shift_detected': False, 'confidence': 0.0}
            
        # Разделение на две половины
        mid = len(stats['trade_history']) // 2
        first_half = stats['trade_history'][:mid]
        second_half = stats['trade_history'][mid:]
        
        # Winrate в каждой половине
        winrate_1 = sum(1 for t in first_half if t.get('profit', 0) > 0) / len(first_half)
        winrate_2 = sum(1 for t in second_half if t.get('profit', 0) > 0) / len(second_half)
        
        # Существенное изменение?
        diff = abs(winrate_2 - winrate_1)
        
        if diff > 0.15:  # Изменение более 15%
            logger.warning(f"⚠️  Pattern shift detected: winrate changed from {winrate_1:.2%} to {winrate_2:.2%}")
            return {'shift_detected': True, 'confidence': diff}
            
        return {'shift_detected': False, 'confidence': 0.0}
