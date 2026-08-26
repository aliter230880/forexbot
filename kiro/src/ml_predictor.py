"""
ML Predictor - машинное обучение для предсказания направления
"""
import pandas as pd
import numpy as np
from loguru import logger
import xgboost as xgb
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report
import joblib
from pathlib import Path
import MetaTrader5 as mt5

class MLPredictor:
    """ML-модель для предсказания направления движения цены"""
    
    def __init__(self, config):
        self.config = config
        self.model = None
        self.feature_names = []
        self.is_trained = False
        self.load_model()
        
    def load_model(self):
        """Загрузка обученной модели"""
        model_path = Path(self.config.ML_MODEL_PATH)
        
        if model_path.exists():
            try:
                self.model = xgb.XGBClassifier()
                self.model.load_model(str(model_path))
                self.is_trained = True
                logger.info(f"✅ ML model loaded from {model_path}")
            except Exception as e:
                logger.error(f"Failed to load model: {e}")
                self.is_trained = False
        else:
            logger.warning("No trained model found. Will train on first data batch.")
            
    def save_model(self):
        """Сохранение модели"""
        if self.model is not None:
            model_path = Path(self.config.ML_MODEL_PATH)
            model_path.parent.mkdir(exist_ok=True)
            self.model.save_model(str(model_path))
            logger.info(f"Model saved to {model_path}")
            
    def predict(self, features: pd.DataFrame) -> dict:
        """
        Предсказание направления
        Возвращает: {'direction': 'buy'/'sell'/'wait', 'probability': 0.0-1.0}
        """
        if not self.is_trained or self.model is None:
            return {'direction': 'wait', 'probability': 0.0}
            
        try:
            # Предсказание
            proba = self.model.predict_proba(features)
            predicted_class = np.argmax(proba[0])
            confidence = np.max(proba[0])
            
            # Классы: 0 = sell, 1 = wait, 2 = buy
            direction_map = {0: 'sell', 1: 'wait', 2: 'buy'}
            direction = direction_map[predicted_class]
            
            # Минимальный порог уверенности
            if confidence < 0.55:
                direction = 'wait'
                
            return {
                'direction': direction,
                'probability': confidence,
                'probabilities': {
                    'sell': proba[0][0],
                    'wait': proba[0][1],
                    'buy': proba[0][2]
                }
            }
            
        except Exception as e:
            logger.error(f"Prediction error: {e}")
            return {'direction': 'wait', 'probability': 0.0}
            
    def train(self, historical_data: pd.DataFrame, labels: pd.Series):
        """
        Обучение модели
        historical_data: датафрейм с фичами
        labels: целевые метки (0=sell, 1=wait, 2=buy)
        """
        if len(historical_data) < self.config.ML_MIN_TRAINING_SAMPLES:
            logger.warning(f"Not enough data for training: {len(historical_data)} < {self.config.ML_MIN_TRAINING_SAMPLES}")
            return False
            
        try:
            logger.info(f"Training ML model on {len(historical_data)} samples...")
            
            # Разделение на train/test
            X_train, X_test, y_train, y_test = train_test_split(
                historical_data, labels, test_size=0.2, random_state=42, stratify=labels
            )
            
            # Параметры модели
            params = {
                'objective': 'multi:softprob',
                'num_class': 3,
                'max_depth': 6,
                'learning_rate': 0.1,
                'n_estimators': 200,
                'subsample': 0.8,
                'colsample_bytree': 0.8,
                'random_state': 42,
                'eval_metric': 'mlogloss'
            }
            
            # Обучение
            self.model = xgb.XGBClassifier(**params)
            self.model.fit(
                X_train, y_train,
                eval_set=[(X_test, y_test)],
                verbose=False
            )
            
            # Оценка качества
            y_pred = self.model.predict(X_test)
            accuracy = accuracy_score(y_test, y_pred)
            
            logger.info(f"✅ Model trained. Accuracy: {accuracy:.2%}")
            logger.info(f"\n{classification_report(y_test, y_pred, target_names=['sell', 'wait', 'buy'])}")
            
            self.is_trained = True
            self.feature_names = list(historical_data.columns)
            self.save_model()
            
            return True
            
        except Exception as e:
            logger.exception(f"Training error: {e}")
            return False
            
    def extract_features(self, df: pd.DataFrame, market_analysis: dict = None,
                         full: bool = False) -> pd.DataFrame:
        """
        Feature engineering - создание признаков для модели
        Цель: 50+ признаков из OHLCV + индикаторов + макро

        full=False (live): вернуть только последнюю строку для предсказания
        full=True (обучение): вернуть все строки датасета
        """
        features = pd.DataFrame()
        
        try:
            # 1. Price-based features
            features['close'] = df['close']
            features['high'] = df['high']
            features['low'] = df['low']
            features['open'] = df['open']
            
            # Returns
            features['return_1'] = df['close'].pct_change(1)
            features['return_5'] = df['close'].pct_change(5)
            features['return_10'] = df['close'].pct_change(10)
            
            # Volatility
            features['volatility_10'] = df['close'].rolling(10).std()
            features['volatility_20'] = df['close'].rolling(20).std()
            
            # 2. Technical indicators
            # RSI
            features['rsi_14'] = self._calculate_rsi(df['close'], 14)
            features['rsi_7'] = self._calculate_rsi(df['close'], 7)
            
            # EMAs
            for period in [5, 9, 12, 21, 50]:
                features[f'ema_{period}'] = df['close'].ewm(span=period).mean()
                features[f'price_to_ema_{period}'] = df['close'] / features[f'ema_{period}']
            
            # EMA slopes
            features['ema_9_slope'] = features['ema_9'].diff(3)
            features['ema_21_slope'] = features['ema_21'].diff(5)
            
            # Bollinger Bands
            bb = self._calculate_bollinger_bands(df['close'], 20)
            features['bb_upper'] = bb['upper']
            features['bb_middle'] = bb['middle']
            features['bb_lower'] = bb['lower']
            features['bb_position'] = (df['close'] - bb['lower']) / (bb['upper'] - bb['lower'])
            features['bb_width'] = (bb['upper'] - bb['lower']) / bb['middle']
            
            # ATR
            atr = self._calculate_atr(df, 14)
            features['atr_14'] = atr
            features['atr_ratio'] = atr / df['close']
            
            # 3. Volume features (если доступны)
            if 'tick_volume' in df.columns:
                features['volume'] = df['tick_volume']
                features['volume_ma_10'] = df['tick_volume'].rolling(10).mean()
                features['volume_ratio'] = df['tick_volume'] / features['volume_ma_10']
            
            # 4. Time-based features
            if 'time' in df.columns:
                features['hour'] = pd.to_datetime(df['time']).dt.hour
                features['day_of_week'] = pd.to_datetime(df['time']).dt.dayofweek
                features['is_london_session'] = ((features['hour'] >= 8) & (features['hour'] <= 12)).astype(int)
                features['is_ny_session'] = ((features['hour'] >= 13) & (features['hour'] <= 17)).astype(int)
            
            # 5. Candlestick patterns
            features['body_size'] = abs(df['close'] - df['open'])
            features['upper_shadow'] = df['high'] - df[['close', 'open']].max(axis=1)
            features['lower_shadow'] = df[['close', 'open']].min(axis=1) - df['low']
            features['is_bullish'] = (df['close'] > df['open']).astype(int)
            
            # 6. Momentum indicators
            # MACD
            ema_12 = df['close'].ewm(span=12).mean()
            ema_26 = df['close'].ewm(span=26).mean()
            features['macd'] = ema_12 - ema_26
            features['macd_signal'] = features['macd'].ewm(span=9).mean()
            features['macd_hist'] = features['macd'] - features['macd_signal']
            
            # Stochastic
            low_14 = df['low'].rolling(14).min()
            high_14 = df['high'].rolling(14).max()
            features['stoch_k'] = 100 * (df['close'] - low_14) / (high_14 - low_14)
            features['stoch_d'] = features['stoch_k'].rolling(3).mean()
            
            # 7. Макро-фичи (если есть market_analysis)
            if market_analysis:
                if market_analysis.get('macro'):
                    macro = market_analysis['macro']
                    if macro.get('dxy'):
                        features['dxy_value'] = macro['dxy'].get('value', 0)
                        features['dxy_change'] = macro['dxy'].get('change_percent', 0)
                    if macro.get('vix'):
                        features['vix_value'] = macro['vix'].get('value', 0)
                        
                # Sentiment score
                if market_analysis.get('sentiment'):
                    sentiment = market_analysis['sentiment']
                    features['sentiment_score'] = sentiment.get('score', 0)
            
            # 8. Support/Resistance levels
            features['distance_to_high_20'] = (df['high'].rolling(20).max() - df['close']) / df['close']
            features['distance_to_low_20'] = (df['close'] - df['low'].rolling(20).min()) / df['close']
            
            # Удаление NaN (pandas 2.x: method= в fillna удалён, используем ffill())
            features = features.ffill().fillna(0)
            
            # full=True → весь датасет для обучения; иначе последняя строка (live)
            return features if full else features.iloc[[-1]]
            
        except Exception as e:
            logger.error(f"Feature extraction error: {e}")
            return pd.DataFrame()
            
    def create_labels(self, df: pd.DataFrame, lookahead: int = 3) -> pd.Series:
        """
        Создание меток для обучения
        Смотрим на цену через lookahead баров:
        - Если выросла > 0.05% -> buy (2)
        - Если упала > 0.05% -> sell (0)
        - Иначе -> wait (1)
        """
        labels = []
        
        for i in range(len(df) - lookahead):
            current_price = df['close'].iloc[i]
            future_price = df['close'].iloc[i + lookahead]
            
            change_percent = ((future_price - current_price) / current_price) * 100
            
            if change_percent > 0.05:
                labels.append(2)  # buy
            elif change_percent < -0.05:
                labels.append(0)  # sell
            else:
                labels.append(1)  # wait
                
        # Дополнить последние lookahead строк меткой "wait"
        labels.extend([1] * lookahead)
        
        return pd.Series(labels)
        
    # ===== Вспомогательные методы =====
    
    def _calculate_rsi(self, series: pd.Series, period: int = 14) -> pd.Series:
        """Расчёт RSI"""
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi
        
    def _calculate_bollinger_bands(self, series: pd.Series, period: int = 20, std: float = 2.0) -> dict:
        """Расчёт Bollinger Bands"""
        middle = series.rolling(window=period).mean()
        std_dev = series.rolling(window=period).std()
        upper = middle + (std_dev * std)
        lower = middle - (std_dev * std)
        
        return {'upper': upper, 'middle': middle, 'lower': lower}
        
    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Расчёт ATR"""
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        atr = true_range.rolling(period).mean()
        
        return atr
