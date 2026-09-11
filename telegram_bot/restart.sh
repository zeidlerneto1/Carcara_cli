#!/bin/bash
# Reinicia o Carcará Telegram bot em background (desanexado do terminal).
set -e
cd "$(dirname "$0")"
mkdir -p logs
pkill -f "python -m telegram_bot" 2>/dev/null || true
sleep 2
setsid nohup uv run python -m telegram_bot >> logs/bot.log 2>&1 < /dev/null &
echo "Bot iniciado (PID $!)"
