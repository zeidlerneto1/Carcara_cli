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
    Suporta thinking_budget_tokens, sampling params e tools do LNCC + nativas.
    """

    name = "carcara"

    DEFAULT_HEADERS = {
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9,pt;q=0.8",
        "Referer": "https://carcara.sinapad.lncc.br/service/",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Origin": "https://carcara.sinapad.lncc.br",
    }

    EXTRA_BODY = {
        "return_progress": True,
        "reasoning_format": "auto",
        "reasoning_control": True,
        "backend_sampling": False,
        "timings_per_token": True,
    }

    LNCC_TOOLS = [
        {
            "type": "function",
            "function": {
                "name": "get_environment",
                "description": "Retorna o contexto de execução: que você (o modelo) está rodando no supercomputador Santos Dumont (SDumont) do LNCC, com as características do ambiente. Chame quando precisar saber/declarar ONDE está sendo executado ou quais recursos de HPC estão disponíveis.",
                "parameters": {"type": "object", "properties": {}, "title": "get_environmentArguments"},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_skills",
                "description": "Lista as skills (áreas de conhecimento de domínio) disponíveis no MCP do LNCC, com uma breve descrição de cada. Use antes de `get_skill` para descobrir qual área consultar.",
                "parameters": {"type": "object", "properties": {}, "title": "list_skillsArguments"},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_skill",
                "description": "Retorna o conhecimento completo (boas práticas, comandos, referências) de uma skill de domínio do LNCC. Use para fundamentar respostas técnicas. area: slug da skill (ex.: 'numerical-methods', 'programming', 'bioinformatics', 'molecular-modeling', 'hpc-sdumont'). Veja `list_skills`.",
                "parameters": {
                    "type": "object",
                    "properties": {"area": {"title": "Area", "type": "string"}},
                    "required": ["area"],
                    "title": "get_skillArguments",
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ask_expert",
                "description": "Consulta um 'especialista' de uma área de domínio do LNCC sobre uma pergunta. HOJE (modo conhecimento): use para obter respostas técnicas aprofundadas de especialistas nas áreas do LNCC.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "area": {"title": "Area", "type": "string"},
                        "question": {"title": "Question", "type": "string"},
                    },
                    "required": ["area", "question"],
                    "title": "ask_expertArguments",
                },
            },
        },
    ]

    THINKING_BUDGET_MAP = {
        "low": 512,
        "medium": 2048,
        "high": 8192,
    }

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

        headers = dict(self.DEFAULT_HEADERS)
        headers["Content-Type"] = "application/json"
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        if custom_headers := client_kwargs.pop("default_headers", None):
            headers.update(custom_headers)

        self._headers = headers
        self._client_kwargs = dict(client_kwargs)
        self._client: httpx.AsyncClient | None = None

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

    def _serialize_history(self, history: Sequence[Message]) -> list[dict[str, Any]]:
        """Convert kosong messages into wire dicts, repairing invalid tails.

        A message that ends with an assistant entry carrying no visible content
        and no tool calls (e.g. a think-only response from an interrupted
        stream) is rejected by servers that enforce
        ``Last message must be from user or tool.``  For such tails we promote
        the reasoning text into ``content`` so the request stays valid and the
        reasoning is not lost.

        This is the wire-layer defense. The CLI's soul layer applies
        ``sanitize_history`` (see ``kimi_cli.soul.dynamic_injection``) before
        calling the provider, so the two are intentionally redundant: this
        guard protects any caller of ``CarcaraProvider``, not just the CLI.
        """
        messages: list[dict[str, Any]] = []
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

        last = messages[-1]
        if not self._is_valid_tail(last):
            if last.get("role") == "assistant":
                reasoning = last.pop(self._reasoning_key, None) if self._reasoning_key else None
                if reasoning:
                    last["content"] = reasoning
                else:
                    last["content"] = ""
            elif last.get("content") in (None, ""):
                last["content"] = ""
        return messages

    @staticmethod
    def _is_valid_tail(entry: dict[str, Any]) -> bool:
        """Whether this message can legally end a chat-completions request.

        Servers that enforce ``Last message must be from user or tool.`` reject
        a request whose tail is an assistant message with no visible content
        and no tool calls (a think-only response has its reasoning extracted
        into ``reasoning_content``, leaving ``content=None``).
        """
        role = entry.get("role")
        if role in ("user", "tool", "system"):
            return True
        content = entry.get("content")
        if content is None:
            return bool(entry.get("tool_calls"))
        if isinstance(content, str):
            return bool(content) or bool(entry.get("tool_calls"))
        if isinstance(content, list):
            return (
                any(not (isinstance(p, dict) and p.get("type") == "think") for p in content)
                or bool(entry.get("tool_calls"))
            )
        return True

    async def generate(
        self,
        system_prompt: str,
        tools: Sequence[Tool],
        history: Sequence[Message],
    ) -> CarcaraStreamedMessage:
        messages: list[dict[str, Any]] = self._serialize_history(history)
        if system_prompt:
            messages = [{"role": "system", "content": system_prompt}] + messages

        all_tools: list[dict[str, Any]] = []
        if os.getenv("CARCARA_LNCC_TOOLS", "").lower() in ("1", "true", "yes", "on"):
            all_tools.extend(self.LNCC_TOOLS)
        for tool in tools:
            all_tools.append({
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
        body.update(self._sampling_params)

        if self._thinking_effort and self._thinking_effort != "off":
            body["chat_template_kwargs"] = {"enable_thinking": True}
            if self._thinking_effort in self.THINKING_BUDGET_MAP:
                body["thinking_budget_tokens"] = self.THINKING_BUDGET_MAP[self._thinking_effort]
        else:
            body["chat_template_kwargs"] = {"enable_thinking": False}

        if all_tools:
            body["tools"] = all_tools
            body["tool_choice"] = "auto"

        if self.stream:
            body["stream_options"] = {"include_usage": True}

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
            func = tc.get("function", {})
            yield ToolCall(
                id=tc.get("id", ""),
                function=ToolCall.FunctionBody(
                    name=func.get("name", ""),
                    arguments=func.get("arguments", ""),
                ),
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

                    # Tool calls: emitir ToolCall (com name) ou ToolCallPart (só arguments)
                    for tc in delta.get("tool_calls", []):
                        func = tc.get("function", {})
                        if not func:
                            continue

                        tc_id = tc.get("id", "")
                        tc_name = func.get("name")
                        tc_args = func.get("arguments")

                        if tc_name:
                            # Início de uma tool call: emitir ToolCall completo
                            yield ToolCall(
                                id=tc_id or "",
                                function=ToolCall.FunctionBody(
                                    name=tc_name,
                                    arguments=tc_args or "",
                                ),
                            )
                        elif tc_args:
                            # Continuação: emitir ToolCallPart (só arguments)
                            yield ToolCallPart(arguments_part=tc_args)
                        else:
                            # skip empty
                            pass
