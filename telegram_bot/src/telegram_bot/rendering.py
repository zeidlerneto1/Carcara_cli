"""Render WireMessage content parts into Telegram-friendly markdown.

Telegram uses a limited markdown; we use HTML parse mode for safety with
escaping. Content parts (text/think/tool results) are extracted and merged
into a single display string.
"""

from __future__ import annotations

import html
from collections.abc import Iterable

from kosong.message import TextPart, ThinkPart

from kimi_cli.wire.types import (
    PlanDisplay,
    ToolCall,
    ToolResult,
)


def _escape(text: str) -> str:
    return html.escape(text, quote=False)


def _code_block(text: str, lang: str = "") -> str:
    return f"<pre>{_escape(text)}</pre>"


def part_to_text(part) -> str:
    """Extract displayable text from a ContentPart."""
    if isinstance(part, TextPart):
        return part.text or ""
    if isinstance(part, ThinkPart):
        return part.text or ""
    return ""


def content_parts_to_text(parts: Iterable) -> str:
    """Concatenate a list of ContentParts into plain text."""
    return "".join(part_to_text(p) for p in parts)


def tool_call_to_text(call: ToolCall) -> str:
    """Render a ToolCall as a short status line."""
    function = call.function
    name = function.name if function else "?"
    args = function.arguments if function else ""
    hint = _extract_hint(name, args)
    if hint:
        return f"🛠️ <b>{_escape(name)}</b>: {_escape(hint)}"
    return f"🛠️ <b>{_escape(name)}</b>"


def _extract_hint(name: str, args: str) -> str:
    import json

    try:
        data = json.loads(args) if args else {}
    except Exception:
        return ""
    if isinstance(data, dict):
        for key in ("command", "path", "file", "query", "text", "url"):
            if key in data and isinstance(data[key], str):
                val = data[key].strip().replace("\n", " ")
                if val and len(val) <= 120:
                    return val
    return ""


def tool_result_to_text(result: ToolResult) -> str:
    """Render a ToolResult as a short summary line."""
    rv = result.return_value
    message = rv.message or ""
    is_error = bool(rv.is_error)
    icon = "❌" if is_error else "✅"
    if message:
        return f"{icon} {_escape(message)}"
    return f"{icon} Ferramenta concluída."


# Telegram's hard limit per message (bytes).  We target 4090 to leave room for overhead.
TELEGRAM_MAX_LEN = 4090


def tool_result_displays(result: ToolResult) -> list[str]:
    """Render a ToolResult's display blocks into text snippets.

    Each display block is wrapped in ``<pre>`` and split so no single
    snippet exceeds the Telegram message length limit.
    """
    # len("<pre>") + len("</pre>") = 5 + 6 = 11 bytes of overhead per chunk.
    overhead = 11
    rendered: list[str] = []
    for block in result.return_value.display:
        text = getattr(block, "text", "") or ""
        if text:
            for chunk in split_message(text, max_chars=TELEGRAM_MAX_LEN - overhead):
                rendered.append(_code_block(chunk))
    return rendered


def plan_to_text(plan: PlanDisplay) -> str:
    """Render a PlanDisplay as a Telegram message."""
    return f"📋 <b>Plan</b>\n\n{_escape(plan.content)}"


def split_message(text: str, max_chars: int = 4000) -> list[str]:
    """Split a long text into Telegram-sized chunks on paragraph boundaries."""
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    while len(text) > max_chars:
        cut = text.rfind("\n\n", 0, max_chars)
        if cut == -1:
            cut = text.rfind("\n", 0, max_chars)
        if cut == -1 or cut < max_chars // 2:
            cut = max_chars
        parts.append(text[:cut])
        text = text[cut:]
    if text:
        parts.append(text)
    return parts


__all__ = [
    "part_to_text",
    "content_parts_to_text",
    "tool_call_to_text",
    "tool_result_to_text",
    "tool_result_displays",
    "plan_to_text",
    "split_message",
]
