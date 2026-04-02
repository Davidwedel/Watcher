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
import aiohttp
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
SYSTEMD_SERVICES = os.getenv('SYSTEMD_SERVICES', '').split(',')
CHECK_INTERVAL_HOURS = int(os.getenv('CHECK_INTERVAL_HOURS', '24'))
PING_TIMEOUT = int(os.getenv('PING_TIMEOUT', '5'))
TELEGRAM_RESPONSE_TIMEOUT = int(os.getenv('TELEGRAM_RESPONSE_TIMEOUT', '30'))
ANOMALOUS_BOT = os.getenv('ANOMALOUS_BOT', '').strip()
WEBSITES_TO_CHECK = os.getenv('WEBSITES_TO_CHECK', '').split(',')
NTRIP_CASTERS_TO_CHECK = os.getenv('NTRIP_CASTERS_TO_CHECK', '').split(',')

# Filter empty strings
TELEGRAM_BOTS_TO_PING = [bot.strip() for bot in TELEGRAM_BOTS_TO_PING if bot.strip()]
IP_ADDRESSES_TO_PING = [ip.strip() for ip in IP_ADDRESSES_TO_PING if ip.strip()]
SYSTEMD_SERVICES = [svc.strip() for svc in SYSTEMD_SERVICES if svc.strip()]
WEBSITES_TO_CHECK = [url.strip() for url in WEBSITES_TO_CHECK if url.strip()]
NTRIP_CASTERS_TO_CHECK = [url.strip() for url in NTRIP_CASTERS_TO_CHECK if url.strip()]


async def check_website(url):
    """Check that a website is reachable.

    Tries a standard HTTP GET first. If the server speaks a non-HTTP protocol
    (e.g. NTRIP casters that reply with 'ICY 200 OK'), falls back to a raw TCP
    connection check so the port-open state is still verified.
    """
    from urllib.parse import urlparse
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=PING_TIMEOUT), allow_redirects=True) as resp:
                return resp.status < 400
    except aiohttp.ClientResponseError as e:
        return e.status < 400
    except Exception:
        # Non-HTTP protocol (e.g. NTRIP/ICY) — fall back to TCP reachability
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=PING_TIMEOUT
            )
            writer.close()
            await writer.wait_closed()
            return True
        except Exception as e:
            print(f"Error checking website {url}: {e}")
            return False


async def check_ntrip_caster(url):
    """Check an NTRIP caster by requesting its sourcetable.

    If the URL includes a mountpoint path (e.g. http://host:2101/MYMOUNT),
    also verifies that mountpoint appears in the sourcetable.
    """
    from urllib.parse import urlparse
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or 2101
    mountpoint = parsed.path.strip('/')

    request = (
        f"GET / HTTP/1.0\r\n"
        f"Host: {host}:{port}\r\n"
        f"Ntrip-Version: Ntrip/2.0\r\n"
        f"User-Agent: NTRIP WatcherClient/1.0\r\n"
        f"\r\n"
    )

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=PING_TIMEOUT
        )
        writer.write(request.encode())
        await writer.drain()
        # Read until ENDSOURCETABLE or connection closes (sourcetable can be very large)
        chunks = []
        while True:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=PING_TIMEOUT)
            if not chunk:
                break
            chunks.append(chunk)
            if b'ENDSOURCETABLE' in chunk:
                break
        response = b''.join(chunks)
        writer.close()
        await writer.wait_closed()

        text = response.decode(errors='ignore')
        first_line = text.split('\r\n')[0].upper()
        caster_up = 'SOURCETABLE 200' in first_line or 'ICY 200' in first_line or 'HTTP/1' in first_line
        if not caster_up:
            return False

        if mountpoint:
            # Each stream entry starts with STR;MOUNTPOINT;
            mountpoints = {
                line.split(';')[1]
                for line in text.splitlines()
                if line.startswith('STR;') and len(line.split(';')) > 1
            }
            if mountpoint not in mountpoints:
                print(f"  Mountpoint '{mountpoint}' not found in sourcetable (available: {', '.join(sorted(mountpoints)) or 'none'})")
                return False

        return True
    except Exception as e:
        print(f"Error checking NTRIP caster {url}: {e}")
        return False


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


async def check_systemd_service(service):
    """Check if a systemd service is active."""
    try:
        result = subprocess.run(
            ['systemctl', 'is-active', service],
            capture_output=True,
            text=True
        )
        return result.stdout.strip() == 'active'
    except Exception as e:
        print(f"Error checking systemd service {service}: {e}")
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
        except asyncio.TimeoutError:
            # Delete just our ping
            await client.delete_messages(username, [sent_msg.id])
            return False

        # For bots that require an extra /checkbell verification
        if ANOMALOUS_BOT and username.lstrip('@').lower() == ANOMALOUS_BOT.lstrip('@').lower():
            return await check_bell(client, username)

        return True
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


async def check_bell(client, username):
    """Send /checkbell to a bot and verify it responds with 'bell checked'."""
    bell_response_received = asyncio.Event()
    bell_ok = False
    bell_msg_ids = []

    @client.on(events.NewMessage(from_users=username))
    async def bell_handler(event):
        nonlocal bell_ok
        text = event.message.text.strip().lower()
        if text in ("bell checked", "bell check failed"):
            bell_ok = text == "bell checked"
            bell_msg_ids.append(event.message.id)
            bell_response_received.set()

    sent_msg = None
    try:
        sent_msg = await client.send_message(username, "/checkbell")
        try:
            await asyncio.wait_for(bell_response_received.wait(), timeout=TELEGRAM_RESPONSE_TIMEOUT)
        except asyncio.TimeoutError:
            bell_ok = False
        finally:
            ids_to_delete = []
            if sent_msg:
                ids_to_delete.append(sent_msg.id)
            ids_to_delete.extend(bell_msg_ids)
            if ids_to_delete:
                await client.delete_messages(username, ids_to_delete)
        return bell_ok
    except Exception as e:
        print(f"Error running /checkbell on {username}: {e}")
        if sent_msg:
            try:
                await client.delete_messages(username, [sent_msg.id])
            except Exception:
                pass
        return False
    finally:
        client.remove_event_handler(bell_handler)


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

    # Check NTRIP casters
    for url in NTRIP_CASTERS_TO_CHECK:
        print(f"Checking NTRIP caster: {url}")
        if not await check_ntrip_caster(url):
            failures.append(f"NTRIP caster down: `{url}`")
            print(f"  FAILED")
        else:
            print(f"  OK")

    # Check websites
    for url in WEBSITES_TO_CHECK:
        print(f"Checking website: {url}")
        if not await check_website(url):
            failures.append(f"Website down: `{url}`")
            print(f"  FAILED")
        else:
            print(f"  OK")

    # Check systemd services
    for service in SYSTEMD_SERVICES:
        print(f"Checking systemd service: {service}")
        if not await check_systemd_service(service):
            failures.append(f"Systemd service not active: `{service}`")
            print(f"  FAILED")
        else:
            print(f"  OK")

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
    print(f"Monitoring {len(NTRIP_CASTERS_TO_CHECK)} NTRIP caster(s), {len(WEBSITES_TO_CHECK)} website(s), {len(IP_ADDRESSES_TO_PING)} IP/domain(s), {len(TELEGRAM_BOTS_TO_PING)} Telegram bot(s), and {len(SYSTEMD_SERVICES)} systemd service(s)")
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
