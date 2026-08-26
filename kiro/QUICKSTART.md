# ⚡ Быстрый старт за 5 минут

## Шаг 1: Установка (2 минуты)

```bash
cd forex-scalper-bot
pip install -r requirements.txt
```

Если `ta-lib` не устанавливается, временно закомментируйте его в `requirements.txt` — остальное будет работать.

## Шаг 2: Настройка MT5 (1 минута)

1. Откройте MetaTrader 5
2. Войдите в **demo-счёт** (File → Open Account → Demo)
3. Включите Algo Trading: `Tools → Options → Expert Advisors → ✓ Allow Algo Trading`

## Шаг 3: Конфигурация (1 минута)

```bash
cp .env.example .env
```

Отредактируйте `.env`:

```env
MT5_LOGIN=ваш_demo_номер_счёта
MT5_PASSWORD=ваш_demo_пароль
MT5_SERVER=ваш_сервер  # например: Pepperstone-Demo

# Остальное можно оставить по умолчанию
```

## Шаг 4: Запуск (30 секунд)

```bash
python main.py
```

Вы увидите:

```
🚀 Запуск Forex Scalper Bot для XAUUSD
✅ Подключено к MT5: Account #12345678, balance: $10000
🔧 Initializing bot components...
✅ All components initialized
🤖 Bot engine started
```

## Шаг 5: Мониторинг (30 секунд)

Откройте второй терминал и следите за логами:

```bash
# PowerShell
Get-Content logs/bot.log -Wait -Tail 50

# Linux/Mac
tail -f logs/bot.log
```

---

## ✅ Готово!

Бот работает. Оставьте его на **demo** минимум на **2 недели**.

### Что дальше?

1. **Наблюдайте за сделками** в MT5 → History
2. **Проверяйте статистику** в логах (каждые 100 итераций)
3. **Настройте Telegram** для уведомлений (см. README.md)
4. **Запустите бэктест**: `python run_backtest.py`

### Когда переходить на live?

Только если после 2 недель demo:
- ✅ Winrate >50%
- ✅ Прибыльность положительная
- ✅ Максимальная просадка <10%
- ✅ Нет критических ошибок в логах

---

**⚠️ НИКОГДА не запускайте на live без тестирования на demo!**
