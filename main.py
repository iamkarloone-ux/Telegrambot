# telegram_bot/main.py
import asyncio
import database as db  # Imports your existing root database.py
from telegram_bot.config import TELEGRAM_BOT_TOKEN
from telegram_bot.handlers import dp, bot

async def main_bot():
    """System bootstrapping entry point."""
    print("🚀 Initializing system modules...")
    print("📡 Connecting to PostgreSQL Database...")
    await db.init_db()
    
    print("🤖 Launching Reseller Telegram Bot...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main_bot())