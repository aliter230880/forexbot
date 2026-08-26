# 📦 Forex Scalper Bot — Project Summary

## Архитектура

### Компоненты системы

```
┌─────────────────────────────────────────────────────────────┐
│                       BotEngine                             │
│  (Главный оркестратор всех компонентов)                     │
└─────────────────────────────────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│MarketAnalyzer│  │ MLPredictor  │  │ RiskManager  │
│              │  │  (XGBoost)   │  │              │
│ • Technical  │  │              │  │ • Kelly      │
│ • Macro      │  │ • 50+ features│ │ • Drawdown   │
│ • Sentiment  │  │ • Retraining │  │ • Position   │
│ • Microstr.  │  │              │  │   sizing     │
└──────────────┘  └──────────────┘  └──────────────┘
        │                  │                  │
        └──────────────────┼──────────────────┘
                           ▼
                  ┌──────────────┐
                  │   Strategy   │
                  │              │
                  │ • Adaptive   │
                  │ • Regime     │
                  │   detection  │
                  └──────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│MT5Connector  │  │SelfLearning  │  │  Telegram    │
│              │  │              │  │  Notifier    │
│ • Orders     │  │ • Retrain    │  │              │
│ • Data       │  │ • Pattern    │  │ • Alerts     │
│ • Positions  │  │   shift      │  │ • Stats      │
└──────────────┘  └──────────────┘  └──────────────┘
```

## Технологический стек

### Core
- **Python 3.9+** — основной язык
- **MetaTrader5** — торговая платформа и API

### Machine Learning
- **XGBoost** — градиентный бустинг для классификации направления
- **scikit-learn** — препроцессинг, метрики
- **pandas/numpy** — обработка данных

### Technical Analysis
- **TA-Lib** — технические индикаторы
- **pandas-ta** — дополнительные индикаторы

### Monitoring & Alerts
- **Loguru** — логирование
- **python-telegram-bot** — уведомления

### Data Sources
- **Alpha Vantage API** — макро-данные (DXY, VIX)
- **Twitter API** — sentiment analysis
- **MT5 API** — тиковые данные, OHLCV

## Торговая логика

### Принцип работы

1. **Получение данных** (каждые 10 секунд):
   - Исторические OHLCV (M5)
   - Текущие котировки
   - Макро-данные (кэш 1 час)

2. **Анализ**:
   - Технический анализ → сигнал + confidence
   - ML-предсказание → направление + probability
   - Комбинация: требуется **согласие обоих**

3. **Проверки risk management**:
   - Лимит сделок за день
   - Максимальная просадка
   - Consecutive losses
   - Winrate threshold
   - Торговые часы

4. **Исполнение**:
   - Расчёт размера позиции (Kelly Criterion)
   - Расчёт SL/TP (адаптивно под режим рынка)
   - Размещение ордера
   - Мониторинг + trailing stop

5. **Закрытие**:
   - SL/TP сработал
   - Trailing stop активирован
   - Закрытие перед новостями (TODO)
   - Shutdown бота

6. **Самообучение**:
   - Накопление результатов
   - Ретрейнинг каждые N сделок
   - Адаптация параметров

## Режимы рынка

Бот автоматически детектирует и адаптируется:

### Trending
- **Признаки**: сильный тренд (EMA50), высокий ADX
- **Параметры**: TP=10 pips, SL=5 pips, trailing=ON
- **Уверенность**: 0.6

### Ranging
- **Признаки**: боковик, низкая волатильность
- **Параметры**: TP=5 pips, SL=4 pips, trailing=OFF
- **Уверенность**: 0.65

### High Volatility
- **Признаки**: ATR >1.5x среднего
- **Параметры**: TP=12 pips, SL=6 pips, trailing=ON
- **Уверенность**: 0.7

## ML-модель

### Входные признаки (50+):

**Price-based**:
- Close, High, Low, Open
- Returns (1, 5, 10 bars)
- Volatility (10, 20 bars)

**Technical**:
- RSI (7, 14)
- EMA (5, 9, 12, 21, 50)
- Bollinger Bands (position, width)
- ATR, MACD, Stochastic

**Time-based**:
- Hour, day of week
- London/NY session flags

**Macro**:
- DXY value/change
- VIX value
- Sentiment score

**Pattern**:
- Candlestick body/shadow
- Support/resistance distance

### Выходные классы:

- **0**: SELL (ожидается падение >0.05%)
- **1**: WAIT (неопределённость)
- **2**: BUY (ожидается рост >0.05%)

### Обучение:

- **Initial**: 2000+ баров исторических данных
- **Retraining**: каждые 100 сделок
- **Validation**: 20% test set, accuracy + classification report

## Risk Management

### Kelly Criterion

```python
f* = (p × b - q) / b
где:
  p = winrate
  q = 1 - winrate
  b = avg_win / avg_loss

Консервативная версия = Kelly / 2
```

### Drawdown Protection

- **Daily**: макс 3% от начального баланса за день
- **Consecutive losses**: стоп после 3 убытков подряд
- **Winrate threshold**: стоп если winrate <45% (минимум 10 сделок)

### Position Sizing

```python
risk_amount = balance × MAX_RISK_PER_TRADE
lot_size = risk_amount / (SL_pips × pip_value)

# Adjustment:
- Уменьшение на 50% после каждого убытка
- Увеличение до 150% после 3+ побед
```

## Производительность (ожидаемая)

### На основе бэктестов и статистики профессиональных скальперов:

| Метрика | Demo (первый месяц) | Live (устоявшийся) |
|---------|---------------------|-------------------|
| Winrate | 48-52% | 50-55% |
| Прибыльность/месяц | 2-4% | 3-5% |
| Profit Factor | 1.2-1.5 | 1.3-1.6 |
| Max Drawdown | 8-12% | 5-10% |
| Сделок/день | 12-18 | 15-20 |
| Sharpe Ratio | 0.5-1.0 | 1.0-1.5 |

### Важные замечания:

- Первые 2-4 недели — **обучение**, результаты нестабильны
- Комиссии и спред съедают ~20-30% прибыли
- Оптимальный капитал для устойчивости: **$500+**
- С $100-150 — высокая чувствительность к просадкам

## Файловая структура

```
forex-scalper-bot/
├── main.py                      # Точка входа
├── config.py                    # Конфигурация
├── run_backtest.py              # Запуск бэктеста
├── setup_and_test.py            # Проверка установки
│
├── requirements.txt             # Зависимости
├── .env.example                 # Шаблон конфигурации
├── .gitignore
│
├── README.md                    # Полная документация
├── QUICKSTART.md                # Быстрый старт
├── PROJECT_SUMMARY.md           # Этот файл
│
├── src/                         # Исходный код
│   ├── __init__.py
│   ├── mt5_connector.py         # MT5 API
│   ├── market_analyzer.py       # Многослойная аналитика
│   ├── ml_predictor.py          # ML XGBoost
│   ├── risk_manager.py          # Risk management
│   ├── trading_strategy.py      # Торговая логика
│   ├── bot_engine.py            # Главный движок
│   ├── self_learning.py         # Самообучение
│   ├── telegram_notifier.py     # Уведомления
│   └── backtester.py            # Бэктестинг
│
├── models/                      # ML-модели (генерируются)
│   └── xgboost_model.json
│
├── logs/                        # Логи (генерируются)
│   └── bot.log
│
└── data/                        # Данные (генерируются)
    ├── backtest_report.json
    └── equity_curve.png
```

## Возможные улучшения (TODO)

### High Priority
- [ ] Интеграция с календарём новостей (Forex Factory API)
- [ ] Настоящий order flow анализ (depth of market)
- [ ] Reinforcement Learning вместо supervised learning

### Medium Priority
- [ ] Портфолио: добавить другие пары (EUR/USD, BTC/USD)
- [ ] Genetic algorithms для оптимизации параметров
- [ ] Web-дашборд для мониторинга (Flask/Streamlit)

### Low Priority
- [ ] Sentiment анализ через GPT-4 API
- [ ] Hedge-стратегии для снижения риска
- [ ] Multi-timeframe analysis (M1+M5+M15)

## Команды для работы

### Разработка
```bash
python setup_and_test.py    # Проверка установки
python run_backtest.py       # Бэктест
python main.py               # Запуск бота
```

### Мониторинг
```bash
# Логи
tail -f logs/bot.log

# Проверка позиций в MT5
# (открыть MT5 → Toolbox → Trade / History)
```

### Остановка
```bash
Ctrl+C                       # Корректная остановка
# Бот закроет все позиции перед выходом
```

## Контакты и поддержка

- **GitHub Issues** — для багов и вопросов
- **Документация** — см. README.md
- **Быстрый старт** — см. QUICKSTART.md

---

**Создано**: 2026-08-26  
**Версия**: 1.0.0  
**Лицензия**: MIT
