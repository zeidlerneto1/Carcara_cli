from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from kosong.message import Message, TextPart, ThinkPart

from kimi_cli.notifications import is_notification_message

if TYPE_CHECKING:
    from kimi_cli.soul.kimisoul import KimiSoul


@dataclass(frozen=True, slots=True)
class DynamicInjection:
    """A dynamic prompt content to be injected before an LLM step."""

    type: str  # identifier, e.g. "plan_mode"
    content: str  # text content (will be wrapped in <system-reminder> tags)


class DynamicInjectionProvider(ABC):
    """Base class for dynamic injection providers.

    Called before each LLM step. Implementations handle their own throttling.
    Providers can access all runtime state via the ``soul`` parameter
    (context_usage, runtime, config, etc.).
    """

    @abstractmethod
    async def get_injections(
        self,
        history: Sequence[Message],
        soul: KimiSoul,
    ) -> list[DynamicInjection]: ...

    async def on_context_compacted(self) -> None:
        """Called after the context is compacted (history is rebuilt).

        Override to reset internal throttling state when prior injections
        may have been collapsed into the compaction summary and are no
        longer literally present in history. Default is a no-op.
        """
        return None

    async def on_afk_changed(self, enabled: bool) -> None:
        """Called when afk mode is toggled at runtime.

        Override to reset internal throttling state when a mode-specific
        reminder should be eligible to fire again after a user toggle.
        """
        _ = enabled
        return None


def _has_meaningful_content(msg: Message) -> bool:
    """Whether a message carries something the model can continue from.

    An assistant message whose only content is a ``ThinkPart`` (reasoning)
    or whose text is empty has no visible payload: on the wire it becomes
    ``content=None`` or ``content=""`` and servers that enforce
    ``Last message must be from user or tool.`` reject the request.
    """
    if msg.tool_calls:
        return True
    for part in msg.content:
        if isinstance(part, ThinkPart):
            continue
        if isinstance(part, TextPart) and not part.text.strip():
            continue
        return True
    return False


def sanitize_history(history: Sequence[Message]) -> list[Message]:
    """Drop trailing messages that cannot legally end an LLM request.

    When a stream is interrupted right after the model finishes thinking
    (or the model answers with reasoning only), the persisted history can
    end with an assistant message that carries no visible content and no
    tool calls.  Servers such as Carcará reject the next request with
    ``400 {"error":"Last message must be from user or tool."}``.

    This walks the tail of the history and removes:
      * assistant messages with no meaningful content and no tool calls;
      * tool messages whose tool_call_id no longer matches any preceding
        assistant tool call (orphaned after dropping an assistant message);
      * user messages that consist solely of empty text (empty injections).

    This is the domain-layer defense. The Carcará provider also applies a
    wire-layer repair in ``_serialize_history`` (see
    ``kosong.contrib.chat_provider.carcara``), so the two are intentionally
    redundant: this guard fixes the history before any provider sees it, while
    the provider guard protects any caller of ``CarcaraProvider``.

    Returns a new list; the input is not mutated.
    """
    if not history:
        return []

    result = list(history)
    while result:
        last = result[-1]
        if last.role == "assistant":
            if not _has_meaningful_content(last):
                result.pop()
                continue
        elif last.role == "tool":
            # Check the tool_call_id is still referenced by a preceding assistant message.
            referenced = any(
                tc.id == last.tool_call_id
                for msg in result
                if msg.role == "assistant"
                for tc in (msg.tool_calls or [])
            )
            if not referenced:
                result.pop()
                continue
        elif last.role == "user":
            texts = [part for part in last.content if isinstance(part, TextPart)]
            if not last.content or (texts and not any(t.text.strip() for t in texts)):
                result.pop()
                continue
        break
    return result


def normalize_history(history: Sequence[Message]) -> list[Message]:
    """Merge adjacent user messages to produce a clean API input sequence.

    Dynamic injections are stored as standalone user messages in history;
    normalization merges them into the adjacent user message.

    Only ``user`` role messages are merged. Assistant and tool messages
    are never merged because their ``tool_calls`` / ``tool_call_id``
    fields form linked pairs that must stay intact.
    """
    if not history:
        return []

    result: list[Message] = []
    for msg in history:
        if (
            result
            and result[-1].role == msg.role
            and msg.role == "user"
            and not is_notification_message(result[-1])
            and not is_notification_message(msg)
        ):
            merged_content = list(result[-1].content) + list(msg.content)
            result[-1] = Message(role="user", content=merged_content)
        else:
            result.append(msg)
    return result
