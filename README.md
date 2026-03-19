Who will watch the watcher?

# Watcher

A monitoring tool that checks IP addresses and Telegram bots, sending alerts via Telegram when failures are detected.

## Features

- Ping IP addresses and domains to verify connectivity
- Send ping messages to Telegram bots and verify responses
- Automatic notifications to a designated Telegram chat on failure
- Configurable check intervals and timeouts

## Requirements

- Python 3.7+
- Telegram bot token

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

Create a `.env` file with the following variables:

```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
NOTIFICATION_CHAT_ID=your_chat_id_here
TELEGRAM_BOTS_TO_PING=bot_chat_id_1,bot_chat_id_2
IP_ADDRESSES_TO_PING=8.8.8.8,example.com
CHECK_INTERVAL_HOURS=24
PING_TIMEOUT=5
TELEGRAM_RESPONSE_TIMEOUT=30
```

## Usage

```bash
python watcher.py
```

The script will run continuously, performing checks at the specified interval. Press Ctrl+C to stop.

## License

MIT
