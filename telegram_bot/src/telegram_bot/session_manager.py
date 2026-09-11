"""Manage per-chat KimiCLI instances.

Each Telegram chat owns a :class:`KimiCLI` instance bound to a private work
directory under ``base_dir/<chat_id>/``. Instances are created lazily and
cached; on restart the last session for a user is resumed so context is kept.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from kaos.path import KaosPath

from kimi_cli.app import KimiCLI
from kimi_cli.session import Session

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

    from telegram_bot.config import BotConfig

logger = logging.getLogger(__name__)


class UserSession:
    """Holds a KimiCLI instance (and its underlying Session) for one chat."""

    def __init__(self, cli: KimiCLI, session: Session, *, yolo: bool) -> None:
        self.cli = cli
        self.session = session
        self.yolo = yolo
        self._run_lock = asyncio.Lock()
        self._running = False

    @property
    def busy(self) -> bool:
        return self._running

    @property
    def approval_runtime(self):
        """Expose the approval runtime from the underlying KimiCLI."""
        print("[DEBUG-SESSION] approval_runtime property called")
        runtime = getattr(self.cli, "_runtime", None)
        print("[DEBUG-SESSION] self.cli._runtime:", runtime)
        if runtime is None:
            print("[DEBUG-SESSION] _runtime is None, returning None")
            return None
        approval_runtime = runtime.approval_runtime
        print("[DEBUG-SESSION] runtime.approval_runtime:", approval_runtime)
        return approval_runtime

    async def run(
        self,
        user_input: str,
        cancel_event: asyncio.Event,
    ):
        """Yield WireMessages from running ``user_input`` through this chat's CLI."""
        async with self._run_lock:
            self._running = True
            try:
                async for msg in self.cli.run(user_input, cancel_event, merge_wire_messages=True):
                    yield msg
            finally:
                self._running = False


class SessionManager:
    """Maps chat IDs to :class:`UserSession` instances."""

    def __init__(self, config: BotConfig) -> None:
        self._config = config
        self._users: dict[int, UserSession] = {}

    def get_user(self, chat_id: int) -> UserSession | None:
        return self._users.get(chat_id)

    async def _create(self, chat_id: int, *, yolo: bool) -> UserSession:
        work_dir = KaosPath.unsafe_from_local_path(self._config.base_dir / str(chat_id))
        await work_dir.canonical().mkdir(parents=True, exist_ok=True)

        # Resume the last session for this user if it exists, else create one.
        session = await Session.continue_(work_dir)
        if session is None:
            session = await Session.create(work_dir)

        cli = await KimiCLI.create(
            session,
            config=self._config.get_carcara_config(),
            model_name=self._config.model_name,
            runtime_afk=yolo,
            ui_mode="print",
        )
        user = UserSession(cli, session, yolo=yolo)
        self._users[chat_id] = user
        logger.info("Created user session for chat %s (session %s)", chat_id, session.id)
        return user

    async def get_or_create(self, chat_id: int, *, yolo: bool) -> UserSession:
        existing = self._users.get(chat_id)
        if existing is not None:
            # If approval mode changed, recreate with the new yolo flag.
            if existing.yolo != yolo:
                await self._recreate(chat_id, yolo=yolo)
                return self._users[chat_id]
            return existing
        return await self._create(chat_id, yolo=yolo)

    async def _recreate(self, chat_id: int, *, yolo: bool) -> None:
        current = self._users.pop(chat_id, None)
        if current is not None:
            logger.info("Recreating user session for chat %s (yolo=%s)", chat_id, yolo)
        await self._create(chat_id, yolo=yolo)

    async def new_session(self, chat_id: int) -> UserSession:
        """Start a fresh session for the user (discarding current context)."""
        current = self._users.pop(chat_id, None)
        if current is not None:
            with contextlib.suppress(Exception):
                await current.session.delete()
        work_dir = KaosPath.unsafe_from_local_path(self._config.base_dir / str(chat_id))
        await work_dir.canonical().mkdir(parents=True, exist_ok=True)
        session = await Session.create(work_dir)
        cli = await KimiCLI.create(
            session,
            config=self._config.get_carcara_config(),
            model_name=self._config.model_name,
            runtime_afk=bool(current and current.yolo),
            ui_mode="print",
        )
        user = UserSession(cli, session, yolo=bool(current and current.yolo))
        self._users[chat_id] = user
        logger.info("Started new session for chat %s (session %s)", chat_id, session.id)
        return user

    async def shutdown(self) -> None:
        for chat_id, user in list(self._users.items()):
            try:
                user.session.save_state()
            except Exception:  # pragma: no cover - defensive
                logger.exception("Failed to save state for chat %s", chat_id)
        self._users.clear()


async def get_user_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Helper to fetch the active user session from bot context."""
    manager: SessionManager = context.bot_data["session_manager"]
    yolo: bool = context.bot_data.get("yolo_users", {}).get(update.effective_chat.id, False)
    return await manager.get_or_create(update.effective_chat.id, yolo=yolo)


__all__ = ["SessionManager", "UserSession", "get_user_session"]
