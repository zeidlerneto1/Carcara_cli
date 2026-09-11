#!/bin/bash
# Inicia o Carcará Telegram bot em background (sem matar processos existentes).
set -e
cd "$(dirname "$0")"
mkdir -p logs
setsid nohup uv run python -m telegram_bot >> logs/bot.log 2>&1 < /dev/null &
echo "Bot iniciado (PID $!)"
