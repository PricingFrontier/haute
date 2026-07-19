"""Provider-neutral streaming adapters for the assistant agent loop.

The optional provider SDKs are deliberately imported only when an adapter has
to construct its production client.  The rest of Haute can therefore import
the assistant package without requiring either SDK, while tests and callers can
inject a client at the adapter seam.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from dataclasses import dataclass
from inspect import isawaitable
from typing import Any, Literal, Protocol, TypeAlias

from haute._logging import get_logger
from haute.assistant._config import AssistantConfig
from haute.errors import HauteError

logger = get_logger(component="assistant.providers")


@dataclass(frozen=True, slots=True)
class TextDelta:
    """A provider text fragment."""

    text: str


@dataclass(frozen=True, slots=True)
class ToolCallRequest:
    """A complete provider tool call with parsed JSON arguments."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    """Token usage for one provider round-trip."""

    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class TurnStop:
    """The provider's terminal reason for one streamed round-trip."""

    reason: Literal["end", "tool_use"]
    usage: ProviderUsage


ProviderEvent: TypeAlias = TextDelta | ToolCallRequest | TurnStop


class AssistantProvider(Protocol):
    """Provider-neutral streaming interface consumed by the agent loop."""

    def stream_turn(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> AsyncIterator[ProviderEvent]: ...


class AssistantProviderError(HauteError):
    """A sanitized provider or provider-stream failure."""

    def __init__(
        self,
        provider: str,
        failure_class: str = "stream",
        detail: str | None = None,
    ) -> None:
        self.provider = provider
        self.failure_class = failure_class
        message = f"{provider} provider {failure_class} failure"
        if detail is not None:
            message = f"{message}: {detail}"
        super().__init__(message)


def _provider_error(provider: str, failure_class: str, detail: str) -> AssistantProviderError:
    """Build an error from hand-authored text only; never include SDK text."""

    return AssistantProviderError(provider, failure_class, detail)


def _classify_sdk_error(provider: str, error: Exception) -> AssistantProviderError:
    """Map an SDK exception class to a sanitized failure category.

    The raw provider error is logged server-side for the operator — the
    sanitization contract only forbids leaking it into the chat stream.
    Without this log a quota/authentication failure is undiagnosable from
    ``haute serve`` output.
    """

    logger.warning(
        "assistant_provider_request_failed",
        provider=provider,
        error_class=type(error).__name__,
        detail=str(error),
    )
    class_name = type(error).__name__.lower()
    if "auth" in class_name or "permission" in class_name:
        category = "authentication"
    elif "rate" in class_name or "ratelimit" in class_name:
        category = "rate_limit"
    elif any(word in class_name for word in ("connection", "timeout", "network")):
        category = "connection"
    elif "status" in class_name or class_name in {
        "badrequesterror",
        "internalservererror",
        "apierror",
    }:
        category = "status"
    else:
        category = "stream"
    return _provider_error(provider, category, "the provider request could not be completed")


def _usage_value(value: object, provider: str, field: str) -> int:
    if value is None:
        return 0
    if type(value) is not int:
        raise _provider_error(provider, "malformed_stream", f"invalid {field} usage")
    result = value
    if result < 0:
        raise _provider_error(provider, "malformed_stream", f"invalid {field} usage")
    return result


def _attr(value: object, name: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _chunk_shape(chunk: object) -> str:
    """Describe one raw OpenAI-dialect chunk structurally, never its content.

    Emitted at debug level so an operator can capture a gateway's exact wire
    dialect (content kinds, finish reasons, usage placement) from a live
    stream. Text values, tool arguments, and any other user or model content
    are deliberately excluded — only type names, counts, and finish/usage
    markers appear. Total by construction: it must never raise mid-stream.
    """

    parts: list[str] = []
    raw_choices = _attr(chunk, "choices", ())
    if raw_choices is None:
        raw_choices = ()
    if not isinstance(raw_choices, Sequence) or isinstance(raw_choices, (str, bytes)):
        parts.append(f"choices=<{type(raw_choices).__name__}>")
    else:
        for choice in raw_choices:
            delta = _attr(choice, "delta")
            bits: list[str] = []
            content = _attr(delta, "content")
            if isinstance(content, str):
                bits.append(f"text[{len(content)}]")
            elif isinstance(content, Sequence) and not isinstance(content, bytes):
                kinds = ",".join(str(_attr(part, "type")) for part in content)
                bits.append(f"parts[{kinds}]")
            elif content is not None:
                bits.append(f"content=<{type(content).__name__}>")
            tool_calls = _attr(delta, "tool_calls")
            if isinstance(tool_calls, Sequence) and not isinstance(tool_calls, (str, bytes)):
                bits.append(f"tools[{len(tool_calls)}]")
            finish = _attr(choice, "finish_reason")
            if finish is not None:
                bits.append(f"finish={finish}")
            parts.append("{" + " ".join(bits) + "}")
    if _attr(chunk, "usage") is not None:
        parts.append("usage")
    return " ".join(parts) or "empty"


def _iter_content_text(content: object) -> Iterator[str]:
    """Normalise an OpenAI content delta into assistant text fragments.

    api.openai.com streams plain strings.  OpenAI-compatible gateways serving
    Anthropic models (e.g. Databricks Foundation Model APIs) instead stream a
    list of typed content parts: ``text`` parts carry the assistant reply and
    ``reasoning`` parts carry thinking summaries, which the chat deliberately
    does not surface.  Any other shape fails loudly.
    """

    if isinstance(content, str):
        yield content
        return
    if not isinstance(content, Sequence) or isinstance(content, (str, bytes)):
        raise _provider_error("openai", "malformed_stream", "content delta is not text")
    for part in content:
        part_type = _attr(part, "type")
        if part_type == "reasoning":
            continue
        if part_type == "text":
            text = _attr(part, "text")
            if not isinstance(text, str):
                raise _provider_error(
                    "openai", "malformed_stream", "text content part carries no text"
                )
            yield text
            continue
        raise _provider_error("openai", "malformed_stream", "unsupported content part type")


def _map_stop_reason(provider: str, reason: object) -> Literal["end", "tool_use"]:
    """Map natural stops; surface every non-natural stop as a typed failure.

    A truncated (output-token limit) or filtered/refused response must never
    be presented as a completed turn — partial prose reading as an answer is
    exactly the silent-wrongness class the project forbids.
    """

    if not isinstance(reason, str):
        raise _provider_error(provider, "malformed_stream", "unsupported stop reason")
    if reason in {"end_turn", "stop", "stop_sequence"}:
        return "end"
    if reason in {"tool_use", "tool_calls", "function_call"}:
        return "tool_use"
    if reason in {"max_tokens", "length"}:
        raise _provider_error(
            provider, "truncated", "the output-token limit was reached before the turn finished"
        )
    if reason in {"refusal", "content_filter"}:
        raise _provider_error(provider, "filtered", "the provider filtered or refused the output")
    raise _provider_error(provider, "malformed_stream", "unsupported stop reason")


def _parse_tool_arguments(
    provider: str,
    raw: str,
    *,
    initial: object = None,
) -> dict[str, Any]:
    if not raw:
        if initial is None:
            return {}
        if isinstance(initial, Mapping):
            return dict(initial)
        raise _provider_error(provider, "malformed_stream", "tool arguments are not an object")

    def reject_constant(_value: str) -> Any:
        raise ValueError

    try:
        parsed = json.loads(
            raw,
            parse_constant=reject_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise _provider_error(
            provider, "malformed_stream", "tool arguments are not valid JSON"
        ) from exc
    if not isinstance(parsed, dict):
        raise _provider_error(provider, "malformed_stream", "tool arguments are not an object")
    return parsed


def _load_anthropic_client(config: AssistantConfig) -> Any:
    try:
        import anthropic
    except (ImportError, ModuleNotFoundError) as exc:
        raise _provider_error(
            "anthropic", "dependency", "the anthropic SDK is not installed"
        ) from exc
    try:
        return anthropic.AsyncAnthropic(api_key=config.api_key)
    except Exception as exc:
        raise _classify_sdk_error("anthropic", exc) from exc


def _load_openai_client(config: AssistantConfig) -> Any:
    try:
        import openai
    except (ImportError, ModuleNotFoundError) as exc:
        raise _provider_error("openai", "dependency", "the openai SDK is not installed") from exc
    kwargs: dict[str, Any] = {"api_key": config.api_key}
    if config.base_url is not None:
        kwargs["base_url"] = config.base_url
    try:
        return openai.AsyncOpenAI(**kwargs)
    except Exception as exc:
        raise _classify_sdk_error("openai", exc) from exc


def _json_string(value: object) -> str:
    """Encode one neutral JSON value for a provider string-content field."""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _anthropic_messages(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Translate neutral history into Anthropic Messages content blocks."""

    translated: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role == "assistant" and message.get("tool_calls"):
            blocks: list[dict[str, Any]] = []
            if content not in (None, ""):
                blocks.append({"type": "text", "text": content})
            for call in message["tool_calls"]:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["name"],
                        "input": call["arguments"],
                    }
                )
            translated.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            translated.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": message["tool_call_id"],
                            "content": _json_string(content),
                            "is_error": bool(message.get("is_error", False)),
                        }
                    ],
                }
            )
        else:
            translated.append({"role": role, "content": content})
    return translated


class AnthropicProvider:
    """Normalize the Anthropic Messages streaming API."""

    def __init__(self, config: AssistantConfig, client: Any | None = None) -> None:
        self.config = config
        self.client = _load_anthropic_client(config) if client is None else client

    async def stream_turn(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> AsyncIterator[ProviderEvent]:
        input_tokens = 0
        output_tokens = 0
        stop_reason: Literal["end", "tool_use"] | None = None
        stop_emitted = False
        pending_tools: dict[int, dict[str, Any]] = {}

        try:
            stream = self.client.messages.stream(
                model=self.config.model,
                system=system,
                messages=_anthropic_messages(messages),
                tools=list(tools),
                max_tokens=self.config.max_output_tokens,
            )
            async with stream as response:
                async for event in response:
                    event_type = _attr(event, "type")
                    if event_type == "message_start":
                        usage = _attr(_attr(event, "message"), "usage")
                        input_tokens = _usage_value(
                            _attr(usage, "input_tokens"), "anthropic", "input_tokens"
                        )
                    elif event_type == "content_block_start":
                        index = _attr(event, "index")
                        block = _attr(event, "content_block")
                        if _attr(block, "type") == "tool_use":
                            if not isinstance(index, int):
                                raise _provider_error(
                                    "anthropic", "malformed_stream", "tool block has no index"
                                )
                            if index in pending_tools:
                                raise _provider_error(
                                    "anthropic",
                                    "malformed_stream",
                                    "duplicate tool block index",
                                )
                            pending_tools[index] = {
                                "id": _attr(block, "id"),
                                "name": _attr(block, "name"),
                                "fragments": [],
                                "initial": _attr(block, "input", {}),
                            }
                    elif event_type == "content_block_delta":
                        index = _attr(event, "index")
                        delta = _attr(event, "delta")
                        delta_type = _attr(delta, "type")
                        if delta_type == "text_delta":
                            text = _attr(delta, "text")
                            if text is not None:
                                if not isinstance(text, str):
                                    raise _provider_error(
                                        "anthropic", "malformed_stream", "text delta is not text"
                                    )
                                yield TextDelta(text=text)
                        elif delta_type == "input_json_delta":
                            if not isinstance(index, int) or index not in pending_tools:
                                raise _provider_error(
                                    "anthropic", "malformed_stream", "tool fragment has no block"
                                )
                            partial = _attr(delta, "partial_json", "")
                            if not isinstance(partial, str):
                                raise _provider_error(
                                    "anthropic",
                                    "malformed_stream",
                                    "tool fragment is not text",
                                )
                            pending_tools[index]["fragments"].append(partial)
                    elif event_type == "content_block_stop":
                        index = _attr(event, "index")
                        if not isinstance(index, int):
                            continue
                        tool = pending_tools.pop(index, None)
                        if tool is not None:
                            tool_id = tool["id"]
                            tool_name = tool["name"]
                            if not isinstance(tool_id, str) or not isinstance(tool_name, str):
                                raise _provider_error(
                                    "anthropic",
                                    "malformed_stream",
                                    "tool block is missing its id or name",
                                )
                            arguments = _parse_tool_arguments(
                                "anthropic",
                                "".join(tool["fragments"]),
                                initial=tool["initial"],
                            )
                            yield ToolCallRequest(tool_id, tool_name, arguments)
                    elif event_type == "message_delta":
                        delta = _attr(event, "delta")
                        raw_reason = _attr(delta, "stop_reason")
                        usage = _attr(event, "usage")
                        if _attr(usage, "output_tokens") is not None:
                            output_tokens = _usage_value(
                                _attr(usage, "output_tokens"), "anthropic", "output_tokens"
                            )
                        if raw_reason is not None:
                            stop_reason = _map_stop_reason("anthropic", raw_reason)
                    elif event_type == "message_stop" and not stop_emitted:
                        if pending_tools:
                            raise _provider_error(
                                "anthropic", "malformed_stream", "stream ended inside a tool block"
                            )
                        if stop_reason is None:
                            raise _provider_error(
                                "anthropic", "malformed_stream", "message has no stop reason"
                            )
                        yield TurnStop(
                            stop_reason,
                            ProviderUsage(input_tokens, output_tokens),
                        )
                        stop_emitted = True
            if not stop_emitted:
                raise _provider_error(
                    "anthropic", "malformed_stream", "stream ended without a stop event"
                )
        except AssistantProviderError:
            raise
        except Exception as exc:
            raise _classify_sdk_error("anthropic", exc) from exc


def _openai_messages(system: str, messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Translate neutral history into OpenAI Chat Completions messages."""

    translated: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role == "assistant" and message.get("tool_calls"):
            translated.append(
                {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": _json_string(call["arguments"]),
                            },
                        }
                        for call in message["tool_calls"]
                    ],
                }
            )
        elif role == "tool":
            translated.append(
                {
                    "role": "tool",
                    "tool_call_id": message["tool_call_id"],
                    "content": _json_string(content),
                }
            )
        else:
            translated.append({"role": role, "content": content})
    return translated


def _openai_tools(tools: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool["input_schema"],
            },
        }
        for tool in tools
    ]


class OpenAIProvider:
    """Normalize the OpenAI Chat Completions streaming API."""

    def __init__(self, config: AssistantConfig, client: Any | None = None) -> None:
        self.config = config
        self.client = _load_openai_client(config) if client is None else client

    async def stream_turn(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> AsyncIterator[ProviderEvent]:
        input_tokens = 0
        output_tokens = 0
        finish_reason: str | None = None
        emitted_tools = False
        saw_usage = False
        saw_text = False
        calls: dict[int, dict[str, Any]] = {}
        stream: Any | None = None

        request: dict[str, Any] = {
            "model": self.config.model,
            "messages": _openai_messages(system, messages),
            "tools": _openai_tools(tools),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if self.config.base_url is None:
            request["max_completion_tokens"] = self.config.max_output_tokens
        else:
            request["max_tokens"] = self.config.max_output_tokens

        try:
            stream = await self.client.chat.completions.create(**request)
            async for chunk in stream:
                logger.debug("assistant_openai_chunk_shape", shape=_chunk_shape(chunk))
                usage = _attr(chunk, "usage")
                if usage is not None:
                    saw_usage = True
                    input_tokens = _usage_value(
                        _attr(usage, "prompt_tokens"), "openai", "prompt_tokens"
                    )
                    output_tokens = _usage_value(
                        _attr(usage, "completion_tokens"), "openai", "completion_tokens"
                    )

                raw_choices = _attr(chunk, "choices", [])
                if raw_choices is None:
                    choices: Sequence[object] = ()
                elif isinstance(raw_choices, Sequence) and not isinstance(
                    raw_choices, (str, bytes)
                ):
                    choices = raw_choices
                else:
                    raise _provider_error("openai", "malformed_stream", "choices is not a sequence")
                for choice in choices:
                    delta = _attr(choice, "delta")
                    content = _attr(delta, "content")
                    if content is not None:
                        for text in _iter_content_text(content):
                            saw_text = True
                            yield TextDelta(text=text)

                    raw_tool_calls = _attr(delta, "tool_calls", [])
                    if raw_tool_calls is None:
                        tool_calls: Sequence[object] = ()
                    elif isinstance(raw_tool_calls, Sequence) and not isinstance(
                        raw_tool_calls, (str, bytes)
                    ):
                        tool_calls = raw_tool_calls
                    else:
                        raise _provider_error(
                            "openai", "malformed_stream", "tool calls is not a sequence"
                        )
                    for fragment in tool_calls:
                        index = _attr(fragment, "index")
                        if not isinstance(index, int):
                            raise _provider_error(
                                "openai", "malformed_stream", "tool fragment has no index"
                            )
                        call = calls.setdefault(
                            index,
                            {"id": None, "name": None, "arguments": []},
                        )
                        call_id = _attr(fragment, "id")
                        if call_id is not None:
                            call["id"] = call_id
                        function = _attr(fragment, "function")
                        name = _attr(function, "name")
                        if name is not None:
                            call["name"] = name
                        arguments = _attr(function, "arguments")
                        if arguments is not None:
                            if not isinstance(arguments, str):
                                raise _provider_error(
                                    "openai",
                                    "malformed_stream",
                                    "tool fragment is not text",
                                )
                            call["arguments"].append(arguments)

                    choice_reason = _attr(choice, "finish_reason")
                    if choice_reason is not None:
                        if finish_reason is not None and choice_reason != finish_reason:
                            raise _provider_error(
                                "openai", "malformed_stream", "multiple finish reasons"
                            )
                        finish_reason = str(choice_reason)
                        if finish_reason in {"tool_calls", "function_call"} and not emitted_tools:
                            for call in calls.values():
                                call_id = call["id"]
                                name = call["name"]
                                if not isinstance(call_id, str) or not isinstance(name, str):
                                    raise _provider_error(
                                        "openai",
                                        "malformed_stream",
                                        "tool call is missing its id or name",
                                    )
                                arguments = _parse_tool_arguments(
                                    "openai", "".join(call["arguments"])
                                )
                                yield ToolCallRequest(call_id, name, arguments)
                            emitted_tools = True
                        elif finish_reason not in {"stop", "length", "content_filter"}:
                            raise _provider_error(
                                "openai", "malformed_stream", "unsupported finish reason"
                            )

            if finish_reason is None:
                # Databricks intermittently omits finish_reason from a complete
                # reply's final text chunk (captured live 2026-07-19). Accept a
                # clean stream end as a natural stop ONLY when nothing suggests
                # a broken or truncated stream: no half-delivered tool call, a
                # text reply actually arrived, and per-chunk usage confirms the
                # output stayed under the budget. Every other shape fails loud.
                if calls and not emitted_tools:
                    raise _provider_error(
                        "openai", "malformed_stream", "stream ended mid tool call"
                    )
                if not saw_usage or not saw_text:
                    raise _provider_error(
                        "openai", "malformed_stream", "stream ended without a finish reason"
                    )
                if output_tokens >= self.config.max_output_tokens:
                    raise _provider_error(
                        "openai",
                        "truncated",
                        "stream ended at the output token budget without a finish reason",
                    )
                logger.warning(
                    "assistant_openai_stream_missing_finish",
                    output_tokens=output_tokens,
                    budget=self.config.max_output_tokens,
                )
                finish_reason = "stop"
            # length / content_filter raise typed truncated/filtered failures
            # here rather than masquerading as a natural end.
            yield TurnStop(
                _map_stop_reason("openai", finish_reason),
                ProviderUsage(input_tokens, output_tokens),
            )
        except AssistantProviderError:
            raise
        except Exception as exc:
            raise _classify_sdk_error("openai", exc) from exc
        finally:
            close = getattr(stream, "aclose", None) or getattr(stream, "close", None)
            if callable(close):
                result = close()
                if isawaitable(result):
                    await result


__all__ = [
    "AnthropicProvider",
    "AssistantProvider",
    "AssistantProviderError",
    "OpenAIProvider",
    "ProviderEvent",
    "ProviderUsage",
    "TextDelta",
    "ToolCallRequest",
    "TurnStop",
]
