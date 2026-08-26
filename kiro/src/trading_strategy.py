"""
Trading Strategy - адаптивная торговая логика
"""
from loguru import logger
from datetime import datetime
import MetaTrader5 as mt5

class TradingStrategy:
    """Адаптивная торговая стратегия с детекцией режимов рынка"""
    
    def __init__(self, config, mt5_connector, market_analyzer, ml_predictor, risk_manager):
        self.config = config
        self.mt5 = mt5_connector
        self.analyzer = market_analyzer
        self.ml = ml_predictor
        self.risk = risk_manager
        
        # Режимы рынка
        self.market_regime = 'neutral'  # trending, ranging, high_volatility
        self.regime_params = self._get_default_params()
        
    def _get_default_params(self) -> dict:
        """Параметры по умолчанию для разных режимов"""
        return {
            'trending': {
                'tp_pips': 10,
                'sl_pips': 5,
                'min_confidence': 0.6,
                'trailing_stop': True
            },
            'ranging': {
                'tp_pips': 5,
                'sl_pips': 4,
                'min_confidence': 0.65,
                'trailing_stop': False
            },
            'high_volatility': {
                'tp_pips': 12,
                'sl_pips': 6,
                'min_confidence': 0.7,
                'trailing_stop': True
            },
            'neutral': {
                'tp_pips': 7,
                'sl_pips': 5,
                'min_confidence': 0.6,
                'trailing_stop': True
            }
        }
        
    def analyze_and_decide(self) -> dict:
        """
        Главный метод принятия торгового решения
        Возвращает: {'action': 'buy'/'sell'/'wait', 'confidence': 0.0-1.0, 'reason': str}
        """
        decision = {
            'action': 'wait',
            'confidence': 0.0,
            'reason': 'No signal',
            'details': {}
        }
        
        try:
            # 1. Проверка разрешения на торговлю
            can_trade = self.risk.can_trade()
            if not can_trade['allowed']:
                decision['reason'] = can_trade['reason']
                return decision
                
            # 2. Получение данных
            df = self.mt5.get_historical_data(timeframe=mt5.TIMEFRAME_M5, bars=1000)
            if df.empty:
                decision['reason'] = 'No historical data'
                return decision
                
            # 3. Анализ рынка
            market_analysis = self.analyzer.analyze(df)
            decision['details']['market_analysis'] = market_analysis
            
            # 4. Детекция режима рынка и адаптация
            self._detect_and_adapt_regime(df, market_analysis)
            decision['details']['market_regime'] = self.market_regime
            
            # 5. ML-предсказание
            features = self.ml.extract_features(df, market_analysis)
            ml_prediction = self.ml.predict(features)
            decision['details']['ml_prediction'] = ml_prediction
            
            # 6. Комбинированное решение
            decision = self._combine_signals(market_analysis, ml_prediction, decision)
            
            # 7. Финальные проверки
            if decision['action'] != 'wait':
                params = self.regime_params[self.market_regime]
                if decision['confidence'] < params['min_confidence']:
                    logger.debug(f"Confidence too low: {decision['confidence']:.2%} < {params['min_confidence']:.2%}")
                    decision['action'] = 'wait'
                    decision['reason'] = 'Insufficient confidence'
                    
            return decision
            
        except Exception as e:
            logger.exception(f"Error in analyze_and_decide: {e}")
            return decision
            
    def _detect_and_adapt_regime(self, df, market_analysis):
        """Определение режима рынка и адаптация параметров"""
        # ATR для волатильности
        atr = market_analysis['technical']['indicators'].get('atr', 0)
        avg_atr = df['close'].rolling(20).std().mean() if not df.empty else 0
        
        # Тренд
        trend = market_analysis['technical']['indicators'].get('trend', 'neutral')
        
        # ADX для силы тренда (упрощённая версия через волатильность)
        volatility_ratio = atr / avg_atr if avg_atr > 0 else 1
        
        # Определение режима
        if volatility_ratio > 1.5:
            new_regime = 'high_volatility'
        elif trend in ['uptrend', 'downtrend'] and volatility_ratio > 1.0:
            new_regime = 'trending'
        elif trend == 'neutral':
            new_regime = 'ranging'
        else:
            new_regime = 'neutral'
            
        if new_regime != self.market_regime:
            logger.info(f"🔄 Market regime changed: {self.market_regime} -> {new_regime}")
            self.market_regime = new_regime
            
            # Обновление параметров конфига
            params = self.regime_params[new_regime]
            self.config.TP_PIPS = params['tp_pips']
            self.config.SL_PIPS = params['sl_pips']
            
    def _combine_signals(self, market_analysis, ml_prediction, decision: dict) -> dict:
        """Комбинирование сигналов от аналитики и ML"""
        
        # Сигналы
        market_signal = market_analysis['signal']
        ml_signal = ml_prediction['direction']
        
        # Уверенности
        market_confidence = market_analysis['confidence']
        ml_confidence = ml_prediction['probability']
        
        logger.debug(f"Market: {market_signal} ({market_confidence:.2%}), ML: {ml_signal} ({ml_confidence:.2%})")
        
        # Стратегия: требуем согласия обоих систем
        if market_signal == 'BUY' and ml_signal == 'buy':
            decision['action'] = 'buy'
            decision['confidence'] = (market_confidence * 0.5 + ml_confidence * 0.5)
            decision['reason'] = 'Market + ML agree on BUY'
            
        elif market_signal == 'SELL' and ml_signal == 'sell':
            decision['action'] = 'sell'
            decision['confidence'] = (market_confidence * 0.5 + ml_confidence * 0.5)
            decision['reason'] = 'Market + ML agree on SELL'
            
        else:
            # Разногласия = ждём
            decision['action'] = 'wait'
            decision['reason'] = f'Signals disagree (Market: {market_signal}, ML: {ml_signal})'
            
        return decision
        
    def execute_trade(self, decision: dict) -> dict:
        """
        Исполнение торгового решения
        Возвращает: {'success': bool, 'ticket': int, 'details': dict}
        """
        result = {'success': False, 'ticket': None, 'details': {}}
        
        if decision['action'] == 'wait':
            return result
            
        try:
            direction = decision['action']
            
            # Получение текущей цены
            bid, ask = self.mt5.get_current_price()
            entry_price = ask if direction == 'buy' else bid
            
            # Расчёт SL/TP
            sl_tp = self.risk.calculate_sl_tp(direction, entry_price)
            sl = sl_tp['sl']
            tp = sl_tp['tp']
            
            # Расчёт размера позиции
            lot_size = self.risk.calculate_position_size(self.config.SL_PIPS)
            
            # Размещение ордера
            comment = f"{decision['reason'][:30]} | Conf: {decision['confidence']:.2%}"
            order_result = self.mt5.place_order(
                order_type=direction,
                volume=lot_size,
                sl=sl,
                tp=tp,
                comment=comment
            )
            
            if order_result['success']:
                result['success'] = True
                result['ticket'] = order_result['ticket']
                result['details'] = {
                    'direction': direction,
                    'entry_price': entry_price,
                    'sl': sl,
                    'tp': tp,
                    'lot_size': lot_size,
                    'confidence': decision['confidence'],
                    'reason': decision['reason']
                }
                
                logger.info(f"""
🚀 Trade Opened #{result['ticket']}:
   Direction: {direction.upper()}
   Entry: {entry_price}
   SL: {sl} | TP: {tp}
   Size: {lot_size} lots
   Confidence: {decision['confidence']:.2%}
                """)
            else:
                logger.error(f"❌ Order failed: {order_result.get('error', 'Unknown')}")
                result['details']['error'] = order_result.get('error')
                
        except Exception as e:
            logger.exception(f"Error executing trade: {e}")
            result['details']['error'] = str(e)
            
        return result
        
    def manage_open_positions(self):
        """Управление открытыми позициями (trailing stop, закрытие перед новостями)"""
        positions = self.mt5.get_open_positions()
        
        for position in positions:
            try:
                # Trailing stop
                params = self.regime_params[self.market_regime]
                if params['trailing_stop']:
                    self.risk.update_trailing_stop(position)
                    
                # Закрытие перед критическими новостями
                # TODO: интеграция с календарём новостей
                
                # Закрытие слишком старых позиций (больше 1 часа = не скальпинг)
                position_age = datetime.now() - position['time']
                if position_age.seconds > 3600:
                    logger.warning(f"Closing old position #{position['ticket']} (age: {position_age})")
                    self.mt5.close_position(position['ticket'])
                    
            except Exception as e:
                logger.error(f"Error managing position #{position['ticket']}: {e}")
                
    def close_position_and_record(self, ticket: int, reason: str = 'manual'):
        """Закрыть позицию и записать результат"""
        positions = self.mt5.get_open_positions()
        position = next((p for p in positions if p['ticket'] == ticket), None)
        
        if not position:
            logger.error(f"Position #{ticket} not found")
            return False
            
        # Закрытие
        success = self.mt5.close_position(ticket)
        
        if success:
            # Запись результата
            trade_data = {
                'ticket': ticket,
                'direction': position['type'],
                'entry_price': position['price_open'],
                'exit_price': position['price_current'],
                'profit': position['profit'],
                'reason': reason
            }
            
            self.risk.record_trade(trade_data)
            return True
            
        return False
