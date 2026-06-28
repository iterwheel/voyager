"""DeepSeek LLM adapter — OpenAI-compatible endpoint with V4 thinking mode support."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import openai


@dataclass
class ToolCall:
    """A single tool call returned by the model."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    """A single conversation message for input to complete()."""

    role: str
    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None

    def to_openai_dict(self) -> dict[str, Any]:
        """Serialize to the dict shape expected by the DeepSeek/OpenAI API."""
        d: dict[str, Any] = {"role": self.role}

        if self.role == "tool":
            d["content"] = self.content
            if self.tool_call_id is not None:
                d["tool_call_id"] = self.tool_call_id
            return d

        if self.content is not None:
            d["content"] = self.content

        # V4 multi-turn rule: reasoning_content MUST be forwarded verbatim on
        # assistant turns — omitting it causes the API to return 400.
        if self.role == "assistant" and self.reasoning_content is not None:
            d["reasoning_content"] = self.reasoning_content

        # Multi-turn tool calling rule: an assistant turn that invoked tools
        # MUST be forwarded with its tool_calls preserved on subsequent calls.
        # Otherwise the next `role: "tool"` message references a tool_call_id
        # that has no matching assistant.tool_calls, and the API rejects the
        # request (or silently re-invents the call).
        if self.role == "assistant" and self.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in self.tool_calls
            ]

        return d


@dataclass
class AssistantTurn:
    """The model's response returned by complete()."""

    content: str | None
    reasoning_content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)


def _completion_kwargs(
    model: str,
    messages: list[Message],
    *,
    thinking: bool,
    reasoning_effort: str | None,
    tools: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    openai_messages = [m.to_openai_dict() for m in messages]

    extra_body: dict[str, Any] = {
        "thinking": {"type": "enabled" if thinking else "disabled"},
    }
    if reasoning_effort is not None:
        extra_body["reasoning_effort"] = reasoning_effort

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": openai_messages,
        "extra_body": extra_body,
    }
    if tools:
        kwargs["tools"] = tools
    return kwargs


def _assistant_turn_from_response(response: Any) -> AssistantTurn:
    message = response.choices[0].message

    # Parse tool_calls if present.
    tool_calls: list[ToolCall] = []
    raw_tool_calls = getattr(message, "tool_calls", None)
    if raw_tool_calls:
        for tc in raw_tool_calls:
            arguments = json.loads(tc.function.arguments)
            tool_calls.append(
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=arguments,
                )
            )

    # reasoning_content is a DeepSeek extension — not on the standard Pydantic
    # model, so we pull it from __pydantic_extra__ or fall back to None.
    reasoning_content: str | None = getattr(message, "reasoning_content", None)
    if reasoning_content == "":
        reasoning_content = None

    return AssistantTurn(
        content=message.content,
        reasoning_content=reasoning_content,
        tool_calls=tool_calls,
    )


class DeepSeekClient:
    """Thin adapter around AsyncOpenAI pointed at api.deepseek.com."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.deepseek.com",
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url
        self._client: httpx.AsyncClient | None = None

    def _async_client(self) -> httpx.AsyncClient:
        """Return a per-instance cached httpx.AsyncClient.

        Override in tests to inject MockTransport. Production code reuses the
        cached client across all complete() calls; the TLS connection pool to
        api.deepseek.com is shared and connection setup is amortized.
        """
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient()
        return self._client

    async def aclose(self) -> None:
        """Close the cached httpx client. Call on FastAPI lifespan shutdown."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    def _openai_client(self) -> openai.AsyncOpenAI:
        return openai.AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            http_client=self._async_client(),
            max_retries=0,
        )

    async def complete(
        self,
        messages: list[Message],
        *,
        thinking: bool = True,
        reasoning_effort: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AssistantTurn:
        """Call the DeepSeek chat completions endpoint and return an AssistantTurn."""
        kwargs = _completion_kwargs(
            self._model,
            messages,
            thinking=thinking,
            reasoning_effort=reasoning_effort,
            tools=tools,
        )

        client = self._openai_client()
        try:
            response = await client.chat.completions.create(**kwargs)
        except openai.APIResponseValidationError as exc:
            # Malformed JSON from the server — normalize to ValueError so callers
            # (and tests) can check isinstance(exc, (json.JSONDecodeError, ValueError)).
            raise ValueError(f"Invalid JSON in DeepSeek response: {exc}") from exc
        except openai.APITimeoutError as exc:
            # Propagate as httpx.TimeoutException so the timeout step assertion passes.
            raise httpx.ReadTimeout("DeepSeek request timed out", request=None) from exc

        return _assistant_turn_from_response(response)


class SyncDeepSeekClient:
    """Synchronous DeepSeek adapter for non-async callers such as Countdown."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.deepseek.com",
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url
        self._client: httpx.Client | None = None

    def _sync_client(self) -> httpx.Client:
        """Return a per-instance cached httpx.Client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client()
        return self._client

    def close(self) -> None:
        """Close the cached sync httpx client."""
        if self._client is not None and not self._client.is_closed:
            self._client.close()
        self._client = None

    def _openai_client(self) -> openai.OpenAI:
        return openai.OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            http_client=self._sync_client(),
            max_retries=0,
        )

    def complete(
        self,
        messages: list[Message],
        *,
        thinking: bool = True,
        reasoning_effort: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AssistantTurn:
        """Call the DeepSeek chat completions endpoint synchronously."""
        kwargs = _completion_kwargs(
            self._model,
            messages,
            thinking=thinking,
            reasoning_effort=reasoning_effort,
            tools=tools,
        )

        client = self._openai_client()
        try:
            response = client.chat.completions.create(**kwargs)
        except openai.APIResponseValidationError as exc:
            raise ValueError(f"Invalid JSON in DeepSeek response: {exc}") from exc
        except openai.APITimeoutError as exc:
            raise httpx.ReadTimeout("DeepSeek request timed out", request=None) from exc

        return _assistant_turn_from_response(response)
