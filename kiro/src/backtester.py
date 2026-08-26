"""
Backtester - тестирование стратегии на исторических данных
"""
import pandas as pd
import numpy as np
from loguru import logger
from datetime import datetime, timedelta
import MetaTrader5 as mt5
from typing import List, Dict
import json

class Backtester:
    """Фреймворк для бэктестирования стратегии"""
    
    def __init__(self, config, strategy, initial_balance: float = 100.0):
        self.config = config
        self.strategy = strategy
        self.initial_balance = initial_balance
        
        # Результаты
        self.trades = []
        self.equity_curve = []
        self.metrics = {}
        
    def run(self, df: pd.DataFrame, start_date=None, end_date=None) -> Dict:
        """
        Запуск бэктеста
        df: исторические данные с OHLCV
        """
        logger.info(f"🔄 Starting backtest on {len(df)} bars...")
        
        if start_date:
            df = df[df['time'] >= start_date]
        if end_date:
            df = df[df['time'] <= end_date]
            
        # Инициализация
        balance = self.initial_balance
        equity = balance
        open_position = None
        
        # Симуляция торговли
        for i in range(100, len(df)):
            window = df.iloc[:i+1]
            current_bar = df.iloc[i]
            
            # Управление открытой позицией
            if open_position:
                result = self._check_position_close(open_position, current_bar)
                
                if result['closed']:
                    profit = result['profit']
                    balance += profit
                    equity = balance
                    
                    trade_record = {
                        'entry_time': open_position['entry_time'],
                        'exit_time': current_bar['time'],
                        'direction': open_position['direction'],
                        'entry_price': open_position['entry_price'],
                        'exit_price': result['exit_price'],
                        'profit': profit,
                        'balance': balance,
                        'reason': result['reason']
                    }
                    
                    self.trades.append(trade_record)
                    open_position = None
                    
                    logger.debug(f"Trade closed: {trade_record['direction']} @ {trade_record['exit_price']:.2f}, P/L: ${profit:.2f}")
                    
            # Поиск новой точки входа
            if open_position is None:
                signal = self._generate_signal(window)
                
                if signal['action'] in ['buy', 'sell']:
                    # Проверка spread
                    spread = self._calculate_spread(current_bar)
                    if spread <= self.config.MAX_SPREAD_PIPS:
                        # Открытие позиции
                        entry_price = current_bar['close']
                        
                        # Расчёт размера позиции (упрощённо)
                        lot_size = self._calculate_lot_size(balance)
                        
                        # SL/TP
                        if signal['action'] == 'buy':
                            sl = entry_price - (self.config.SL_PIPS * 0.1)
                            tp = entry_price + (self.config.TP_PIPS * 0.1)
                        else:
                            sl = entry_price + (self.config.SL_PIPS * 0.1)
                            tp = entry_price - (self.config.TP_PIPS * 0.1)
                            
                        open_position = {
                            'direction': signal['action'],
                            'entry_time': current_bar['time'],
                            'entry_price': entry_price,
                            'sl': sl,
                            'tp': tp,
                            'lot_size': lot_size
                        }
                        
                        logger.debug(f"Position opened: {signal['action']} @ {entry_price:.2f}")
                        
            # Запись equity
            self.equity_curve.append({
                'time': current_bar['time'],
                'equity': equity,
                'balance': balance
            })
            
        # Закрытие оставшейся позиции
        if open_position:
            last_bar = df.iloc[-1]
            result = self._force_close_position(open_position, last_bar)
            profit = result['profit']
            balance += profit
            
            self.trades.append({
                'entry_time': open_position['entry_time'],
                'exit_time': last_bar['time'],
                'direction': open_position['direction'],
                'entry_price': open_position['entry_price'],
                'exit_price': result['exit_price'],
                'profit': profit,
                'balance': balance,
                'reason': 'backtest_end'
            })
            
        # Расчёт метрик
        self.metrics = self._calculate_metrics(balance)
        
        logger.info(f"✅ Backtest completed: {len(self.trades)} trades")
        self._print_results()
        
        return {
            'trades': self.trades,
            'equity_curve': self.equity_curve,
            'metrics': self.metrics
        }
        
    def _generate_signal(self, window: pd.DataFrame) -> Dict:
        """Генерация торгового сигнала (упрощённая версия)"""
        # Используем только технический анализ для бэктеста
        from src.market_analyzer import MarketAnalyzer
        
        analyzer = MarketAnalyzer(None, self.config)
        analysis = analyzer._technical_analysis(window)
        
        return {
            'action': analysis['signal'].lower() if analysis['signal'] != 'WAIT' else 'wait',
            'confidence': analysis.get('score', 0) / 5.0  # Нормализация
        }
        
    def _check_position_close(self, position: Dict, current_bar: pd.Series) -> Dict:
        """Проверка закрытия позиции по SL/TP"""
        high = current_bar['high']
        low = current_bar['low']
        close = current_bar['close']
        
        if position['direction'] == 'buy':
            # Проверка SL
            if low <= position['sl']:
                return {
                    'closed': True,
                    'exit_price': position['sl'],
                    'profit': (position['sl'] - position['entry_price']) * position['lot_size'] * 100,
                    'reason': 'stop_loss'
                }
            # Проверка TP
            if high >= position['tp']:
                return {
                    'closed': True,
                    'exit_price': position['tp'],
                    'profit': (position['tp'] - position['entry_price']) * position['lot_size'] * 100,
                    'reason': 'take_profit'
                }
        else:  # sell
            # Проверка SL
            if high >= position['sl']:
                return {
                    'closed': True,
                    'exit_price': position['sl'],
                    'profit': (position['entry_price'] - position['sl']) * position['lot_size'] * 100,
                    'reason': 'stop_loss'
                }
            # Проверка TP
            if low <= position['tp']:
                return {
                    'closed': True,
                    'exit_price': position['tp'],
                    'profit': (position['entry_price'] - position['tp']) * position['lot_size'] * 100,
                    'reason': 'take_profit'
                }
                
        return {'closed': False}
        
    def _force_close_position(self, position: Dict, bar: pd.Series) -> Dict:
        """Принудительное закрытие позиции"""
        exit_price = bar['close']
        
        if position['direction'] == 'buy':
            profit = (exit_price - position['entry_price']) * position['lot_size'] * 100
        else:
            profit = (position['entry_price'] - exit_price) * position['lot_size'] * 100
            
        return {
            'closed': True,
            'exit_price': exit_price,
            'profit': profit,
            'reason': 'forced'
        }
        
    def _calculate_lot_size(self, balance: float) -> float:
        """Упрощённый расчёт размера позиции"""
        risk_amount = balance * self.config.MAX_RISK_PER_TRADE
        lot_size = risk_amount / (self.config.SL_PIPS * 10)  # 10$ per pip per lot (примерно)
        return max(0.01, min(1.0, lot_size))
        
    def _calculate_spread(self, bar: pd.Series) -> float:
        """Оценка спреда (симуляция)"""
        # В реальности нужны bid/ask данные
        # Для золота обычно 2-4 пипса
        return 2.5
        
    def _calculate_metrics(self, final_balance: float) -> Dict:
        """Расчёт метрик производительности"""
        if len(self.trades) == 0:
            return {}
            
        trades_df = pd.DataFrame(self.trades)
        
        # Базовые метрики
        total_trades = len(self.trades)
        winning_trades = len(trades_df[trades_df['profit'] > 0])
        losing_trades = len(trades_df[trades_df['profit'] <= 0])
        
        winrate = winning_trades / total_trades if total_trades > 0 else 0
        
        total_profit = trades_df[trades_df['profit'] > 0]['profit'].sum()
        total_loss = trades_df[trades_df['profit'] <= 0]['profit'].sum()
        
        net_profit = final_balance - self.initial_balance
        roi = (net_profit / self.initial_balance) * 100
        
        avg_win = total_profit / winning_trades if winning_trades > 0 else 0
        avg_loss = total_loss / losing_trades if losing_trades > 0 else 0
        
        profit_factor = abs(total_profit / total_loss) if total_loss != 0 else 0
        
        # Drawdown
        equity_df = pd.DataFrame(self.equity_curve)
        equity_series = equity_df['equity']
        running_max = equity_series.cummax()
        drawdown = (equity_series - running_max) / running_max
        max_drawdown = drawdown.min() * 100
        
        # Sharpe Ratio (упрощённо)
        returns = trades_df['profit'].pct_change().dropna()
        sharpe = (returns.mean() / returns.std() * np.sqrt(252)) if returns.std() > 0 else 0
        
        return {
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'winrate': winrate,
            'net_profit': net_profit,
            'roi': roi,
            'profit_factor': profit_factor,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'max_drawdown': max_drawdown,
            'sharpe_ratio': sharpe,
            'initial_balance': self.initial_balance,
            'final_balance': final_balance
        }
        
    def _print_results(self):
        """Вывод результатов бэктеста"""
        m = self.metrics
        
        logger.info(f"""
╔══════════════════════════════════════════════════════════
║ 📊 BACKTEST RESULTS
╠══════════════════════════════════════════════════════════
║ Initial Balance:    ${m['initial_balance']:.2f}
║ Final Balance:      ${m['final_balance']:.2f}
║ Net Profit:         ${m['net_profit']:.2f}
║ ROI:                {m['roi']:.2f}%
║ 
║ Total Trades:       {m['total_trades']}
║ Winning Trades:     {m['winning_trades']}
║ Losing Trades:      {m['losing_trades']}
║ Winrate:            {m['winrate']:.2%}
║ 
║ Profit Factor:      {m['profit_factor']:.2f}
║ Avg Win:            ${m['avg_win']:.2f}
║ Avg Loss:           ${m['avg_loss']:.2f}
║ 
║ Max Drawdown:       {m['max_drawdown']:.2f}%
║ Sharpe Ratio:       {m['sharpe_ratio']:.2f}
╚══════════════════════════════════════════════════════════
        """)
        
    def save_report(self, filename: str = 'backtest_report.json'):
        """Сохранение отчёта"""
        report = {
            'timestamp': datetime.now().isoformat(),
            'metrics': self.metrics,
            'trades': self.trades,
            'equity_curve': self.equity_curve
        }
        
        with open(filename, 'w') as f:
            json.dump(report, f, indent=2, default=str)
            
        logger.info(f"Report saved to {filename}")
        
    def plot_equity_curve(self):
        """Построение графика equity"""
        try:
            import matplotlib.pyplot as plt
            
            df = pd.DataFrame(self.equity_curve)
            
            plt.figure(figsize=(12, 6))
            plt.plot(df['time'], df['equity'], label='Equity', linewidth=2)
            plt.plot(df['time'], df['balance'], label='Balance', linewidth=1, alpha=0.7)
            plt.axhline(y=self.initial_balance, color='r', linestyle='--', label='Initial')
            
            plt.title('Equity Curve')
            plt.xlabel('Time')
            plt.ylabel('Balance ($)')
            plt.legend()
            plt.grid(True, alpha=0.3)
            plt.tight_layout()
            
            plt.savefig('equity_curve.png', dpi=150)
            logger.info("Equity curve saved to equity_curve.png")
            
        except ImportError:
            logger.warning("matplotlib not installed, skipping plot")
