"""Entry point: run the Carcará Telegram bot.

Usage:
    uv run python -m telegram_bot [--debug]

Configuration is read from a `.env` file in the project root (or from
already-exported environment variables).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from telegram_bot import __version__
from telegram_bot.bot import build_application
from telegram_bot.config import load_bot_config

# Load .env from the project root (telegram_bot/.env).
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="telegram_bot", description="Carcará Telegram bot")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose (DEBUG) logging, including HTTP request logs.",
    )
    args = parser.parse_args(argv)

    level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger("telegram_bot")
    logger.info("Carcará Telegram bot v%s starting... (debug=%s)", __version__, args.debug)

    # Suppress noisy HTTP request logs (one line per getUpdates) unless debugging.
    if args.debug:
        # Surface detailed HTTP traffic from httpx (Telegram API + Carcará).
        logging.getLogger("httpx").setLevel(logging.DEBUG)
        logging.getLogger("httpcore").setLevel(logging.DEBUG)
        # Make kimi_cli's loguru logger verbose too.
        try:
            from loguru import logger as loguru_logger

            loguru_logger.remove()
            loguru_logger.add(sys.stderr, level="DEBUG")
        except Exception:
            pass
    else:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("telegram.ext").setLevel(logging.WARNING)

    try:
        config = load_bot_config()
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1

    app = build_application(config)

    try:
        app.run_polling()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down...")
    return 0


if __name__ == "__main__":
    sys.exit(main())