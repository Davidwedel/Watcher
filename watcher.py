#!/usr/bin/env python3
"""
Watcher script that monitors IP addresses and Telegram bots.
Sends notifications via Telegram when failures are detected.
"""

import os
import subprocess
import asyncio
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from telegram import Bot
from telegram.error import TelegramError
from telethon import TelegramClient, events

# Load environment variables
load_dotenv()

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
NOTIFICATION_CHAT_ID = os.getenv('NOTIFICATION_CHAT_ID')
TELEGRAM_API_ID = int(os.getenv('TELEGRAM_API_ID', '0'))
TELEGRAM_API_HASH = os.getenv('TELEGRAM_API_HASH')
TELEGRAM_PHONE = os.getenv('TELEGRAM_PHONE')
TELETHON_SESSION_PATH = os.getenv('TELETHON_SESSION_PATH', './data/telegram_session')
TELEGRAM_BOTS_TO_PING = os.getenv('TELEGRAM_BOTS_TO_PING', '').split(',')
IP_ADDRESSES_TO_PING = os.getenv('IP_ADDRESSES_TO_PING', '').split(',')
CHECK_INTERVAL_HOURS = int(os.getenv('CHECK_INTERVAL_HOURS', '24'))
PING_TIMEOUT = int(os.getenv('PING_TIMEOUT', '5'))
TELEGRAM_RESPONSE_TIMEOUT = int(os.getenv('TELEGRAM_RESPONSE_TIMEOUT', '30'))

# Filter empty strings
TELEGRAM_BOTS_TO_PING = [bot.strip() for bot in TELEGRAM_BOTS_TO_PING if bot.strip()]
IP_ADDRESSES_TO_PING = [ip.strip() for ip in IP_ADDRESSES_TO_PING if ip.strip()]


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


async def check_telegram_bot(client, username):
    """Send ping to a bot via personal account and wait for a reply."""
    response_received = asyncio.Event()
    response_msg_id = None

    @client.on(events.NewMessage(from_users=username))
    async def handler(event):
        nonlocal response_msg_id
        if event.message.text.strip().lower() == "ping":
            response_msg_id = event.message.id
            response_received.set()

    sent_msg = None
    try:
        sent_msg = await client.send_message(username, "ping")
        try:
            await asyncio.wait_for(response_received.wait(), timeout=TELEGRAM_RESPONSE_TIMEOUT)
            # Delete both our ping and the bot's response
            ids_to_delete = [sent_msg.id]
            if response_msg_id:
                ids_to_delete.append(response_msg_id)
            await client.delete_messages(username, ids_to_delete)
            return True
        except asyncio.TimeoutError:
            # Delete just our ping
            await client.delete_messages(username, [sent_msg.id])
            return False
    except Exception as e:
        print(f"Error pinging Telegram bot {username}: {e}")
        if sent_msg:
            try:
                await client.delete_messages(username, [sent_msg.id])
            except Exception:
                pass
        return False
    finally:
        client.remove_event_handler(handler)


async def send_notification(bot, message):
    """Send failure notification via Telegram."""
    try:
        await bot.send_message(
            chat_id=NOTIFICATION_CHAT_ID,
            text=f"*Watcher Alert*\n\n{message}",
            parse_mode='Markdown'
        )
    except TelegramError as e:
        print(f"Error sending notification: {e}")


async def run_checks(client):
    """Run all monitoring checks."""
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    failures = []

    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Running checks...")

    # Check IP addresses
    for address in IP_ADDRESSES_TO_PING:
        print(f"Checking IP/domain: {address}")
        if not await ping_ip(address):
            failures.append(f"IP/Domain failed: `{address}`")
            print(f"  FAILED")
        else:
            print(f"  OK")

    # Check Telegram bots
    for username in TELEGRAM_BOTS_TO_PING:
        print(f"Checking Telegram bot: {username}")
        if not await check_telegram_bot(client, username):
            failures.append(f"Telegram bot failed: `{username}`")
            print(f"  FAILED")
        else:
            print(f"  OK")

    # Send notifications if there are failures
    if failures:
        notification = "\n".join(failures)
        print(f"\nSending notification about {len(failures)} failure(s)")
        await send_notification(bot, notification)
    else:
        print("\nAll checks passed")


async def main():
    """Main function to run the watcher."""
    if not TELEGRAM_BOT_TOKEN or not NOTIFICATION_CHAT_ID:
        print("ERROR: TELEGRAM_BOT_TOKEN and NOTIFICATION_CHAT_ID must be set in .env")
        return

    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH or not TELEGRAM_PHONE:
        print("ERROR: TELEGRAM_API_ID, TELEGRAM_API_HASH, and TELEGRAM_PHONE must be set in .env")
        return

    Path(TELETHON_SESSION_PATH).parent.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(TELETHON_SESSION_PATH, TELEGRAM_API_ID, TELEGRAM_API_HASH)
    await client.start(phone=TELEGRAM_PHONE)

    print("Watcher started!")
    print(f"Monitoring {len(IP_ADDRESSES_TO_PING)} IP/domain(s) and {len(TELEGRAM_BOTS_TO_PING)} Telegram bot(s)")
    print(f"Check interval: {CHECK_INTERVAL_HOURS} hour(s)")

    while True:
        try:
            await run_checks(client)
        except Exception as e:
            print(f"Error during checks: {e}")

        print(f"\nNext check in {CHECK_INTERVAL_HOURS} hour(s)...")
        await asyncio.sleep(CHECK_INTERVAL_HOURS * 3600)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nWatcher stopped by user")
