# telegram_bot/main.py
import os
import asyncio
import database as db  # Imports your existing root database.py
from telegram_bot.config import TELEGRAM_BOT_TOKEN
from telegram_bot.handlers import dp, bot

async def dummy_port_binder():
    """Spins up a stable, lightweight background HTTP port binder for Render health checks."""
    port = int(os.getenv("PORT", 8080))
    
    async def handle_client(reader, writer):
        try:
            # Short read timeout prevents lingering open sockets from crashing the binder
            try:
                await asyncio.wait_for(reader.read(1024), timeout=3.0)
            except asyncio.TimeoutError:
                pass
                
            response = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/plain\r\n"
                "Content-Length: 26\r\n"
                "Connection: close\r\n\r\n"
                "Telegram Bot is active! 🚀"
            )
            writer.write(response.encode("utf-8"))
            await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            
    try:
        server = await asyncio.start_server(handle_client, "0.0.0.0", port)
        print(f"📡 Port binder active on port {port}. Fulfilling Render health checks.")
        async with server:
            await server.serve_forever()
    except Exception as e:
        print(f"⚠️ Port binder failed to start: {e}")

async def main_bot():
    """System bootstrapping entry point."""
    print("🚀 Initializing system modules...")
    print("📡 Connecting to PostgreSQL Database...")
    await db.init_db()
    
    # Launch the lightweight port binder in the background
    asyncio.create_task(dummy_port_binder())
    
    print("🤖 Launching Reseller Telegram Bot...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main_bot())
