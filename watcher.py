#!/usr/bin/env python3
"""
Watcher script that monitors IP addresses and Telegram bots.
Sends notifications via Telegram when failures are detected.
"""

import os
import time
import subprocess
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from telegram import Bot
from telegram.ext import Application, MessageHandler, filters
from telegram.error import TelegramError

# Load environment variables
load_dotenv()

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
NOTIFICATION_CHAT_ID = os.getenv('NOTIFICATION_CHAT_ID')
TELEGRAM_BOTS_TO_PING = os.getenv('TELEGRAM_BOTS_TO_PING', '').split(',')
IP_ADDRESSES_TO_PING = os.getenv('IP_ADDRESSES_TO_PING', '').split(',')
CHECK_INTERVAL_HOURS = int(os.getenv('CHECK_INTERVAL_HOURS', '24'))
PING_TIMEOUT = int(os.getenv('PING_TIMEOUT', '5'))
TELEGRAM_RESPONSE_TIMEOUT = int(os.getenv('TELEGRAM_RESPONSE_TIMEOUT', '30'))

# Filter empty strings
TELEGRAM_BOTS_TO_PING = [bot.strip() for bot in TELEGRAM_BOTS_TO_PING if bot.strip()]
IP_ADDRESSES_TO_PING = [ip.strip() for ip in IP_ADDRESSES_TO_PING if ip.strip()]

# Store bot responses
bot_responses = {}


async def ping_ip(address):
    """Ping an IP address or domain."""
    try:
        result = subprocess.run(
            ['ping', '-c', '1', '-W', str(PING_TIMEOUT), address],
            capture_output=True,
            text=True
        )
        return result.returncode == 0
    except Exception as e:
        print(f"Error pinging {address}: {e}")
        return False


async def ping_telegram_bot(bot, chat_id):
    """Send ping to a Telegram bot and wait for response."""
    try:
        # Clear previous response
        bot_responses[chat_id] = None

        # Send ping message
        await bot.send_message(chat_id=chat_id, text="ping")

        # Wait for response
        start_time = time.time()
        while time.time() - start_time < TELEGRAM_RESPONSE_TIMEOUT:
            if bot_responses.get(chat_id) == "ping":
                return True
            await asyncio.sleep(0.5)

        return False
    except TelegramError as e:
        print(f"Error pinging Telegram bot {chat_id}: {e}")
        return False


async def handle_message(update, context):
    """Handle incoming messages from bots."""
    chat_id = str(update.effective_chat.id)
    message_text = update.message.text.lower().strip() if update.message.text else ""

    if message_text == "ping":
        bot_responses[chat_id] = "ping"


async def send_notification(bot, message):
    """Send failure notification via Telegram."""
    try:
        await bot.send_message(
            chat_id=NOTIFICATION_CHAT_ID,
            text=f"🚨 *Watcher Alert*\n\n{message}",
            parse_mode='Markdown'
        )
    except TelegramError as e:
        print(f"Error sending notification: {e}")


async def run_checks():
    """Run all monitoring checks."""
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    failures = []

    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Running checks...")

    # Check IP addresses
    for address in IP_ADDRESSES_TO_PING:
        print(f"Checking IP/domain: {address}")
        if not await ping_ip(address):
            failure_msg = f"❌ IP/Domain failed: `{address}`"
            failures.append(failure_msg)
            print(f"  FAILED")
        else:
            print(f"  OK")

    # Check Telegram bots
    for chat_id in TELEGRAM_BOTS_TO_PING:
        print(f"Checking Telegram bot: {chat_id}")
        if not await ping_telegram_bot(bot, chat_id):
            failure_msg = f"❌ Telegram bot failed: `{chat_id}`"
            failures.append(failure_msg)
            print(f"  FAILED")
        else:
            print(f"  OK")

    # Send notifications if there are failures
    if failures:
        notification = "\n".join(failures)
        print(f"\nSending notification about {len(failures)} failure(s)")
        await send_notification(bot, notification)
    else:
        print("\nAll checks passed ✓")


async def main():
    """Main function to run the watcher."""
    if not TELEGRAM_BOT_TOKEN or not NOTIFICATION_CHAT_ID:
        print("ERROR: TELEGRAM_BOT_TOKEN and NOTIFICATION_CHAT_ID must be set in .env")
        return

    # Set up Telegram bot application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Add message handler for bot responses
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Start the bot
    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    print("Watcher started!")
    print(f"Monitoring {len(IP_ADDRESSES_TO_PING)} IP/domain(s) and {len(TELEGRAM_BOTS_TO_PING)} Telegram bot(s)")
    print(f"Check interval: {CHECK_INTERVAL_HOURS} hour(s)")

    # Run checks in a loop
    while True:
        try:
            await run_checks()
        except Exception as e:
            print(f"Error during checks: {e}")

        # Wait for next check interval
        print(f"\nNext check in {CHECK_INTERVAL_HOURS} hour(s)...")
        await asyncio.sleep(CHECK_INTERVAL_HOURS * 3600)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nWatcher stopped by user")
