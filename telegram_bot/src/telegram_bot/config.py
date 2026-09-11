"""Bot configuration: Telegram token, base working directory, Carcará config.

Configuration is read from environment variables with sensible defaults so the
bot can be run with minimal setup.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from kimi_cli.config import Config, load_config

#: Hard upper bound (seconds) for the approval/question action timeout.
MAX_ACTION_TIMEOUT: float = 30.0


@dataclass
class BotConfig:
    """Resolved configuration for the bot."""

    #: Telegram bot token (from @BotFather).
    token: str
    #: Base directory under which each Telegram user gets their own work dir.
    base_dir: Path
    #: Path to a Carcará config.toml, or None to use the default (~/.kimi/config.toml).
    carcara_config_file: Path | None
    #: Optional model name override.
    model_name: str | None = None
    #: Whether thinking output should be shown in the chat.
    show_thinking: bool = False
    #: Default approval mode for new users: "inline" or "yolo".
    default_approval: str = "inline"
    #: Telegram user IDs allowed to run privileged commands (e.g. /restart).
    admin_ids: list[int] = field(default_factory=list)
    #: Seconds to wait for an approval before auto-rejecting (0 = never, capped at 30).
    approval_timeout: float = 30.0
    #: How often (seconds) to refresh the streaming message while a turn runs.
    stream_refresh_interval: float = 0.6
    #: Max characters before a streaming message is flushed to a new one.
    stream_max_chars: int = 3500

    def get_carcara_config(self) -> Config:
        """Load the Carcará config to use when creating KimiCLI instances."""
        if self.carcara_config_file is not None:
            return load_config(self.carcara_config_file)
        return load_config(None)


def _env_int_list(name: str) -> list[int]:
    """Parse a comma-separated env var into a list of ints (empty if unset)."""
    raw = os.getenv(name)
    if not raw:
        return []
    return [int(part.strip()) for part in raw.split(",") if part.strip()]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_bot_config() -> BotConfig:
    """Build a :class:`BotConfig` from environment variables."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required. Set it to the token from @BotFather.")

    base_dir = Path(os.getenv("CARCARA_TG_BASE_DIR", "~/.carcara_telegram")).expanduser()

    config_file_raw = os.getenv("CARCARA_CONFIG_FILE")
    carcara_config_file = Path(config_file_raw).expanduser() if config_file_raw else None

    return BotConfig(
        token=token,
        base_dir=base_dir,
        carcara_config_file=carcara_config_file,
        model_name=os.getenv("CARCARA_MODEL") or None,
        show_thinking=_env_bool("CARCARA_SHOW_THINKING", False),
        default_approval=os.getenv("CARCARA_DEFAULT_APPROVAL", "inline").strip().lower(),
        admin_ids=_env_int_list("CARCARA_ADMIN_IDS"),
        stream_refresh_interval=float(os.getenv("CARCARA_STREAM_REFRESH", "0.6")),
        stream_max_chars=int(os.getenv("CARCARA_STREAM_MAX_CHARS", "3500")),
        approval_timeout=min(
            float(os.getenv("CARCARA_APPROVAL_TIMEOUT", "30")),
            MAX_ACTION_TIMEOUT,
        ),
    )


__all__ = ["BotConfig", "load_bot_config"]
