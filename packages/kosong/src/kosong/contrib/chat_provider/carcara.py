from __future__ import annotations

import copy
from collections.abc import AsyncIterator, Sequence
from typing import Any, Self, Unpack

import httpx
from openai import AsyncStream, OpenAIError, omit
from openai.types import CompletionUsage, ReasoningEffort
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionChunk,
    ChatCompletionMessageParam,
)
from typing_extensions import TypedDict

from kosong.chat_provider import (
    ChatProvider,
    RetryableChatProvider,
    StreamedMessagePart,
    ThinkingEffort,
    TokenUsage,
)
from kosong.chat_provider.openai_common import (
    close_replaced_openai_client,
    convert_error,
    create_openai_client,
    reasoning_effort_to_thinking_effort,
    thinking_effort_to_reasoning_effort,
    tool_to_openai,
)
from kosong.contrib.chat_provider.common import ToolMessageConversion
from kosong.message import ContentPart, Message, TextPart, ThinkPart, ToolCall, ToolCallPart
from kosong.tooling import Tool


class CarcaraProvider:
    """
    Chat provider para o servidor Carcará (llama.cpp compatível).
    Baseado no OpenAILegacy mas injeta headers fixos e body params extras.
    """

    name = "carcara"

    class GenerationKwargs(TypedDict, extra_items=Any, total=False):
        max_tokens: int | None
        temperature: float | None
        top_p: float | None
        n: int | None
        presence_penalty: float | None
        frequency_penalty: float | None
        stop: str | list[str] | None
        prompt_cache_key: str | None

    # Headers fixos exigidos pelo servidor Carcará
    DEFAULT_HEADERS = {
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9,pt;q=0.8",
        "Referer": "https://carcara.sinapad.lncc.br/service/",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Origin": "https://carcara.sinapad.lncc.br",
    }

    # Body params extras exigidos pelo servidor Carcará
    EXTRA_BODY = {
        "return_progress": True,
        "reasoning_format": "auto",
        "chat_template_kwargs": {"enable_thinking": False},
        "reasoning_control": True,
        "backend_sampling": False,
        "timings_per_token": True,
    }

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        stream: bool = True,
        reasoning_key: str | None = None,
        tool_message_conversion: ToolMessageConversion | None = None,
        **client_kwargs: Any,
    ):
        self.model = model
        self.stream = stream
        self._api_key: str | None = api_key
        self._base_url: str | None = base_url
        self._client_kwargs: dict[str, Any] = dict(client_kwargs)

        # Merge headers fixos com os custom headers passados
        headers = dict(self.DEFAULT_HEADERS)
        if custom_headers := self._client_kwargs.pop("default_headers", None):
            headers.update(custom_headers)

        self.client = create_openai_client(
            api_key=self._api_key,
            base_url=self._base_url,
            client_kwargs={**self._client_kwargs, "default_headers": headers},
        )
        self._reasoning_effort: ReasoningEffort | Any = omit
        self._reasoning_key = reasoning_key
        self._tool_message_conversion: ToolMessageConversion | None = tool_message_conversion
        self._generation_kwargs: CarcaraProvider.GenerationKwargs = {}

    @property
    def model_name(self) -> str:
        return self.model

    @property
    def thinking_effort(self) -> ThinkingEffort | None:
        if isinstance(self._reasoning_effort, type(omit)):
            return None
        return reasoning_effort_to_thinking_effort(self._reasoning_effort)

    async def generate(
        self,
        system_prompt: str,
        tools: Sequence[Tool],
        history: Sequence[Message],
    ) -> "CarcaraStreamedMessage":
        messages: list[ChatCompletionMessageParam] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(self._convert_message(message) for message in history)

        generation_kwargs: dict[str, Any] = {}
        generation_kwargs.update(self._generation_kwargs)

        reasoning_effort = self._reasoning_effort
        if isinstance(reasoning_effort, type(omit)) and self._reasoning_key:
            has_think_part = any(
                isinstance(part, ThinkPart) for message in history for part in message.content
            )
            if has_think_part:
                reasoning_effort = "medium"

        # Monta o body da request com os campos extras do Carcará
        request_body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "tools": [tool_to_openai(tool) for tool in tools],
            "stream": self.stream,
            "stream_options": {"include_usage": True} if self.stream else omit,
            "reasoning_effort": reasoning_effort,
            **self.EXTRA_BODY,
            **generation_kwargs,
        }

        try:
            response = await self.client.chat.completions.create(**request_body)
            return CarcaraStreamedMessage(response, self._reasoning_key)
        except (OpenAIError, httpx.HTTPError) as e:
            raise convert_error(e) from e

    def on_retryable_error(self, error: BaseException) -> bool:
        old_client = self.client
        headers = dict(self.DEFAULT_HEADERS)
        if custom_headers := self._client_kwargs.get("default_headers"):
            headers.update(custom_headers)
        self.client = create_openai_client(
            api_key=self._api_key,
            base_url=self._base_url,
            client_kwargs={**self._client_kwargs, "default_headers": headers},
        )
        close_replaced_openai_client(old_client, client_kwargs=self._client_kwargs)
        return True

    def with_thinking(self, effort: ThinkingEffort) -> Self:
        new_self = copy.copy(self)
        new_self._reasoning_effort = thinking_effort_to_reasoning_effort(effort)
        return new_self

    def with_generation_kwargs(self, **kwargs: Unpack[GenerationKwargs]) -> Self:
        new_self = copy.copy(self)
        new_self._generation_kwargs = copy.deepcopy(self._generation_kwargs)
        new_self._generation_kwargs.update(kwargs)
        return new_self

    @property
    def model_parameters(self) -> dict[str, Any]:
        model_parameters: dict[str, Any] = {"base_url": str(self.client.base_url)}
        if self._reasoning_effort is not omit:
            model_parameters["reasoning_effort"] = self._reasoning_effort
        return model_parameters

    def _convert_message(self, message: Message) -> ChatCompletionMessageParam:
        message = message.model_copy(deep=True)
        reasoning_content: str = ""
        content: list[ContentPart] = []
        has_reasoning = False
        for part in message.content:
            if isinstance(part, ThinkPart):
                has_reasoning = True
                reasoning_content += part.think
            else:
                content.append(part)
        if message.role == "tool" and self._tool_message_conversion == "extract_text":
            message.content = [TextPart(text=message.extract_text(sep="\n"))]
        else:
            message.content = content
        dumped_message = message.model_dump(exclude_none=True)
        if has_reasoning and self._reasoning_key:
            dumped_message[self._reasoning_key] = reasoning_content
        return dumped_message  # type: ignore[return-value]


class CarcaraStreamedMessage:
    def __init__(
        self,
        response: ChatCompletion | AsyncStream[ChatCompletionChunk],
        reasoning_key: str | None,
    ):
        self._reasoning_key: str | None = reasoning_key
        if isinstance(response, ChatCompletion):
            self._iter = self._convert_non_stream_response(response)
        else:
            self._iter = self._convert_stream_response(response)
        self._id: str | None = None
        self._usage: CompletionUsage | None = None

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
        if self._usage:
            cached = 0
            other_input = self._usage.prompt_tokens
            if (
                self._usage.prompt_tokens_details
                and self._usage.prompt_tokens_details.cached_tokens
            ):
                cached = self._usage.prompt_tokens_details.cached_tokens
                other_input -= cached
            return TokenUsage(
                input_other=other_input,
                output=self._usage.completion_tokens,
                input_cache_read=cached,
            )
        return None

    async def _convert_non_stream_response(
        self,
        response: ChatCompletion,
    ) -> AsyncIterator[StreamedMessagePart]:
        self._id = response.id
        if response.usage:
            self._usage = response.usage
        choice = response.choices[0]
        message = choice.message
        if hasattr(message, "reasoning_content") and message.reasoning_content:
            yield ThinkPart(think=message.reasoning_content)
        if message.content:
            yield TextPart(text=message.content)
        if message.tool_calls:
            for tool_call in message.tool_calls:
                yield ToolCall(
                    id=tool_call.id,
                    name=tool_call.function.name,
                    arguments=tool_call.function.arguments,
                )

    async def _convert_stream_response(
        self,
        response: AsyncStream[ChatCompletionChunk],
    ) -> AsyncIterator[StreamedMessagePart]:
        reasoning_buffer = ""
        content_buffer = ""
        async for chunk in response:
            if chunk.id:
                self._id = chunk.id
            if chunk.usage:
                self._usage = chunk.usage
            for choice in chunk.choices:
                delta = choice.delta
                if self._reasoning_key and hasattr(delta, self._reasoning_key):
                    reasoning_content = getattr(delta, self._reasoning_key)
                    if reasoning_content:
                        reasoning_buffer += reasoning_content
                        yield ThinkPart(think=reasoning_buffer)
                        reasoning_buffer = ""
                if delta.content:
                    content_buffer += delta.content
                    yield TextPart(text=content_buffer)
                    content_buffer = ""
                if delta.tool_calls:
                    for tool_call in delta.tool_calls:
                        if tool_call.function:
                            yield ToolCall(
                                id=tool_call.id or "",
                                name=tool_call.function.name or "",
                                arguments=tool_call.function.arguments or "",
                            )
