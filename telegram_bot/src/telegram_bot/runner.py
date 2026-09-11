"""Run a KimiCLI turn and translate WireMessages into Telegram actions.

The runner consumes the async generator from :meth:`UserSession.run` and
drives the Telegram UI: streaming text edits, tool status lines, approval
buttons and interactive questions.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
from typing import TYPE_CHECKING

from kimi_cli.wire.types import (
    ApprovalRequest,
    ApprovalResponse,
    ContentPart,
    Notification,
    PlanDisplay,
    QuestionRequest,
    StepBegin,
    StepInterrupted,
    StepRetry,
    ThinkPart,
    ToolCall,
    ToolCallPart,
    ToolResult,
    TurnBegin,
    TurnEnd,
    WireMessage,
)
from telegram.error import BadRequest
from telegram_bot import rendering
from telegram_bot.approvals import ApprovalTracker
from telegram_bot.questions import QuestionTracker

if TYPE_CHECKING:
    from telegram import Bot, Message

    from telegram_bot.config import BotConfig
    from telegram_bot.session_manager import UserSession

logger = logging.getLogger(__name__)


class _ContentBuffer:
    """Accumulates ContentParts across a step, mirroring FinalOnlyTextPrinter.

    Thinking parts (ThinkPart) are kept separately and only rendered when the
    bot config has ``show_thinking`` enabled.
    """

    def __init__(self) -> None:
        self.parts: list[ContentPart] = []
        self.thinking: list[ContentPart] = []

    def add(self, part: ContentPart) -> None:
        if isinstance(part, ThinkPart):
            self.thinking.append(part)
            return
        if self.parts and self.parts[-1].merge_in_place(part):
            return
        self.parts.append(part)

    def clear(self) -> None:
        self.parts.clear()
        self.thinking.clear()

    def text(self, *, include_thinking: bool = False) -> str:
        # Escape all plain-text content so it is safe to send with parse_mode="HTML".
        text = html.escape(rendering.content_parts_to_text(self.parts), quote=False)
        if include_thinking and self.thinking:
            think_text = html.escape(
                rendering.content_parts_to_text(self.thinking), quote=False
            )
            if think_text:
                think_block = f"💭 <i>{think_text}</i>"
                text = f"{think_block}\n\n{text}" if text else think_block
        return text

    def __bool__(self) -> bool:
        return bool(self.parts)


class TurnRunner:
    """Runs a single user turn through the agent, streaming to Telegram."""

    def __init__(
        self,
        bot: Bot,
        chat_id: int,
        user_session: UserSession,
        config: BotConfig,
        approvals: ApprovalTracker,
        questions: QuestionTracker,
    ) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.user_session = user_session
        self.config = config
        self.approvals = approvals
        self.questions = questions

        self._buffer = _ContentBuffer()
        self._stream_msg: Message | None = None
        self._stream_text = ""
        self._last_flush = 0.0
        self._cancel_event = asyncio.Event()

    @property
    def cancel_event(self) -> asyncio.Event:
        return self._cancel_event

    async def run(self, user_input: str) -> str:
        """Run the turn and return a summary/error string."""
        status_msg = await self.bot.send_message(self.chat_id, "🤖 processando...")
        try:
            await self._consume(self.user_session.run(user_input, self._cancel_event))
        except asyncio.CancelledError:
            await self._finish_stream(final=True)
            return "⏹️ Turno cancelado."
        except Exception as exc:
            logger.exception("Turn failed for chat %s", self.chat_id)
            await self._finish_stream(final=True)
            return f"❌ Erro: {_escape_type(exc)}"
        finally:
            await self._safe_delete(status_msg)
            await self._finish_stream(final=True)
        return "✅ Concluído."

    async def _consume(self, stream) -> None:
        async for msg in stream:
            await self._handle(msg)

    async def _send(self, text: str) -> None:
        """Send a text message, splitting if needed to stay under Telegram's 4096 limit."""
        for chunk in rendering.split_message(text, max_chars=self.config.stream_max_chars):
            try:
                await self.bot.send_message(self.chat_id, chunk, parse_mode="HTML")
            except BadRequest:
                # HTML parse error or other 400 — retry as plain text.
                logger.warning("HTML send failed, retrying as plain text")
                await self.bot.send_message(self.chat_id, html.unescape(chunk))

    async def _handle(self, msg: WireMessage) -> None:
        match msg:
            case TurnBegin():
                pass
            case TurnEnd():
                pass
            case StepBegin():
                self._buffer.clear()
                await self._flush(force=True)
            case StepInterrupted():
                self._buffer.clear()
                await self._flush(force=True)
            case StepRetry():
                self._buffer.clear()
                await self._flush(force=True)
                await self._send(
                    f"⚠️ Tentativa {msg.next_attempt}/{msg.max_attempts} "
                    f"(erro: {_escape_type(msg.error_type)})"
                )
            case ContentPart():
                self._buffer.add(msg)
                await self._maybe_stream()
            case ToolCall():
                await self._flush(force=True)
                await self._send(rendering.tool_call_to_text(msg))
            case ToolCallPart():
                pass
            case ToolResult():
                await self._flush(force=True)
                await self._send(rendering.tool_result_to_text(msg))
                for text in rendering.tool_result_displays(msg):
                    await self._send(text)
            case PlanDisplay():
                await self._flush(force=True)
                await self._send(rendering.plan_to_text(msg))
            case Notification():
                await self._flush(force=True)
                await self._send(f"🔔 {html.escape(msg.body)}")
            case ApprovalRequest():
                await self._flush(force=True)
                # handle() sends the inline buttons and returns immediately. The
                # request is resolved asynchronously via callback; the agent
                # (kimi_cli) blocks on ApprovalRequest.wait() internally.
                # Pass the approval_runtime so the tracker can resolve it properly.
                approval_runtime = self.user_session.approval_runtime
                print("[DEBUG-RUNNER] ApprovalRequest received, approval_runtime:", approval_runtime)
                await self.approvals.handle(self.bot, self.chat_id, msg, approval_runtime)
            case QuestionRequest():
                await self._flush(force=True)
                await self.questions.handle(self.bot, self.chat_id, msg)
            case _:
                pass

    async def _maybe_stream(self) -> None:
        now = asyncio.get_event_loop().time()
        if now - self._last_flush < self.config.stream_refresh_interval:
            return
        await self._flush()

    async def _flush(self, force: bool = False) -> None:
        text = self._buffer.text(include_thinking=self.config.show_thinking)
        if not text:
            return
        self._last_flush = asyncio.get_event_loop().time()
        if self._stream_msg is None:
            self._stream_msg = await self._send_message_safe(text)
            self._stream_text = text
            return
        # Skip editing if the text hasn't changed
        if text == self._stream_text:
            return
        # If the accumulated text exceeds the limit, send a new message.
        if len(text) > self.config.stream_max_chars and not force:
            self._stream_msg = await self._send_message_safe(text)
            self._stream_text = text
            return
        try:
            self._stream_msg = await self._stream_msg.edit_text(text, parse_mode="HTML")
            self._stream_text = text
        except BadRequest:
            # HTML parse error — retry as plain text, or send a new message if edit fails.
            logger.warning("HTML edit failed, falling back to plain text")
            try:
                self._stream_msg = await self._stream_msg.edit_text(html.unescape(text))
                self._stream_text = text
            except BadRequest:
                # Edit also failed (e.g. message too long) — send a new message.
                self._stream_msg = await self._send_message_safe(text)
                self._stream_text = text

    async def _send_message_safe(self, text: str) -> Message:
        """Send a text message with HTML, splitting or degrading if needed."""
        for chunk in rendering.split_message(text, max_chars=self.config.stream_max_chars):
            try:
                msg = await self.bot.send_message(self.chat_id, chunk, parse_mode="HTML")
            except BadRequest:
                msg = await self.bot.send_message(self.chat_id, html.unescape(chunk))
        # Return the last message for streaming edits.
        assert msg is not None
        return msg

    async def _finish_stream(self, final: bool) -> None:
        await self._flush(force=True)

    async def _safe_delete(self, msg: Message | None) -> None:
        if msg is None:
            return
        with contextlib.suppress(Exception):
            await msg.delete()


def _escape_type(exc: Exception) -> str:
    name = type(exc).__name__
    msg = str(exc)
    return html.escape(f"{name}: {msg}" if msg else name)


__all__ = ["TurnRunner"]