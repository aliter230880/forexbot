"""
Sentiment Analyzer - предобученная модель для анализа sentiment
"""
from loguru import logger
from typing import Dict, List
import re
import requests

class SentimentAnalyzer:
    """
    Анализ sentiment с использованием предобученных моделей
    
    Источники:
    1. Hugging Face transformers (FinBERT для финансов)
    2. Twitter/X API
    3. News headlines
    4. Reddit WallStreetBets sentiment
    """
    
    def __init__(self, config):
        self.config = config
        
        # Инициализация предобученной модели
        self.model = None
        self.model_loaded = False
        
        # Попытка загрузить FinBERT (если доступен)
        self._try_load_finbert()
        
        # Словари для rule-based sentiment (fallback)
        self.bullish_keywords = [
            'bullish', 'buy', 'long', 'rally', 'surge', 'breakout', 
            'golden cross', 'support', 'bounce', 'uptrend', 'moon',
            'gains', 'profit', 'growth', 'rise', 'strong'
        ]
        
        self.bearish_keywords = [
            'bearish', 'sell', 'short', 'crash', 'drop', 'breakdown',
            'death cross', 'resistance', 'downtrend', 'fall', 'dump',
            'loss', 'decline', 'weak', 'collapse', 'plunge'
        ]
        
    def _try_load_finbert(self):
        """
        Попытка загрузить FinBERT (предобученная модель для финансов)
        
        FinBERT: BERT fine-tuned на финансовых текстах
        """
        try:
            # Проверка доступности transformers
            from transformers import AutoTokenizer, AutoModelForSequenceClassification
            import torch
            
            logger.info("Loading FinBERT model...")
            
            model_name = "ProsusAI/finbert"
            
            self.tokenizer = AutoTokenizer.from_pretrained(model_name)
            self.model = AutoModelForSequenceClassification.from_pretrained(model_name)
            
            self.model_loaded = True
            logger.info("✅ FinBERT model loaded successfully")
            
        except ImportError:
            logger.warning("transformers library not available, using rule-based sentiment")
            self.model_loaded = False
        except Exception as e:
            logger.warning(f"Failed to load FinBERT: {e}")
            self.model_loaded = False
            
    def analyze_text(self, text: str) -> Dict:
        """
        Анализ sentiment текста
        
        Возвращает:
        {
            'sentiment': 'positive' / 'negative' / 'neutral',
            'score': -1.0 to 1.0,
            'confidence': 0.0 to 1.0
        }
        """
        if self.model_loaded:
            return self._analyze_with_finbert(text)
        else:
            return self._analyze_with_rules(text)
            
    def _analyze_with_finbert(self, text: str) -> Dict:
        """Анализ с использованием FinBERT"""
        try:
            from transformers import pipeline
            
            # Pipeline для sentiment analysis
            sentiment_pipeline = pipeline(
                "sentiment-analysis",
                model=self.model,
                tokenizer=self.tokenizer
            )
            
            result = sentiment_pipeline(text)[0]
            
            # Конвертация в стандартный формат
            label = result['label'].lower()
            confidence = result['score']
            
            if label == 'positive':
                sentiment = 'positive'
                score = confidence
            elif label == 'negative':
                sentiment = 'negative'
                score = -confidence
            else:
                sentiment = 'neutral'
                score = 0.0
                
            return {
                'sentiment': sentiment,
                'score': score,
                'confidence': confidence
            }
            
        except Exception as e:
            logger.error(f"FinBERT analysis error: {e}")
            return self._analyze_with_rules(text)
            
    def _analyze_with_rules(self, text: str) -> Dict:
        """Rule-based sentiment analysis (fallback)"""
        text_lower = text.lower()
        
        # Подсчёт bullish/bearish keywords
        bullish_count = sum(1 for word in self.bullish_keywords if word in text_lower)
        bearish_count = sum(1 for word in self.bearish_keywords if word in text_lower)
        
        total_count = bullish_count + bearish_count
        
        if total_count == 0:
            return {'sentiment': 'neutral', 'score': 0.0, 'confidence': 0.3}
            
        # Расчёт score
        score = (bullish_count - bearish_count) / total_count
        
        if score > 0.3:
            sentiment = 'positive'
            confidence = min(score, 1.0)
        elif score < -0.3:
            sentiment = 'negative'
            confidence = min(abs(score), 1.0)
        else:
            sentiment = 'neutral'
            confidence = 0.5
            
        return {
            'sentiment': sentiment,
            'score': score,
            'confidence': confidence
        }
        
    def analyze_social_media(self, symbol: str = "XAUUSD") -> Dict:
        """
        Анализ sentiment в социальных сетях
        
        Источники:
        1. Twitter/X
        2. Reddit (r/wallstreetbets, r/forex)
        3. StockTwits
        """
        result = {
            'overall_sentiment': 'neutral',
            'score': 0.0,
            'sources': {}
        }
        
        try:
            # Twitter sentiment
            if self.config.TWITTER_BEARER_TOKEN:
                twitter_sentiment = self._analyze_twitter(symbol)
                result['sources']['twitter'] = twitter_sentiment
                
            # Reddit sentiment
            reddit_sentiment = self._analyze_reddit(symbol)
            result['sources']['reddit'] = reddit_sentiment
            
            # Агрегация
            all_scores = [
                s['score'] for s in result['sources'].values() 
                if 'score' in s
            ]
            
            if all_scores:
                avg_score = sum(all_scores) / len(all_scores)
                result['score'] = avg_score
                
                if avg_score > 0.2:
                    result['overall_sentiment'] = 'positive'
                elif avg_score < -0.2:
                    result['overall_sentiment'] = 'negative'
                else:
                    result['overall_sentiment'] = 'neutral'
                    
        except Exception as e:
            logger.error(f"Social media sentiment error: {e}")
            
        return result
        
    def _analyze_twitter(self, symbol: str) -> Dict:
        """Анализ Twitter/X"""
        try:
            # Поиск твитов
            query = f"${symbol} OR #Gold OR #XAUUSD"
            
            headers = {
                'Authorization': f'Bearer {self.config.TWITTER_BEARER_TOKEN}'
            }
            
            url = f"https://api.twitter.com/2/tweets/search/recent?query={query}&max_results=100"
            
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code != 200:
                logger.warning(f"Twitter API returned {response.status_code}")
                return {'sentiment': 'neutral', 'score': 0.0}
                
            data = response.json()
            
            if 'data' not in data:
                return {'sentiment': 'neutral', 'score': 0.0}
                
            # Анализ каждого твита
            sentiments = []
            for tweet in data['data']:
                text = tweet.get('text', '')
                sentiment = self.analyze_text(text)
                sentiments.append(sentiment['score'])
                
            if sentiments:
                avg_score = sum(sentiments) / len(sentiments)
                
                return {
                    'sentiment': 'positive' if avg_score > 0.1 else 'negative' if avg_score < -0.1 else 'neutral',
                    'score': avg_score,
                    'tweets_analyzed': len(sentiments)
                }
                
        except Exception as e:
            logger.debug(f"Twitter analysis error: {e}")
            
        return {'sentiment': 'neutral', 'score': 0.0}
        
    def _analyze_reddit(self, symbol: str) -> Dict:
        """Анализ Reddit sentiment"""
        try:
            # Reddit не требует API key для публичных постов
            subreddits = ['wallstreetbets', 'forex', 'Gold']
            
            sentiments = []
            
            for subreddit in subreddits:
                url = f"https://www.reddit.com/r/{subreddit}/search.json?q={symbol}&limit=50&sort=new"
                
                headers = {'User-Agent': 'Mozilla/5.0'}
                
                response = requests.get(url, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    for post in data.get('data', {}).get('children', []):
                        title = post['data'].get('title', '')
                        sentiment = self.analyze_text(title)
                        sentiments.append(sentiment['score'])
                        
            if sentiments:
                avg_score = sum(sentiments) / len(sentiments)
                
                return {
                    'sentiment': 'positive' if avg_score > 0.1 else 'negative' if avg_score < -0.1 else 'neutral',
                    'score': avg_score,
                    'posts_analyzed': len(sentiments)
                }
                
        except Exception as e:
            logger.debug(f"Reddit analysis error: {e}")
            
        return {'sentiment': 'neutral', 'score': 0.0}
        
    def analyze_news_headlines(self) -> Dict:
        """
        Анализ новостных заголовков
        
        Источники:
        1. Reuters
        2. Bloomberg
        3. CNBC
        """
        # Публичные RSS по золоту/сырью/рынкам (без API-ключей)
        rss_feeds = [
            'https://www.investing.com/rss/commodities_Gold.rss',
            'https://www.investing.com/rss/news_11.rss',
            'https://feeds.marketwatch.com/marketwatch/marketpulse/',
            'https://www.kitco.com/rss/KitcoNews.xml',
        ]
        sentiments: List[float] = []
        headlines: List[str] = []
        try:
            import feedparser
            for url in rss_feeds:
                try:
                    feed = feedparser.parse(url)
                    for entry in feed.entries[:12]:
                        title = getattr(entry, 'title', '') or ''
                        if title:
                            sentiments.append(self.analyze_text(title)['score'])
                            headlines.append(title)
                except Exception as e:
                    logger.debug(f"RSS {url} error: {e}")
        except ImportError:
            logger.warning("feedparser не установлен, пробую web scraping")

        # fallback / дополнение через web scraping
        if len(sentiments) < 5:
            s2, h2 = self._scrape_gold_news()
            sentiments += s2
            headlines += h2

        if not sentiments:
            logger.warning("news headlines: заголовки не получены")
            return {'sentiment': 'neutral', 'score': 0.0, 'headlines_analyzed': 0}

        avg = sum(sentiments) / len(sentiments)
        sentiment = 'positive' if avg > 0.1 else ('negative' if avg < -0.1 else 'neutral')
        logger.info(f"news headlines: {len(sentiments)} шт, avg {avg:.2f} ({sentiment})")
        return {
            'sentiment': sentiment,
            'score': round(avg, 3),
            'headlines_analyzed': len(sentiments),
            'sample': headlines[:3],
        }
            

    def _scrape_gold_news(self) -> tuple:
        """Web scraping золотых новостей (fallback для RSS)."""
        try:
            import requests
            from bs4 import BeautifulSoup
            
            sentiments = []
            headlines = []
            
            # Investing.com gold news
            url = 'https://www.investing.com/commodities/gold-news'
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                articles = soup.find_all('article', limit=15)
                
                for article in articles:
                    title_tag = article.find(['h3', 'h2', 'a'])
                    if title_tag:
                        title = title_tag.get_text(strip=True)
                        sentiment = self.analyze_text(title)
                        sentiments.append(sentiment['score'])
                        headlines.append(title)
                        
        except Exception as e:
            logger.debug(f"Web scraping error: {e}")
            
        return sentiments, headlines

    def get_fear_greed_index(self) -> Dict:
        """
        Fear & Greed Index для золота
        
        На основе:
        - Volatility (VIX)
        - Market momentum
        - Safe haven demand
        """
        # Alternative.me Fear & Greed Index (live, публичный, без ключа).
        # Индекс крипто-рынка, но как прокси risk-on/risk-off он коррелирует
        # с safe-haven спросом на золото (обратная связь: страх → рост золота).
        try:
            r = requests.get('https://api.alternative.me/fng/?limit=1', timeout=10)
            if r.status_code == 200:
                d = r.json().get('data', [])
                if d:
                    idx = int(d[0]['value'])
                    label = d[0].get('value_classification', '')
                    logger.info(f"Fear&Greed (alternative.me): {idx} ({label})")
                    return {
                        'index': idx,
                        'sentiment': ('greed' if idx > 60 else
                                      'fear' if idx < 40 else 'neutral'),
                        'description': label,
                        'source': 'alternative.me',
                    }
        except Exception as e:
            logger.warning(f"Fear&Greed API error: {e}")
        return {'index': 50, 'sentiment': 'neutral', 'description': 'n/a', 'source': 'fallback'}
            
    def get_sentiment_trading_bias(self, sentiment_data: Dict) -> str:
        """
        Конвертация sentiment в торговый bias
        
        Возвращает: 'bullish' / 'bearish' / 'neutral'
        """
        score = sentiment_data.get('score', 0.0)
        
        if score > 0.3:
            return 'bullish'
        elif score < -0.3:
            return 'bearish'
        else:
            return 'neutral'



