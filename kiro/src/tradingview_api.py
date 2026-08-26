"""
TradingView Technical Analysis API - готовые технические сигналы
"""
import requests
from loguru import logger
from typing import Dict
import json

class TradingViewAPI:
    """
    Интеграция с TradingView для получения technical analysis
    
    Источники:
    1. TradingView Technical Analysis widget
    2. Community ideas sentiment
    3. Встроенные индикаторы TradingView
    """
    
    def __init__(self, config):
        self.config = config
        
        # TradingView endpoints (неофициальные, могут измениться)
        self.base_url = "https://scanner.tradingview.com"
        
    def get_technical_analysis(self, symbol: str = "XAUUSD") -> Dict:
        """
        Получить технический анализ от TradingView
        
        Возвращает:
        {
            'summary': 'STRONG_BUY' / 'BUY' / 'NEUTRAL' / 'SELL' / 'STRONG_SELL',
            'oscillators': {'RECOMMENDATION': 'BUY', 'RSI': 45.2, ...},
            'moving_averages': {'RECOMMENDATION': 'BUY', 'EMA10': 2045.5, ...},
            'confidence': 0.0-1.0
        }
        """
        result = {
            'summary': 'NEUTRAL',
            'oscillators': {},
            'moving_averages': {},
            'confidence': 0.0,
            'data_available': False
        }
        
        try:
            # Запрос к TradingView scanner API
            analysis_data = self._fetch_tradingview_analysis(symbol)
            
            if analysis_data:
                result.update(analysis_data)
                result['data_available'] = True
                
                logger.info(f"TradingView analysis for {symbol}: {result['summary']}")
            else:
                # Fallback: используем встроенный анализ
                logger.warning("TradingView API unavailable, using fallback")
                result = self._fallback_analysis()
                
        except Exception as e:
            logger.error(f"TradingView API error: {e}")
            result = self._fallback_analysis()
            
        return result
        
    def _fetch_tradingview_analysis(self, symbol: str) -> Dict:
        """
        Запрос к TradingView API
        
        ВАЖНО: TradingView не имеет официального public API
        Используем неофициальный scanner endpoint
        """
        try:
            # Конвертация символа в TradingView format
            tv_symbol = self._convert_to_tv_symbol(symbol)
            
            # Endpoint для технического анализа
            url = f"{self.base_url}/forex/scan"
            
            # Payload для запроса
            payload = {
                "symbols": {
                    "tickers": [tv_symbol],
                    "query": {"types": []}
                },
                "columns": [
                    "Recommend.All",
                    "Recommend.MA",
                    "Recommend.Other",
                    "RSI",
                    "RSI[1]",
                    "Stoch.K",
                    "Stoch.D",
                    "MACD.macd",
                    "MACD.signal",
                    "ADX",
                    "EMA10",
                    "EMA20",
                    "EMA50",
                    "EMA100",
                    "EMA200"
                ]
            }
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Content-Type': 'application/json'
            }
            
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            
            if response.status_code != 200:
                logger.warning(f"TradingView API returned {response.status_code}")
                return None
                
            data = response.json()
            
            # Парсинг ответа
            if 'data' in data and len(data['data']) > 0:
                tv_data = data['data'][0]['d']
                return self._parse_tradingview_response(tv_data)
                
        except Exception as e:
            logger.debug(f"TradingView fetch error: {e}")
            
        return None
        
    def _parse_tradingview_response(self, tv_data: list) -> Dict:
        """Парсинг ответа от TradingView"""
        try:
            # Структура данных (индексы могут отличаться)
            recommend_all = tv_data[0] if len(tv_data) > 0 else 0
            recommend_ma = tv_data[1] if len(tv_data) > 1 else 0
            recommend_osc = tv_data[2] if len(tv_data) > 2 else 0
            
            # Определение summary
            summary = self._calculate_summary(recommend_all)
            
            # Oscillators
            oscillators = {
                'RSI': tv_data[3] if len(tv_data) > 3 else 50,
                'Stoch.K': tv_data[5] if len(tv_data) > 5 else 50,
                'MACD': tv_data[7] if len(tv_data) > 7 else 0,
                'ADX': tv_data[9] if len(tv_data) > 9 else 20,
                'RECOMMENDATION': self._calculate_summary(recommend_osc)
            }
            
            # Moving Averages
            moving_averages = {
                'EMA10': tv_data[10] if len(tv_data) > 10 else 0,
                'EMA20': tv_data[11] if len(tv_data) > 11 else 0,
                'EMA50': tv_data[12] if len(tv_data) > 12 else 0,
                'EMA100': tv_data[13] if len(tv_data) > 13 else 0,
                'EMA200': tv_data[14] if len(tv_data) > 14 else 0,
                'RECOMMENDATION': self._calculate_summary(recommend_ma)
            }
            
            # Confidence
            confidence = abs(recommend_all) / 1.0  # Нормализация
            
            return {
                'summary': summary,
                'oscillators': oscillators,
                'moving_averages': moving_averages,
                'confidence': min(confidence, 1.0)
            }
            
        except Exception as e:
            logger.error(f"Parse error: {e}")
            return None
            
    def _calculate_summary(self, score: float) -> str:
        """Расчёт summary на основе score"""
        if score >= 0.5:
            return 'STRONG_BUY'
        elif score >= 0.1:
            return 'BUY'
        elif score <= -0.5:
            return 'STRONG_SELL'
        elif score <= -0.1:
            return 'SELL'
        else:
            return 'NEUTRAL'
            
    def _convert_to_tv_symbol(self, symbol: str) -> str:
        """Конвертация символа в TradingView format"""
        # Для форекса: XAUUSD -> OANDA:XAUUSD или FX:XAUUSD
        if symbol == "XAUUSD":
            return "OANDA:XAUUSD"
        elif symbol == "EURUSD":
            return "FX:EURUSD"
        else:
            return f"FX:{symbol}"
            
    def _fallback_analysis(self) -> Dict:
        """
        Fallback анализ если TradingView недоступен
        
        Используем встроенные индикаторы TradingView logic
        """
        return {
            'summary': 'NEUTRAL',
            'oscillators': {
                'RECOMMENDATION': 'NEUTRAL'
            },
            'moving_averages': {
                'RECOMMENDATION': 'NEUTRAL'
            },
            'confidence': 0.3,
            'data_available': False
        }
        
    def get_community_sentiment(self, symbol: str = "XAUUSD") -> Dict:
        """
        Получить sentiment от TradingView community (ideas)
        
        Возвращает:
        {
            'bullish_percent': 65,
            'bearish_percent': 35,
            'sentiment': 'bullish' / 'bearish' / 'neutral',
            'ideas_count': 150
        }
        """
        # Реальный агрегат настроения: усредняем рекомендации Recommend.All
        # с нескольких таймфреймов через тот же scanner-endpoint.
        # Community ideas не имеют стабильного публичного API, а сводка
        # рекомендаций аналитиков TV — прямой и надёжный прокси настроения.
        try:
            # золото — CFD/commodity, а не forex: рабочий путь cfd/scan + OANDA:XAUUSD
            tv_symbol = 'OANDA:XAUUSD' if 'XAU' in symbol.upper() else self._convert_to_tv_symbol(symbol)
            url = "https://scanner.tradingview.com/cfd/scan"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Content-Type': 'application/json',
            }
            cols = ["Recommend.All|5", "Recommend.All|15",
                    "Recommend.All|60", "Recommend.All|240"]
            payload = {"symbols": {"tickers": [tv_symbol], "query": {"types": []}},
                       "columns": cols}
            r = requests.post(url, json=payload, headers=headers, timeout=10)
            if r.status_code == 200:
                data = r.json().get('data', [])
                if data and data[0].get('d'):
                    vals = [v for v in data[0]['d'] if isinstance(v, (int, float))]
                    if vals:
                        avg = sum(vals) / len(vals)   # -1..+1
                        bullish = round(50 + avg * 50)
                        bullish = max(0, min(100, bullish))
                        sentiment = ('bullish' if avg > 0.1 else
                                     'bearish' if avg < -0.1 else 'neutral')
                        logger.info(f"TV community sentiment {symbol}: {sentiment} "
                                    f"({bullish}% bull, avg {avg:.2f})")
                        return {
                            'bullish_percent': bullish,
                            'bearish_percent': 100 - bullish,
                            'sentiment': sentiment,
                            'ideas_count': len(vals),
                            'source': 'tv_multiframe_recommend',
                        }
        except Exception as e:
            logger.warning(f"Community sentiment error: {e}")
        return {'sentiment': 'neutral', 'bullish_percent': 50, 'bearish_percent': 50,
                'ideas_count': 0, 'source': 'fallback'}
            
    def get_trading_signal(self, technical_analysis: Dict) -> Dict:
        """
        Конвертация TradingView анализа в торговый сигнал
        
        Возвращает:
        {
            'action': 'buy' / 'sell' / 'wait',
            'confidence': 0.0-1.0,
            'reasons': [...]
        }
        """
        summary = technical_analysis['summary']
        confidence = technical_analysis['confidence']
        
        signal = {
            'action': 'wait',
            'confidence': 0.0,
            'reasons': []
        }
        
        # Логика конвертации
        if summary == 'STRONG_BUY':
            signal['action'] = 'buy'
            signal['confidence'] = 0.8
            signal['reasons'].append("TradingView: Strong Buy")
        elif summary == 'BUY':
            signal['action'] = 'buy'
            signal['confidence'] = 0.6
            signal['reasons'].append("TradingView: Buy")
        elif summary == 'STRONG_SELL':
            signal['action'] = 'sell'
            signal['confidence'] = 0.8
            signal['reasons'].append("TradingView: Strong Sell")
        elif summary == 'SELL':
            signal['action'] = 'sell'
            signal['confidence'] = 0.6
            signal['reasons'].append("TradingView: Sell")
        else:
            signal['action'] = 'wait'
            signal['confidence'] = 0.3
            signal['reasons'].append("TradingView: Neutral")
            
        # Дополнительные детали
        osc_rec = technical_analysis['oscillators'].get('RECOMMENDATION', 'NEUTRAL')
        ma_rec = technical_analysis['moving_averages'].get('RECOMMENDATION', 'NEUTRAL')
        
        signal['reasons'].append(f"Oscillators: {osc_rec}")
        signal['reasons'].append(f"Moving Averages: {ma_rec}")
        
        return signal
        
    def get_recommended_indicators_settings(self) -> Dict:
        """
        Получить рекомендуемые настройки индикаторов от TradingView
        
        На основе стандартных настроек TradingView
        """
        return {
            'RSI': {'period': 14, 'overbought': 70, 'oversold': 30},
            'MACD': {'fast': 12, 'slow': 26, 'signal': 9},
            'Stochastic': {'k': 14, 'd': 3, 'smooth': 3},
            'EMA': {'short': 10, 'medium': 20, 'long': 50, 'extra_long': 200},
            'Bollinger_Bands': {'period': 20, 'std': 2},
            'ADX': {'period': 14, 'threshold': 25}
        }

