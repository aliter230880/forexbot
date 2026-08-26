# 🤖 Forex Scalper Bot для XAUUSD (Золото)

Интеллектуальный скальпер-бот для торговли золотом с машинным обучением, многослойной аналитикой и самообучением.

## 🎯 Ключевые особенности

### Многослойная аналитика
- **Технический анализ**: RSI, EMA, Bollinger Bands, ATR, MACD, Stochastic
- **Макро-контекст**: корреляции с DXY (USD Index), VIX, реальные ставки
- **Sentiment**: анализ социальных сетей, positioning от брокеров
- **Микроструктура**: контроль спреда, volume analysis, order flow

### Machine Learning (XGBoost)
- 50+ признаков из цены, индикаторов, времени, макро-данных
- Предсказание направления с вероятностью
- Автоматический ретрейнинг каждые N сделок
- Обучение на собственных результатах

### Адаптивная торговля
- Детекция режимов рынка: trending / ranging / high volatility
- Динамическая подстройка TP/SL под режим
- Комбинированные сигналы: требуется согласие аналитики + ML

### Risk Management
- Kelly Criterion для расчёта размера позиции
- Динамический контроль просадки
- Trailing stop для защиты прибыли
- Правило "3 убытка подряд = стоп"
- Максимум сделок в день, контроль winrate

### Самообучение
- Накопление данных о сделках
- Периодический ретрейнинг модели
- Детекция смены паттернов рынка
- Адаптация к изменениям

## 📋 Требования

### Software
- **Python 3.9+**
- **MetaTrader 5** (установлен и настроен)
- **Windows 10/11** (MT5 работает только на Windows)

### Брокер
- ECN/STP брокер с низкими спредами на золото (<3 пипса)
- Поддержка API MetaTrader 5
- Рекомендуемые: Pepperstone, IC Markets, FP Markets

### Начальный капитал
- **Минимум**: $100-150
- **Рекомендуется**: $500+ (для устойчивости к комиссиям и проскальзыванию)

## 🚀 Установка

### 1. Клонирование и установка зависимостей

```bash
cd forex-scalper-bot
pip install -r requirements.txt
```

### 2. Установка TA-Lib (для технического анализа)

**Windows:**
```bash
# Скачать wheel с https://www.lfd.uci.edu/~gohlke/pythonlibs/#ta-lib
pip install TA_Lib-0.4.28-cp39-cp39-win_amd64.whl
```

### 3. Настройка MetaTrader 5

1. Установите MT5 от вашего брокера
2. Войдите в аккаунт
3. Включите **Algo Trading** в настройках (Tools → Options → Expert Advisors → Allow Algo Trading)

### 4. Конфигурация бота

Скопируйте `.env.example` в `.env` и заполните:

```bash
cp .env.example .env
```

Откройте `.env` и настройте:

```env
# MT5 данные
MT5_LOGIN=ваш_номер_счёта
MT5_PASSWORD=ваш_пароль
MT5_SERVER=сервер_брокера  # например: Pepperstone-Demo
MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe

# Торговые параметры
SYMBOL=XAUUSD
INITIAL_BALANCE=100
MAX_RISK_PER_TRADE=0.01  # 1% риска на сделку
MAX_DAILY_DRAWDOWN=0.03  # 3% максимум просадки за день
MAX_TRADES_PER_DAY=20

# Часы торговли (UTC)
TRADING_START_HOUR=8   # London open
TRADING_END_HOUR=17    # NY close

# Telegram (опционально, для уведомлений)
TELEGRAM_BOT_TOKEN=ваш_токен
TELEGRAM_CHAT_ID=ваш_chat_id
```

### 5. API ключи (опционально, для макро-аналитики)

```env
# Для получения DXY, макро-данных
ALPHA_VANTAGE_API_KEY=ваш_ключ  # бесплатно на https://www.alphavantage.co

# Для sentiment analysis
TWITTER_BEARER_TOKEN=ваш_токен  # https://developer.twitter.com
```

## 🎮 Запуск

### Demo режим (ОБЯЗАТЕЛЬНО начните с этого!)

1. **Откройте demo-счёт** в MT5
2. Настройте `.env` с demo-данными
3. Запустите бота:

```bash
python main.py
```

### Бэктестирование

Перед live-торговлей протестируйте на исторических данных:

```bash
python run_backtest.py
```

Результаты сохранятся в:
- `backtest_report.json` — детальные метрики
- `equity_curve.png` — график баланса

### Live торговля

**⚠️ ТОЛЬКО после успешного demo-тестирования минимум 1-2 недели!**

1. Убедитесь, что winrate в demo >50%
2. Проверьте, что максимальная просадка <10%
3. Переключитесь на live-счёт в `.env`
4. Запустите бота

```bash
python main.py
```

## 📊 Мониторинг

### Логи

Логи сохраняются в `logs/bot.log`:

```bash
tail -f logs/bot.log  # Linux/Mac
Get-Content logs/bot.log -Wait  # PowerShell
```

### Telegram уведомления

Если настроен Telegram, вы получите:
- ✅ Уведомления об открытии/закрытии сделок
- 📊 Статистику каждые 100 итераций
- ⚠️ Ошибки и предупреждения

### Панель MT5

Открытые позиции, историю сделок можно отслеживать прямо в MT5.

## ⚙️ Настройка параметров

### Агрессивность (больше сделок)

```env
MAX_TRADES_PER_DAY=25
MIN_WINRATE_THRESHOLD=0.40
```

В `config.py` уменьшите минимальную confidence:
```python
min_confidence = 0.55  # было 0.6
```

### Консервативность (меньше риска)

```env
MAX_RISK_PER_TRADE=0.005  # 0.5%
MAX_TRADES_PER_DAY=15
MIN_WINRATE_THRESHOLD=0.50
```

### Изменение TP/SL

В `config.py`:
```python
TP_PIPS = 10  # было 7
SL_PIPS = 6   # было 5
```

## 🧪 Тестирование компонентов

### Тест подключения к MT5

```python
from src.mt5_connector import MT5Connector
from config import Config

config = Config()
mt5 = MT5Connector()

if mt5.connect():
    print("✅ Connected!")
    print(mt5.get_account_info())
    print(mt5.get_symbol_info())
else:
    print("❌ Connection failed")
```

### Тест аналитики

```python
import MetaTrader5 as mt5
from src.market_analyzer import MarketAnalyzer

analyzer = MarketAnalyzer(mt5_conn, config)
df = mt5_conn.get_historical_data(bars=1000)
analysis = analyzer.analyze(df)
print(analysis)
```

## 📈 Ожидаемые результаты

### Реалистичные цели (на основе статистики профи-скальперов):

- **Winrate**: 50-55%
- **Прибыльность**: 3-5% в месяц (устойчивая)
- **Сделок в день**: 15-20
- **Просадка**: <10% в месяц

### ⚠️ Важно понимать:

- **10% в месяц** — возможно, но **высокорискованно**
- Комиссии и спред съедают ~30% прибыли на малом капитале
- Один резкий новостной скачок может вызвать просадку 5-10%
- Минимум 3 месяца нужно для оценки реальной производительности

## 🛡️ Безопасность

### Обязательные правила:

1. **Начинайте с demo** — минимум 2 недели
2. **Никогда не рискуйте >1%** на сделку при балансе <$500
3. **Включите уведомления** Telegram для контроля
4. **Проверяйте бота ежедневно** первые 2 недели
5. **Остановите бота** если просадка >15%

### VPS (рекомендуется для 24/7 работы)

Для стабильной работы используйте VPS:
- **Forex VPS** (специализированные, низкая латентность)
- **Vultr, DigitalOcean** (Windows Server + MetaTrader)
- Latency к серверу брокера <50ms

## 🔧 Разработка и доработки

### Структура проекта

```
forex-scalper-bot/
├── main.py                 # Точка входа
├── config.py               # Конфигурация
├── run_backtest.py         # Запуск бэктеста
├── requirements.txt
├── src/
│   ├── mt5_connector.py    # Подключение к MT5
│   ├── market_analyzer.py  # Многослойная аналитика
│   ├── ml_predictor.py     # ML-модель XGBoost
│   ├── trading_strategy.py # Торговая логика
│   ├── risk_manager.py     # Risk management
│   ├── self_learning.py    # Самообучение
│   ├── bot_engine.py       # Главный движок
│   ├── telegram_notifier.py # Уведомления
│   └── backtester.py       # Бэктестинг
├── models/                 # ML-модели
├── logs/                   # Логи
└── data/                   # Исторические данные
```

### Добавление индикаторов

В `src/market_analyzer.py`, метод `_technical_analysis`:

```python
# Ваш индикатор
adx = self._calculate_adx(df, period=14)
result['indicators']['adx'] = adx.iloc[-1]

if adx.iloc[-1] > 25:
    result['score'] += 1  # Сильный тренд
```

### Подключение календаря новостей

В `src/trading_strategy.py`, метод `manage_open_positions`:

```python
# Проверка календаря
news = self._check_news_calendar()
if news['high_impact_in_minutes'] < 30:
    logger.warning("High-impact news in 30 min, closing positions")
    for pos in positions:
        self.mt5.close_position(pos['ticket'])
```

## 🐛 Частые проблемы

### "MT5 initialization failed"
- Проверьте, что MT5 запущен и вы залогинены
- Проверьте путь к `terminal64.exe` в `.env`
- Включите Algo Trading в настройках MT5

### "Symbol XAUUSD not found"
- У вашего брокера символ может называться `GOLD`, `XAUUSD.m`, etc.
- Посмотрите точное название в Market Watch MT5
- Измените `SYMBOL=` в `.env`

### "Order failed: Not enough money"
- Баланс слишком мал или leverage недостаточен
- Бот пытается открыть слишком большой лот
- Уменьшите `MAX_RISK_PER_TRADE` в `.env`

### "Spread too high"
- Текущий спред >3 пипса (нормально во время низкой ликвидности)
- Бот не откроет сделку (защита)
- Торгуйте во время London/NY сессий

### ML-модель не обучается
- Нужно минимум 500 баров данных
- Проверьте наличие исторических данных: `mt5_conn.get_historical_data()`
- Первый запуск может занять 2-3 минуты для обучения

## 📚 Дополнительные ресурсы

- [MetaTrader 5 Python Documentation](https://www.mql5.com/en/docs/python_metatrader5)
- [XGBoost Documentation](https://xgboost.readthedocs.io/)
- [Forex Factory Calendar](https://www.forexfactory.com/calendar) — новости
- [TradingView](https://www.tradingview.com/symbols/TVC-GOLD/) — графики золота

## ⚖️ Дисклеймер

**Этот бот предоставляется "как есть" для образовательных целей.**

- Торговля на Forex сопряжена с **высоким риском**
- Вы можете **потерять весь капитал**
- Прошлые результаты **не гарантируют** будущую прибыль
- **Всегда тестируйте на demo** перед live-торговлей
- Автор не несёт ответственности за ваши торговые результаты

## 📝 Лицензия

MIT License — свободное использование и модификация.

## 🤝 Поддержка

Если бот помог заработать — буду рад звёздочке ⭐ на GitHub!

---

**Удачной торговли! 🚀💰**
