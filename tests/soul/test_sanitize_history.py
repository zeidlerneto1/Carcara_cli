"""Tests for sanitize_history — repairs history tails that break strict chat APIs.

Regression: a persisted history ending in an assistant message with no visible
content (think-only response from an interrupted stream, or empty text) caused
the next request to be rejected by Carcará with:
    400 {"error":"Last message must be from user or tool."}
"""

from __future__ import annotations

from kosong.message import Message, TextPart, ThinkPart, ToolCall

from kimi_cli.soul.dynamic_injection import sanitize_history


def _assistant_with_tool_calls() -> Message:
    return Message(
        role="assistant",
        content=[],
        tool_calls=[
            ToolCall(
                id="call_1",
                function=ToolCall.FunctionBody(name="shell", arguments='{"command": "ls"}'),
            )
        ],
    )


class TestSanitizeHistory:
    def test_think_only_assistant_tail_is_dropped(self):
        history = [
            Message(role="user", content="What is 2+2?"),
            Message(role="assistant", content=[ThinkPart(think="Let me think...")]),
        ]
        result = sanitize_history(history)
        assert len(result) == 1
        assert result[0].role == "user"

    def test_think_and_text_assistant_tail_is_kept(self):
        history = [
            Message(role="user", content="What is 2+2?"),
            Message(role="assistant", content=[ThinkPart(think="Thinking..."), TextPart(text="4.")]),
        ]
        result = sanitize_history(history)
        assert len(result) == 2
        assert result[1].role == "assistant"

    def test_empty_text_assistant_tail_is_dropped(self):
        history = [
            Message(role="user", content="What is 2+2?"),
            Message(role="assistant", content=""),
        ]
        result = sanitize_history(history)
        assert len(result) == 1
        assert result[0].role == "user"

    def test_empty_text_part_assistant_tail_is_dropped(self):
        history = [
            Message(role="user", content="What is 2+2?"),
            Message(role="assistant", content=[TextPart(text="   ")]),
        ]
        result = sanitize_history(history)
        assert len(result) == 1

    def test_assistant_with_tool_calls_tail_is_kept(self):
        history = [
            Message(role="user", content="Run ls"),
            _assistant_with_tool_calls(),
        ]
        result = sanitize_history(history)
        assert len(result) == 2
        assert result[1].tool_calls

    def test_orphaned_tool_message_with_no_tool_calls_is_dropped(self):
        """A tool message whose tool_call_id matches nothing is dropped."""
        tool_result = Message(role="tool", content="file1 file2", tool_call_id="call_gone")
        history = [
            Message(role="user", content="Run ls"),
            tool_result,
        ]
        result = sanitize_history(history)
        assert len(result) == 1
        assert result[0].role == "user"

    def test_multiple_invalid_tails_are_all_dropped(self):
        history = [
            Message(role="user", content="What is 2+2?"),
            Message(role="assistant", content=[ThinkPart(think="Thinking...")]),
            Message(role="assistant", content=""),
        ]
        result = sanitize_history(history)
        assert len(result) == 1
        assert result[0].role == "user"

    def test_valid_history_is_unchanged(self):
        history = [
            Message(role="user", content="What is 2+2?"),
            Message(role="assistant", content="4."),
            Message(role="user", content="Thanks!"),
        ]
        result = sanitize_history(history)
        assert len(result) == 3
        assert result[-1].role == "user"

    def test_tool_tail_with_valid_reference_is_kept(self):
        assistant = _assistant_with_tool_calls()
        tool_result = Message(role="tool", content="5", tool_call_id="call_1")
        history = [
            Message(role="user", content="Add 2 and 3"),
            assistant,
            tool_result,
        ]
        result = sanitize_history(history)
        assert len(result) == 3
        assert result[-1].role == "tool"

    def test_empty_history(self):
        assert sanitize_history([]) == []

    def test_input_not_mutated(self):
        original = [
            Message(role="user", content="What is 2+2?"),
            Message(role="assistant", content=[ThinkPart(think="Thinking...")]),
        ]
        snapshot = [msg.model_copy(deep=True) for msg in original]
        sanitize_history(original)
        assert len(original) == 2
        for orig, snap in zip(original, snapshot, strict=True):
            assert orig.model_dump() == snap.model_dump()

    def test_user_tail_with_empty_text_is_dropped(self):
        history = [
            Message(role="user", content="What is 2+2?"),
            Message(role="assistant", content="4."),
            Message(role="user", content=[TextPart(text="")]),
        ]
        result = sanitize_history(history)
        assert len(result) == 2
        assert result[-1].role == "assistant"

    def test_user_tail_with_nonempty_text_is_kept(self):
        history = [
            Message(role="user", content="What is 2+2?"),
            Message(role="assistant", content="4."),
            Message(role="user", content="Thanks!"),
        ]
        result = sanitize_history(history)
        assert len(result) == 3
        assert result[-1].role == "user"
