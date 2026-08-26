"""
Telegram Notifier - уведомления о сделках и статусе
"""
from loguru import logger
import asyncio
from telegram import Bot
from telegram.error import TelegramError

class TelegramNotifier:
    """Отправка уведомлений в Telegram"""
    
    def __init__(self, config):
        self.config = config
        self.bot = None
        self.enabled = False
        
        if self.config.TELEGRAM_BOT_TOKEN and self.config.TELEGRAM_CHAT_ID:
            try:
                self.bot = Bot(token=self.config.TELEGRAM_BOT_TOKEN)
                self.enabled = True
                logger.info("✅ Telegram notifications enabled")
            except Exception as e:
                logger.error(f"Failed to initialize Telegram bot: {e}")
        else:
            logger.warning("Telegram credentials not provided, notifications disabled")
            
    async def send_message(self, message: str, parse_mode: str = None):
        """Отправка сообщения"""
        if not self.enabled:
            return
            
        try:
            await self.bot.send_message(
                chat_id=self.config.TELEGRAM_CHAT_ID,
                text=message,
                parse_mode=parse_mode
            )
        except TelegramError as e:
            logger.error(f"Failed to send Telegram message: {e}")
        except Exception as e:
            logger.error(f"Unexpected error sending Telegram message: {e}")
            
    async def notify_trade_opened(self, trade_details: dict):
        """Уведомление об открытии сделки"""
        direction_emoji = "🟢" if trade_details['direction'] == 'buy' else "🔴"
        
        message = f"""
{direction_emoji} **Trade Opened**

Direction: {trade_details['direction'].upper()}
Entry: {trade_details['entry_price']:.2f}
SL: {trade_details['sl']:.2f}
TP: {trade_details['tp']:.2f}
Size: {trade_details['lot_size']} lots
Confidence: {trade_details['confidence']:.1%}

Reason: {trade_details['reason']}
        """
        
        await self.send_message(message, parse_mode='Markdown')
        
    async def notify_trade_closed(self, trade_data: dict):
        """Уведомление о закрытии сделки"""
        profit = trade_data.get('profit', 0)
        emoji = "✅" if profit > 0 else "❌"
        
        message = f"""
{emoji} **Trade Closed**

Ticket: #{trade_data['ticket']}
Direction: {trade_data['direction'].upper()}
Entry: {trade_data['entry_price']:.2f}
Exit: {trade_data.get('exit_price', 0):.2f}
Profit: ${profit:.2f}
        """
        
        await self.send_message(message, parse_mode='Markdown')
        
    async def notify_error(self, error_message: str):
        """Уведомление об ошибке"""
        message = f"⚠️ **Error**\n\n{error_message}"
        await self.send_message(message, parse_mode='Markdown')
        
    async def notify_daily_summary(self, stats: dict):
        """Ежедневная сводка"""
        message = f"""
📊 **Daily Summary**

Trades: {stats['daily_trades']}
Profit: ${stats['daily_profit']:.2f}
Winrate: {stats['winrate']:.1%}

Total Trades: {stats['total_trades']}
Overall Winrate: {stats['winrate']:.1%}
        """
        
        await self.send_message(message, parse_mode='Markdown')
