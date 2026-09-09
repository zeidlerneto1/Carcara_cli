from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from kosong.chat_provider import ChatProvider, StreamedMessage, StreamedMessagePart, ThinkingEffort, TokenUsage
from kosong.message import Message, TextPart, ToolCall
from kosong.tooling import Tool


class _OllamaStreamedMessage:
    def __init__(
        self,
        *,
        parts: list[StreamedMessagePart],
        message_id: str | None = None,
        usage: Any = None,
    ) -> None:
        self.parts = parts
        self.id = message_id or str(uuid.uuid4())
        self.usage = usage
        self.trace_id = None

    def __aiter__(self) -> AsyncIterator[StreamedMessagePart]:
        async def iterator() -> AsyncIterator[StreamedMessagePart]:
            for part in self.parts:
                yield part

        return iterator()


class OllamaProvider(ChatProvider):
    def __init__(
        self,
        *,
        model: str,
        base_url: str = "http://localhost:11434/api",
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.name = "ollama"
        self._thinking = False

    @property
    def model_name(self) -> str:
        return self.model

    @property
    def thinking_effort(self) -> ThinkingEffort | None:
        return "high" if self._thinking else "off"

    def with_thinking(self, effort: ThinkingEffort) -> OllamaProvider:
        self._thinking = effort != "off"
        return self

    def _convert_message(self, message: Message) -> dict[str, Any]:
        content = message.extract_text()

        if message.role == "tool":
            result: dict[str, Any] = {
                "role": "tool",
                "content": content,
            }

            if message.name:
                result["tool_name"] = message.name

            return result

        result = {
            "role": message.role,
            "content": content,
        }

        if message.name:
            result["name"] = message.name

        if message.tool_calls:
            tool_calls = []

            for call in message.tool_calls:
                arguments = call.function.arguments or "{}"

                try:
                    parsed_arguments = json.loads(arguments)
                except (TypeError, ValueError):
                    parsed_arguments = {}

                tool_calls.append(
                    {
                        "function": {
                            "name": call.function.name,
                            "arguments": parsed_arguments,
                        }
                    }
                )

            result["tool_calls"] = tool_calls

        return result

    def _build_messages(
        self,
        system_prompt: str,
        history: Sequence[Message],
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []

        if system_prompt:
            messages.append(
                {
                    "role": "system",
                    "content": system_prompt,
                }
            )

        for message in history:
            messages.append(self._convert_message(message))

        return messages

    def _parse_tool_call(self, raw: dict[str, Any]) -> ToolCall:
        function = raw.get("function") or {}

        name = function.get("name") or raw.get("name") or ""
        arguments = function.get("arguments", raw.get("arguments", {}))

        if isinstance(arguments, str):
            arguments_json = arguments
        else:
            arguments_json = json.dumps(
                arguments,
                ensure_ascii=False,
                separators=(",", ":"),
            )

        call_id = (
            raw.get("id")
            or function.get("id")
            or f"ollama-{uuid.uuid4().hex}"
        )

        return ToolCall(
            id=call_id,
            function=ToolCall.FunctionBody(
                name=name,
                arguments=arguments_json,
            ),
        )

    async def generate(
        self,
        system_prompt: str,
        tools: Sequence[Tool],
        history: Sequence[Message],
    ) -> StreamedMessage:
        messages = self._build_messages(system_prompt, history)

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }

        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in tools
            ]

        if self._thinking:
            payload["think"] = True

        url = f"{self.base_url}/chat"

        async with httpx.AsyncClient(timeout=None) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()

        message = data.get("message") or {}

        parts: list[StreamedMessagePart] = []

        content = message.get("content")

        if content:
            parts.append(TextPart(text=content))

        raw_tool_calls = message.get("tool_calls") or []

        for raw_tool_call in raw_tool_calls:
            if isinstance(raw_tool_call, dict):
                parts.append(self._parse_tool_call(raw_tool_call))

        message_id = data.get("id")

        usage = None

        if any(
            key in data
            for key in (
                "prompt_eval_count",
                "eval_count",
            )
        ):
            usage = TokenUsage(
                input_other=data.get("prompt_eval_count", 0),
                output=data.get("eval_count", 0),
            )

        return _OllamaStreamedMessage(
            parts=parts,
            message_id=message_id,
            usage=usage,
        )
