"""
MT5 Connector - подключение и работа с MetaTrader5
"""
import MetaTrader5 as mt5
from loguru import logger
from config import Config
from datetime import datetime
import pandas as pd

class MT5Connector:
    """Коннектор для работы с MT5"""
    
    def __init__(self):
        self.config = Config()
        self.connected = False
        self.account_info = None
        
    def connect(self) -> bool:
        """Подключение к MT5"""
        try:
            # Инициализация MT5
            if not mt5.initialize(
                path=self.config.MT5_PATH,
                login=self.config.MT5_LOGIN,
                password=self.config.MT5_PASSWORD,
                server=self.config.MT5_SERVER
            ):
                error = mt5.last_error()
                logger.error(f"MT5 initialization failed: {error}")
                return False
                
            # Проверка авторизации
            account = mt5.account_info()
            if account is None:
                logger.error("Failed to get account info")
                return False
                
            self.account_info = account
            self.connected = True
            logger.info(f"Connected to account #{account.login}, balance: ${account.balance}")
            return True
            
        except Exception as e:
            logger.exception(f"Connection error: {e}")
            return False
            
    def disconnect(self):
        """Отключение от MT5"""
        if self.connected:
            mt5.shutdown()
            self.connected = False
            logger.info("Disconnected from MT5")
            
    def get_account_info(self) -> dict:
        """Получить информацию об аккаунте"""
        if not self.connected:
            return {}
            
        acc = mt5.account_info()
        return {
            'balance': acc.balance,
            'equity': acc.equity,
            'margin': acc.margin,
            'free_margin': acc.margin_free,
            'profit': acc.profit,
            'leverage': acc.leverage
        }
        
    def get_symbol_info(self, symbol: str = None) -> dict:
        """Информация о символе"""
        symbol = symbol or self.config.SYMBOL
        info = mt5.symbol_info(symbol)
        
        if info is None:
            logger.error(f"Symbol {symbol} not found")
            return {}
            
        return {
            'symbol': info.name,
            'bid': info.bid,
            'ask': info.ask,
            'spread': info.spread,
            'digits': info.digits,
            'point': info.point,
            'trade_contract_size': info.trade_contract_size,
            'volume_min': info.volume_min,
            'volume_max': info.volume_max,
            'volume_step': info.volume_step
        }
        
    def get_current_price(self, symbol: str = None) -> tuple:
        """Получить текущую цену (bid, ask)"""
        symbol = symbol or self.config.SYMBOL
        tick = mt5.symbol_info_tick(symbol)
        
        if tick is None:
            return (0, 0)
            
        return (tick.bid, tick.ask)
        
    def get_spread(self, symbol: str = None) -> float:
        """Получить текущий спред в пипсах"""
        symbol = symbol or self.config.SYMBOL
        info = mt5.symbol_info(symbol)
        
        if info is None:
            return 999.0
            
        spread_points = info.spread
        point = info.point
        
        # Для золота обычно 1 пипс = 0.1
        spread_pips = spread_points * point * 10
        return spread_pips
        
    def get_historical_data(self, symbol: str = None, timeframe=mt5.TIMEFRAME_M5, bars: int = 1000) -> pd.DataFrame:
        """Получить исторические данные"""
        symbol = symbol or self.config.SYMBOL
        
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
        
        if rates is None or len(rates) == 0:
            logger.error(f"Failed to get historical data for {symbol}")
            return pd.DataFrame()
            
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        return df
        
    def place_order(self, order_type: str, volume: float, sl: float = 0, tp: float = 0, comment: str = "") -> dict:
        """
        Разместить ордер
        order_type: 'buy' или 'sell'
        volume: размер лота
        sl: stop loss цена
        tp: take profit цена
        """
        symbol = self.config.SYMBOL
        symbol_info = mt5.symbol_info(symbol)
        
        if symbol_info is None:
            return {'success': False, 'error': 'Symbol not found'}
            
        # Определение типа ордера
        if order_type.lower() == 'buy':
            order_type_mt5 = mt5.ORDER_TYPE_BUY
            price = mt5.symbol_info_tick(symbol).ask
        else:
            order_type_mt5 = mt5.ORDER_TYPE_SELL
            price = mt5.symbol_info_tick(symbol).bid
            
        # Подготовка запроса
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type_mt5,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 20,
            "magic": 234000,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        # Отправка ордера
        result = mt5.order_send(request)
        
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Order failed: {result.comment}")
            return {
                'success': False,
                'error': result.comment,
                'retcode': result.retcode
            }
            
        logger.info(f"✅ Order placed: {order_type} {volume} lots at {price}, ticket #{result.order}")
        return {
            'success': True,
            'ticket': result.order,
            'volume': result.volume,
            'price': result.price,
            'comment': result.comment
        }
        
    def close_position(self, ticket: int) -> bool:
        """Закрыть позицию по ticket"""
        position = mt5.positions_get(ticket=ticket)
        
        if position is None or len(position) == 0:
            logger.error(f"Position {ticket} not found")
            return False
            
        position = position[0]
        symbol = position.symbol
        volume = position.volume
        
        # Определение типа закрывающего ордера
        if position.type == mt5.ORDER_TYPE_BUY:
            order_type = mt5.ORDER_TYPE_SELL
            price = mt5.symbol_info_tick(symbol).bid
        else:
            order_type = mt5.ORDER_TYPE_BUY
            price = mt5.symbol_info_tick(symbol).ask
            
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "position": ticket,
            "price": price,
            "deviation": 20,
            "magic": 234000,
            "comment": "close position",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        result = mt5.order_send(request)
        
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Failed to close position {ticket}: {result.comment}")
            return False
            
        logger.info(f"✅ Position {ticket} closed")
        return True
        
    def get_open_positions(self, symbol: str = None) -> list:
        """Получить открытые позиции"""
        symbol = symbol or self.config.SYMBOL
        positions = mt5.positions_get(symbol=symbol)
        
        if positions is None:
            return []
            
        result = []
        for pos in positions:
            result.append({
                'ticket': pos.ticket,
                'symbol': pos.symbol,
                'type': 'buy' if pos.type == mt5.ORDER_TYPE_BUY else 'sell',
                'volume': pos.volume,
                'price_open': pos.price_open,
                'price_current': pos.price_current,
                'sl': pos.sl,
                'tp': pos.tp,
                'profit': pos.profit,
                'time': datetime.fromtimestamp(pos.time)
            })
            
        return result
        
    def modify_position(self, ticket: int, sl: float = None, tp: float = None) -> bool:
        """Изменить SL/TP позиции"""
        position = mt5.positions_get(ticket=ticket)
        
        if position is None or len(position) == 0:
            return False
            
        position = position[0]
        
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": position.symbol,
            "position": ticket,
            "sl": sl if sl is not None else position.sl,
            "tp": tp if tp is not None else position.tp,
        }
        
        result = mt5.order_send(request)
        return result.retcode == mt5.TRADE_RETCODE_DONE
