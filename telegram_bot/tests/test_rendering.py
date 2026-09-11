"""Unit tests for the rendering module."""

from __future__ import annotations

from kosong.message import TextPart, ToolCall
from kosong.tooling import BriefDisplayBlock, ToolResult, ToolReturnValue

from telegram_bot import rendering


def test_content_parts_to_text():
    parts = [TextPart(text="Hello "), TextPart(text="world")]
    assert rendering.content_parts_to_text(parts) == "Hello world"


def test_tool_call_to_text_hint():
    call = ToolCall(
        id="1",
        function=ToolCall.FunctionBody(name="Shell", arguments='{"command": "cat foo.py"}'),
    )
    text = rendering.tool_call_to_text(call)
    assert "Shell" in text
    assert "cat foo.py" in text


def test_tool_result_to_text_error():
    rv = ToolReturnValue(is_error=True, output="", message="Command failed", display=[])
    result = ToolResult(tool_call_id="1", return_value=rv)
    text = rendering.tool_result_to_text(result)
    assert "Command failed" in text
    assert "❌" in text


def test_tool_result_displays():
    rv = ToolReturnValue(
        is_error=False,
        output="",
        message="",
        display=[BriefDisplayBlock(text="done")],
    )
    result = ToolResult(tool_call_id="1", return_value=rv)
    rendered = rendering.tool_result_displays(result)
    assert rendered == ["<pre>done</pre>"]


def test_tool_result_displays_long_block_splits():
    """A very long display block must be split into multiple <pre> chunks."""
    long_text = "x" * 5000
    rv = ToolReturnValue(
        is_error=False,
        output="",
        message="",
        display=[BriefDisplayBlock(text=long_text)],
    )
    result = ToolResult(tool_call_id="1", return_value=rv)
    rendered = rendering.tool_result_displays(result)
    assert len(rendered) > 1
    # Each rendered chunk (including <pre> tags) must be under the Telegram limit.
    for chunk in rendered:
        assert len(chunk) <= rendering.TELEGRAM_MAX_LEN


def test_split_message_single():
    text = "short"
    assert rendering.split_message(text, max_chars=4000) == [text]


def test_split_message_long():
    text = "A" * 100
    parts = rendering.split_message(text, max_chars=40)
    assert len(parts) > 1
    assert sum(len(p) for p in parts) == 100
