"""
News Calendar - календарь экономических новостей и торговля по ожиданиям
"""
import requests
from datetime import datetime, timedelta
from loguru import logger
from typing import Dict, List
import json

class NewsCalendar:
    """
    Интеграция с календарём новостей Forex Factory
    Торговля по ожиданиям новостей
    """
    
    def __init__(self, config):
        self.config = config
        self.news_cache = []
        self.cache_time = None
        
    def get_upcoming_news(self, hours_ahead: int = 24) -> List[Dict]:
        """
        Получить предстоящие новости на следующие N часов
        
        Возвращает список новостей с:
        - title: название
        - time: время UTC
        - impact: high/medium/low
        - currency: USD, EUR и т.д.
        - forecast: прогноз
        - previous: предыдущее значение
        """
        # Обновление кэша раз в час
        now = datetime.utcnow()
        if self.cache_time is None or (now - self.cache_time).seconds > 3600:
            self._update_news_cache()
            self.cache_time = now
            
        # Фильтрация по времени
        cutoff = now + timedelta(hours=hours_ahead)
        upcoming = [
            news for news in self.news_cache 
            if now <= news['time'] <= cutoff
        ]
        
        return upcoming
        
    def get_news_bias(self) -> Dict:
        """
        Определение bias рынка на основе ожидаемых новостей
        
        Возвращает:
        {
            'direction': 'bullish'/'bearish'/'neutral',
            'confidence': 0.0-1.0,
            'next_major_news': {...},
            'reasons': [...]
        }
        """
        result = {
            'direction': 'neutral',
            'confidence': 0.0,
            'next_major_news': None,
            'reasons': []
        }
        
        try:
            # Новости на следующие 4 часа
            upcoming = self.get_upcoming_news(hours_ahead=4)
            
            if not upcoming:
                return result
                
            # Фокус на high-impact новостях для USD (влияют на золото)
            high_impact_usd = [
                news for news in upcoming 
                if news['impact'] == 'high' and news['currency'] == 'USD'
            ]
            
            if not high_impact_usd:
                return result
                
            # Ближайшая major новость
            next_news = high_impact_usd[0]
            result['next_major_news'] = next_news
            
            # Определение направления по ожиданиям
            direction, confidence = self._analyze_news_expectation(next_news)
            
            result['direction'] = direction
            result['confidence'] = confidence
            result['reasons'] = self._generate_news_reasons(next_news, direction)
            
            logger.info(f"News bias: {direction} (confidence: {confidence:.2%}) - {next_news['title']}")
            
        except Exception as e:
            logger.error(f"News bias analysis error: {e}")
            
        return result
        
    def should_avoid_trading(self) -> Dict:
        """
        Проверка: нужно ли избегать торговли из-за близких новостей?
        
        Возвращает:
        {
            'avoid': True/False,
            'reason': str,
            'minutes_until': int
        }
        """
        try:
            upcoming = self.get_upcoming_news(hours_ahead=2)
            
            # High-impact новости USD
            high_impact = [
                news for news in upcoming 
                if news['impact'] == 'high' and news['currency'] == 'USD'
            ]
            
            if not high_impact:
                return {'avoid': False, 'reason': '', 'minutes_until': 0}
                
            next_news = high_impact[0]
            minutes_until = (next_news['time'] - datetime.utcnow()).seconds // 60
            
            # Избегать торговли за 30 минут до major новостей
            if minutes_until <= 30:
                return {
                    'avoid': True,
                    'reason': f"High-impact news in {minutes_until} min: {next_news['title']}",
                    'minutes_until': minutes_until
                }
                
        except Exception as e:
            logger.error(f"News avoidance check error: {e}")
            
        return {'avoid': False, 'reason': '', 'minutes_until': 0}
        
    def _update_news_cache(self):
        """Реальный экономический календарь: бесплатный weekly-JSON Forex Factory.

        Endpoint nfs.faireconomy.media/ff_calendar_thisweek.json — официальный
        публичный фид FF без ключа. Берём только USD-события high/medium impact
        (золото котируется в долларах, поэтому важен именно USD-фон).
        """
        try:
            import requests
            url = 'https://nfs.faireconomy.media/ff_calendar_thisweek.json'
            r = requests.get(url, timeout=12,
                             headers={'User-Agent': 'Mozilla/5.0'})
            events = []
            if r.status_code == 200:
                for e in r.json():
                    impact = (e.get('impact') or '').lower()
                    if e.get('country') != 'USD' or impact not in ('high', 'medium'):
                        continue
                    try:
                        dt = datetime.fromisoformat(
                            e['date'].replace('Z', '+00:00')).replace(tzinfo=None)
                    except Exception:
                        continue
                    events.append({
                        'title': e.get('title', ''),
                        'time': dt,
                        'impact': impact,
                        'currency': 'USD',
                    })
            if events:
                self.news_cache = events
                logger.info(f"News calendar (Forex Factory): {len(events)} USD-событий")
            else:
                logger.warning("FF calendar пуст — fallback на типовые события")
                self.news_cache = self._get_mock_news_data()
        except Exception as e:
            logger.warning(f"FF calendar error: {e} — fallback")
            self.news_cache = self._get_mock_news_data()
            
    def _get_mock_news_data(self) -> List[Dict]:
        """
        Моковые данные новостей (для тестирования)
        В продакшене заменить на реальный API
        """
        now = datetime.utcnow()
        
        # Типичные новости, влияющие на золото
        mock_news = [
            {
                'title': 'Non-Farm Payrolls (NFP)',
                'time': now.replace(hour=12, minute=30, second=0) + timedelta(days=2),
                'impact': 'high',
                'currency': 'USD',
                'forecast': '180K',
                'previous': '175K'
            },
            {
                'title': 'Federal Funds Rate Decision',
                'time': now.replace(hour=18, minute=0, second=0) + timedelta(days=5),
                'impact': 'high',
                'currency': 'USD',
                'forecast': '5.50%',
                'previous': '5.25%'
            },
            {
                'title': 'Consumer Price Index (CPI)',
                'time': now.replace(hour=12, minute=30, second=0) + timedelta(days=1),
                'impact': 'high',
                'currency': 'USD',
                'forecast': '3.2%',
                'previous': '3.0%'
            },
            {
                'title': 'Initial Jobless Claims',
                'time': now.replace(hour=12, minute=30, second=0),
                'impact': 'medium',
                'currency': 'USD',
                'forecast': '220K',
                'previous': '215K'
            }
        ]
        
        return mock_news
        
    def _analyze_news_expectation(self, news: Dict) -> tuple:
        """
        Анализ ожидания новости для определения направления
        
        Логика:
        - Если ожидается позитивная новость для USD → золото падает
        - Если ожидается негативная новость для USD → золото растёт
        """
        title = news['title'].lower()
        forecast = news.get('forecast', '')
        previous = news.get('previous', '')
        
        direction = 'neutral'
        confidence = 0.0
        
        try:
            # Парсинг значений
            forecast_val = float(''.join(filter(lambda x: x.isdigit() or x == '.', forecast))) if forecast else 0
            previous_val = float(''.join(filter(lambda x: x.isdigit() or x == '.', previous))) if previous else 0
            
            # NFP (Non-Farm Payrolls)
            if 'nfp' in title or 'non-farm' in title or 'payrolls' in title:
                if forecast_val > previous_val:
                    direction = 'bearish'  # USD сильнее → золото слабее
                    confidence = 0.7
                elif forecast_val < previous_val:
                    direction = 'bullish'  # USD слабее → золото сильнее
                    confidence = 0.7
                    
            # CPI (Consumer Price Index)
            elif 'cpi' in title or 'inflation' in title:
                if forecast_val > previous_val:
                    direction = 'bullish'  # Высокая инфляция → золото растёт
                    confidence = 0.75
                elif forecast_val < previous_val:
                    direction = 'bearish'  # Низкая инфляция → золото падает
                    confidence = 0.65
                    
            # Fed Rate Decision
            elif 'fed' in title or 'interest rate' in title or 'fomc' in title:
                if forecast_val > previous_val:
                    direction = 'bearish'  # Повышение ставок → золото падает
                    confidence = 0.8
                elif forecast_val < previous_val:
                    direction = 'bullish'  # Снижение ставок → золото растёт
                    confidence = 0.8
                    
            # GDP
            elif 'gdp' in title:
                if forecast_val > previous_val:
                    direction = 'bearish'  # Сильная экономика → золото слабее
                    confidence = 0.6
                elif forecast_val < previous_val:
                    direction = 'bullish'
                    confidence = 0.6
                    
        except Exception as e:
            logger.error(f"News expectation analysis error: {e}")
            
        return direction, confidence
        
    def _generate_news_reasons(self, news: Dict, direction: str) -> List[str]:
        """Генерация причин для news bias"""
        reasons = []
        
        time_until = (news['time'] - datetime.utcnow()).seconds // 60
        
        reasons.append(f"Upcoming: {news['title']} in {time_until} minutes")
        reasons.append(f"Forecast: {news.get('forecast', 'N/A')} vs Previous: {news.get('previous', 'N/A')}")
        
        if direction == 'bullish':
            reasons.append("Expectations favor gold strength")
        elif direction == 'bearish':
            reasons.append("Expectations favor USD strength (gold weakness)")
            
        return reasons
        
    def get_trading_strategy_by_news(self, news_bias: Dict) -> str:
        """
        Стратегия торговли на основе новостей
        
        Возвращает: 'pre_news' / 'wait_and_see' / 'trade_direction'
        """
        if not news_bias['next_major_news']:
            return 'trade_direction'
            
        minutes_until = (news_bias['next_major_news']['time'] - datetime.utcnow()).seconds // 60
        
        if minutes_until <= 15:
            return 'wait_and_see'  # Слишком близко к новости
        elif minutes_until <= 60:
            return 'pre_news'  # Можно торговать по ожиданиям
        else:
            return 'trade_direction'  # Обычная торговля
