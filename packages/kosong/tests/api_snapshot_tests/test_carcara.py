"""Snapshot tests for the Carcara (llama.cpp compatible) chat provider."""

import json

import respx
from common import COMMON_CASES, Case, make_chat_completion_response, run_test_cases
from httpx import Response

from kosong.contrib.chat_provider.carcara import CarcaraProvider
from kosong.message import Message, TextPart, ThinkPart, ToolCall

TEST_CASES: dict[str, Case] = {**COMMON_CASES}

BASE_URL = "https://carcara.example/v1"


def _has_meaningful_content(content) -> bool:
    """True if content carries something the model can continue from."""
    if content is None:
        return False
    if isinstance(content, str):
        return bool(content)
    if isinstance(content, list):
        return any(
            not (isinstance(part, dict) and part.get("type") == "think")
            for part in content
        )
    return bool(content)


def _last_message_is_valid(msg: dict) -> bool:
    """A last message is accepted by servers that enforce 'Last message must be from user or tool'.

    Mirrors ``CarcaraProvider._is_valid_tail``: user/tool/system tails are
    always fine; an assistant tail needs non-empty content or tool_calls.
    A think-only assistant message (reasoning extracted to reasoning_content)
    has ``content=None`` and is rejected by the server.
    """
    if msg["role"] in ("user", "tool", "system"):
        return True
    return _has_meaningful_content(msg.get("content")) or bool(msg.get("tool_calls"))


async def test_carcara_message_conversion():
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=Response(200, json=make_chat_completion_response("test-model"))
        )
        provider = CarcaraProvider(model="test-model", base_url=BASE_URL, stream=False)
        results = await run_test_cases(mock, provider, TEST_CASES, ("messages", "tools"))

        # Carcara always sends its extra body params and chat_template_kwargs
        body = json.loads(mock.calls.last.request.content.decode())
        assert body.get("reasoning_format") == "auto"
        assert body.get("return_progress") is True
        assert "chat_template_kwargs" in body

        # Every common case must end with a server-acceptable last message
        for name, case in results.items():
            last = case["messages"][-1]
            assert _last_message_is_valid(last), (
                f"case {name!r} ends with invalid message: {last!r}"
            )


async def test_carcara_reasoning_roundtrip():
    """ThinkPart in history must round-trip as the reasoning_content field."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=Response(200, json=make_chat_completion_response())
        )
        provider = CarcaraProvider(
            model="deepseek-reasoner",
            base_url=BASE_URL,
            stream=False,
            reasoning_key="reasoning_content",
        )
        history = [
            Message(role="user", content="What is 2+2?"),
            Message(
                role="assistant",
                content=[ThinkPart(think="Thinking..."), TextPart(text="4.")],
            ),
            Message(role="user", content="Thanks!"),
        ]
        async for _ in await provider.generate("", [], history):
            pass
        body = json.loads(mock.calls.last.request.content.decode())
        assert body["messages"] == [
            {"role": "user", "content": "What is 2+2?"},
            {
                "role": "assistant",
                "content": "4.",
                "reasoning_content": "Thinking...",
            },
            {"role": "user", "content": "Thanks!"},
        ]


async def test_carcara_reasoning_key_override():
    """A custom reasoning_key must be used for the reasoning field name."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=Response(200, json=make_chat_completion_response())
        )
        provider = CarcaraProvider(
            model="m", base_url=BASE_URL, stream=False, reasoning_key="thinking"
        )
        history = [
            Message(role="user", content="Hi"),
            Message(
                role="assistant",
                content=[ThinkPart(think="Thinking..."), TextPart(text="Hello!")],
            ),
            Message(role="user", content="Thanks!"),
        ]
        async for _ in await provider.generate("", [], history):
            pass
        body = json.loads(mock.calls.last.request.content.decode())
        assert body["messages"] == [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!", "thinking": "Thinking..."},
            {"role": "user", "content": "Thanks!"},
        ]


def _assistant_think_only_messages() -> list[Message]:
    """A history whose last message is an assistant message carrying ONLY a ThinkPart.

    This is exactly what the CLI persists when a stream is interrupted right after
    the model finishes thinking (or when the model answers with reasoning only).
    On resume, the next generate() call must NOT send an assistant message with
    content=None at the tail of the request — servers reject it with
    `{"error":"Last message must be from user or tool."}`.
    """
    return [
        Message(role="user", content="What is 2+2?"),
        Message(role="assistant", content=[ThinkPart(think="Let me think hard...")]),
    ]


async def test_carcara_last_message_assistant_think_only():
    """Regression: assistant-final history with only a ThinkPart must be repaired.

    Before the fix, the serialized body ended with:
        {"role": "assistant", "content": None, "reasoning_content": "..."}
    which the Carcara server rejected with:
        400 {"error":"Last message must be from user or tool."}
    """
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=Response(200, json=make_chat_completion_response())
        )
        provider = CarcaraProvider(model="m", base_url=BASE_URL, stream=False)
        history = _assistant_think_only_messages()
        async for _ in await provider.generate("", [], history):
            pass
        body = json.loads(mock.calls.last.request.content.decode())
        last = body["messages"][-1]
        assert _last_message_is_valid(last), (
            f"server-400 repro: request ends with invalid last message: {last!r}"
        )
        # The reasoning must not be silently lost: it is promoted into content
        # so the assistant message carries a non-empty tail.
        if last["role"] == "assistant":
            assert last.get("content")
        print(
            "[assistant_think_only] serialized messages:"
        )
        print(json.dumps(body["messages"], indent=2, ensure_ascii=False))


async def test_carcara_last_message_assistant_empty_text():
    """Regression: assistant-final history with empty text must be repaired.

    A Message(role="assistant", content="") dumps as content="" (the field
    serializer maps a single empty TextPart to ""), producing the same
    400 "Last message must be from user or tool." on the Carcara server.

    The provider-side repair is a no-op here (empty content cannot be
    invented out of nothing), so this test documents the wire format the
    CLI-layer guard in ``_sanitize_history`` must catch and drop.
    """
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=Response(200, json=make_chat_completion_response())
        )
        provider = CarcaraProvider(model="m", base_url=BASE_URL, stream=False)
        history = [
            Message(role="user", content="What is 2+2?"),
            Message(role="assistant", content=""),
        ]
        async for _ in await provider.generate("", [], history):
            pass
        body = json.loads(mock.calls.last.request.content.decode())
        last = body["messages"][-1]
        # The provider cannot repair an empty assistant tail — it stays empty
        # on the wire. The CLI-layer guard (test_sanitize_history) must drop it.
        assert last == {"role": "assistant", "content": ""}
        print(
            "[assistant_empty_text] serialized messages:"
        )
        print(json.dumps(body["messages"], indent=2, ensure_ascii=False))


async def test_carcara_last_message_assistant_think_and_tool_calls():
    """An assistant-final message with tool_calls is valid even if content is empty."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=Response(200, json=make_chat_completion_response())
        )
        provider = CarcaraProvider(model="m", base_url=BASE_URL, stream=False)
        history = [
            Message(role="user", content="Add 2 and 3"),
            Message(
                role="assistant",
                content=[ThinkPart(think="I will call the add tool")],
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        function=ToolCall.FunctionBody(name="add", arguments='{"a": 2, "b": 3}'),
                    )
                ],
            ),
        ]
        async for _ in await provider.generate("", [], history):
            pass
        body = json.loads(mock.calls.last.request.content.decode())
        last = body["messages"][-1]
        # assistant + tool_calls is acceptable: the tool result should follow,
        # but at minimum the request must not end with an empty assistant msg.
        assert last.get("tool_calls"), f"unexpected last message: {last!r}"
        assert _last_message_is_valid(last)


async def test_carcara_tool_message_serialization():
    """Tool messages must round-trip with tool_call_id intact."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=Response(200, json=make_chat_completion_response())
        )
        provider = CarcaraProvider(model="m", base_url=BASE_URL, stream=False)
        history = [
            Message(role="user", content="Add 2 and 3"),
            Message(
                role="assistant",
                content="I'll add those.",
                tool_calls=[
                    ToolCall(
                        id="call_abc123",
                        function=ToolCall.FunctionBody(name="add", arguments='{"a": 2, "b": 3}'),
                    )
                ],
            ),
            Message(role="tool", content="5", tool_call_id="call_abc123"),
            Message(role="user", content="And 3+3?"),
        ]
        async for _ in await provider.generate("", [], history):
            pass
        body = json.loads(mock.calls.last.request.content.decode())
        assert body["messages"] == [
            {"role": "user", "content": "Add 2 and 3"},
            {
                "role": "assistant",
                "content": "I'll add those.",
                "tool_calls": [
                    {
                        "type": "function",
                        "id": "call_abc123",
                        "function": {"name": "add", "arguments": '{"a": 2, "b": 3}'},
                    }
                ],
            },
            {"role": "tool", "content": "5", "tool_call_id": "call_abc123"},
            {"role": "user", "content": "And 3+3?"},
        ]
