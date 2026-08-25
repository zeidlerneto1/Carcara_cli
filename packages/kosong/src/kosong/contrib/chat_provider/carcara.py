from __future__ import annotations

import copy
import json
import os
from collections.abc import AsyncIterator, Sequence
from typing import Any, Self

import httpx

from kosong.chat_provider import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    ChatProvider,
    RetryableChatProvider,
    StreamedMessagePart,
    ThinkingEffort,
    TokenUsage,
)
from kosong.message import ContentPart, Message, TextPart, ThinkPart, ToolCall, ToolCallPart
from kosong.tooling import Tool


class CarcaraProvider:
    """
    Chat provider para o servidor Carcará (llama.cpp compatível).
    Usa httpx diretamente para injetar headers fixos e body params extras.
    Suporta todos os sampling params do llama.cpp: temperature, top_k, top_p,
    min_p, xtc, dynatemp, typ_p, backend_sampling, etc.
    """

    name = "carcara"

    # Headers fixos exigidos pelo servidor Carcará
    DEFAULT_HEADERS = {
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9,pt;q=0.8",
        "Referer": "https://carcara.sinapad.lncc.br/service/",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Origin": "https://carcara.sinapad.lncc.br",
    }

    # Body params extras fixos do Carcará
    EXTRA_BODY = {
        "return_progress": True,
        "reasoning_format": "auto",
        "chat_template_kwargs": {"enable_thinking": False},
        "reasoning_control": True,
        "timings_per_token": True,
    }

    # Sampling params aceitos pelo servidor (mapeados de env var -> nome do campo)
    SAMPLING_ENV_MAP = {
        "CARCARA_TEMPERATURE": ("temperature", float),
        "CARCARA_DYNATEMP_RANGE": ("dynatemp_range", float),
        "CARCARA_DYNATEMP_EXPONENT": ("dynatemp_exponent", float),
        "CARCARA_TOP_K": ("top_k", int),
        "CARCARA_TOP_P": ("top_p", float),
        "CARCARA_MIN_P": ("min_p", float),
        "CARCARA_XTC_PROBABILITY": ("xtc_probability", float),
        "CARCARA_XTC_THRESHOLD": ("xtc_threshold", float),
        "CARCARA_TYP_P": ("typ_p", float),
        "CARCARA_BACKEND_SAMPLING": ("backend_sampling", lambda v: v.lower() in ("1", "true", "yes", "on")),
    }

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        stream: bool = True,
        reasoning_key: str | None = None,
        **client_kwargs: Any,
    ):
        self.model = model
        self.stream = stream
        self._api_key = api_key or ""
        self._base_url = (base_url or "").rstrip("/")
        self._reasoning_key = reasoning_key or "reasoning_content"
        self._thinking_effort: ThinkingEffort | None = None

        # Montar headers
        headers = dict(self.DEFAULT_HEADERS)
        headers["Content-Type"] = "application/json"
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        if custom_headers := client_kwargs.pop("default_headers", None):
            headers.update(custom_headers)

        self._headers = headers
        self._client_kwargs = dict(client_kwargs)
        self._client: httpx.AsyncClient | None = None

        # Coletar sampling params de env vars
        self._sampling_params: dict[str, Any] = {}
        for env_name, (field_name, converter) in self.SAMPLING_ENV_MAP.items():
            raw = os.getenv(env_name)
            if raw is not None:
                try:
                    self._sampling_params[field_name] = converter(raw)
                except Exception:
                    pass

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers=self._headers,
                timeout=httpx.Timeout(60.0, connect=10.0),
                **self._client_kwargs,
            )
        return self._client

    @property
    def model_name(self) -> str:
        return self.model

    @property
    def thinking_effort(self) -> ThinkingEffort | None:
        return self._thinking_effort

    async def generate(
        self,
        system_prompt: str,
        tools: Sequence[Tool],
        history: Sequence[Message],
    ) -> "CarcaraStreamedMessage":
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        for msg in history:
            dumped = msg.model_dump(exclude_none=True)
            reasoning = ""
            content_parts = []
            for part in msg.content:
                if isinstance(part, ThinkPart):
                    reasoning += part.think
                else:
                    content_parts.append(part.model_dump(exclude_none=True))
            if reasoning and self._reasoning_key:
                dumped[self._reasoning_key] = reasoning
            if content_parts:
                if len(content_parts) == 1 and content_parts[0].get("type") == "text":
                    dumped["content"] = content_parts[0].get("text", "")
                else:
                    dumped["content"] = content_parts
            messages.append(dumped)

        openai_tools = []
        for tool in tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            })

        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": self.stream,
            **self.EXTRA_BODY,
        }

        # Merge sampling params (env vars override defaults)
        body.update(self._sampling_params)

        if openai_tools:
            body["tools"] = openai_tools
            body["tool_choice"] = "auto"

        if self.stream:
            body["stream_options"] = {"include_usage": True}

        if self._thinking_effort and self._thinking_effort != "off":
            body["reasoning_effort"] = self._thinking_effort

        url = f"{self._base_url}/chat/completions"
        client = self._get_client()

        try:
            response = await client.post(url, json=body)
            response.raise_for_status()

            if self.stream:
                return CarcaraStreamedMessage(response.aiter_text(), self._reasoning_key)
            else:
                data = response.json()
                return CarcaraStreamedMessage.from_json(data, self._reasoning_key)

        except httpx.TimeoutException as e:
            raise APITimeoutError(str(e)) from e
        except httpx.NetworkError as e:
            raise APIConnectionError(str(e)) from e
        except httpx.HTTPStatusError as e:
            raise APIStatusError(
                e.response.status_code,
                str(e),
                request_id=e.response.headers.get("x-request-id"),
                trace_id=e.response.headers.get("x-trace-id"),
            ) from e
        except Exception as e:
            from kosong.chat_provider import ChatProviderError
            raise ChatProviderError(f"Carcara request failed: {e}") from e

    def on_retryable_error(self, error: BaseException) -> bool:
        return True

    def with_thinking(self, effort: ThinkingEffort) -> Self:
        new_self = copy.copy(self)
        new_self._thinking_effort = effort
        return new_self

    @property
    def model_parameters(self) -> dict[str, Any]:
        params: dict[str, Any] = {"base_url": self._base_url, "model": self.model}
        params.update(self._sampling_params)
        return params


class CarcaraStreamedMessage:
    def __init__(
        self,
        response: AsyncIterator[str] | dict[str, Any],
        reasoning_key: str | None,
    ):
        self._reasoning_key = reasoning_key
        if isinstance(response, dict):
            self._iter = self._from_dict(response)
        else:
            self._iter = self._from_sse(response)
        self._id: str | None = None
        self._usage: TokenUsage | None = None

    def __aiter__(self) -> AsyncIterator[StreamedMessagePart]:
        return self

    async def __anext__(self) -> StreamedMessagePart:
        return await self._iter.__anext__()

    @property
    def id(self) -> str | None:
        return self._id

    @property
    def trace_id(self) -> str | None:
        return None

    @property
    def usage(self) -> TokenUsage | None:
        return self._usage

    @classmethod
    def from_json(cls, data: dict[str, Any], reasoning_key: str | None) -> "CarcaraStreamedMessage":
        instance = cls.__new__(cls)
        instance._reasoning_key = reasoning_key
        instance._id = data.get("id")
        instance._usage = None
        instance._iter = instance._from_dict(data)
        return instance

    async def _from_dict(self, data: dict[str, Any]) -> AsyncIterator[StreamedMessagePart]:
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})

        if self._reasoning_key and message.get(self._reasoning_key):
            yield ThinkPart(think=message[self._reasoning_key])

        if message.get("content"):
            yield TextPart(text=message["content"])

        for tc in message.get("tool_calls", []):
            yield ToolCall(
                id=tc.get("id", ""),
                name=tc.get("function", {}).get("name", ""),
                arguments=tc.get("function", {}).get("arguments", ""),
            )

        usage = data.get("usage")
        if usage:
            self._usage = TokenUsage(
                input_other=usage.get("prompt_tokens", 0),
                output=usage.get("completion_tokens", 0),
            )

    async def _from_sse(self, aiter_text: AsyncIterator[str]) -> AsyncIterator[StreamedMessagePart]:
        buffer = ""
        reasoning_buffer = ""
        content_buffer = ""

        async for chunk in aiter_text:
            buffer += chunk
            while "\n\n" in buffer:
                event, buffer = buffer.split("\n\n", 1)
                lines = event.strip().split("\n")
                if not lines:
                    continue

                data_line = ""
                for line in lines:
                    if line.startswith("data: "):
                        data_line = line[6:]
                        break

                if not data_line or data_line == "[DONE]":
                    continue

                try:
                    data = json.loads(data_line)
                except json.JSONDecodeError:
                    continue

                if data.get("id"):
                    self._id = data["id"]

                if "usage" in data and data["usage"]:
                    usage = data["usage"]
                    self._usage = TokenUsage(
                        input_other=usage.get("prompt_tokens", 0),
                        output=usage.get("completion_tokens", 0),
                    )

                for choice in data.get("choices", []):
                    delta = choice.get("delta", {})

                    if self._reasoning_key and delta.get(self._reasoning_key):
                        reasoning_buffer += delta[self._reasoning_key]
                        yield ThinkPart(think=reasoning_buffer)
                        reasoning_buffer = ""

                    if delta.get("content"):
                        content_buffer += delta["content"]
                        yield TextPart(text=content_buffer)
                        content_buffer = ""

                    for tc in delta.get("tool_calls", []):
                        func = tc.get("function", {})
                        if func.get("name") or func.get("arguments"):
                            yield ToolCall(
                                id=tc.get("id", ""),
                                name=func.get("name", ""),
                                arguments=func.get("arguments", ""),
                            )
