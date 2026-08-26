"""
Risk Manager - управление рисками и позициями
"""
from loguru import logger
from datetime import datetime, timedelta
import math

class RiskManager:
    """Управление рисками на основе Kelly Criterion и динамической защиты"""
    
    def __init__(self, config, mt5_connector):
        self.config = config
        self.mt5 = mt5_connector
        
        # Статистика сделок
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.total_profit = 0.0
        self.total_loss = 0.0
        
        # Daily tracking
        self.daily_trades = 0
        self.daily_profit = 0.0
        self.daily_start_balance = 0.0
        self.consecutive_losses = 0
        self.last_trade_date = None
        
        # Trade history для самообучения
        self.trade_history = []
        
    def reset_daily_stats(self):
        """Сброс дневной статистики"""
        account_info = self.mt5.get_account_info()
        self.daily_trades = 0
        self.daily_profit = 0.0
        self.daily_start_balance = account_info.get('balance', self.config.INITIAL_BALANCE)
        self.last_trade_date = datetime.now().date()
        logger.info(f"📊 Daily stats reset. Starting balance: ${self.daily_start_balance:.2f}")
        
    def can_trade(self) -> dict:
        """
        Проверка возможности открытия новой сделки
        Возвращает: {'allowed': bool, 'reason': str}
        """
        # Проверка новый ли день
        today = datetime.now().date()
        if self.last_trade_date is None or self.last_trade_date != today:
            self.reset_daily_stats()
            
        # 1. Проверка лимита сделок за день
        if self.daily_trades >= self.config.MAX_TRADES_PER_DAY:
            return {
                'allowed': False,
                'reason': f'Daily trade limit reached: {self.daily_trades}/{self.config.MAX_TRADES_PER_DAY}'
            }
            
        # 2. Проверка дневной просадки
        account_info = self.mt5.get_account_info()
        current_balance = account_info.get('balance', 0)
        daily_drawdown = (self.daily_start_balance - current_balance) / self.daily_start_balance
        
        if daily_drawdown >= self.config.MAX_DAILY_DRAWDOWN:
            return {
                'allowed': False,
                'reason': f'Daily drawdown limit reached: {daily_drawdown:.2%} >= {self.config.MAX_DAILY_DRAWDOWN:.2%}'
            }
            
        # 3. Правило "3 убытка подряд = стоп"
        if self.consecutive_losses >= 3:
            return {
                'allowed': False,
                'reason': f'Too many consecutive losses: {self.consecutive_losses}'
            }
            
        # 4. Проверка winrate (если есть минимум 10 сделок)
        if self.total_trades >= 10:
            winrate = self.winning_trades / self.total_trades
            if winrate < self.config.MIN_WINRATE_THRESHOLD:
                return {
                    'allowed': False,
                    'reason': f'Winrate too low: {winrate:.2%} < {self.config.MIN_WINRATE_THRESHOLD:.2%}'
                }
                
        # 5. Проверка торговых часов
        current_hour = datetime.utcnow().hour
        if not (self.config.TRADING_START_HOUR <= current_hour <= self.config.TRADING_END_HOUR):
            return {
                'allowed': False,
                'reason': f'Outside trading hours: {current_hour}:00 UTC'
            }
            
        # 6. Проверка открытых позиций (максимум 1 одновременно для скальпинга)
        open_positions = self.mt5.get_open_positions()
        if len(open_positions) > 0:
            return {
                'allowed': False,
                'reason': f'Already have {len(open_positions)} open position(s)'
            }
            
        return {'allowed': True, 'reason': 'All checks passed'}
        
    def calculate_position_size(self, stop_loss_pips: float) -> float:
        """
        Расчёт размера позиции на основе Kelly Criterion
        stop_loss_pips: размер стопа в пипсах
        """
        account_info = self.mt5.get_account_info()
        balance = account_info.get('balance', self.config.INITIAL_BALANCE)
        
        # Базовый риск на сделку
        risk_amount = balance * self.config.MAX_RISK_PER_TRADE
        
        # Адаптация на основе последних результатов
        if self.total_trades >= 10:
            winrate = self.winning_trades / self.total_trades
            avg_win = self.total_profit / self.winning_trades if self.winning_trades > 0 else 0
            avg_loss = abs(self.total_loss / self.losing_trades) if self.losing_trades > 0 else 1
            
            # Kelly Criterion: f* = (p*b - q) / b
            # где p = winrate, q = 1-p, b = avg_win/avg_loss
            if avg_loss > 0:
                b = avg_win / avg_loss
                kelly_fraction = (winrate * b - (1 - winrate)) / b
                kelly_fraction = max(0, min(kelly_fraction, 0.25))  # Cap at 25%
                
                # Консервативная версия (половина Kelly)
                kelly_fraction *= 0.5
                
                risk_amount = balance * kelly_fraction
                logger.debug(f"Kelly sizing: {kelly_fraction:.2%} of balance")
        
        # Уменьшение размера после убытков
        if self.consecutive_losses > 0:
            reduction_factor = 0.5 ** self.consecutive_losses
            risk_amount *= reduction_factor
            logger.debug(f"Risk reduced by {reduction_factor:.2%} due to consecutive losses")
            
        # Увеличение после серии побед (максимум 1.5x)
        if self.consecutive_losses == 0 and self.total_trades > 0:
            consecutive_wins = self._count_recent_wins()
            if consecutive_wins >= 3:
                risk_amount *= min(1.5, 1 + consecutive_wins * 0.1)
                
        # Расчёт лота
        symbol_info = self.mt5.get_symbol_info()
        point = symbol_info.get('point', 0.01)
        contract_size = symbol_info.get('trade_contract_size', 100)
        
        # Для золота: 1 пипс = 0.1, point обычно 0.01
        pip_value = contract_size * (point * 10)  # Value of 1 pip per 1 lot
        
        # Расчёт лота: risk_amount / (stop_loss_pips * pip_value)
        lot_size = risk_amount / (stop_loss_pips * pip_value)
        
        # Округление до минимального шага
        volume_min = symbol_info.get('volume_min', 0.01)
        volume_max = symbol_info.get('volume_max', 100.0)
        volume_step = symbol_info.get('volume_step', 0.01)
        
        lot_size = max(volume_min, lot_size)
        lot_size = min(volume_max, lot_size)
        lot_size = round(lot_size / volume_step) * volume_step
        
        logger.info(f"💰 Position size: {lot_size} lots (risk: ${risk_amount:.2f}, SL: {stop_loss_pips} pips)")
        
        return lot_size
        
    def calculate_sl_tp(self, direction: str, entry_price: float) -> dict:
        """
        Расчёт Stop Loss и Take Profit
        direction: 'buy' или 'sell'
        entry_price: цена входа
        """
        symbol_info = self.mt5.get_symbol_info()
        point = symbol_info.get('point', 0.01)
        
        # Для золота пипс = 0.1
        pip_value = point * 10
        
        sl_distance = self.config.SL_PIPS * pip_value
        tp_distance = self.config.TP_PIPS * pip_value
        
        if direction.lower() == 'buy':
            sl = entry_price - sl_distance
            tp = entry_price + tp_distance
        else:
            sl = entry_price + sl_distance
            tp = entry_price - tp_distance
            
        return {
            'sl': round(sl, symbol_info.get('digits', 2)),
            'tp': round(tp, symbol_info.get('digits', 2))
        }
        
    def update_trailing_stop(self, position: dict):
        """
        Обновление trailing stop для прибыльной позиции
        position: словарь с данными позиции
        """
        ticket = position['ticket']
        position_type = position['type']
        entry_price = position['price_open']
        current_price = position['price_current']
        current_sl = position['sl']
        
        symbol_info = self.mt5.get_symbol_info()
        point = symbol_info.get('point', 0.01)
        pip_value = point * 10
        
        trailing_distance = self.config.TRAILING_STOP_PIPS * pip_value
        
        # Минимальная прибыль для активации trailing stop
        min_profit_pips = 3 * pip_value
        
        if position_type == 'buy':
            profit = current_price - entry_price
            if profit >= min_profit_pips:
                new_sl = current_price - trailing_distance
                if new_sl > current_sl:
                    self.mt5.modify_position(ticket, sl=new_sl)
                    logger.info(f"🔄 Trailing stop updated for #{ticket}: {current_sl} -> {new_sl}")
        else:  # sell
            profit = entry_price - current_price
            if profit >= min_profit_pips:
                new_sl = current_price + trailing_distance
                if new_sl < current_sl or current_sl == 0:
                    self.mt5.modify_position(ticket, sl=new_sl)
                    logger.info(f"🔄 Trailing stop updated for #{ticket}: {current_sl} -> {new_sl}")
                    
    def record_trade(self, trade_data: dict):
        """
        Запись результата сделки
        trade_data: {'ticket', 'direction', 'entry_price', 'exit_price', 'profit', 'reason'}
        """
        self.total_trades += 1
        self.daily_trades += 1
        
        profit = trade_data.get('profit', 0)
        
        if profit > 0:
            self.winning_trades += 1
            self.total_profit += profit
            self.consecutive_losses = 0
            logger.info(f"✅ Win #{self.total_trades}: +${profit:.2f}")
        else:
            self.losing_trades += 1
            self.total_loss += profit
            self.consecutive_losses += 1
            logger.info(f"❌ Loss #{self.total_trades}: ${profit:.2f}")
            
        self.daily_profit += profit
        
        # Добавление в историю
        trade_data['timestamp'] = datetime.now()
        trade_data['daily_trades'] = self.daily_trades
        trade_data['total_trades'] = self.total_trades
        self.trade_history.append(trade_data)
        
        # Логирование статистики
        self._log_statistics()
        
    def _log_statistics(self):
        """Вывод текущей статистики"""
        if self.total_trades == 0:
            return
            
        winrate = self.winning_trades / self.total_trades
        avg_win = self.total_profit / self.winning_trades if self.winning_trades > 0 else 0
        avg_loss = self.total_loss / self.losing_trades if self.losing_trades > 0 else 0
        
        logger.info(f"""
📊 Trading Statistics:
   Total trades: {self.total_trades}
   Winrate: {winrate:.2%} ({self.winning_trades}W / {self.losing_trades}L)
   Avg Win: ${avg_win:.2f} | Avg Loss: ${avg_loss:.2f}
   Daily: {self.daily_trades} trades, ${self.daily_profit:.2f}
   Consecutive losses: {self.consecutive_losses}
        """)
        
    def _count_recent_wins(self, lookback: int = 5) -> int:
        """Подсчёт последних побед подряд"""
        if len(self.trade_history) < lookback:
            lookback = len(self.trade_history)
            
        recent = self.trade_history[-lookback:]
        consecutive = 0
        
        for trade in reversed(recent):
            if trade.get('profit', 0) > 0:
                consecutive += 1
            else:
                break
                
        return consecutive
        
    def get_statistics(self) -> dict:
        """Получить статистику для самообучения"""
        return {
            'total_trades': self.total_trades,
            'winning_trades': self.winning_trades,
            'losing_trades': self.losing_trades,
            'winrate': self.winning_trades / self.total_trades if self.total_trades > 0 else 0,
            'total_profit': self.total_profit,
            'total_loss': self.total_loss,
            'avg_win': self.total_profit / self.winning_trades if self.winning_trades > 0 else 0,
            'avg_loss': self.total_loss / self.losing_trades if self.losing_trades > 0 else 0,
            'daily_trades': self.daily_trades,
            'daily_profit': self.daily_profit,
            'trade_history': self.trade_history
        }
