"""
Скрипт первоначальной проверки и настройки
"""
from loguru import logger
import sys
from pathlib import Path

def check_environment():
    """Проверка окружения"""
    print("🔍 Проверка окружения...\n")
    
    issues = []
    
    # Python version
    if sys.version_info < (3, 9):
        issues.append(f"❌ Python версия {sys.version_info.major}.{sys.version_info.minor} < 3.9")
    else:
        print(f"✅ Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    
    # Required packages
    required = ['MetaTrader5', 'pandas', 'numpy', 'xgboost', 'sklearn', 'loguru', 'telegram']
    
    for package in required:
        try:
            __import__(package)
            print(f"✅ {package}")
        except ImportError:
            issues.append(f"❌ Отсутствует пакет: {package}")
            print(f"❌ {package} — не установлен")
    
    # .env file
    env_file = Path('.env')
    if env_file.exists():
        print("✅ .env файл существует")
    else:
        issues.append("❌ .env файл не найден")
        print("❌ .env файл не найден (скопируйте .env.example)")
    
    # Directories
    for dir_name in ['logs', 'models', 'data']:
        path = Path(dir_name)
        if path.exists():
            print(f"✅ Директория {dir_name}/")
        else:
            path.mkdir(exist_ok=True)
            print(f"✅ Создана директория {dir_name}/")
    
    print()
    
    if issues:
        print("⚠️  Обнаружены проблемы:\n")
        for issue in issues:
            print(f"   {issue}")
        print("\n❌ Установка не завершена. Исправьте проблемы выше.")
        return False
    else:
        print("✅ Все проверки пройдены!")
        return True

def test_mt5_connection():
    """Тест подключения к MT5"""
    print("\n🔌 Тестирование подключения к MetaTrader5...\n")
    
    try:
        from src.mt5_connector import MT5Connector
        from config import Config
        
        config = Config()
        mt5 = MT5Connector()
        
        if not mt5.connect():
            print("❌ Не удалось подключиться к MT5")
            print("\nВозможные причины:")
            print("  1. MT5 не запущен")
            print("  2. Не залогинены в аккаунт")
            print("  3. Неверные данные в .env (MT5_LOGIN, MT5_PASSWORD, MT5_SERVER)")
            print("  4. Algo Trading не включен в MT5 (Tools → Options → Expert Advisors)")
            return False
        
        account = mt5.get_account_info()
        symbol = mt5.get_symbol_info()
        
        print("✅ Подключено к MT5!")
        print(f"\nАккаунт: #{account.get('balance')}")
        print(f"Баланс: ${account.get('balance', 0):.2f}")
        print(f"Equity: ${account.get('equity', 0):.2f}")
        print(f"Leverage: 1:{account.get('leverage', 0)}")
        
        print(f"\nСимвол: {config.SYMBOL}")
        print(f"Bid: {symbol.get('bid', 0):.2f}")
        print(f"Ask: {symbol.get('ask', 0):.2f}")
        print(f"Spread: {symbol.get('spread', 0)} points")
        
        mt5.disconnect()
        return True
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return False

def main():
    """Главная функция"""
    print("""
╔══════════════════════════════════════════════════════════╗
║                                                          ║
║   🤖 Forex Scalper Bot — Setup & Test                   ║
║                                                          ║
╚══════════════════════════════════════════════════════════╝
    """)
    
    # Проверка окружения
    if not check_environment():
        return
    
    # Тест MT5
    if not test_mt5_connection():
        return
    
    print("""
╔══════════════════════════════════════════════════════════╗
║                                                          ║
║   ✅ Установка завершена успешно!                       ║
║                                                          ║
║   Следующие шаги:                                       ║
║                                                          ║
║   1. Бэктест:    python run_backtest.py                ║
║   2. Запуск:     python main.py                         ║
║                                                          ║
║   ⚠️  ВАЖНО: Начинайте с demo-счёта!                   ║
║                                                          ║
╚══════════════════════════════════════════════════════════╝
    """)

if __name__ == "__main__":
    main()
