"""
Liquidity Analyzer - анализ карты ликвидаций (где "умные деньги")

Источники карт ликвидаций:
1. Coinglass.com - для крипты, но можно адаптировать
2. TradingView - Volume Profile
3. Broker's own data (если доступно через API)
4. Aggregated orderbook data
"""
import pandas as pd
import numpy as np
from loguru import logger
from typing import Dict, List, Tuple
import requests
from bs4 import BeautifulSoup
import json

class LiquidityAnalyzer:
    """
    Анализ карты ликвидаций для определения направления "умных денег"
    
    Карта ликвидаций показывает:
    - Где сосредоточены стоп-лоссы розничных трейдеров
    - Куда крупные игроки "охотятся" за ликвидностью
    - Уровни, где ожидаются резкие движения
    """
    
    def __init__(self, mt5_connector, config):
        self.mt5 = mt5_connector
        self.config = config
        self.liquidity_zones = []
        
        # Источники данных
        self.data_sources = {
            'tradingview': 'https://www.tradingview.com',  # Volume Profile
            'investing': 'https://www.investing.com',      # Sentiment & orderbook
            'myfxbook': 'https://www.myfxbook.com',        # Retail positioning
        }
        
        self.liquidity_cache = {
            'data': [],
            'timestamp': None
        }
        
    def analyze_liquidity(self, df: pd.DataFrame) -> Dict:
        """
        Главный метод анализа ликвидности
        
        Возвращает:
        {
            'direction': 'up'/'down'/'neutral',
            'confidence': 0.0-1.0,
            'target_levels': [уровни ликвидности],
            'reasons': [причины]
        }
        """
        result = {
            'direction': 'neutral',
            'confidence': 0.0,
            'target_levels': [],
            'reasons': []
        }
        
        try:
            # 1. Парсинг внешних источников (если доступны)
            external_liquidity = self._fetch_external_liquidity_data()
            
            # 2. Определение зон ликвидности через volume profile
            liquidity_zones = self._find_liquidity_zones(df)
            
            # 3. Анализ Round Numbers (психологические уровни)
            round_levels = self._find_round_number_liquidity(df)
            
            # 4. Анализ swing highs/lows (где стоят стопы)
            swing_liquidity = self._find_swing_liquidity(df)
            
            # 5. Имбалансы ордербука (bid/ask дисбаланс)
            orderbook_imbalance = self._analyze_orderbook_imbalance()
            
            # 6. Retail positioning (где розница, туда НЕ идём)
            retail_positioning = self._get_retail_positioning()
            
            # 7. Определение направления охоты за ликвидностью
            direction, confidence = self._determine_liquidity_hunt_direction(
                liquidity_zones, round_levels, swing_liquidity, 
                orderbook_imbalance, external_liquidity, retail_positioning
            )
            
            result['direction'] = direction
            result['confidence'] = confidence
            result['target_levels'] = liquidity_zones + round_levels + swing_liquidity
            result['reasons'] = self._generate_reasons(direction, liquidity_zones, orderbook_imbalance, retail_positioning)
            
            logger.debug(f"Liquidity analysis: {direction} (confidence: {confidence:.2%})")
            
        except Exception as e:
            logger.error(f"Liquidity analysis error: {e}")
            
        return result
        
    def _find_liquidity_zones(self, df: pd.DataFrame) -> List[float]:
        """
        Поиск зон ликвидности через volume profile
        
        Зоны с высоким объёмом = много стопов = цель для крупных игроков
        """
        zones = []
        
        if 'tick_volume' not in df.columns:
            return zones
            
        try:
            # Создаём volume profile (гистограмма объёмов по ценовым уровням)
            price_levels = np.linspace(df['low'].min(), df['high'].max(), 50)
            volume_profile = []
            
            for level in price_levels:
                # Объём в диапазоне ±0.5 от уровня
                mask = (df['close'] >= level - 0.5) & (df['close'] <= level + 0.5)
                volume = df[mask]['tick_volume'].sum()
                volume_profile.append({'level': level, 'volume': volume})
                
            # Сортировка по объёму
            volume_profile = sorted(volume_profile, key=lambda x: x['volume'], reverse=True)
            
            # Топ-5 уровней с максимальным объёмом
            zones = [item['level'] for item in volume_profile[:5]]
            
        except Exception as e:
            logger.error(f"Volume profile error: {e}")
            
        return zones
        
    def _find_round_number_liquidity(self, df: pd.DataFrame) -> List[float]:
        """
        Психологические уровни (круглые числа)
        
        Для золота: 2000, 2050, 2100 и т.д.
        Розничные трейдеры ставят стопы на круглых числах
        """
        current_price = df['close'].iloc[-1]
        
        # Круглые уровни с шагом 50 для золота
        base = int(current_price / 50) * 50
        
        round_levels = [
            base - 100,
            base - 50,
            base,
            base + 50,
            base + 100
        ]
        
        # Фильтрация только близких к цене
        nearby_levels = [
            level for level in round_levels 
            if abs(level - current_price) <= 100  # в пределах 100 пунктов
        ]
        
        return nearby_levels
        
    def _find_swing_liquidity(self, df: pd.DataFrame) -> List[float]:
        """
        Swing highs/lows - уровни где стоят стопы трейдеров
        
        После пробития swing high/low часто идёт резкое движение
        (сбор стоп-лоссов)
        """
        liquidity_levels = []
        
        try:
            # Поиск локальных максимумов и минимумов (swing points)
            window = 10
            
            # Swing Highs
            df['swing_high'] = df['high'].rolling(window=window, center=True).apply(
                lambda x: x[window//2] == x.max(), raw=True
            )
            
            # Swing Lows
            df['swing_low'] = df['low'].rolling(window=window, center=True).apply(
                lambda x: x[window//2] == x.min(), raw=True
            )
            
            # Последние 20 баров
            recent_df = df.tail(20)
            
            # Swing highs (где стоят sell stops)
            swing_highs = recent_df[recent_df['swing_high'] == 1]['high'].tolist()
            
            # Swing lows (где стоят buy stops)
            swing_lows = recent_df[recent_df['swing_low'] == 1]['low'].tolist()
            
            liquidity_levels = swing_highs + swing_lows
            
        except Exception as e:
            logger.error(f"Swing liquidity error: {e}")
            
        return liquidity_levels[:5]  # Топ-5 ближайших
        
    def _analyze_orderbook_imbalance(self) -> Dict:
        """
        Анализ имбаланса ордербука (bid vs ask pressure)
        
        ВАЖНО: Требуется доступ к Level 2 данным (depth of market)
        Если брокер не предоставляет - используем упрощённую версию
        """
        # Реальный имбаланс по тикам за 15 мин (up-ticks vs down-ticks).
        # Level 2 у розничного MT5 нет, но направление тиков — рабочий прокси
        # давления покупателей/продавцов.
        symbol_info = self.mt5.get_symbol_info()
        bid = symbol_info.get('bid', 0)
        ask = symbol_info.get('ask', 0)
        spread = symbol_info.get('spread', 0)

        imbalance_score = 0.5
        try:
            import MetaTrader5 as mt5
            import numpy as np
            from datetime import datetime, timedelta
            ticks = mt5.copy_ticks_from(
                self.config.SYMBOL, datetime.now() - timedelta(minutes=15),
                2000, mt5.COPY_TICKS_ALL)
            if ticks is not None and len(ticks) > 30:
                mid = np.array([(t['bid'] + t['ask']) / 2 for t in ticks])
                diffs = np.diff(mid)
                up = float((diffs > 0).sum())
                dn = float((diffs < 0).sum())
                if up + dn > 0:
                    imbalance_score = round(up / (up + dn), 3)
        except Exception as e:
            logger.debug(f"orderbook imbalance tick error: {e}")

        return {
            'bid': bid,
            'ask': ask,
            'spread': spread,
            'imbalance_score': imbalance_score,
            'pressure': 'buy' if imbalance_score > 0.55 else 'sell' if imbalance_score < 0.45 else 'neutral'
        }
        
    def _determine_liquidity_hunt_direction(
        self, 
        liquidity_zones: List[float],
        round_levels: List[float],
        swing_liquidity: List[float],
        orderbook: Dict,
        external_liquidity: Dict,
        retail_positioning: Dict
    ) -> Tuple[str, float]:
        """
        Определение направления охоты за ликвидностью
        
        Логика:
        - Если выше текущей цены много ликвидности → цена пойдёт вверх собирать её
        - Если ниже текущей цены много ликвидности → цена пойдёт вниз
        - Учёт retail positioning (контр-индикатор)
        - Учёт external sentiment
        """
        current_price = self.mt5.get_current_price()[0]  # bid
        
        # Подсчёт ликвидности выше/ниже цены
        all_levels = liquidity_zones + round_levels + swing_liquidity
        
        above_price = [level for level in all_levels if level > current_price]
        below_price = [level for level in all_levels if level < current_price]
        
        # Взвешивание по близости (ближе = важнее)
        above_weight = sum([1 / (abs(level - current_price) + 1) for level in above_price])
        below_weight = sum([1 / (abs(level - current_price) + 1) for level in below_price])
        
        # Учёт orderbook pressure
        ob_pressure = orderbook.get('imbalance_score', 0.5)
        
        # Учёт retail positioning (контр-индикатор!)
        retail_bias = retail_positioning.get('bias', 'neutral')
        retail_conf = retail_positioning.get('confidence', 0.0)
        
        retail_score = 0.5  # нейтрально
        if retail_bias == 'bearish':
            retail_score = 0.3  # розница в лонгах → мы ждём падения
        elif retail_bias == 'bullish':
            retail_score = 0.7  # розница в шортах → мы ждём роста
            
        # Учёт external sentiment
        sentiment = external_liquidity.get('sentiment', 'neutral')
        sentiment_score = 0.5
        
        if sentiment == 'strong_buy':
            sentiment_score = 0.7
        elif sentiment == 'buy':
            sentiment_score = 0.6
        elif sentiment == 'strong_sell':
            sentiment_score = 0.3
        elif sentiment == 'sell':
            sentiment_score = 0.4
            
        # Финальный расчёт (взвешенная сумма)
        total_weight = above_weight + below_weight
        if total_weight == 0:
            return 'neutral', 0.0
            
        # Веса компонентов
        liquidity_weight = 0.4
        orderbook_weight = 0.2
        retail_weight = 0.25
        sentiment_weight = 0.15
        
        above_score = (
            (above_weight / total_weight) * liquidity_weight +
            ob_pressure * orderbook_weight +
            retail_score * retail_weight +
            sentiment_score * sentiment_weight
        )
        
        below_score = (
            (below_weight / total_weight) * liquidity_weight +
            (1 - ob_pressure) * orderbook_weight +
            (1 - retail_score) * retail_weight +
            (1 - sentiment_score) * sentiment_weight
        )
        
        # Решение
        if above_score > 0.6:
            return 'up', above_score
        elif below_score > 0.6:
            return 'down', below_score
        else:
            return 'neutral', 0.5
            
    def _generate_reasons(
        self, 
        direction: str, 
        liquidity_zones: List[float],
        orderbook: Dict,
        retail_positioning: Dict
    ) -> List[str]:
        """Генерация причин для направления"""
        reasons = []
        
        if direction == 'up':
            reasons.append(f"Liquidity hunt upward: {len(liquidity_zones)} zones above price")
            if orderbook['pressure'] == 'buy':
                reasons.append("Orderbook shows buy pressure")
                
            # Retail positioning
            if retail_positioning.get('bias') == 'bearish':
                retail_long = retail_positioning.get('retail_long_percent', 50)
                reasons.append(f"Retail heavily long ({retail_long:.0f}%) → contrarian signal for UP")
                
        elif direction == 'down':
            reasons.append(f"Liquidity hunt downward: {len(liquidity_zones)} zones below price")
            if orderbook['pressure'] == 'sell':
                reasons.append("Orderbook shows sell pressure")
                
            # Retail positioning
            if retail_positioning.get('bias') == 'bullish':
                retail_long = retail_positioning.get('retail_long_percent', 50)
                reasons.append(f"Retail heavily short ({100-retail_long:.0f}%) → contrarian signal for DOWN")
                
        else:
            reasons.append("Balanced liquidity distribution")
            
        return reasons
        
    def get_nearest_liquidity_target(self, direction: str) -> float:
        """Получить ближайший уровень ликвидности в заданном направлении"""
        if not self.liquidity_zones:
            return 0.0
            
        current_price = self.mt5.get_current_price()[0]
        
        if direction == 'up':
            targets = [z for z in self.liquidity_zones if z > current_price]
            return min(targets) if targets else 0.0
        else:
            targets = [z for z in self.liquidity_zones if z < current_price]
            return max(targets) if targets else 0.0
            
    # ===== ПАРСИНГ ВНЕШНИХ ИСТОЧНИКОВ =====
    
    def _fetch_external_liquidity_data(self) -> Dict:
        """
        Парсинг карт ликвидаций с внешних сайтов
        
        Источники:
        1. MyFxBook - retail positioning (где розница)
        2. Investing.com - sentiment indicator
        3. TradingView - public ideas sentiment
        """
        external_data = {
            'retail_long_percent': 50,  # По умолчанию нейтрально
            'sentiment': 'neutral',
            'data_available': False
        }
        
        try:
            # Попытка получить retail positioning
            retail_data = self._parse_myfxbook_positioning()
            if retail_data:
                external_data.update(retail_data)
                external_data['data_available'] = True
                
            # Попытка получить sentiment с Investing.com
            sentiment_data = self._parse_investing_sentiment()
            if sentiment_data:
                external_data['sentiment'] = sentiment_data
                
        except Exception as e:
            logger.error(f"External liquidity data fetch error: {e}")
            
        return external_data
        
    def _parse_myfxbook_positioning(self) -> Dict:
        """
        Парсинг MyFxBook для получения retail positioning
        
        ВАЖНО: MyFxBook требует регистрации и может блокировать парсинг
        Используем публичные данные если доступны
        """
        try:
            # URL для XAUUSD community outlook
            url = "https://www.myfxbook.com/community/outlook/XAUUSD"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=5)
            
            if response.status_code != 200:
                logger.warning(f"MyFxBook returned status {response.status_code}")
                return None
                
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Поиск данных о позиционировании
            # ПРИМЕРНАЯ структура (реальная может отличаться):
            # <div class="outlookShortPositions">45%</div>
            # <div class="outlookLongPositions">55%</div>
            
            long_elem = soup.find('div', class_='outlookLongPositions')
            short_elem = soup.find('div', class_='outlookShortPositions')
            
            if long_elem and short_elem:
                long_percent = float(long_elem.text.strip().replace('%', ''))
                
                return {
                    'retail_long_percent': long_percent,
                    'retail_short_percent': 100 - long_percent,
                    'source': 'myfxbook'
                }
                
        except Exception as e:
            logger.debug(f"MyFxBook parsing failed: {e}")
            
        return None
        
    def _parse_investing_sentiment(self) -> str:
        """
        Парсинг Investing.com для technical sentiment
        
        Возвращает: 'strong_buy' / 'buy' / 'neutral' / 'sell' / 'strong_sell'
        """
        try:
            # URL для XAU/USD technical summary
            url = "https://www.investing.com/currencies/xau-usd-technical"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=5)
            
            if response.status_code != 200:
                return 'neutral'
                
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Поиск sentiment indicator
            # ПРИМЕРНАЯ структура:
            # <div class="technicalSummary">Strong Buy</div>
            
            sentiment_elem = soup.find('div', class_='technicalSummary')
            
            if sentiment_elem:
                sentiment_text = sentiment_elem.text.strip().lower()
                
                if 'strong buy' in sentiment_text:
                    return 'strong_buy'
                elif 'buy' in sentiment_text:
                    return 'buy'
                elif 'strong sell' in sentiment_text:
                    return 'strong_sell'
                elif 'sell' in sentiment_text:
                    return 'sell'
                else:
                    return 'neutral'
                    
        except Exception as e:
            logger.debug(f"Investing.com parsing failed: {e}")
            
        return 'neutral'
        
    def _get_retail_positioning(self) -> Dict:
        """
        Получить positioning розничных трейдеров
        
        Принцип: если 70%+ розницы в лонгах → вероятно пойдём вниз (контр-индикатор)
        """
        external_data = self._fetch_external_liquidity_data()
        
        if not external_data['data_available']:
            return {'bias': 'neutral', 'confidence': 0.0}
            
        retail_long_percent = external_data['retail_long_percent']
        
        # Контр-индикатор: идём против розницы
        if retail_long_percent >= 70:
            return {
                'bias': 'bearish',  # Розница в лонгах → мы в шортах
                'confidence': 0.7,
                'retail_long_percent': retail_long_percent
            }
        elif retail_long_percent <= 30:
            return {
                'bias': 'bullish',  # Розница в шортах → мы в лонгах
                'confidence': 0.7,
                'retail_long_percent': retail_long_percent
            }
        else:
            return {
                'bias': 'neutral',
                'confidence': 0.0,
                'retail_long_percent': retail_long_percent
            }
