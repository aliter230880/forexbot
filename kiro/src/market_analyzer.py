"""
Market Analyzer - многослойная аналитика рынка
"""
import pandas as pd
import numpy as np
from loguru import logger
import requests
from datetime import datetime, timedelta
import MetaTrader5 as mt5

class MarketAnalyzer:
    """Многослойный анализ рынка для принятия торговых решений"""
    
    def __init__(self, mt5_connector, config):
        self.mt5 = mt5_connector
        self.config = config
        self.dxy_cache = None
        self.vix_cache = None
        self.cache_time = None
        
    def analyze(self, df: pd.DataFrame) -> dict:
        """
        Полный анализ рынка
        Возвращает словарь с сигналами и уверенностью
        """
        result = {
            'signal': 'WAIT',  # BUY, SELL, WAIT
            'confidence': 0.0,  # 0.0 - 1.0
            'reasons': [],
            'technical': {},
            'macro': {},
            'sentiment': {},
            'microstructure': {}
        }
        
        # 1. Технический анализ
        result['technical'] = self._technical_analysis(df)
        
        # 2. Макро-контекст (корреляции)
        result['macro'] = self._macro_analysis()
        
        # 3. Sentiment analysis
        result['sentiment'] = self._sentiment_analysis()
        
        # 4. Микроструктура рынка
        result['microstructure'] = self._microstructure_analysis()
        
        # 5. Агрегация сигналов
        result = self._aggregate_signals(result)
        
        return result
        
    def _technical_analysis(self, df: pd.DataFrame) -> dict:
        """Технический анализ с индикаторами"""
        if df.empty or len(df) < 50:
            return {'signal': 'WAIT', 'score': 0}
            
        result = {
            'signal': 'WAIT',
            'score': 0,
            'indicators': {}
        }
        
        try:
            # RSI
            rsi = self._calculate_rsi(df['close'], self.config.RSI_PERIOD)
            current_rsi = rsi.iloc[-1]
            result['indicators']['rsi'] = current_rsi
            
            if current_rsi < 30:
                result['score'] += 1
                result['indicators']['rsi_signal'] = 'oversold'
            elif current_rsi > 70:
                result['score'] -= 1
                result['indicators']['rsi_signal'] = 'overbought'
            
            # EMA Cross
            ema_fast = df['close'].ewm(span=self.config.EMA_FAST).mean()
            ema_slow = df['close'].ewm(span=self.config.EMA_SLOW).mean()
            
            result['indicators']['ema_fast'] = ema_fast.iloc[-1]
            result['indicators']['ema_slow'] = ema_slow.iloc[-1]
            
            # Проверка кроссовера
            if ema_fast.iloc[-1] > ema_slow.iloc[-1] and ema_fast.iloc[-2] <= ema_slow.iloc[-2]:
                result['score'] += 2
                result['indicators']['ema_cross'] = 'bullish'
            elif ema_fast.iloc[-1] < ema_slow.iloc[-1] and ema_fast.iloc[-2] >= ema_slow.iloc[-2]:
                result['score'] -= 2
                result['indicators']['ema_cross'] = 'bearish'
            else:
                result['indicators']['ema_cross'] = 'none'
            
            # Bollinger Bands
            bb_result = self._calculate_bollinger_bands(df['close'], self.config.BB_PERIOD)
            current_price = df['close'].iloc[-1]
            
            result['indicators']['bb_upper'] = bb_result['upper'].iloc[-1]
            result['indicators']['bb_middle'] = bb_result['middle'].iloc[-1]
            result['indicators']['bb_lower'] = bb_result['lower'].iloc[-1]
            
            if current_price <= bb_result['lower'].iloc[-1]:
                result['score'] += 1
                result['indicators']['bb_signal'] = 'lower_bounce'
            elif current_price >= bb_result['upper'].iloc[-1]:
                result['score'] -= 1
                result['indicators']['bb_signal'] = 'upper_bounce'
            
            # ATR для волатильности
            atr = self._calculate_atr(df, self.config.ATR_PERIOD)
            result['indicators']['atr'] = atr.iloc[-1]
            
            # Определение тренда на M15
            trend = self._detect_trend(df)
            result['indicators']['trend'] = trend
            
            if trend == 'uptrend':
                result['score'] += 1
            elif trend == 'downtrend':
                result['score'] -= 1
            
            # Финальный сигнал
            if result['score'] >= 2:
                result['signal'] = 'BUY'
            elif result['score'] <= -2:
                result['signal'] = 'SELL'
            else:
                result['signal'] = 'WAIT'
                
        except Exception as e:
            logger.error(f"Technical analysis error: {e}")
            
        return result
        
    def _macro_analysis(self) -> dict:
        """Макро-анализ: USD Index, VIX, корреляции"""
        result = {
            'dxy': None,
            'vix': None,
            'gold_correlation': 'neutral',
            'score': 0
        }
        
        try:
            # Кэширование (обновлять раз в час)
            now = datetime.now()
            if self.cache_time is None or (now - self.cache_time).seconds > 3600:
                self._update_macro_cache()
                self.cache_time = now
            
            result['dxy'] = self.dxy_cache
            result['vix'] = self.vix_cache
            
            # Золото обратно коррелирует с DXY
            if self.dxy_cache:
                if self.dxy_cache['change_percent'] < -0.5:
                    result['score'] += 1  # DXY падает -> золото растёт
                    result['gold_correlation'] = 'bullish'
                elif self.dxy_cache['change_percent'] > 0.5:
                    result['score'] -= 1
                    result['gold_correlation'] = 'bearish'
            
            # Высокий VIX -> flight to safety (золото растёт)
            if self.vix_cache:
                if self.vix_cache['value'] > 25:
                    result['score'] += 0.5
                    
        except Exception as e:
            logger.error(f"Macro analysis error: {e}")
            
        return result
        
    def _sentiment_analysis(self) -> dict:
        """Sentiment анализ из социальных сетей и брокеров"""
        result = {
            'social_sentiment': 'neutral',
            'broker_positioning': None,
            'score': 0
        }
        
        try:
            # Twitter sentiment (если есть API token)
            if self.config.TWITTER_BEARER_TOKEN:
                sentiment = self._get_twitter_sentiment()
                result['social_sentiment'] = sentiment
                
                if sentiment == 'bullish':
                    result['score'] += 0.5
                elif sentiment == 'bearish':
                    result['score'] -= 0.5
            
            # Broker positioning (контр-индикатор)
            # Если 80% розницы в лонгах -> вероятно падение
            positioning = self._get_broker_positioning()
            if positioning:
                result['broker_positioning'] = positioning
                
                if positioning['long_percent'] > 75:
                    result['score'] -= 1  # Слишком много лонгов = контр-сигнал
                elif positioning['long_percent'] < 25:
                    result['score'] += 1
                    
        except Exception as e:
            logger.error(f"Sentiment analysis error: {e}")
            
        return result
        
    def _microstructure_analysis(self) -> dict:
        """Анализ микроструктуры: спред, объёмы, order flow"""
        result = {
            'spread': 0,
            'spread_quality': 'good',
            'volume_trend': 'neutral',
            'score': 0
        }
        
        try:
            # Проверка спреда
            spread = self.mt5.get_spread()
            result['spread'] = spread
            
            if spread > self.config.MAX_SPREAD_PIPS:
                result['spread_quality'] = 'bad'
                result['score'] -= 5  # Блокирующий фактор
                logger.warning(f"Spread too high: {spread} pips")
            else:
                result['spread_quality'] = 'good'
            
            # Volume analysis (если доступен тиковый объём)
            # В реальном MT5 можно получить tick_volume
            # Здесь упрощённая версия
            
        except Exception as e:
            logger.error(f"Microstructure analysis error: {e}")
            
        return result
        
    def _aggregate_signals(self, analysis: dict) -> dict:
        """Агрегация всех сигналов в финальное решение"""
        total_score = 0
        reasons = []
        
        # Технический анализ (вес 40%)
        tech_score = analysis['technical'].get('score', 0) * 0.4
        total_score += tech_score
        if analysis['technical']['signal'] != 'WAIT':
            reasons.append(f"Technical: {analysis['technical']['signal']} (score: {analysis['technical']['score']})")
        
        # Макро-анализ (вес 30%)
        macro_score = analysis['macro'].get('score', 0) * 0.3
        total_score += macro_score
        if analysis['macro']['gold_correlation'] != 'neutral':
            reasons.append(f"Macro: {analysis['macro']['gold_correlation']}")
        
        # Sentiment (вес 20%)
        sentiment_score = analysis['sentiment'].get('score', 0) * 0.2
        total_score += sentiment_score
        if analysis['sentiment']['social_sentiment'] != 'neutral':
            reasons.append(f"Sentiment: {analysis['sentiment']['social_sentiment']}")
        
        # Микроструктура (вес 10%, но может блокировать)
        micro_score = analysis['microstructure'].get('score', 0)
        
        # Блокирующие факторы
        if analysis['microstructure']['spread_quality'] == 'bad':
            analysis['signal'] = 'WAIT'
            analysis['confidence'] = 0
            analysis['reasons'] = ['BLOCKED: Spread too high']
            return analysis
        
        total_score += micro_score * 0.1
        
        # Финальный сигнал
        if total_score >= 1.5:
            analysis['signal'] = 'BUY'
            analysis['confidence'] = min(total_score / 3.0, 1.0)
        elif total_score <= -1.5:
            analysis['signal'] = 'SELL'
            analysis['confidence'] = min(abs(total_score) / 3.0, 1.0)
        else:
            analysis['signal'] = 'WAIT'
            analysis['confidence'] = 0
            
        analysis['reasons'] = reasons
        analysis['total_score'] = total_score
        
        return analysis
        
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
        
        return {
            'upper': upper,
            'middle': middle,
            'lower': lower
        }
        
    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Расчёт ATR (Average True Range)"""
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        atr = true_range.rolling(period).mean()
        
        return atr
        
    def _detect_trend(self, df: pd.DataFrame) -> str:
        """Определение тренда на M15"""
        if len(df) < 50:
            return 'neutral'
            
        # Используем EMA50 для определения тренда
        ema50 = df['close'].ewm(span=50).mean()
        current_price = df['close'].iloc[-1]
        ema50_value = ema50.iloc[-1]
        
        # Проверка наклона EMA
        ema50_slope = ema50.iloc[-1] - ema50.iloc[-10]
        
        if current_price > ema50_value and ema50_slope > 0:
            return 'uptrend'
        elif current_price < ema50_value and ema50_slope < 0:
            return 'downtrend'
        else:
            return 'neutral'
            
    def _update_macro_cache(self):
        """Обновление кэша макро-данных (реальные котировки через yfinance)."""
        try:
            dxy = self._fetch_yf('DX-Y.NYB')          # индекс доллара
            if dxy is None and self.config.ALPHA_VANTAGE_API_KEY:
                dxy = self._fetch_alpha_vantage('DXY')
            self.dxy_cache = dxy or {'value': None, 'change_percent': 0.0}

            vix = self._fetch_vix()
            self.vix_cache = vix
        except Exception as e:
            logger.error(f"Failed to update macro cache: {e}")

    def _fetch_yf(self, ticker: str) -> dict:
        """Реальная котировка + дневное изменение через yfinance."""
        try:
            import yfinance as yf
            hist = yf.Ticker(ticker).history(period='5d', interval='1d')
            if hist is None or len(hist) < 2:
                return None
            last = float(hist['Close'].iloc[-1])
            prev = float(hist['Close'].iloc[-2])
            chg = (last - prev) / prev * 100 if prev else 0.0
            logger.info(f"yfinance {ticker}: {last:.2f} ({chg:+.2f}%)")
            return {'value': round(last, 2), 'change_percent': round(chg, 2)}
        except Exception as e:
            logger.warning(f"yfinance {ticker} error: {e}")
            return None
            
    def _fetch_alpha_vantage(self, symbol: str) -> dict:
        """Получение данных через Alpha Vantage API"""
        try:
            url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={self.config.ALPHA_VANTAGE_API_KEY}"
            response = requests.get(url, timeout=5)
            data = response.json()
            
            if 'Global Quote' in data:
                quote = data['Global Quote']
                return {
                    'value': float(quote.get('05. price', 0)),
                    'change_percent': float(quote.get('10. change percent', '0').replace('%', ''))
                }
        except Exception as e:
            logger.error(f"Alpha Vantage API error: {e}")
            
        return None
        
    def _fetch_vix(self) -> dict:
        """Реальный VIX через yfinance (тикер ^VIX)."""
        data = self._fetch_yf('^VIX')
        if data and data.get('value'):
            return data
        return {'value': None, 'change_percent': 0.0}

    def _get_twitter_sentiment(self) -> str:
        """Sentiment соцсетей.

        Twitter/X API v2 платный ($100+/мес) — без ключа честно возвращаем
        neutral. Реальный новостной sentiment считается в SentimentAnalyzer
        (RSS + FinBERT), он и используется в решении вместо твиттера.
        """
        token = getattr(self.config, 'TWITTER_BEARER_TOKEN', '')
        if not token:
            return 'neutral'
        # при наличии ключа — заглушка под подключение X API v2 recent search
        try:
            import requests
            r = requests.get(
                'https://api.twitter.com/2/tweets/search/recent',
                params={'query': '#XAUUSD OR #Gold -is:retweet', 'max_results': 50},
                headers={'Authorization': f'Bearer {token}'}, timeout=10)
            if r.status_code == 200:
                # анализ через общий словарный метод не тут, вернём агрегат позже
                return 'neutral'
        except Exception as e:
            logger.debug(f"Twitter API error: {e}")
        return 'neutral'

    def _get_broker_positioning(self) -> dict:
        """Позиционирование по золоту.

        Retail long/short от брокеров закрыт без платных фидов (Myfxbook/IG).
        Как реальный публичный прокси используем недельный COT-отчёт CFTC
        (managed money net position по gold futures) через yfinance-недоступно,
        поэтому оцениваем bias по DXY: сильный доллар → давление на золото.
        """
        dxy = getattr(self, 'dxy_cache', None) or {}
        chg = dxy.get('change_percent')
        if chg is None:
            return {'long_percent': 50, 'short_percent': 50, 'source': 'n/a'}
        # DXY растёт → золото под давлением (меньше лонгов)
        long_pct = max(30, min(70, 50 - chg * 8))
        return {
            'long_percent': round(long_pct),
            'short_percent': round(100 - long_pct),
            'source': 'dxy_proxy',
        }
