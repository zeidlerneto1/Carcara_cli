"""Handle QuestionRequest WireMessages via Telegram inline buttons.

A QuestionRequest carries 1-4 QuestionItems, each with 2-4 options plus a
synthetic "Other" option. The bot renders them as a Telegram message with an
inline keyboard; multi-select questions allow toggling before confirming.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from kimi_cli.wire.types import QuestionItem, QuestionRequest

if TYPE_CHECKING:
    from telegram import Bot

logger = logging.getLogger(__name__)

PREFIX = "qst"

# Actions for question callbacks
_ACT_SELECT = "s"  # select/toggle an option
_ACT_CONFIRM = "c"  # confirm multi-select
_ACT_OTHER = "o"  # trigger free-text "Other"


@dataclass
class _QuestionState:
    request_id: str
    request: QuestionRequest
    items: list[QuestionItem]
    message_ids: list[int] = field(default_factory=list)
    # For multi-select: item_index -> set of option indexes
    selections: dict[int, set[int]] = field(default_factory=dict)
    # Free-text answers keyed by item index (for "Other")
    free_text: dict[int, str] = field(default_factory=dict)
    # item_index waiting for free-text reply
    awaiting_free_text: dict[int, bool] = field(default_factory=dict)


def _callback(request_id: str, item_idx: int, opt_idx: int, action: str) -> str:
    return f"{PREFIX}:{request_id}:{item_idx}:{opt_idx}:{action}"


def parse_callback(callback_data: str) -> tuple[str, int, int, str] | None:
    """Parse a question callback into (request_id, item_idx, opt_idx, action)."""
    if not callback_data.startswith(PREFIX + ":"):
        return None
    parts = callback_data.split(":")
    if len(parts) != 5:
        return None
    _, request_id, item_idx, opt_idx, action = parts
    try:
        return request_id, int(item_idx), int(opt_idx), action
    except ValueError:
        return None


def _item_text(item: QuestionItem, idx: int) -> str:
    header = f"[{html.escape(item.header)}] " if item.header else ""
    multi = " (múltipla escolha)" if item.multi_select else ""
    return f"{idx + 1}. {header}{html.escape(item.question)}{multi}"


class QuestionTracker:
    """Tracks pending question requests so callbacks can resolve them."""

    def __init__(self, timeout_s: float = 300.0) -> None:
        self._pending: dict[str, _QuestionState] = {}
        # chat_id -> request_id currently awaiting free text
        self._chat_free_text: dict[int, str] = {}
        self.timeout_s = timeout_s
        # request_id -> timeout task
        self._timeout_tasks: dict[str, asyncio.Task] = {}

    async def _auto_dismiss(self, request_id: str) -> None:
        await asyncio.sleep(self.timeout_s)
        state = self._pending.get(request_id)
        if state is not None and not state.request.resolved:
            logger.warning("Question %s timed out, dismissing", request_id)
            self._resolve_all(state, {})  # empty answers = dismissed
        self._timeout_tasks.pop(request_id, None)

    async def handle(self, bot: Bot, chat_id: int, request: QuestionRequest) -> None:
        state = _QuestionState(request_id=request.id, request=request, items=request.questions)
        self._pending[request.id] = state
        if self.timeout_s > 0:
            self._timeout_tasks[request.id] = asyncio.create_task(self._auto_dismiss(request.id))

        for idx, item in enumerate(request.questions):
            lines = [f"❓ <b>{_item_text(item, idx)}</b>"]
            if item.body:
                lines.append(html.escape(item.body))
            text = "\n".join(lines)

            buttons: list[list[InlineKeyboardButton]] = [
                [
                    InlineKeyboardButton(
                        html.escape(opt.label),
                        callback_data=_callback(request.id, idx, opt_idx, _ACT_SELECT),
                    )
                ]
                for opt_idx, opt in enumerate(item.options)
            ]
            buttons.append(
                [
                    InlineKeyboardButton(
                        "✍️ Outra resposta",
                        callback_data=_callback(request.id, idx, 0, _ACT_OTHER),
                    )
                ]
            )
            if item.multi_select:
                buttons.append(
                    [
                        InlineKeyboardButton(
                            "✅ Confirmar",
                            callback_data=_callback(request.id, idx, 0, _ACT_CONFIRM),
                        )
                    ]
                )

            msg = await bot.send_message(
                chat_id, text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML"
            )
            state.message_ids.append(msg.message_id)
        logger.info("Question asked for chat %s: %s", chat_id, request.id)

    def _get_state(self, request_id: str) -> _QuestionState | None:
        return self._pending.get(request_id)

    def _item_keyboard(self, state: _QuestionState, item_idx: int):
        item = state.items[item_idx]
        sel = state.selections.get(item_idx, set())
        buttons: list[list[InlineKeyboardButton]] = [
            [
                InlineKeyboardButton(
                    ("✅ " if oi in sel else "") + html.escape(opt.label),
                    callback_data=_callback(state.request_id, item_idx, oi, _ACT_SELECT),
                )
            ]
            for oi, opt in enumerate(item.options)
        ]
        buttons.append(
            [
                InlineKeyboardButton(
                    "✍️ Outra resposta",
                    callback_data=_callback(state.request_id, item_idx, 0, _ACT_OTHER),
                )
            ]
        )
        if item.multi_select:
            buttons.append(
                [
                    InlineKeyboardButton(
                        "✅ Confirmar",
                        callback_data=_callback(state.request_id, item_idx, 0, _ACT_CONFIRM),
                    )
                ]
            )
        return InlineKeyboardMarkup(buttons)

    async def on_select(
        self, bot: Bot, chat_id: int, request_id: str, item_idx: int, opt_idx: int
    ) -> None:
        state = self._get_state(request_id)
        if state is None or item_idx >= len(state.items):
            return
        item = state.items[item_idx]
        if item.multi_select:
            sel = state.selections.setdefault(item_idx, set())
            if opt_idx in sel:
                sel.discard(opt_idx)
            else:
                sel.add(opt_idx)
            await self._update_multi_message(bot, chat_id, state, item_idx)
        else:
            option = item.options[opt_idx]
            self._resolve_item(state, item_idx, option.label)
            await self._cleanup(bot, chat_id, state)

    async def on_confirm(self, bot: Bot, chat_id: int, request_id: str, item_idx: int) -> None:
        state = self._get_state(request_id)
        if state is None or item_idx >= len(state.items):
            return
        sel = state.selections.get(item_idx, set())
        if not sel:
            await bot.send_message(chat_id, "Selecione pelo menos uma opção.")
            return
        item = state.items[item_idx]
        labels = [item.options[i].label for i in sorted(sel)]
        self._resolve_item(state, item_idx, ", ".join(labels))
        await self._cleanup(bot, chat_id, state)

    async def on_other(self, bot: Bot, chat_id: int, request_id: str, item_idx: int) -> None:
        state = self._get_state(request_id)
        if state is None or item_idx >= len(state.items):
            return
        state.awaiting_free_text[item_idx] = True
        self.mark_chat_free_text(chat_id, request_id)
        await bot.send_message(
            chat_id,
            "✍️ Digite sua resposta livre para esta pergunta (ou /cancelar para sair):",
        )

    async def on_free_text(self, bot: Bot, chat_id: int, request_id: str, text: str) -> None:
        state = self._get_state(request_id)
        if state is None:
            self.clear_chat_free_text(chat_id)
            return
        # Find the item awaiting free text.
        item_idx = next((i for i, v in state.awaiting_free_text.items() if v), None)
        if item_idx is None:
            return
        state.awaiting_free_text.pop(item_idx, None)
        self._resolve_item(state, item_idx, text.strip())
        self.clear_chat_free_text(chat_id)
        await self._cleanup(bot, chat_id, state)

    def _resolve_item(self, state: _QuestionState, item_idx: int, answer: str) -> None:
        state.free_text[item_idx] = answer
        self._try_resolve(state)

    def _try_resolve(self, state: _QuestionState) -> None:
        if len(state.free_text) < len(state.items):
            return
        answers: dict[str, str] = {}
        for idx, item in enumerate(state.items):
            answers[item.question] = state.free_text.get(idx, "")
        self._resolve_all(state, answers)

    def _resolve_all(self, state: _QuestionState, answers: dict[str, str]) -> None:
        request = state.request
        if not request.resolved:
            request.resolve(answers)
        self._pending.pop(state.request_id, None)
        timeout_task = self._timeout_tasks.pop(state.request_id, None)
        if timeout_task is not None:
            timeout_task.cancel()

    async def _update_multi_message(
        self, bot: Bot, chat_id: int, state: _QuestionState, item_idx: int
    ) -> None:
        item = state.items[item_idx]
        sel = state.selections.get(item_idx, set())
        lines = [f"❓ <b>{_item_text(item, item_idx)}</b>"]
        for opt_idx, opt in enumerate(item.options):
            marker = "✅ " if opt_idx in sel else ""
            lines.append(f"{marker}{html.escape(opt.label)}")
        lines.append("\n<i>Selecione as opções e confirme.</i>")
        text = "\n".join(lines)
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=state.message_ids[item_idx],
                text=text,
                reply_markup=self._item_keyboard(state, item_idx),
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Failed to update multi-select message")

    async def _cleanup(self, bot: Bot, chat_id: int, state: _QuestionState) -> None:
        for msg_id in state.message_ids:
            with contextlib.suppress(Exception):
                await bot.delete_message(chat_id=chat_id, message_id=msg_id)

    def is_awaiting_free_text(self, request_id: str) -> bool:
        state = self._get_state(request_id)
        return bool(state and state.awaiting_free_text)

    # --- per-chat free-text routing ---
    def mark_chat_free_text(self, chat_id: int, request_id: str) -> None:
        self._chat_free_text[chat_id] = request_id

    def is_awaiting_free_text_for_chat(self, chat_id: int) -> bool:
        return chat_id in self._chat_free_text

    def free_text_request_for_chat(self, chat_id: int) -> str | None:
        return self._chat_free_text.get(chat_id)

    def clear_chat_free_text(self, chat_id: int) -> None:
        self._chat_free_text.pop(chat_id, None)


__all__ = ["QuestionTracker", "parse_callback", "PREFIX"]
