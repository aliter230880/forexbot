"""
Обучение XGBoost-модели на РЕАЛЬНОЙ истории котировок из MT5.

Это закрывает главный пробел: без обученной модели confidence-фильтр
предиктора бессмыслен (models/ была пустой). Скрипт:
  1. Тянет N баров M5 из MT5 (или из CSV, если MT5 недоступен)
  2. Строит фичи через ml_predictor.extract_features()
  3. Размечает create_labels() (buy/sell/wait по будущему движению)
  4. Обучает XGBoost и сохраняет в models/xgboost_model.json

Запуск: python train_model.py [bars]
"""
import sys
from pathlib import Path

from loguru import logger

sys.path.insert(0, str(Path(__file__).parent))
from config import Config
from src.ml_predictor import MLPredictor


def load_from_mt5(symbol: str, bars: int):
    """Реальные бары M5 из MT5."""
    try:
        import MetaTrader5 as mt5
        import pandas as pd
        path = getattr(Config, 'MT5_PATH', None)
        ok = mt5.initialize(path=path) if path else mt5.initialize()
        if not ok:
            logger.warning(f"MT5 initialize failed: {mt5.last_error()}")
            return None
        # выбрать символ (у PU Prime суффикс .s)
        for sym in (symbol, symbol + '.s', symbol + '.'):
            if mt5.symbol_select(sym, True):
                rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M5, 1, bars)
                if rates is not None and len(rates) > 500:
                    logger.info(f"MT5: загружено {len(rates)} баров {sym}")
                    df = pd.DataFrame(rates)
                    df['time'] = pd.to_datetime(df['time'], unit='s')
                    mt5.shutdown()
                    return df
        mt5.shutdown()
    except Exception as e:
        logger.warning(f"MT5 load error: {e}")
    return None


def load_from_yfinance(bars: int):
    """Fallback: дневные+часовые данные золота через yfinance."""
    try:
        import yfinance as yf
        import pandas as pd
        logger.info("Fallback: тяну XAUUSD (GC=F) через yfinance...")
        hist = yf.Ticker('GC=F').history(period='60d', interval='5m')
        if hist is None or len(hist) < 500:
            hist = yf.Ticker('GC=F').history(period='2y', interval='1h')
        if hist is None or len(hist) < 500:
            return None
        df = pd.DataFrame({
            'time': hist.index,
            'open': hist['Open'].values,
            'high': hist['High'].values,
            'low': hist['Low'].values,
            'close': hist['Close'].values,
            'tick_volume': hist['Volume'].values,
        }).reset_index(drop=True)
        logger.info(f"yfinance: загружено {len(df)} баров")
        return df
    except Exception as e:
        logger.warning(f"yfinance load error: {e}")
    return None


def main():
    bars = int(sys.argv[1]) if len(sys.argv) > 1 else 15000
    logger.info(f"=== Обучение ML-модели ({Config.SYMBOL}, ~{bars} баров M5) ===")

    df = load_from_mt5(Config.SYMBOL, bars)
    if df is None:
        df = load_from_yfinance(bars)
    if df is None:
        logger.error("Не удалось получить данные ни из MT5, ни из yfinance")
        sys.exit(1)

    ml = MLPredictor(Config)

    logger.info("Извлечение фичей...")
    features = ml.extract_features(df, full=True)
    logger.info(f"Фичей: {features.shape[1]}, строк: {features.shape[0]}")

    logger.info("Разметка (create_labels)...")
    labels = ml.create_labels(df, lookahead=3)

    # выровнять длины и убрать NaN
    n = min(len(features), len(labels))
    features = features.iloc[:n].reset_index(drop=True)
    labels = labels.iloc[:n].reset_index(drop=True)
    mask = ~features.isna().any(axis=1)
    features, labels = features[mask], labels[mask]
    logger.info(f"После очистки: {len(features)} строк")

    dist = labels.value_counts().to_dict()
    logger.info(f"Распределение меток (0=sell,1=wait,2=buy): {dist}")

    logger.info("Обучение XGBoost...")
    ok = ml.train(features, labels)
    if ok:
        logger.info(f"✅ Модель обучена и сохранена: {Config.ML_MODEL_PATH}")
        # проверка предсказания
        pred = ml.predict(features.tail(1))
        logger.info(f"Тест предсказания на последнем баре: {pred}")
    else:
        logger.error("Обучение не удалось (мало данных или ошибка)")
        sys.exit(1)


if __name__ == "__main__":
    main()
